"""Authorization gates (Component 1, step 7) and page protection.

    python -m c1.auth analysis-out     # needs ast/, cfg/, index/includes.json, dfg/, labels.json
                                       # writes auth.json

1. Session checks: every branch whose predicate reads `$_SESSION` (directly, through a
   variable, or through a helper whose result comes from the session).
2. Classification, per check:
   * kind:   authentication (is someone signed in: isset/empty/truthiness of a session
             value) | authorization:role (session value compared with a literal) |
             authorization:ownership (session value compared with data or input) | unknown
   * effect: terminating-guard (the failing side always ends in exit) | gates-data (a
             body runs a query or changes state) | display-only (bodies only output) |
             ambiguous. This is the master-reference rule ("IF body contains a database
             query OR modifies state -> AUTHORIZATION; ELSE IF only echo/HTML -> DISPLAY;
             ELSE AMBIGUOUS -> abstain and flag"). RoleCast (Son et al., OOPSLA 2011)
             motivates treating a failed check that exits as the security-relevant
             pattern.
   * serverSide: true for guards and gates (stay in FastAPI as Depends()), false for
             display-only (frontend may render it), null when ambiguous (flagged).
3. Guard points: terminating guards, calls to *guard helpers* (a function that only
   returns normally after a passing check, e.g. WackoPicko `require_login()`), and
   includes of *guard files* (a file whose top level only finishes after a passing check).
4. Protection: a statement is protected when a guard point dominates it (Cooper et al.
   dominators from the CFG stage).
5. Redirect without exit: `header('Location: …')` followed by more statements on some
   path. Execution continues after the redirect, so the "protected" code still runs.
   This is called an Execution After Redirect (EAR) flaw: Doupé et al., "Fear the EAR",
   ACM CCS 2011.

Business decisions are never guessed: a page that reads or writes data without any
guard is reported as `unguarded` with `needsDecision: true` (public page or missing
check?), because only the app owner knows (master reference §9.5).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import AstDoc
from .cfg import COMPOUND_STMTS
from .dfg import build_project_dfg
from .dominance import dominates
from .extract import canonical_json

COMPARE_OPS = frozenset({"Expr_BinaryOp_Equal", "Expr_BinaryOp_Identical", "Expr_BinaryOp_NotEqual",
                         "Expr_BinaryOp_NotIdentical", "Expr_BinaryOp_Greater", "Expr_BinaryOp_GreaterOrEqual",
                         "Expr_BinaryOp_Smaller", "Expr_BinaryOp_SmallerOrEqual", "Expr_BinaryOp_Spaceship"})
LITERAL_KINDS = frozenset({"Scalar_String", "Scalar_Int", "Scalar_Float", "Expr_ConstFetch", "Expr_ClassConstFetch"})
# labels (concerns.py) that read or write application data; R04 (connect) and R05 (SQL text) do not
ACCESS_RULES = frozenset({"R02", "R03", "R14", "R19"})  # R19: possible ORM access, counted (conservative)


class Unit:
    """A CFG unit with the lookups the auth analysis needs."""

    def __init__(self, fid: str, cfg_unit: dict[str, Any]):
        self.fid = fid
        self.u = cfg_unit
        self.id = cfg_unit["id"]
        self.blocks = {b["id"]: b for b in cfg_unit["blocks"]}
        self.succ: dict[str, list[tuple[str, str]]] = {b: [] for b in self.blocks}
        for e in cfg_unit["edges"]:
            self.succ[e["from"]].append((e["to"], e["label"]))
        self.idom = dict(cfg_unit["idom"])
        self.idom[cfg_unit["entry"]] = cfg_unit["entry"]
        self.where: dict[str, tuple[str, int]] = {}
        for b in cfg_unit["blocks"]:
            for i, item in enumerate(b["items"]):
                self.where[item] = (b["id"], i)

    def reach(self, start: str) -> set[str]:
        seen, stack = {start}, [start]
        while stack:
            x = stack.pop()
            for y, _ in self.succ.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return seen

    def side_terminates(self, side: str, other: str) -> bool:
        """Every path from `side` ends in exit, and never joins the other side."""
        r = self.reach(side)
        if other in r:
            return False
        for b in r:
            for y, label in self.succ.get(b, ()):
                if y == self.u["exit"] and label not in ("exit", "error"):
                    return False
        return self.u["exit"] in r

    def point_dominates(self, point: tuple[str, int], item: str) -> bool:
        """Guard point (block, index of guard item) dominates a later statement."""
        if item not in self.where:
            return False
        b, i = self.where[item]
        pb, pi = point
        if b == pb:
            return i > pi
        return pb in self.idom and b in self.idom and dominates(self.idom, pb, b)


class AuthAnalysis:
    def __init__(self, asts, cfgs, includes, dfgs, labels):
        self.A = {a.file_id: a for a in asts}
        self.C = {c["fileId"]: c for c in cfgs}
        self.includes = includes
        self.labels = labels["labels"]
        self.p = build_project_dfg(asts, cfgs, includes)
        self.units: dict[tuple[str, str], Unit] = {}
        for fid, c in self.C.items():
            for u in c["units"]:
                self.units[(fid, u["id"])] = Unit(fid, u)
        self.sources = {}  # node -> source record
        for d in dfgs:
            for u in d["units"]:
                for s in u["sources"]:
                    self.sources[s["node"]] = s
        self.checks: list[dict[str, Any]] = []
        self.guard_units: dict[tuple[str, str], dict[str, Any]] = {}
        self.flags: list[dict[str, Any]] = []

    # -- facts about a predicate ------------------------------------------------------
    def session_keys(self, fid: str, unit_id: str, pred: str) -> list[str]:
        """Identity values a predicate reads: `$_SESSION` keys, and `cookie:<name>` for
        apps that keep their own session id in a cookie (WackoPicko's admin area)."""
        a = self.A[fid]
        keys: set[str] = set()
        for x in a.walk(pred):
            s = self.sources.get(x)
            if s and not s.get("write") and s["superglobal"] in ("SESSION", "COOKIE"):
                name = str(s["dims"][0]) if s["dims"] else "*"
                keys.add(name if s["superglobal"] == "SESSION" else "cookie:" + name)
        for o in self.p.origins.of_expr(fid, unit_id, pred):
            if o.startswith("session:"):
                keys.add(o[8:])
            elif o.startswith("input:COOKIE:"):
                keys.add("cookie:" + o[13:])
        for x in a.walk(pred):
            if a.kind(x) in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                callee = self.callee_of(fid, unit_id, x)
                if callee in self.session_funcs:
                    keys |= self.session_funcs[callee]
        return sorted(keys)

    def callee_of(self, fid: str, unit_id: str, call: str) -> tuple[str, str] | None:
        """Unit of a user function / Class::method / self:: / $this-> call, if unique."""
        a = self.A[fid]
        k = a.kind(call)
        if k == "Expr_FuncCall":
            return self.p.functions.get(a.call_name(call) or "")
        m = a.name_of(a.field(call, "name"))
        if not m:
            return None
        if k == "Expr_StaticCall":
            cls = a.name_of(a.field(call, "class"))
            if cls in ("self", "static"):
                cls = self.p.unit_class.get((fid, unit_id))
            return self.p.methods.get(f"{cls.lstrip(chr(92))}::{m}".lower()) if cls else None
        if k == "Expr_MethodCall":
            var = a.field(call, "var")
            if a.kind(var) == "Expr_Variable" and a.attr(var, "name") == "this":
                cls = self.p.unit_class.get((fid, unit_id))
                return self.p.methods.get(f"{cls}::{m}".lower()) if cls else None
        return None

    def compute_session_funcs(self) -> None:
        """Functions whose result depends on the session, by data *or* control flow.

        `is_logged_in() { if (current_user()) return True; else return False; }` returns
        literals, so data-flow origins alone miss it: the session decides which literal is
        returned (control dependence). A function is session-dependent when one of its
        branch predicates or return values reads the session, directly or through another
        session-dependent function. Iterated to a fixpoint.
        """
        self.session_funcs: dict[tuple[str, str], set[str]] = {}
        for _ in range(10):
            changed = False
            for key, unit in sorted(self.units.items()):
                if unit.u["kind"] not in ("function", "method", "closure", "arrow-fn"):
                    continue
                fid, uid = key
                a = self.A[fid]
                keys: set[str] = set()
                for b in unit.u["blocks"]:
                    if not b["reachable"]:
                        continue
                    t = b.get("terminator")
                    if t and t["kind"] in ("branch", "coalesce"):
                        keys |= set(self.session_keys(fid, uid, t["node"]))
                    for item in b["items"]:
                        if a.kind(item) == "Stmt_Return" and a.field(item, "expr"):
                            keys |= set(self.session_keys(fid, uid, a.field(item, "expr")))
                if keys and self.session_funcs.get(key) != keys:
                    self.session_funcs[key] = keys
                    changed = True
            if not changed:
                break

    def kind_of(self, fid: str, unit_id: str, pred: str) -> str:
        """authentication | authorization:role | authorization:ownership | unknown."""
        a = self.A[fid]
        found = set()
        for x in a.walk(pred):
            k = a.kind(x)
            if k in COMPARE_OPS:
                l, r = a.field(x, "left"), a.field(x, "right")
                ls, rs = self._sess(fid, unit_id, l), self._sess(fid, unit_id, r)
                if ls or rs:
                    side, other = (l, r) if ls else (r, l)
                    if self._presence_test(a, side, other):
                        continue  # strlen($_SESSION['login']) == 0: "is anyone signed in"
                    if a.kind(other) in LITERAL_KINDS:
                        found.add("authorization:role")
                    else:
                        found.add("authorization:ownership")
            elif k == "Expr_FuncCall" and a.call_name(x) == "in_array":
                args = [a.field(y, "value") for y in a.field_list(x, "args")]
                if args and self._sess(fid, unit_id, args[0]):
                    found.add("authorization:role")
        if found:
            return sorted(found)[-1] if len(found) == 1 else "authorization:" + "+".join(sorted(f.split(":")[1] for f in found))
        if self.session_keys(fid, unit_id, pred):
            return "authentication"
        return "unknown"

    def _sess(self, fid: str, unit_id: str, expr: str | None) -> bool:
        if expr is None:
            return False
        a = self.A[fid]
        for x in a.walk(expr):
            s = self.sources.get(x)
            if s and s["superglobal"] in ("SESSION", "COOKIE"):
                return True
        return any(o.startswith(("session:", "input:COOKIE:")) for o in self.p.origins.of_expr(fid, unit_id, expr))

    @staticmethod
    def _presence_test(a: AstDoc, side: str, other: str) -> bool:
        """`strlen($s) == 0`, `count($s) > 0`, `$s == ''`, `$s != null`: tests presence, not value."""
        empty_literal = False
        ok = a.kind(other)
        if ok == "Scalar_String" and a.attr(other, "value") == "":
            empty_literal = True
        elif ok == "Scalar_Int" and a.attr(other, "value") == 0:
            empty_literal = True
        elif ok == "Expr_ConstFetch" and (a.name_of(a.field(other, "name")) or "").lower() in ("null", "false"):
            empty_literal = True
        wrapped = a.kind(side) == "Expr_FuncCall" and a.call_name(side) in ("strlen", "count", "trim", "sizeof", "mb_strlen")
        return empty_literal or (wrapped and ok == "Scalar_Int")

    # -- guard helpers and guard files ------------------------------------------------
    def helper_guard(self, fid: str, unit_id: str, call: str) -> dict[str, Any] | None:
        target = self.callee_of(fid, unit_id, call)
        return self.guard_units.get(target) if target else None

    def guard_summary(self, key: tuple[str, str], checks: list[dict[str, Any]]) -> dict[str, Any] | None:
        """A unit is a guard when every normal way out of it (return / end of code) is
        dominated by the passing side of a terminating session guard."""
        unit = self.units[key]
        guards = [c for c in checks if c["effect"] == "terminating-guard"]
        if not guards:
            return None
        outs = [e for e in unit.u["edges"] if e["to"] == unit.u["exit"] and e["label"] in ("return", "end")
                and unit.blocks[e["from"]]["reachable"]]
        if not outs:
            return None
        for e in outs:
            if not any(dominates(unit.idom, g["_pass"], e["from"]) for g in guards):
                return None
        keys = sorted({k for g in guards for k in g["sessionKeys"]})
        kinds = sorted({g["kind"] for g in guards})
        return {"unit": key[1], "fileId": key[0], "sessionKeys": keys, "kind": kinds[-1] if len(kinds) == 1 else "+".join(kinds),
                "checks": [g["id"] for g in guards]}

    # -- step 1+2: checks in one unit ---------------------------------------------------
    def checks_in_unit(self, key: tuple[str, str]) -> list[dict[str, Any]]:
        fid, uid = key
        unit = self.units[key]
        a = self.A[fid]
        out = []
        for b in unit.u["blocks"]:
            t = b.get("terminator")
            if not t or t["kind"] != "branch" or not b["reachable"]:
                continue
            pred = t["node"]
            keys = self.session_keys(fid, uid, pred)
            if not keys:
                continue
            succ = {label: to for to, label in unit.succ[b["id"]] if label in ("true", "false")}
            if "true" not in succ or "false" not in succ:
                continue
            stmt = t.get("stmt") or pred
            fail = None
            if unit.side_terminates(succ["true"], succ["false"]):
                fail = "true"
            elif unit.side_terminates(succ["false"], succ["true"]):
                fail = "false"
            kind = self.kind_of(fid, uid, pred)
            if fail:
                effect, server = "terminating-guard", True
                pass_block = succ["false" if fail == "true" else "true"]
            else:
                lab = self.labels.get(stmt, {})
                rule = lab.get("ruleId")
                effect, server = {"R06": ("gates-data", True), "R08": ("display-only", False),
                                  "R12": ("display-only", False)}.get(rule, ("ambiguous", None))
                if self.redirects_in(fid, stmt):
                    # meant as a guard, but nothing stops the code after the redirect
                    effect, server = "broken-guard", True
                elif effect == "ambiguous" and self.only_returns(fid, stmt):
                    effect, server = "predicate-helper", True
                pass_block = None
            rec = {
                "id": stmt, "predicate": pred, "fileId": fid, "path": a.path, "line": a.line(stmt),
                "unit": uid, "unitName": unit.u["name"], "sessionKeys": keys, "kind": kind,
                "effect": effect, "serverSide": server,
                "condExpr": self.text(fid, pred),
            }
            if fail:
                rec["failSide"] = fail
                rec["_pass"] = pass_block
            if effect == "ambiguous" or kind == "unknown":
                rec["basis"] = "abstained"
                rec["reason"] = "branch body mixes output and logic, or calls code we cannot classify" \
                    if effect == "ambiguous" else "session value used in a way no rule covers"
            else:
                rec["basis"] = "rule"
            if effect == "broken-guard":
                rec["reason"] = "redirects on a failed check but does not exit: the rest of the page still runs (EAR)"
            out.append(rec)
        return out

    def branch_bodies(self, fid: str, stmt: str) -> list[str]:
        a = self.A[fid]
        if a.kind(stmt) != "Stmt_If":
            return []
        out = list(a.field_list(stmt, "stmts"))
        for ei in a.field_list(stmt, "elseifs"):
            out += a.field_list(ei, "stmts")
        if a.field(stmt, "else"):
            out += a.field_list(a.field(stmt, "else"), "stmts")
        return out

    def redirects_in(self, fid: str, stmt: str) -> bool:
        a = self.A[fid]
        for s in self.branch_bodies(fid, stmt):
            for x in a.walk(s):
                if a.kind(x) == "Expr_FuncCall" and a.call_name(x) == "header":
                    args = a.field_list(x, "args")
                    first = a.field(args[0], "value") if args else None
                    if first and any(isinstance(a.attr(y, "value"), str) and
                                     a.attr(y, "value").lower().lstrip().startswith("location")
                                     for y in a.walk(first)):
                        return True
        return False

    def only_returns(self, fid: str, stmt: str) -> bool:
        a = self.A[fid]
        bodies = self.branch_bodies(fid, stmt)
        return bool(bodies) and all(a.kind(s) == "Stmt_Return" for s in bodies)

    def text(self, fid: str, nid: str) -> str | None:
        src = self.source_bytes(fid)
        loc = self.A[fid].loc(nid)
        if src is None or not loc:
            return None
        return src[loc["startByte"]:loc["endByte"]].decode("utf-8", "replace")

    def source_bytes(self, fid: str) -> bytes | None:
        return getattr(self, "_src", {}).get(fid)

    # -- step 3+4: guard points and protection ---------------------------------------------
    def guard_points(self, key: tuple[str, str]) -> list[dict[str, Any]]:
        fid, uid = key
        unit = self.units[key]
        a = self.A[fid]
        pts = []
        for c in self.by_unit.get(key, []):
            if c["effect"] == "terminating-guard":
                pts.append({"via": "check", "id": c["id"], "point": (c["_pass"], -1), "kind": c["kind"],
                            "sessionKeys": c["sessionKeys"]})
        for item, (b, i) in unit.where.items():
            if a.kind(item) != "Stmt_Expression":
                continue
            e = a.field(item, "expr")
            if a.kind(e) in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                g = self.helper_guard(fid, uid, e)
                if g:
                    pts.append({"via": "helper", "id": item, "helper": self.C[g["fileId"]] and
                                self.units[(g["fileId"], g["unit"])].u["name"], "point": (b, i),
                                "kind": g["kind"], "sessionKeys": g["sessionKeys"]})
            elif a.kind(e) == "Expr_Include":
                tgt = self.include_target(e)
                g = self.guard_units.get((tgt, self.A[tgt].root_id)) if tgt in self.A else None
                if g:
                    pts.append({"via": "include", "id": item, "file": self.A[tgt].path, "point": (b, i),
                                "kind": g["kind"], "sessionKeys": g["sessionKeys"]})
        return pts

    def include_target(self, inc_node: str) -> str | None:
        s = self.site_res.get(inc_node)
        return s.get("targetFileId") if s and s["status"] == "resolved" else None

    def page_report(self, entry_fid: str) -> dict[str, Any]:
        key = (entry_fid, self.A[entry_fid].root_id)
        unit = self.units[key]
        a = self.A[entry_fid]
        pts = self.guard_points(key)
        points = []  # statements that read or write application data
        guard_items = {p["id"] for p in pts}  # a guard call reads the user record itself
        for item in unit.where:
            lab = self.labels.get(item, {})
            if item in guard_items:
                continue
            if lab.get("ruleId") in ACCESS_RULES and a.kind(item) not in COMPOUND_STMTS:
                points.append(item)
            elif a.kind(item) == "Stmt_Expression" and a.kind(a.field(item, "expr")) == "Expr_Include":
                tgt = self.include_target(a.field(item, "expr"))
                if tgt and self.file_does_data(tgt):
                    points.append(item)
        prot, unprot = [], []
        for item in points:
            by = [p["id"] for p in pts if unit.point_dominates(p["point"], item)]
            (prot if by else unprot).append({"node": item, "line": a.line(item), "guards": by} if by else
                                             {"node": item, "line": a.line(item)})
        reachable_points = [x for x in points if unit.blocks[unit.where[x][0]]["reachable"]]
        writes = [x["node"] for x in unprot if self.is_write(entry_fid, x["node"])]
        if not reachable_points:
            status = "no-data-access"
        elif not unprot:
            status = "guarded"
        elif prot:
            status = "partially-guarded"
        else:
            status = "unguarded"
        rep = {"page": a.path, "fileId": entry_fid, "status": status,
               "guards": [{k: v for k, v in p.items() if k != "point"} for p in pts],
               "dataAccess": len(reachable_points), "protected": len(prot), "unprotected": unprot}
        if status in ("unguarded", "partially-guarded"):
            rep["needsDecision"] = True
            rep["unguardedWrites"] = len(writes)
            rep["writesSession"] = self.writes_session(entry_fid)
        return rep

    def file_does_data(self, fid: str) -> bool:
        """Top-level code of an included file runs queries (not just a DB connection)."""
        root = self.A[fid].root_id
        for item in self.units[(fid, root)].where:
            if self.labels.get(item, {}).get("ruleId") in ACCESS_RULES:
                return True
        return False

    def is_write(self, fid: str, item: str) -> bool:
        """The statement runs SQL that starts with INSERT/UPDATE/DELETE/REPLACE (DFG origins)."""
        a = self.A[fid]
        for d in self.dfg_docs[fid]["units"]:
            for q in d["queries"]:
                if q["item"] == item and q["sqlArg"]:
                    for x in a.walk(q["sqlArg"]):
                        pass
                    if self.sql_starts_with_write(fid, d["id"], q["sqlArg"]):
                        return True
        return False

    def sql_starts_with_write(self, fid: str, uid: str, sql_arg: str) -> bool:
        a = self.A[fid]
        texts = []
        for x in a.walk(sql_arg):
            if a.kind(x) in ("Scalar_String", "InterpolatedStringPart"):
                texts.append(a.attr(x, "value"))
        # follow the variable to its assignments (one level is enough for the usual $query = "...")
        d = next(u for u in self.dfg_docs[fid]["units"] if u["id"] == uid)
        defs = {x["id"]: x for x in d["defs"]}
        for use in d["uses"]:
            if use["node"] == sql_arg:
                for did in use["defs"]:
                    rhs = defs.get(did, {}).get("rhs")
                    if rhs:
                        for x in a.walk(rhs):
                            if a.kind(x) in ("Scalar_String", "InterpolatedStringPart"):
                                texts.append(a.attr(x, "value"))
        return any(isinstance(t, str) and t.lstrip().lower().startswith(("insert", "update", "delete", "replace"))
                   for t in texts)

    def writes_session(self, fid: str) -> bool:
        """The page (or a function it calls) writes the session or sets a cookie: a hint
        that it is a login page, which is normally public."""
        a = self.A[fid]
        root = a.root_id
        for s in next(u for u in self.dfg_docs[fid]["units"] if u["id"] == root)["sources"]:
            if s.get("write") and s["superglobal"] == "SESSION":
                return True
        for item in self.units[(fid, root)].where:
            for x in a.walk(item):
                if a.kind(x) == "Expr_FuncCall" and a.call_name(x) in ("setcookie", "session_regenerate_id"):
                    return True
                if a.kind(x) in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                    callee = self.callee_of(fid, root, x)
                    if callee and callee in self.session_writers:
                        return True
        return False

    def compute_session_writers(self) -> None:
        self.session_writers: set[tuple[str, str]] = set()
        for _ in range(10):
            before = len(self.session_writers)
            for key, unit in self.units.items():
                if key in self.session_writers or unit.u["kind"] == "main":
                    continue
                fid, uid = key
                a = self.A[fid]
                d = next(u for u in self.dfg_docs[fid]["units"] if u["id"] == uid)
                if any(s.get("write") and s["superglobal"] == "SESSION" for s in d["sources"]):
                    self.session_writers.add(key)
                    continue
                for item in unit.where:
                    for x in a.walk(item):
                        if a.kind(x) == "Expr_FuncCall" and a.call_name(x) == "setcookie":
                            self.session_writers.add(key)
                        elif a.kind(x) in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                            c = self.callee_of(fid, uid, x)
                            if c in self.session_writers:
                                self.session_writers.add(key)
            if len(self.session_writers) == before:
                break

    # -- step 5: redirect without exit ------------------------------------------------------
    def ear(self, key: tuple[str, str]) -> list[dict[str, Any]]:
        fid, uid = key
        unit = self.units[key]
        a = self.A[fid]
        out = []
        for item, (b, i) in unit.where.items():
            if not unit.blocks[b]["reachable"] or a.kind(item) != "Stmt_Expression":
                continue
            e = a.field(item, "expr")
            if a.kind(e) != "Expr_FuncCall" or a.call_name(e) != "header":
                continue
            args = a.field_list(e, "args")
            first = a.field(args[0], "value") if args else None
            txt = "".join(a.attr(x, "value") or "" for x in a.walk(first) if a.kind(x) in
                          ("Scalar_String", "InterpolatedStringPart")) if first else ""
            if not txt.lower().lstrip().startswith("location"):
                continue
            after = self.statements_after(unit, b, i)
            if after:
                out.append({"node": item, "fileId": fid, "path": a.path, "line": a.line(item), "unit": uid,
                            "statementsAfter": len(after),
                            "dataAccessAfter": sum(1 for x in after if self.labels.get(x, {}).get("concern") in
                                                   ("data_access", "mixed")),
                            "reason": "redirect without exit: code after header('Location') still runs (EAR)"})
        return out

    def statements_after(self, unit: Unit, b: str, i: int) -> list[str]:
        """Statements that can run after item i of block b, stopping at exit edges."""
        a = self.A[unit.fid]
        found: list[str] = []
        blk = unit.blocks[b]
        rest = blk["items"][i + 1:]
        found += [x for x in rest if a.kind(x) not in ("Expr_Exit",)]
        stack = [y for y, label in unit.succ[b] if label != "exit"]
        if any(label == "exit" for _, label in unit.succ[b]) and not any(
                label != "exit" for _, label in unit.succ[b]):
            return found
        seen = set(stack)
        while stack:
            x = stack.pop()
            if x == unit.u["exit"]:
                continue
            found += unit.blocks[x]["items"]
            for y, label in unit.succ[x]:
                if label != "exit" and y not in seen:
                    seen.add(y)
                    stack.append(y)
        return [x for x in found if not self._is_noop_after_redirect(a, x)]

    @staticmethod
    def _is_noop_after_redirect(a: AstDoc, x: str) -> bool:
        k = a.kind(x)
        if k == "Stmt_InlineHTML":
            v = a.attr(x, "value")
            return isinstance(v, str) and not v.strip()
        if k == "Stmt_Expression" and a.kind(a.field(x, "expr")) == "Expr_FuncCall":
            return a.call_name(a.field(x, "expr")) in ("ob_end_flush", "session_write_close")
        return False

    # -- driver ----------------------------------------------------------------------
    def run(self, sources: dict[str, bytes], dfg_docs: dict[str, dict]) -> dict[str, Any]:
        self._src = sources
        self.dfg_docs = dfg_docs
        self.site_res = {s["site"]: s["self"] for s in self.includes["sites"]}
        self.session_funcs = {}
        self.compute_session_funcs()
        self.compute_session_writers()
        self.by_unit: dict[tuple[str, str], list[dict[str, Any]]] = {}
        # guard helpers can sit behind other helpers: iterate to a fixpoint
        for _ in range(6):
            before = dict(self.guard_units)
            self.by_unit = {k: self.checks_in_unit(k) for k in sorted(self.units)}
            for k, cs in self.by_unit.items():
                g = self.guard_summary(k, cs)
                if g:
                    self.guard_units[k] = g
            if self.guard_units == before:
                break
        checks = [c for k in sorted(self.by_unit) for c in self.by_unit[k]]
        pages = [self.page_report(fid) for fid in sorted(self.includes["entries"])]
        ear = [x for k in sorted(self.units) for x in self.ear(k)]
        public_checks = [{k: v for k, v in c.items() if not k.startswith("_")} for c in checks]
        counts: dict[str, int] = {}
        for p in pages:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        eff: dict[str, int] = {}
        for c in checks:
            eff[c["effect"]] = eff.get(c["effect"], 0) + 1
        return {
            "schemaVersion": "1.0",
            "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "auth"},
            "checks": public_checks,
            "guardHelpers": [
                {**g, "name": self.units[(g["fileId"], g["unit"])].u["name"]}
                for _, g in sorted(self.guard_units.items())],
            "pages": pages,
            "redirectWithoutExit": ear,
            "totals": {"checks": len(checks), **{f"effect:{k}": v for k, v in sorted(eff.items())},
                       **{f"pages:{k}": v for k, v in sorted(counts.items())},
                       "redirectWithoutExit": len(ear)},
        }


def run(out_dir: Path, src_root: Path | None = None) -> dict[str, Any]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    asts = [AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]]
    cfgs = [json.loads((out_dir / "cfg" / f"{f['fileId']}.json").read_text(encoding="utf-8")) for f in manifest["files"]]
    dfgs = [json.loads((out_dir / "dfg" / f"{f['fileId']}.json").read_text(encoding="utf-8")) for f in manifest["files"]]
    includes = json.loads((out_dir / "index" / "includes.json").read_text(encoding="utf-8"))
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    sources = {}
    if src_root is not None:
        for f in manifest["files"]:
            p = src_root / f["path"]
            if p.exists():
                sources[f["fileId"]] = p.read_bytes()
    res = analyse(asts, cfgs, includes, dfgs, labels, sources)
    res["generatedFor"] = manifest["contentHash"]
    (out_dir / "auth.json").write_bytes(canonical_json(res, pretty=True))
    return res


def analyse(asts, cfgs, includes, dfgs, labels, sources: dict[str, bytes] | None = None) -> dict[str, Any]:
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        an = AuthAnalysis(asts, cfgs, includes, dfgs, labels)
        return an.run(sources or {}, {d["fileId"]: d for d in dfgs})
    finally:
        sys.setrecursionlimit(old)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.auth", description="Authorization gates and page protection")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--src", type=Path, help="legacy app root (adds condExpr text)")
    args = ap.parse_args(argv)
    if not (args.out_dir / "labels.json").exists():
        print("error: run `python -m c1.concerns` first", file=sys.stderr)
        return 1
    r = run(args.out_dir, args.src)
    print(", ".join(f"{k} {v}" for k, v in r["totals"].items()) + f" -> {args.out_dir}/auth.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
