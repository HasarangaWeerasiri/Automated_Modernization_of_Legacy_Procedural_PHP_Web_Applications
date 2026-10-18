"""Phase 4b: give every difference compare.py found exactly one class (output/runtime/<app>/classified.json).

  flag:<kind>   explained by a flag the pipeline emitted AT THAT NODE: a node that printed the differing element's
                start tag (or a part of it), or the function component called inside that element
  flag-but-wrong:<kind>
                a flag exists at that node, but the generated tree contradicts the browser's tree (Phase 4c, owner):
                the flag claims the generated tree is the one a browser builds (an HTML5 repair or close: spec
                section 5b, R-L6), or drops `<head>` content that the browser's tree has in `<body>`. Counted
                separately: such a difference is a wrong result, not an explained gap
  framework:react_hoist
                React 19 renders `<meta>`, `<title>` and non-stylesheet `<link>` elements into the document's <head>,
                wherever the component tree has them (owner, Phase 4e): a missing body element of those kinds whose
                exact copy (tag and attributes) is in the generated page's <head>. Explained, not a generator error,
                counted separately (it changes nothing visible)
  env:<reason>  caused by the run, not the tool (get_only pages, a legacy runtime error, no replay data)
  norm:<reason> an artefact of the comparison itself
  UNEXPLAINED   none of the above; its root cause comes from the reviewed notes (tools/runtime/<app>/findings.json)

How a difference is tied to a node: each unit's timeline is laid out as the text it prints (inline HTML as written,
an echo's literal parts as written, an expression part as one placeholder), every span keeping its node id (a part
keeps its part id), and call / include entries as markers. The differing element's start tag is found in that text
by its own attribute values (the occurrence matching the most of them; ties must all agree); the nodes overlapping
it are "the nodes that printed it". A flag counts only if it is on one of those nodes and its detail names the
differing attribute (or, for a missing / extra element, it is a markup flag that drops or moves elements). Content
printed by a function component (no static start tag of its own) is tied to the call inside the nearest identified
ancestor, and the flag must be in that component. Anything else is UNEXPLAINED: a flag elsewhere on the page never
explains a difference.

Fallback (Phase 4e, phpBlog): when the differing element cannot be found by its own attribute values (its only
attribute is printed by an expression, or it has none), the nearest ancestor that can be found is searched: a gap flag of
the right kind on a node printed inside that ancestor (a C2Todo / unresolvedCondition for text, an attribute flag naming
the same attribute and tag) explains it, marked `via` that ancestor (weaker evidence than the node itself).

Which rules produced the run (`rules` in the folder's lists.json, written by generated_app.py; "1.0" when absent,
as for the runs made before the switch) decides which reviewed findings apply: each finding names the rules it was
found under. So does the database the legacy app ran on (`--data`, default "seed": the app's shipped seed; Phase 4f:
"fixture", phpBlog's recorded fixture): a difference path depends on the data (a carousel that renders shifts its
siblings), so a finding names the data it was reviewed on (`data`: one name or a list, default "seed").

Usage, from component2_frontend/ (after tools/runtime/compare.py <app>):
    venv/Scripts/python.exe tools/runtime/classify.py hms [--folder hms-4a] [--before hms-4a] [--data seed]
(`--before`: the same pages generated without the data wiring, to tell List rows from static elements; default
<app>-4a, the Phase 4a capture.)
"""

import collections
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare  # noqa: E402

ROOT = HERE.parents[1]  # component2_frontend/
OUTPUT = ROOT / "output" / "runtime"
PLACEHOLDER = "\x00"
# markup flags that drop or move elements (spec sections 5, 5b; R-L6)
STRUCTURAL = {("document_shell", "<head> and its content dropped"), ("implicit_close", None),
              ("unmatched_close", None), ("html_nesting_changed", None), ("implicit_p_close", None),
              ("implied_end_tag", None), ("stray_end_tag_repaired", None), ("output_dropped", None)}
# Flags that say the generated tree IS the browser's (spec section 5b repairs, R-L6 "browsers ignore it", an element
# closed by an end tag): a difference at their node contradicts what they claim.
EQUIVALENCE = {"implicit_close", "unmatched_close", "implicit_p_close", "implied_end_tag", "stray_end_tag_repaired",
               "implicit_tbody", "duplicate_attribute"}
HEAD_DROP = ("document_shell", "<head> and its content dropped")


# ---------------------------------------------------------------- the units of a page and their printed text


class Unit:
    """One generated unit: its printed text with node spans, its call markers, and its flags by node id."""

    def __init__(self, key: str, folder: Path):
        self.key = key
        timeline = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
        stage5 = json.loads((folder / "stage5.json").read_text(encoding="utf-8"))
        self.text, self.spans, self.calls = "", [], []  # spans: (start, end, node id); calls: (offset, callee)
        self.loops = {}  # node id (and part ids) -> the ids of the loops enclosing it
        self.ifs = {}  # node id (and part ids) -> the ids of the ifs enclosing it
        self.arms = {}  # node id (and part ids) -> [(if id, branch)] of the ifs enclosing it
        for entry in timeline["sequence"]:
            loops = [e["nodeId"] for e in entry["enclosedBy"] if e["role"] == "iteration"]
            ifs = [e["nodeId"] for e in entry["enclosedBy"] if e["role"] == "branch"]
            arms = [(e["nodeId"], e.get("branch")) for e in entry["enclosedBy"] if e["role"] == "branch"]
            for node_id in [entry["id"]] + [part["nodeId"] for part in entry.get("parts", [])]:
                self.loops[node_id], self.ifs[node_id], self.arms[node_id] = loops, ifs, arms
            if entry["kind"] in ("call", "include"):
                self.calls.append((len(self.text), entry.get("callee") or entry.get("path")))
            elif entry["kind"] == "Stmt_InlineHTML":
                self._add(entry["raw"], entry["id"])
            elif entry.get("parts"):
                for part in entry["parts"]:
                    self._add(part["value"] if part["kind"] == "literal" else PLACEHOLDER, part["nodeId"])
                self.spans.append((self.spans[-len(entry["parts"])][0], len(self.text), entry["id"]))
            else:
                self._add(PLACEHOLDER, entry["id"])
        self.flags = collections.defaultdict(list)
        for flag in stage5["flags"] + stage5["c2todos"]:
            for node_id in flag["nodeId"].split(","):
                self.flags[node_id].append(flag)
        self.function = timeline.get("function", {}).get("nodeId")

    def _add(self, text: str, node_id: str) -> None:
        self.spans.append((len(self.text), len(self.text) + len(text), node_id))
        self.text += text

    def nodes(self, start: int, end: int) -> set:
        return {node_id for s, e, node_id in self.spans if s < end and e > start}


def start_tags(text: str, tag: str):
    """(start, end) of every start tag `<tag ...>` in printed text (quotes respected)."""
    for found in re.finditer(rf"<{re.escape(tag)}(?=[\s/>{PLACEHOLDER}])", text, re.I):
        quote, index = None, found.end()
        while index < len(text):
            ch = text[index]
            if quote:
                quote = None if ch == quote else quote
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                yield found.start(), index + 1
                break
            index += 1


def _has(tag_text: str, name: str, value: str) -> bool:
    if name == "style":  # compared in the canonical form compare.py gives it
        found = re.search(r"""style\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag_text, re.I)
        return found is not None and compare._style(found.group(1) or found.group(2) or "") == value
    if value == "" or name in compare.BOOLEAN:
        return re.search(rf"[\s{PLACEHOLDER}]{re.escape(name)}(?=[\s=/>])", tag_text, re.I) is not None
    for quoted in (f'"{value}"', f"'{value}'", value):
        if re.search(rf"{re.escape(name)}\s*=\s*{re.escape(quoted)}(?=[\s/>{PLACEHOLDER}\"'])", tag_text, re.I):
            return True
    return False


def _tokens(tag_text: str, name: str, value: str) -> bool:
    """A weaker match (Phase 4e): every word of the value appears in the start tag's text (a value partly printed by
    an if or an expression, e.g. `class="<?php if (...) echo 'container'; ?> mt-3"`)."""
    words = value.split()
    return bool(words) and re.search(rf"[\s{PLACEHOLDER}]{re.escape(name)}\s*=", tag_text, re.I) is not None \
        and all(w in tag_text for w in words)  # (an if's arms are printed next to each other: no word boundaries)


def _score(tag_text: str, attrs, weak: bool) -> int:
    return sum((_tokens if weak else _has)(tag_text, n, v) for n, v in attrs)


def locate(unit: Unit, element: compare.Node) -> list:
    """(start, end) of the start tag(s) in the unit's text that printed `element`: the occurrences of its tag
    matching the most of its attribute values (at least one, unless it has none and the tag occurs once). When no
    occurrence matches a value exactly, the values are matched word by word."""
    found = _locate(unit, element, weak=False)
    return found if found or not element.attrs else _locate(unit, element, weak=True)


def _locate(unit: Unit, element: compare.Node, weak: bool) -> list:
    scored = []
    for start, end in start_tags(unit.text, element.tag):
        tag_text = unit.text[start:end]
        scored.append((_score(tag_text, element.attrs, weak), start, end))
    if not scored:
        return []
    best = max(s for s, _, _ in scored)
    if best == 0 and (element.attrs or len(scored) > 1):
        return []
    return [(start, end) for s, start, end in scored if s == best]


# ---------------------------------------------------------------- classification


def _detail_names(flag: dict, attr: str, tag: str) -> bool:
    """Whether a flag's detail is about this attribute of this tag."""
    detail = flag["detail"]
    if (flag.get("flag") or flag["kind"]) == "document_shell":
        return detail.startswith(f"<{tag} ") and f" {attr}" in detail.split(">")[0] + " "
    return detail.startswith(f"{attr} ") and f"<{tag}>" in detail or detail.startswith(f"second {attr} on <{tag}>")


def flag_id(unit: Unit, flag: dict) -> dict:
    return {"unit": unit.key, "nodeId": flag["nodeId"], "kind": flag.get("flag") or flag["kind"],
            "reason": flag["reason"], "detail": flag["detail"], "file": flag["file"], "line": flag["line"],
            "sourceLine": flag.get("sourceLine")}


def tag_nodes(units: list, element: compare.Node) -> list:
    """[(unit, node ids)] of the occurrences of the element's start tag that match the most of its attribute values
    across all the units together (a weaker match in another unit is not the element); ties must all be found. When no
    occurrence matches a value exactly, the values are matched word by word."""
    found = _tag_nodes(units, element, weak=False)
    return found if found or not element.attrs else _tag_nodes(units, element, weak=True)


def _tag_nodes(units: list, element: compare.Node, weak: bool) -> list:
    scored = []
    for unit in units:
        for start, end in start_tags(unit.text, element.tag):
            tag_text = unit.text[start:end]
            scored.append((_score(tag_text, element.attrs, weak), unit, start, end))
    if not scored:
        return []
    best = max(s for s, _, _, _ in scored)
    if best == 0 and (element.attrs or len(scored) > 1):
        return []
    return [(unit, unit.nodes(start, end)) for s, unit, start, end in scored if s == best]


def region(units, element: compare.Node) -> list:
    """[(unit, node ids)] printed between the start tag of `element` and its end tag, for each located occurrence."""
    out = []
    for unit in units:
        for start, end in locate(unit, element):
            close = unit.text.lower().find(f"</{element.tag}", end)
            out.append((unit, unit.nodes(start, close if close >= 0 else len(unit.text))))
    return out


def via_ancestor(units, tree, path: str, wanted) -> dict | None:
    """The nearest located ancestor's region: the first flag `wanted(flag)` accepts on a node printed inside it."""
    for ancestor in ancestors_of(tree, path):
        found = region(units, ancestor)
        if not found:
            continue
        for unit, nodes in found:
            for node in sorted(nodes):
                for f in unit.flags.get(node, []):
                    if wanted(f):
                        return flag_id(unit, f) | {"via": f"inside <{ancestor.tag}> {dict(ancestor.attrs)}"}
        return None  # the nearest located ancestor decides
    return None


def explain_attributes(units, element, names, tree=None, path: str = "") -> dict | None:
    found = tag_nodes(units, element)
    if not found:
        if tree is None or not names:
            return None
        return via_ancestor(units, tree, path, lambda f: all(_detail_names(f, n, element.tag) for n in names[:1]))
    explained = []
    for attr in names:
        hits = []
        for unit, nodes in found:
            # an if inside the start tag (R-I3 / R-I3b) printed the attribute through its arms: its flags count too
            printing = set(nodes) | {i for node in nodes for i in unit.ifs.get(node, [])}
            hit = next((flag_id(unit, f) for node in sorted(printing) for f in unit.flags.get(node, [])
                        if _detail_names(f, attr, element.tag)), None)
            hits.append(hit)
        if not hits or any(h is None for h in hits):  # every tied occurrence must carry it
            return None
        explained.append(hits[0])
    return explained[0] | ({"also": explained[1:]} if len(explained) > 1 else {})


def in_head(unit: Unit, element: compare.Node) -> bool:
    """Whether the element's start tag stands between `<head` and `</head>` in the printed text."""
    text = unit.text.lower()
    for start, _ in locate(unit, element):
        opened, closed = text.rfind("<head", 0, start), text.rfind("</head", 0, start)
        if opened >= 0 and opened > closed and not text.startswith("<header", opened):
            return True
    return False


def explain_structure(units, elements: list, missing: bool) -> dict | None:
    """A missing / extra element: a structural markup flag on a node that printed it or, failing that, a relative.
    Dropping `<head>` explains only a missing element whose start tag stands inside that `<head>`. A missing element
    printed inside a List's loop is explained by that List's collection C2Todo (no data for it)."""
    if missing and elements:
        for unit, nodes in tag_nodes(units, elements[0]):
            for loop in sorted({loop for node in nodes for loop in unit.loops.get(node, [])}):
                gap = next((f for f in unit.flags.get(loop, []) if f["kind"] == "C2Todo"
                            and f["detail"] == "collection"), None)
                if gap is not None:
                    return flag_id(unit, gap)
    if missing and elements:  # an element printed inside an if whose condition was not translated
        for unit, nodes in tag_nodes(units, elements[0]):
            found = unresolved_guard(unit, nodes)
            if found is not None:
                return found
    for element in elements:
        for unit, nodes in tag_nodes(units, element):
            for node in sorted(nodes):
                for f in unit.flags.get(node, []):
                    kind = f.get("flag") or f["kind"]
                    if (kind, f["detail"]) == ("document_shell", "<head> and its content dropped"):
                        if missing and element is elements[0] and in_head(unit, element):
                            return flag_id(unit, f)
                    elif (kind, None) in STRUCTURAL:
                        return flag_id(unit, f)
    return None


def preorder(node: compare.Node) -> list:
    out = [node]
    for child in node.children:
        out += preorder(child)
    return out


def explain_by_order(units, tree: compare.Node, path: str) -> dict | None:
    """A missing element with nothing to match it by (e.g. a bare <tr> of a List): its nearest ancestor whose tag
    occurs in the legacy page exactly as often as its static start tags occur in one unit's printed text is tied to
    the start tag at the same position; a List's collection C2Todo on a loop printing inside that element explains
    it. Equal counts are required: otherwise nothing is tied."""
    steps = path.split(">")
    for n in range(len(steps) - 1, 0, -1):
        ancestor = compare.find(tree, ">".join(steps[:n]))
        same = [e for e in preorder(tree) if e.tag == ancestor.tag]
        position = next(i for i, e in enumerate(same) if e is ancestor)
        for unit in units:
            occurrences = list(start_tags(unit.text, ancestor.tag))
            if len(occurrences) != len(same):
                continue
            start, end = occurrences[position]
            close = unit.text.lower().find(f"</{ancestor.tag}", end)
            nodes = unit.nodes(end, close if close >= 0 else len(unit.text))
            for loop in sorted({loop for node in nodes for loop in unit.loops.get(node, [])}):
                gap = next((f for f in unit.flags.get(loop, []) if f["kind"] == "C2Todo"
                            and f["detail"] == "collection"), None)
                if gap is not None:
                    return flag_id(unit, gap) | {"via": f"<{ancestor.tag}> {position + 1} of {len(same)}"}
            return None
    return None


def explain_by_call(units, by_function, ancestors: list) -> dict | None:
    """Content with no static start tag: the call inside the nearest identified ancestor, and a gap flag in the
    called component (its List's collection C2Todo)."""
    for ancestor in ancestors:
        for unit, start, end in ((u, s, e) for u in units for s, e in locate(u, ancestor)):
            close = unit.text.lower().find(f"</{ancestor.tag}", end)
            for offset, callee in unit.calls:
                if end <= offset <= (close if close >= 0 else len(unit.text)) and callee in by_function:
                    component = by_function[callee]
                    gap = next((f for fs in component.flags.values() for f in fs
                                if f["kind"] == "C2Todo" and f["detail"] == "collection"), None)
                    if gap is not None:
                        return flag_id(component, gap) | {"via": f"call in <{ancestor.tag}> of {unit.key}"}
            return None
    return None


def unresolved_guard(unit: Unit, nodes) -> dict | None:
    """An `unresolvedCondition` (spec section 6: the content it guards never renders) on an if enclosing these
    nodes."""
    for node in sorted(nodes):
        for if_id in unit.ifs.get(node, []):
            gap = next((f for f in unit.flags.get(if_id, []) if f["kind"] == "unresolvedCondition"), None)
            if gap is not None:
                return flag_id(unit, gap)
    return None


def explain_text(units, legacy_text: str, generated_text: str, tree=None, path: str = "") -> dict | None:
    """Text printed with an expression the pipeline left as a gap: a C2Todo / flag on a part of the node whose
    literal text is the generated text. Text the generated page lacks altogether: a node whose literal text is in
    it, inside an if whose condition was not translated (unresolvedCondition). Failing both, the element's (or its
    nearest located ancestor's) region holds a C2Todo / unresolvedCondition (Phase 4e)."""
    found = _explain_text(units, legacy_text, generated_text)
    if found is None and tree is not None:
        element = compare.find(tree, path)
        gap = lambda f: f["kind"] in ("C2Todo", "unresolvedCondition")  # noqa: E731
        for unit, nodes in region(units, element) if element is not None and element.attrs else []:
            for node in sorted(nodes):
                for f in unit.flags.get(node, []):
                    if gap(f):
                        return flag_id(unit, f)
        found = via_ancestor(units, tree, path + ">x[0]", gap)  # (its ancestors, from the element itself)
    return found


def _explain_text(units, legacy_text: str, generated_text: str) -> dict | None:
    if not generated_text:
        wanted = compare._text(legacy_text)
        for unit in units:
            for start, end, node in unit.spans:
                literal = compare._text(html.unescape(unit.text[start:end].replace(PLACEHOLDER, " ")))
                found = unresolved_guard(unit, {node}) if literal and literal in wanted else None
                if found is not None:
                    return found
        return None
    for unit in units:
        for start, end, node in unit.spans:
            literal = html.unescape(unit.text[start:end].replace(PLACEHOLDER, ""))
            if PLACEHOLDER in unit.text[start:end] and compare._text(literal) == generated_text:
                for part in sorted(unit.nodes(start, end)):
                    for f in unit.flags.get(part, []):
                        if f["kind"] == "C2Todo" or (f.get("flag") or "") == "attribute_omitted":
                            return flag_id(unit, f)
    return None


def ancestors_of(tree: compare.Node, path: str) -> list:
    steps = path.split(">")
    return [compare.find(tree, ">".join(steps[:n])) for n in range(len(steps) - 1, 0, -1)]


def descendants(node: compare.Node) -> list:
    out = []
    for child in node.children:
        out.append(child)
        out += descendants(child)
    return out


def defaulted_arm(units, element: compare.Node | None, legacy: compare.Node | None = None) -> dict | None:
    """The generated element was printed in an arm that renders only because the condition of its if was not
    translated (unresolvedCondition is false, so an `else` / later arm renders on every request), and the legacy page
    did not take that arm (its counterpart, when there is one, was printed elsewhere): that flag."""
    if element is None:
        return None
    legacy_arms = {arm for unit, nodes in (tag_nodes(units, legacy) if legacy is not None else [])
                   for node in nodes for arm in unit.arms.get(node, [])}
    for unit, nodes in tag_nodes(units, element):
        for node in sorted(nodes):
            for if_id, branch in unit.arms.get(node, []):
                if branch == "then" or (if_id, branch) in legacy_arms:
                    continue
                gap = next((f for f in unit.flags.get(if_id, []) if f["kind"] == "unresolvedCondition"), None)
                if gap is not None:
                    return flag_id(unit, gap)
    return None


def contradiction(found: dict, difference: dict) -> str | None:
    """Why a flag that explains this difference is a wrong result (flag-but-wrong), or None. Only the body is
    compared, so a missing element is always one the browser built inside `<body>`."""
    kind = found["kind"]
    if (kind, found["detail"]) == HEAD_DROP and difference["kind"] == "missing":
        return "dropped as <head> content, but the browser's tree has it in <body>"
    if kind in EQUIVALENCE:
        return f"{kind} says the generated tree is the one a browser builds; the browser's tree differs here"
    return None


# What Next.js itself writes into every page's <head>: a copy of one of these there proves nothing by itself
NEXT_HEAD = ((("charset", "utf-8"),), (("content", "width=device-width, initial-scale=1"), ("name", "viewport")))


def react_hoisted(element: compare.Node, generated_head: list) -> bool:
    """React 19 always renders these into <head> (document metadata): <meta> without itemprop, <title>, and a <link>
    that is not a stylesheet. The generated <head> must hold the element: once if Next.js does not write it itself,
    twice if it does (Next.js's own and the hoisted one)."""
    attrs = dict(element.attrs)
    kinds = element.tag in ("meta", "title") or (element.tag == "link" and "stylesheet" not in attrs.get("rel", ""))
    copies = sum((h.tag, h.attrs, h.text) == (element.tag, element.attrs, element.text) for h in generated_head)
    own = element.tag == "meta" and element.attrs in NEXT_HEAD
    return kinds and "itemprop" not in attrs and copies > (1 if own else 0)


def classify_page(page: dict, legacy_tree, generated_tree, units, by_function, notes: dict,
                  generated_head: list = (), pairs: dict | None = None) -> list:
    out = []
    for difference in page["differences"]:
        kind, path = difference["kind"], difference["path"]
        if kind == "missing" and react_hoisted(compare.find(legacy_tree, path), generated_head):
            out.append(difference | {"class": "framework:react_hoist",
                                     "evidence": "the same element is in the generated page's <head> (React 19)"})
            continue
        if kind != "missing":  # the generated side of the difference
            gpath = difference.get("generatedPath") or (pairs or {}).get(path)
            arm = defaulted_arm(units, compare.find(generated_tree, gpath) if gpath else None,
                                None if kind == "extra" else compare.find(legacy_tree, path))
            if arm is not None:
                out.append(difference | {"class": "flag-but-wrong:unresolvedCondition", "flag": arm,
                                         "contradiction": "an arm rendered on every request because its untranslated "
                                                          "condition is false; the browser took another arm"})
                continue
        found = None
        if kind == "attributes":
            found = explain_attributes(units, compare.find(legacy_tree, path), difference["names"], legacy_tree, path)
        elif kind == "text":
            found = explain_text(units, difference["legacy"], difference["generated"], legacy_tree, path)
        else:
            tree = legacy_tree if kind == "missing" else generated_tree
            element = compare.find(tree, path if kind == "missing" else difference["generatedPath"])
            relatives = [element] + [d for d in descendants(element) if d.attrs] + [
                a for a in ancestors_of(tree, path if kind == "missing" else difference["generatedPath"]) if a.attrs]
            found = explain_by_call(units, by_function, [element] + ancestors_of(
                tree, path if kind == "missing" else difference["generatedPath"])) if not tag_nodes(
                units, element) else None
            found = found or explain_structure(units, relatives, kind == "missing")
            if found is None and kind == "missing":
                found = explain_by_order(units, tree, path)
        note = notes.get((page["page"], path)) or notes.get((page["page"], "*"))
        wrong = contradiction(found, difference) if found is not None else None
        if wrong is not None:
            out.append(difference | {"class": f"flag-but-wrong:{found['kind']}", "flag": found, "contradiction": wrong})
        elif found is not None:
            out.append(difference | {"class": f"flag:{found['kind']}", "flag": found})
        elif note is not None and note["class"] != "UNEXPLAINED":
            out.append(difference | {"class": note["class"], "evidence": note["rootCause"]})
        else:
            out.append(difference | {"class": "UNEXPLAINED",
                                     "rootCause": note["rootCause"] if note else "not analysed yet"})
    return out


# ---------------------------------------------------------------- list rows (for the metrics)


def row_paths(before_html: str, after_html: str) -> set:
    """Generated paths of the elements the data wiring added (the List rows): elements of the generated page with
    data that the same page without data does not have."""
    before, after = compare.body(before_html, generated=True), compare.body(after_html, generated=True)
    found = set()
    for difference in compare.align(after, before)[1]:  # after as "legacy": its unmatched elements are rows
        if difference["kind"] == "missing":
            root = compare.find(after, difference["path"])
            found.add(difference["path"])
            stack = [(difference["path"], root)]
            while stack:
                path, node = stack.pop()
                for index, child in enumerate(node.children):
                    child_path = compare._step(path, node.children, index)
                    found.add(child_path)
                    stack.append((child_path, child))
    return found


def _names(value, default: str) -> list:
    return value if isinstance(value, list) else [value or default]


def reviewed_notes(path: Path, rules: str, data: str) -> dict:
    """{(page, path): finding} of the reviewed findings that apply to a run of these rules on this data."""
    findings = json.loads(path.read_text(encoding="utf-8"))["findings"] if path.exists() else []
    return {(n["page"], n["path"]): n for n in findings
            if rules in _names(n.get("rules"), "1.0") and data in _names(n.get("data"), "seed")}


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("--"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    app = argv[0]
    folder = argv[argv.index("--folder") + 1] if "--folder" in argv else app  # e.g. hms-4a: before the wiring
    out = OUTPUT / folder
    pipeline = out / "pipeline" / app
    # the same pages generated without the data wiring: no List rows there (Phase 4a, or a --no-data build)
    before = OUTPUT / (argv[argv.index("--before") + 1] if "--before" in argv else f"{app}-4a")
    index = json.loads((pipeline / "index.json").read_text(encoding="utf-8"))
    report = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    lists_file = out / "lists.json"
    rules = json.loads(lists_file.read_text(encoding="utf-8")).get("rules", "1.0") if lists_file.exists() else "1.0"
    data = argv[argv.index("--data") + 1] if "--data" in argv else "seed"  # the legacy database's contents
    notes = reviewed_notes(HERE / app / "findings.json", rules, data)
    units = {e["key"]: Unit(e["key"], pipeline / e["key"]) for e in index["pages"]
             if (pipeline / e["key"] / "stage5.json").exists()}
    by_function = {u.function: u for u in units.values() if u.function}
    pages, totals = [], collections.Counter()
    for page in report["pages"]:
        legacy_html = (out / "legacy" / f"{page['page']}.html").read_text(encoding="utf-8")
        generated_html = (out / "generated" / f"{page['page']}.html").read_text(encoding="utf-8")
        legacy_tree, generated_tree = compare.body(legacy_html), compare.body(generated_html, generated=True)
        unit = units[page.get("unit", page["page"])]  # Phase 4g: a parameterised GET of a page unit
        # a page inside a shell (R-F2): the shell's header and footer printed part of it (Phase 4e, phpBlog)
        entry = next(e for e in index["pages"] if e["key"] == unit.key)
        shell = [units[e["key"]] for e in index["pages"] if e["role"] == "shell" and e.get("group")
                 and e["group"] == entry.get("group") and e["key"] in units]
        called = [by_function[c] for u in [unit] + shell for _, c in u.calls if c in by_function]
        matches = []
        compare.compare(legacy_html, generated_html, matches)
        differences = classify_page(page, legacy_tree, generated_tree, [unit] + shell + called, by_function, notes,
                                    compare.head(generated_html), dict(matches))
        # list rows: legacy elements matched to a row the wiring added, and missing legacy elements whose gap is a
        # List's collection (no data for it)
        # (the generated pages do not read the query string: a parameterised GET's baseline is its unit's page)
        baseline = next((before / "generated" / f"{key}.html" for key in (page["page"], unit.key)
                         if (before / "generated" / f"{key}.html").exists()), None)
        rows = row_paths(baseline.read_text(encoding="utf-8"), generated_html) \
            if out != before and baseline is not None else set()
        list_matched = sum(1 for _, g in matches[1:] if g in rows)
        list_missing = sum(d.get("elements", 0) for d in differences
                           if d["kind"] == "missing" and d.get("flag", {}).get("detail") == "collection")
        by_class = collections.Counter(d["class"] for d in differences)
        row = {"page": page["page"], **({"unit": page["unit"]} if "unit" in page else {}), "getOnly": page["getOnly"], "legacyElements": page["legacyElements"],
               "generatedElements": page["generatedElements"], "matched": page["matched"],
               "renderMatch": round(page["matched"] / page["legacyElements"], 4) if page["legacyElements"] else None,
               "listElements": list_matched + list_missing, "listMatched": list_matched,
               "staticElements": page["legacyElements"] - list_matched - list_missing,
               "staticMatched": page["matched"] - list_matched,
               "byClass": dict(sorted(by_class.items())), "unexplained": by_class.get("UNEXPLAINED", 0),
               "flagButWrong": sum(n for c, n in by_class.items() if c.startswith("flag-but-wrong:")),
               "framework": sum(n for c, n in by_class.items() if c.startswith("framework:")),
               "differences": differences}
        pages.append(row)
        for key in ("legacyElements", "generatedElements", "matched", "listElements", "listMatched",
                    "staticElements", "staticMatched", "unexplained", "flagButWrong", "framework"):
            totals[key] += row[key]
        for name, count in by_class.items():
            totals["class:" + name] += count
    totals_out = {k: v for k, v in totals.items() if not k.startswith("class:")}
    totals_out["renderMatch"] = round(totals["matched"] / totals["legacyElements"], 4)
    totals_out["byClass"] = dict(sorted((k[6:], v) for k, v in totals.items() if k.startswith("class:")))
    result = {"app": app, "rules": rules, "data": data,
              "classes": {"flag": "a flag the pipeline emitted at that node",
                          "flag-but-wrong": "a flag at that node, but the generated tree contradicts the browser's",
                          "framework": "the target framework renders the generated tree elsewhere (React 19 hoisting)",
                          "env": "the run", "norm": "the comparison", "UNEXPLAINED": "none of these; root cause noted"},
              "totals": totals_out, "pages": pages}
    (out / "classified.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                         newline="\n")
    print(f"rules v{rules}, data {data}")
    print(f"{'page':24} {'legacy':>6} {'match':>6} {'render':>7} {'list':>9} {'static':>9} {'unexpl':>6} {'wrong':>5}"
          "  classes")
    for p in pages:
        render = f"{p['renderMatch']:.3f}" if p["renderMatch"] is not None else "  -  "
        print(f"{p['page'] + (' *' if p['getOnly'] else ''):24} {p['legacyElements']:6} {p['matched']:6} {render:>7} "
              f"{p['listMatched']:4}/{p['listElements']:<4} {p['staticMatched']:4}/{p['staticElements']:<4} "
              f"{p['unexplained']:6} {p['flagButWrong']:5}  {p['byClass']}")
    t = totals_out
    print(f"{'total':24} {t['legacyElements']:6} {t['matched']:6} {t['renderMatch']:.3f} "
          f"{t['listMatched']:4}/{t['listElements']:<4} {t['staticMatched']:4}/{t['staticElements']:<4} "
          f"{t['unexplained']:6} {t['flagButWrong']:5}  {t['byClass']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
