"""Write docs/php-analysis/pattern-log.md: what the pipeline could not handle on the whole apps.

The evidence for roadmap Phases 2 and 3. From the outputs of tools/harness/run_apps.py it lists
every construct the pipeline abstains on, cannot model, or handles with a last-resort fallback,
grouped by kind, each with its count and file:line examples and mapped to the pattern checklist in
docs/php-analysis/boundary-rules.md section 8 (an existing pattern, or new). It records; it does not
decide: no rule is written here.

Reads only output/apps/<app>/ (index.json and each page's timeline.json, stage2.json, stage5.json).
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
from results_table import source  # noqa: E402

OUTPUT = ROOT / "output" / "apps"
DEFAULT_MARKDOWN = ROOT / "docs" / "php-analysis" / "pattern-log.md"
APPS = ("hms", "wackopicko")
MAX_EXAMPLES = 6
NEW = "**new**"
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
        "A", "Loop in a function whose container is opened by the caller",
        "existing: Cross-file output (function echoes `<option>` into a page's `<select>`)",
        "The function's timeline has no open element at the loop, so R-L2a finds no container. Waits on R-F1 "
        "(Member 01 Q4)."),
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
        "D", "Nesting a browser repairs", "existing: Malformed HTML",
        "The generated tree differs from what an HTML5 parser builds (e.g. `<tr>` directly in `<table>`, a block "
        "inside `<p>`)."),
    "flag:duplicate_attribute_dropped": (
        "D", "Duplicate attribute", "existing: Malformed HTML", "The second one is dropped, as HTML does."),
    "flag:unsupported_style": ("D", "Style attribute Stage 5 cannot convert", NEW, "The attribute is omitted."),
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
    "E": "Handled since Phase 2a (no longer a gap)",
}


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
    summary = {}

    def add(kind, place, detail=""):
        found.setdefault(kind, []).append((place, detail))

    for app in APPS:
        index = json.loads((output / app / "index.json").read_text(encoding="utf-8"))
        printing_pages = {p["path"] for p in index["pages"] if p["function"] is None}
        summary[app] = index
        for entry in index["pages"]:
            if entry["crash"]:
                add("crash", where(app, entry, None), entry["crash"]["error"])
                continue
            folder = output / app / entry["key"]
            timeline = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
            stage2 = json.loads((folder / "stage2.json").read_text(encoding="utf-8"))
            stage5 = json.loads((folder / "stage5.json").read_text(encoding="utf-8"))
            line_of = {e["id"]: e["loc"] for e in timeline["sequence"]}
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
                    add(kind, where(app, entry, *first))
                if node["type"] == "ConditionalComponent":
                    checklist["r_i5"].append((where(app, entry, *first), node["elements"]))
                if node["type"] == "List" and any(p["type"] == "Item" for p in parents):
                    checklist["nested"].append(where(app, entry, *first))
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
                    shared = "its container also holds" in todo["detail"]
                    add("fallback:container_shared" if shared else "fallback:markup_not_generatable", place,
                        todo["detail"])
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
    return {"found": found, "rules": rules, "calls": calls, "includes": includes, "conditions": conditions,
            "checklist": checklist, "summary": summary}


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
            f"{len(summary[app]['pages'])} timelines)" for app in APPS) + ".",
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
        "| Group | Kind | Count | §8 mapping |",
        "|---|---|---|---|",
    ]
    ordered = [k for k in CATALOGUE if k in found] + sorted(k for k in found if k not in CATALOGUE and k != "crash")
    for kind in ordered:
        group, title, mapping, _ = CATALOGUE.get(kind, ("B", kind, NEW, ""))
        lines.append(f"| {group}. {GROUPS[group]} | {title} | {len(found[kind])} | {mapping} |")
    new = [CATALOGUE.get(k, ("B", k, NEW))[1] for k in ordered if CATALOGUE.get(k, ("B", k, NEW))[2] == NEW]
    remaining = sum(len(found[k]) for k in ordered if CATALOGUE.get(k, ("B",))[0] != "E")
    handled = sum(len(found[k]) for k in ordered if CATALOGUE.get(k, ("B",))[0] == "E")
    lines += [
        "",
        f"**New patterns (not in §8): {len(new)}** — " + ("; ".join(new) if new else "none") + ".",
        "",
        f"Groups A–D are what remains ({remaining} places). Group E is what Phase 2a now handles ({handled} "
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

    lines += [f"## {number}. Functions and includes (evidence for R-F1 / R-F2)", "",
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
        mapping = ("§8 \"not yet seen\": layout via `include header.php` → **now seen**" if prints
                   else "no pattern: the file defines functions / classes or sets up data")
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
    lines += ["", "Counts are Stage 2 nodes carrying the rule. R-F1 and R-F2 are not implemented (Phase 2).", ""]
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
