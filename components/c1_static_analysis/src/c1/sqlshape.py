"""SQL shape: rebuild each query's SQL template from the DFG and read its structure.

C3 owns SQL parsing (master reference §3.3). Component 1 still needs a small part of it
to recover endpoints: the statement kind, its tables, which columns a request value is
compared with (WHERE) or written to (INSERT/UPDATE SET), and the selected columns.
This module does only that, on the *template*: literal SQL text with each interpolated
value replaced by a placeholder that keeps its DFG origins. When C3's parsed queries
are available they are joined by query node id and take precedence.

Sound-or-abstain (C3's rule, master reference §3.3 step 6): a placeholder in a
structural position (table or column name, clause text) makes the query `dynamic`.
We do not guess its shape.
"""

from __future__ import annotations

import re
from itertools import product
from typing import Any

from .includes import _str_value

MAX_VARIANTS = 16
MARK = "\x00"


class Template:
    """One possible SQL text: a list of ("lit", text) and ("val", node, origins) parts."""

    def __init__(self, parts: list[tuple]):
        self.parts = parts

    def text(self) -> str:
        out, i = [], 0
        for p in self.parts:
            if p[0] == "lit":
                out.append(p[1])
            else:
                out.append(f"{MARK}{i}{MARK}")
                i += 1
        return "".join(out)

    def values(self) -> list[tuple]:
        return [p for p in self.parts if p[0] == "val"]


def templates(project, fid: str, uid: str, expr: str, depth: int = 0) -> list[Template]:
    """All SQL texts the expression can produce (capped), using use-def chains."""
    a = project.asts[fid]
    k = a.kind(expr)
    if depth > 25:
        return [Template([("val", expr, frozenset({"unknown:depth"}))])]
    if k == "Scalar_String":
        return [Template([("lit", _str_value(a.attr(expr, "value")) or "")])]
    if k == "Scalar_InterpolatedString":
        seqs: list[list[Template]] = []
        for p in a.field_list(expr, "parts"):
            if a.kind(p) == "InterpolatedStringPart":
                seqs.append([Template([("lit", _str_value(a.attr(p, "value")) or "")])])
            else:
                seqs.append(templates(project, fid, uid, p, depth + 1))
        return _concat(seqs)
    if k == "Expr_BinaryOp_Concat":
        return _concat([templates(project, fid, uid, a.field(expr, "left"), depth + 1),
                        templates(project, fid, uid, a.field(expr, "right"), depth + 1)])
    if k == "Expr_FuncCall" and a.call_name(expr) == "sprintf":
        args = [a.field(x, "value") for x in a.field_list(expr, "args") if a.kind(x) == "Arg"]
        if args and a.kind(args[0]) == "Scalar_String":
            fmt = _str_value(a.attr(args[0], "value")) or ""
            pieces = re.split(r"(%%|%(?:\d+\$)?[-+ 0']*\d*(?:\.\d+)?[bcdeEfFgGosuxX])", fmt)
            seqs: list[list[Template]] = []
            argi = 1
            for piece in pieces:
                if piece == "%%":
                    seqs.append([Template([("lit", "%")])])
                elif piece.startswith("%") and len(piece) > 1:
                    pos = re.match(r"%(\d+)\$", piece)
                    idx = int(pos.group(1)) if pos else argi
                    argi += 0 if pos else 1
                    if idx < len(args):
                        seqs.append([Template([("val", args[idx], project.origins.of_expr(fid, uid, args[idx]))])])
                    else:
                        seqs.append([Template([("val", expr, frozenset({"unknown:sprintf-arg"}))])])
                elif piece:
                    seqs.append([Template([("lit", piece)])])
            return _concat(seqs)
    if k == "Expr_Variable":
        use = project.uses_by_outer.get((fid, uid), {}).get(expr)
        d = project.units[(fid, uid)]
        if use and use["defs"] and not use["dims"]:
            out: list[Template] = []
            for did in use["defs"]:
                dd = d.defs.get(did)
                if dd is None or not dd.get("rhs"):
                    return [Template([("val", expr, project.origins.of_use(fid, uid, use))])]
                if dd["kind"] == "assign":
                    rhs_k = a.kind(dd["rhs"])
                    if rhs_k in ("Scalar_String", "Scalar_InterpolatedString", "Expr_BinaryOp_Concat") or \
                            (rhs_k == "Expr_FuncCall" and a.call_name(dd["rhs"]) == "sprintf"):
                        out += templates(project, fid, uid, dd["rhs"], depth + 1)
                        continue
                if dd["kind"] == "compound" and a.kind(dd["rhs"]) == "Expr_AssignOp_Concat":
                    out += _concat([templates(project, fid, uid, a.field(dd["rhs"], "var"), depth + 1),
                                    templates(project, fid, uid, a.field(dd["rhs"], "expr"), depth + 1)])
                    continue
                return [Template([("val", expr, project.origins.of_use(fid, uid, use))])]
            return _dedupe(out)[:MAX_VARIANTS]
    origins = project.origins.of_expr(fid, uid, expr)
    return [Template([("val", expr, origins)])]


def _concat(seqs: list[list[Template]]) -> list[Template]:
    out = []
    for combo in product(*seqs):
        parts: list[tuple] = []
        for t in combo:
            parts += t.parts
        out.append(Template(_merge_lits(parts)))
        if len(out) >= MAX_VARIANTS:
            break
    return _dedupe(out)


def _merge_lits(parts: list[tuple]) -> list[tuple]:
    out: list[tuple] = []
    for p in parts:
        if p[0] == "lit" and out and out[-1][0] == "lit":
            out[-1] = ("lit", out[-1][1] + p[1])
        else:
            out.append(p)
    return out


def _dedupe(ts: list[Template]) -> list[Template]:
    seen, out = set(), []
    for t in ts:
        key = repr([(p[0], p[1]) for p in t.parts])
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


# -- reading the template ------------------------------------------------------------------
IDENT = r"`?([A-Za-z_][A-Za-z0-9_]*)`?(?:\.`?([A-Za-z_][A-Za-z0-9_]*)`?)?"
PH = MARK + r"(\d+)" + MARK
VALUE = r"""(?:'[^']*?'|"[^"]*?"|""" + PH + r"|-?\d+(?:\.\d+)?)"""


def _ident(m_table: str | None, m_col: str | None) -> tuple[str | None, str]:
    """`t.c` -> ("t", "c"); `c` -> (None, "c")."""
    return (m_table, m_col) if m_col else (None, m_table or "")


def shape(t: Template) -> dict[str, Any]:
    sql = t.text()
    flat = re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()
    low = flat.lower()
    vals = t.values()
    res: dict[str, Any] = {"kind": low.split(" ", 1)[0] if low else "", "tables": [], "params": []}
    if res["kind"] in ("begin", "commit", "rollback", "start", "savepoint", "set"):
        res["kind"] = "transaction" if res["kind"] != "set" else "session-setting"
        res["confidence"] = "resolved"
        return res
    if res["kind"] not in ("select", "insert", "update", "delete", "replace"):
        res["kind"] = res["kind"] or "unknown"
        res["confidence"] = "unresolved"
        return res

    # where is each placeholder? inside quotes / after a comparison = value position
    struct = _structural_placeholders(flat)
    for i, (_, node, origins) in enumerate(vals):
        res["params"].append({"index": i, "node": node, "origins": sorted(origins),
                              "position": "structural" if i in struct else "value"})
    if struct:
        res["confidence"] = "dynamic"
        res["reason"] = "request/runtime value in a structural SQL position (table, column or clause)"
    else:
        res["confidence"] = "resolved"

    if res["kind"] == "select":
        m = re.match(r"select\s+(distinct\s+)?(.*?)\s+from\s+(.*)$", flat, re.I | re.S)
        if m:
            cols = [c.strip() for c in _split_commas(m.group(2))]
            res["columns"] = ["*" if c == "*" or c.endswith(".*") else _col_name(c) for c in cols]
            res["tables"] = _tables(m.group(3))
            res["join"] = bool(re.search(r"\bjoin\b", m.group(3), re.I)) or "," in m.group(3).split(" where ")[0]
    elif res["kind"] in ("insert", "replace"):
        m = re.match(r"(?:insert|replace)\s+(?:ignore\s+)?into\s+" + IDENT + r"\s*\((.*?)\)\s*values\s*\((.*)\)\s*$",
                     flat, re.I | re.S)
        if m:
            res["tables"] = [m.group(2) or m.group(1)]
            cols = [_col_name(c) for c in _split_commas(m.group(3))]
            values = _split_commas(m.group(4))
            res["writes"] = []
            for c, v in zip(cols, values):
                res["writes"].append(_write(c, v))
        else:
            m2 = re.match(r"(?:insert|replace)\s+(?:ignore\s+)?into\s+" + IDENT, flat, re.I)
            res["tables"] = [m2.group(2) or m2.group(1)] if m2 else []
            res["confidence"] = res.get("confidence") if res["confidence"] == "dynamic" else "partial"
    elif res["kind"] == "update":
        m = re.match(r"update\s+" + IDENT + r"\s+set\s+(.*?)(?:\s+where\s+(.*))?$", flat, re.I | re.S)
        if m:
            res["tables"] = [m.group(2) or m.group(1)]
            res["writes"] = []
            for asg in _split_commas(m.group(3)):
                mm = re.match(r"\s*" + IDENT + r"\s*=\s*(.*)$", asg, re.S)
                if mm:
                    res["writes"].append(_write(_ident(mm.group(1), mm.group(2))[1], mm.group(3)))
    elif res["kind"] == "delete":
        m = re.match(r"delete\s+from\s+" + IDENT, flat, re.I)
        res["tables"] = [m.group(2) or m.group(1)] if m else []

    where = re.search(r"\bwhere\b(.*?)(?:\border\s+by\b|\bgroup\s+by\b|\blimit\b|$)", flat, re.I | re.S)
    res["where"] = []
    if where:
        # one condition per AND/OR term: `col op <expr with one value>`; the value may be
        # wrapped, e.g. password = SHA1(CONCAT('§1§', salt)) (WackoPicko)
        for term in re.split(r"\band\b|\bor\b", where.group(1), flags=re.I):
            mm = re.match(r"\s*\(?\s*" + IDENT + r"\s*(=|!=|<>|>=|<=|>|<|\blike\b)\s*(.*)$", term, re.I | re.S)
            if not mm:
                continue
            phs = [int(x) for x in re.findall(PH, mm.group(4))]
            if len(phs) == 1:
                cond = {"column": _ident(mm.group(1), mm.group(2))[1], "op": mm.group(3).lower(), "param": phs[0]}
                if not re.fullmatch(r"\s*['\"]?\s*" + PH + r"\s*['\"]?\s*\)?\s*;?\s*", mm.group(4)):
                    cond["wrapped"] = True
                res["where"].append(cond)
    return res


def _write(column: str, value: str) -> dict[str, Any]:
    """A written value: one placeholder (maybe wrapped, e.g. SHA1('§1§')) or a literal."""
    phs = [int(x) for x in re.findall(PH, value)]
    if len(phs) == 1:
        w: dict[str, Any] = {"column": column, "param": phs[0], "literal": None}
        if not re.fullmatch(r"\s*['\"]?\s*" + PH + r"\s*['\"]?\s*", value):
            w["wrapped"] = value.strip().replace(MARK, "§")
        return w
    return {"column": column, "param": None, "literal": value.strip().replace(MARK, "§")}


def _structural_placeholders(sql: str) -> set[int]:
    """Placeholders that are not inside quotes and not directly after a comparison/VALUES/SET =."""
    out = set()
    in_q: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_q:
            if ch == in_q:
                in_q = None
        elif ch in ("'", '"'):
            in_q = ch
        elif ch == MARK:
            j = sql.index(MARK, i + 1)
            idx = int(sql[i + 1:j])
            before = sql[:i].rstrip()
            value_pos = bool(re.search(r"(=|<>|!=|>|<|\blike|\bin\s*\(|,|\(|\blimit|\boffset|\bvalues\s*\(|-|\+)$",
                                       before, re.I))
            if not value_pos:
                out.add(idx)
            i = j
        i += 1
    return out


def _split_commas(s: str) -> list[str]:
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _col_name(c: str) -> str:
    c = c.strip()
    m = re.match(r".*\bas\s+`?(\w+)`?\s*$", c, re.I | re.S)
    if m:
        return m.group(1)
    m = re.match(r"^" + IDENT + r"\s*$", c)
    if m:
        return _ident(m.group(1), m.group(2))[1]
    return c


def _tables(from_part: str) -> list[str]:
    head = re.split(r"\b(where|order\s+by|group\s+by|limit|having)\b", from_part, flags=re.I)[0]
    names = []
    for m in re.finditer(r"(?:^|,|\bjoin\b)\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", head, re.I):
        if m.group(1).lower() not in ("inner", "left", "right", "outer", "cross", "natural", "on", "as"):
            names.append(m.group(1))
    return names


# -- schema dump reader (minimal; C3's pluggable reader replaces it) ------------------------------
CREATE_RE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?`?(\w+)`?\s*\((.*?)\)\s*(?:engine|default|;|charset|auto_increment|collate)",
                       re.I | re.S)


def read_schema_dump(sql_text: str) -> dict[str, dict[str, dict[str, Any]]]:
    """{table: {column: {"sqlType", "nullable"}}} from CREATE TABLE statements in a dump."""
    schema: dict[str, dict[str, dict[str, Any]]] = {}
    for m in CREATE_RE.finditer(sql_text):
        table = m.group(1)
        cols: dict[str, dict[str, Any]] = {}
        for line in _split_commas(m.group(2)):
            line = line.strip()
            cm = re.match(r"`?(\w+)`?\s+([a-zA-Z]+(?:\s*\([^)]*\))?(?:\s+unsigned)?)(.*)$", line, re.S)
            if not cm or cm.group(1).lower() in ("primary", "key", "unique", "constraint", "index", "foreign", "fulltext"):
                continue
            cols[cm.group(1)] = {"sqlType": cm.group(2).lower().replace(" ", ""),
                                 "nullable": "not null" not in cm.group(3).lower()}
        schema[table] = cols
    return schema


def json_type(sql_type: str) -> dict[str, Any]:
    """MySQL column type -> JSON Schema (OpenAPI 3.1) type."""
    t = sql_type.lower()
    if t.startswith(("tinyint(1)", "bool")):
        return {"type": "boolean"}
    if t.startswith(("int", "tinyint", "smallint", "mediumint", "bigint", "year")):
        return {"type": "integer"}
    if t.startswith(("decimal", "numeric", "float", "double", "real")):
        return {"type": "number"}
    if t.startswith("datetime") or t.startswith("timestamp"):
        return {"type": "string", "format": "date-time"}
    if t.startswith("date"):
        return {"type": "string", "format": "date"}
    if t.startswith("time"):
        return {"type": "string", "format": "time"}
    m = re.match(r"(?:var)?char\((\d+)\)", t)
    if m:
        return {"type": "string", "maxLength": int(m.group(1))}
    return {"type": "string"}
