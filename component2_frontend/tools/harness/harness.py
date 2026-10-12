"""Component 2 whole-app test harness: nikic/php-parser AST -> output timelines (schema 1.0).

A **test harness, not Member 01's core** (docs/c2-roadmap.md Phase 1). It lets Stages 1-5 run on
whole procedural PHP apps before the real static analysis exists. It emits the same shapes and the
same id scheme as tools/gen_timelines.py ("<fileId>#<token index>"), so src/mapping/loader.py
reads its output unchanged. PHP is parsed (tools/harness/parse.php), never executed.

One PHP file gives one page timeline (its top-level output) and one timeline per function that
produces output ("<file>#<function>"). Function calls and includes are recorded, not inlined
(extension lists `calls` and `includes`, pending Member 01 Q4).

Reads are conservative; nothing is guessed:

  $row['x'], $row from mysqli_fetch_* / mysql_fetch_* on a $result from mysqli_query / mysql_query
  with string-literal SQL          -> db_row_field. Single-table SELECT: table and column
                                      (SELECT * as in the mocks, pending Member 01 Q1);
                                      a join or anything else: ambiguous
  $_SESSION / $_GET ... / $_SERVER -> session / request / server
  a literal                        -> literal
  h(), htmlspecialchars(), htmlentities() around a read -> computed, with derivedFrom
  anything else                    -> unresolved

"Comes from" is decided inside one scope (a file's top level or one function) and only when it is
certain: the definition that reaches a use must be the latest one before it, sit on the use's own
control path (never inside a branch or loop the use is not in), have no include / extract / eval
between it and the use, and not be overwritten later in a loop both share. A row variable must be
assigned only from fetch calls. Otherwise the read is unresolved.

An echo whose expression is a `.` concatenation (or several echo arguments) gives one read per
piece, in order. An interpolated string or heredoc is one unresolved read: its pieces are not
separate source expressions.
"""

import re
from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0"
QUERY_FUNCTIONS = {"mysqli_query": 1, "mysql_query": 0}  # name -> index of the SQL argument
FETCH_ROW = ("mysqli_fetch_array", "mysqli_fetch_assoc", "mysqli_fetch_row", "mysql_fetch_array",
             "mysql_fetch_assoc", "mysql_fetch_row")
FETCH_ALL = ("mysqli_fetch_all",)
SUPERGLOBALS = {"$_SESSION": "session", "$_GET": "request", "$_POST": "request", "$_REQUEST": "request",
                "$_COOKIE": "request", "$_SERVER": "server"}
ESCAPE_WRAPPERS = ("h", "htmlspecialchars", "htmlentities")
OUTPUT_FUNCTIONS = ("printf", "vprintf", "print_r", "var_dump", "var_export", "readfile", "fpassthru", "passthru")
SCOPE_BREAKERS = ("extract", "eval", "parse_str", "import_request_variables")  # may define any variable
LOOP_NODES = ("Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach")
INCLUDE_KINDS = {1: "include", 2: "include_once", 3: "require", 4: "require_once"}
CONFIDENCE = ("resolved", "ambiguous", "unresolved")

LOC_NOTE = ("Harness extension, pending Member 01 Q13-Q15: loc {startLine, endLine, startCol, endCol} on every "
            "sequence entry; 1-based, columns count bytes, the end is the statement's last byte (an echo's ';' is "
            "included; a print's is not).")
COND_EXPR_NOTE = ("Harness extension, pending Member 01 Q2: condExpr on then/elseif branch enclosures is the "
                  "byte-exact source between the if/elseif parentheses, whitespace just inside them trimmed.")
CALLS_NOTE = ("Harness extensions, pending Member 01 Q4: `calls` lists every call of a function defined in the "
              "app (producesOutput: the callee prints, directly or through a function it calls) and `includes` "
              "every include/require, each with its node id, line and enclosedBy. Recorded only: no output is "
              "inlined.")
QUERIES_NOTE = "Agent extension: `queries` holds the SQL of every query a read was traced to."
NOTES_NOTE = ("Harness extension: `harnessNotes` lists output the schema cannot express or that the harness left "
              "unresolved on purpose (for docs/php-analysis/pattern-log.md).")


@dataclass
class _Def:
    """One place a variable gets a value."""

    pos: int
    chain: tuple  # control path: ((node id, arm), ...), outermost first
    kind: str  # assign | foreach | other
    node: dict | None = None  # assign: the right-hand side; foreach: the Stmt_Foreach


@dataclass
class _Scope:
    name: str | None  # None: the file's top level
    line: int = 1
    outputs: list = field(default_factory=list)  # (node, kind, chain, enclosures)
    defs: dict = field(default_factory=dict)  # "$name" -> [_Def]
    breakers: list = field(default_factory=list)  # positions of include / extract / eval
    calls: list = field(default_factory=list)
    includes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _attr(node: dict, name: str, default=None):
    return node.get("attributes", {}).get(name, default)


def _is(node, *types) -> bool:
    return isinstance(node, dict) and node.get("nodeType") in types


def _name(node) -> str | None:
    """A Name / Identifier node as text."""
    if not isinstance(node, dict):
        return None
    if node.get("nodeType", "").startswith("Name") or node.get("nodeType") in ("Identifier", "VarLikeIdentifier"):
        return node.get("name")
    return None


def _variable(node) -> str | None:
    """"$name" for a plain variable; None for $$x, ${expr} and everything else."""
    if _is(node, "Expr_Variable") and isinstance(node.get("name"), str):
        return "$" + node["name"]
    return None


def _callee(node) -> str | None:
    """The called function's name in lower case, for a call by plain name."""
    if _is(node, "Expr_FuncCall"):
        name = _name(node.get("name"))
        return name.lower().lstrip("\\") if name else None
    return None


def _args(node) -> list:
    return [a["value"] for a in node.get("args", []) if _is(a, "Arg")]


class FileHarness:
    """The timelines of one PHP file. `parsed` is parse.php's output for it."""

    def __init__(self, source: bytes, parsed: dict, file_id: str, entrypoint: str):
        self.source, self.parsed, self.file_id, self.entrypoint = source, parsed, file_id, entrypoint
        try:
            source.decode("utf-8")
            self.encoding = "utf-8"
        except UnicodeDecodeError:
            self.encoding = "cp1252"  # legacy files: decoded for JSON; byte offsets are unaffected
        self.line_starts = [0] + [m.end() for m in re.finditer(b"\n", source)]
        self.top = _Scope(None)
        self.functions: dict[str, _Scope] = {}  # name as written -> scope (first definition wins)
        self.errors = list(parsed.get("errors", []))
        self._class: str | None = None  # the class whose methods are being walked
        self._loops: dict[str, tuple] = {}  # loop id -> (enclosure, node, scope, chain around it)
        self._walk_list(parsed.get("ast") or [], self.top, (), [])

    # ---- source positions

    def text(self, start: int, end: int) -> str:
        """Source bytes start..end-1 as text."""
        return self.source[start:end].decode(self.encoding, errors="replace")

    def node_text(self, node: dict) -> str:
        return self.text(_attr(node, "startFilePos"), _attr(node, "endFilePos") + 1)

    def node_id(self, token: int) -> str:
        return f"{self.file_id}#{token:05d}"

    def position(self, offset: int) -> tuple[int, int]:
        """1-based (line, column) of a byte offset; the column counts bytes."""
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - self.line_starts[lo] + 1

    def loc(self, start: int, end: int) -> dict:
        start_line, start_col = self.position(start)
        end_line, end_col = self.position(end)
        return {"startLine": start_line, "endLine": end_line, "startCol": start_col, "endCol": end_col}

    def paren_text(self, node: dict) -> str | None:
        if _attr(node, "parenStartPos") is None:
            return None
        return self.text(_attr(node, "parenStartPos"), _attr(node, "parenEndPos")).strip()

    # ---- the walk: outputs, definitions, calls and includes, with the control path of each

    def _walk_list(self, nodes, scope: _Scope, chain: tuple, enclosures: list) -> None:
        for node in nodes or []:
            self._walk(node, scope, chain, enclosures)

    def _walk(self, node, scope: _Scope, chain: tuple, enclosures: list) -> None:
        if isinstance(node, list):
            self._walk_list(node, scope, chain, enclosures)
            return
        if not isinstance(node, dict) or "nodeType" not in node:
            return
        kind = node["nodeType"]
        start = _attr(node, "startTokenPos", 0)

        if kind in ("Stmt_InlineHTML", "Stmt_Echo", "Expr_Print"):
            scope.outputs.append((node, kind, chain, list(enclosures)))
            self._walk(node.get("exprs") or node.get("expr"), scope, chain, enclosures)
        elif kind == "Stmt_If":
            self._if(node, scope, chain, enclosures)
        elif kind in LOOP_NODES:
            self._loop(node, scope, chain, enclosures)
        elif kind == "Stmt_Switch":
            self._walk(node.get("cond"), scope, chain, enclosures)
            switch_id = self.node_id(start)
            for case in node.get("cases", []):
                case_id = self.node_id(_attr(case, "startTokenPos", 0))
                enclosure = {"nodeId": switch_id, "kind": "Stmt_Switch", "role": "switch_case", "caseNodeId": case_id,
                             "caseExpr": self.node_text(case["cond"]) if case.get("cond") else None}
                self._walk_list(case.get("stmts"), scope, chain + ((switch_id, case_id),), enclosures + [enclosure])
        elif kind == "Stmt_TryCatch":
            try_id = self.node_id(start)
            parts = [("try", node.get("stmts"))]
            parts += [("catch", c.get("stmts")) for c in node.get("catches", [])]
            if node.get("finally"):
                parts.append(("finally", node["finally"].get("stmts")))
            for index, (part, stmts) in enumerate(parts):
                enclosure = {"nodeId": try_id, "kind": "Stmt_TryCatch", "role": "try_catch", "part": part}
                self._walk_list(stmts, scope, chain + ((try_id, f"{part}{index}"),), enclosures + [enclosure])
            for catch in node.get("catches", []):
                self._define(scope, _variable(catch.get("var")), catch, chain, "other")
        elif kind in ("Stmt_Function", "Stmt_ClassMethod", "Expr_Closure", "Expr_ArrowFunction"):
            self._function(node, kind, scope)
        elif kind in ("Stmt_Class", "Stmt_Trait", "Stmt_Interface", "Stmt_Enum"):
            previous, self._class = self._class, _name(node.get("name")) or "class"
            self._walk_list(node.get("stmts"), scope, chain, enclosures)
            self._class = previous
        else:
            self._expression(node, kind, scope, chain, enclosures)
            for key, value in node.items():
                if key != "attributes" and isinstance(value, (dict, list)):
                    self._walk(value, scope, chain, enclosures)

    def _expression(self, node: dict, kind: str, scope: _Scope, chain: tuple, enclosures: list) -> None:
        """Record what a non-control node means for the scope: definitions, calls, includes, notes."""
        pos, line = _attr(node, "startFilePos", 0), _attr(node, "startLine", 0)
        if kind == "Expr_Assign":
            target = node.get("var")
            if _variable(target):
                self._define(scope, _variable(target), node, chain, "assign", node.get("expr"))
            else:
                self._define_all(scope, target, node, chain)
        elif kind == "Expr_AssignRef" or kind.startswith("Expr_AssignOp_"):
            self._define_all(scope, node.get("var"), node, chain)
        elif kind in ("Expr_PreInc", "Expr_PreDec", "Expr_PostInc", "Expr_PostDec"):
            self._define_all(scope, node.get("var"), node, chain)
        elif kind in ("Stmt_Global", "Stmt_Static", "Stmt_Unset"):
            for item in node.get("vars", []):
                self._define_all(scope, item.get("var") if _is(item, "StaticVar") else item, node, chain)
        elif kind == "Expr_Include":
            scope.breakers.append(pos)
            expr = node.get("expr")
            scope.includes.append({
                "nodeId": self.node_id(_attr(node, "startTokenPos", 0)), "line": line,
                "kind": INCLUDE_KINDS.get(node.get("type"), "include"), "expr": self.node_text(expr),
                "literal": expr.get("value") if _is(expr, "Scalar_String") else None,
                "enclosedBy": list(enclosures)})
        elif kind == "Expr_Eval":
            scope.breakers.append(pos)
        elif kind == "Expr_Exit" and node.get("expr") and not _is(node["expr"], "Scalar_Int"):
            scope.notes.append({"kind": "exit_with_output", "line": line,
                                "detail": self.node_text(node)[:80]})
        elif kind in ("Expr_FuncCall", "Expr_StaticCall"):
            callee = _callee(node)
            if kind == "Expr_StaticCall":
                cls, method = _name(node.get("class")), _name(node.get("name"))
                callee = f"{cls}::{method}".lower() if cls and method else None
            if callee in SCOPE_BREAKERS:
                scope.breakers.append(pos)
            if callee in OUTPUT_FUNCTIONS:
                scope.notes.append({"kind": "output_function", "line": line, "detail": callee})
            if callee == "ob_start":
                scope.notes.append({"kind": "output_buffering", "line": line, "detail": "ob_start()"})
            if callee:
                scope.calls.append({"nodeId": self.node_id(_attr(node, "startTokenPos", 0)), "line": line,
                                    "callee": callee, "enclosedBy": list(enclosures)})

    def _define(self, scope: _Scope, name: str | None, node: dict, chain: tuple, kind: str, rhs=None) -> None:
        if name:
            scope.defs.setdefault(name, []).append(
                _Def(_attr(node, "startFilePos", 0), chain, kind, rhs if kind == "assign" else node))

    def _define_all(self, scope: _Scope, target, node: dict, chain: tuple) -> None:
        """Every plain variable written through `target` (a list(), an array element, a property) is redefined."""
        if isinstance(target, list):
            for item in target:
                self._define_all(scope, item, node, chain)
        elif isinstance(target, dict):
            if _variable(target):
                self._define(scope, _variable(target), node, chain, "other")
            for key in ("var", "items", "value"):
                if key in target:
                    self._define_all(scope, target[key], node, chain)

    def _if(self, node: dict, scope: _Scope, chain: tuple, enclosures: list) -> None:
        if_id = self.node_id(_attr(node, "startTokenPos", 0))
        cond_id = self.node_id(_attr(node, "parenFirstToken", _attr(node.get("cond") or {}, "startTokenPos", 0)))
        self._walk(node.get("cond"), scope, chain, enclosures)
        arms = [("then", node, cond_id, node.get("stmts"))]
        for elseif in node.get("elseifs", []):
            arms.append(("elseif", elseif, self.node_id(_attr(elseif, "parenFirstToken",
                                                              _attr(elseif.get("cond") or {}, "startTokenPos", 0))),
                         elseif.get("stmts")))
        if node.get("else"):
            arms.append(("else", None, cond_id, node["else"].get("stmts")))
        for index, (branch, owner, cond_node_id, stmts) in enumerate(arms):
            if branch == "elseif":
                self._walk(owner.get("cond"), scope, chain, enclosures)
            enclosure = {"nodeId": if_id, "kind": "Stmt_If", "role": "branch", "branch": branch,
                         "condNodeId": cond_node_id}
            cond_expr = self.paren_text(owner) if owner is not None else None
            if cond_expr:
                enclosure["condExpr"] = cond_expr
            self._walk_list(stmts, scope, chain + ((if_id, f"{branch}{index}"),), enclosures + [enclosure])

    def _loop(self, node: dict, scope: _Scope, chain: tuple, enclosures: list) -> None:
        kind = node["nodeType"]
        loop_id = self.node_id(_attr(node, "startTokenPos", 0))
        inner = chain + ((loop_id, "loop"),)
        enclosure = {"nodeId": loop_id, "kind": kind, "role": "iteration", "iterExpr": self.paren_text(node) or "",
                     "iterSourceKind": "unresolved", "valueVar": None, "keyVar": None}
        if kind in ("Stmt_While", "Stmt_Do"):
            cond = node.get("cond")
            if _is(cond, "Expr_Assign") and _variable(cond.get("var")):
                enclosure["valueVar"] = self.node_text(cond["var"])
                if _callee(cond.get("expr")) in FETCH_ROW:
                    enclosure["iterSourceKind"] = "db_row_field"
            self._walk(cond, scope, inner, enclosures)  # the header runs once per iteration
        elif kind == "Stmt_Foreach":
            enclosure["iterExpr"] = self.node_text(node["expr"])
            enclosure["valueVar"] = self.node_text(node["valueVar"])
            enclosure["keyVar"] = self.node_text(node["keyVar"]) if node.get("keyVar") else None
            self._walk(node.get("expr"), scope, chain, enclosures)
            self._define_all(scope, node.get("keyVar"), node, inner)
            if _variable(node.get("valueVar")):
                self._define(scope, _variable(node["valueVar"]), node, inner, "foreach")
            else:
                self._define_all(scope, node.get("valueVar"), node, inner)
        else:  # Stmt_For: which fields a C-style for fills is open (Member 01 Q10), so the variables stay null
            for part in ("init", "cond", "loop"):
                self._walk(node.get(part), scope, inner, enclosures)
        self._loops[loop_id] = (enclosure, node, scope, chain)
        self._walk_list(node.get("stmts"), scope, inner, enclosures + [enclosure])

    def _function(self, node: dict, kind: str, outer: _Scope) -> None:
        if kind == "Stmt_Function":
            name = _name(node.get("name"))
        elif kind == "Stmt_ClassMethod":
            name = f"{self._class or 'class'}::{_name(node.get('name'))}"
        else:
            name = f"closure@{_attr(node, 'startLine', 0)}"
        scope = _Scope(name, _attr(node, "startLine", 0))
        for param in node.get("params", []):
            self._define(scope, _variable(param.get("var")), param, (), "other")
        for use in node.get("uses", []):
            self._define(scope, _variable(use.get("var")), use, (), "other")
        body = node.get("stmts") if kind != "Expr_ArrowFunction" else [node.get("expr")]
        self._walk_list(body, scope, (), [])
        self.functions.setdefault(name, scope)

    # ---- reaching definitions

    def _reaching(self, scope: _Scope, name: str, pos: int, chain: tuple) -> _Def | None:
        """The one definition of `name` that certainly reaches `pos`, or None."""
        defs = scope.defs.get(name, [])
        before = [d for d in defs if d.pos < pos]
        if not before:
            return None
        latest = max(before, key=lambda d: d.pos)
        if latest.chain != chain[:len(latest.chain)]:
            return None  # set inside a branch or loop the use is not in
        if any(latest.pos < b < pos for b in scope.breakers):
            return None  # an include / extract / eval in between may have changed it
        loops = {entry for entry in chain if entry[1] == "loop"}
        if any(d.pos > pos and loops & set(d.chain) for d in defs):
            return None  # overwritten later in a loop both are in: the next iteration sees that value
        return latest

    def _sql(self, scope: _Scope, node, pos: int, chain: tuple):
        """(sql text, source text) for a string-literal SQL argument, directly or through a variable."""
        if _is(node, "Scalar_String") and _attr(node, "kind") in (1, 2):
            return node["value"], self.node_text(node)
        name = _variable(node)
        if name:
            reaching = self._reaching(scope, name, pos, chain)
            if reaching and reaching.kind == "assign" and _is(reaching.node, "Scalar_String") \
                    and _attr(reaching.node, "kind") in (1, 2):
                return reaching.node["value"], self.node_text(reaching.node)
        return None

    def _query_of(self, scope: _Scope, fetch: dict, pos: int, chain: tuple):
        """The query call a fetch call reads from: the call node, or None when that is not certain."""
        args = _args(fetch)
        if not args:
            return None
        result = args[0]
        if _callee(result) in QUERY_FUNCTIONS:
            return result, None
        name = _variable(result)
        if not name:
            return None
        reaching = self._reaching(scope, name, pos, chain)
        if reaching and reaching.kind == "assign" and _callee(reaching.node) in QUERY_FUNCTIONS:
            return reaching.node, name
        return None

    def _row_fetch(self, scope: _Scope, name: str, pos: int, chain: tuple):
        """The fetch call that fills row variable `name` at `pos`, or None."""
        defs = scope.defs.get(name, [])
        reaching = self._reaching(scope, name, pos, chain)
        if reaching is None:
            return None
        if reaching.kind == "assign":
            if _callee(reaching.node) not in FETCH_ROW:
                return None
            if any(not (d.kind == "assign" and _callee(d.node) in FETCH_ROW) for d in defs):
                return None  # also assigned from something else somewhere in the scope
            return reaching.node, reaching.pos, reaching.chain
        if reaching.kind == "foreach":
            if any(d.kind != "foreach" for d in defs):
                return None
            source = reaching.node.get("expr")
            outer = reaching.chain[:-1]
            if _callee(source) in FETCH_ALL:
                return source, _attr(source, "startFilePos", 0), outer
            rows = _variable(source)
            if rows:
                loop_pos = _attr(reaching.node, "startFilePos", 0)
                filled = self._reaching(scope, rows, loop_pos, outer)
                if filled and filled.kind == "assign" and _callee(filled.node) in FETCH_ALL and all(
                        d.kind == "assign" and _callee(d.node) in FETCH_ALL for d in scope.defs.get(rows, [])):
                    return filled.node, filled.pos, filled.chain
        return None

    # ---- reads

    def _var_read(self, scope: _Scope, node: dict, chain: tuple, queries: dict, notes: list) -> dict | None:
        """A read of $name or $name['k']...: None when `node` is not that shape."""
        path, base = [], node
        while _is(base, "Expr_ArrayDimFetch"):
            dim = base.get("dim")
            if _is(dim, "Scalar_String"):
                path.insert(0, dim["value"])
            elif _is(dim, "Scalar_Int"):
                path.insert(0, str(dim["value"]))
            elif _is(dim, "Expr_ConstFetch") and _name(dim.get("name")):
                path.insert(0, _name(dim["name"]))
            else:
                return None
            base = base.get("var")
        name = _variable(base)
        if name is None:
            return None
        read = {"expr": self.node_text(node), "var": name, "path": path}
        pos = _attr(node, "startFilePos", 0)
        if name in SUPERGLOBALS:
            return read | {"sourceKind": SUPERGLOBALS[name], "confidence": "resolved", "source": None}
        unresolved = read | {"sourceKind": "unresolved", "confidence": "unresolved", "source": None}
        fetched = self._row_fetch(scope, name, pos, chain) if path else None
        if fetched is None:
            return unresolved
        fetch, fetch_pos, fetch_chain = fetched
        line = _attr(node, "startLine", 0)
        found = self._query_of(scope, fetch, fetch_pos, fetch_chain)
        if found is None:
            notes.append({"kind": "row_read_not_traced", "line": line, "detail": "the fetch's result is not "
                          "certainly one query call"})
            return unresolved
        query, result_var = found
        query_pos = _attr(query, "startFilePos", 0)
        query_chain = next((d.chain for d in scope.defs.get(result_var, []) if d.node is query), fetch_chain)
        arguments = _args(query)
        index = QUERY_FUNCTIONS[_callee(query)]
        sql = self._sql(scope, arguments[index], query_pos, query_chain) if len(arguments) > index else None
        if sql is None:  # interpolated or computed SQL: never guessed
            notes.append({"kind": "row_read_not_traced", "line": line, "detail": "the SQL is not a string literal "
                          "(interpolated or built)"})
            return unresolved
        sql_value, sql_source = sql
        shape = parse_select(sql_value)
        fetch_id = self.node_id(_attr(fetch, "startTokenPos", 0))
        query_id = self.node_id(_attr(query, "startTokenPos", 0))
        queries[query_id] = {"line": _attr(query, "startLine", 0), "sql": sql_source,
                             "tables": [shape.table] if shape.table else [], "columns": shape.columns,
                             "resultVar": result_var}
        source = {"fetchNodeId": fetch_id, "queryNodeId": query_id, "table": None, "column": None}
        if shape.table is None or len(path) != 1:
            return read | {"sourceKind": "db_row_field", "confidence": "ambiguous", "source": source}
        column = path[0]
        if shape.columns is not None and column not in shape.columns:
            if _is(node.get("dim"), "Scalar_Int"):  # a numeric index on a row: a column, but not which one
                return read | {"sourceKind": "db_row_field", "confidence": "ambiguous", "source": source}
            return unresolved  # not in the select list
        if _is(node.get("dim"), "Scalar_Int"):
            return read | {"sourceKind": "db_row_field", "confidence": "ambiguous", "source": source}
        return read | {"sourceKind": "db_row_field", "confidence": "resolved",
                       "source": source | {"table": shape.table, "column": column}}

    def _opaque(self, node: dict) -> dict:
        text = self.node_text(node)
        return {"expr": text, "var": text, "path": [], "sourceKind": "unresolved", "confidence": "unresolved",
                "source": None}

    def _reads(self, scope: _Scope, node: dict, chain: tuple, queries: dict, notes: list) -> list[dict]:
        """The reads an output expression prints, in order (one per piece of a concatenation)."""
        if _is(node, "Expr_BinaryOp_Concat"):
            return (self._reads(scope, node["left"], chain, queries, notes)
                    + self._reads(scope, node["right"], chain, queries, notes))
        line = _attr(node, "startLine", 0)
        if _is(node, "Scalar_String") and _attr(node, "kind") in (1, 2) or _is(node, "Scalar_Int", "Scalar_Float"):
            return [{"expr": self.node_text(node), "var": None, "path": None, "sourceKind": "literal",
                     "confidence": "resolved", "source": None}]
        if _is(node, "Scalar_String", "Scalar_InterpolatedString"):
            text = self.node_text(node)
            heredoc = _attr(node, "kind") in (3, 4)
            kind = "heredoc_output" if heredoc else "interpolated_string_output"
            notes.append({"kind": kind, "line": line, "markup": "<" in text, "detail": text[:80]})
            return [self._opaque(node)]
        if _callee(node) in ESCAPE_WRAPPERS and _args(node):
            inner = [r for r in self._reads(scope, _args(node)[0], chain, queries, notes)
                     if r["sourceKind"] != "literal"]
            if inner:
                worst = max((r["confidence"] for r in inner), key=CONFIDENCE.index)
                return [{"expr": self.node_text(node), "var": None, "path": None, "sourceKind": "computed",
                         "confidence": worst, "source": None, "derivedFrom": inner}]
            return [self._opaque(node)]
        read = self._var_read(scope, node, chain, queries, notes)
        return [read if read is not None else self._opaque(node)]

    # ---- timelines

    def scopes(self) -> list[_Scope]:
        return [self.top] + [self.functions[name] for name in sorted(self.functions)]

    def loop_enclosure(self, enclosure: dict) -> dict:
        """iterSourceKind for a foreach over fetched rows needs the definitions, which are complete only now."""
        entry = self._loops.get(enclosure["nodeId"])
        if entry is None or entry[1]["nodeType"] != "Stmt_Foreach":
            return enclosure
        _, node, scope, chain = entry
        source = node.get("expr")
        if _callee(source) in FETCH_ALL:
            enclosure["iterSourceKind"] = "db_row_field"
        elif _variable(source):
            filled = self._reaching(scope, _variable(source), _attr(node, "startFilePos", 0), chain)
            if filled and filled.kind == "assign" and _callee(filled.node) in FETCH_ALL:
                enclosure["iterSourceKind"] = "db_row_field"
        return enclosure

    def output_functions(self) -> dict[str, bool]:
        """lower-case name -> prints directly, for every function this file defines."""
        return {name.lower(): bool(scope.outputs) for name, scope in self.functions.items()}

    def timeline(self, scope: _Scope, provenance: dict, produces_output=None) -> dict | None:
        """The schema 1.0 document of one scope, or None when it prints nothing.

        `produces_output(name)` says whether an app function prints (None: not an app function); without it,
        only this file's own functions are known."""
        if not scope.outputs:
            return None
        if produces_output is None:
            produces_output = self.output_functions().get
        queries, notes, sequence = {}, list(scope.notes), []
        for node, kind, chain, enclosures in sorted(scope.outputs, key=lambda o: _attr(o[0], "startTokenPos", 0)):
            start = _attr(node, "startFilePos", 0)
            entry = {"id": self.node_id(_attr(node, "startTokenPos", 0)), "kind": kind}
            if kind == "Stmt_InlineHTML":
                end = _attr(node, "endFilePos", start)
                entry["raw"] = self.text(start, end + 1)
            else:
                end = _attr(node, "spanEndPos", _attr(node, "endFilePos", start))
                exprs = node.get("exprs") if kind == "Stmt_Echo" else [node.get("expr")]
                entry["reads"] = [read for expr in exprs for read in self._reads(scope, expr, chain, queries, notes)]
                if len(entry["reads"]) > 1 and any(
                        r["sourceKind"] == "literal" and "<" in r["expr"] for r in entry["reads"]):
                    notes.append({"kind": "markup_in_concatenated_echo", "line": _attr(node, "startLine", 0),
                                  "markup": True, "detail": self.text(start, min(end + 1, start + 80))})
            entry["enclosedBy"] = [self.loop_enclosure(e) if e["role"] == "iteration" else e for e in enclosures]
            entry["loc"] = self.loc(start, end)
            sequence.append(entry)
        calls = [c | {"producesOutput": produces_output(c["callee"])} for c in scope.calls
                 if produces_output(c["callee"]) is not None]
        entrypoint = self.entrypoint if scope.name is None else f"{self.entrypoint}#{scope.name}"
        notes.sort(key=lambda n: (n["line"], n["kind"], n["detail"]))
        return {
            "entrypoint": entrypoint,
            "schemaVersion": SCHEMA_VERSION,
            "sequence": sequence,
            "queries": dict(sorted(queries.items())),
            "calls": sorted(calls, key=lambda c: c["nodeId"]),
            "includes": sorted(scope.includes, key=lambda i: i["nodeId"]),
            "harnessNotes": notes,
            "_provenance": provenance | {
                "function": scope.name,
                "sourceEncoding": self.encoding,
                "locExtension": LOC_NOTE, "condExprExtension": COND_EXPR_NOTE, "callsExtension": CALLS_NOTE,
                "queriesExtension": QUERIES_NOTE, "harnessNotesExtension": NOTES_NOTE,
            },
        }


@dataclass(frozen=True)
class SelectShape:
    table: str | None  # the one table of a single-table SELECT; None for a join or anything else
    columns: list | None  # the selected names and aliases; None for SELECT *


_SELECT = re.compile(r"^\s*select\s+(?:distinct\s+)?(?P<cols>.+?)\s+from\s+`?(?P<table>[A-Za-z_][\w$]*)`?(?P<rest>.*)$",
                     re.I | re.S)
_REST = re.compile(r"^\s*(?:(?:as\s+)?(?!where\b|order\b|group\b|limit\b|having\b)[A-Za-z_]\w*)?\s*"
                   r"(?:(?:where|order\s+by|group\s+by|limit|having)\b.*)?;?\s*$", re.I | re.S)
_COLUMN = re.compile(r"^(?:`?\w+`?\.)?`?(?P<name>\w+)`?$")
_ALIAS = re.compile(r"^.+?\s+as\s+`?(?P<name>\w+)`?$", re.I | re.S)


def parse_select(sql: str) -> SelectShape:
    """What a literal SQL string selects, only as far as that is plain to read."""
    found = _SELECT.match(sql)
    if not found:
        return SelectShape(None, None)
    rest = found.group("rest")
    if re.search(r"\b(join|union|select)\b", rest, re.I) or rest.lstrip().startswith(",") or not _REST.match(rest):
        return SelectShape(None, None)
    cols = found.group("cols").strip()
    if cols == "*":
        return SelectShape(found.group("table"), None)
    names, depth, start = [], 0, 0
    items = []
    for index, ch in enumerate(cols):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(cols[start:index])
            start = index + 1
    items.append(cols[start:])
    for item in items:
        item = item.strip()
        if re.search(r"\bselect\b", item, re.I) or item.endswith("*"):
            return SelectShape(None, None)  # a subquery or t.*: not plain
        alias, column = _ALIAS.match(item), _COLUMN.match(item)
        if alias:
            names.append(alias.group("name"))
        elif column:
            names.append(column.group("name"))
    return SelectShape(found.group("table"), names)
