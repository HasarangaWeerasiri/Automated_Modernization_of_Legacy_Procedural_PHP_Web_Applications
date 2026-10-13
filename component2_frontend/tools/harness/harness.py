"""Component 2 whole-app test harness, timeline builder: an AST -> output timelines (schema 1.0).

A **test harness, not Member 01's core** (docs/c2-roadmap.md Phases 1 and 1b). It lets Stages 1-5 run
on whole procedural PHP apps before the real static analysis exists. It reads an AST in Member 01's
format through src/mapping/loader.py - the harness's own (tools/harness/parse.php) or Member 01's -
and uses Member 01's conventions: node ids are the AST's pre-order ids, `loc` is the AST node's loc
(1-based lines, 0-based columns and bytes, exclusive ends), and condExpr is the source of the `cond` node.

One PHP file gives one page timeline (its top-level output) and one timeline per function that
produces output ("<file>#<function>"). A function's output is never inlined: the call site is a
sequence entry {kind: "call", callee, outputsInside} (Member 01 Q4, proposed). Includes are recorded
in the extension list `includes`.

Reads are conservative; nothing is guessed:

  $row['x'], $row from mysqli_fetch_* / mysql_fetch_* on a $result from mysqli_query / mysql_query
  with string-literal SQL          -> db_row_field. Single-table SELECT with a column list: table
                                      and column. Single-table SELECT *: resolved, with basis
                                      "schema-expanded", only when the app ships a schema that
                                      declares the table (Member 01 Q1, proposed); else ambiguous.
                                      A join or anything else: ambiguous
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
piece, in order. Roadmap Phase 2a adds three more inputs, each conservative and each named in
`_provenance`:

  parts (boundary-rules section 1, Member 01 Q16)
      An echo or print whose arguments are strings, interpolated strings, heredocs, concatenations
      or several arguments carries `parts`, in source order: {kind: "literal", value, nodeId} with
      the PHP-decoded text, or {kind: "expr", expr, nodeId, read} with the index of its own read.
      They come from the AST (Scalar_InterpolatedString.parts, concat nodes, Stmt_Echo.exprs), and
      nodeId is the AST node. An argument that cannot be split exactly (a float) leaves the echo
      without parts: one opaque node, as before.
  copy propagation (Q17)
      `$v = $row['k'];` then a use of `$v` in the same block, with no branch, no assignment to
      `$v` or `$row` and no call that takes either as an argument in between: the read of `$v` is
      `computed`, derived from the read of `$row['k']`. Anything else stays unresolved.
  interpolated SQL values (Q17)
      When every interpolation in a SQL string sits inside a quoted value or is a whole value
      after a comparison operator, the select list and the table come from the string's fixed
      text, so reads resolve as for literal SQL and the query is marked `valuesInterpolated`.
      An interpolation anywhere else leaves the read unresolved.
"""

import posixpath
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
DYNAMIC_CALLERS = ("call_user_func", "call_user_func_array")  # call a function chosen at run time
LOOP_NODES = ("Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach")
CALL_NODES = ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall", "Expr_NullsafeMethodCall")
INCLUDE_KINDS = {1: "include", 2: "include_once", 3: "require", 4: "require_once"}
CONFIDENCE = ("resolved", "ambiguous", "unresolved")
OUTPUTS_INSIDE = (False, "unknown", True)  # weakest first: what is known about a function's output
SCHEMA_BASIS = "schema-expanded"

LOC_NOTE = ("Member 01's convention (Q13-Q15): loc {startLine, endLine, startCol, endCol, startByte, endByte} is the "
            "AST node's own loc, on every sequence entry and on loop and branch enclosures (the node named by "
            "nodeId). Lines are 1-based; columns and bytes are 0-based; every end is exclusive. An echo closed by "
            "'?>' ends after that tag, as its AST node does.")
COND_EXPR_NOTE = ("Member 01's convention (Q2): condExpr on then/elseif branch enclosures is the exact source of the "
                  "if/elseif `cond` node; condNodeId is that node's id.")
CALLS_NOTE = ("Member 01 Q4 (proposed): a call site is a sequence entry {kind: \"call\", callee, outputsInside}; no "
              "output is inlined. callee is the called function's node id, or null when the call is not resolved to "
              "one function of the app (a method call on an object, a dynamic call, a name that several files "
              "define and the file's literal includes do not narrow to one). outputsInside is true, false or "
              "\"unknown\" (the callee, or something it calls, cannot be followed). Calls of PHP's own functions have "
              "no entry. Harness extensions: `args` on a call entry (each argument as {kind: \"literal\", type, value} "
              "or {kind: \"expr\", expr}), `function` on a function's timeline ({nodeId, name, params: [{name, default, readOnly}]}), `callNames` "
              "(call id -> the name as written) and `includes` (every include/require, with node id, line and "
              "enclosedBy).")
RESOLUTION_NOTE = ("boundary-rules v0.11 section 4: a name defined in several files resolves only through the calling "
                   "file's own literal includes, followed transitively; exactly one reachable definition -> that "
                   "function, otherwise callee null.")
SELECT_STAR_NOTE = ("Member 01 Q1 (proposed): a single-table SELECT * is resolved only when the app ships a schema "
                    "(.sql with CREATE TABLE) that declares the table; such reads carry basis \"schema-expanded\". "
                    "Otherwise it is ambiguous.")
QUERIES_NOTE = ("Agent extension: `queries` holds the SQL of every query a read was traced to; "
                "`valuesInterpolated: true` marks a query whose SQL has interpolated values (see sqlValuesExtension).")
PARTS_NOTE = ("Harness extension, pending Member 01 Q16: `parts` on an echo/print entry lists what it prints in "
              "source order, taken from the AST: {kind: \"literal\", value, nodeId} (value is the PHP-decoded text) "
              "or {kind: \"expr\", expr, nodeId, read} (read is the index of this part's read in `reads`). nodeId "
              "is the AST node of the part and loc is that node's loc. An echo that cannot be split exactly has no "
              "parts.")
COPY_NOTE = ("Harness extension, pending Member 01 Q17: a read of $v right after `$v = $row['k'];` (same block, no "
             "branch, no assignment to $v or $row and no call taking either as an argument in between) is "
             "`computed` with derivedFrom = the read of $row['k'].")
SQL_VALUES_NOTE = ("Harness extension, pending Member 01 Q17: a query whose SQL interpolates values only (inside a "
                   "quoted value, or a whole value after a comparison operator) is resolved from its fixed text and "
                   "marked valuesInterpolated. This assumes the query's structure is its template: a value that "
                   "carries SQL (injection) could change the select list or the table at run time.")
COMPARISONS = re.compile(r"(?:<>|!=|<=|>=|=|<|>)\s*$")
NOTES_NOTE = ("Harness extension: `harnessNotes` lists output the schema cannot express or that the harness left "
              "unresolved on purpose (for docs/php-analysis/pattern-log.md).")


class _N:
    """One AST node, with its roles resolved to nodes (a view over src.model.AstNode)."""

    __slots__ = ("id", "kind", "loc", "attrs", "fields", "kids")

    def __init__(self, node):
        if node.loc is None:
            raise ValueError(f"{node.id}: the timeline builder needs loc on every AST node")
        self.id, self.kind, self.loc, self.attrs = node.id, node.kind, node.loc, node.attrs
        self.fields, self.kids = {}, []

    def get(self, name: str, default=None):
        """A role (a node, a list of nodes) or an attribute; `default` when the node has neither."""
        if name in self.fields:
            return self.fields[name]
        return self.attrs.get(name, default)

    @property
    def start(self) -> int:
        return self.loc.start_byte

    @property
    def end(self) -> int:
        return self.loc.end_byte

    @property
    def line(self) -> int:
        return self.loc.start_line


def node_views(ast) -> dict:
    """id -> _N for every node of an AST (src.model.Ast)."""
    views = {node_id: _N(node) for node_id, node in ast.nodes.items()}
    for node_id, node in ast.nodes.items():
        view = views[node_id]
        view.kids = [views[child] for child in node.children]
        for role, value in node.fields.items():
            if isinstance(value, tuple):
                view.fields[role] = [views[ref] if ref is not None else None for ref in value]
            else:
                view.fields[role] = views[value] if value is not None else None
    return views


def loc_dict(loc) -> dict:
    return {"startLine": loc.start_line, "endLine": loc.end_line, "startCol": loc.start_col, "endCol": loc.end_col,
            "startByte": loc.start_byte, "endByte": loc.end_byte}


@dataclass
class _Def:
    """One place a variable gets a value."""

    pos: int
    chain: tuple  # control path: ((node id, arm), ...), outermost first
    kind: str  # assign | foreach | other
    node: object = None  # assign: the right-hand side; foreach: the Stmt_Foreach


@dataclass
class _Scope:
    name: str | None  # None: the file's top level
    line: int = 1
    node_id: str | None = None  # the function's AST node; None for the top level
    params: list = field(default_factory=list)  # {name, default}: default is a literal, an expr, or None
    outputs: list = field(default_factory=list)  # (node, kind, chain, enclosures)
    defs: dict = field(default_factory=dict)  # "$name" -> [_Def]
    breakers: list = field(default_factory=list)  # positions of include / extract / eval
    calls: list = field(default_factory=list)  # {node, name, plain, enclosures}
    controls: list = field(default_factory=list)  # start of every if / loop / switch / try
    arguments: dict = field(default_factory=dict)  # "$name" -> positions where it is passed to a call
    includes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _is(node, *kinds) -> bool:
    return isinstance(node, _N) and node.kind in kinds


def _name(node) -> str | None:
    """A Name / Identifier node as text."""
    if isinstance(node, _N) and (node.kind.startswith("Name") or node.kind in ("Identifier", "VarLikeIdentifier")):
        name = node.attrs.get("name")
        return name if isinstance(name, str) else None
    return None


def _variable(node) -> str | None:
    """"$name" for a plain variable; None for $$x, ${expr} and everything else."""
    if _is(node, "Expr_Variable") and isinstance(node.attrs.get("name"), str):
        return "$" + node.attrs["name"]
    return None


def _callee(node) -> str | None:
    """The called function's name in lower case, for a call by plain name."""
    if _is(node, "Expr_FuncCall"):
        name = _name(node.get("name"))
        return name.lower().lstrip("\\") if name else None
    return None


def _args(node) -> list:
    return [a.get("value") for a in node.get("args") or [] if _is(a, "Arg")]


def weakest(values) -> bool | str:
    """What is known about several outputs taken together: true if any prints, else unknown if any is."""
    return max(values, key=OUTPUTS_INSIDE.index, default=False)


class FileHarness:
    """The timelines of one PHP file. `ast` is its AST as the loader gives it (src.model.Ast).

    `schema` maps a table name to its columns, for the app's own schema files; None or a missing table
    means SELECT * on it cannot be expanded.
    """

    def __init__(self, source: bytes, ast, entrypoint: str, schema: dict | None = None):
        self.source, self.ast, self.file_id, self.entrypoint = source, ast, ast.file_id, entrypoint
        self.schema = schema or {}
        try:
            source.decode("utf-8")
            self.encoding = "utf-8"
        except UnicodeDecodeError:
            self.encoding = "cp1252"  # legacy files: decoded for JSON; byte offsets are unaffected
        self.top = _Scope(None)
        self.functions: dict[str, _Scope] = {}  # name as written -> scope (first definition wins)
        self.errors = list(ast.parse_errors)
        self._class: str | None = None  # the class whose methods are being walked
        self._loops: dict[str, tuple] = {}  # loop id -> (enclosure, node, scope, chain around it)
        self.nodes = node_views(ast)
        self._walk_list(self.nodes[ast.root_id].get("stmts"), self.top, (), [])

    # ---- source text

    def text(self, start: int, end: int) -> str:
        """Source bytes start..end-1 as text."""
        return self.source[start:end].decode(self.encoding, errors="replace")

    def node_text(self, node: _N) -> str:
        return self.text(node.start, node.end)

    def quoted(self, node: _N) -> bool:
        """A '...' or "..." string (not a heredoc or nowdoc)."""
        return self.source[node.start:node.start + 1] in (b"'", b'"')

    def argument(self, node: _N) -> dict:
        """A call argument or a parameter default: a literal with its value, or an expression as written."""
        if _is(node, "Scalar_String") and self.quoted(node):
            return {"kind": "literal", "type": "string", "value": node.get("value")}
        if _is(node, "Scalar_Int", "Scalar_Float"):
            return {"kind": "literal", "type": "number", "value": node.get("value")}
        return {"kind": "expr", "expr": self.node_text(node)}

    def header_text(self, node: _N) -> str:
        """A C-style for's header expressions as written: from the first one to the last one."""
        parts = [e for role in ("init", "cond", "loop") for e in node.get(role) or [] if e is not None]
        return self.text(min(e.start for e in parts), max(e.end for e in parts)) if parts else ""

    # ---- the walk: outputs, definitions, calls and includes, with the control path of each

    def _walk_list(self, nodes, scope: _Scope, chain: tuple, enclosures: list) -> None:
        for node in nodes or []:
            self._walk(node, scope, chain, enclosures)

    def _walk(self, node, scope: _Scope, chain: tuple, enclosures: list) -> None:
        if isinstance(node, list):
            self._walk_list(node, scope, chain, enclosures)
            return
        if not isinstance(node, _N):
            return
        kind = node.kind

        if kind in ("Stmt_If", "Stmt_Switch", "Stmt_TryCatch") or kind in LOOP_NODES:
            scope.controls.append(node.start)
        if kind in ("Stmt_InlineHTML", "Stmt_Echo", "Expr_Print"):
            scope.outputs.append((node, kind, chain, list(enclosures)))
            self._walk(node.get("exprs") or node.get("expr"), scope, chain, enclosures)
        elif kind == "Stmt_If":
            self._if(node, scope, chain, enclosures)
        elif kind in LOOP_NODES:
            self._loop(node, scope, chain, enclosures)
        elif kind == "Stmt_Switch":
            self._walk(node.get("cond"), scope, chain, enclosures)
            switch_id = node.id
            for case in node.get("cases") or []:
                case_id = case.id
                enclosure = {"nodeId": switch_id, "kind": "Stmt_Switch", "role": "switch_case", "caseNodeId": case_id,
                             "caseExpr": self.node_text(case.get("cond")) if case.get("cond") else None}
                self._walk(case.get("cond"), scope, chain, enclosures)
                self._walk_list(case.get("stmts"), scope, chain + ((switch_id, case_id),), enclosures + [enclosure])
        elif kind == "Stmt_TryCatch":
            try_id = node.id
            parts = [("try", node.get("stmts"))]
            parts += [("catch", c.get("stmts")) for c in node.get("catches") or []]
            if node.get("finally"):
                parts.append(("finally", node.get("finally").get("stmts")))
            for index, (part, stmts) in enumerate(parts):
                enclosure = {"nodeId": try_id, "kind": "Stmt_TryCatch", "role": "try_catch", "part": part}
                self._walk_list(stmts, scope, chain + ((try_id, f"{part}{index}"),), enclosures + [enclosure])
            for catch in node.get("catches") or []:
                self._define(scope, _variable(catch.get("var")), catch, chain, "other")
        elif kind in ("Stmt_Function", "Stmt_ClassMethod", "Expr_Closure", "Expr_ArrowFunction"):
            self._function(node, kind, scope)
        elif kind in ("Stmt_Class", "Stmt_Trait", "Stmt_Interface", "Stmt_Enum"):
            previous, self._class = self._class, _name(node.get("name")) or "class"
            self._walk_list(node.get("stmts"), scope, chain, enclosures)
            self._class = previous
        else:
            self._expression(node, kind, scope, chain, enclosures)
            self._walk_list(node.kids, scope, chain, enclosures)

    def _expression(self, node: _N, kind: str, scope: _Scope, chain: tuple, enclosures: list) -> None:
        """Record what a non-control node means for the scope: definitions, calls, includes, notes."""
        pos, line, col = node.start, node.line, node.loc.column
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
            for item in node.get("vars") or []:
                self._define_all(scope, item.get("var") if _is(item, "StaticVar") else item, node, chain)
        elif kind == "Expr_Include":
            scope.breakers.append(pos)
            expr = node.get("expr")
            scope.includes.append({
                "nodeId": node.id, "line": line, "col": col,
                "kind": INCLUDE_KINDS.get(node.get("type"), "include"), "expr": self.node_text(expr),
                "literal": expr.get("value") if _is(expr, "Scalar_String") else None,
                "enclosedBy": list(enclosures)})
        elif kind == "Expr_Eval":
            scope.breakers.append(pos)
        elif kind == "Expr_Exit" and node.get("expr") and not _is(node.get("expr"), "Scalar_Int"):
            scope.notes.append({"kind": "exit_with_output", "line": line, "col": col,
                                "detail": self.node_text(node)[:80]})
        elif kind in CALL_NODES:
            callee = _callee(node)  # None: a dynamic call or a method call on an object
            if kind == "Expr_StaticCall":
                cls, method = _name(node.get("class")), _name(node.get("name"))
                if cls and cls.lower() in ("self", "static") and self._class:
                    cls = self._class
                callee = f"{cls}::{method}".lower() if cls and method else None
            if callee in SCOPE_BREAKERS:
                scope.breakers.append(pos)
            if callee in OUTPUT_FUNCTIONS:
                scope.notes.append({"kind": "output_function", "line": line, "col": col, "detail": callee})
            if callee == "ob_start":
                scope.notes.append({"kind": "output_buffering", "line": line, "col": col,
                                    "detail": "ob_start()"})
            for argument in _args(node):  # a call may change a variable it is given (by reference)
                if _variable(argument) and callee not in ESCAPE_WRAPPERS:  # (those take their value by copy)
                    scope.arguments.setdefault(_variable(argument), []).append(argument.start)
            # plain: a call by plain function name, which is PHP's own function when the app does not define it
            scope.calls.append({"node": node, "name": callee, "enclosures": list(enclosures),
                                "plain": kind == "Expr_FuncCall" and callee is not None
                                and callee not in DYNAMIC_CALLERS,
                                "args": [self.argument(a) for a in _args(node)]})

    def _define(self, scope: _Scope, name: str | None, node: _N, chain: tuple, kind: str, rhs=None) -> None:
        if name:
            scope.defs.setdefault(name, []).append(_Def(node.start, chain, kind, rhs if kind == "assign" else node))

    def _define_all(self, scope: _Scope, target, node: _N, chain: tuple) -> None:
        """Every plain variable written through `target` (a list(), an array element, a property) is redefined."""
        if isinstance(target, list):
            for item in target:
                self._define_all(scope, item, node, chain)
        elif isinstance(target, _N):
            if _variable(target):
                self._define(scope, _variable(target), node, chain, "other")
            for key in ("var", "items", "value"):
                if key in target.fields:
                    self._define_all(scope, target.fields[key], node, chain)

    def _if(self, node: _N, scope: _Scope, chain: tuple, enclosures: list) -> None:
        if_id, cond = node.id, node.get("cond")
        self._walk(cond, scope, chain, enclosures)
        arms = [("then", cond, node.get("stmts"))]
        for elseif in node.get("elseifs") or []:
            arms.append(("elseif", elseif.get("cond"), elseif.get("stmts")))
        if node.get("else"):
            arms.append(("else", cond, node.get("else").get("stmts")))  # an else points at its if's condition
        for index, (branch, branch_cond, stmts) in enumerate(arms):
            if branch == "elseif":
                self._walk(branch_cond, scope, chain, enclosures)
            enclosure = {"nodeId": if_id, "kind": "Stmt_If", "role": "branch", "branch": branch,
                         "condNodeId": branch_cond.id}
            if branch != "else":
                enclosure["condExpr"] = self.node_text(branch_cond)
            enclosure["loc"] = loc_dict(node.loc)
            self._walk_list(stmts, scope, chain + ((if_id, f"{branch}{index}"),), enclosures + [enclosure])

    def _loop(self, node: _N, scope: _Scope, chain: tuple, enclosures: list) -> None:
        kind, loop_id = node.kind, node.id
        inner = chain + ((loop_id, "loop"),)
        enclosure = {"nodeId": loop_id, "kind": kind, "role": "iteration", "iterExpr": "",
                     "iterSourceKind": "unresolved", "valueVar": None, "keyVar": None, "loc": loc_dict(node.loc)}
        if kind in ("Stmt_While", "Stmt_Do"):
            cond = node.get("cond")
            enclosure["iterExpr"] = self.node_text(cond)
            if _is(cond, "Expr_Assign") and _variable(cond.get("var")):
                enclosure["valueVar"] = self.node_text(cond.get("var"))
                if _callee(cond.get("expr")) in FETCH_ROW:
                    enclosure["iterSourceKind"] = "db_row_field"
            self._walk(cond, scope, inner, enclosures)  # the header runs once per iteration
        elif kind == "Stmt_Foreach":
            enclosure["iterExpr"] = self.node_text(node.get("expr"))
            enclosure["valueVar"] = self.node_text(node.get("valueVar"))
            enclosure["keyVar"] = self.node_text(node.get("keyVar")) if node.get("keyVar") else None
            self._walk(node.get("expr"), scope, chain, enclosures)
            self._define_all(scope, node.get("keyVar"), node, inner)
            if _variable(node.get("valueVar")):
                self._define(scope, _variable(node.get("valueVar")), node, inner, "foreach")
            else:
                self._define_all(scope, node.get("valueVar"), node, inner)
        else:  # Stmt_For: which fields a C-style for fills is open (Member 01 Q10), so the variables stay null
            enclosure["iterExpr"] = self.header_text(node)
            for part in ("init", "cond", "loop"):
                self._walk(node.get(part), scope, inner, enclosures)
        self._loops[loop_id] = (enclosure, node, scope, chain)
        self._walk_list(node.get("stmts"), scope, inner, enclosures + [enclosure])

    def _function(self, node: _N, kind: str, outer: _Scope) -> None:
        if kind == "Stmt_Function":
            name = _name(node.get("name"))
        elif kind == "Stmt_ClassMethod":
            name = f"{self._class or 'class'}::{_name(node.get('name'))}"
        else:
            name = f"closure@{node.line}"
        scope = _Scope(name, node.line, node.id)
        for param in node.get("params") or []:
            self._define(scope, _variable(param.get("var")), param, (), "other")
            default = param.get("default")
            scope.params.append({"name": _variable(param.get("var")),
                                 "default": self.argument(default) if isinstance(default, _N) else None})
        for use in node.get("uses") or []:
            self._define(scope, _variable(use.get("var")), use, (), "other")
        body = node.get("stmts") if kind != "Expr_ArrowFunction" else [node.get("expr")]
        self._walk_list(body, scope, (), [])
        for param in scope.params:  # read-only: never assigned, never handed to a call, nothing that could set it
            param["readOnly"] = bool(param["name"]) and len(scope.defs.get(param["name"], [])) == 1 \
                and not scope.breakers and not scope.arguments.get(param["name"])
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
        """(sql text, source text, values interpolated) for a SQL argument, directly or through a variable.

        A string literal is its own text. A string with interpolated values (Q17) is its fixed text with a
        placeholder for each value. Anything else is None."""
        name = _variable(node)
        if name:
            reaching = self._reaching(scope, name, pos, chain)
            if reaching is None or reaching.kind != "assign":
                return None
            node = reaching.node
        if _is(node, "Scalar_String") and self.quoted(node):
            return node.get("value"), self.node_text(node), False
        template = self._sql_template(node)
        return (template, self.node_text(node), True) if template is not None else None

    def _sql_template(self, node) -> str | None:
        """The SQL of an interpolated or concatenated string, with a placeholder for every interpolated value.

        None unless every interpolation is a value: inside a quoted SQL string, or a whole value right after a
        comparison operator. A table name, a column list, ORDER BY, LIMIT, IN (...) or a whole clause is not."""
        pieces = self._pieces(node) if _is(node, "Scalar_InterpolatedString", "Expr_BinaryOp_Concat") else None
        if pieces is None or not any(piece[0] == "expr" for piece in pieces):
            return None
        text, quote = "", None
        for index, piece in enumerate(pieces):
            if piece[0] == "literal":
                skip = False
                for ch in piece[2]:
                    if skip:
                        skip = False
                    elif quote and ch == "\\":
                        skip = True
                    elif quote:
                        quote = None if ch == quote else quote
                    elif ch in "'\"`":
                        quote = ch
                text += piece[2]
            elif quote in ("'", '"'):
                text += "?"  # part of a quoted value
            elif quote is None and COMPARISONS.search(text):
                after = pieces[index + 1] if index + 1 < len(pieces) else None
                if after is not None and (after[0] != "literal" or (after[2][:1] and after[2][:1] not in " \t\r\n);")):
                    return None  # the value runs on into something else
                text += "0"
            else:
                return None
        return text if quote is None else None

    def _query_of(self, scope: _Scope, fetch: _N, pos: int, chain: tuple):
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
                return source, source.start, outer
            rows = _variable(source)
            if rows:
                loop_pos = reaching.node.start
                filled = self._reaching(scope, rows, loop_pos, outer)
                if filled and filled.kind == "assign" and _callee(filled.node) in FETCH_ALL and all(
                        d.kind == "assign" and _callee(d.node) in FETCH_ALL for d in scope.defs.get(rows, [])):
                    return filled.node, filled.pos, filled.chain
        return None

    # ---- reads

    def _var_read(self, scope: _Scope, node: _N, chain: tuple, queries: dict, notes: list) -> dict | None:
        """A read of $name or $name['k']...: None when `node` is not that shape."""
        path, base = [], node
        while _is(base, "Expr_ArrayDimFetch"):
            dim = base.get("dim")
            if _is(dim, "Scalar_String"):
                path.insert(0, dim.get("value"))
            elif _is(dim, "Scalar_Int"):
                path.insert(0, str(dim.get("value")))
            elif _is(dim, "Expr_ConstFetch") and _name(dim.get("name")):
                path.insert(0, _name(dim.get("name")))
            else:
                return None
            base = base.get("var")
        name = _variable(base)
        if name is None:
            return None
        read = {"expr": self.node_text(node), "var": name, "path": path}
        pos = node.start
        if name in SUPERGLOBALS:
            return read | {"sourceKind": SUPERGLOBALS[name], "confidence": "resolved", "source": None}
        unresolved = read | {"sourceKind": "unresolved", "confidence": "unresolved", "source": None}
        if not path:
            copied = self._copy_read(scope, node, name, chain, queries, notes)
            return copied if copied is not None else unresolved
        fetched = self._row_fetch(scope, name, pos, chain)
        if fetched is None:
            return unresolved
        fetch, fetch_pos, fetch_chain = fetched
        line, col = node.line, node.loc.column
        found = self._query_of(scope, fetch, fetch_pos, fetch_chain)
        if found is None:
            notes.append({"kind": "row_read_not_traced", "line": line, "col": col,
                          "detail": "the fetch's result is not certainly one query call"})
            return unresolved
        query, result_var = found
        query_pos = query.start
        query_chain = next((d.chain for d in scope.defs.get(result_var, []) if d.node is query), fetch_chain)
        arguments = _args(query)
        index = QUERY_FUNCTIONS[_callee(query)]
        sql = self._sql(scope, arguments[index], query_pos, query_chain) if len(arguments) > index else None
        if sql is None:  # built SQL, or an interpolation that is not a value: never guessed
            notes.append({"kind": "row_read_not_traced", "line": line, "col": col,
                          "detail": "the SQL is not a string literal, and not one with interpolated values only"})
            return unresolved
        sql_value, sql_source, interpolated = sql
        shape = parse_select(sql_value)
        columns, basis = shape.columns, None
        if shape.table is not None and columns is None:  # SELECT * on one table (Member 01 Q1)
            columns = self.schema.get(shape.table)
            basis = SCHEMA_BASIS if columns is not None else None
        fetch_id, query_id = fetch.id, query.id
        queries[query_id] = {"line": query.line, "sql": sql_source,
                             "tables": [shape.table] if shape.table else [],
                             "columns": list(columns) if columns is not None else None, "resultVar": result_var}
        if interpolated:
            queries[query_id]["valuesInterpolated"] = True
            notes.append({"kind": "sql_interpolated_values", "line": query.line, "col": query.loc.column,
                          "detail": " ".join(sql_source.split())[:80]})
        source = {"fetchNodeId": fetch_id, "queryNodeId": query_id, "table": None, "column": None}
        ambiguous = read | {"sourceKind": "db_row_field", "confidence": "ambiguous", "source": source}
        if shape.table is None or columns is None or len(path) != 1:
            return ambiguous  # a join, anything not plain, or SELECT * with no schema to expand it
        column = path[0]
        if _is(node.get("dim"), "Scalar_Int"):  # a numeric index on a row: a column, but not which one
            return ambiguous
        if column not in columns:
            return unresolved  # not in the select list
        resolved = read | {"sourceKind": "db_row_field", "confidence": "resolved",
                           "source": source | {"table": shape.table, "column": column}}
        return resolved | {"basis": basis} if basis else resolved

    def _copy_read(self, scope: _Scope, node: _N, name: str, chain: tuple, queries: dict, notes: list):
        """Copy propagation (Q17): the read of `$v` straight after `$v = $row['k'];`, or None.

        Only in the same block (the same control path), and only when nothing between the copy and the use
        could change either variable: no if / loop / switch / try, no assignment to `$v` or `$row`, no include,
        and no call that is given `$v` or `$row`."""
        pos = node.start
        copy = self._reaching(scope, name, pos, chain)
        if copy is None or copy.kind != "assign" or copy.chain != chain or not _is(copy.node, "Expr_ArrayDimFetch"):
            return None
        base = copy.node
        while _is(base, "Expr_ArrayDimFetch"):
            base = base.get("var")
        row = _variable(base)
        if row is None or row in SUPERGLOBALS:  # $v = $_SESSION['k'] is Member 01 Q3, still open
            return None
        between = [p for p in scope.controls if copy.pos < p < pos]
        between += [d.pos for d in scope.defs.get(row, []) if copy.pos < d.pos < pos]
        between += [p for v in (name, row) for p in scope.arguments.get(v, []) if copy.pos < p < pos]
        if between:
            return None
        inner = self._var_read(scope, copy.node, copy.chain, queries, notes)
        if inner is None or inner["sourceKind"] != "db_row_field":
            return None
        return {"expr": self.node_text(node), "var": None, "path": None, "sourceKind": "computed",
                "confidence": inner["confidence"], "source": None, "derivedFrom": [inner]}

    def _opaque(self, node: _N) -> dict:
        text = self.node_text(node)
        return {"expr": text, "var": text, "path": [], "sourceKind": "unresolved", "confidence": "unresolved",
                "source": None}

    def _reads(self, scope: _Scope, node: _N, chain: tuple, queries: dict, notes: list) -> list[dict]:
        """The reads an output expression prints, in order (one per piece of a concatenation)."""
        if _is(node, "Expr_BinaryOp_Concat"):
            return (self._reads(scope, node.get("left"), chain, queries, notes)
                    + self._reads(scope, node.get("right"), chain, queries, notes))
        line = node.line
        if _is(node, "Scalar_String") and self.quoted(node) or _is(node, "Scalar_Int", "Scalar_Float"):
            return [{"expr": self.node_text(node), "var": None, "path": None, "sourceKind": "literal",
                     "confidence": "resolved", "source": None}]
        if _is(node, "Scalar_String", "Scalar_InterpolatedString"):
            text = self.node_text(node)
            heredoc = self.source[node.start:node.start + 3] == b"<<<"
            kind = "heredoc_output" if heredoc else "interpolated_string_output"
            notes.append({"kind": kind, "line": line, "col": node.loc.column, "markup": "<" in text,
                          "detail": text[:80]})
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

    def _pieces(self, node) -> list | None:
        """What one echo argument prints, in order: ("literal", node, text) or ("expr", node).

        None when a piece cannot be split exactly (a float prints by PHP's own rules)."""
        if _is(node, "Expr_BinaryOp_Concat"):
            left, right = self._pieces(node.get("left")), self._pieces(node.get("right"))
            return None if left is None or right is None else left + right
        if _is(node, "Scalar_String"):  # '...', "..." without variables, or a heredoc / nowdoc without them
            return [("literal", node, node.get("value"))] if isinstance(node.get("value"), str) else None
        if _is(node, "Scalar_Int"):
            return [("literal", node, str(node.get("value")))]
        if _is(node, "Scalar_Float"):
            return None
        if _is(node, "Scalar_InterpolatedString"):
            pieces = []
            for part in node.get("parts") or []:
                if _is(part, "InterpolatedStringPart") and isinstance(part.get("value"), str):
                    pieces.append(("literal", part, part.get("value")))
                elif isinstance(part, _N) and not _is(part, "InterpolatedStringPart"):
                    pieces.append(("expr", part))
                else:
                    return None
            return pieces
        return [("expr", node)] if isinstance(node, _N) else None

    def _echo(self, scope: _Scope, node: _N, exprs: list, chain: tuple, queries: dict, notes: list):
        """(reads, parts or None) of an echo / print. Without parts the reads are as before: one per piece of
        a concatenation, and a string with variables in it is one unresolved read."""
        pieces = []
        for expr in exprs:
            found = self._pieces(expr)
            if found is None:
                pieces = None
                break
            pieces += found
        if pieces is None or [piece[0] for piece in pieces] == ["expr"]:  # nothing to split, or not exactly
            return [read for expr in exprs for read in self._reads(scope, expr, chain, queries, notes)], None
        reads, parts = [], []
        for piece in pieces:
            if piece[0] == "literal":
                parts.append({"kind": "literal", "value": piece[2], "nodeId": piece[1].id,
                              "loc": loc_dict(piece[1].loc)})
                if not _is(piece[1], "InterpolatedStringPart"):  # a literal of its own in the source: read as before
                    reads.append({"expr": self.node_text(piece[1]), "var": None, "path": None,
                                  "sourceKind": "literal", "confidence": "resolved", "source": None})
            else:
                (read,) = self._reads(scope, piece[1], chain, queries, notes)
                parts.append({"kind": "expr", "expr": self.node_text(piece[1]), "nodeId": piece[1].id,
                              "read": len(reads), "loc": loc_dict(piece[1].loc)})
                reads.append(read)
        forms = [form for form, found in (
            ("heredoc", any(self.source[e.start:e.start + 3] == b"<<<" for e in exprs)),
            ("interpolated_string", any(_is(p[1], "InterpolatedStringPart") for p in pieces)),
            ("concatenation", any(_is(e, "Expr_BinaryOp_Concat") for e in exprs)),
            ("several_arguments", len(exprs) > 1)) if found]
        if forms and any(part["kind"] == "expr" for part in parts):
            notes.append({"kind": "echo_parts", "line": node.line, "col": node.loc.column, "form": forms[0],
                          "markup": any(p["kind"] == "literal" and "<" in p["value"] for p in parts),
                          "detail": self.text(node.start, min(node.end, node.start + 80))})
        return reads, parts

    # ---- timelines

    def scopes(self) -> list[_Scope]:
        return [self.top] + [self.functions[name] for name in sorted(self.functions)]

    def loop_enclosure(self, enclosure: dict) -> dict:
        """iterSourceKind for a foreach over fetched rows needs the definitions, which are complete only now."""
        entry = self._loops.get(enclosure["nodeId"])
        if entry is None or entry[1].kind != "Stmt_Foreach":
            return enclosure
        _, node, scope, chain = entry
        source = node.get("expr")
        if _callee(source) in FETCH_ALL:
            enclosure["iterSourceKind"] = "db_row_field"
        elif _variable(source):
            filled = self._reaching(scope, _variable(source), node.start, chain)
            if filled and filled.kind == "assign" and _callee(filled.node) in FETCH_ALL:
                enclosure["iterSourceKind"] = "db_row_field"
        return enclosure

    def direct_output(self, scope: _Scope) -> bool | str:
        """Whether a scope prints by itself: true, or "unknown" when it includes a file, else false."""
        if scope.outputs or any(n["kind"] == "output_function" for n in scope.notes):
            return True
        return "unknown" if scope.includes else False

    def timeline(self, scope: _Scope, provenance: dict, resolve=None) -> dict | None:
        """The schema 1.0 document of one scope, or None when it prints nothing itself.

        `resolve(name)` gives (callee node id or None, outputsInside) for a function of the app and None for
        any other name; without it, only this file's own functions are known."""
        if not scope.outputs:
            return None
        if resolve is None:
            resolve = function_resolver({self.file_id: self})
        own_resolve, resolve = resolve, lambda name: own_resolve(name, self)
        queries, notes, sequence, call_names = {}, list(scope.notes), [], {}
        for node, kind, chain, enclosures in scope.outputs:
            entry = {"id": node.id, "kind": kind}
            if kind == "Stmt_InlineHTML":
                entry["raw"] = self.node_text(node)
            else:
                exprs = node.get("exprs") if kind == "Stmt_Echo" else [node.get("expr")]
                entry["reads"], parts = self._echo(scope, node, exprs, chain, queries, notes)
                if parts is not None:
                    entry["parts"] = parts
                elif len(entry["reads"]) > 1 and any(
                        r["sourceKind"] == "literal" and "<" in r["expr"] for r in entry["reads"]):
                    notes.append({"kind": "markup_in_concatenated_echo", "line": node.line,
                                  "col": node.loc.column, "markup": True,
                                  "detail": self.text(node.start, min(node.end, node.start + 80))})
            entry["enclosedBy"] = [self.loop_enclosure(e) if e["role"] == "iteration" else e for e in enclosures]
            entry["loc"] = loc_dict(node.loc)
            sequence.append((node.start, entry))
        for call in scope.calls:
            found = resolve(call["name"]) if call["name"] else None
            if found is None and call["plain"]:
                continue  # PHP's own function (or one the app does not define): not a call site of the app
            callee, outputs_inside = found if found is not None else (None, "unknown")
            node = call["node"]
            sequence.append((node.start, {
                "id": node.id, "kind": "call", "callee": callee, "outputsInside": outputs_inside,
                "enclosedBy": [self.loop_enclosure(e) if e["role"] == "iteration" else e for e in call["enclosures"]],
                "loc": loc_dict(node.loc), "args": call["args"]}))
            if call["name"]:
                call_names[node.id] = call["name"]
        sequence.sort(key=lambda item: (item[0], item[1]["id"]))
        entrypoint = self.entrypoint if scope.name is None else f"{self.entrypoint}#{scope.name}"
        notes = [dict(items) for items in dict.fromkeys(tuple(n.items()) for n in notes)]  # a read made twice
        notes.sort(key=lambda n: (n["line"], n["col"], n["kind"], n["detail"]))
        function = {} if scope.node_id is None else {
            "function": {"nodeId": scope.node_id, "name": scope.name, "params": scope.params}}
        return {
            "entrypoint": entrypoint,
            "schemaVersion": SCHEMA_VERSION,
            **function,
            "sequence": [entry for _, entry in sequence],
            "queries": dict(sorted(queries.items())),
            "callNames": dict(sorted(call_names.items())),
            "includes": sorted(scope.includes, key=lambda i: i["nodeId"]),
            "harnessNotes": notes,
            "_provenance": provenance | {
                "function": scope.name,
                "functionNodeId": scope.node_id,
                "sourceEncoding": self.encoding,
                "locConvention": LOC_NOTE, "condExprConvention": COND_EXPR_NOTE, "callsConvention": CALLS_NOTE,
                "callResolution": RESOLUTION_NOTE,
                "selectStarConvention": SELECT_STAR_NOTE,
                "partsExtension": PARTS_NOTE, "copyPropagationExtension": COPY_NOTE,
                "sqlValuesExtension": SQL_VALUES_NOTE,
                "queriesExtension": QUERIES_NOTE, "harnessNotesExtension": NOTES_NOTE,
            },
        }


def function_resolver(harnesses: dict):
    """resolve(name, harness) for a set of files that form one app, keyed by their path in the app.

    It gives (callee node id or None, outputsInside) for a name the app defines, and None for any other name.
    A name with one definition is that function. A name that several files define resolves only through the
    calling file's own literal includes, followed transitively: exactly one reachable definition -> that
    function; otherwise there is no single callee (id None, and its output is what all definitions agree on).

    A function prints if it prints itself or calls a function that prints. It is "unknown" when it, or something
    it calls, includes a file or makes a call that cannot be followed.
    """
    definitions: dict[str, list] = {}  # lower-case name -> [(file key, harness, scope)]
    for key in sorted(harnesses):
        for name, scope in harnesses[key].functions.items():
            definitions.setdefault(name.lower(), []).append((key, harnesses[key], scope))
    key_of = {id(harness): key for key, harness in harnesses.items()}
    state = {id(scope): harness.direct_output(scope) for found in definitions.values() for _, harness, scope in found}
    reach: dict[str, set] = {}

    def reachable(key: str) -> set:
        """The file itself and every app file its top-level literal includes lead to."""
        if key not in reach:
            seen, todo = {key}, [key]
            while todo:
                current = todo.pop()
                for include in harnesses[current].top.includes:
                    if include["literal"] is None:
                        continue
                    for target in (posixpath.normpath(posixpath.join(posixpath.dirname(current), include["literal"])),
                                   posixpath.normpath(include["literal"])):
                        if target in harnesses and target not in seen:
                            seen.add(target)
                            todo.append(target)
                            break
            reach[key] = seen
        return reach[key]

    def targets(name: str, key: str) -> list:
        """The definitions a call of `name` written in file `key` may reach: one when it is resolved."""
        found = definitions.get(name.lower(), [])
        if len(found) > 1:
            narrowed = [d for d in found if d[0] in reachable(key)]
            if len(narrowed) == 1:
                return narrowed
        return found

    def together(found: list) -> bool | str:
        values = {state[id(scope)] for _, _, scope in found}
        return values.pop() if len(values) == 1 else "unknown"

    def called(call: dict, key: str) -> bool | str:
        found = targets(call["name"], key) if call["name"] else []
        if found:
            return together(found)
        return False if call["plain"] else "unknown"

    changed = True
    while changed:
        changed = False
        for found in definitions.values():
            for key, _, scope in found:
                value = weakest([state[id(scope)]] + [called(call, key) for call in scope.calls])
                if value != state[id(scope)]:
                    state[id(scope)] = value
                    changed = True

    def resolve(name: str, harness):
        found = targets(name, key_of[id(harness)])
        if not found:
            return None
        return (found[0][2].node_id if len(found) == 1 else None), together(found)

    return resolve


def parse_schema(sql: str) -> dict:
    """{table: [columns]} for every CREATE TABLE of a schema file, as far as it is plain to read."""
    tables = {}
    for found in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?(\w+)[`\"]?\s*\(", sql, re.I):
        items, depth, start, quote, index = [], 1, found.end(), None, found.end()
        while index < len(sql) and depth:
            ch = sql[index]
            if quote:
                if ch == "\\":
                    index += 1
                elif ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    items.append(sql[start:index])
            elif ch == "," and depth == 1:
                items.append(sql[start:index])
                start = index + 1
            index += 1
        if depth:
            continue  # unbalanced: not plain
        columns = []
        for item in items:
            column = re.match(r"\s*(?:`([^`]+)`|(\w+))\s+\w", item)
            if column and (column.group(1) or column.group(2).upper() not in (
                    "PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX", "FOREIGN", "FULLTEXT", "SPATIAL", "CHECK")):
                columns.append(column.group(1) or column.group(2))
        tables.setdefault(found.group(1), []).append(columns)
    # a table declared twice with different columns is not certain: left out
    return {table: versions[0] for table, versions in tables.items() if all(v == versions[0] for v in versions)}


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
