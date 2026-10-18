"""Control-flow graphs (CFG) from schema-1.0 ASTs.

    python -m c1.cfg analysis-out          # reads manifest.json + ast/, writes cfg/

One graph per *unit*: the file body ("{main}"), every function, method, closure and
arrow function. Design sources (see docs/DESIGN_CFG.md for the full list):

* Xie & Aiken, USENIX Security 2006, Sec. 3: one CFG per function; basic blocks are
  maximal single-entry single-exit statement runs; an `exit` makes an exit block;
  conditional edges carry the branch predicate; a call to a function that always
  exits cuts the block (statements after it are unreachable); `include` is a point
  in the caller's scope (we mark it here, the include resolver splices later).
* PHP manual: `and`/`or`/`&&`/`||` short-circuit (logical operators page, Example 1),
  `exit` terminates the script and `finally` blocks are never executed after it
  (exit page), `break N` / `continue N` count enclosing structures and `switch`
  counts as a loop for `continue` (break / continue pages).
* RoleCast (Son et al., OOPSLA 2011): a failed check that quickly ends in die/exit
  is the security-relevant pattern, so `X or die()` is a real branch, not one statement.

Sound-or-abstain: anything we cannot model exactly is kept as an over-approximation
(extra edges, never missing ones) and written to `flags` with a reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import CLASS_LIKE_KINDS, SCOPE_KINDS, AstDoc
from .dominance import immediate_dominators
from .extract import canonical_json, sha256_hex

CFG_SCHEMA_VERSION = "1.0"

OR_OPS = frozenset({"Expr_BinaryOp_LogicalOr", "Expr_BinaryOp_BooleanOr"})
AND_OPS = frozenset({"Expr_BinaryOp_LogicalAnd", "Expr_BinaryOp_BooleanAnd"})
LOOP_KINDS = frozenset({"Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach"})

# Edge labels (documented in docs/DESIGN_CFG.md)
#   next      plain fall-through
#   true/false   branch on terminator.node (the predicate, Xie & Aiken)
#   iterate/done foreach has another element / is finished
#   notnull/null  `??` left side
#   back      loop back-edge
#   break / continue   jump out of / to the top of a loop or switch
#   return    `return` statement     end    falling off the end of the unit
#   exit      script terminates (exit/die, or a call to an always-exiting function)
#   throw     explicit throw         catch  possible exception inside try -> catch
#   goto      goto statement         error  PHP would raise a fatal error here
#   halt      __halt_compiler()


class Facts:
    """Per-file, per-round cache: which subtrees may terminate / contain an include.

    Nodes are stored in pre-order, so walking the list backwards sees children before
    parents and one pass is enough. Nested functions/classes are separate units and
    never make their parent terminate.
    """

    def __init__(self, ast: AstDoc, always_exit: frozenset[str]):
        self.always_exit = always_exit
        self.term: set[str] = set()
        self.incl: set[str] = set()
        for n in reversed(ast.doc["nodes"]):
            nid, k = n["id"], n["kind"]
            if k in ("Expr_Exit", "Expr_Throw") or (k == "Expr_FuncCall" and ast.call_name(nid) in always_exit):
                self.term.add(nid)
            if k == "Expr_Include":
                self.incl.add(nid)
            if k in SCOPE_KINDS or k in CLASS_LIKE_KINDS:
                continue
            for c in n["children"]:
                ck = ast.kind(c)
                if ck in SCOPE_KINDS or ck in CLASS_LIKE_KINDS:
                    continue
                if c in self.term:
                    self.term.add(nid)
                if c in self.incl:
                    self.incl.add(nid)


class UnitBuilder:
    """Builds the CFG of one unit. Not reused across units."""

    def __init__(self, ast: AstDoc, unit_node: str, facts: Facts):
        self.ast = ast
        self.unit_node = unit_node
        self.facts = facts
        self.always_exit = facts.always_exit
        self.blocks: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.flags: list[dict[str, str]] = []
        self.include_sites: list[dict[str, str]] = []
        self.exit_sites: list[dict[str, str]] = []
        self.loops: list[dict[str, str]] = []
        self.trys: list[dict[str, Any]] = []
        self.labels: dict[str, str] = {}
        self.gotos: list[tuple[str, str, str]] = []
        self.entry = self._new_block(role="entry")
        self.exit = self._new_block(role="exit")
        self.cur: str | None = self.entry

    # -- graph primitives -----------------------------------------------------
    def _new_block(self, role: str | None = None) -> str:
        bid = f"B{len(self.blocks)}"
        self.blocks.append({"id": bid, "items": [], "terminator": None, "role": role})
        for t in self.trys:
            t["blocks"].append(bid)
        return bid

    def new_block(self) -> str:
        return self._new_block()

    def edge(self, a: str, b: str, label: str, node: str | None = None) -> None:
        e: dict[str, Any] = {"from": a, "to": b, "label": label}
        if node is not None:
            e["node"] = node
        self.edges.append(e)

    def here(self) -> str:
        """Current block; code after a jump starts a new block with no predecessors (unreachable)."""
        if self.cur is None:
            self.cur = self.new_block()
        return self.cur

    def append(self, nid: str) -> None:
        self.blocks[int(self.here()[1:])]["items"].append(nid)

    def jump(self, target: str, label: str, node: str | None = None) -> None:
        if self.cur is not None:
            self.edge(self.cur, target, label, node)
        self.cur = None

    def fall_to(self, target: str, label: str = "next") -> None:
        if self.cur is not None:
            self.edge(self.cur, target, label)
        self.cur = target

    def set_terminator(self, kind: str, node: str, stmt: str | None = None) -> str:
        bid = self.here()
        t: dict[str, Any] = {"kind": kind, "node": node}
        if stmt is not None:
            t["stmt"] = stmt
        self.blocks[int(bid[1:])]["terminator"] = t
        return bid

    def flag(self, nid: str, reason: str) -> None:
        self.flags.append({"node": nid, "reason": reason})

    # -- expression facts -----------------------------------------------------
    def may_terminate(self, nid: str | None) -> bool:
        """Subtree contains exit/die, throw, or a call to an always-exiting function."""
        return nid is not None and nid in self.facts.term

    def includes_in(self, nid: str) -> list[str]:
        if nid not in self.facts.incl:
            return []
        return [x for x in self.ast.walk(nid) if self.ast.kind(x) == "Expr_Include"]

    def splits(self, nid: str | None) -> bool:
        """Expression shapes flow_expr turns into explicit branches / terminators."""
        if nid is None or not self.may_terminate(nid):
            return False
        k = self.ast.kind(nid)
        if k in ("Expr_Exit", "Expr_Throw"):
            return True
        if k == "Expr_FuncCall":
            return self.ast.call_name(nid) in self.always_exit
        if k in OR_OPS or k in AND_OPS or k == "Expr_BinaryOp_Coalesce":
            return self.may_terminate(self.ast.field(nid, "right"))
        if k == "Expr_Ternary":
            return self.may_terminate(self.ast.field(nid, "if")) or self.may_terminate(self.ast.field(nid, "else"))
        if k == "Expr_Assign":
            return self.splits(self.ast.field(nid, "expr"))
        return False

    # -- expressions that change control flow ----------------------------------
    def flow_expr(self, e: str, item: str) -> None:
        """Evaluate expression `e` at the current point. `item` is the node id recorded in
        the block for this evaluation (the statement for a top-level expression)."""
        a = self.ast
        k = a.kind(e)
        if k == "Expr_Exit":
            self.append(item)
            self.exit_sites.append({"node": e, "block": self.here(), "via": "exit"})
            self.jump(self.exit, "exit", e)
            return
        if k == "Expr_Throw":
            self.append(item)
            self.throw(e)
            return
        if k == "Expr_FuncCall" and a.call_name(e) in self.always_exit:
            self.append(item)
            self.exit_sites.append({"node": e, "block": self.here(), "via": "call:" + str(a.call_name(e))})
            self.jump(self.exit, "exit", e)
            return
        if (k in OR_OPS or k in AND_OPS) and self.may_terminate(a.field(e, "right")):
            left, right = a.field(e, "left"), a.field(e, "right")
            self.flow_expr_simple(left, item)
            src = self.set_terminator("branch", left, e)
            join, rhs = self.new_block(), self.new_block()
            skip, run = ("true", "false") if k in OR_OPS else ("false", "true")
            self.edge(src, join, skip, left)
            self.edge(src, rhs, run, left)
            self.cur = rhs
            self.flow_expr(right, right)
            self.fall_to(join)
            return
        if k == "Expr_BinaryOp_Coalesce" and self.may_terminate(a.field(e, "right")):
            left, right = a.field(e, "left"), a.field(e, "right")
            self.flow_expr_simple(left, item)
            src = self.set_terminator("coalesce", left, e)
            join, rhs = self.new_block(), self.new_block()
            self.edge(src, join, "notnull", left)
            self.edge(src, rhs, "null", left)
            self.cur = rhs
            self.flow_expr(right, right)
            self.fall_to(join)
            return
        if k == "Expr_Ternary" and (self.may_terminate(a.field(e, "if")) or self.may_terminate(a.field(e, "else"))):
            cond, then, other = a.field(e, "cond"), a.field(e, "if"), a.field(e, "else")
            self.flow_expr_simple(cond, item)
            src = self.set_terminator("branch", cond, e)
            join = self.new_block()
            if then is None:  # short ternary  c ?: x
                self.edge(src, join, "true", cond)
            else:
                tb = self.new_block()
                self.edge(src, tb, "true", cond)
                self.cur = tb
                self.flow_expr(then, then)
                self.fall_to(join)
            eb = self.new_block()
            self.edge(src, eb, "false", cond)
            self.cur = eb
            self.flow_expr(other, other)
            self.fall_to(join)
            return
        if k == "Expr_Assign" and self.splits(a.field(e, "expr")):
            # `$x = a() || die()`: the right side decides the flow; the assignment only
            # happens on the path that continues.
            self.flow_expr(a.field(e, "expr"), item)
            return
        self.flow_expr_simple(e, item)

    def flow_expr_simple(self, e: str, item: str) -> None:
        """An expression kept in one block. An exit buried in a position we do not split on
        is over-approximated: the block gets an extra `exit` edge and may also continue."""
        self.append(item)
        if self.may_terminate(e):
            self.flag(e, "exit-or-throw-inside-expression: modelled as 'may exit' (extra exit edge)")
            self.exit_sites.append({"node": e, "block": self.here(), "via": "maybe"})
            self.edge(self.here(), self.exit, "exit", e)
        self.after_includes(e)

    def after_includes(self, e: str) -> None:
        """An include is a point where another file's code runs in this scope (Xie & Aiken
        splice it there). End the block after it so the resolver can splice at a boundary."""
        incs = self.includes_in(e)
        if not incs:
            return
        bid = self.here()
        for inc in incs:
            self.include_sites.append({"node": inc, "block": bid})
        nxt = self.new_block()
        self.edge(bid, nxt, "next")
        self.cur = nxt

    def throw(self, node: str) -> None:
        if self.trys:
            src = self.here()
            for h in self.trys[-1]["handlers"]:
                self.edge(src, h, "throw", node)
            if not self.trys[-1]["handlers"]:  # try/finally without catch
                self.edge(src, self.trys[-1]["finally"] or self.exit, "throw", node)
            self.cur = None
        else:
            self.jump(self.exit, "throw", node)

    # -- statements -------------------------------------------------------------
    def stmts(self, ids: list[str]) -> None:
        for s in ids:
            self.stmt(s)

    def stmt(self, s: str) -> None:
        a = self.ast
        k = a.kind(s)
        if k == "Stmt_Expression":
            self.flow_expr(a.field(s, "expr"), s)
        elif k == "Stmt_Echo":
            self.append(s)
            if self.may_terminate(s):
                self.flag(s, "exit-or-throw-inside-echo: modelled as 'may exit'")
                self.edge(self.here(), self.exit, "exit", s)
            self.after_includes(s)
        elif k == "Stmt_If":
            self.stmt_if(s)
        elif k == "Stmt_While":
            self.stmt_while(s)
        elif k == "Stmt_Do":
            self.stmt_do(s)
        elif k == "Stmt_For":
            self.stmt_for(s)
        elif k == "Stmt_Foreach":
            self.stmt_foreach(s)
        elif k == "Stmt_Switch":
            self.stmt_switch(s)
        elif k in ("Stmt_Break", "Stmt_Continue"):
            self.stmt_jump(s, k)
        elif k == "Stmt_Return":
            expr = a.field(s, "expr")
            if expr is not None and self.may_terminate(expr):
                self.flow_expr(expr, s)
                if self.cur is None:
                    return
            else:
                self.append(s)
                if expr is not None:
                    self.after_includes(expr)
            self.jump(self.exit, "return", s)
        elif k == "Stmt_TryCatch":
            self.stmt_try(s)
        elif k == "Stmt_Label":
            name = a.name_of(a.field(s, "name")) or s
            b = self.new_block()
            self.fall_to(b)
            self.labels[name] = b
            self.append(s)
        elif k == "Stmt_Goto":
            self.append(s)
            self.gotos.append((self.here(), a.name_of(a.field(s, "name")) or "", s))
            self.cur = None
        elif k == "Stmt_HaltCompiler":
            self.append(s)
            self.jump(self.exit, "halt", s)
        elif k in ("Stmt_Namespace", "Stmt_Block"):
            self.stmts(a.field_list(s, "stmts"))
        elif k == "Stmt_Declare":
            self.append(s)
            self.stmts(a.field_list(s, "stmts"))
        else:
            # Declarations (function/class: their bodies are separate units), global, static,
            # unset, inline HTML, const, use, nop ... are straight-line items.
            self.append(s)
            if k not in SCOPE_KINDS and k not in CLASS_LIKE_KINDS:
                if self.may_terminate(s):
                    self.flag(s, f"exit-or-throw-inside-{k}: modelled as 'may exit'")
                    self.edge(self.here(), self.exit, "exit", s)
                if self.includes_in(s):
                    self.after_includes(s)

    def cond(self, c: str, stmt: str) -> str:
        """Evaluate a condition in the current block and make it the block's branch."""
        if self.may_terminate(c):
            self.flag(c, "exit-or-throw-inside-condition: modelled as 'may exit'")
            self.edge(self.here(), self.exit, "exit", c)
        self.append(c)
        if self.includes_in(c):
            self.flag(c, "include-inside-condition: include site recorded, block not split")
            for inc in self.includes_in(c):
                self.include_sites.append({"node": inc, "block": self.here()})
        return self.set_terminator("branch", c, stmt)

    def stmt_if(self, s: str) -> None:
        a = self.ast
        after = None
        ends: list[str] = []
        arms = [(a.field(s, "cond"), a.field_list(s, "stmts"))]
        arms += [(a.field(ei, "cond"), a.field_list(ei, "stmts")) for ei in a.field_list(s, "elseifs")]
        else_id = a.field(s, "else")
        for cond_id, body in arms:
            src = self.cond(cond_id, s)
            tb = self.new_block()
            self.edge(src, tb, "true", cond_id)
            self.cur = tb
            self.stmts(body)
            if self.cur is not None:
                ends.append(self.cur)
            fb = self.new_block()
            self.edge(src, fb, "false", cond_id)
            self.cur = fb
        if else_id is not None:
            self.stmts(a.field_list(else_id, "stmts"))
        if self.cur is not None:
            ends.append(self.cur)
        if ends:
            after = self.new_block()
            for b in ends:
                self.edge(b, after, "next")
        self.cur = after

    def loop_body(self, body: list[str], brk: str, cont: str, kind: str) -> None:
        self.loops.append({"kind": kind, "brk": brk, "cont": cont})
        self.stmts(body)
        self.loops.pop()

    def stmt_while(self, s: str) -> None:
        a = self.ast
        head = self.new_block()
        self.fall_to(head)
        src = self.cond(a.field(s, "cond"), s)
        body, after = self.new_block(), self.new_block()
        self.edge(src, body, "true", a.field(s, "cond"))
        self.edge(src, after, "false", a.field(s, "cond"))
        self.cur = body
        self.loop_body(a.field_list(s, "stmts"), after, head, "while")
        self.jump(head, "back")
        self.cur = after

    def stmt_do(self, s: str) -> None:
        a = self.ast
        body, test, after = self.new_block(), self.new_block(), self.new_block()
        self.fall_to(body)
        self.loop_body(a.field_list(s, "stmts"), after, test, "do")
        self.fall_to(test)
        src = self.cond(a.field(s, "cond"), s)
        self.edge(src, body, "true", a.field(s, "cond"))
        self.edge(src, after, "false", a.field(s, "cond"))
        self.cur = after

    def stmt_for(self, s: str) -> None:
        a = self.ast
        for x in a.field_list(s, "init"):
            self.flow_expr(x, x)
        head = self.new_block()
        self.fall_to(head)
        conds = a.field_list(s, "cond")
        body, step, after = self.new_block(), self.new_block(), self.new_block()
        if conds:
            for c in conds[:-1]:
                self.append(c)
            src = self.cond(conds[-1], s)
            self.edge(src, body, "true", conds[-1])
            self.edge(src, after, "false", conds[-1])
        else:  # for(;;) only leaves through break/return/exit
            self.edge(head, body, "true")
        self.cur = body
        self.loop_body(a.field_list(s, "stmts"), after, step, "for")
        self.fall_to(step)
        for x in a.field_list(s, "loop"):
            self.flow_expr(x, x)
        self.jump(head, "back")
        self.cur = after

    def stmt_foreach(self, s: str) -> None:
        a = self.ast
        self.flow_expr(a.field(s, "expr"), a.field(s, "expr"))  # evaluated once, before the loop
        head = self.new_block()
        self.fall_to(head)
        self.set_terminator("foreach", s, s)
        body, after = self.new_block(), self.new_block()
        self.edge(head, body, "iterate", s)
        self.edge(head, after, "done", s)
        self.cur = body
        for v in (a.field(s, "keyVar"), a.field(s, "valueVar")):  # assigned on every iteration
            if v is not None:
                self.append(v)
        self.loop_body(a.field_list(s, "stmts"), after, head, "foreach")
        self.jump(head, "back")
        self.cur = after

    def stmt_switch(self, s: str) -> None:
        a = self.ast
        self.flow_expr(a.field(s, "cond"), a.field(s, "cond"))
        cases = a.field_list(s, "cases")
        after = self.new_block()
        bodies = [self.new_block() for _ in cases]
        default_body = None
        for c, b in zip(cases, bodies):
            if a.field(c, "cond") is None:
                default_body = b
        # tests run in source order, default is taken only when no case matches
        for c, b in zip(cases, bodies):
            cc = a.field(c, "cond")
            if cc is None:
                continue
            test = self.new_block()
            self.fall_to(test)
            src = self.cond(cc, c)
            self.edge(src, b, "true", cc)
            nxt = self.new_block()
            self.edge(src, nxt, "false", cc)
            self.cur = nxt
        self.jump(default_body if default_body is not None else after, "next")
        self.loops.append({"kind": "switch", "brk": after, "cont": after})
        for i, (c, b) in enumerate(zip(cases, bodies)):
            self.cur = b if self.cur is None else self.cur
            if self.cur != b:
                self.fall_to(b)  # fall-through from the previous case
            self.stmts(a.field_list(c, "stmts"))
        self.loops.pop()
        self.fall_to(after)

    def stmt_jump(self, s: str, k: str) -> None:
        a = self.ast
        self.append(s)
        num_id = a.field(s, "num")
        n = 1
        if num_id is not None:
            v = a.attr(num_id, "value") if a.kind(num_id) == "Scalar_Int" else None
            if not isinstance(v, int) or v < 1:
                self.flag(s, "non-literal-break-level: treated as fatal error")
                self.jump(self.exit, "error", s)
                return
            n = v
        if n > len(self.loops):
            self.flag(s, f"{k[5:].lower()}-{n}-outside-loop: PHP fatal error")
            self.jump(self.exit, "error", s)
            return
        target = self.loops[-n]
        if k == "Stmt_Break":
            self.jump(target["brk"], "break", s)
        else:
            if target["kind"] == "switch":
                self.flag(s, "continue-targets-switch: acts like break (PHP manual, E_WARNING since 7.3)")
            self.jump(target["cont"], "break" if target["kind"] == "switch" else "continue", s)

    def stmt_try(self, s: str) -> None:
        a = self.ast
        catches = a.field_list(s, "catches")
        fin_id = a.field(s, "finally")
        after = self.new_block()
        handlers = [self.new_block() for _ in catches]
        fin = self.new_block() if fin_id is not None else None
        start = self.new_block()
        self.fall_to(start)
        ctx = {"handlers": handlers, "finally": fin, "blocks": [start]}
        self.trys.append(ctx)
        self.stmts(a.field_list(s, "stmts"))
        self.trys.pop()
        # any block of the try body may raise (calls can throw): edge to every catch
        for b in sorted(set(ctx["blocks"]), key=lambda x: int(x[1:])):
            for h in handlers:
                self.edge(b, h, "catch")
        target = fin if fin is not None else after
        self.fall_to(target) if self.cur is not None else None
        for c, h in zip(catches, handlers):
            self.cur = h
            if a.field(c, "var") is not None:
                self.append(a.field(c, "var"))
            self.stmts(a.field_list(c, "stmts"))
            if self.cur is not None:
                self.edge(self.cur, target, "next")
        if fin is not None:
            self.flag(s, "finally-approximation: return/break inside try skip the finally block in this CFG")
            self.cur = fin
            self.stmts(a.field_list(fin_id, "stmts"))
            self.fall_to(after)
        self.cur = after if self.has_preds(after) or self.cur == after else None

    def has_preds(self, b: str) -> bool:
        return any(e["to"] == b for e in self.edges)

    # -- finish ---------------------------------------------------------------
    def build(self, body: list[str], kind: str) -> dict[str, Any]:
        a = self.ast
        if kind == "arrow-fn":
            expr = a.field(self.unit_node, "expr")
            self.flow_expr(expr, expr)
            self.jump(self.exit, "return")
        else:
            self.stmts(body)
            self.jump(self.exit, "end")
        for src, name, node in self.gotos:
            if name in self.labels:
                self.edge(src, self.labels[name], "goto", node)
            else:
                self.flag(node, "goto-unknown-label")
                self.edge(src, self.exit, "error", node)

        succ: dict[str, list[str]] = {b["id"]: [] for b in self.blocks}
        for e in self.edges:
            if e["to"] not in succ[e["from"]]:
                succ[e["from"]].append(e["to"])
        idom = immediate_dominators(succ, self.entry)
        reachable = set(idom)
        for b in self.blocks:
            b["reachable"] = b["id"] in reachable
            if b["role"] is None:
                del b["role"]
            if b["terminator"] is None:
                del b["terminator"]
        into_exit = [e for e in self.edges if e["to"] == self.exit and e["from"] in reachable]
        always_exits = bool(into_exit) and all(e["label"] == "exit" for e in into_exit)
        unreachable_items = [i for b in self.blocks if not b["reachable"] for i in b["items"]]
        return {
            "id": self.unit_node,
            "kind": kind,
            "name": a.unit_name(self.unit_node),
            "entry": self.entry,
            "exit": self.exit,
            "blocks": self.blocks,
            "edges": self.edges,
            "idom": {b: idom[b] for b in sorted(idom, key=lambda x: int(x[1:])) if b != self.entry},
            "alwaysExits": always_exits,
            "includeSites": self.include_sites,
            "exitSites": self.exit_sites,
            "unreachableItems": unreachable_items,
            "flags": self.flags,
        }


COMPOUND_STMTS = frozenset({
    "Stmt_If", "Stmt_ElseIf", "Stmt_Else", "Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach",
    "Stmt_Switch", "Stmt_Case", "Stmt_TryCatch", "Stmt_Catch", "Stmt_Finally", "Stmt_Namespace",
    "Stmt_Block",
})


def unit_statements(ast: AstDoc, unit: dict[str, Any]) -> list[str]:
    """Every simple statement that belongs to this unit (not to nested functions)."""
    out = []
    for nid in ast.walk(unit["id"]):
        k = ast.kind(nid)
        if nid == unit["id"] or not k or not k.startswith("Stmt_") or k in COMPOUND_STMTS:
            continue
        # skip statements that live inside a class body (properties, consts, methods)
        if k in ("Stmt_Property", "Stmt_ClassConst", "Stmt_TraitUse", "Stmt_EnumCase"):
            continue
        out.append(nid)
    return out


def validate_unit(ast: AstDoc, unit: dict[str, Any]) -> list[str]:
    """Structural invariants. An empty list means the unit's graph is well formed."""
    problems: list[str] = []
    ids = {b["id"] for b in unit["blocks"]}
    seen: dict[str, int] = {}
    for b in unit["blocks"]:
        for i in b["items"]:
            seen[i] = seen.get(i, 0) + 1
    for s in unit_statements(ast, unit):
        if seen.get(s, 0) != 1:
            problems.append(f"statement {s} ({ast.kind(s)}) is in {seen.get(s, 0)} blocks")
    for e in unit["edges"]:
        if e["from"] not in ids or e["to"] not in ids:
            problems.append(f"edge to/from unknown block: {e}")
        if e["from"] == unit["exit"]:
            problems.append(f"edge leaves EXIT: {e}")
    if any(e["to"] == unit["entry"] for e in unit["edges"]):
        problems.append("edge into ENTRY")
    exit_block = next(b for b in unit["blocks"] if b["id"] == unit["exit"])
    if exit_block["items"]:
        problems.append("EXIT block has items")
    return problems


UNIT_KINDS = {
    "Stmt_Function": "function",
    "Stmt_ClassMethod": "method",
    "Expr_Closure": "closure",
    "Expr_ArrowFunction": "arrow-fn",
}


def build_file_cfg(ast: AstDoc, always_exit: frozenset[str] = frozenset()) -> dict[str, Any]:
    """All units of one file, in pre-order of their declaring node (deterministic)."""
    facts = Facts(ast, always_exit)
    units = [UnitBuilder(ast, ast.root_id, facts).build(ast.field_list(ast.root_id, "stmts"), "main")]
    for nid in ast.walk(ast.root_id, into_scopes=True):
        k = ast.kind(nid)
        if k in UNIT_KINDS:
            stmts = ast.field(nid, "stmts")
            if k == "Stmt_ClassMethod" and stmts is None:
                continue  # abstract / interface method: no body
            units.append(UnitBuilder(ast, nid, facts).build(ast.field_list(nid, "stmts"), UNIT_KINDS[k]))
    return {
        "schemaVersion": CFG_SCHEMA_VERSION,
        "fileId": ast.file_id,
        "path": ast.path,
        "units": units,
    }


def function_index(asts: list[AstDoc]) -> dict[str, list[tuple[str, str]]]:
    """lower-case function name -> [(fileId, nodeId)] for every `function f()` in the app."""
    idx: dict[str, list[tuple[str, str]]] = {}
    for ast in asts:
        for nid in ast.walk(ast.root_id, into_scopes=True):
            if ast.kind(nid) == "Stmt_Function":
                name = ast.name_of(ast.field(nid, "name"))
                if name:
                    idx.setdefault(name.lower(), []).append((ast.file_id, nid))
    return idx


def build_project_cfgs(asts: list[AstDoc], max_rounds: int = 20) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build every file's CFG, finding always-exiting functions to a fixpoint.

    A function is "always exiting" when every path from its entry ends in `exit`.
    Calls to it are cut like `exit` (Xie & Aiken, Sec. 3.1.6). We only use a name when it
    is defined exactly once in the app; a name defined twice is ambiguous and not cut
    (that keeps extra paths, which is the safe direction).
    """
    idx = function_index(asts)
    unique = {name: locs[0] for name, locs in idx.items() if len(locs) == 1}
    always: frozenset[str] = frozenset()
    rounds = 0
    while True:
        rounds += 1
        cfgs = [build_file_cfg(a, always) for a in asts]
        found = set()
        for cfg in cfgs:
            for u in cfg["units"]:
                if u["kind"] == "function" and u["alwaysExits"]:
                    name = u["name"].lower()
                    if unique.get(name) == (cfg["fileId"], u["id"]):
                        found.add(name)
        new = frozenset(found)
        if new == always or rounds >= max_rounds:
            break
        always = new
    summary = {
        "alwaysExitFunctions": sorted(always),
        "ambiguousFunctionNames": sorted(n for n, locs in idx.items() if len(locs) > 1),
        "fixpointRounds": rounds,
    }
    return cfgs, summary


def run(out_dir: Path, pretty: bool = False) -> dict[str, Any]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    asts = [AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]]
    cfgs, summary = build_project_cfgs(asts)
    cfg_dir = out_dir / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for old in cfg_dir.glob("*.json"):
        old.unlink()
    files = []
    totals = {"units": 0, "blocks": 0, "edges": 0, "flags": 0, "unreachableItems": 0, "includeSites": 0}
    for cfg in cfgs:
        data = canonical_json(cfg, pretty)
        (cfg_dir / f"{cfg['fileId']}.json").write_bytes(data)
        u = cfg["units"]
        files.append({"fileId": cfg["fileId"], "path": cfg["path"], "cfg": f"cfg/{cfg['fileId']}.json",
                      "sha256": sha256_hex(data), "units": len(u)})
        totals["units"] += len(u)
        for unit in u:
            totals["blocks"] += len(unit["blocks"])
            totals["edges"] += len(unit["edges"])
            totals["flags"] += len(unit["flags"])
            totals["unreachableItems"] += len(unit["unreachableItems"])
            totals["includeSites"] += len(unit["includeSites"])
    index = {
        "schemaVersion": CFG_SCHEMA_VERSION,
        "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "cfg"},
        "generatedFor": manifest["contentHash"],
        "files": files,
        "totals": totals,
        **summary,
    }
    (cfg_dir / "index.json").write_bytes(canonical_json(index, pretty=True))
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.cfg", description="Build CFGs from analysis-out/ast")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    if not (args.out_dir / "manifest.json").exists():
        print(f"error: {args.out_dir}/manifest.json not found. Run `python -m c1.extract` first.", file=sys.stderr)
        return 1
    idx = run(args.out_dir, args.pretty)
    t = idx["totals"]
    print(f"{len(idx['files'])} files, {t['units']} units, {t['blocks']} blocks, {t['edges']} edges, "
          f"{t['flags']} flags, {t['unreachableItems']} unreachable items -> {args.out_dir}/cfg")
    if idx["alwaysExitFunctions"]:
        print("  always-exiting functions: " + ", ".join(idx["alwaysExitFunctions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
