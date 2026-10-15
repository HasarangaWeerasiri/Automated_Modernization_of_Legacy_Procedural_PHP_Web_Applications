"""Component 2 whole-app test harness, timeline builder: an AST -> output timelines (schema 1.0).

A **test harness, not Member 01's core** (docs/c2-roadmap.md Phases 1 and 1b). It lets Stages 1-5 run
on whole procedural PHP apps before the real static analysis exists. It reads an AST in Member 01's
format through src/mapping/loader.py - the harness's own (tools/harness/parse.php) or Member 01's -
and uses Member 01's conventions: node ids are the AST's pre-order ids, `loc` is the AST node's loc
(1-based lines, 0-based columns and bytes, exclusive ends), and condExpr is the source of the `cond` node.

One PHP file gives one page timeline (its top-level output) and one timeline per function that
produces output ("<file>#<function>"). A function's output is never inlined: the call site is a
sequence entry {kind: "call", callee, outputsInside} (Member 01 Q4, proposed). An include / require is
a sequence entry too, at its position: {kind: "include", path, outputsInside} (harness extension,
pending Member 01 Q4); every include is also listed in the extension list `includes`.

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
HTML_SPACE = " \t\n\f\r"  # HTML5 whitespace (not str.strip()'s Unicode set)
OUTPUT_FUNCTIONS = ("printf", "vprintf", "print_r", "var_dump", "var_export", "readfile", "fpassthru", "passthru")
RETURNING_FUNCTIONS = ("print_r", "var_export")  # with a second argument `true` they return their text
_MARKUP_TEXT = re.compile(r"<[A-Za-z!/]|\b[a-z-]+=\"")  # a tag, or an attribute written out
SCOPE_BREAKERS = ("extract", "eval", "parse_str", "import_request_variables")  # may define any variable
DYNAMIC_CALLERS = ("call_user_func", "call_user_func_array")  # call a function chosen at run time
LOOP_NODES = ("Stmt_While", "Stmt_Do", "Stmt_For", "Stmt_Foreach")
CALL_NODES = ("Expr_FuncCall", "Expr_StaticCall", "Expr_MethodCall", "Expr_NullsafeMethodCall")
OR_NODES = ("Expr_BinaryOp_LogicalOr", "Expr_BinaryOp_BooleanOr")  # `X or die(m)`: an if (rules v0.15 section 1)
# boundary-rules v0.15 section 4: methods of PHP's own mysqli classes (php.net), which never print. fetch_object is
# left out: it may construct an object of an app class
MYSQLI_METHODS = {
    "mysqli": frozenset((
        "autocommit", "begin_transaction", "change_user", "character_set_name", "close", "commit", "connect",
        "dump_debug_info", "escape_string", "execute_query", "get_charset", "get_client_info",
        "get_connection_stats", "get_server_info", "get_warnings", "init", "kill", "more_results", "multi_query",
        "next_result", "options", "ping", "prepare", "query", "real_connect", "real_escape_string", "real_query",
        "reap_async_query", "refresh", "release_savepoint", "rollback", "savepoint", "select_db", "set_charset",
        "set_opt", "ssl_set", "stat", "stmt_init", "store_result", "thread_id", "use_result")),
    "result": frozenset((
        "close", "data_seek", "fetch_all", "fetch_array", "fetch_assoc", "fetch_column", "fetch_field",
        "fetch_field_direct", "fetch_fields", "fetch_row", "field_seek", "free", "free_result")),
    "stmt": frozenset((
        "attr_get", "attr_set", "bind_param", "bind_result", "close", "data_seek", "execute", "fetch",
        "free_result", "get_result", "get_warnings", "more_results", "next_result", "prepare", "reset",
        "result_metadata", "send_long_data", "store_result")),
}
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
EXIT_NOTE = ("Harness extensions, pending Member 01 Q20 (boundary rule R-I8 reads them): `exits: true` on a branch "
             "enclosure whose arm has an `exit` / `die` statement directly in its own statement list (read from the "
             "AST); an `exit(\"msg\")` / `die(\"msg\")` that is a statement of its own is a sequence entry of kind "
             "\"Expr_Exit\" (it prints its message), with reads and parts like an echo, at a file's top level only. "
             "An exit inside a function has no entry (a harness note records it). A statement `X or die(m)`, "
             "`X || die(m)` or `X or exit(m)` is, in PHP, `if (!X) { die(m); }` (short-circuit) and is written as "
             "that if (boundary-rules v0.15 section 1): a branch enclosure on the `or` node (kind \"Stmt_If\", "
             "branch \"then\", condNodeId = X, condExpr \"!(X)\", exits true). Any other exit inside an "
             "expression has no entry (a note records it).")
EXIT_OUTPUT_NOTE = ("Harness extension (boundary rule R-I8c, v0.14): output inside an arm that always exits does not "
                    "count as output of the file or function when it is included or called (it is an error stop that "
                    "is not generated). A call or include entry that reaches such an arm, directly or through what "
                    "it includes and calls, lists it in `exitOutput`: [{file, line}] (the exit statement).")
FETCH_NOTE = ("Harness extension (boundary rule R-L4, v0.14): `fetchNodeId` on a loop enclosure is the fetch call that "
              "gives the loop its row (`while ($row = mysqli_fetch_array(...))`, a foreach over a fetch_all result); "
              "a read's `source.fetchNodeId` names the loop whose row it reads.")
REDIRECTS_NOTE = ("Harness extension (generation-spec v0.10, header_redirect): `redirects` lists every "
                  "`header(\"Location: ...\")` call of this scope whose argument is a string literal: {nodeId, "
                  "target, enclosedBy, loc}. It prints nothing; C1 owns the redirect.")
BUILTIN_METHODS_NOTE = ("boundary-rules v0.15 section 4: a method of PHP's own mysqli / mysqli_result / mysqli_stmt "
                        "classes is not a call site when its receiver is certainly of that class: a variable whose "
                        "only definitions in the app are `new mysqli(...)` / `mysqli_connect(...)`, or one whose "
                        "definition reaching the call (the same block or one enclosing it) is such a variable's "
                        "`query()` / `prepare()` or `mysqli_query(<it>, ...)`. A harness note records each one.")
CONTENT_TYPE_NOTE = ("Harness extension: `responseContentType` is the media type of the first `header(\"Content-Type: "
                     "...\")` call with a literal argument in this scope (boundary-rules section 5, non_html_response).")
INCLUDES_NOTE = ("Harness extension, pending Member 01 Q4: an include / require is a sequence entry at its position, "
                 "like a call entry: {kind: \"include\", path, outputsInside}. path is the entrypoint of the included "
                 "file, or null when the path is not a literal or names no PHP file of the app (a library, a file "
                 "that is not in the repository). A literal path is looked up from the including file's own "
                 "directory, then from the app's root. outputsInside is true, false or \"unknown\", as for a call: "
                 "what the included file's top level prints, itself or through what it includes and calls.")
FOLDED_INCLUDE_NOTE = ("boundary-rules v1.0 section 4 (include paths): an include whose path is not one literal but is "
                       "built only from string literals, __DIR__, __FILE__, dirname() / realpath() of those, "
                       "DIRECTORY_SEPARATOR and constants define()d once in the app with such a value is resolved "
                       "statically: its `includes` item carries `folded`, the path from the app's root, starting "
                       "with \"/\".")
WHITESPACE_INCLUDE_NOTE = ("boundary-rules v1.0 section 4 (whitespace-only included output): a file whose top level prints "
                           "only whitespace (outside arms that exit) does not print for its includers: its include "
                           "entry has outputsInside false and `whitespace`, the text, which stands at the include "
                           "site as text (spec section 5 whitespace rules). Such a file has no timeline of its own.")
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
    include_nodes: list = field(default_factory=list)  # (the Expr_Include node, its enclosures), as `includes`
    content_type: str | None = None  # the media type of the first literal header("Content-Type: ...")
    exit_arms: list = field(default_factory=list)  # lines of exit statements whose arm prints (R-I8c)
    redirects: list = field(default_factory=list)  # header("Location: ...") with a literal target (spec v0.10)
    prints_by_function: bool = False  # printf / print_r ... outside every arm that exits
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
        self._printing_exits: set[str] = set()  # ids of exit("msg") statements that are sequence entries
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
        previous = None
        for node in nodes or []:
            if _is(node, "Stmt_Expression") and _is(node.get("expr"), *OR_NODES)                     and _is(node.get("expr").get("right"), "Expr_Exit"):
                self._or_exit(node.get("expr"), scope, chain, enclosures)
                previous = node
                continue
            # `echo ...; exit;` in one block: what follows the block is not printed when it runs. The timeline
            # cannot say so (it has no exit), so it is noted for the pattern log.
            if _is(node, "Stmt_Expression") and _is(node.get("expr"), "Expr_Exit") and not node.get("expr").get("expr") \
                    and _is(previous, "Stmt_Echo", "Stmt_InlineHTML", "Stmt_Expression") and (
                        not _is(previous, "Stmt_Expression") or _is(previous.get("expr"), "Expr_Print")):
                scope.notes.append({"kind": "exit_after_output", "line": node.line, "col": node.loc.column,
                                    "detail": self.node_text(previous)[:80]})
            if _is(node, "Stmt_Expression") and _is(node.get("expr"), "Expr_Exit"):
                message = node.get("expr").get("expr")
                # exit("msg") prints its message. Only at a file's top level: R-I8 speaks of the rest of the
                # enclosing block, and an exit inside a function also ends the page that called it, so there
                # the message stays what it was, a note (not in the timeline).
                if message is not None and not _is(message, "Scalar_Int") and scope.name is None:
                    scope.outputs.append((node.get("expr"), "Expr_Exit", chain, list(enclosures)))
                    self._printing_exits.add(node.get("expr").id)
            self._walk(node, scope, chain, enclosures)
            previous = node

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
        if kind in ("Expr_Assign", "Expr_AssignOp_Concat") and self._has_markup(node.get("expr")):
            # `$image = '<img ...>';` / `$active = 'class="active"';`: markup that reaches the page through a
            # variable. Where it is printed it is one value, so React escapes it. Noted for the pattern log.
            scope.notes.append({"kind": "markup_in_assigned_string", "line": line, "col": col,
                                "detail": self.node_text(node)[:80]})
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
            scope.include_nodes.append((node, list(enclosures)))
        elif kind == "Expr_Eval":
            scope.breakers.append(pos)
        elif kind == "Expr_Exit" and node.get("expr") and not _is(node.get("expr"), "Scalar_Int") \
                and node.id not in self._printing_exits:
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
            returns = callee in RETURNING_FUNCTIONS and len(_args(node)) > 1 and _is(
                _args(node)[1], "Expr_ConstFetch") and (_name(_args(node)[1].get("name")) or "").lower() == "true"
            if callee in OUTPUT_FUNCTIONS and not returns and not any(e.get("exits") for e in enclosures):
                scope.prints_by_function = True
            if callee in OUTPUT_FUNCTIONS and not returns:
                scope.notes.append({"kind": "output_function", "line": line, "col": col, "detail": callee})
            if callee == "header" and scope.content_type is None and _args(node) \
                    and _is(_args(node)[0], "Scalar_String"):
                found = re.match(r"\s*content-type\s*:\s*([^;\s]+)", _args(node)[0].get("value") or "", re.I)
                if found:
                    scope.content_type = found.group(1).lower()
            if callee == "header" and _args(node) and _is(_args(node)[0], "Scalar_String"):
                found = re.match(r"\s*location\s*:\s*(.*?)\s*$", _args(node)[0].get("value") or "", re.I | re.S)
                if found:  # spec v0.10: no output; reported to C1 (header_redirect)
                    scope.redirects.append({"nodeId": node.id, "target": found.group(1),
                                            "enclosedBy": list(enclosures), "loc": loc_dict(node.loc)})
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
                                "args": [self.argument(a) for a in _args(node)],
                                "receiver": (_variable(node.get("var")), (_name(node.get("name")) or "").lower(),
                                             chain) if kind == "Expr_MethodCall" else None})

    def _has_markup(self, node) -> bool:
        """Whether an expression holds a string literal with a tag or a written-out attribute in it."""
        todo = [node]
        while todo:
            current = todo.pop()
            if isinstance(current, list):
                todo += current
            elif isinstance(current, _N):
                value = current.get("value") if current.kind in ("Scalar_String", "InterpolatedStringPart") else None
                if isinstance(value, str) and _MARKUP_TEXT.search(value):
                    return True
                todo += list(current.kids)
        return False

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

    def mysqli_kind(self, scope: _Scope, call: dict, connections: set) -> str | None:
        """Which of PHP's mysqli classes the receiver of a method call certainly is (rules v0.15 section 4), or
        None: "mysqli" for a variable whose only definitions in the app are `new mysqli(...)` /
        `mysqli_connect(...)`; "result" / "stmt" for one whose definition reaching the call (the same block or
        one enclosing it) is such a variable's `query()` / `prepare()`, or `mysqli_query(<it>, ...)`."""
        variable, _, chain = call["receiver"]
        if variable in connections:
            return "mysqli"
        found = self._reaching(scope, variable, call["node"].start, chain) if variable else None
        rhs = found.node if found is not None and found.kind == "assign" else None
        while _is(rhs, "Expr_ErrorSuppress"):
            rhs = rhs.get("expr")
        if _is(rhs, "Expr_MethodCall") and _variable(rhs.get("var")) in connections:
            return {"query": "result", "prepare": "stmt"}.get((_name(rhs.get("name")) or "").lower())
        if _is(rhs, "Expr_FuncCall") and _callee(rhs) == "mysqli_query" and _args(rhs)                 and _variable(_args(rhs)[0]) in connections:
            return "result"
        return None

    def _or_exit(self, node: _N, scope: _Scope, chain: tuple, enclosures: list) -> None:
        """`X or die(m)` as a statement: `if (!X) { die(m); }` (boundary-rules v0.15 section 1), so that R-I8 and
        R-I8c decide it like the if it is."""
        left, stop = node.get("left"), node.get("right")
        scope.controls.append(node.start)
        self._walk(left, scope, chain, enclosures)  # always runs
        enclosure = {"nodeId": node.id, "kind": "Stmt_If", "role": "branch", "branch": "then",
                     "condNodeId": left.id, "condExpr": f"!({self.node_text(left)})", "loc": loc_dict(node.loc),
                     "exits": True}
        inner_chain, inner = chain + ((node.id, "then0"),), enclosures + [enclosure]
        message = stop.get("expr")
        prints = message is not None and not _is(message, "Scalar_Int")
        if prints and scope.name is None:  # as for `die("msg");` in an if arm: a sequence entry
            scope.outputs.append((stop, "Expr_Exit", inner_chain, list(inner)))
            self._printing_exits.add(stop.id)
        self._walk(stop, scope, inner_chain, inner)
        if prints:
            scope.exit_arms.append(stop.line)  # R-I8c: an arm that prints and then exits
        scope.notes.append({"kind": "expression_exit", "line": node.line, "col": node.loc.column,
                            "detail": self.node_text(node)[:80]})

    def _if(self, node: _N, scope: _Scope, chain: tuple, enclosures: list) -> None:
        if_id, cond = node.id, node.get("cond")
        self._walk(cond, scope, chain, enclosures)
        arms = [("then", cond, node.get("stmts"))]
        for elseif in node.get("elseifs") or []:
            arms.append(("elseif", elseif.get("cond"), elseif.get("stmts")))
        if node.get("else"):
            arms.append(("else", cond, node.get("else").get("stmts")))  # an else points at its if's condition
        printed = []  # per arm: whether anything in it has a place in the timeline
        for index, (branch, branch_cond, stmts) in enumerate(arms):
            if branch == "elseif":
                self._walk(branch_cond, scope, chain, enclosures)
            enclosure = {"nodeId": if_id, "kind": "Stmt_If", "role": "branch", "branch": branch,
                         "condNodeId": branch_cond.id}
            if branch != "else":
                enclosure["condExpr"] = self.node_text(branch_cond)
            enclosure["loc"] = loc_dict(node.loc)
            exit_stmt = next((s for s in stmts or [] if _is(s, "Stmt_Expression") and _is(s.get("expr"), "Expr_Exit")),
                             None)
            if exit_stmt is not None:
                enclosure["exits"] = True  # R-I8: the arm always ends the script (Member 01 Q20)
            before = len(scope.outputs)
            notes_before = sum(n["kind"] in ("output_function", "exit_with_output") for n in scope.notes)
            self._walk_list(stmts, scope, chain + ((if_id, f"{branch}{index}"),), enclosures + [enclosure])
            printed.append(len(scope.outputs) > before)
            message = exit_stmt.get("expr").get("expr") if exit_stmt is not None else None
            if exit_stmt is not None and (
                    len(scope.outputs) > before or (message is not None and not _is(message, "Scalar_Int"))
                    or sum(n["kind"] in ("output_function", "exit_with_output") for n in scope.notes) > notes_before):
                scope.exit_arms.append(exit_stmt.line)  # R-I8c: an arm that prints and then exits
        # An `elseif` arm that prints nothing, before an arm that prints, is invisible in the timeline (only
        # arms with output are in it): the later arm then looks as if it followed the earlier ones directly.
        if any(not p and arms[i][0] == "elseif" and any(printed[i + 1:]) for i, p in enumerate(printed)):
            scope.notes.append({"kind": "silent_elseif_arm", "line": node.line, "col": node.loc.column,
                                "detail": self.node_text(cond)[:80]})

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
                    enclosure["fetchNodeId"] = cond.get("expr").id  # R-L4: the fetch this loop's row comes from
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
            enclosure["fetchNodeId"] = source.id
        elif _variable(source):
            filled = self._reaching(scope, _variable(source), node.start, chain)
            if filled and filled.kind == "assign" and _callee(filled.node) in FETCH_ALL:
                enclosure["iterSourceKind"] = "db_row_field"
                enclosure["fetchNodeId"] = filled.node.id
        return enclosure

    def whitespace_text(self, scope: _Scope) -> str | None:
        """What a file's top level prints when all of it (outside arms that exit) is whitespace inline HTML
        (boundary-rules v1.0 section 4, e.g. warehouse `includes/load.php`, an empty line before `<?php`); else
        None. Function scopes are not looked at."""
        if scope.name is not None or scope.prints_by_function:
            return None
        printed = [(node, kind) for node, kind, _, enclosures in scope.outputs
                   if not any(e.get("exits") for e in enclosures)]
        if not printed or any(kind != "Stmt_InlineHTML" or self.node_text(node).strip(HTML_SPACE)
                              for node, kind in printed):
            return None
        return "".join(self.node_text(node) for node, _ in printed)

    def direct_output(self, scope: _Scope) -> bool:
        """Whether a scope prints by itself (what it includes and calls is followed by function_resolver).
        Output inside an arm that always exits does not count (R-I8c): it is an error stop. Nor does a file's
        top level that prints only whitespace (boundary-rules v1.0 section 4): that text stands at its includes."""
        if self.whitespace_text(scope) is not None:
            return False
        return scope.prints_by_function or any(
            not any(e.get("exits") for e in enclosures) for _, _, _, enclosures in scope.outputs)

    def slots_print(self, scope: _Scope, resolve) -> bool:
        """Whether a scope has a timeline: it prints itself, or it is a file's top level whose calls or includes
        certainly print (a page made only of `include header; show(); include footer`)."""
        if scope.outputs and self.whitespace_text(scope) is None:
            return True
        if scope.name is not None:
            return False
        calls = [resolve(call["name"], self) for call in scope.calls if call["name"]]
        includes = [resolve.include(include, self) for include in scope.includes]
        return any(found is not None and found[1] is True for found in calls + includes)

    def timeline(self, scope: _Scope, provenance: dict, resolve=None) -> dict | None:
        """The schema 1.0 document of one scope, or None when it prints nothing (see slots_print).

        `resolve(name)` gives (callee node id or None, outputsInside) for a function of the app and None for
        any other name; without it, only this file's own functions are known."""
        if resolve is None:
            resolve = function_resolver({self.file_id: self})
        own_resolve, resolve = resolve, lambda name: own_resolve(name, self)
        if not self.slots_print(scope, own_resolve):
            return None
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
            exits = own_resolve.exits(call["name"], self) if call["name"] else []
            sequence.append((node.start, {
                "id": node.id, "kind": "call", "callee": callee, "outputsInside": outputs_inside,
                "enclosedBy": [self.loop_enclosure(e) if e["role"] == "iteration" else e for e in call["enclosures"]],
                "loc": loc_dict(node.loc), "args": call["args"], **({"exitOutput": exits} if exits else {})}))
            if call["name"]:
                call_names[node.id] = call["name"]
        for include, (node, enclosures) in zip(scope.includes, scope.include_nodes):
            target, outputs_inside = own_resolve.include(include, self)
            exits = own_resolve.include_exits(include, self)
            whitespace = own_resolve.include_whitespace(include, self)
            sequence.append((node.start, {
                "id": node.id, "kind": "include", "path": target, "outputsInside": outputs_inside,
                "enclosedBy": [self.loop_enclosure(e) if e["role"] == "iteration" else e for e in enclosures],
                "loc": loc_dict(node.loc), **({"exitOutput": exits} if exits else {}),
                **({"whitespace": whitespace} if whitespace else {})}))
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
            **({"responseContentType": scope.content_type} if scope.content_type else {}),
            "sequence": [entry for _, entry in sequence],
            **({"redirects": [redirect | {"enclosedBy": [
                self.loop_enclosure(e) if e["role"] == "iteration" else e for e in redirect["enclosedBy"]]}
                for redirect in scope.redirects]} if scope.redirects else {}),
            "queries": dict(sorted(queries.items())),
            "callNames": dict(sorted(call_names.items())),
            "includes": sorted(scope.includes, key=lambda i: i["nodeId"]),
            "harnessNotes": notes,
            "_provenance": provenance | {
                "function": scope.name,
                "functionNodeId": scope.node_id,
                "sourceEncoding": self.encoding,
                "locConvention": LOC_NOTE, "condExprConvention": COND_EXPR_NOTE, "callsConvention": CALLS_NOTE,
                "callResolution": RESOLUTION_NOTE, "includesExtension": INCLUDES_NOTE,
                "exitExtension": EXIT_NOTE, "contentTypeExtension": CONTENT_TYPE_NOTE,
                "exitOutputExtension": EXIT_OUTPUT_NOTE, "fetchExtension": FETCH_NOTE,
                "foldedIncludes": FOLDED_INCLUDE_NOTE, "whitespaceIncludes": WHITESPACE_INCLUDE_NOTE,
                "redirectsExtension": REDIRECTS_NOTE, "builtinMethods": BUILTIN_METHODS_NOTE,
                "selectStarConvention": SELECT_STAR_NOTE,
                "partsExtension": PARTS_NOTE, "copyPropagationExtension": COPY_NOTE,
                "sqlValuesExtension": SQL_VALUES_NOTE,
                "queriesExtension": QUERIES_NOTE, "harnessNotesExtension": NOTES_NOTE,
            },
        }


def _connection(rhs) -> bool:
    while _is(rhs, "Expr_ErrorSuppress"):
        rhs = rhs.get("expr")
    return (_is(rhs, "Expr_New") and (_name(rhs.get("class")) or "").lower() in ("mysqli", "\\mysqli")
            or _is(rhs, "Expr_FuncCall") and _callee(rhs) == "mysqli_connect")


def drop_builtin_methods(harnesses: dict) -> None:
    """Calls of PHP's own mysqli methods are not call sites (boundary-rules v0.15 section 4): like a built-in
    function, they never print. A variable is a connection when every definition of it in the app (a `global`
    statement only binds it) is `new mysqli(...)` / `mysqli_connect(...)`. What is dropped is noted."""
    found: dict[str, list] = {}
    for harness in harnesses.values():
        for scope in harness.scopes():
            for name, defs in scope.defs.items():
                found.setdefault(name, []).extend(d for d in defs if not _is(d.node, "Stmt_Global"))
    connections = {name for name, defs in found.items()
                   if defs and all(d.kind == "assign" and _connection(d.node) for d in defs)}
    for harness in harnesses.values():
        for scope in harness.scopes():
            kept = []
            for call in scope.calls:
                kind = harness.mysqli_kind(scope, call, connections) if call.get("receiver") else None
                if kind is not None and call["receiver"][1] in MYSQLI_METHODS[kind]:
                    node = call["node"]
                    note = {"kind": "builtin_mysqli_method", "line": node.line, "col": node.loc.column,
                            "detail": f"{kind}::{call['receiver'][1]}"}
                    if note not in scope.notes:
                        scope.notes.append(note)
                else:
                    kept.append(call)
            scope.calls[:] = kept


def _fold(node, key: str, constants: dict, seen: tuple = ()) -> str | None:
    """The value of an include path expression written in app file `key`, when it is statically certain
    (boundary-rules v1.0 section 4); a path from the app's root starts with "/". None otherwise."""
    if _is(node, "Scalar_String"):
        return node.get("value")
    if _is(node, "Expr_BinaryOp_Concat"):
        left, right = _fold(node.get("left"), key, constants, seen), _fold(node.get("right"), key, constants, seen)
        return None if left is None or right is None else left + right
    if _is(node, "Scalar_MagicConst_File"):
        return "/" + key
    if _is(node, "Scalar_MagicConst_Dir"):
        return posixpath.dirname("/" + key)
    if _is(node, "Expr_FuncCall") and _callee(node) in ("dirname", "realpath") and len(_args(node)) == 1:
        value = _fold(_args(node)[0], key, constants, seen)
        if value is None or not value.startswith("/"):
            return None  # relative to the working directory, which the source does not fix
        return posixpath.dirname(value) if _callee(node) == "dirname" else posixpath.normpath(value)
    if _is(node, "Expr_ConstFetch"):
        name = _name(node.get("name"))
        if name == "DIRECTORY_SEPARATOR":
            return "/"
        if name in constants and name not in seen:
            where, value = constants[name]
            return _fold(value, where, constants, seen + (name,))
    return None


def fold_include_paths(harnesses: dict) -> None:
    """Gives every include whose path is not one literal its statically certain path, if it has one
    (`folded`): constants count only when the app define()s them exactly once."""
    defines: dict[str, list] = {}
    for key in sorted(harnesses):
        for node in harnesses[key].nodes.values():
            if _is(node, "Expr_FuncCall") and _callee(node) == "define" and len(_args(node)) >= 2 \
                    and _is(_args(node)[0], "Scalar_String"):
                defines.setdefault(_args(node)[0].get("value"), []).append((key, _args(node)[1]))
    constants = {name: found[0] for name, found in defines.items() if len(found) == 1}
    for key, harness in harnesses.items():
        for scope in harness.scopes():
            for include, (node, _) in zip(scope.includes, scope.include_nodes):
                if include["literal"] is None:
                    folded = _fold(node.get("expr"), key, constants)
                    if folded is not None and folded.startswith("/"):
                        include["folded"] = posixpath.normpath(folded)


def function_resolver(harnesses: dict):
    """resolve(name, harness) for a set of files that form one app, keyed by their path in the app.

    It gives (callee node id or None, outputsInside) for a name the app defines, and None for any other name.
    A name with one definition is that function. A name that several files define resolves only through the
    calling file's own literal includes, followed transitively: exactly one reachable definition -> that
    function; otherwise there is no single callee (id None, and its output is what all definitions agree on).

    A function, or a file's top level, prints if it prints itself, calls a function that prints or includes a
    file whose top level prints. It is "unknown" when it, or something it calls or includes, makes a call or an
    include that cannot be followed.

    `resolve.include(include, harness)` gives (the included file's entrypoint or None, outputsInside) for one
    entry of a scope's `includes` (see INCLUDES_NOTE).
    """
    drop_builtin_methods(harnesses)
    fold_include_paths(harnesses)
    definitions: dict[str, list] = {}  # lower-case name -> [(file key, harness, scope)]
    for key in sorted(harnesses):
        for name, scope in harnesses[key].functions.items():
            definitions.setdefault(name.lower(), []).append((key, harnesses[key], scope))
    key_of = {id(harness): key for key, harness in harnesses.items()}
    scopes = [(key, harnesses[key], scope) for key in sorted(harnesses) for scope in harnesses[key].scopes()]
    state = {id(scope): harness.direct_output(scope) for _, harness, scope in scopes}
    reach: dict[str, set] = {}

    def include_target(include: dict, key: str) -> str | None:
        """The app file a literal (or statically folded) include written in file `key` names, or None."""
        if include.get("folded"):  # a path from the app's root (boundary-rules v1.0 section 4)
            target = include["folded"].lstrip("/")
            return target if target in harnesses else None
        if include["literal"] is None:
            return None
        for target in (posixpath.normpath(posixpath.join(posixpath.dirname(key), include["literal"])),
                       posixpath.normpath(include["literal"])):
            if target in harnesses:
                return target
        return None

    def reachable(key: str) -> set:
        """The file itself and every app file its top-level literal includes lead to."""
        if key not in reach:
            seen, todo = {key}, [key]
            while todo:
                current = todo.pop()
                for include in harnesses[current].top.includes:
                    target = include_target(include, current)
                    if target is not None and target not in seen:
                        seen.add(target)
                        todo.append(target)
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

    def included(include: dict, key: str) -> bool | str:
        target = include_target(include, key)
        return "unknown" if target is None else state[id(harnesses[target].top)]

    # R-I8c: the exit arms that print, reached by a scope directly or through what it calls and includes
    exits = {id(scope): {(harness.entrypoint, line) for line in scope.exit_arms} for _, harness, scope in scopes}
    grown = True
    while grown:
        grown = False
        for key, _, scope in scopes:
            reached = set(exits[id(scope)])
            for call in scope.calls:
                for _, _, target in (targets(call["name"], key) if call["name"] else []):
                    reached |= exits[id(target)]
            for include in scope.includes:
                target = include_target(include, key)
                if target is not None:
                    reached |= exits[id(harnesses[target].top)]
            if reached != exits[id(scope)]:
                exits[id(scope)], grown = reached, True

    def listed(found: set) -> list:
        return [{"file": file, "line": line} for file, line in sorted(found)]

    changed = True
    while changed:
        changed = False
        for key, _, scope in scopes:
            value = weakest([state[id(scope)]] + [called(call, key) for call in scope.calls]
                            + [included(include, key) for include in scope.includes])
            if value != state[id(scope)]:
                state[id(scope)] = value
                changed = True

    def resolve(name: str, harness):
        found = targets(name, key_of[id(harness)])
        if not found:
            return None
        return (found[0][2].node_id if len(found) == 1 else None), together(found)

    def resolve_include(include: dict, harness):
        key = key_of[id(harness)]
        target = include_target(include, key)
        return (None if target is None else harnesses[target].entrypoint), included(include, key)

    def resolve_exits(name: str, harness) -> list:
        found = targets(name, key_of[id(harness)])
        return listed(set().union(*(exits[id(scope)] for _, _, scope in found))) if found else []

    def whitespace(key: str, seen: frozenset = frozenset()) -> str:
        """The whitespace a file prints for its includers: its own, and that of the whitespace-only files it
        includes, in source order. Empty when the file prints anything else."""
        top = harnesses[key].top
        if state[id(top)] is not False:
            return ""
        own = harnesses[key].whitespace_text(top) or ""
        pieces = [(node.start, harnesses[key].node_text(node)) for node, kind, _, enclosures in top.outputs
                  if own and not any(e.get("exits") for e in enclosures)]
        for include, (node, _) in zip(top.includes, top.include_nodes):
            target = include_target(include, key)
            if target is not None and target not in seen:
                pieces.append((node.start, whitespace(target, seen | {key})))
        return "".join(text for _, text in sorted(pieces, key=lambda p: p[0]))

    def resolve_include_whitespace(include: dict, harness) -> str:
        target = include_target(include, key_of[id(harness)])
        return "" if target is None else whitespace(target)

    def resolve_include_exits(include: dict, harness) -> list:
        target = include_target(include, key_of[id(harness)])
        return [] if target is None else listed(exits[id(harnesses[target].top)])

    resolve.include = resolve_include
    resolve.exits = resolve_exits
    resolve.include_exits = resolve_include_exits
    resolve.include_whitespace = resolve_include_whitespace
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
