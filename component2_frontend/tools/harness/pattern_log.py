"""Write docs/php-analysis/pattern-log.md: what the pipeline could not handle on the whole apps.

The evidence for roadmap Phases 2 and 3. From the outputs of tools/harness/run_apps.py it lists
every construct the pipeline abstains on, cannot model, or handles with a last-resort fallback,
grouped by kind, each with its count and file:line examples and mapped to the pattern checklist in
docs/php-analysis/boundary-rules.md section 8 (an existing pattern, or new). It records; it does not
decide: no rule is written here.

Reads only output/apps/<app>/ (index.json and each page's timeline.json, stage2.json, stage5.json).
One count is made by reading a timeline's markup again (attribute_ifs): Stage 5 stops at the first `if`
that prints an attribute, so its outputs name one per unit, not all.
Deterministic: the same outputs give byte-identical Markdown.

Usage, from component2_frontend/ (after tools/harness/run_apps.py):
    venv/Scripts/python.exe tools/harness/pattern_log.py
"""

import json
import posixpath
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from results_table import source  # noqa: E402

from src.mapping import app_units, loader, markup  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.stage2_boundaries import _regions  # noqa: E402

OUTPUT = ROOT / "output" / "apps"
DEFAULT_MARKDOWN = ROOT / "docs" / "php-analysis" / "pattern-log.md"
APPS = ("hms", "wackopicko", "phpblog", "attendance", "warehouse")
MAX_EXAMPLES = 6
NEW = "**new**"
CANDIDATE = "**candidate new** (owner decides)"  # Phase 3: judged by the definition in boundary-rules section 8
REQUEST_IF = re.compile(r"isset\s*\(\s*\$_(POST|GET|REQUEST)\b")
COPIED = re.compile(r"\$[A-Za-z_]\w*")  # a computed read that is a plain variable: copy propagation

# kind -> (group, title, mapping to boundary-rules section 8, what it is)
CATALOGUE = {
    "abstain:chunked_container": (
        "A", "Chunked loop", "existing: Chunked loop (closes and reopens its container)",
        "The loop body closes and reopens its own container (R-L7). Stage 2 abstains."),
    "abstain:tag_crosses_branch": (
        "A", "A tag opens in one branch and closes outside it", "existing: Malformed HTML (R-L6, per-branch balance)",
        "An `if` branch opens an element that is closed after the `if`, or closes one opened before it."),
    "abstain:container_undetermined@function": (
        "A", "Loop in a function with no determinable container", "existing: Malformed HTML",
        "The loop's tags do not balance inside the function (R-F1b covers only a container the caller opens)."),
    "abstain:unresolved_call": (
        "A", "Call or include whose output cannot be followed",
        "existing: rules v0.11 section 4 (call-entry table); Member 01 Q11",
        "A call with no single callee (a method call on an object, a dynamic call, a name several files define), "
        "a call whose output is unknown, or an include of a file that is not in the app (a library, a file the "
        "installer writes). Consecutive ones are one gap: one C2Todo listing all their ids (spec v0.7 section 8a)."),
    "abstain:tag_crosses_function": (
        "A", "Tags cross a function boundary outside a shell pair", "existing: rules v0.11 R-F2",
        "A page calls a header without its footer (or the other way round), or a function leaves tags open "
        "without being half of a pair."),
    "abstain:tag_crosses_function@include": (
        "A", "Included header / footer files that do not form a shell pair", "existing: rules v0.11 R-F2",
        "R-F2 as written for included files: the header file leaves elements open and the footer file closes "
        "tags, but the footer's closing tags are not exactly the header's open elements in reverse (condition 2), "
        "so there is no pair and each include abstains. In phpBlog `admin/header.php` leaves `<head>` and `<main>` "
        "open and `admin/footer.php` never closes them (a browser does, implicitly)."),
    "abstain:cross_file_output": (
        "A", "Include of a file that prints, outside a shell pair",
        "existing: section 5 (cross-file output); section 8 Layout via `include header.php`",
        "The included file's top level prints (it has a timeline of its own) and it is not half of a shell pair. "
        "R-F1 is written for functions, so no rule places that output here: one C2Todo at the include."),
    "abstain:list_guard_multiple_loops": (
        "A", "An `if` with output loops in different arms", "existing: R-I2 / R-I2b (rules v0.13)",
        "R-I2b covers several loops in one arm. Loops in different arms: neither rule says which List an arm "
        "is the Empty of."),
    "abstain:non_html_response": (
        "A", "Unit that is not an HTML response", "existing: section 5 `non_html_response` (rules v0.13)",
        "`header(\"Content-Type: text/xml\")`: an RSS feed, a sitemap. Not a page; an API route owned by C1. "
        "Nothing is generated; the unit is listed under `notGenerated`."),
    "abstain:exit_arm_not_covered": (
        "A", "Exit arm that R-I8 does not cover", "existing: section 5 `exit_arm_not_covered` (rules v0.14)",
        "An arm that ends in `exit` / `die` after output that is not a redirect, where \"the rest of the "
        "enclosing block is its implicit else\" is not the whole truth: the arm is nested in another `if` or "
        "loop, or it is not the `then` arm, or the rest closes tags opened before the `if` (in a function or an "
        "included file R-I8c applies instead)."),
    "abstain:partial_attribute_output": (
        "A", "An `if` that starts inside a tag and also prints elements", "existing: section 5 (no rule matches)",
        "R-I3 covers an `if` that stays inside one attribute value. This one starts in a tag and goes on."),
    "abstain:guarded_loop_abstained": (
        "A", "List guard whose loop abstained", "existing: section 5", "No List to guard."),
    "abstain:loop_in_attribute": (
        "A", "Loop that prints inside a tag", "existing: section 5 (no rule matches)", "Stage 2 abstains."),
    "abstain:output_outside_shell": (
        "A", "Page output before the header or after the footer", "existing: rules v0.11 R-F2",
        "It would stand outside the shell's `<html>`."),
    "abstain:recursive_function": (
        "A", "Recursive function", "existing: rules v0.11 R-F1", "A function component cannot call itself here."),
    "abstain:call_in_attribute": (
        "A", "Call that prints inside a tag", NEW,
        "A function is called where an attribute is written. A component cannot stand there."),
    "abstain:container_undetermined@page": (
        "A", "Loop with no determinable container", "existing: Malformed HTML",
        "No element is open at the loop start, or the loop's tags do not balance."),
    "abstain:no_rule@markup_in_string": (
        "A", "Rows printed by one echo string", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "The loop body is one `echo` of a string that holds the row markup, and the echo has no `parts` (it could "
        "not be split exactly), so Stage 2 sees a loop whose elements it cannot place and abstains (`no_rule`)."),
    "abstain:no_rule@text_only": (
        "A", "Loop whose elements are not at the body's own level", "existing: section 5 (no rule matches)",
        "The loop body has elements but none opens at its own level. Stage 2 abstains (`no_rule`)."),
    "abstain:markup_from_expression": (
        "A", "Expression where a tag name or an attribute name is written", "existing: rules v0.9 §1",
        "An echo part such as `\"<$tag>\"` or `\"<td $attr>\"`. It cannot be generated; that echo abstains."),
    "abstain:switch_case_enclosure": (
        "A", "Output inside a switch", "existing: section 5 (no rule yet)", "Stage 2 abstains."),
    "abstain:try_catch_enclosure": (
        "A", "Output inside try / catch", "existing: section 5 (no rule yet)", "Stage 2 abstains."),
    "fallback:container_shared": (
        "B", "Page output inside the list's container, outside the loop",
        "existing: Page output inside a List's container outside the loop (rules v0.9 §8)",
        "R-L2a gives the List its container (`<table>`), but that element also holds output that belongs to the "
        "page: in HMS an error echo, `if(!$result){ echo mysqli_error($con); }`, just before the row loop. The List "
        "cannot own the container, so Stage 5 replaces the List with one C2Todo (`markup_not_generatable`); the "
        "rest of the page is generated."),
    "fallback:markup_not_generatable": (
        "B", "Structure Stage 5 cannot generate", NEW, "Last-resort fallback for another reason (see the detail)."),
    "fallback:if_prints_attribute": (
        "B", "`if` that prints a whole attribute inside a tag", CANDIDATE,
        "`<option value=\"x\" <?php if ($a == 'x') { echo 'selected'; } ?>>`, `<li <?php if (...) { echo "
        "'class=\"nav-item\"'; } ?>>`. R-I3 covers an `if` inside an attribute *value*; this `if` stands between "
        "attributes and prints the attribute itself. Stage 2 labels it R-I3, Stage 5 cannot write it, and the "
        "last resort replaces the smallest enclosing component with one C2Todo: here that is the whole page or "
        "shell (one row below per unit lost; the first such `if` of the unit is shown)."),
    "seen:if_prints_attribute": (
        "E", "`if` between the attributes of a start tag (counted by re-reading the markup)",
        "existing: R-I3b (rules v0.13)",
        "Every occurrence. The detail says what the `if` prints. Complete literal attributes are conditional "
        "props (R-I3b); anything else costs that tag's attribute only (start-tag fallback)."),
    "fallback:expression_in_attribute_name": (
        "B", "Expression where an attribute name is written (not caught by Stage 2)", "existing: rules v0.9 §1",
        "A separate `echo $selected` between attributes. Section 1 abstains on this for an echo *part* "
        "(`markup_from_expression`); as a statement of its own it reaches Stage 5's last resort."),
    "fallback:element_not_in_react_types": (
        "B", "Element React has no type for", "existing: Element with no React type (rules v0.9 §8)",
        "An obsolete or unknown element (e.g. `<frameset>`). The element becomes a C2Todo."),
    "fallback:expression_not_translatable": (
        "B", "Echo of several pieces, without parts", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "One `echo` prints several pieces and has no `parts` (one piece cannot be split exactly, e.g. a float), so "
        "Stage 5 has no single value to write and the echo is one C2Todo."),
    "fallback:dynamic_raw_text": (
        "B", "Output inside a raw-text element", NEW, "PHP output inside `<style>` or `<title>` content."),
    "fallback:abstained_code_not_generated": (
        "B", "Abstained region whose code could not be written", NEW, "Only a flag is left."),
    "note:interpolated_string_output+markup": (
        "C", "Markup inside an interpolated string, not split", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "`echo \"<td>$x</td>\"` that could not be given `parts`: one unresolved read, as before Phase 2a."),
    "note:interpolated_string_output": (
        "C", "Interpolated string without markup, not split", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "`echo \"Hello $name\"` that could not be given `parts`: one unresolved read."),
    "note:markup_in_concatenated_echo+markup": (
        "C", "Markup inside a concatenated echo, not split", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "`echo '<td>' . $x . '</td>'` that could not be given `parts` (see \"Echo of several pieces\")."),
    "note:heredoc_output+markup": ("C", "Markup inside a heredoc, not split", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)", "One unresolved read."),
    "note:heredoc_output": ("C", "Heredoc output, not split", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)", "One unresolved read."),
    "note:exit_after_output": (
        "E", "Output followed by `exit` in one block, as the harness sees it", "existing: R-I8 (rules v0.13)",
        "`if (...) { echo '<meta http-equiv=\"refresh\" ...>'; exit; }`. When the block runs, nothing after it is "
        "printed. The timeline has no exit, so everything after the guard is generated as if it always rendered. "
        "Related section 8 row: `die()` / `exit()` with a message."),
    "note:silent_elseif_arm": (
        "C", "`elseif` arm that prints nothing, before an arm that prints", CANDIDATE,
        "Only arms with output are in the timeline, so the later arm looks as if it followed the earlier ones "
        "directly: `if (a) {X} elseif (b) {} else {Y}` would be generated as `a ? X : Y`."),
    "note:markup_in_assigned_string": (
        "C", "Markup or an attribute kept in a variable (`$image = '<img ...>'`)",
        "data flow, not a boundary rule (rules v0.13 section 8)",
        "A string literal with a tag or a written-out attribute is assigned (or appended) to a variable. Where the "
        "variable is printed it is one value: an unresolved read, which React would escape. Not every such "
        "variable is printed (some are mailed or written to a file)."),
    "note:exit_with_output": (
        "C", "`die()` / `exit()` with a message", "existing: `die()` / `exit()` with a message (rules v0.9 §8)",
        "Prints its argument and stops. Schema 1.0 has no output kind for it, so the timeline does not contain it."),
    "note:output_function": (
        "C", "Output through a function (`printf`, `print_r`, …)", NEW,
        "Schema 1.0 has `Stmt_Echo`, `Stmt_InlineHTML` and `Expr_Print` only; this output is not in the timeline."),
    "note:output_buffering": (
        "C", "Output buffering (`ob_start`)", NEW, "Captured output may never be printed, or be printed elsewhere."),
    "note:row_read_not_traced": (
        "C", "Row read not traced to its SQL", "data, not a boundary pattern (Member 01 Q1, Q3, Q17)",
        "`$row['x']` comes from a fetch, but the query's SQL is built, or interpolates something that is not a "
        "value (a table, a column list, ORDER BY, LIMIT), or the result variable is not certainly one query. The "
        "read stays `unresolved` (never guessed)."),
    "flag:output_dropped": (
        "D", "PHP output inside a dropped element", NEW,
        "An echo inside `<script>` or `<head>`. Stage 5 drops the element (inline JS, document shell), so the value "
        "is dropped with it and flagged `output_dropped`."),
    "flag:html_nesting_changed": (
        "D", "Nesting a browser repairs, not repaired here",
        "existing: Invalid nesting the browser repairs (rules v0.11 section 8)",
        "The generated tree differs from what an HTML5 parser builds and the difference is not one of spec "
        "section 5b's repairs: foster parenting (`<form>` / `<input>` directly in `<table>`), a repair that would "
        "cross a loop, if, call or expression, or a stray `</p>`."),
    "flag:unsupported_style": ("D", "Style attribute Stage 5 cannot convert", NEW, "The attribute is omitted."),
    "handled:r_f1": (
        "E", "Function output as a component at the call site (R-F1)", "existing: Function = component / Cross-file output (rules v0.11 R-F1)",
        "A function that prints, is called, and is tag-balanced on its own is one component; each call site "
        "holds `<Name />`."),
    "handled:r_f1b": (
        "E", "Function loop whose container the caller opens (R-F1b)", "existing: Cross-file output (rules v0.11 R-F1b)",
        "A List without wrapper inside the function component; the container stays around the call site."),
    "handled:r_f2": (
        "E", "Page inside a header / footer shell (R-F2)", "existing: Layout via functions (rules v0.11 R-F2)",
        "The page's content is the shell component's children; `<html>`, `<head>` and `<body>` are the route "
        "group's root layout."),
    "handled:r_i3b": (
        "E", "Attribute-presence conditional (R-I3b)", "existing: R-I3b (rules v0.13)",
        "`<li <?php if (...) echo 'class=\"nav-item\"'; ?>>`: each attribute is a conditional prop."),
    "handled:r_i2b": (
        "E", "List guard around several loops (R-I2b)", "existing: R-I2b (rules v0.13)",
        "One conditional around all the Lists; the guard's other arm is their Empty."),
    "handled:r_i8_guard": (
        "E", "Redirect guard (R-I8, case 1)", "existing: R-I8 (rules v0.13)",
        "An arm that prints only a redirect and exits: not a component, nothing rendered, flag `redirect_guard`."),
    "handled:r_i8c": (
        "E", "Exit arm in a called function or an included file (R-I8c)", "existing: R-I8c (rules v0.14)",
        "Not generated; the function / file counts as non-printing otherwise; every include / call site that "
        "reaches it is flagged `exit_output_not_generated`."),
    "flag:exit_output_not_generated": (
        "E", "Include / call site reaching an exit arm that is not generated (R-I8c)", "existing: R-I8c (rules v0.14)",
        "The flag (review) names the arm's file and line."),
    "note:expression_exit": (
        "E", "`X or die(m)` statement, read as `if (!X) { die(m); }`", "existing: R-I8 / R-I8c (rules v0.15 \u00a71)",
        "Input normalisation (PHP short-circuit): R-I8 decides it in a page, R-I8c in an included file or a "
        "function (flagged at every include / call site)."),
    "note:builtin_mysqli_method": (
        "E", "Method of PHP's own mysqli classes: not a call", "existing: call resolution (rules v0.15 \u00a74)",
        "The receiver is certainly a mysqli connection, result or statement (`new mysqli` / `mysqli_connect`, "
        "or its `query()` / `prepare()` / `mysqli_query`); such methods never print, so there is no call entry."),
    "flag:header_redirect": (
        "E", "`header(\"Location: \u2026\")` with a literal target", "existing: `header_redirect` (spec v0.10)",
        "No output, no component; flagged (info) for C1 with the target and the guarding condition. A file that "
        "prints nothing has no unit: its redirects are listed in `index.json` (`silentFileRedirects`)."),
    "seen:json_echo": (
        "E", "`echo json_encode(...)` in a unit with no `Content-Type` header and no markup of its own",
        "existing: `non_html_response`, subtype `ajax_endpoint` (rules v1.0 section 5)",
        "An AJAX endpoint: not generated, listed under `notGenerated` with the `.js` lines that call it."),
    "seen:filter_in_loop": (
        "A", "Loop whose whole body is one `if` (filter-in-loop)",
        "\u00a78 \"not yet seen: filter-in-loop\" \u2192 **now seen**",
        "Every output of the loop is inside the `then` arm of one `if` at the loop body's own level. The detail is "
        "the condition. How Stage 2 judged the loop is in the abstain rows (if any) at the same line."),
    "handled:r_i8_exit": (
        "E", "Exit arm with other output (R-I8, case 2)", "existing: R-I8 (rules v0.13)",
        "`cond ? <arm> : <rest of the block>`."),
    "flag:implied_end_tag": (
        "E", "Implied end tag: `<li>`, `<dt>` / `<dd>`, `<option>`, `<head>` before `<body>` (spec 5b, v0.8)",
        "existing: Invalid nesting the browser repairs (rules v0.11 section 8)", "Closed as a browser does."),
    "handled:r_i7": (
        "E", "`if` or loop whose only output is inline JS (R-I7)", "existing: rules v0.11 R-I7",
        "Nothing to render, so no boundary. The JS is still flagged `inline_js_dropped`."),
    "flag:implicit_tbody": (
        "E", "Rows directly in `<table>`: implicit `<tbody>` (spec 5b)",
        "existing: Invalid nesting the browser repairs (rules v0.11 section 8)", "Wrapped as a browser does."),
    "flag:implicit_p_close": (
        "E", "`<p>` closed by a block element (spec 5b)",
        "existing: Invalid nesting the browser repairs (rules v0.11 section 8)", "Split as a browser does."),
    "flag:stray_end_tag_repaired": (
        "E", "Stray `</p>` / `</br>` (spec 5b, v0.7)",
        "existing: Invalid nesting the browser repairs (rules v0.11 section 8)",
        "An empty `<p></p>` / a `<br />`, as a browser builds it."),
    "seen:nested_list": (
        "E", "Nested loops (R-L4): the inner List is one node of the outer Item",
        "existing: section 8 \"not yet seen: nested loops\" → **now seen**; R-L4 was design only",
        "First real instances. The inner Item gets its own row only: a read of the *outer* loop's row inside "
        "the inner loop (phpBlog `admin/dashboard.php` 180 / 182, a per-row lookup query) has no prop to come "
        "from, and would reach Stage 5's last resort once a contract maps the data."),
    "seen:r_i5_request": (
        "E", "R-I5 on a request-handling `if` (`isset($_POST[...])` / `isset($_GET[...])`)",
        "existing: Request-handling `if` (section 8)", "As in HMS: the fallback fires on form / query handlers."),
    "seen:r_i5_other": (
        "E", "R-I5 on any other `if` above 3 elements",
        "existing rule (R-I5 fallback); the report statement \"no display conditional above 3 elements\" "
        "must be re-read", "The detail is the condition: role checks, login state, settings switches."),
    "flag:duplicate_attribute": (
        "E", "Duplicate attribute: the first is kept (spec 5b)", "existing: Malformed HTML",
        "The later one is dropped, as a browser does."),
    "handled:echo_parts+markup": (
        "E", "Echo string with markup, read piece by piece", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "`echo \"<tr><td>$x</td>…\"`, a concatenation, a heredoc or several echo arguments. The timeline gives "
        "`parts`: literal pieces go through the tag tracker like inline HTML, each expression is one read. Before "
        "Phase 2a such an echo was one opaque node."),
    "handled:echo_parts": (
        "E", "Echo string without markup, read piece by piece", "existing: Rows printed by one echo string / concatenated echo with markup (rules v0.9 §1, §8)",
        "The same, with no tag in the literal pieces: the text around the values is now generated."),
    "handled:r_l3b": (
        "E", "Text-only loop → inline map (R-L3b)", "existing: Text-only loop (rules v0.9 §2, §8)",
        "The loop body prints text and values but no element: an inline `.map()` of keyed Fragments, no Item."),
    "handled:copy_propagation": (
        "E", "Read through a copied variable", "data (Member 01 Q17)",
        "`$v = $row['k'];` then `$v` printed in the same block with nothing in between that could change either: "
        "the read is `computed`, derived from `$row['k']`."),
    "handled:sql_interpolated_values": (
        "E", "Query with interpolated values", "data (Member 01 Q17)",
        "Every interpolation in the SQL is a value (inside quotes, or a whole value after a comparison), so the "
        "select list and the table are read from the fixed text. Flag `sql_interpolated_values` (info). Assumes the "
        "query's structure is its template: a value carrying SQL could change it at run time."),
}
GROUPS = {
    "A": "Abstained by Stage 2",
    "B": "Last-resort fallback in Stage 5",
    "C": "Not modelled by the timeline (harness notes)",
    "D": "Dropped or changed by Stage 5",
    "E": "Handled by a rule or repair (no longer a gap)",
}


class _AttributeIfs(markup.Builder):
    """The markup tokenizer, noting every `if` that prints inside a tag but outside an attribute value (and
    leaving it out, so that reading goes on)."""

    def __init__(self, outputs, config):
        super().__init__(outputs, config, {}, strict=False)
        self.found = []  # (if id, the tag it is in, what its first branch prints)

    def if_(self, item):
        if self.state not in ("data", "rawtext") and self.state not in markup._ATTR_VALUE:
            first = markup._first_output(item)
            text = (first.raw if first.raw is not None else markup.php_literal_text(first.reads[0].expr)
                    if len(first.reads) == 1 else None) if first is not None else None
            self.found.append((item.node_id, self.tag_name.lower(), " ".join((text or "an expression").split())))
            return
        super().if_(item)


def attribute_ifs(timeline_path: Path, config) -> list:
    """[(line, tag, text)] for every `if` of a timeline that prints an attribute between attributes."""
    document = json.loads(timeline_path.read_text(encoding="utf-8"))
    lines = {enc["nodeId"]: (enc["loc"]["startLine"], enc["loc"]["startCol"] + 1)
             for e in document["sequence"] for enc in e["enclosedBy"] if enc["role"] == "branch" and "loc" in enc}
    outputs = app_units.outputs_with_slots(loader.load_timeline(timeline_path))
    builder = _AttributeIfs(outputs, config)
    try:
        builder.build(_regions(outputs))
    except markup.GenerationError:
        pass  # something else the tokenizer cannot place: what was found before it still counts
    return [(lines.get(if_id, (None, None)), tag, text) for if_id, tag, text in builder.found]


def _walk(node, parents=()):
    yield node, parents
    for child in node["children"]:
        yield from _walk(child, parents + (node,))


def where(app: str, entry: dict, line, col=None) -> str:
    """file:line:col, the column 1-based (loc.startCol + 1); `?` when the node has no loc."""
    function = f" ({entry['function']})" if entry["function"] else ""
    place = "?" if line is None else f"{line}:{col}" if col is not None else str(line)
    return f"`{app}/{entry['path']}:{place}`{function}"


def last_line(loc: dict) -> int:
    """The last line a node has bytes on (its end is exclusive, so an end at column 0 is the line before)."""
    return loc["endLine"] if loc["endCol"] > 0 or loc["endLine"] == loc["startLine"] else loc["endLine"] - 1


def collect(output: Path) -> dict:
    found: dict[str, list] = {}  # kind -> [(where, detail)]
    rules: dict[str, dict] = {app: {} for app in APPS}
    calls: dict[tuple, list] = {}  # (app, name, prints, resolved) -> [where]
    includes: dict[tuple, list] = {}  # (app, target, prints) -> [where]
    conditions: dict[str, dict] = {}  # reason -> {condExpr: [where]}
    checklist = {"r_i5": [], "nested": [], "r_l3": [], "page_guard": [], "for": [], "do": []}
    summary, todo_reasons = {}, {}
    config = load_boundary_config()

    def add(kind, place, detail=""):
        found.setdefault(kind, []).append((place, detail))

    for app in APPS:
        index = json.loads((output / app / "index.json").read_text(encoding="utf-8"))
        printing_pages = {p["path"] for p in index["pages"] if p["function"] is None}
        summary[app] = index
        for entry in index["pages"]:
            if entry.get("role") != "shell" and not entry["crash"]:  # a shell's two halves are read on their own
                for (line, col), tag, text in attribute_ifs(output / app / entry["key"] / "timeline.json", config):
                    add("seen:if_prints_attribute", where(app, entry, line, col), f"in `<{tag}>`: prints `{text}`")
            if entry.get("role") == "shell_part":  # its output is in the shell's unit
                continue
            if entry["crash"]:
                add("crash", where(app, entry, None), entry["crash"]["error"])
                continue
            folder = output / app / entry["key"]
            timeline = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
            stage2 = json.loads((folder / "stage2.json").read_text(encoding="utf-8"))
            stage5 = json.loads((folder / "stage5.json").read_text(encoding="utf-8"))
            line_of = {e["id"]: e["loc"] for e in timeline["sequence"] if e.get("loc")}
            entry_of = {e["id"]: e for e in timeline["sequence"]}
            include_text = {i["nodeId"]: i["expr"] for i in timeline["includes"]}
            todo_reasons.setdefault(app, {})
            for todo in stage5["c2todos"]:
                if todo["severity"] == "todo":
                    todo_reasons[app][todo["reason"]] = todo_reasons[app].get(todo["reason"], 0) + 1
            cond_of = {}
            for e in timeline["sequence"]:
                for enc in e["enclosedBy"]:
                    if enc["role"] == "branch" and "condExpr" in enc:
                        cond_of[enc["nodeId"] if enc["branch"] == "then" else enc["condNodeId"]] = enc["condExpr"]
                    if enc["role"] == "iteration" and enc["kind"] in ("Stmt_For", "Stmt_Do"):
                        checklist["for" if enc["kind"] == "Stmt_For" else "do"].append(
                            where(app, entry, e["loc"]["startLine"], e["loc"]["startCol"] + 1))
            markup_lines = {n["line"] for n in timeline["harnessNotes"] if n.get("markup")}

            for node, parents in _walk(stage2["tree"]):
                locs = [line_of[i] for i in node["nodeIds"] if i in line_of]
                first = (locs[0]["startLine"], locs[0]["startCol"] + 1) if locs else (None, None)
                for rule in node["rules"]:
                    rules[app][rule] = rules[app].get(rule, 0) + 1
                if node["status"] == "abstain":
                    kind = f"abstain:{node['reason']}"
                    if node["reason"] == "container_undetermined":
                        kind += "@function" if entry["function"] else "@page"
                    elif node["reason"] == "no_rule":
                        in_string = any(loc["startLine"] <= line <= last_line(loc) for loc in locs
                                        for line in markup_lines)
                        kind += "@markup_in_string" if in_string else "@text_only"
                    detail = ""
                    sources = [entry_of[s] for s in node["sourceIds"] if s in entry_of]
                    if node["reason"] == "tag_crosses_function" and sources and sources[0]["kind"] == "include":
                        kind += "@include"
                        detail = f"include {include_text.get(sources[0]['id'], '')}"
                    elif node["reason"] in ("unresolved_call", "cross_file_output"):
                        causes = []
                        for source_entry in sources:
                            if source_entry["kind"] == "include":
                                causes.append(f"include {include_text.get(source_entry['id'], '')}"
                                              + (": not a file of the app" if source_entry["path"] is None else ""))
                            else:
                                called = timeline["callNames"].get(source_entry["id"])
                                causes.append(f"{called}()" if called else "a method or dynamic call")
                        counted = {c: causes.count(c) for c in dict.fromkeys(causes)}
                        detail = "; ".join(c if n == 1 else f"{c} × {n}" for c, n in counted.items())
                    add(kind, where(app, entry, *first), detail)
                if node["type"] == "ConditionalComponent":
                    checklist["r_i5"].append((where(app, entry, *first), node["elements"]))
                    text = " ".join(cond_of.get(node["sourceIds"][0], "(no condExpr)").split())[:80]
                    add("seen:r_i5_request" if REQUEST_IF.search(text) else "seen:r_i5_other",
                        where(app, entry, *first), f"`{text}`, {node['elements']} elements")
                if node["type"] == "List" and any(p["type"] == "Item" for p in parents):
                    checklist["nested"].append(where(app, entry, *first))
                    add("seen:nested_list", where(app, entry, *first))
                if node["type"] == "FunctionCall":
                    add("handled:r_f1", where(app, entry, *first),
                        timeline["callNames"].get(node["sourceIds"][0], ""))
                if node["type"] == "List" and "R-F1b" in node["rules"]:
                    add("handled:r_f1b", where(app, entry, *first))
                if node["type"] == "Page" and "R-F2" in node["rules"]:
                    add("handled:r_f2", where(app, entry, None))
                if node["rule"] == "R-I7":
                    add("handled:r_i7", where(app, entry, *first))
                if node["rule"] == "R-I3b" and node["status"] != "abstain":
                    add("handled:r_i3b", where(app, entry, *first))
                if node["rule"] == "R-I2b" and node["type"] == "InlineConditional":
                    add("handled:r_i2b", where(app, entry, *first))
                if node["rule"] == "R-I8" and node["type"] == "Static":
                    add("handled:r_i8_guard", where(app, entry, *first))
                if node["rule"] == "R-I8" and node["type"] == "InlineConditional":
                    add("handled:r_i8_exit", where(app, entry, *first))
                if node["rule"] == "R-I8c":
                    add("handled:r_i8c", where(app, entry, *first))
                if node["type"] == "List" and "R-L3b" in node["rules"]:
                    add("handled:r_l3b", where(app, entry, *first))
                if node["rule"] == "R-L3":
                    checklist["r_l3"].append(where(app, entry, *first))
                if node["type"] == "Page" and node["rule"] == "R-I1":
                    checklist["page_guard"].append(where(app, entry, *first))

            for todo in stage5["c2todos"]:
                place = where(app, entry, todo["sourceLine"], todo["sourceCol"])
                if todo["kind"] == "unresolvedCondition":
                    text = cond_of.get(todo["nodeId"], "(no condExpr)")
                    conditions.setdefault(todo["reason"], {}).setdefault(text, []).append(place)
                elif todo["reason"] == "markup_not_generatable":
                    kind = ("fallback:container_shared" if "its container also holds" in todo["detail"] else
                            "fallback:if_prints_attribute" if "inside a tag, outside a value" in todo["detail"] else
                            "fallback:expression_in_attribute_name" if "tag name or attribute name" in todo["detail"]
                            else "fallback:markup_not_generatable")
                    add(kind, where(app, entry, None), f"{todo['function']}: {todo['detail']}")
                elif todo["reason"] in ("element_not_in_react_types", "expression_not_translatable",
                                        "dynamic_raw_text"):
                    add(f"fallback:{todo['reason']}", place, todo["detail"])
            for flag in stage5["flags"]:
                place = where(app, entry, flag["sourceLine"], flag["sourceCol"])
                if flag["flag"] == "attribute_omitted" and flag["reason"] in (
                        "condition_not_translatable", "condition_unavailable", "loose_compare_semantics"):
                    text = cond_of.get(flag["nodeId"], "(no condExpr)")
                    conditions.setdefault(flag["reason"], {}).setdefault(text, []).append(place)
                elif flag["flag"] == "abstained_code_not_generated":
                    add("fallback:abstained_code_not_generated", place, flag["detail"])
                elif f"flag:{flag['flag']}" in CATALOGUE:
                    add(f"flag:{flag['flag']}", place, flag["detail"])

            for e in timeline["sequence"]:
                for read in e.get("reads", []):
                    if read["sourceKind"] == "computed" and COPIED.fullmatch(read["expr"]):
                        add("handled:copy_propagation", where(app, entry, e["loc"]["startLine"],
                                                              e["loc"]["startCol"] + 1),
                            f"{read['expr']} = {read['derivedFrom'][0]['expr']}")
            for loop_line, condition in filters_in_loops(timeline):
                add("seen:filter_in_loop", where(app, entry, loop_line), f"`{condition}`")
            markup = any(e["kind"] == "Stmt_InlineHTML" and "<" in e["raw"] for e in timeline["sequence"])
            for e in timeline["sequence"]:  # a JSON answer printed by a unit that sends no Content-Type
                if e["kind"] == "Stmt_Echo" and "responseContentType" not in timeline and not markup and any(
                        r["expr"].replace(" ", "").lower().startswith("json_encode(") for r in e.get("reads", [])):
                    add("seen:json_echo", where(app, entry, e["loc"]["startLine"], e["loc"]["startCol"] + 1),
                        next(r["expr"] for r in e["reads"] if r["expr"].replace(" ", "").lower().startswith(
                            "json_encode(")))
            for note in timeline["harnessNotes"]:
                kind = f"note:{note['kind']}" + ("+markup" if note.get("markup") else "")
                if note["kind"] in ("echo_parts", "sql_interpolated_values"):
                    kind = f"handled:{note['kind']}" + ("+markup" if note.get("markup") else "")
                add(kind, where(app, entry, note["line"], note["col"]), " ".join(note["detail"].split())[:70])
            for call in timeline["sequence"]:  # call sites (Member 01 Q4): callee id, outputsInside
                if call["kind"] == "call" and call["outputsInside"] is not False:
                    name = timeline["callNames"].get(call["id"], "(a method or dynamic call)")
                    key = (app, name, "yes" if call["outputsInside"] is True else "unknown",
                           "one function" if call["callee"] else "not one function")
                    calls.setdefault(key, []).append(
                        where(app, entry, call["loc"]["startLine"], call["loc"]["startCol"] + 1))
            for include in timeline["includes"]:
                target = include["literal"]
                resolved = None
                if target is not None:
                    candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(entry["path"]), target)),
                                  posixpath.normpath(target)]
                    resolved = next((c for c in candidates if c in printing_pages), None)
                key = (app, target if target is not None else include["expr"], resolved is not None)
                includes.setdefault(key, []).append(where(app, entry, include["line"], include["col"]))
        for path, notes in sorted(index.get("silentFileNotes", {}).items()):  # files that print nothing
            for note in notes:
                add(f"note:{note['kind']}", where(app, {"path": path, "function": None}, note["line"], note["col"]),
                    " ".join(note["detail"].split())[:70])
    return {"found": found, "rules": rules, "calls": calls, "includes": includes, "conditions": conditions,
            "checklist": checklist, "summary": summary, "todos": todo_reasons}


def filters_in_loops(timeline: dict) -> list:
    """[(loop line, condition)] for every loop whose outputs all stand in the `then` arm of one if at the loop
    body's own level (boundary-rules section 8, filter-in-loop)."""
    by_loop: dict[str, list] = {}
    for entry in timeline["sequence"]:
        chain = entry["enclosedBy"]
        for depth, enclosure in enumerate(chain):
            if enclosure["role"] == "iteration":
                by_loop.setdefault(enclosure["nodeId"], []).append((enclosure, chain[depth + 1:]))
    out = []
    for loop_id, entries in by_loop.items():
        inner = [rest[0] if rest else None for _, rest in entries]
        if any(e is None or e["role"] != "branch" or e["branch"] != "then" for e in inner):
            continue
        if len({e["nodeId"] for e in inner}) == 1:
            loop = entries[0][0]
            out.append((loop.get("loc", {}).get("startLine"), " ".join(inner[0].get("condExpr", "?").split())[:60]))
    return sorted(out, key=lambda x: (x[0] or 0, x[1]))


def per_app(places: list, app: str) -> int:
    return sum(place.startswith(f"`{app}/") for place, _ in places)


# Phase 3 (boundary-rules v0.12 section 8): where each kind stands against the definition of a new pattern.
# class a = candidate new, b = existing pattern with new instances, d = data / labels / endpoint only.
PHASE3 = {
    "seen:if_prints_attribute": ("a", "NEW (owner, v0.13) → R-I3b",
        "`selected` / `checked` / `autofocus` on form controls, `class=\"nav-item\"` on menu items: conditional "
        "props now. No unit is lost to them any more."),
    "handled:r_i2b": ("a", "NEW (owner, v0.13) → R-I2b", "One conditional around both carousel Lists."),
    "handled:r_i8_guard": ("a", "NEW (owner, v0.13) → R-I8, redirect guard",
        "`echo '<meta http-equiv=\"refresh\" ...>'; exit;`: nothing rendered, flag `redirect_guard` (review)."),
    "handled:r_i8_exit": ("a", "NEW (owner, v0.13) → R-I8, exit arm", "`cond ? <arm> : <rest>`."),
    "abstain:non_html_response": ("a", "NEW (owner, v0.13) → `non_html_response`",
        "`rss.php`, `sitemap.php`: not generated, listed under `notGenerated`."),
    "abstain:exit_arm_not_covered": ("a", "R-I8, where its written text does not fit (reported to the owner)",
        "See the log entry."),
    "note:markup_in_assigned_string": ("b", "Not new (owner, v0.13): data flow, Member 01's DFG",
        "Stays `unresolved` / `markup_from_expression`."),
    "abstain:list_guard_multiple_loops": ("b", "R-I2 / R-I2b", "Loops in different arms of one `if`."),
    "abstain:cross_file_output": ("b", "§5 cross-file output (name accepted in v0.13)",
        "`core.php` (a redirect guard at its top level) and `config.php` (analysed from `install/config.tpl`: "
        "it prints through `printf` when the connection fails)."),
    "seen:nested_list": ("b", "Nested loops, R-L4 (first real cases, v0.13)",
        "The inner Item receives the outer row as `outer` where the inner loop's iterable or reads use it."),
    "seen:r_i5_other": ("b", "R-I5 fallback (existing rule)",
        "The rule fires as written. What changes is the report statement (rules §3): these are display "
        "conditionals above 3 elements (role checks, login state, settings switches), not request handlers."),
    "seen:r_i5_request": ("b", "Request-handling `if` (existing row, HMS)",
        "Same shape as HMS: `isset($_POST['...'])` / `isset($_GET['edit-id'])` around a form or a handler."),
    "abstain:unresolved_call": ("b", "Call whose output cannot be followed (call-entry table)",
        "Same reason as HMS's TCPDF calls. Causes since rules v0.14: mysqli method calls, and `include "
        "\"core.php\"`, whose top level, once its redirect guard counts as an exit arm (R-I8c), still makes a "
        "mysqli method call (`$querych->fetch_array()`) whose output is not known."),
    "handled:r_i8c": ("b", "R-I8c (v0.14)", "Exit arms in `head()`, `admin/header.php`, `core.php`, `config.php`."),
    "abstain:tag_crosses_branch": ("b", "Malformed HTML (R-L6, per-branch balance)", "More instances."),
    "abstain:markup_from_expression": ("b", "Echo strings with markup, rules §1",
        "`echo '<option ... ' . $selected . '>'`: an expression where an attribute name is written."),
    "abstain:partial_attribute_output": ("b", "§5 no rule matches (existing reason)", "One instance."),
    "fallback:container_shared": ("b", "Page output inside a List's container outside the loop", "More instances."),
    "fallback:expression_in_attribute_name": ("b", "Echo strings with markup, rules §1", "See the log entry."),
    "note:exit_with_output": ("b", "`die()` / `exit()` with a message", "One more case."),
    "flag:html_nesting_changed": ("b", "Invalid nesting the browser repairs", "More instances."),
    "note:row_read_not_traced": ("d", "data (Member 01 Q1, Q3, Q17)", "Unresolved reads; the DFG's job."),
}
PHASE3_CLASSES = {
    "a": "(a) New patterns (the owner's verdict, rules v0.13) and what each is now",
    "b": "(b) Existing patterns with new instances",
    "d": "(d) Unresolved reads, missing labels, no endpoint only",
}
GAPS_FIXED = (
    "An include / require is a timeline entry at its position (`{kind: \"include\", path, outputsInside}`, pending "
    "Member 01 Q4); an included file is a unit for R-F2, as the rule says (\"functions, or included files\").",
    "A file whose top level prints nothing itself but includes or calls something that certainly prints has a "
    "timeline (it was silent before).",
    "Two shell headers of one name (`head()` in `core.php` and in `install/core.php`): the second shell gets a "
    "numeric suffix (`HeadShell2`, route group `(head-2)`).",
    "A footer is paired with a header only if some timeline calls both: `core.php`'s `footer()` and "
    "`admin/footer.php` close the same tags.",
    "The end tag of a void element (`</meta>`) is not an unmatched closing tag of a function: it can close nothing.",
    "An `if` that prints only in its `else` / `elseif` arm: the `then` arm is written with no content and the "
    "condition `condition_unavailable` (it was a syntax error in the generated file).",
    "`print_r($x, true)` / `var_export($x, true)` return their text: no longer counted as output.",
    "`markup_from_expression` (rules §1) is applied inside R-L3 / R-L3b rows too.",
    "A C2Todo for a call site has its source line.",
)


# Attendance (roadmap Phase 3, app 4): the owner's verdict (rules v0.15 section 8): no new pattern, counter 1 / 2.
ATTENDANCE = {
    "seen:filter_in_loop": ("a", "Not new (owner, v0.15): \u00a78 \"filter-in-loop\" \u2192 now seen",
        "`while (...) { $i++; ... if ($i <= 1) { <tbody><tr>\u2026</tr>\u2026 } }`: the body prints only on the first "
        "pass. The loops abstain for an unclosed `<tbody>` opened in a branch; a rule would need loop-counter "
        "semantics (a guess)."),
    "abstain:try_catch_enclosure": ("a", "Not new (owner, v0.15): \u00a75 abstains; `try` now seen",
        "All 6 come from `$e->getMessage()` in a `catch` block: a method call whose output cannot be followed (Q11)."),
    "note:expression_exit": ("a", "Not new (owner, v0.15, a judgement call): normalised into R-I8c (rules \u00a71)",
        "`mysql_connect(...) or die('Cannot connect to server')` in the four included `connect.php`: since step 4 "
        "an R-I8c arm, flagged at every include (it was dropped silently before). A stricter reading would count "
        "it as new (counter 0 / 2); the report states both."),
    "note:output_buffering": ("a", "Not new (owner, v0.15): output identical",
        "`ob_start()` at the top of 10 pages; the buffer is flushed at the end, so the page prints the same."),
    "flag:header_redirect": ("a", "Not a boundary (owner, v0.15): `header_redirect` (info, spec v0.10)",
        "`header('location: \u2026')` without `exit` (no output): flagged for C1."),
    "abstain:container_undetermined@page": ("b", "Malformed HTML (R-L6)",
        "The loop body opens `<tbody>` and never closes it (`student/report.php`, `teacher/report.php`)."),
    "abstain:guarded_loop_abstained": ("b", "R-I2 list guard whose loop abstained", "The same two loops."),
    "abstain:tag_crosses_branch": ("b", "Malformed HTML (R-L6, per-branch balance)",
        "`if (isset($_POST['sr_date'])) { ?><table>\u2026<?php } ?></table>`: the table opens in the arm, closes after."),
    "seen:r_i5_request": ("b", "Request-handling `if` (existing row, HMS)", "`isset($_POST['reset'])`."),
    "fallback:container_shared": ("b", "Page output inside a List's container outside the loop", "More instances."),
    "flag:html_nesting_changed": ("b", "Invalid nesting the browser repairs",
        "Rows after a `<thead>` from a loop inside an `if` (not repaired across a branch), and rows outside any "
        "table in abstained code."),
    "note:row_read_not_traced": ("d", "data (Member 01 Q1, Q3, Q17)", "Unresolved reads; the DFG's job."),
}
ATTENDANCE_CLASSES = {
    "a": "(a) Candidates the owner judged (rules v0.15)",
    "b": "(b) Existing patterns with new instances",
    "d": "(d) Unresolved reads, missing labels, no endpoint only",
}
ATTENDANCE_GAPS = (
    "An abstained `try` / `switch` region gets its `Abstained_` function (spec section 7): the builder now reads "
    "the region's own content (it stopped with \"try_catch ... was not abstained\").",
    "Harness notes of a file that prints nothing (it has no timeline) are kept in `index.json` "
    "(`silentFileNotes`).",
    "Step 4 (rules v0.15): `X or die(m)` read as an if (\u00a71), so `connect.php` prints its messages in R-I8c arms "
    "and is a unit of its own (as phpBlog's `core.php`); built-in mysqli methods are not calls (\u00a74); "
    "`header_redirect` flags (spec v0.10). These follow rule text the owner wrote for them.",
)


# Warehouse (roadmap Phase 3, app 5, the reserve app): the owner's verdict (rules v1.0 section 8): no new pattern,
# counter 2 / 2, the rules are frozen. What each construct is after the step-5 changes.
WAREHOUSE = {
    "seen:json_echo": ("a", "Not new (owner, v1.0, a judgement call): \u00a75 `non_html_response`, subtype "
        "`ajax_endpoint`",
        "`ajax.php` 26, 63 `echo json_encode($html)`, called from `libs/js/functions.js` 15, 53: an AJAX endpoint, "
        "out of scope since CONTEXT v1. Not generated; the `notGenerated` entry names the two callers. A strict "
        "reading that ties `non_html_response` to a Content-Type header would count it as new; the report states "
        "both readings."),
    "abstain:cross_file_output": ("b", "\u00a75 cross_file_output (existing)",
        "The menu includes in `layouts/header.php` 59\u201367. The 37 includes of `includes/load.php`, whose only "
        "output is an empty line, are no longer here: whitespace-only included output is text at the include "
        "site (rules v1.0 \u00a74)."),
    "abstain:unresolved_call": ("d", "Data flow (Member 01 Q11)",
        "Calls through the `MySqli_DB` / `Session` objects (`$db->query`, `$session->msg`) and the helpers of "
        "`includes/sql.php` that use them. The include of `includes/load.php` is one of them: its six "
        "`LIB_PATH_INC.'\u2026'` includes now resolve (rules v1.0 \u00a74), and `session.php` calls `$session` "
        "methods at its top level."),
    "abstain:output_outside_shell": ("b", "R-F2 `output_outside_shell` (existing reason)",
        "A POST handler before `include_once('layouts/header.php')` (`edit_user.php`, before its line 67): its calls "
        "(`$session->msg`, `redirect`, `$db->query`) may print, so they are page output before the header. The "
        "cause is the unresolved calls above."),
    "note:markup_in_assigned_string": ("b", "Not new (owner, v0.13): data flow, Member 01's DFG",
        "`display_msg()` (`includes/functions.php`) builds `<div class=\"alert\">\u2026` in `$output` and returns it."),
    "note:output_buffering": ("b", "Not new (owner, v0.15)", "`ob_start()` in `index.php`, `login_v2.php`."),
    "flag:html_nesting_changed": ("b", "Invalid nesting the browser repairs", "More instances."),
    "seen:r_i5_request": ("b", "Request-handling `if` (existing row, HMS)", "More instances."),
    "fallback:container_shared": ("b", "Page output inside a List's container outside the loop", "More instances."),
    "note:row_read_not_traced": ("d", "data (Member 01 Q1, Q3, Q17)", "Unresolved reads; the DFG's job."),
}
WAREHOUSE_CLASSES = {
    "a": "(a) Candidates the owner judged (rules v1.0)",
    "b": "(b) Existing patterns with new instances",
    "d": "(d) Unresolved reads, missing labels, no endpoint only",
}
WAREHOUSE_GAPS = (
    "The app was supplied as a `git archive` and a sha256 manifest (the agent had no network): the harness reads "
    "the folder directly after checking all 63 files against `legacy-apps/warehouse-69fdd1c.sha256` "
    "(`tools/harness/app.py`, `verify_manifest`). An input change, not a rule change.",
    "Step 5 (rules v1.0, written by the owner for these cases): include paths built from constants resolve "
    "statically (\u00a74); an included file that prints only whitespace hands it to the spec \u00a75 whitespace rules at "
    "the include site (\u00a74); an AJAX endpoint is `non_html_response`, subtype `ajax_endpoint` (\u00a75).",
)
WAREHOUSE_DECIDED = ("the owner did (rules v1.0: no new pattern, saturation counter 2 / 2, **frozen**; the "
                     "`ajax_endpoint` reading is recorded as a judgement call). It shows what each construct is "
                     "after the step-5 changes.")


PHPBLOG_DECIDED = ("the owner did (rules v0.13: 4 new patterns, saturation counter 0 / 2). It shows what each "
                   "construct is after the new rules were implemented.")
ATTENDANCE_DECIDED = ("the owner did (rules v0.15: no new pattern, saturation counter 1 / 2; the `or die` "
                      "normalisation is recorded as a judgement call). It shows what each construct is after the "
                      "step-4 corrections.")


def phase3_section(number: int, app: str, data: dict, ordered: list, verdicts: dict = None, classes: dict = None,
                   gaps: tuple = GAPS_FIXED, decided: str | None = PHPBLOG_DECIDED) -> list:
    """One app's constructs against the section 8 definition of a new pattern (Phase 3)."""
    verdicts, classes = verdicts or PHASE3, classes or PHASE3_CLASSES
    found, todos = data["found"], data["todos"].get(app, {})
    lines = [f"## {number}. {app}: constructs against the §8 definition of a new pattern (Phase 3)", "",
             "boundary-rules §8 (v0.12): an app adds a new pattern if a construct (a) needs a rule or abstain reason "
             "that §2–§5 do not have, or (b) is handled by an existing rule wrongly. More instances of an existing "
             "row, an implementation gap for a written rule, and unresolved reads are not new. **This table does "
             "not decide**: " + (decided or "where there is doubt the construct is listed as a candidate, with "
                                 "the reason; the owner decides."), ""]
    for letter, heading in classes.items():
        rows = []
        for kind in ordered:
            places = [(p, d) for p, d in found[kind] if p.startswith(f"`{app}/")]
            if not places:
                continue
            _, title, mapping, _ = CATALOGUE.get(kind, ("B", kind, CANDIDATE, ""))
            default = "d" if mapping.startswith("data") else "b" if mapping.startswith("existing") else "a"
            cls, row, why = verdicts.get(kind, (default, mapping, ""))
            if kind.startswith(("handled:", "flag:implicit", "flag:duplicate", "flag:stray", "flag:implied",
                                "flag:exit_output")) and kind not in verdicts:
                continue  # handled by a rule or repair: counted in section 1
            if cls == letter:
                rows.append(f"| {title} — **{len(places)}**<br>{examples(places[:3])} | {row} | {why} |")
        lines += [f"### {heading}", ""]
        if letter == "d":
            lines += ["C2Todos by reason: " + ", ".join(
                f"`{reason}` {n}" for reason, n in sorted(todos.items(), key=lambda item: (-item[1], item[0]))) + ".",
                "", "`no_endpoint`, `unresolved_read`, `needs_request_context`, `needs_auth_context` and "
                "`condition_not_translatable` need the contract, the DFG or labels, not a boundary rule.", ""]
        if rows:
            lines += ["| Construct — count, examples | §8 row it belongs to, or NEW | Why (by the §8 definition) |",
                      "|---|---|---|", *rows, ""]
        elif letter != "d":
            lines += ["None.", ""]
    lines += ["### (c) Implementation gaps fixed for this app (not new patterns)", ""]
    lines += [f"- {text}" for text in gaps] + [""]
    return lines


def examples(places: list) -> str:
    shown = [p if not d else f"{p} — {d}" for p, d in places[:MAX_EXAMPLES]]
    more = len(places) - len(shown)
    return "<br>".join(shown) + (f"<br>… {more} more" if more > 0 else "")


def render(output: Path = OUTPUT, markdown: Path = DEFAULT_MARKDOWN) -> str:
    data = collect(output)
    found = data["found"]
    summary = data["summary"]
    lines = [
        "# Component 2 — Pattern log (whole-app harness run)",
        "",
        f"Generated by `tools/harness/pattern_log.py` from the outputs of `tools/harness/run_apps.py`, "
        f"{source(markdown)}. Apps: " + ", ".join(
            f"{app} @ `{summary[app]['sourceCommit'][:7]}` ({summary[app]['files']} files, "
            f"{sum(p['role'] != 'shell' for p in summary[app]['pages'])} timelines)" for app in APPS) + ".",
        "",
        "Every construct the pipeline **abstains on**, **cannot model**, or handles with a **last-resort fallback**, "
        "with its count and file:line examples, mapped to the pattern checklist in `boundary-rules.md` §8. This log "
        "records evidence for roadmap Phases 2 and 3. It writes no rule: the owner decides.",
        "",
        "Lines are PHP source lines (from `loc`); `?` means the node has no `loc` (an `if` or a loop). Input is the "
        "C2 harness, not Member 01's core.",
        "",
        "## 1. Summary",
        "",
        "| Group | Kind | " + " | ".join(APPS) + " | Total | §8 mapping |",
        "|---|---|" + "---|" * len(APPS) + "---|---|",
    ]
    ordered = [k for k in CATALOGUE if k in found] + sorted(k for k in found if k not in CATALOGUE and k != "crash")
    for kind in ordered:
        group, title, mapping, _ = CATALOGUE.get(kind, ("B", kind, CANDIDATE, ""))
        lines.append(f"| {group}. {GROUPS[group]} | {title} | "
                     + " | ".join(str(per_app(found[kind], app)) for app in APPS)
                     + f" | {len(found[kind])} | {mapping} |")
    new = [CATALOGUE.get(k, ("B", k, CANDIDATE))[1] for k in ordered
           if CATALOGUE.get(k, ("B", k, CANDIDATE))[2] == NEW]
    candidates = [CATALOGUE.get(k, ("B", k, CANDIDATE))[1] for k in ordered
                  if CATALOGUE.get(k, ("B", k, CANDIDATE))[2] == CANDIDATE]
    remaining = sum(len(found[k]) for k in ordered if CATALOGUE.get(k, ("B",))[0] != "E")
    handled = sum(len(found[k]) for k in ordered if CATALOGUE.get(k, ("B",))[0] == "E")
    lines += [
        "",
        f"**New patterns recorded in Phases 1–2 (not in §8): {len(new)}** — " + ("; ".join(new) if new else "none")
        + ".",
        "",
        f"**Candidate new patterns (Phase 3, for the owner to judge by the §8 definition): {len(candidates)}** — "
        + ("; ".join(candidates) if candidates else "none") + ".",
        "",
        f"Groups A–D are what remains ({remaining} places). Group E is what a rule or repair now handles ({handled} "
        "places): it is listed for the record, not as a gap.",
        "",
        f"Crashes: {len(found.get('crash', []))}. Parse errors: "
        f"{sum(len(summary[app]['parseErrors']) for app in APPS)}.",
        "",
    ]
    number = 2
    for group, heading in GROUPS.items():
        kinds = [k for k in ordered if CATALOGUE.get(k, ("B",))[0] == group]
        if not kinds:
            continue
        lines += [f"## {number}. {heading}", ""]
        number += 1
        for kind in kinds:
            _, title, mapping, text = CATALOGUE.get(kind, ("B", kind, NEW, ""))
            lines += [f"### {title} — {len(found[kind])}", "", f"- §8 mapping: {mapping}", f"- {text}",
                      f"- Examples: {examples(found[kind])}", ""]

    lines += phase3_section(number, "phpblog", data, ordered)
    number += 1
    lines += phase3_section(number, "attendance", data, ordered, ATTENDANCE, ATTENDANCE_CLASSES, ATTENDANCE_GAPS,
                            decided=ATTENDANCE_DECIDED)
    number += 1
    lines += phase3_section(number, "warehouse", data, ordered, WAREHOUSE, WAREHOUSE_CLASSES, WAREHOUSE_GAPS,
                            decided=WAREHOUSE_DECIDED)
    number += 1

    shells = [(app, s) for app in APPS for s in summary[app].get("shells", [])]
    lines += [f"## {number}. Functions and includes (R-F1 / R-F2)", "",
              "Shell pairs found (R-F2): " + ("; ".join(
                  f"{app}: `{s['name']}` in route group `{s['group']}`, props "
                  + (", ".join(f"`{p[0]}: {p[1]}`" for p in s["props"]) or "none") for app, s in shells) or "none")
              + ".", "",
              "Call sites are timeline entries `{kind: \"call\", callee, outputsInside}` (Member 01 Q4, proposed); "
              "nothing is inlined. *Callee* says whether the call is resolved to one function of the app "
              "(`callee` is its node id) or not (`callee` is null: a method call on an object, a dynamic call, or "
              "a name the app defines more than once). Calls whose callee prints nothing are not listed.", "",
              "| App | Called | Prints | Callee | Call sites | Examples | §8 mapping |", "|---|---|---|---|---|---|---|"]
    number += 1
    for (app, callee, prints, resolved), places in sorted(data["calls"].items()):
        mapping = ("existing: Function = component / Layout via functions / Cross-file output" if prints == "yes"
                   else "not known: the callee, or something it calls, cannot be followed (Member 01 Q11)")
        lines.append(f"| {app} | `{callee}` | {prints} | {resolved} | {len(places)} | "
                     f"{examples([(p, '') for p in places[:3]])} | {mapping} |")
    lines += ["", "| App | Included file | Prints at its top level | Includes | Examples | §8 mapping |",
              "|---|---|---|---|---|---|"]
    for (app, target, prints), places in sorted(data["includes"].items()):
        mapping = ("the file prints at its top level: half of a shell pair (R-F2), or `cross_file_output`"
                   if prints else "no pattern: the file defines functions / classes or sets up data, or is not "
                   "a file of the app")
        lines.append(f"| {app} | `{target}` | {'**yes**' if prints else 'no'} | {len(places)} | "
                     f"{examples([(p, '') for p in places[:3]])} | {mapping} |")

    check = data["checklist"]
    sizes = sorted({n for _, n in check["r_i5"]})
    lines += ["", f"## {number}. §8 \"not yet seen\" patterns, checked on whole apps", "",
              "| §8 pattern | Whole-app result | Examples |", "|---|---|---|",
              f"| Plain content `if` > 3 elements (R-I5 fallback) | **seen {len(check['r_i5'])} times** "
              f"(sizes {', '.join(str(s) for s in sizes) or '—'} elements) | "
              f"{examples([(p, f'{n} elements') for p, n in check['r_i5']])} |",
              f"| Nested loops (R-L4) | {'**seen ' + str(len(check['nested'])) + ' times**' if check['nested'] else 'not seen'} | "
              f"{examples([(p, '') for p in check['nested']])} |",
              "| Layout via `include header.php` | see the include table above | |",
              f"| Filter-in-loop / R-L3 rows | {len(check['r_l3'])} R-L3 rows | "
              f"{examples([(p, '') for p in check['r_l3']])} |",
              f"| Page guard (R-I1), for reference | {len(check['page_guard'])} | "
              f"{examples([(p, '') for p in check['page_guard']])} |",
              f"| C-style `for` / `do` loops, for reference | {len(set(check['for']))} / {len(set(check['do']))} output "
              f"lines | {examples([(p, '') for p in sorted(set(check['for']))[:3]])} |", ""]
    number += 1

    lines += [f"## {number}. Conditions Stage 5 could not translate", "",
              "Not a boundary pattern: the condition is outside the whitelist (generation-spec §6), so it becomes "
              "`unresolvedCondition()` or its attribute is omitted. Listed so the whitelist can be judged on real "
              "conditions.", "", "| Reason | Conditions | Distinct texts | Most common |", "|---|---|---|---|"]
    number += 1
    for reason, texts in sorted(data["conditions"].items()):
        ranked = sorted(texts.items(), key=lambda item: (-len(item[1]), item[0]))
        top = "<br>".join(f"`{' '.join(text.split())[:70]}` × {len(places)}" for text, places in ranked[:MAX_EXAMPLES])
        lines.append(f"| {reason} | {sum(len(p) for p in texts.values())} | {len(texts)} | {top} |")

    all_rules = sorted({rule for app in APPS for rule in data["rules"][app]})
    lines += ["", f"## {number}. Boundary rules that fired", "", "| Rule | " + " | ".join(APPS) + " |",
              "|---|" + "---|" * len(APPS)]
    for rule in all_rules:
        lines.append(f"| {rule} | " + " | ".join(str(data["rules"][app].get(rule, 0)) for app in APPS) + " |")
    lines += ["", "Counts are Stage 2 nodes carrying the rule.", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    markdown = Path(argv[0]) if argv else DEFAULT_MARKDOWN
    text = render(OUTPUT, markdown)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
