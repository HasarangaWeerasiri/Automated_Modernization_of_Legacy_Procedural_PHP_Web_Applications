"""Data-flow graph: reaching definitions, use-def chains, and value origins.

    python -m c1.dfg analysis-out        # needs ast/, cfg/, index/includes.json; writes dfg/

Classic reaching-definitions dataflow over our CFG blocks (forward, may-analysis,
iterate to a fixpoint). Then use-def chains, then *origins*: where a used value can come
from (request input, session, a query result column, a literal, a call ...).

Design notes (docs/DESIGN_DFG.md has the full list and sources):
* Variables are function-scoped in PHP. One analysis per CFG unit.
* `$a['k'] = v` and `$o->p = v` are *weak* definitions: they add a definition but do not
  kill earlier ones (we do not track individual array cells).
* An include runs the included file's top-level code in the caller's scope (PHP manual).
  Variables that file assigns are weak definitions at the include site. Their origins
  are looked up in the included file. An unresolved include may define anything: we
  record it as a "havoc" definition that reaches every later use (sound-or-abstain).
* Superglobals (`$_GET`, `$_POST`, `$_SESSION` ...) are sources, not local variables.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .astdoc import CLASS_LIKE_KINDS, SCOPE_KINDS, AstDoc
from .dominance import reverse_postorder
from .extract import canonical_json
from .includes import _str_value

SUPERGLOBALS = {"_GET": "GET", "_POST": "POST", "_REQUEST": "REQUEST", "_COOKIE": "COOKIE",
                "_FILES": "FILES", "_SERVER": "SERVER", "_SESSION": "SESSION", "_ENV": "ENV",
                "GLOBALS": "GLOBALS"}

# function -> 0-based positions of by-reference (written) arguments
BYREF_ARGS = {
    "preg_match": (2,), "preg_match_all": (2,), "parse_str": (1,), "settype": (0,),
    "array_push": (0,), "array_pop": (0,), "array_shift": (0,), "array_unshift": (0,),
    "array_splice": (0,), "sort": (0,), "rsort": (0,), "usort": (0,), "uasort": (0,),
    "uksort": (0,), "ksort": (0,), "krsort": (0,), "asort": (0,), "arsort": (0,),
    "shuffle": (0,), "mysqli_stmt_bind_result": tuple(range(1, 30)), "str_replace": (3,),
    "exec": (1, 2), "openssl_sign": (1,), "each": (0,),
}
HAVOC_CALLS = frozenset({"extract", "eval"})  # write variables we cannot name

QUERY_CALLS = frozenset({"mysqli_query", "mysql_query", "mysql_db_query", "mysql_unbuffered_query",
                         "pg_query", "sqlite_query", "mysqli_real_query", "mysqli_multi_query"})
QUERY_METHODS = frozenset({"query", "prepare", "execute"})
FETCH_CALLS = frozenset({"mysqli_fetch_array", "mysqli_fetch_assoc", "mysqli_fetch_row",
                         "mysqli_fetch_object", "mysql_fetch_array", "mysql_fetch_assoc",
                         "mysql_fetch_row", "mysql_fetch_object", "mysqli_fetch_all",
                         "pg_fetch_assoc", "pg_fetch_array", "mysql_result", "mysqli_fetch_field"})
FETCH_METHODS = frozenset({"fetch", "fetch_assoc", "fetch_array", "fetch_row", "fetch_object",
                           "fetchAll", "fetch_all", "fetchColumn", "fetchObject"})
# calls whose result keeps the origin of their (first) argument
PASS_THROUGH = frozenset({"trim", "ltrim", "rtrim", "intval", "floatval", "strval", "htmlspecialchars",
                          "htmlentities", "strip_tags", "addslashes", "stripslashes", "mysqli_real_escape_string",
                          "mysql_real_escape_string", "mysql_escape_string", "urlencode", "urldecode",
                          "nl2br", "strtolower", "strtoupper", "ucfirst", "ucwords", "md5", "sha1",
                          "number_format", "date", "substr", "sprintf", "json_encode", "base64_encode",
                          "filter_var", "filter_input", "stripcslashes", "escapeshellarg", "rawurlencode"})


PASS_THROUGH_ARG = {"mysqli_real_escape_string": 1, "mysqli_escape_string": 1}


def _var_name(ast: AstDoc, nid: str | None) -> str | None:
    if nid and ast.kind(nid) == "Expr_Variable":
        v = ast.attr(nid, "name")
        return v if isinstance(v, str) else None
    return None


def dim_path(ast: AstDoc, nid: str) -> tuple[str, list[Any]]:
    """For a use node, climb `$x['a'][0]` and return (outermost node, ['a', 0]).
    A dim that is not a literal is '*'."""
    dims: list[Any] = []
    cur = nid
    while True:
        p = ast.parent(cur)
        if p is None or ast.kind(p) != "Expr_ArrayDimFetch" or ast.field(p, "var") != cur:
            break
        d = ast.field(p, "dim")
        if d is None:
            dims.append("[]")
        elif ast.kind(d) == "Scalar_String":
            dims.append(_str_value(ast.attr(d, "value")))
        elif ast.kind(d) == "Scalar_Int":
            dims.append(ast.attr(d, "value"))
        else:
            dims.append("*")
        cur = p
    return cur, dims


class UnitDFG:
    """Reaching definitions + use-def chains for one CFG unit."""

    def __init__(self, ast: AstDoc, unit: dict[str, Any], include_vars, include_targets):
        self.ast = ast
        self.unit = unit
        self.include_vars = include_vars        # site -> sorted var names it may define, or None (havoc)
        self.include_targets = include_targets  # site -> target fileId (resolved only)
        self.items = {i for b in unit["blocks"] for i in b["items"]}
        self.defs: dict[str, dict[str, Any]] = {}
        self.uses: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.flags: list[dict[str, str]] = []
        self.block_events: dict[str, list[tuple]] = {}

    # -- event extraction (evaluation order: reads before the write they feed) ----
    def events_for_item(self, item: str, block: str) -> list[tuple]:
        out: list[tuple] = []
        a = self.ast
        parent = a.parent(item)
        pk = a.kind(parent) if parent else None
        # loop / catch variables listed as items are definitions
        if pk == "Stmt_Foreach" and item in (a.field(parent, "keyVar"), a.field(parent, "valueVar")):
            self.target(item, out, parent, "foreach", strong=True, rhs=a.field(parent, "expr"))
            return out
        if pk == "Stmt_Catch" and item == a.field(parent, "var"):
            self.target(item, out, parent, "catch", strong=True, rhs=None)
            return out
        self.ev(item, item, out)
        return out

    def ev(self, n: str, item: str, out: list[tuple]) -> None:
        a = self.ast
        if n != item and n in self.items:
            return  # evaluated in its own block
        node = a.nodes[n]
        k = node["kind"]
        f = node["fields"]
        if k in CLASS_LIKE_KINDS or k in ("Stmt_Function", "Stmt_ClassMethod"):
            return
        if k == "Expr_Closure":
            for u in a.field_list(n, "uses"):
                name = _var_name(a, a.field(u, "var"))
                if name:
                    out.append(("use", name, a.field(u, "var")))
            return
        if k == "Expr_ArrowFunction":
            return
        if k == "Expr_Variable":
            name = a.attr(n, "name")
            if isinstance(name, str) and name in SUPERGLOBALS:
                outer, dims = dim_path(a, n)
                out.append(("src", SUPERGLOBALS[name], n, dims))
            elif isinstance(name, str):
                if name != "this":
                    out.append(("use", name, n))
            else:
                self.flags.append({"node": n, "reason": "variable-variable ($$x): not tracked"})
                for c in node["children"]:
                    self.ev(c, item, out)
            return
        if k == "Expr_Assign":
            self.ev(f["expr"], item, out)
            self.target(f["var"], out, n, "assign", strong=True, rhs=f["expr"])
            return
        if k.startswith("Expr_AssignOp_"):
            self.ev(f["var"], item, out)
            self.ev(f["expr"], item, out)
            self.target(f["var"], out, n, "compound", strong=True, rhs=n)
            return
        if k == "Expr_AssignRef":
            self.ev(f["expr"], item, out)
            self.target(f["var"], out, n, "ref", strong=True, rhs=f["expr"])
            return
        if k in ("Expr_PreInc", "Expr_PreDec", "Expr_PostInc", "Expr_PostDec"):
            self.ev(f["var"], item, out)
            self.target(f["var"], out, n, "incdec", strong=True, rhs=n)
            return
        if k == "Stmt_Unset":
            for v in a.field_list(n, "vars"):
                name = _var_name(a, v)
                if name:
                    out.append(("def", name, v, "unset", True, None))
                else:
                    self.ev(v, item, out)
            return
        if k == "Stmt_Global":
            for v in a.field_list(n, "vars"):
                name = _var_name(a, v)
                if name:
                    out.append(("def", name, v, "global", True, None))
            return
        if k == "Stmt_Static":
            for sv in a.field_list(n, "vars"):
                if sv and a.field(sv, "default"):
                    self.ev(a.field(sv, "default"), item, out)
                name = _var_name(a, a.field(sv, "var"))
                if name:
                    out.append(("def", name, a.field(sv, "var"), "static", True, a.field(sv, "default")))
            return
        if k == "Expr_FuncCall":
            fname = a.call_name(n)
            args = a.field_list(n, "args")
            byref = BYREF_ARGS.get(fname or "", ())
            for i, arg in enumerate(args):
                if a.kind(arg) != "Arg":
                    self.ev(arg, item, out)
                    continue
                val = a.field(arg, "value")
                self.ev(val, item, out)
                if i in byref:
                    self.target(val, out, n, f"byref:{fname}", strong=True, rhs=n)
            self.calls.append({"node": n, "name": fname, "item": item})
            if fname in HAVOC_CALLS:
                out.append(("havoc", n))
                self.flags.append({"node": n, "reason": f"{fname}() writes variables by name: later uses may come from it"})
            if fname is None:
                self.ev(a.field(n, "name"), item, out)
            return
        if k == "Expr_Eval":
            for c in node["children"]:
                self.ev(c, item, out)
            out.append(("havoc", n))
            self.flags.append({"node": n, "reason": "eval(): may define any variable"})
            return
        if k == "Expr_Include":
            self.ev(f["expr"], item, out)
            out.append(("include", n))
            return
        for c in node["children"]:
            self.ev(c, item, out)

    def target(self, var: str | None, out: list[tuple], at: str, kind: str, strong: bool, rhs: str | None) -> None:
        """Record writes to an assignment target (and the reads inside it, like dims)."""
        if var is None:
            return
        a = self.ast
        k = a.kind(var)
        if k == "Expr_Variable":
            name = a.attr(var, "name")
            if isinstance(name, str) and name in SUPERGLOBALS:
                out.append(("sgwrite", SUPERGLOBALS[name], var, [], rhs))
            elif isinstance(name, str):
                out.append(("def", name, var, kind, strong, rhs))
            else:
                self.flags.append({"node": var, "reason": "write to variable-variable: not tracked"})
            return
        if k in ("Expr_ArrayDimFetch", "Expr_PropertyFetch", "Expr_NullsafePropertyFetch"):
            # reads inside the target ($a[$i], $o->x[$k]) then a weak write of the base variable
            base = var
            dims_reads = []
            while a.kind(base) in ("Expr_ArrayDimFetch", "Expr_PropertyFetch", "Expr_NullsafePropertyFetch"):
                d = a.field(base, "dim") if a.kind(base) == "Expr_ArrayDimFetch" else a.field(base, "name")
                if d is not None:
                    dims_reads.append(d)
                base = a.field(base, "var")
            for d in reversed(dims_reads):
                if d not in self.items:
                    self.ev(d, d, out)
            name = _var_name(a, base)
            if name in SUPERGLOBALS:
                _, dims = dim_path(a, base)
                out.append(("sgwrite", SUPERGLOBALS[name], base, dims, rhs))
            elif name:
                if name != "this":
                    out.append(("def", name, base, kind + ":element", False, rhs))
            else:
                self.ev(base, base, out)
            return
        if k in ("Expr_List", "Expr_Array"):
            for it in a.field_list(var, "items"):
                if a.kind(it) == "ArrayItem":
                    if a.field(it, "key") is not None:
                        self.ev(a.field(it, "key"), it, out)
                    self.target(a.field(it, "value"), out, at, kind + ":list", strong, rhs)
            return
        if k == "Expr_StaticPropertyFetch":
            return
        self.ev(var, var, out)

    # -- dataflow ------------------------------------------------------------
    def run(self) -> None:
        u = self.unit
        a = self.ast
        for b in u["blocks"]:
            evs: list[tuple] = []
            for item in b["items"]:
                for e in self.events_for_item(item, b["id"]):
                    evs.append(e + (item,))
            self.block_events[b["id"]] = evs
        # parameters are definitions at entry
        entry_evs = []
        if u["kind"] in ("function", "method", "closure", "arrow-fn"):
            for p in a.field_list(u["id"], "params"):
                name = _var_name(a, a.field(p, "var"))
                if name:
                    entry_evs.append(("def", name, p, "param", True, a.field(p, "default"), p))
            if u["kind"] == "method":
                entry_evs.append(("def", "this", u["id"], "this", True, None, u["id"]))
        self.block_events[u["entry"]] = entry_evs + self.block_events[u["entry"]]

        succ: dict[str, list[str]] = {b["id"]: [] for b in u["blocks"]}
        preds: dict[str, list[str]] = {b["id"]: [] for b in u["blocks"]}
        for e in u["edges"]:
            if e["to"] not in succ[e["from"]]:
                succ[e["from"]].append(e["to"])
                preds[e["to"]].append(e["from"])
        order = reverse_postorder(succ, u["entry"])
        state_in: dict[str, dict[str, frozenset]] = {}
        state_out: dict[str, dict[str, frozenset]] = {}
        changed = True
        rounds = 0
        while changed and rounds < 200:
            changed = False
            rounds += 1
            for b in order:
                ins = [state_out[p] for p in preds[b] if p in state_out]
                cur = merge(ins)
                state_in[b] = cur
                out = self.transfer(b, dict(cur), record=False)
                if state_out.get(b) != out:
                    state_out[b] = out
                    changed = True
        if rounds >= 200:
            self.flags.append({"node": u["id"], "reason": "dataflow did not converge in 200 rounds"})
        for b in order:
            self.transfer(b, dict(state_in.get(b, {})), record=True)
        self.exit_state = state_in.get(u["exit"], {})

    def transfer(self, b: str, st: dict[str, frozenset], record: bool) -> dict[str, frozenset]:
        for ev in self.block_events[b]:
            kind = ev[0]
            if kind == "use":
                _, name, node, item = ev
                if record:
                    reach = st.get(name, st.get("*", frozenset()))
                    outer, dims = dim_path(self.ast, node)
                    self.uses.append({"node": node, "var": name, "dims": dims, "outer": outer,
                                      "defs": sorted(reach), "block": b, "item": item})
            elif kind == "def":
                _, name, node, dkind, strong, rhs, item = ev
                did = node if dkind != "include" else node
                if record:
                    self.defs[did] = {"id": did, "var": name, "kind": dkind, "strong": strong,
                                      "rhs": rhs, "block": b, "item": item}
                if strong:
                    st[name] = frozenset({did})
                else:
                    st[name] = st.get(name, st.get("*", frozenset())) | {did}
            elif kind == "src":
                _, sg, node, dims, item = ev
                if record:
                    self.sources.append({"node": node, "superglobal": sg, "dims": dims, "block": b, "item": item})
            elif kind == "sgwrite":
                _, sg, node, dims, rhs, item = ev
                if record:
                    self.sources.append({"node": node, "superglobal": sg, "dims": dims, "block": b,
                                         "item": item, "write": True, "rhs": rhs})
            elif kind == "include":
                _, site, item = ev
                names = self.include_vars.get(site)
                if names is None:  # unresolved: may define anything
                    hid = f"{site}:*"
                    if record:
                        self.defs[hid] = {"id": hid, "var": "*", "kind": "include-unresolved", "strong": False,
                                          "rhs": None, "block": b, "item": item}
                    for v in list(st):
                        st[v] = st[v] | {hid}
                    st["*"] = st.get("*", frozenset()) | {hid}
                else:
                    for name in names:
                        did = f"{site}:{name}"
                        if record:
                            self.defs[did] = {"id": did, "var": name, "kind": "include", "strong": False,
                                              "rhs": None, "block": b, "item": item,
                                              "fromFile": self.include_targets.get(site)}
                        st[name] = st.get(name, st.get("*", frozenset())) | {did}
            elif kind == "havoc":
                _, node, item = ev
                hid = f"{node}:*"
                if record:
                    self.defs[hid] = {"id": hid, "var": "*", "kind": "havoc", "strong": False,
                                      "rhs": None, "block": b, "item": item}
                for v in list(st):
                    st[v] = st[v] | {hid}
                st["*"] = st.get("*", frozenset()) | {hid}
        return st


def merge(states: list[dict[str, frozenset]]) -> dict[str, frozenset]:
    if not states:
        return {}
    if len(states) == 1:
        return dict(states[0])
    keys = set().union(*states)
    out = {}
    for k in keys:
        acc: frozenset = frozenset()
        for s in states:
            acc = acc | s.get(k, s.get("*", frozenset()) if k != "*" else frozenset())
        out[k] = acc
    return out


# -- origins ---------------------------------------------------------------------------
class Origins:
    """Where a value can come from. Strings, so they sort and compare simply:

    input:GET:id   session:role   query:<callNode>   row:<callNode>   column:<callNode>:name
    literal   call:<name>   method:<name>   param:<name>   global:<name>   unknown:<why>
    """

    def __init__(self, project: "ProjectDFG"):
        self.p = project
        self.memo: dict[tuple[str, str], frozenset[str]] = {}

    def of_def(self, file_id: str, unit_id: str, did: str, depth: int = 0) -> frozenset[str]:
        key = (file_id, did)
        if key in self.memo:
            return self.memo[key]
        if depth > 40:
            return frozenset({"unknown:depth"})
        self.memo[key] = frozenset({"unknown:cycle"})  # guards loops like $i = $i + 1
        dfg = self.p.units[(file_id, unit_id)]
        d = dfg.defs.get(did)
        res: set[str] = set()
        if d is None:
            res.add("unknown:no-def")
        elif d["kind"] == "param":
            res.add(f"param:{d['var']}")
        elif d["kind"] == "this":
            res.add("this")
        elif d["kind"] == "global":
            res.add(f"global:{d['var']}")
        elif d["kind"] == "unset":
            res.add("unset")
        elif d["kind"] in ("include-unresolved", "havoc"):
            res.add(f"unknown:{d['kind']}")
        elif d["kind"] == "include":
            res |= self.of_include_var(d.get("fromFile"), d["var"], depth + 1)
        elif d["kind"] == "foreach":
            # the loop variable takes elements of the iterated value
            inner = self.of_expr(file_id, unit_id, d["rhs"], depth + 1) if d["rhs"] else {"unknown:foreach"}
            res |= {("row:" + o[6:]) if o.startswith("query:") else o for o in inner}
        elif d["kind"].startswith("byref:"):
            res.add(f"call:{d['kind'][6:]}")
        elif d["rhs"] is not None:
            res |= self.of_expr(file_id, unit_id, d["rhs"], depth + 1)
        else:
            res.add(f"unknown:{d['kind']}")
        out = frozenset(res)
        self.memo[key] = out
        return out

    def of_include_var(self, target: str | None, var: str, depth: int) -> frozenset[str]:
        if target is None or (target, self.p.main_unit(target)) not in self.p.units:
            return frozenset({"unknown:include"})
        tu = self.p.main_unit(target)
        dfg = self.p.units[(target, tu)]
        defs = dfg.exit_state.get(var, dfg.exit_state.get("*", frozenset()))
        res: set[str] = set()
        for did in sorted(defs):
            res |= self.of_def(target, tu, did, depth + 1)
        return frozenset(res or {"unknown:include"})

    def of_use(self, file_id: str, unit_id: str, use: dict[str, Any], depth: int = 0) -> frozenset[str]:
        res: set[str] = set()
        if not use["defs"]:
            # top-level code of an included file reads the includer's variables
            included = file_id in self.p.included_files and unit_id == self.p.main_unit(file_id)
            res.add(f"context:{use['var']}" if included else "undefined")
        for did in use["defs"]:
            res |= self.of_def(file_id, unit_id, did, depth + 1)
        dims = use["dims"]
        if dims:
            res = {self.apply_dims(o, dims) for o in res}
        return frozenset(res)

    def returns_of(self, callee: tuple[str, str], depth: int) -> frozenset[str]:
        """Origins of everything the callee can return (context-insensitive summary)."""
        key = ("return", callee[0] + "|" + callee[1])
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = frozenset({"unknown:recursion"})
        fid, uid = callee
        a = self.p.asts[fid]
        cfg_unit = self.p.unit_index[(fid, uid)]
        res: set[str] = set()
        for b in cfg_unit["blocks"]:
            if not b["reachable"]:
                continue
            for item in b["items"]:
                if a.kind(item) == "Stmt_Return":
                    e = a.field(item, "expr")
                    res |= self.of_expr(fid, uid, e, depth + 1) if e else {"literal"}
        if cfg_unit["kind"] == "arrow-fn":
            res |= self.of_expr(fid, uid, a.field(uid, "expr"), depth + 1)
        out = frozenset(res or {"void"})
        self.memo[key] = out
        return out

    def through_call(self, file_id: str, unit_id: str, callee: tuple[str, str], args: list[str],
                     label: str, depth: int) -> frozenset[str]:
        """Interprocedural step: callee's return origins, with param:x replaced by the
        origins of the matching argument at this call site."""
        if depth > 30:
            return frozenset({label, "unknown:depth"})
        ret = self.returns_of(callee, depth + 1)
        fid, uid = callee
        ca = self.p.asts[fid]
        params = [_var_name(ca, ca.field(p, "var")) for p in ca.field_list(uid, "params")]
        res: set[str] = {label}
        for o in ret:
            if o.startswith("param:"):
                pname = o[6:]
                if pname in params and params.index(pname) < len(args):
                    res |= self.of_expr(file_id, unit_id, args[params.index(pname)], depth + 1)
                else:
                    res.add("literal")  # default value
            else:
                res.add(o)
        return frozenset(res)

    @staticmethod
    def apply_dims(o: str, dims: list[Any]) -> str:
        if o.startswith("row:") and dims and dims[0] not in ("*", "[]", None):
            return f"column:{o[4:]}:{dims[0]}"
        if o.startswith("row:"):
            return o + ":*"
        return o

    def of_expr(self, file_id: str, unit_id: str, expr: str, depth: int = 0) -> frozenset[str]:
        a = self.p.asts[file_id]
        k = a.kind(expr)
        dfg = self.p.units[(file_id, unit_id)]
        if k in ("Scalar_String", "Scalar_Int", "Scalar_Float", "Expr_ConstFetch", "Expr_ClassConstFetch",
                 "Scalar_MagicConst_Dir", "Scalar_MagicConst_File", "Scalar_MagicConst_Line"):
            return frozenset({"literal"})
        if k == "Expr_FuncCall":
            name = a.call_name(expr)
            args = [a.field(x, "value") for x in a.field_list(expr, "args") if a.kind(x) == "Arg"]
            if name in QUERY_CALLS:
                return frozenset({f"query:{expr}"})
            if name in FETCH_CALLS and args:
                inner = self.of_expr(file_id, unit_id, args[0], depth + 1)
                rows = {("row:" + o[6:]) for o in inner if o.startswith("query:")}
                return frozenset(rows or {f"call:{name}"})
            if name in PASS_THROUGH and args:
                # mysqli_real_escape_string($link, $value): the value is the second argument
                i = PASS_THROUGH_ARG.get(name, 0)
                if i < len(args):
                    return self.of_expr(file_id, unit_id, args[i], depth + 1) | {f"via:{name}"}
            callee = self.p.functions.get(name or "")
            if callee is not None:
                return self.through_call(file_id, unit_id, callee, args, f"fn:{name}", depth)
            # unknown function: its result may depend on any argument
            res_call = {f"call:{name or '?'}"}
            for x in args:
                res_call |= {o for o in self.of_expr(file_id, unit_id, x, depth + 1) if o != "literal"}
            return frozenset(res_call)
        if k in ("Expr_MethodCall", "Expr_NullsafeMethodCall", "Expr_StaticCall"):
            mname = a.name_of(a.field(expr, "name")) or "?"
            if mname in QUERY_METHODS:
                return frozenset({f"query:{expr}"})
            if mname in FETCH_METHODS:
                recv = a.field(expr, "var") if k != "Expr_StaticCall" else None
                inner = self.of_expr(file_id, unit_id, recv, depth + 1) if recv else frozenset()
                rows = {("row:" + o[6:]) for o in inner if o.startswith("query:")}
                return frozenset(rows or {f"method:{mname}"})
            cls = a.name_of(a.field(expr, "class")) if k == "Expr_StaticCall" else None
            margs = [a.field(x, "value") for x in a.field_list(expr, "args") if a.kind(x) == "Arg"]
            callee = None
            if cls in ("self", "static") or (k != "Expr_StaticCall" and a.kind(a.field(expr, "var")) == "Expr_Variable"
                                              and a.attr(a.field(expr, "var"), "name") == "this"):
                owner = self.p.unit_class.get((file_id, unit_id))
                callee = self.p.methods.get(f"{owner}::{mname}".lower()) if owner else None
                cls = owner
            elif cls:
                callee = self.p.methods.get(f"{cls.lstrip(chr(92))}::{mname}".lower())
            if callee is not None:
                return self.through_call(file_id, unit_id, callee, margs, f"method:{cls}::{mname}", depth)
            res_m = {f"method:{cls + '::' if cls else ''}{mname}"}
            for x in a.field_list(expr, "args"):
                if a.kind(x) == "Arg":
                    res_m |= {o for o in self.of_expr(file_id, unit_id, a.field(x, "value"), depth + 1) if o != "literal"}
            return frozenset(res_m)
        # any other expression: combine the origins of the sources, variables and calls in it
        res: set[str] = set()
        uses_here = self.p.uses_by_outer.get((file_id, unit_id), {})
        srcs_here = self.p.srcs_by_node.get(file_id, {})
        for x in a.walk(expr):
            if x in srcs_here:
                s = srcs_here[x]
                if not s.get("write"):
                    key = s["dims"][0] if s["dims"] and s["dims"][0] not in ("*", "[]") else "*"
                    res.add(f"input:{s['superglobal']}:{key}" if s["superglobal"] != "SESSION"
                            else f"session:{key}")
            if x in uses_here:
                res |= self.of_use(file_id, unit_id, uses_here[x], depth + 1)
            if x != expr and a.kind(x) in ("Expr_FuncCall", "Expr_MethodCall", "Expr_StaticCall"):
                res |= self.of_expr(file_id, unit_id, x, depth + 1)
        if not res:
            res.add("literal")
        return frozenset(res)


class ProjectDFG:
    def __init__(self, asts: list[AstDoc], cfgs: list[dict[str, Any]], includes: dict[str, Any]):
        self.asts = {a.file_id: a for a in asts}
        self.cfgs = {c["fileId"]: c for c in cfgs}
        site_res = {s["site"]: s["self"] for s in includes["sites"]}
        file_vars = self.toplevel_vars(asts)
        closure_vars = self.closure_vars(file_vars, includes)
        self.units: dict[tuple[str, str], UnitDFG] = {}
        for c in cfgs:
            a = self.asts[c["fileId"]]
            for u in c["units"]:
                inc_vars: dict[str, Any] = {}
                inc_targets: dict[str, str] = {}
                for s in u["includeSites"]:
                    r = site_res.get(s["node"], {"status": "unresolved"})
                    if r["status"] == "resolved" and r.get("targetFileId"):
                        inc_vars[s["node"]] = sorted(closure_vars.get(r["targetFileId"], set()))
                        inc_targets[s["node"]] = r["targetFileId"]
                    elif r["status"] == "excluded":
                        inc_vars[s["node"]] = []  # vendored library: assume it defines no page variables
                    else:
                        inc_vars[s["node"]] = None
                d = UnitDFG(a, u, inc_vars, inc_targets)
                self.units[(c["fileId"], u["id"])] = d
        self.included_files = set(includes.get("includedBy", {}))
        # callee indexes for the interprocedural origin step. A name defined twice is
        # left out (ambiguous), so calls to it stay `call:` / `method:` origins.
        self.unit_index = {(c["fileId"], u["id"]): u for c in cfgs for u in c["units"]}
        self.functions: dict[str, tuple[str, str]] = {}
        self.methods: dict[str, tuple[str, str]] = {}
        self.unit_class: dict[tuple[str, str], str] = {}
        dup_f: set[str] = set()
        dup_m: set[str] = set()
        for (fid, uid), u in self.unit_index.items():
            a = self.asts[fid]
            if u["kind"] == "function":
                key = u["name"].lower()
                (dup_f.add(key) if key in self.functions else None)
                self.functions[key] = (fid, uid)
            elif u["kind"] == "method":
                key = u["name"].lower()
                (dup_m.add(key) if key in self.methods else None)
                self.methods[key] = (fid, uid)
                self.unit_class[(fid, uid)] = u["name"].split("::")[0]
        for key in dup_f:
            del self.functions[key]
        for key in dup_m:
            del self.methods[key]
        # closures / nested units inside a method share its class for self:: / $this
        for (fid, uid), u in self.unit_index.items():
            if u["kind"] in ("closure", "arrow-fn"):
                for anc in self.asts[fid].ancestors(uid):
                    if (fid, anc) in self.unit_class:
                        self.unit_class[(fid, uid)] = self.unit_class[(fid, anc)]
                        break
        # every unit runs before any origin is asked, so exit states of included files exist
        for key in sorted(self.units):
            self.units[key].run()
        self.uses_by_outer: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.srcs_by_node: dict[str, dict[str, dict[str, Any]]] = {}
        for key, d in self.units.items():
            self.uses_by_outer[key] = {u["node"]: u for u in d.uses}
            m = self.srcs_by_node.setdefault(key[0], {})
            for s in d.sources:
                m[s["node"]] = s
        self.origins = Origins(self)

    def main_unit(self, file_id: str) -> str:
        return self.asts[file_id].root_id

    @staticmethod
    def toplevel_vars(asts: list[AstDoc]) -> dict[str, set[str]]:
        """Variable names a file's top-level code may write (what it leaks into an includer)."""
        out: dict[str, set[str]] = {}
        for a in asts:
            names: set[str] = set()
            for nid in a.walk(a.root_id):
                k = a.kind(nid)
                if k in ("Expr_Assign", "Expr_AssignRef") or k.startswith("Expr_AssignOp_"):
                    for x in a.walk(a.field(nid, "var")):
                        nm = _var_name(a, x)
                        if nm and nm not in SUPERGLOBALS and nm != "this":
                            names.add(nm)
                elif k == "Stmt_Foreach":
                    for f in ("keyVar", "valueVar"):
                        nm = _var_name(a, a.field(nid, f))
                        if nm:
                            names.add(nm)
                elif k in ("Stmt_Global", "Stmt_Static"):
                    for x in a.walk(nid):
                        nm = _var_name(a, x)
                        if nm:
                            names.add(nm)
            out[a.file_id] = names
        return out

    @staticmethod
    def closure_vars(file_vars: dict[str, set[str]], includes: dict[str, Any]) -> dict[str, set[str]]:
        """A file's own top-level writes plus those of everything it includes (cycle-safe)."""
        children: dict[str, set[str]] = {}
        for s in includes["sites"]:
            r = s["self"]
            if r["status"] == "resolved" and r.get("targetFileId"):
                children.setdefault(s["fileId"], set()).add(r["targetFileId"])
        out: dict[str, set[str]] = {}
        for f in file_vars:
            seen, stack, acc = {f}, [f], set()
            while stack:
                x = stack.pop()
                acc |= file_vars.get(x, set())
                for c in children.get(x, ()):
                    if c not in seen:
                        seen.add(c)
                        stack.append(c)
            out[f] = acc
        return out

    # -- output ------------------------------------------------------------------
    def file_doc(self, file_id: str) -> dict[str, Any]:
        cfg = self.cfgs[file_id]
        units = []
        for u in cfg["units"]:
            d = self.units[(file_id, u["id"])]
            uses = []
            for use in d.uses:
                uses.append({**{k: use[k] for k in ("node", "var", "dims", "defs", "block", "item")},
                             "origins": sorted(self.origins.of_use(file_id, u["id"], use))})
            sources = []
            for s in d.sources:
                rec = {k: s[k] for k in ("node", "superglobal", "dims", "block", "item")}
                if s.get("write"):
                    rec["write"] = True
                    rec["origins"] = sorted(self.origins.of_expr(file_id, u["id"], s["rhs"])) if s.get("rhs") else []
                sources.append(rec)
            defs = [{k: v for k, v in dd.items() if v is not None} for _, dd in sorted(d.defs.items())]
            for dd in defs:
                dd["origins"] = sorted(self.origins.of_def(file_id, u["id"], dd["id"]))
            units.append({"id": u["id"], "name": u["name"], "kind": u["kind"],
                          "defs": defs, "uses": uses, "sources": sources,
                          "queries": self.queries(file_id, d),
                          "exitVars": {k: sorted(v) for k, v in sorted(d.exit_state.items())},
                          "flags": d.flags})
        return {"schemaVersion": "1.0", "fileId": file_id, "path": cfg["path"], "units": units}

    def queries(self, file_id: str, d: UnitDFG) -> list[dict[str, Any]]:
        """SQL call sites with the origins of their SQL argument (C3 owns SQL parsing)."""
        a = self.asts[file_id]
        out = []
        for c in d.calls:
            if c["name"] in QUERY_CALLS:
                args = [a.field(x, "value") for x in a.field_list(c["node"], "args") if a.kind(x) == "Arg"]
                sql = args[1] if c["name"].startswith("mysqli") and len(args) > 1 else (args[0] if args else None)
                out.append({"node": c["node"], "call": c["name"], "sqlArg": sql, "item": c["item"],
                            "sqlOrigins": sorted(self.origins.of_expr(file_id, d.unit["id"], sql)) if sql else []})
        return out


def build_project_dfg(asts, cfgs, includes) -> ProjectDFG:
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        return ProjectDFG(asts, cfgs, includes)
    finally:
        sys.setrecursionlimit(old)


def run(out_dir: Path, pretty: bool = False) -> dict[str, Any]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    asts = [AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]]
    cfgs = [json.loads((out_dir / "cfg" / f"{f['fileId']}.json").read_text(encoding="utf-8")) for f in manifest["files"]]
    includes = json.loads((out_dir / "index" / "includes.json").read_text(encoding="utf-8"))
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        p = ProjectDFG(asts, cfgs, includes)
        dfg_dir = out_dir / "dfg"
        dfg_dir.mkdir(parents=True, exist_ok=True)
        for old_f in dfg_dir.glob("*.json"):
            old_f.unlink()
        totals = {"defs": 0, "uses": 0, "sources": 0, "queries": 0, "undefinedUses": 0, "flags": 0}
        for f in manifest["files"]:
            doc = p.file_doc(f["fileId"])
            (dfg_dir / f"{f['fileId']}.json").write_bytes(canonical_json(doc, pretty))
            for u in doc["units"]:
                totals["defs"] += len(u["defs"])
                totals["uses"] += len(u["uses"])
                totals["sources"] += len(u["sources"])
                totals["queries"] += len(u["queries"])
                totals["flags"] += len(u["flags"])
                totals["undefinedUses"] += sum(1 for x in u["uses"] if "undefined" in x["origins"])
    finally:
        sys.setrecursionlimit(old)
    index = {"schemaVersion": "1.0", "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "dfg"},
             "generatedFor": manifest["contentHash"], "totals": totals}
    (dfg_dir / "index.json").write_bytes(canonical_json(index, pretty=True))
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.dfg", description="Reaching definitions, use-def chains, origins")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    if not (args.out_dir / "index" / "includes.json").exists():
        print("error: run `python -m c1.includes` first", file=sys.stderr)
        return 1
    idx = run(args.out_dir, args.pretty)
    print(", ".join(f"{k} {v}" for k, v in idx["totals"].items()) + f" -> {args.out_dir}/dfg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
