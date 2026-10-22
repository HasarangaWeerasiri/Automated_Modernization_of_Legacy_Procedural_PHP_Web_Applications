"""Endpoint recovery, CRUD mapping and response shape (Component 1 steps 5, 6, 8).

    python -m c1.endpoints analysis-out --src <app> [--schema dump.sql]
        -> endpoints.json       full C1 view (used to generate the contract)
        -> endpoint-map.json    for C4 (same structure as its "c4-stand-in/1" map) and C2
                                (page -> endpoints, queryNodeId -> endpoint)

Step 5, cluster into endpoints. Each legacy page is an HTTP entry point. Inside it:
  * a *handler* is a branch whose condition reads request input (`isset($_POST['x'])`,
    `$_GET['action'] == ...`, `$_SERVER['REQUEST_METHOD']`). The queries it reaches,
    directly or through called functions, form one write/search endpoint;
  * a SELECT outside every handler is a *render* query. Each one becomes its own GET
    endpoint (C2 maps a List to the query its loop fetches: "per-query endpoints").
Step 8, CRUD mapping (master reference table):
  SELECT -> GET /api/{t}   · key column compared with input -> GET /api/{t}/{col}
  INSERT -> POST /api/{t}  · UPDATE -> PUT /api/{t}/{col}  · DELETE -> DELETE /api/{t}/{col}
  WHERE compared with a session value -> GET /api/me/{t} (filtered by the token's user)
  login handler (checks a password column and writes the session) -> POST /api/auth/login
Step 6, response shape: only the columns the page actually uses, through the DFG
  origins `column:<query>:<name>` (not the full `SELECT *`). Types come from the schema.

Never guessed (flagged instead): a legacy GET that writes data ("mutating GET",
supervisor decision pending), a page with data access and no guard (needsDecision: the
endpoint is locked by default, `securedByDefault`, until a person marks it public),
dynamic SQL structure, unknown column types, sensitive fields (password-like).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import SCOPE_KINDS, AstDoc
from .auth import AuthAnalysis
from .dfg import QUERY_CALLS, QUERY_METHODS
from .extract import canonical_json
from .sqlshape import json_type, read_schema_dump, shape, templates

INPUT_SG = {"GET": "query", "POST": "body", "REQUEST": "query"}
KEY_COLUMN = re.compile(r"^(id|.*_id|.*id|pid|uid)$", re.I)
SENSITIVE = re.compile(r"(pass(word)?|pwd|secret|salt|token|hash)", re.I)
WRITE_ORDER = {"delete": 4, "update": 3, "insert": 2, "replace": 2, "select": 1}


def _resource(table: str | None) -> str:
    return re.sub(r"[^a-z0-9_]", "_", (table or "unknown").lower())


class EndpointRecovery:
    def __init__(self, asts, cfgs, includes, dfgs, labels, schema: dict[str, dict[str, Any]] | None,
                 sources: dict[str, bytes] | None = None):
        self.auth = AuthAnalysis(asts, cfgs, includes, dfgs, labels)
        self.auth_result = self.auth.run(sources or {}, {d["fileId"]: d for d in dfgs})
        self.p = self.auth.p
        self.A = self.auth.A
        self.labels = labels["labels"]
        self.includes = includes
        self.schema = schema or {}
        self.schema_lower = {t.lower(): t for t in self.schema}
        self.flags: list[dict[str, Any]] = []
        self.queries_by_unit: dict[tuple[str, str], list[str]] = {}
        for key, d in self.p.units.items():
            self.queries_by_unit[key] = [c["node"] for c in d.calls if c["name"] in QUERY_CALLS]
        # method-style queries ($db->query("..."))
        for key in self.p.units:
            fid, uid = key
            a = self.A[fid]
            for b in self.auth.units[key].u["blocks"]:
                for item in b["items"]:
                    for x in a.walk(item):
                        if a.kind(x) == "Expr_MethodCall" and a.name_of(a.field(x, "name")) in ("query", "exec"):
                            self.queries_by_unit[key].append(x)
        self.shapes: dict[str, dict[str, Any]] = {}
        self.used_columns = self._used_columns()

    # -- facts ----------------------------------------------------------------------
    def sql_arg(self, fid: str, call: str) -> str | None:
        a = self.A[fid]
        args = [a.field(x, "value") for x in a.field_list(call, "args") if a.kind(x) == "Arg"]
        if a.kind(call) == "Expr_FuncCall" and (a.call_name(call) or "").startswith("mysqli") and len(args) > 1:
            return args[1]
        return args[0] if args else None

    def shape_of(self, fid: str, uid: str, call: str) -> dict[str, Any]:
        if call not in self.shapes:
            arg = self.sql_arg(fid, call)
            if arg is None:
                self.shapes[call] = {"kind": "unknown", "confidence": "unresolved", "tables": [], "params": []}
            else:
                ts = templates(self.p, fid, uid, arg)
                sh = shape(ts[0])
                sh["variants"] = len(ts)
                if len(ts) > 1:
                    kinds = {shape(t)["kind"] for t in ts}
                    if len(kinds) > 1:
                        sh["confidence"] = "ambiguous"
                        sh["reason"] = "conditional SQL builds different statement kinds"
                sh["sql"] = ts[0].text().replace("\x00", "§")
                self.shapes[call] = sh
        return self.shapes[call]

    def _used_columns(self) -> dict[str, set[str]]:
        """query node -> columns used anywhere (output, conditions, copies), from DFG origins."""
        out: dict[str, set[str]] = {}
        for d in self.p.units.values():
            pass
        for (fid, uid), d in self.p.units.items():
            for use in d.uses:
                for o in self.p.origins.of_use(fid, uid, use):
                    if o.startswith("column:"):
                        _, q, col = o.split(":", 2)
                        q = q if "#" in q else q
                        out.setdefault(_rejoin(o), set()).add(col)
        return out

    def queries_reached(self, fid: str, uid: str, stmt: str, seen: frozenset = frozenset(),
                        chain: tuple = ()) -> list[tuple]:
        """Queries a statement runs, directly or through user functions.

        Returns (fid, uid, call, names, chain). `chain` holds one (caller fid, caller unit,
        call node, callee) per call on the way, so a query value that is a parameter of
        the callee can be traced back to the argument at the call site.
        """
        a = self.A[fid]
        out = []
        own = set(self.queries_by_unit.get((fid, uid), ()))
        for x in a.walk(stmt):
            if x in own:
                out.append((fid, uid, x, [], chain))
            k = a.kind(x)
            if k in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                callee = self.auth.callee_of(fid, uid, x)
                if callee and callee not in seen:
                    cfid, cuid = callee
                    name = self.auth.units[callee].u["name"]
                    for s in self.A[cfid].field_list(cuid, "stmts"):
                        for q in self.queries_reached(cfid, cuid, s, seen | {callee}, chain + ((fid, uid, x, callee),)):
                            out.append((q[0], q[1], q[2], [name] + q[3], q[4]))
        return out

    def bind(self, origins: list[str] | frozenset, chain: tuple) -> set[str]:
        """Replace `param:x` by the origins of the argument passed at each call site."""
        out: set[str] = set()
        for o in origins:
            if o.startswith("param:") and chain:
                cfid, cuid, call, callee = chain[-1]
                ca = self.A[callee[0]]
                names = [ca.attr(ca.field(p, "var"), "name") for p in ca.field_list(callee[1], "params")]
                args = [self.A[cfid].field(x, "value") for x in self.A[cfid].field_list(call, "args")
                        if self.A[cfid].kind(x) == "Arg"]
                pname = o[6:]
                if pname in names and names.index(pname) < len(args):
                    sub = self.p.origins.of_expr(cfid, cuid, args[names.index(pname)])
                    out |= self.bind(sub, chain[:-1])
                else:
                    out.add("literal")
            else:
                out.add(o)
        return out

    def trigger(self, fid: str, uid: str, cond: str) -> dict[str, Any] | None:
        """Request input that decides a handler branch."""
        a = self.A[fid]
        keys = []
        for x in a.walk(cond):
            s = self.auth.sources.get(x)
            if s and not s.get("write") and s["superglobal"] in ("GET", "POST", "REQUEST"):
                keys.append((s["superglobal"], s["dims"][0] if s["dims"] else "*"))
            if s and s["superglobal"] == "SERVER" and s["dims"] and s["dims"][0] == "REQUEST_METHOD":
                lit = [a.attr(y, "value") for y in a.walk(cond) if a.kind(y) == "Scalar_String"]
                keys.append(("METHOD", (lit[0] if lit else "*").upper()))
        if not keys:
            return None
        method = "POST" if any(k[0] == "POST" or (k[0] == "METHOD" and k[1] == "POST") for k in keys) else "GET"
        fixed = {str(k[1]): "1" for k in keys if k[0] in ("GET", "POST", "REQUEST") and a.kind(cond) in
                 ("Expr_Isset", "Expr_BooleanNot", "Expr_BinaryOp_BooleanAnd", "Expr_BinaryOp_LogicalAnd")}
        values = {}
        for x in a.walk(cond):
            if a.kind(x) in ("Expr_BinaryOp_Equal", "Expr_BinaryOp_Identical"):
                l, r = a.field(x, "left"), a.field(x, "right")
                for side, other in ((l, r), (r, l)):
                    s = self.auth.sources.get(next((y for y in a.walk(side) if y in self.auth.sources), ""), None)
                    if s and s["superglobal"] in ("GET", "POST", "REQUEST") and a.kind(other) == "Scalar_String":
                        values[str(s["dims"][0] if s["dims"] else "*")] = a.attr(other, "value")
        fixed.update(values)
        return {"method": method, "keys": sorted({f"{k[0]}:{k[1]}" for k in keys}), "fixed": dict(sorted(fixed.items()))}

    def handler_regions(self, fid: str) -> list[dict[str, Any]]:
        a = self.A[fid]
        uid = a.root_id
        regions = []
        for nid in a.walk(uid):
            if a.kind(nid) != "Stmt_If":
                continue
            arms = [(a.field(nid, "cond"), a.field_list(nid, "stmts"))]
            arms += [(a.field(ei, "cond"), a.field_list(ei, "stmts")) for ei in a.field_list(nid, "elseifs")]
            for cond, body in arms:
                t = self.trigger(fid, uid, cond)
                if t:
                    regions.append({"stmt": nid, "cond": cond, "body": body, "trigger": t, "line": a.line(nid)})
        return regions

    def repeated_fields(self, fid: str, region: dict) -> list[dict[str, Any]]:
        """Request fields the handler requires to be equal, e.g. `if ($password == $cpassword)`:
        a generated request must send the same value in both, or the legacy page rejects it."""
        a = self.A[fid]
        uid = a.root_id
        out = []
        for st in region["body"]:
            for x in a.walk(st):
                if a.kind(x) not in ("Expr_BinaryOp_Equal", "Expr_BinaryOp_Identical"):
                    continue
                sides = []
                for side in (a.field(x, "left"), a.field(x, "right")):
                    o = {v for v in self.p.origins.of_expr(fid, uid, side) if not v.startswith("via:")}
                    ins = {v for v in o if v.startswith("input:")}
                    sides.append(next(iter(ins)) if len(ins) == 1 and o == ins else None)
                if None in sides or sides[0] == sides[1] or sides[0].split(":")[1] != sides[1].split(":")[1]:
                    continue
                k1, k2 = sides[0].split(":", 2)[2], sides[1].split(":", 2)[2]
                # the confirmation field is the one named after the other (cpassword, password_confirm, ...)
                rep, orig = (k1, k2) if _repeats(k1, k2) and not _repeats(k2, k1) else (k2, k1)
                out.append({"field": rep, "same_as": orig, "line": a.line(x)})
        return sorted({(d["field"], d["same_as"]): d for d in out}.values(), key=lambda d: (d["field"], d["same_as"]))

    @staticmethod
    def inside(a: AstDoc, node: str, roots: list[str]) -> bool:
        if node in roots:
            return True
        return any(anc in roots for anc in a.ancestors(node))

    # -- endpoints ----------------------------------------------------------------------
    def entry_pages(self) -> list[str]:
        """Files never included by another file, plus files a form posts to or a link
        points at (HMS posts its login form straight to func.php, which other pages also
        include). Any .php file is reachable over HTTP; these are the ones the app uses."""
        entries = set(self.includes["entries"])
        by_path = {self.A[f].path: f for f in self.A}
        pat = re.compile(r"""(?:action|href)\s*=\s*["']?([A-Za-z0-9_./-]+\.php)""", re.I)
        for fid, a in self.A.items():
            base = a.path.rsplit("/", 1)[0] if "/" in a.path else ""
            for n in a.doc["nodes"]:
                v = n["attrs"].get("value")
                if n["kind"] in ("Stmt_InlineHTML", "Scalar_String", "InterpolatedStringPart") and isinstance(v, str):
                    for m in pat.finditer(v):
                        target = m.group(1)
                        path = _norm(target.lstrip("/")) if target.startswith("/") else _norm(f"{base}/{target}" if base else target)
                        if path in by_path:
                            entries.add(by_path[path])
        return sorted(entries)

    def page_files(self, entry: str) -> list[tuple[str, str | None]]:
        """(fileId, include statement in the entry page that leads to it). The included
        files' top-level code runs as part of the request to the entry page."""
        if entry not in self.includes["entries"]:
            return self._closure_from_sites(entry)
        info = self.includes["entries"][entry]
        by_path = {self.A[f].path: f for f in self.A}
        parent: dict[str, str] = {}
        for e in info["edges"]:
            src, dst = by_path.get(e["from"]), by_path.get(e["to"])
            if dst and dst not in parent and dst != entry:
                parent[dst] = e["site"] if src == entry else parent.get(src, e["site"])
        out: list[tuple[str, str | None]] = [(entry, None)]
        a = self.A[entry]
        for f in info["closure"][1:]:
            site = parent.get(f)
            # dominance is checked at the include statement of the entry page
            stmt = next((x for x in a.ancestors(site)), None) if site and site.startswith(entry + "#") else None
            while stmt and a.kind(stmt) != "Stmt_Expression" and a.parent(stmt):
                stmt = a.parent(stmt)
            out.append((f, stmt))
        return out

    def _closure_from_sites(self, entry: str) -> list[tuple[str, str | None]]:
        """Include closure of a file that is not a never-included page (self resolution)."""
        out: list[tuple[str, str | None]] = [(entry, None)]
        a = self.A[entry]
        seen = {entry}
        queue = [(entry, None)]
        while queue:
            cur, via = queue.pop(0)
            for s in self.includes["sites"]:
                if s["fileId"] != cur or s["self"]["status"] != "resolved":
                    continue
                tgt = s["self"].get("targetFileId")
                if tgt and tgt not in seen:
                    seen.add(tgt)
                    stmt = via
                    if cur == entry:
                        stmt = s["site"]
                        while stmt and a.kind(stmt) != "Stmt_Expression" and a.parent(stmt):
                            stmt = a.parent(stmt)
                    out.append((tgt, stmt))
                    queue.append((tgt, stmt))
        return out

    def recover(self) -> dict[str, Any]:
        eps: dict[str, dict[str, Any]] = {}
        pages: dict[str, list[str]] = {}
        query_ep: dict[str, str] = {}
        page_reports = {p["fileId"]: p for p in self.auth_result["pages"]}
        for fid in self.entry_pages():
            a = self.A[fid]
            uid = a.root_id
            unit = self.auth.units[(fid, uid)]
            regions = []
            pts = self.auth.guard_points((fid, uid))
            groups: dict[str, list[tuple]] = {}   # region cond id (or "render") -> queries
            for code_fid, via in self.page_files(fid):
                ca = self.A[code_fid]
                cuid = ca.root_id
                cunit = self.auth.units[(code_fid, cuid)]
                file_regions = self.handler_regions(code_fid)
                regions += file_regions
                for item in sorted(cunit.where, key=lambda x: (ca.line(x) or 0, x)):
                    if not cunit.blocks[cunit.where[item][0]]["reachable"]:
                        continue
                    for q in self.queries_reached(code_fid, cuid, item):
                        reg = None
                        # the *outermost* request-triggered branch is the handler; inner
                        # branches that also read the request are validation inside it
                        for r in file_regions:
                            if self.inside(ca, item, r["body"]) and (reg is None or self.inside(ca, reg["stmt"], r["body"])):
                                reg = r
                        # in the entry page itself, dominance is checked at the statement;
                        # in an included file, at the include statement that brought it in
                        dom_item = item if code_fid == fid else (via or item)
                        groups.setdefault(reg["cond"] if reg else "render", []).append((dom_item, q, reg, code_fid, item))
            page_eps: list[str] = []
            render_selects = [g for g in groups.get("render", []) if self.shape_of(*g[1][:3])["kind"] == "select"]
            read_handlers = [k for k, ms in groups.items() if k != "render" and self.reads_records([m[1] for m in ms])]
            side_effects = []
            for gkey, members in groups.items():
                if gkey == "render":
                    for m in members:
                        dom_item, q = m[0], m[1]
                        sh = self.shape_of(q[0], q[1], q[2])
                        if sh["kind"] in ("transaction", "session-setting"):
                            continue
                        if sh["kind"] != "select" and q[3]:
                            # a write inside a helper the page calls while rendering
                            # (e.g. clearing expired sessions): part of the GET, not an endpoint
                            side_effects.append({"query": q[2], "kind": sh["kind"], "via": q[3],
                                                 "table": (sh.get("tables") or [None])[0]})
                            continue
                        rec_idx = ("only" if len(render_selects) == 1 else render_selects.index(m) + 1) \
                            if m in render_selects else "none"
                        ep = self.endpoint_for(fid, dom_item, [q], None, pts, unit, rec_idx, page_reports.get(fid), m)
                        self.merge(eps, ep)
                        page_eps.append(ep["id"])
                        query_ep[q[2]] = ep["id"]
                else:
                    reg = members[0][2]
                    qs = [m[1] for m in members]
                    ep = self.endpoint_for(fid, members[0][0], qs, reg, pts, unit,
                                           self.handler_records(qs, render_selects, read_handlers),
                                           page_reports.get(fid), members[0])
                    self.merge(eps, ep)
                    page_eps.append(ep["id"])
                    for q in qs:
                        query_ep.setdefault(q[2], ep["id"])
            # session-only handlers (logout) still need an endpoint
            for r in regions:
                if r["cond"] in groups:
                    continue
                code_fid = r["cond"].split("#")[0]
                if self.region_logs_out(code_fid, r):
                    ep = self.auth_endpoint("logout", fid, r, [])
                    self.merge(eps, ep)
                    page_eps.append(ep["id"])
            pages[a.path] = sorted(set(page_eps))
            if side_effects:
                for eid in pages[a.path]:
                    if eps[eid]["method"] == "GET":
                        eps[eid].setdefault("sideEffects", [])
                        for s in side_effects:
                            if s not in eps[eid]["sideEffects"]:
                                eps[eid]["sideEffects"].append(s)
                        eps[eid]["flags"] = _dedupe_flags(eps[eid]["flags"] + [{
                            "flag": "write-during-render",
                            "reason": "rendering the legacy page also writes data through a helper"}])
        self.annotate_logins(eps)
        return {"endpoints": [eps[k] for k in sorted(eps)], "pages": dict(sorted(pages.items())),
                "queries": dict(sorted(query_ep.items()))}

    # -- login details for test clients (C4): which user type each endpoint needs -------------
    def login_facts(self, src: dict) -> dict[str, Any]:
        """For one legacy login form: the user type (the table its password is checked against),
        the session keys it sets, and where it redirects."""
        code_path = src["code"]["path"]
        fid = next(f for f, a in self.A.items() if a.path == code_path)
        a = self.A[fid]
        # the outermost request-triggered branch around the login query is the login handler
        regions = [r for r in self.handler_regions(fid) if r["stmt"] == src["stmt"] or self.inside(a, src["stmt"], r["body"])]
        region = next((r for r in regions if not any(o is not r and self.inside(a, r["stmt"], o["body"]) for o in regions)), None)
        body = region["body"] if region else []
        tables = sorted({p["column"].split(".")[0] for p in src["parameters"]
                         if p.get("column") and SENSITIVE.search(p["column"].split(".")[-1])})
        sets = sorted(self.identity_written(fid, a.root_id, body))
        redirects = []
        for st in body:
            for x in a.walk(st):
                if a.kind(x) == "Expr_FuncCall" and a.call_name(x) == "header":
                    lits = [a.attr(y, "value") for y in a.walk(x) if a.kind(y) in ("Scalar_String", "InterpolatedStringPart")]
                    loc = next((v.split(":", 1)[1].strip() for v in lits if isinstance(v, str) and v.lower().startswith("location")), None)
                    if loc:
                        redirects.append({"to": loc, "line": a.line(x)})
        return {"userType": tables[0] if len(tables) == 1 else (tables or None), "sets": sets,
                "fields": sorted(p["legacy_name"] for p in src["parameters"] if p.get("legacy_name")),
                "redirects": redirects}

    CALLS = ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall")

    def identity_written(self, fid: str, uid: str, stmts: list[str], depth: int = 0) -> set[str]:
        """Session keys ($_SESSION['k'] = ...) and cookies (setcookie('k', ...)) written by these
        statements, including inside the functions they call (Users::login_user, ...)."""
        a = self.A[fid]
        out: set[str] = set()
        for st in stmts:
            for x in a.walk(st):
                src = self.auth.sources.get(x)
                if src and src.get("write") and src["superglobal"] == "SESSION" and src["dims"]:
                    out.add(str(src["dims"][0]))
                if a.kind(x) == "Expr_FuncCall" and a.call_name(x) == "setcookie":
                    lits = [a.attr(y, "value") for y in a.walk(x) if a.kind(y) == "Scalar_String"]
                    if lits:
                        out.add(f"cookie:{lits[0]}")
                if a.kind(x) in self.CALLS and depth < 3:
                    c = self.auth.callee_of(fid, uid, x)
                    if c:
                        out |= self.identity_written(c[0], c[1], [c[1]], depth + 1)
        return out

    def guard_keys(self, gid: str) -> set[str]:
        """Session keys a guard checks: a direct check, or a guard helper it calls (require_login)."""
        checks = {c["id"]: c for c in self.auth_result["checks"]}
        if gid in checks:
            return set(checks[gid].get("sessionKeys", []))
        helpers = {h["unit"]: h for h in self.auth_result.get("guardHelpers", [])}
        fid = gid.split("#")[0]
        a = self.A.get(fid)
        if a is None or gid not in a.nodes:
            return set()
        uid = next((x for x in a.ancestors(gid) if a.kind(x) in SCOPE_KINDS), a.root_id)
        keys: set[str] = set()
        for x in a.walk(gid):
            if a.kind(x) in self.CALLS:
                c = self.auth.callee_of(fid, uid, x)
                if c and c[1] in helpers:
                    keys |= set(helpers[c[1]].get("sessionKeys", []))
        return keys

    def annotate_logins(self, eps: dict[str, dict]) -> None:
        types: dict[str, set[str]] = {}
        for ep in eps.values():
            if ep["kind"] != "login":
                continue
            for src in ep["sources"]:
                src["login"] = self.login_facts(src)
                t = src["login"]["userType"]
                if isinstance(t, str):
                    types.setdefault(t, set()).update(src["login"]["sets"])
        for ep in eps.values():
            if ep["kind"] == "login" or not ep["auth"].get("required"):
                continue
            need = {b["sessionKey"] for b in ep.get("sessionBindings", [])}
            need |= {k for r in ep["auth"].get("roles", []) for k in r.get("sessionKeys", [])}
            need |= {k for g in ep["auth"].get("guards", []) for k in self.guard_keys(g)}
            fit = sorted(t for t, keys in types.items() if need <= keys) if need else sorted(types)
            ep["auth"]["userTypes"] = fit
            ep["auth"]["sessionKeys"] = sorted(need)
            if need and not fit and types:
                ep["flags"] = _dedupe_flags(ep["flags"] + [{"flag": "user-type-unknown", "reason":
                    f"no legacy login sets all of {', '.join(sorted(need))}"}])

    def reads_records(self, qs: list) -> bool:
        """A handler that only SELECTs and whose rows the page then uses (a search form)."""
        shapes = [self.shape_of(q[0], q[1], q[2]) for q in qs]
        return (not any(sh["kind"] in ("insert", "replace", "update", "delete") for sh in shapes)
                and any(sh["kind"] == "select" and self.used_columns.get(q[2]) for q, sh in zip(qs, shapes)))

    def handler_records(self, qs: list, render_selects: list, read_handlers: list) -> str | int:
        """Where a handler's rows appear on the page: "only" when it is the page's single result
        table, "unknown" when the page has other tables too (C4 then must not assume one), "none"
        when the handler only writes."""
        if not self.reads_records(qs):
            return "none"
        return "only" if not render_selects and len(read_handlers) == 1 else "unknown"

    def region_logs_out(self, fid: str, r: dict) -> bool:
        a = self.A[fid]
        return any(a.kind(x) == "Expr_FuncCall" and a.call_name(x) in ("session_destroy", "session_unset")
                   for s in r["body"] for x in a.walk(s))

    def writes_session_in(self, fid: str, uid: str, stmts: list[str]) -> bool:
        a = self.A[fid]
        d = self.p.units[(fid, uid)]
        for s in d.sources:
            if s.get("write") and s["superglobal"] == "SESSION" and self.inside(a, s["node"], stmts):
                return True
        for s in stmts:
            for x in a.walk(s):
                if a.kind(x) in ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall"):
                    c = self.auth.callee_of(fid, uid, x)
                    if c and c in self.auth.session_writers:
                        return True
        return False

    def auth_endpoint(self, kind: str, fid: str, region: dict, params: list[dict], member=None) -> dict[str, Any]:
        path = "/api/auth/login" if kind == "login" else "/api/auth/logout"
        member = member or (None, None, region, region["cond"].split("#")[0], region["stmt"])
        return {"id": f"POST {path}", "method": "POST", "path": path, "kind": kind, "resource": "auth",
                "params": _dedupe_params(params), "sessionBindings": [],
                "response": {"kind": "token" if kind == "login" else "status", "recordsAt": None, "fields": {}},
                "auth": {"required": kind == "logout", "kinds": ["authentication"] if kind == "logout" else []},
                "sources": [self.source_rec(fid, region["stmt"], region, params, "none", [], member)], "flags": []}

    def endpoint_for(self, fid, item, qs, region, pts, unit, rec_idx, page_report, member) -> dict[str, Any]:
        code_fid, code_item = member[3], member[4]
        a = self.A[code_fid]
        uid = a.root_id
        shapes = [(q, self.shape_of(q[0], q[1], q[2])) for q in qs]
        # the handler's operation is its first write in execution order (register = INSERT,
        # even if it then updates last_login); with no write, its first SELECT
        writes = [x for x in shapes if x[1]["kind"] in ("insert", "replace", "update", "delete")]
        main_q, main_sh = writes[0] if writes else next((x for x in shapes if x[1]["kind"] == "select"), shapes[0])
        kind = main_sh["kind"]
        also = [{"query": q[2], "kind": sh["kind"], "table": (sh.get("tables") or [None])[0]}
                for q, sh in writes[1:]]
        table = (main_sh.get("tables") or [None])[0]
        table_real = self.schema_lower.get((table or "").lower(), table)
        flags: list[dict[str, str]] = []
        params: list[dict[str, Any]] = []
        session_filters = []
        for q, sh in shapes:
            tbl = self.schema_lower.get(((sh.get("tables") or [None])[0] or "").lower(), (sh.get("tables") or [None])[0])
            for p in sh.get("params", []):
                if p["position"] == "structural":
                    flags.append({"flag": "dynamic-sql-structure", "query": q[2]})
            for role, items in (("where", sh.get("where", [])), ("write", sh.get("writes", []))):
                for w in items:
                    if w.get("param") is None or w["param"] >= len(sh["params"]):
                        continue
                    origins = sorted(self.bind(sh["params"][w["param"]]["origins"], q[4]))
                    for o in origins:
                        if o.startswith("input:") and o.split(":")[1] in INPUT_SG:
                            _, sg, key = o.split(":", 2)
                            params.append({"name": w["column"], "role": role, "column": f"{tbl}.{w['column']}",
                                           "legacy_name": key, "legacy_in": INPUT_SG[sg], "op": w.get("op")})
                        elif o.startswith("session:"):
                            session_filters.append({"column": f"{tbl}.{w['column']}", "sessionKey": o[8:], "role": role})
        # login: a handler that checks a password-like column against request input and
        # writes the session (directly or in a called function)
        checks_password = any(SENSITIVE.search(p["name"]) and p["role"] == "where" for p in params)
        if region is not None and checks_password and self.writes_session_in(code_fid, uid, region["body"]):
            for p in params:
                p["in"] = "body"
            ep = self.auth_endpoint("login", fid, region, params, member)
            ep["sources"][0]["queries"] = [q[2] for q in qs]
            return ep
        legacy_method = region["trigger"]["method"] if region else "GET"
        key_params = [p for p in params if p["role"] == "where" and p.get("op") == "=" and KEY_COLUMN.match(p["name"])]
        res = _resource(table_real)
        if kind == "select":
            if session_filters and not params:
                # the signed-in user's rows; a filter on a non-key column (a doctor's name)
                # is a different view of the table than one on the user's id
                cols = sorted({f["column"].split(".")[-1] for f in session_filters if f["role"] == "where"})
                suffix = "" if cols and all(KEY_COLUMN.match(c) for c in cols) else "/by-" + "-".join(cols).lower()
                method, path = "GET", f"/api/me/{res}{suffix}"
            elif key_params and region is None or (key_params and legacy_method == "GET" and len(params) == len(key_params)):
                method, path = "GET", f"/api/{res}/{{{key_params[0]['name']}}}"
            else:
                method, path = "GET", f"/api/{res}"
        elif kind in ("insert", "replace"):
            method, path = "POST", f"/api/{res}"
        elif kind == "update":
            method, path = ("PUT", f"/api/{res}/{{{key_params[0]['name']}}}") if key_params else ("PUT", f"/api/{res}")
            if not key_params:
                flags.append({"flag": "update-without-key", "reason": "UPDATE not filtered by a key column from the request"})
        elif kind == "delete":
            method, path = ("DELETE", f"/api/{res}/{{{key_params[0]['name']}}}") if key_params else ("DELETE", f"/api/{res}")
            if not key_params:
                flags.append({"flag": "delete-without-key", "reason": "DELETE not filtered by a key column from the request"})
        else:
            method, path = "POST", f"/api/{res}/{kind}"
            flags.append({"flag": "unknown-sql-kind", "reason": main_sh.get("reason", kind)})
        if self.schema and table and table_real not in self.schema:
            flags.append({"flag": "table-not-in-schema", "table": table,
                          "reason": "the database dump has no such table: probably dead template code; a test "
                                    "client should skip this endpoint"})
        if main_sh.get("confidence") not in ("resolved", None):
            flags.append({"flag": f"sql-{main_sh.get('confidence')}", "reason": main_sh.get("reason", "")})
        if legacy_method == "GET" and kind in ("insert", "update", "delete", "replace"):
            flags.append({"flag": "mutating-get", "reason": "the legacy page changes data on a GET request; "
                          "mapped by SQL kind, decision pending (supervisor)"})
        # parameter placement on the new API
        path_names = re.findall(r"{(\w+)}", path)
        for p in params:
            if p["name"] in path_names:
                p["in"] = "path"
            elif method in ("GET", "DELETE"):
                p["in"] = "query"
            else:
                p["in"] = "body"
        # response shape
        response = {"kind": "status", "recordsAt": None, "fields": {}}
        if kind == "select":
            used = set()
            for q, sh in shapes:
                if sh["kind"] == "select":
                    used |= self.used_columns.get(q[2], set())
            selected = main_sh.get("columns") or ["*"]
            cols = sorted(used) if "*" in selected else sorted(c for c in selected if c in used or not used)
            if not used:
                flags.append({"flag": "no-column-reads-found", "reason": "no read of this query's columns was traced; "
                              "response lists the selected columns"})
                cols = [c for c in selected if c != "*"] or []
            fields, field_cols = {}, {}
            q_tables = [self.schema_lower.get(t.lower(), t) for t in (main_sh.get("tables") or [])]
            qualified = self.qualified_select(main_sh.get("sql") or "")
            for c in cols:
                owner = qualified.get(c) or self.column_owner(c, q_tables, table_real)
                t = self.schema.get(owner or "", {}).get(c)
                fields[c] = json_type(t["sqlType"]) if t else {"type": "string"}
                field_cols[c] = f"{owner}.{c}" if owner else None
                if owner is None:
                    flags.append({"flag": "column-ambiguous", "column": c,
                                  "reason": f"more than one joined table has this column: {', '.join(q_tables)}"})
                elif not t:
                    flags.append({"flag": "type-unknown", "column": f"{owner}.{c}"})
                if SENSITIVE.search(c):
                    flags.append({"flag": "sensitive-field", "column": f"{owner or table_real}.{c}",
                                  "reason": "the legacy page outputs this column; review before exposing it in the API"})
            is_detail = "{" in path
            response = {"kind": "object" if is_detail else "list", "recordsAt": None if is_detail else res,
                        "fields": fields, "columns": field_cols, "selectedStar": "*" in selected}
        auth = self.auth_requirement(fid, item, pts, unit, page_report, session_filters)
        if auth.get("needsDecision"):
            flags.append({"flag": "no-guard-in-legacy", "reason": "the legacy page has no guard before this data access; "
                          "locked by default (login required): mark it public if anyone should reach it"})
        ep = {"id": f"{method} {path}", "method": method, "path": path, "kind": kind, "resource": res,
              "table": table_real, "params": _dedupe_params(params), "sessionBindings": session_filters,
              "response": response, "auth": auth,
              "sources": [self.source_rec(fid, item, region, params, rec_idx, [q[2] for q in qs], member)],
              "flags": flags}
        if also:
            ep["alsoWrites"] = also
        return ep

    def qualified_select(self, sql: str) -> dict[str, str]:
        """Output column -> table, for select items written as alias.column
        (`select p.pid, a.docFees from prestb p inner join appointmenttb a ...`)."""
        m = re.match(r"\s*select\s+(.*?)\s+from\s+(.*)$", sql, re.I | re.S)
        if not m:
            return {}
        alias = {}
        for t, al in re.findall(r"(?:from|join|,)\s*`?(\w+)`?(?:\s+(?:as\s+)?(?!on\b|inner\b|left\b|right\b|join\b|where\b)(\w+))?",
                                "from " + m.group(2), re.I):
            real = self.schema_lower.get(t.lower(), t)
            alias[t.lower()] = real
            if al:
                alias[al.lower()] = real
        out = {}
        for item in _split_select(m.group(1)):
            q = re.fullmatch(r"`?(\w+)`?\.`?(\w+)`?(?:\s+(?:as\s+)?`?(\w+)`?)?", item.strip(), re.I)
            if q and q.group(1).lower() in alias:
                out[q.group(3) or q.group(2)] = alias[q.group(1).lower()]
        return out

    def column_owner(self, col: str, tables: list[str], main: str | None) -> str | None:
        """Which table a selected column comes from. One table: that one. A join: the only joined
        table whose schema has the column; None when several do (ambiguous), the main table when
        the schema is unknown."""
        if len(tables) <= 1 or not self.schema:
            return main
        owners = [t for t in tables if col in self.schema.get(t, {})]
        if len(owners) == 1:
            return owners[0]
        return None if owners else main

    def source_rec(self, fid, item, region, params, rec_idx, queries, member) -> dict[str, Any]:
        """One legacy request behind an endpoint, with *its own* parameters (C4 needs the
        legacy field names per page). `fixed` keeps only trigger fields that are not
        parameters, e.g. the submit button."""
        a = self.A[fid]
        code_fid, code_item = member[3], member[4]
        legacy_names = {p["legacy_name"] for p in params}
        if region:
            # a trigger field the handler body also reads is an input, not a fixed value
            ca = self.A[code_fid]
            body_keys = {str(s["dims"][0]) for s in self.auth.sources.values()
                         if s["node"].startswith(code_fid + "#") and s["dims"] and not s.get("write")
                         and s["superglobal"] in ("GET", "POST", "REQUEST") and self.inside(ca, s["node"], region["body"])}
            legacy_names |= body_keys
        fixed = {k: v for k, v in (region["trigger"]["fixed"] if region else {}).items() if k not in legacy_names}
        repeats = self.repeated_fields(code_fid, region) if region else []
        same_as = {d["field"]: d["same_as"] for d in repeats}
        rec = {"source_id": f"{a.path}:{code_item}", "page": a.path, "constraints": repeats,
               "legacy": {"method": region["trigger"]["method"] if region else "GET", "path": "/" + a.path,
                          "fixed": fixed, "records": rec_idx,
                          "trigger": region["trigger"]["keys"] if region else []},
               "parameters": _dedupe_params([{**p, "same_as": same_as[p["legacy_name"]]}
                                             if p.get("legacy_name") in same_as else dict(p) for p in params]),
               "queries": queries or [], "stmt": code_item,
               "code": {"path": self.A[code_fid].path, "line": self.A[code_fid].line(code_item)}}
        return rec

    def auth_requirement(self, fid, item, pts, unit, page_report, session_filters) -> dict[str, Any]:
        guards = [p for p in pts if unit.point_dominates(p["point"], item)]
        roles = []
        a = self.A[fid]
        for c in self.auth_result["checks"]:
            if c["fileId"] == fid and c["kind"].startswith("authorization") and c["effect"] in ("gates-data", "terminating-guard"):
                if self.inside(a, item, [c["id"]]) or any(g["id"] == c["id"] for g in guards):
                    roles.append({"check": c["id"], "kind": c["kind"], "condExpr": c.get("condExpr"),
                                  "sessionKeys": c["sessionKeys"]})
        if guards or roles or session_filters:
            kinds = sorted({g["kind"] for g in guards} | {r["kind"] for r in roles} |
                           ({"authentication"} if session_filters and not guards else set()))
            out = {"required": True, "kinds": kinds, "guards": [g["id"] for g in guards], "roles": roles}
            if session_filters and not guards:
                out["note"] = "the query is filtered by a session value but no guard was found: the token must identify the user"
            return out
        # secure by default (OWASP: deny by default): the legacy page had no check, so the new
        # endpoint requires a login until a person marks it public. Still flagged for that decision.
        return {"required": True, "securedByDefault": True, "needsDecision": True,
                "kinds": ["authentication"], "guards": [], "roles": []}

    @staticmethod
    def merge(eps: dict[str, dict], ep: dict) -> None:
        if ep["id"] not in eps:
            eps[ep["id"]] = ep
            return
        cur = eps[ep["id"]]
        known = {s["source_id"] for s in cur["sources"]}
        cur["sources"] += [s for s in ep["sources"] if s["source_id"] not in known]
        cur["params"] = _dedupe_params(cur["params"] + ep["params"])
        for b in ep.get("sessionBindings", []):
            if b not in cur.setdefault("sessionBindings", []):
                cur["sessionBindings"].append(b)
        for k, v in ep["response"].get("fields", {}).items():
            cur["response"].setdefault("fields", {}).setdefault(k, v)
        for k, v in ep["response"].get("columns", {}).items():
            cur["response"].setdefault("columns", {}).setdefault(k, v)
        cur["flags"] = _dedupe_flags(cur["flags"] + ep["flags"])
        if bool(ep["auth"].get("securedByDefault")) != bool(cur["auth"].get("securedByDefault")):
            cur["flags"] = _dedupe_flags(cur["flags"] + [{"flag": "inconsistent-guard",
                                                           "reason": "some legacy pages guard this data, others do not"}])
            if not ep["auth"].get("securedByDefault"):  # keep the requirement the legacy code proves
                cur["auth"] = ep["auth"]


def _split_select(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur] if cur.strip() else out


def _repeats(rep: str, orig: str) -> bool:
    """Is `rep` the confirmation of `orig`? cpassword / confirm_password / password2 / re_password ..."""
    r, o = rep.lower(), orig.lower()
    return r != o and o in r and re.fullmatch(rf"(c|conf|confirm|re|repeat|retype)?_?{re.escape(o)}_?(2|again|confirm|"
                                              rf"confirmation|repeat)?", r) is not None


def _norm(path: str) -> str:
    parts: list[str] = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
        else:
            parts.append(p)
    return "/".join(parts)


def _rejoin(o: str) -> str:
    """column:<fileId#node>:<name> -> query node id."""
    return o.split(":")[1]


def _dedupe_params(params: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in params:
        key = (p["name"], p.get("in"), p.get("legacy_name"))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return sorted(out, key=lambda p: (p.get("in") or "", p["name"], p.get("legacy_name") or ""))


def _dedupe_flags(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flags:
        key = json.dumps(f, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def endpoint_map(rec: dict[str, Any]) -> dict[str, Any]:
    """C4 view (same structure as c4-stand-in/1, plus the fields C4 asked for on 2026-10-21) + C2 view.

    Per source: legacy.records is "only", a table number, "unknown" or "none"; constraints lists
    fields that must repeat another (same_as); a login form also carries its user type, fields
    and redirects. Per endpoint: auth (required, userTypes, sessionKeys) and response_fields
    (field -> table.column). Top level: how to log in and where the token is."""
    out = []
    login: dict[str, Any] = {}
    for ep in rec["endpoints"]:
        sources = []
        for s in ep["sources"]:
            src = {"source_id": s["source_id"], "code": s.get("code"),
                   "legacy": {k: s["legacy"][k] for k in ("method", "path", "fixed", "records")},
                   "parameters": [{"name": p["name"], "in": p.get("in", "query"), "legacy_name": p["legacy_name"],
                                   "legacy_in": p["legacy_in"], "column": p.get("column"),
                                   **({"same_as": p["same_as"]} if p.get("same_as") else {})}
                                  for p in s["parameters"] if p.get("legacy_name")],
                   "constraints": s.get("constraints", [])}
            if s.get("login"):
                src["login"] = s["login"]
                t = s["login"]["userType"]
                if isinstance(t, str):
                    login.setdefault(t, {"pages": [], "fields": s["login"]["fields"], "sets": s["login"]["sets"],
                                         "redirects": s["login"]["redirects"]})["pages"].append(s["legacy"]["path"])
            sources.append(src)
        auth = {"required": bool(ep["auth"].get("required")), "securedByDefault": bool(ep["auth"].get("securedByDefault")),
                "userTypes": ep["auth"].get("userTypes", []),
                "sessionKeys": ep["auth"].get("sessionKeys", [])}
        out.append({"endpoint_id": ep["id"], "method": ep["method"], "path": ep["path"],
                    "records_at": ep["response"].get("recordsAt"),
                    "response_fields": ep["response"].get("columns", {}),
                    "auth": auth, "flags": sorted({f["flag"] for f in ep["flags"]}),
                    "sources": sources, "status": "200", "mediaType": "application/json"})
    return {"format": "c1-endpoint-map/1", "endpoints": out, "pages": rec["pages"], "queries": rec["queries"],
            "login": {"endpoint": "POST /api/auth/login", "request": "JSON body with the login parameters",
                      "tokenAt": "access_token", "tokenType": "bearer",
                      "sendAs": "Authorization: Bearer <access_token>",
                      "userTypes": {k: {**v, "pages": sorted(set(v["pages"]))} for k, v in sorted(login.items())}}}


def run(out_dir: Path, src_root: Path | None, schema_files: list[Path]) -> dict[str, Any]:
    from .concerns import load_stage
    manifest, asts, cfgs, dfgs = load_stage(out_dir)
    includes = json.loads((out_dir / "index" / "includes.json").read_text(encoding="utf-8"))
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    sources = {}
    if src_root:
        for f in manifest["files"]:
            p = src_root / f["path"]
            if p.exists():
                sources[f["fileId"]] = p.read_bytes()
        if not schema_files:
            schema_files = sorted(p for p in src_root.rglob("*.sql") if ".git" not in p.parts)
    schema: dict[str, dict] = {}
    for sf in schema_files:
        schema.update(read_schema_dump(sf.read_text(encoding="utf-8", errors="replace")))
    rec = recover(asts, cfgs, includes, dfgs, labels, schema, sources)
    rec["generatedFor"] = manifest["contentHash"]
    rec["schemaSource"] = [str(p.relative_to(src_root)) if src_root and p.is_relative_to(src_root) else p.name
                           for p in schema_files]
    (out_dir / "endpoints.json").write_bytes(canonical_json(rec, pretty=True))
    em = endpoint_map(rec)
    em["generatedFor"] = manifest["contentHash"]
    (out_dir / "endpoint-map.json").write_bytes(canonical_json(em, pretty=True))
    return rec


def recover(asts, cfgs, includes, dfgs, labels, schema, sources=None) -> dict[str, Any]:
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        er = EndpointRecovery(asts, cfgs, includes, dfgs, labels, schema, sources)
        rec = er.recover()
        used = {q for ep in rec["endpoints"] for s in ep["sources"] for q in s["queries"]}
        rec["sql"] = {q: sh["sql"] for q, sh in sorted(er.shapes.items()) if q in used and "sql" in sh}
        rec["schema"] = schema
    finally:
        sys.setrecursionlimit(old)
    flags: dict[str, int] = {}
    for ep in rec["endpoints"]:
        for f in ep["flags"]:
            flags[f["flag"]] = flags.get(f["flag"], 0) + 1
    methods: dict[str, int] = {}
    for ep in rec["endpoints"]:
        methods[ep["method"]] = methods.get(ep["method"], 0) + 1
    return {"schemaVersion": "1.0",
            "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "endpoints"},
            **rec, "totals": {"endpoints": len(rec["endpoints"]), **{f"method:{k}": v for k, v in sorted(methods.items())},
                              **{f"flag:{k}": v for k, v in sorted(flags.items())}}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.endpoints", description="Recover endpoints and response shapes")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--src", type=Path, help="legacy app root (source text, schema dump discovery)")
    ap.add_argument("--schema", type=Path, action="append", default=[], help="SQL dump with CREATE TABLE (repeatable)")
    args = ap.parse_args(argv)
    if not (args.out_dir / "labels.json").exists():
        print("error: run `python -m c1.concerns` first", file=sys.stderr)
        return 1
    rec = run(args.out_dir, args.src, args.schema)
    print(", ".join(f"{k} {v}" for k, v in rec["totals"].items()) + f" -> {args.out_dir}/endpoints.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
