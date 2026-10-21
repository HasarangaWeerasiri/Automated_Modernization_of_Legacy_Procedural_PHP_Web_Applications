"""Output timeline for Component 2 (format: component2_frontend/CONTEXT.md, "Timeline").

    python -m c1.timeline analysis-out --src <app>   # -> timeline/<fileId>.json + timeline/index.json

One document per file: the output statements of its top-level code in source order
(`sequence`), and those of each function that outputs (`functions`). C2's conventions,
confirmed with Member 01 on 12-13 Oct 2026:

* entry: {id, kind: Stmt_Echo | Stmt_InlineHTML | Expr_Print, raw, loc, enclosedBy, reads, parts?}
  - raw: byte-exact source; for Stmt_InlineHTML the text PHP outputs (attrs.value)
  - loc: the AST's six numbers (lines 1-based; columns/bytes 0-based, end exclusive)
* enclosedBy, outermost -> innermost:
  - loop:   {nodeId, kind, role: "iteration", iterExpr, iterSourceKind, valueVar, keyVar, loc}
  - branch: {nodeId, kind: "Stmt_If", role: "branch", branch: then|elseif|else, condNodeId, condExpr?, loc}
            (no condExpr on else; its condNodeId is the if's condition)
  - switch_case: {nodeId (switch), kind, role: "switch_case", caseNodeId, condExpr?, loc}
  - try_catch:   {nodeId, kind, role: "try_catch", part: try|catch|finally, loc}
* reads: {expr, var, path, sourceKind, confidence, source, derivedFrom?, basis?}
  sourceKind: db_row_field | session | request | server | literal | computed | unresolved
  source (db_row_field): {fetchNodeId, queryNodeId, table, column}
  SELECT * (C2 Q1): ambiguous unless a schema has the table and the query has one table
  and no JOIN; then resolved with basis "schema-expanded".
* call entries (C2 Q4, no inlining): {id, kind: "call", callee, calleeName, outputsInside}
* include entries: {id, kind: "include", target, targetFileId, outputsInside}
* parts (C2 Q16): an echo of an interpolated string lists its parts {id, kind: literal|expr, text?}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import CLASS_LIKE_KINDS, SCOPE_KINDS, AstDoc
from .extract import canonical_json
from .includes import _str_value

LOOPS = {"Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach"}
OUTPUT_KINDS = {"Stmt_Echo", "Stmt_InlineHTML"}


class Timeline:
    def __init__(self, er, sources: dict[str, bytes]):
        """`er` is an EndpointRecovery (it holds the DFG project, auth facts and SQL shapes)."""
        self.er = er
        self.p = er.p
        self.A = er.A
        self.src = sources
        from .concerns import Facts
        self.facts = Facts(self.A, {f: c for f, c in er.auth.C.items()},
                           {f: self.p.file_doc(f) for f in self.A})
        self.queries_used: dict[str, dict[str, Any]] = {}

    def text(self, fid: str, nid: str | None) -> str | None:
        if nid is None or fid not in self.src:
            return None
        loc = self.A[fid].loc(nid)
        if not loc:
            return None
        return self.src[fid][loc["startByte"]:loc["endByte"]].decode("utf-8", "replace")

    # -- enclosures -------------------------------------------------------------------
    def enclosed_by(self, fid: str, nid: str, unit_id: str) -> list[dict[str, Any]]:
        a = self.A[fid]
        chain = []
        child = nid
        for anc in a.ancestors(nid):
            if anc == unit_id:
                break
            k = a.kind(anc)
            if k in SCOPE_KINDS or k in CLASS_LIKE_KINDS:
                break
            e = self.enclosure(fid, anc, child, unit_id)
            if e:
                chain.append(e)
            child = anc
        return list(reversed(chain))

    def enclosure(self, fid: str, anc: str, child: str, unit_id: str) -> dict[str, Any] | None:
        a = self.A[fid]
        k = a.kind(anc)
        loc = a.loc(anc)
        if k in LOOPS:
            head = a.field(anc, "expr") if k == "Stmt_Foreach" else a.field(anc, "cond")
            if isinstance(head, list):
                head = head[-1] if head else None
            if child == head or (isinstance(a.field(anc, "init"), list) and child in a.field(anc, "init")):
                return None  # the loop's own condition is not "inside" the loop body
            value_var = key_var = None
            if k == "Stmt_Foreach":
                value_var = self.text(fid, a.field(anc, "valueVar"))
                key_var = self.text(fid, a.field(anc, "keyVar"))
            elif head and a.kind(head) == "Expr_Assign":
                value_var = self.text(fid, a.field(head, "var"))
            return {"nodeId": anc, "kind": k, "role": "iteration", "iterExpr": self.text(fid, head),
                    "iterSourceKind": self.iter_source_kind(fid, unit_id, anc, head),
                    "valueVar": value_var, "keyVar": key_var, "loc": loc}
        if k == "Stmt_If":
            if child == a.field(anc, "cond"):
                return None
            if child in a.field_list(anc, "stmts"):
                return {"nodeId": anc, "kind": k, "role": "branch", "branch": "then",
                        "condNodeId": a.field(anc, "cond"), "condExpr": self.text(fid, a.field(anc, "cond")), "loc": loc}
            if child in a.field_list(anc, "elseifs"):
                return {"nodeId": anc, "kind": k, "role": "branch", "branch": "elseif",
                        "condNodeId": a.field(child, "cond"), "condExpr": self.text(fid, a.field(child, "cond")),
                        "elseifNodeId": child, "loc": loc}
            if child == a.field(anc, "else"):
                return {"nodeId": anc, "kind": k, "role": "branch", "branch": "else",
                        "condNodeId": a.field(anc, "cond"), "loc": loc}
            return None
        if k in ("Stmt_ElseIf", "Stmt_Else"):
            return None  # described by the enclosing Stmt_If
        if k == "Stmt_Case":
            sw = a.parent(anc)
            rec = {"nodeId": sw, "kind": "Stmt_Switch", "role": "switch_case", "caseNodeId": anc,
                   "loc": a.loc(sw)}
            if a.field(anc, "cond"):
                rec["condExpr"] = self.text(fid, a.field(anc, "cond"))
            else:
                rec["default"] = True
            return rec
        if k == "Stmt_TryCatch":
            part = "try" if child in a.field_list(anc, "stmts") else ("finally" if child == a.field(anc, "finally") else "catch")
            return {"nodeId": anc, "kind": k, "role": "try_catch", "part": part, "loc": loc}
        return None

    def iter_source_kind(self, fid: str, unit_id: str, loop: str, head: str | None) -> str:
        if head is None:
            return "computed"
        o = self.p.origins.of_expr(fid, unit_id, head)
        if any(x.startswith(("row:", "query:", "column:")) for x in o):
            return "db_row_field"
        if any(x.startswith("session:") for x in o):
            return "session"
        if any(x.startswith("input:") for x in o):
            return "request"
        if o <= {"literal"}:
            return "literal"
        return "computed" if not any(x.startswith(("unknown", "undefined", "call", "method", "context")) for x in o) \
            else "unresolved"

    # -- reads -------------------------------------------------------------------------
    def operands(self, fid: str, e: str) -> list[str]:
        """Flatten `.` chains and interpolated strings into the values they join."""
        a = self.A[fid]
        k = a.kind(e)
        if k == "Expr_BinaryOp_Concat":
            return self.operands(fid, a.field(e, "left")) + self.operands(fid, a.field(e, "right"))
        if k == "Scalar_InterpolatedString":
            return [p for p in a.field_list(e, "parts") if a.kind(p) != "InterpolatedStringPart"]
        return [e]

    def read(self, fid: str, unit_id: str, x: str) -> dict[str, Any]:
        a = self.A[fid]
        k = a.kind(x)
        expr = self.text(fid, x)
        base = x
        dims: list[Any] = []
        while a.kind(base) == "Expr_ArrayDimFetch":
            d = a.field(base, "dim")
            dims.insert(0, (_str_value(a.attr(d, "value")) if a.kind(d) == "Scalar_String" else
                            a.attr(d, "value") if a.kind(d) == "Scalar_Int" else "*") if d else "[]")
            base = a.field(base, "var")
        if k in ("Scalar_String", "Scalar_Int", "Scalar_Float", "Expr_ConstFetch", "Expr_ClassConstFetch"):
            return {"expr": expr, "var": None, "path": None, "sourceKind": "literal", "confidence": "resolved", "source": None}
        if a.kind(base) == "Expr_Variable" and isinstance(a.attr(base, "name"), str):
            name = a.attr(base, "name")
            rec: dict[str, Any] = {"expr": expr, "var": "$" + name, "path": dims}
            sg = {"_SESSION": "session", "_GET": "request", "_POST": "request", "_REQUEST": "request",
                  "_COOKIE": "request", "_FILES": "request", "_SERVER": "server"}.get(name)
            if sg:
                rec.update({"sourceKind": sg, "confidence": "resolved", "source": None})
                return rec
            use = self.p.uses_by_outer.get((fid, unit_id), {}).get(base)
            origins = sorted(self.p.origins.of_use(fid, unit_id, use)) if use else ["unknown:no-use"]
            rec.update(self.classify(fid, unit_id, origins, use))
            return rec
        if k == "Expr_StaticPropertyFetch":
            return {"expr": expr, "var": expr, "path": [], "sourceKind": "unresolved", "confidence": "unresolved",
                    "source": None, "reason": "static property"}
        # anything else is computed from the values inside it
        inner = []
        for y in a.walk(x):
            if y == x:
                continue
            ky = a.kind(y)
            if ky == "Expr_ArrayDimFetch" and a.kind(a.parent(y)) != "Expr_ArrayDimFetch" or \
                    (ky == "Expr_Variable" and a.kind(a.parent(y)) != "Expr_ArrayDimFetch") or \
                    ky == "Expr_StaticPropertyFetch":
                inner.append(self.read(fid, unit_id, y))
        conf = "resolved" if inner and all(r["confidence"] == "resolved" for r in inner) else \
            ("unresolved" if not inner else "ambiguous" if any(r["confidence"] == "resolved" for r in inner) else "unresolved")
        rec = {"expr": expr, "var": None, "path": None, "sourceKind": "computed",
               "confidence": conf if inner else "resolved", "source": None, "derivedFrom": inner}
        if a.kind(x) == "Expr_FuncCall":
            rec["call"] = a.call_name(x)
        return rec

    def classify(self, fid: str, unit_id: str, origins: list[str], use: dict | None) -> dict[str, Any]:
        cols = [o for o in origins if o.startswith("column:")]
        others = [o for o in origins if not o.startswith(("column:", "via:", "fn:", "method:"))]
        kinds = set()
        for o in origins:
            if o.startswith("column:") or o.startswith("row:"):
                kinds.add("db_row_field")
            elif o.startswith("session:"):
                kinds.add("session")
            elif o.startswith("input:SERVER"):
                kinds.add("server")
            elif o.startswith("input:"):
                kinds.add("request")
            elif o == "literal":
                kinds.add("literal")
            elif o.startswith(("via:", "fn:", "method:")):
                continue
            else:
                kinds.add("unresolved")
        # a helper that returns a fetched row or `false` (no row): still that column, may be absent
        nullable = len(cols) == 1 and kinds == {"db_row_field", "literal"}
        if len(cols) == 1 and (kinds == {"db_row_field"} or nullable):
            _, q, col = cols[0].split(":", 2)
            sh = self.query_info(q)
            fetch = self.fetch_node(fid, unit_id, use)
            rec = {"sourceKind": "db_row_field", "source": {"fetchNodeId": fetch, "queryNodeId": q,
                                                            "table": (sh.get("tables") or [None])[0], "column": col}}
            if "*" in (sh.get("columns") or ["*"]) and sh.get("kind") == "select":
                tables = sh.get("tables") or []
                table = tables[0] if tables else None
                if table and len(tables) == 1 and not sh.get("join") and col in self.er.schema.get(
                        self.er.schema_lower.get(table.lower(), table), {}):
                    rec.update({"confidence": "resolved", "basis": "schema-expanded"})
                else:
                    rec.update({"confidence": "ambiguous", "reason": "SELECT * without a schema entry for the column"})
            else:
                rec["confidence"] = "resolved" if col in (sh.get("columns") or []) or not sh.get("columns") else "ambiguous"
            if nullable:
                rec["mayBeAbsent"] = True
            return rec
        if len(kinds) == 1 and kinds != {"unresolved"} and not cols:
            k = next(iter(kinds))
            return {"sourceKind": k, "confidence": "resolved", "source": None,
                    **({"origins": origins} if k != "literal" else {})}
        if not kinds - {"unresolved"}:
            return {"sourceKind": "unresolved", "confidence": "unresolved", "source": None, "origins": origins}
        return {"sourceKind": "computed" if len(kinds) > 1 else next(iter(kinds)), "confidence": "ambiguous",
                "source": None, "origins": origins}

    def fetch_node(self, fid: str, unit_id: str, use: dict | None) -> str | None:
        """The fetch call (or foreach) that defines the row variable."""
        if not use:
            return None
        d = self.p.units[(fid, unit_id)]
        a = self.A[fid]
        for did in use["defs"]:
            dd = d.defs.get(did)
            if not dd:
                continue
            if dd["kind"] == "foreach":
                return dd["item"] and a.parent(dd["item"])
            rhs = dd.get("rhs")
            if rhs and a.kind(rhs) in ("Expr_FuncCall", "Expr_MethodCall", "Expr_StaticCall"):
                return rhs
        return None

    def query_info(self, q: str) -> dict[str, Any]:
        sh = self.er.shapes.get(q)
        if sh is None:
            fid = q.split("#")[0]
            unit = next((uid for (f, uid), d in self.p.units.items() if f == fid and
                         any(c["node"] == q for c in d.calls)), None)
            sh = self.er.shape_of(fid, unit, q) if unit else {}
        self.queries_used[q] = {k: sh.get(k) for k in ("kind", "tables", "columns", "sql", "confidence") if k in sh}
        return sh

    # -- entries ------------------------------------------------------------------------
    def unit_sequence(self, fid: str, unit_id: str) -> list[dict[str, Any]]:
        a = self.A[fid]
        out = []
        for nid in a.walk(unit_id):
            k = a.kind(nid)
            if k in OUTPUT_KINDS or k == "Expr_Print":
                out.append(self.output_entry(fid, unit_id, nid))
            elif k == "Stmt_Expression":
                e = a.field(nid, "expr")
                ek = a.kind(e)
                if ek == "Expr_Include":
                    out.append(self.include_entry(fid, unit_id, nid, e))
                elif ek in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                    c = self.call_entry(fid, unit_id, nid, e)
                    if c:
                        out.append(c)
        return out

    def output_entry(self, fid: str, unit_id: str, nid: str) -> dict[str, Any]:
        a = self.A[fid]
        k = a.kind(nid)
        if k == "Stmt_InlineHTML":
            v = a.attr(nid, "value")
            raw = v if isinstance(v, str) else _str_value(v)
        else:
            raw = self.text(fid, nid)
        rec: dict[str, Any] = {"id": nid, "kind": k, "raw": raw, "loc": a.loc(nid),
                               "enclosedBy": self.enclosed_by(fid, nid, unit_id)}
        if k == "Stmt_InlineHTML":
            return rec
        exprs = a.field_list(nid, "exprs") if k == "Stmt_Echo" else [a.field(nid, "expr")]
        reads, parts = [], []
        for e in exprs:
            if a.kind(e) == "Scalar_InterpolatedString":
                for p in a.field_list(e, "parts"):
                    if a.kind(p) == "InterpolatedStringPart":
                        parts.append({"id": p, "kind": "literal", "text": _str_value(a.attr(p, "value"))})
                    else:
                        parts.append({"id": p, "kind": "expr"})
            ops = self.operands(fid, e)
            non_lit = [x for x in ops if a.kind(x) not in ("Scalar_String", "InterpolatedStringPart")]
            if not non_lit:
                reads.append({"expr": self.text(fid, e), "var": None, "path": None, "sourceKind": "literal",
                              "confidence": "resolved", "source": None})
            for x in non_lit:
                reads.append(self.read(fid, unit_id, x))
        rec["reads"] = reads
        if parts:
            rec["parts"] = parts
        return rec

    def include_entry(self, fid: str, unit_id: str, stmt: str, inc: str) -> dict[str, Any]:
        s = self.er.auth.site_res.get(inc, {})
        tgt = s.get("targetFileId") if s.get("status") == "resolved" else None
        outputs = None
        if tgt:
            ta = self.A[tgt]
            outputs = any(ta.kind(n) in OUTPUT_KINDS or ta.kind(n) == "Expr_Print" for n in ta.walk(ta.root_id))
        return {"id": stmt, "kind": "include", "target": s.get("target"), "targetFileId": tgt,
                "status": s.get("status", "unresolved"), "outputsInside": outputs if tgt else "unknown",
                "loc": self.A[fid].loc(stmt), "enclosedBy": self.enclosed_by(fid, stmt, unit_id)}

    def call_entry(self, fid: str, unit_id: str, stmt: str, call: str) -> dict[str, Any] | None:
        a = self.A[fid]
        callee = self.er.auth.callee_of(fid, unit_id, call)
        if callee:
            name = self.er.auth.units[callee].u["name"]
            eff = self.facts.func_effects.get(name.lower()) if self.er.auth.units[callee].u["kind"] == "function" \
                else self.facts.method_effects.get(name.lower())
            if eff is None:
                outputs: Any = "unknown"
            elif "output" in eff:
                outputs = True
            else:
                return None  # a user function that never outputs: not an output event
            return {"id": stmt, "kind": "call", "callee": callee[1], "calleeName": name, "outputsInside": outputs,
                    "loc": a.loc(stmt), "enclosedBy": self.enclosed_by(fid, stmt, unit_id)}
        name = a.call_name(call)
        if name in ("printf", "print_r", "var_dump", "vprintf", "readfile", "var_export"):
            return {"id": stmt, "kind": "call", "callee": None, "calleeName": name, "builtin": True,
                    "outputsInside": True, "loc": a.loc(stmt), "enclosedBy": self.enclosed_by(fid, stmt, unit_id)}
        if a.kind(call) != "Expr_FuncCall":
            return {"id": stmt, "kind": "call", "callee": None, "calleeName": self.text(fid, a.field(call, "name")),
                    "outputsInside": "unknown", "loc": a.loc(stmt), "enclosedBy": self.enclosed_by(fid, stmt, unit_id)}
        return None

    def file_doc(self, fid: str) -> dict[str, Any]:
        a = self.A[fid]
        self.queries_used = {}
        seq = self.unit_sequence(fid, a.root_id)
        funcs = {}
        for u in self.er.auth.C[fid]["units"]:
            if u["kind"] == "main":
                continue
            s = self.unit_sequence(fid, u["id"])
            if s:
                funcs[u["id"]] = {"name": u["name"], "kind": u["kind"], "sequence": s}
        return {"schemaVersion": "1.0", "entrypoint": a.path, "fileId": fid,
                "isPage": fid in self.entry_set, "sequence": seq, "functions": funcs,
                "queries": dict(sorted(self.queries_used.items())),
                "_provenance": {"producer": f"c1-static-analysis-core {__version__}", "stage": "timeline",
                                "generatedFor": self.generated_for}}


def run(out_dir: Path, src_root: Path | None) -> dict[str, Any]:
    from .concerns import load_stage
    from .endpoints import EndpointRecovery
    from .sqlshape import read_schema_dump
    manifest, asts, cfgs, dfgs = load_stage(out_dir)
    includes = json.loads((out_dir / "index" / "includes.json").read_text(encoding="utf-8"))
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    sources: dict[str, bytes] = {}
    schema: dict[str, Any] = {}
    if src_root:
        for f in manifest["files"]:
            p = src_root / f["path"]
            if p.exists():
                sources[f["fileId"]] = p.read_bytes()
        for sf in sorted(p for p in src_root.rglob("*.sql") if ".git" not in p.parts):
            schema.update(read_schema_dump(sf.read_text(encoding="utf-8", errors="replace")))
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        er = EndpointRecovery(asts, cfgs, includes, dfgs, labels, schema, sources)
        tl = Timeline(er, sources)
        tl.generated_for = manifest["contentHash"]
        tl.entry_set = set(er.entry_pages())
        out = out_dir / "timeline"
        out.mkdir(parents=True, exist_ok=True)
        for old_f in out.glob("*.json"):
            old_f.unlink()
        totals = {"files": 0, "entries": 0, "reads": 0, "db_row_field": 0, "resolved": 0}
        files = []
        for f in manifest["files"]:
            doc = tl.file_doc(f["fileId"])
            (out / f"{f['fileId']}.json").write_bytes(canonical_json(doc, pretty=True))
            entries = doc["sequence"] + [e for fn in doc["functions"].values() for e in fn["sequence"]]
            reads = [r for e in entries for r in e.get("reads", [])]
            totals["files"] += 1
            totals["entries"] += len(entries)
            totals["reads"] += len(reads)
            totals["db_row_field"] += sum(1 for r in reads if r["sourceKind"] == "db_row_field")
            totals["resolved"] += sum(1 for r in reads if r["confidence"] == "resolved")
            files.append({"fileId": f["fileId"], "path": f["path"], "isPage": doc["isPage"],
                          "timeline": f"timeline/{f['fileId']}.json", "entries": len(entries)})
    finally:
        sys.setrecursionlimit(old)
    index = {"schemaVersion": "1.0", "generatedFor": manifest["contentHash"], "files": files, "totals": totals}
    (out / "index.json").write_bytes(canonical_json(index, pretty=True))
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.timeline", description="Output timelines for C2")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--src", type=Path, help="legacy app root (raw text, schema dump)")
    args = ap.parse_args(argv)
    if not (args.out_dir / "labels.json").exists():
        print("error: run `python -m c1.concerns` first", file=sys.stderr)
        return 1
    idx = run(args.out_dir, args.src)
    print(", ".join(f"{k} {v}" for k, v in idx["totals"].items()) + f" -> {args.out_dir}/timeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
