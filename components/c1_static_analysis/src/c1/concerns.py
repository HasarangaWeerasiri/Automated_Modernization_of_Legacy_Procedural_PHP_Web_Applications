"""Concern classifier: label every statement presentation / business_logic / data_access.

    python -m c1.concerns analysis-out     # needs ast/, cfg/, dfg/; writes labels.json

Master reference, Component 1 step 4 ("Classify concerns ... rule-based pattern
matching") and step 7's branch rule:

    IF the branch body contains a database query OR modifies state -> gates data (logic)
    ELSE IF the body contains only echo/HTML output                  -> display
    ELSE                                                              -> ambiguous: abstain

Output format agreed with C2 (component2_frontend/CONTEXT.md, "Labels"):
    {schemaVersion, generatedFor, rules, labels: {<nodeId>: {concern, basis, ruleId, reason}}}
    concern: presentation | business_logic | data_access | mixed | undecided
    basis:   rule | abstained
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import AstDoc, CLASS_LIKE_KINDS, SCOPE_KINDS
from .dfg import FETCH_CALLS, FETCH_METHODS, QUERY_CALLS, QUERY_METHODS
from .extract import canonical_json

CONNECT_CALLS = frozenset({"mysqli_connect", "mysql_connect", "mysql_pconnect", "mysqli_select_db",
                           "mysql_select_db", "mysqli_close", "mysql_close", "pg_connect", "mysqli_init",
                           "mysqli_real_connect", "mysqli_set_charset", "mysql_set_charset"})
DB_HELPER_CALLS = frozenset({"mysqli_num_rows", "mysql_num_rows", "mysqli_affected_rows", "mysql_affected_rows",
                             "mysqli_insert_id", "mysql_insert_id", "mysqli_error", "mysql_error",
                             "mysqli_free_result", "mysql_free_result", "mysqli_data_seek", "mysql_result",
                             "mysqli_real_escape_string", "mysql_real_escape_string", "mysqli_prepare",
                             "mysqli_stmt_execute", "mysqli_stmt_bind_param", "mysqli_stmt_get_result",
                             "mysqli_stmt_bind_result", "mysqli_stmt_fetch"})
DB_CLASSES = frozenset({"mysqli", "pdo"})
OUTPUT_CALLS = frozenset({"printf", "print_r", "var_dump", "vprintf", "readfile", "fpassthru", "var_export"})
STATE_CALLS = frozenset({"session_start", "session_destroy", "session_unset", "session_regenerate_id",
                         "setcookie", "setrawcookie", "header", "mail", "move_uploaded_file", "unlink",
                         "file_put_contents", "fwrite", "fputs", "mkdir", "rename", "copy", "session_write_close"})
# query-builder / ORM verbs (MysqliDb, Eloquent-style, PDO wrappers). The master reference puts
# ORMs on the legacy side out of scope; we must not miss them silently, so they abstain.
ORM_METHODS = frozenset({"get", "getone", "getvalue", "insert", "insertmulti", "update", "delete", "where",
                         "orwhere", "rawquery", "rawqueryone", "rawqueryvalue", "paginate", "has", "join",
                         "orderby", "groupby", "select", "find", "findall", "save", "first", "all"})
SQL_PREFIXES = ("select ", "insert ", "update ", "delete ", "replace ", "create ", "drop ", "alter ",
                "select*", "insert into", "truncate ")

RULES = {
    "R01": ("presentation", "output_statement", "echo / print / inline HTML / printf"),
    "R02": ("data_access", "sql_execution", "runs a query (mysqli_query, ->query, ->execute ...)"),
    "R03": ("data_access", "row_fetch", "fetches a result row (mysqli_fetch_*, ->fetch ...)"),
    "R04": ("data_access", "db_connection", "connects to / configures / closes the database"),
    "R05": ("data_access", "sql_building", "builds a string that reaches a query's SQL argument (DFG)"),
    "R06": ("business_logic", "gates_data_access", "branch/loop whose body runs a query or changes state"),
    "R07": ("business_logic", "state_change", "session, cookie, header/redirect, exit, file or mail side effect"),
    "R08": ("presentation", "display_conditional", "branch whose bodies only produce output"),
    "R09": ("business_logic", "computation", "assignment / call that is not output or data access"),
    "R10": ("undecided", "logic_or_display", "branch mixes output with logic but no data access: abstain"),
    "R11": ("data_access", "db_result_helper", "row counts, insert ids, escaping, prepared-statement plumbing"),
    "R12": ("presentation", "render_rows", "loop over fetched rows whose body only produces output"),
    "R13": ("business_logic", "request_input", "reads request input ($_GET/$_POST/...) into a variable"),
    "R14": ("mixed", "output_and_data", "one statement both outputs and touches the database"),
    "R15": ("business_logic", "control_flow", "return / break / continue / exit"),
    "R16": ("undecided", "unknown_call_in_branch", "branch body calls code we could not classify: abstain"),
    "R17": ("presentation", "display_binding", "copies a value whose every later use is an output statement (forward use-def)"),
    "R18": ("business_logic", "include", "include / require of another file"),
    "R19": ("undecided", "possible_orm_access", "query-builder / ORM call ($db->get(), ->insert() ...): "
            "data access we cannot read as SQL (out of scope, flagged)"),
}


class Facts:
    """What each statement does, from the AST plus user-function summaries."""

    def __init__(self, asts: dict[str, AstDoc], cfgs: dict[str, dict], dfgs: dict[str, dict]):
        self.asts = asts
        self.cfgs = cfgs
        self.dfgs = dfgs
        self.sql_defs: set[str] = set()   # def ids whose value reaches a query SQL argument
        self._collect_sql_defs()
        self.func_effects: dict[str, set[str]] = {}  # lower name -> effects
        self.method_effects: dict[str, set[str]] = {}
        self._summarise_functions()

    def _collect_sql_defs(self) -> None:
        """R05: definitions that build the SQL text of a query.

        Walk back from each query's SQL argument along the use-def chains. We follow a
        variable when it *is* the SQL argument (or a fragment that holds SQL text), and
        the chain of that same variable (`$sql = ...; $sql .= ...`). A variable that is
        only a value inside the SQL (`$id = $_GET['id']`) is input, not SQL building.
        """
        for fid, d in self.dfgs.items():
            a = self.asts[fid]
            for u in d["units"]:
                uses_by_item: dict[str, list[dict]] = {}
                for x in u["uses"]:
                    uses_by_item.setdefault(x["item"], []).append(x)
                defs = {x["id"]: x for x in u["defs"]}
                todo: list[str] = []
                for q in u["queries"]:
                    arg = q["sqlArg"]
                    if arg and a.kind(arg) == "Expr_Variable":
                        todo += next((x["defs"] for x in u["uses"] if x["node"] == arg), [])
                seen: set[str] = set()
                while todo:
                    did = todo.pop()
                    if did in seen or did not in defs:
                        continue
                    seen.add(did)
                    dd = defs[did]
                    if dd["kind"] not in ("assign", "compound") or not dd.get("rhs"):
                        continue
                    if a.kind(dd["rhs"]) in ("Expr_FuncCall", "Expr_New", "Expr_MethodCall", "Expr_StaticCall"):
                        continue  # a handle or a call result, not SQL text
                    self.sql_defs.add(did)
                    for x in uses_by_item.get(dd["item"], []):
                        same_var = x["var"] == dd["var"]
                        fragment = any(_sql_literal(a, defs[y]["rhs"]) for y in x["defs"]
                                       if y in defs and defs[y].get("rhs"))
                        if same_var or fragment:
                            todo += [y for y in x["defs"] if y != did]

    # -- effects of a subtree --------------------------------------------------------
    def effects(self, fid: str, nid: str, into_items: bool = True) -> set[str]:
        """Effects of a subtree: output, query, fetch, connect, dbhelper, state, exit, input, call:?"""
        a = self.asts[fid]
        out: set[str] = set()
        for x in a.walk(nid):
            k = a.kind(x)
            if k in ("Stmt_Echo", "Stmt_InlineHTML", "Expr_Print"):
                out.add("output")
            elif k in ("Expr_Exit",):
                out.add("exit")
            elif k == "Expr_FuncCall":
                name = a.call_name(x)
                if name in QUERY_CALLS:
                    out.add("query")
                elif name in FETCH_CALLS:
                    out.add("fetch")
                elif name in CONNECT_CALLS:
                    out.add("connect")
                elif name in DB_HELPER_CALLS:
                    out.add("dbhelper")
                elif name in OUTPUT_CALLS:
                    out.add("output")
                elif name in STATE_CALLS:
                    out.add("state")
                elif name in self.func_effects:
                    out |= self.func_effects[name]
                elif name is None:
                    out.add("call:dynamic")
            elif k in ("Expr_MethodCall", "Expr_NullsafeMethodCall", "Expr_StaticCall"):
                m = a.name_of(a.field(x, "name")) or ""
                if m in QUERY_METHODS:
                    out.add("query")
                elif m in FETCH_METHODS:
                    out.add("fetch")
                else:
                    cls = a.name_of(a.field(x, "class")) if k == "Expr_StaticCall" else None
                    key = f"{cls}::{m}".lower() if cls else None
                    if key and key in self.method_effects:
                        out |= self.method_effects[key]
                    elif not cls:
                        hits = [v for kk, v in self.method_effects.items() if kk.endswith("::" + m.lower())]
                        recv = a.field(x, "var")
                        is_this = a.kind(recv) == "Expr_Variable" and a.attr(recv, "name") == "this"
                        if len(hits) == 1:
                            out |= hits[0]
                        elif hits:
                            out.add("call:method-ambiguous")
                        elif m.lower() in ORM_METHODS and not is_this:
                            out.add("orm")
                        else:
                            out.add("call:method-unknown")
            elif k == "Expr_New":
                cn = (a.name_of(a.field(x, "class")) or "").lstrip("\\").lower()
                if cn in DB_CLASSES:
                    out.add("connect")
            elif k == "Expr_Variable":
                nm = a.attr(x, "name")
                if nm == "_SESSION":
                    p = a.parent(x)
                    out.add("session-read")
                    # a write: $_SESSION[...] = ...
                    while p and a.kind(p) == "Expr_ArrayDimFetch":
                        p = a.parent(p)
                    if p and a.kind(p) in ("Expr_Assign",) and self._inside_target(a, x, p):
                        out.add("state")
                elif nm in ("_GET", "_POST", "_REQUEST", "_COOKIE", "_FILES"):
                    out.add("input")
        return out

    @staticmethod
    def _inside_target(a: AstDoc, node: str, assign: str) -> bool:
        tgt = a.field(assign, "var")
        return node == tgt or any(anc == tgt for anc in a.ancestors(node))

    def _summarise_functions(self) -> None:
        """Effects of user functions / methods, iterated so callees propagate to callers."""
        bodies: list[tuple[str, str, str, str]] = []  # (kind, key, fid, nodeId)
        names_f: dict[str, int] = {}
        names_m: dict[str, int] = {}
        for fid, cfg in self.cfgs.items():
            for u in cfg["units"]:
                if u["kind"] == "function":
                    key = u["name"].lower()
                    names_f[key] = names_f.get(key, 0) + 1
                    bodies.append(("f", key, fid, u["id"]))
                elif u["kind"] == "method":
                    key = u["name"].lower()
                    names_m[key] = names_m.get(key, 0) + 1
                    bodies.append(("m", key, fid, u["id"]))
        for _ in range(10):
            changed = False
            for kind, key, fid, nid in bodies:
                if (kind == "f" and names_f[key] > 1) or (kind == "m" and names_m[key] > 1):
                    continue
                eff = set()
                for s in self.asts[fid].field_list(nid, "stmts"):
                    eff |= self.effects(fid, s)
                table = self.func_effects if kind == "f" else self.method_effects
                if table.get(key) != eff:
                    table[key] = eff
                    changed = True
            if not changed:
                break


DATA = {"query", "fetch", "connect", "dbhelper", "orm"}


def label_statement(facts: Facts, fid: str, nid: str) -> tuple[str, str] | None:
    """(ruleId, reason detail) for a simple statement or expression item."""
    a = facts.asts[fid]
    k = a.kind(nid)
    if k in SCOPE_KINDS or k in CLASS_LIKE_KINDS or k in ("Stmt_Nop", "Stmt_Use", "Stmt_GroupUse",
                                                           "Stmt_Namespace", "Stmt_Declare", "Stmt_Const"):
        return None
    eff = facts.effects(fid, nid)
    data = eff & DATA
    if "output" in eff and data:
        return ("R14", ",".join(sorted(eff)))
    if k in ("Stmt_Echo", "Stmt_InlineHTML") or ("output" in eff and not data and
                                                 not (eff & {"state", "exit"})):
        return ("R01", "")
    if "query" in eff:
        return ("R02", "")
    if "fetch" in eff:
        return ("R03", "")
    if "connect" in eff:
        return ("R04", "")
    if "dbhelper" in eff:
        return ("R11", "")
    if "orm" in eff:
        return ("R19", "")
    if k in ("Stmt_Return", "Stmt_Break", "Stmt_Continue", "Stmt_Goto"):
        return ("R15", "")
    if eff & {"state", "exit"}:
        return ("R07", ",".join(sorted(eff & {"state", "exit"})))
    # SQL string construction (DFG): a definition whose value reaches a query
    for did in facts.dfg_defs_of_item(fid, nid):
        if did in facts.sql_defs:
            return ("R05", "")
    if k == "Stmt_Expression" and _looks_like_sql_assign(a, nid):
        return ("R05", "sql-literal")
    if "input" in eff:
        return ("R13", "")
    if k == "Stmt_Expression" and a.kind(a.field(nid, "expr")) == "Expr_Include":
        return ("R18", "")
    return ("R09", "")


def _sql_literal(a: AstDoc, expr: str | None) -> bool:
    """The expression contains a string literal that starts like an SQL statement."""
    if not expr:
        return False
    for x in a.walk(expr):
        if a.kind(x) in ("Scalar_String", "InterpolatedStringPart"):
            v = a.attr(x, "value")
            if isinstance(v, str) and v.lstrip().lower().startswith(SQL_PREFIXES):
                return True
    return False


def _looks_like_sql_assign(a: AstDoc, nid: str) -> bool:
    e = a.field(nid, "expr")
    if a.kind(e) not in ("Expr_Assign", "Expr_AssignOp_Concat"):
        return False
    return _sql_literal(a, a.field(e, "expr"))


def label_branch(facts: Facts, fid: str, nid: str, labels: dict[str, dict]) -> tuple[str, str]:
    """Step-7 rule for if / switch / loops, applied to the bodies (not the condition)."""
    a = facts.asts[fid]
    k = a.kind(nid)
    bodies: list[str] = []
    if k == "Stmt_If":
        bodies += a.field_list(nid, "stmts")
        for ei in a.field_list(nid, "elseifs"):
            bodies += a.field_list(ei, "stmts")
        if a.field(nid, "else"):
            bodies += a.field_list(a.field(nid, "else"), "stmts")
    elif k == "Stmt_Switch":
        for c in a.field_list(nid, "cases"):
            bodies += a.field_list(c, "stmts")
    elif k == "Stmt_TryCatch":
        bodies += a.field_list(nid, "stmts")
        for c in a.field_list(nid, "catches"):
            bodies += a.field_list(c, "stmts")
    else:  # loops
        bodies += a.field_list(nid, "stmts")
    eff: set[str] = set()
    for b in bodies:
        eff |= facts.effects(fid, b)
    if eff & (DATA | {"state", "exit"}):
        return ("R06", ",".join(sorted(eff & (DATA | {"state", "exit"}))))
    if k in ("Stmt_While", "Stmt_Foreach", "Stmt_For", "Stmt_Do"):
        head = a.field(nid, "expr") if k == "Stmt_Foreach" else a.field(nid, "cond")
        head_eff: set[str] = set()
        for h in (head if isinstance(head, list) else [head]):
            if isinstance(h, str):
                head_eff |= facts.effects(fid, h)
        inner_logic = any(labels.get(x, {}).get("ruleId") in ("R09", "R13") for b in bodies for x in a.walk(b))
        if eff <= {"output", "input", "session-read"} and "output" in eff and not inner_logic:
            return ("R12" if head_eff & DATA or _iterates_rows(facts, fid, nid) else "R08", "")
    if any(e.startswith("call:") for e in eff):
        return ("R16", ",".join(sorted(e for e in eff if e.startswith("call:"))))
    # "only echo/HTML" (master reference): any computation in the body means we abstain
    inner_logic = any(labels.get(x, {}).get("ruleId") in ("R09", "R13")
                      for b in bodies for x in a.walk(b))
    if "output" in eff and inner_logic:
        return ("R10", "output+computation")
    if eff <= {"output", "session-read", "input"} and "output" in eff:
        return ("R08", "")
    if "output" in eff:
        return ("R10", ",".join(sorted(eff)))
    return ("R09", "no-output")


def _iterates_rows(facts: Facts, fid: str, nid: str) -> bool:
    a = facts.asts[fid]
    if a.kind(nid) != "Stmt_Foreach":
        return False
    expr = a.field(nid, "expr")
    d = facts.dfgs[fid]
    for u in d["units"]:
        for x in u["uses"]:
            if x["node"] == expr or (x["item"] == expr):
                if any(o.startswith(("row:", "query:", "column:")) for o in x["origins"]):
                    return True
    return False


BRANCH_KINDS = frozenset({"Stmt_If", "Stmt_Switch", "Stmt_While", "Stmt_Foreach", "Stmt_For", "Stmt_Do", "Stmt_TryCatch"})


def build_labels(asts: list[AstDoc], cfgs: list[dict], dfgs: list[dict], content_hash: str) -> dict[str, Any]:
    A = {a.file_id: a for a in asts}
    C = {c["fileId"]: c for c in cfgs}
    D = {d["fileId"]: d for d in dfgs}
    facts = Facts(A, C, D)
    # def ids per item, for R05
    defs_by_item: dict[str, dict[str, list[str]]] = {}
    for fid, d in D.items():
        m = defs_by_item.setdefault(fid, {})
        for u in d["units"]:
            for dd in u["defs"]:
                m.setdefault(dd["item"], []).append(dd["id"])
    facts.dfg_defs_of_item = lambda fid, item: defs_by_item.get(fid, {}).get(item, [])  # type: ignore[attr-defined]

    labels: dict[str, dict[str, str]] = {}
    # 1. simple statements, 2. display bindings (needs 1), 3. branches (need 1 and 2)
    for fid in sorted(A):
        for u in C[fid]["units"]:
            for b in u["blocks"]:
                for item in b["items"]:
                    r = label_statement(facts, fid, item)
                    if r:
                        labels[item] = _label(r)
    _display_bindings(A, D, labels)
    for fid in sorted(A):
        a = A[fid]
        for u in C[fid]["units"]:
            branch_nodes = [n for n in a.walk(u["id"]) if a.kind(n) in BRANCH_KINDS]
            for nid in reversed(branch_nodes):  # inner branches first
                labels[nid] = _label(label_branch(facts, fid, nid, labels))
    counts: dict[str, int] = {}
    for v in labels.values():
        counts[v["concern"]] = counts.get(v["concern"], 0) + 1
    return {
        "schemaVersion": "1.0",
        "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "concerns"},
        "generatedFor": content_hash,
        "rules": {k: {"concern": v[0], "reason": v[1], "description": v[2]} for k, v in RULES.items()},
        "labels": dict(sorted(labels.items())),
        "totals": dict(sorted(counts.items())),
    }


def _display_bindings(A: dict[str, AstDoc], D: dict[str, dict], labels: dict[str, dict]) -> None:
    """R17: `$name = $row['name'];` whose value is only ever echoed belongs to presentation.

    Forward step over the use-def chains: collect every use reached by the definition.
    The definition is a display binding only if there is at least one use and every
    use sits in an output statement (R01), or in another display binding. We iterate
    so that chains of copies are handled.
    """
    for fid, d in D.items():
        a = A[fid]
        for u in d["units"]:
            uses_of: dict[str, list[dict]] = {}
            for x in u["uses"]:
                for did in x["defs"]:
                    uses_of.setdefault(did, []).append(x)
            # only plain copies: `$x = $row['a']`, `$x = "<b>$y</b>"`, `$x = h($y)`. A calculation
            # (`$total = $a + $b`) is logic even when its result is only displayed.
            item_of_def = {dd["id"]: dd["item"] for dd in u["defs"]
                           if dd["kind"] == "assign" and _is_copy(a, dd.get("rhs"))}
            changed = True
            while changed:
                changed = False
                for did, item in item_of_def.items():
                    lab = labels.get(item)
                    if not lab or lab["ruleId"] != "R09" or a.kind(item) != "Stmt_Expression":
                        continue
                    users = uses_of.get(did, [])
                    if users and all(labels.get(x["item"], {}).get("ruleId") in ("R01", "R17") for x in users):
                        labels[item] = _label(("R17", ""))
                        changed = True


COPY_KINDS = frozenset({"Expr_Variable", "Expr_ArrayDimFetch", "Expr_PropertyFetch", "Expr_StaticPropertyFetch",
                        "Scalar_String", "Scalar_Int", "Scalar_Float", "Scalar_InterpolatedString",
                        "InterpolatedStringPart", "Expr_BinaryOp_Concat", "Expr_ConstFetch", "Expr_ClassConstFetch",
                        "Name", "Identifier", "Arg", "Name_FullyQualified"})
FORMAT_CALLS = frozenset({"htmlspecialchars", "htmlentities", "nl2br", "strip_tags", "trim", "ucfirst",
                          "ucwords", "strtoupper", "strtolower", "number_format", "date", "h", "urlencode"})


def _is_copy(a: AstDoc, rhs: str | None) -> bool:
    if not rhs:
        return False
    for x in a.walk(rhs):
        k = a.kind(x)
        if k == "Expr_FuncCall" and a.call_name(x) in FORMAT_CALLS:
            continue
        if k not in COPY_KINDS:
            return False
    return True


def _label(r: tuple[str, str]) -> dict[str, str]:
    rule, detail = r
    concern, reason, _ = RULES[rule]
    out = {"concern": concern, "basis": "abstained" if concern == "undecided" else "rule",
           "ruleId": rule, "reason": reason}
    if detail:
        out["detail"] = detail
    return out


def load_stage(out_dir: Path):
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    asts = [AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]]
    cfgs = [json.loads((out_dir / "cfg" / f"{f['fileId']}.json").read_text(encoding="utf-8")) for f in manifest["files"]]
    dfgs = [json.loads((out_dir / "dfg" / f"{f['fileId']}.json").read_text(encoding="utf-8")) for f in manifest["files"]]
    return manifest, asts, cfgs, dfgs


def run(out_dir: Path) -> dict[str, Any]:
    manifest, asts, cfgs, dfgs = load_stage(out_dir)
    res = build_labels(asts, cfgs, dfgs, manifest["contentHash"])
    (out_dir / "labels.json").write_bytes(canonical_json(res, pretty=True))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.concerns", description="Label statements by concern")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    args = ap.parse_args(argv)
    if not (args.out_dir / "dfg" / "index.json").exists():
        print("error: run `python -m c1.dfg` first", file=sys.stderr)
        return 1
    r = run(args.out_dir)
    print(", ".join(f"{k} {v}" for k, v in r["totals"].items()) + f" -> {args.out_dir}/labels.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
