"""HTML nesting check for Stage 5's output (docs/generation-spec.md v0.2 section 10).

A browser does not keep invalid nesting as written: `<form>` directly in a
`<table>` is re-parented, a `<div>` in a `<tr>` is moved in front of the
table, a `<tr>` directly in a `<table>` gets a `<tbody>`. The legacy page went
through that parser; React renders its element tree as written (and on a
client-side navigation builds the DOM without any parser). tsc cannot see
this, so the generated element tree is checked here:

  1. the tree is serialised to HTML (attributes left out, every text and data
     expression written as the placeholder text "x", C2Todo as nothing);
  2. html5lib (an HTML5-compliant parser, pinned in requirements.txt) parses
     it as body content;
  3. the parsed element tree is compared with ours, parent by parent. Where a
     parent's children differ, that parent is reported with its path and both
     child lists; its subtree is not compared further.

Only element structure and the position of non-whitespace text are compared.
"""

from dataclasses import dataclass, field

import html5lib

HTML_SPACE = " \t\n\f\r"
PLACEHOLDER = "x"
TEXT = "#text"


@dataclass(eq=False)
class Node:
    """One element of the tree to check, with what the caller needs to place a flag on it."""

    tag: str
    children: list = field(default_factory=list)  # Node or TEXT
    label: str = ""  # tag plus #id when the element has a static id
    owner: object = None  # opaque: the caller's handle on the generated element


@dataclass(frozen=True)
class Difference:
    path: str  # e.g. "div#app-hist > table"; "(top level)" for the root
    ours: tuple[str, ...]  # the children as generated
    parsed: tuple[str, ...]  # the children after an HTML5 parser
    owner: object  # the parent's owner (None at the top level)


def serialise(nodes: list, void_tags: frozenset) -> str:
    out = []
    for node in nodes:
        if node == TEXT:
            out.append(PLACEHOLDER)
            continue
        out.append(f"<{node.tag}>")
        if node.tag not in void_tags:
            out.append(serialise(node.children, void_tags))
            out.append(f"</{node.tag}>")
    return "".join(out)


def _children(element) -> list:
    """An etree element's children as tags and TEXT markers (non-whitespace text only)."""
    out = []
    if element.text and element.text.strip(HTML_SPACE):
        out.append(TEXT)
    for child in element:
        if isinstance(child.tag, str):
            out.append(child)
        if child.tail and child.tail.strip(HTML_SPACE):
            out.append(TEXT)
    return _merge_text(out)


def _merge_text(items: list) -> list:
    out = []
    for item in items:
        if item == TEXT and out and out[-1] == TEXT:
            continue
        out.append(item)
    return out


def _names(items: list) -> tuple[str, ...]:
    return tuple(TEXT if item == TEXT else item.tag for item in items)


def compare(nodes: list, void_tags: frozenset) -> list[Difference]:
    """Every place where an HTML5 parser would build a different tree from `nodes`."""
    # A whole document, not parseFragment: in fragment mode html5lib drops foster-parented content (a <div>
    # in a <tr>) instead of moving it in front of the table, which would hide exactly what this check is for.
    document = html5lib.parse(f"<!DOCTYPE html><html><head></head><body>{serialise(nodes, void_tags)}</body></html>",
                              treebuilder="etree", namespaceHTMLElements=False)
    parsed = document.find("body")
    differences: list[Difference] = []

    def walk(ours: list, theirs: list, path: list, owner) -> None:
        ours = _merge_text(ours)
        if _names(ours) != _names(theirs):
            differences.append(Difference(" > ".join(path) or "(top level)", _names(ours), _names(theirs), owner))
            return
        for mine, other in zip(ours, theirs):
            if mine != TEXT:
                walk(mine.children, _children(other), path + [mine.label or mine.tag], mine.owner)

    walk(nodes, _children(parsed), [], None)
    return differences
