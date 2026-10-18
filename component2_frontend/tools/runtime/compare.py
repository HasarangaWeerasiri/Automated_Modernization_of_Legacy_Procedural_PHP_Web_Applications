"""Phase 4 runtime check: raw comparison of the captured legacy and generated DOMs (no classification: that is
Phase 4b).

Both captures are parsed with html5lib (the parser the nesting check already uses) and normalised the same way:
  - comments are dropped; `<script>` elements are dropped with their content (inline JS is out of scope; Next.js
    adds its own runtime scripts, which do not run with JavaScript disabled either);
  - text: whitespace runs collapse to one space, ends trimmed; a text that is only whitespace is no text;
  - attributes: sorted by name, values with whitespace collapsed and trimmed; a `style` value is written in a
    canonical form (each declaration `property:value` with the spaces around `:` and `;` removed, property names
    lower-cased as CSS reads them, empty declarations and the trailing `;` dropped, order kept), because React
    serialises the same declarations as `margin-top:50px` where the PHP page wrote `margin-top: 50px;`; the value
    of an HTML boolean attribute (`required`, `readonly`, `selected`, ...) is written "" (its presence is its
    meaning: `required="required"` and React's `required=""` are the same);
  - generated side only: the empty `<div hidden>` that Next.js puts first in every `<body>` (a streaming boundary
    holding two comments) is dropped; it is framework scaffolding, not page content, and would otherwise be
    aligned with the legacy page's first `<div>`.
Then the two `<body>` element trees are aligned: at each level the children are matched by the longest common
subsequence of their tag names, and matched pairs are aligned recursively. Per page it reports the elements in
the legacy body, in the generated body, the matched pairs, and every difference:
  missing     a legacy element (with its subtree) that has no counterpart
  extra       a generated element (with its subtree) that has no counterpart
  attributes  a matched pair whose attributes differ (the names that differ)
  text        a matched pair whose own text (its text and the tails of its children) differs
The output is deterministic: same captures, same report.

Usage, from component2_frontend/ (after tools/runtime/capture.py <app>):
    venv/Scripts/python.exe tools/runtime/compare.py hms
"""

import json
import re
import sys
from pathlib import Path

import html5lib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # component2_frontend/
OUTPUT = ROOT / "output" / "runtime"
DROPPED = ("script",)
# HTML Living Standard boolean attributes: only presence matters
BOOLEAN = frozenset(("allowfullscreen", "async", "autofocus", "autoplay", "checked", "controls", "default", "defer",
                     "disabled", "formnovalidate", "hidden", "inert", "ismap", "itemscope", "loop", "multiple",
                     "muted", "nomodule", "novalidate", "open", "playsinline", "readonly", "required", "reversed",
                     "selected"))
_SPACE = re.compile(r"[ \t\n\f\r]+")


def _text(value: str | None) -> str:
    return _SPACE.sub(" ", value or "").strip()


def _style(value: str) -> str:
    """A style attribute's declarations in a canonical form (same declarations, same order)."""
    declarations = []
    for declaration in value.split(";"):
        name, colon, rest = declaration.partition(":")
        if name.strip() or rest.strip():
            declarations.append(f"{name.strip().lower()}{colon}{_text(rest)}")
    return ";".join(declarations)


class Node:
    """A normalised element: tag, sorted attributes, own text, element children."""

    __slots__ = ("tag", "attrs", "text", "children")

    def __init__(self, tag: str, attrs: tuple, text: str, children: list):
        self.tag, self.attrs, self.text, self.children = tag, attrs, text, children

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children)


def _normalise(element) -> Node:
    pieces, children = [element.text or ""], []
    for child in element:
        if not isinstance(child.tag, str):  # a comment: dropped, its tail kept
            pieces.append(child.tail or "")
            continue
        if child.tag in DROPPED:
            pieces.append(child.tail or "")
            continue
        children.append(_normalise(child))
        pieces.append(child.tail or "")
    attrs = tuple(sorted((name, _style(value) if name == "style" else "" if name in BOOLEAN else _text(value))
                         for name, value in element.attrib.items()))
    return Node(element.tag, attrs, _text(" ".join(pieces)), children)


def head(html: str) -> list:
    """The elements of the document's <head>, normalised like the body (React 19 moves some there)."""
    document = html5lib.parse(html, treebuilder="etree", namespaceHTMLElements=False)
    found = document.find("head")
    return [] if found is None else _normalise(found).children


def body(html: str, generated: bool = False) -> Node:
    document = html5lib.parse(html, treebuilder="etree", namespaceHTMLElements=False)
    found = document.find("body")
    node = _normalise(found) if found is not None else Node("body", (), "", [])
    first = node.children[0] if node.children else None
    if generated and first is not None and (first.tag, first.attrs, first.text, first.children) == (
            "div", (("hidden", ""),), "", []):
        node.children = node.children[1:]  # Next.js's streaming boundary
    return node


def _score(legacy: "Node", generated: "Node", memo: dict) -> tuple[int, int]:
    """(1 if their attributes are identical, the elements their best alignment matches: 1 + their children's)."""
    key = (id(legacy), id(generated))
    if key not in memo:
        memo[key] = (int(legacy.attrs == generated.attrs), 1 + _table(legacy.children, generated.children, memo)[0][0][1])
    return memo[key]


def _plus(a: tuple, b: tuple) -> tuple:
    return a[0] + b[0], a[1] + b[1]


def _table(left: list, right: list, memo: dict) -> list:
    """The weighted LCS table of two lists of sibling elements: only same-tag elements pair; the best alignment has
    first the most attribute-identical pairs among these siblings, then the most matched elements in their subtrees
    (Phase 4e: a missing sibling no longer shifts every later pairing, and an element pairs with its own counterpart
    before a merely similar one)."""
    n, m = len(left), len(right)
    table = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(table[i + 1][j], table[i][j + 1])
            if left[i].tag == right[j].tag:
                best = max(best, _plus(_score(left[i], right[j], memo), table[i + 1][j + 1]))
            table[i][j] = best
    return table


def _lcs(left: list, right: list, memo: dict | None = None) -> list[tuple[int, int]]:
    """Index pairs of the sibling alignment with the most attribute-identical pairs, then the most matched elements
    (deterministic: earliest pairs)."""
    memo = {} if memo is None else memo
    table = _table(left, right, memo)
    pairs, i, j, n, m = [], 0, 0, len(left), len(right)
    while i < n and j < m:
        if left[i].tag == right[j].tag and _plus(_score(left[i], right[j], memo), table[i + 1][j + 1]) == table[i][j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _step(path: str, siblings: list, index: int) -> str:
    tag = siblings[index].tag
    return f"{path}>{tag}[{sum(s.tag == tag for s in siblings[:index])}]"


def align(legacy: Node, generated: Node, path: str = "body", gpath: str = "body",
          matches: list | None = None, memo: dict | None = None) -> tuple[int, list]:
    """(matched element pairs under and including this pair, differences). `path` is the legacy element's path,
    `gpath` the generated one's; `matches`, when given, collects every matched (legacy path, generated path)."""
    matched, differences, memo = 1, [], {} if memo is None else memo
    if matches is not None:
        matches.append((path, gpath))
    if legacy.attrs != generated.attrs:
        names = sorted({n for n, _ in legacy.attrs} ^ {n for n, _ in generated.attrs}
                       | {n for (n, v) in legacy.attrs if (n, v) not in generated.attrs and n in dict(generated.attrs)})
        differences.append({"kind": "attributes", "path": path, "names": names,
                            "legacy": dict(legacy.attrs), "generated": dict(generated.attrs)})
    if legacy.text != generated.text:
        differences.append({"kind": "text", "path": path, "legacy": legacy.text, "generated": generated.text})
    pairs = _lcs(legacy.children, generated.children, memo)
    paired_l, paired_g = {i for i, _ in pairs}, {j for _, j in pairs}
    for i, child in enumerate(legacy.children):
        if i not in paired_l:
            differences.append({"kind": "missing", "path": _step(path, legacy.children, i), "elements": child.size()})
    for j, child in enumerate(generated.children):
        if j not in paired_g:
            differences.append({"kind": "extra", "path": _step(path, generated.children, j),
                                "generatedPath": _step(gpath, generated.children, j), "elements": child.size()})
    for i, j in pairs:
        sub_matched, sub_differences = align(legacy.children[i], generated.children[j],
                                             _step(path, legacy.children, i), _step(gpath, generated.children, j),
                                             matches, memo)
        matched += sub_matched
        differences += sub_differences
    return matched, differences


def find(node: Node, path: str) -> Node | None:
    """The element at a path such as body>div[0]>p[1] (as align writes it), or None."""
    for step in path.split(">")[1:]:
        tag, index = step[:-1].split("[")
        same = [c for c in node.children if c.tag == tag]
        if int(index) >= len(same):
            return None
        node = same[int(index)]
    return node


def compare(legacy_html: str, generated_html: str, matches: list | None = None) -> dict:
    legacy, generated = body(legacy_html), body(generated_html, generated=True)
    matched, differences = align(legacy, generated, matches=matches)
    order = {"missing": 0, "extra": 1, "attributes": 2, "text": 3}
    differences.sort(key=lambda d: (d["path"], order[d["kind"]]))
    return {"legacyElements": legacy.size() - 1, "generatedElements": generated.size() - 1,
            "matched": matched - 1, "differences": differences}  # the <body> pair itself is not counted


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    out = OUTPUT / argv[0]
    capture = json.loads((out / "capture.json").read_text(encoding="utf-8"))
    pages = []
    redirected = []
    for row in capture["pages"]:
        if not row["captured"]:
            continue
        moved = {k: v for k, v in row.items() if k.endswith(("FinalUrl", "Refresh"))}
        if moved:  # one side sent the browser elsewhere: not the same page any more (capture.py)
            redirected.append({"page": row["page"], **({"unit": row["unit"]} if "unit" in row else {}),
                               "getOnly": row["getOnly"], **moved})
            continue
        legacy = (out / "legacy" / f"{row['page']}.html").read_text(encoding="utf-8")
        generated = (out / "generated" / f"{row['page']}.html").read_text(encoding="utf-8")
        pages.append({"page": row["page"], **({"unit": row["unit"]} if "unit" in row else {}),
                      "getOnly": row["getOnly"], **compare(legacy, generated)})
    totals = {key: sum(p[key] for p in pages) for key in ("legacyElements", "generatedElements", "matched")}
    totals["differences"] = sum(len(p["differences"]) for p in pages)
    report = {"app": argv[0], "normalisation": {
        "comments": "dropped", "script": "elements dropped with their content",
        "text": "whitespace collapsed, trimmed", "attributes": "sorted, values whitespace-collapsed",
        "style": "declarations in a canonical form (spaces around : and ; removed, trailing ; dropped, order kept)",
        "boolean": "HTML boolean attributes valued \"\" (presence is their meaning)",
        "nextjs": "the empty <div hidden> first in the generated <body> (streaming boundary) dropped",
        "alignment": "body element trees; siblings of the same tag paired to give first the most attribute-identical "
                     "pairs, then the most matched elements (a weighted LCS, recursively)"},
        "totals": totals, "pages": pages, "redirected": redirected}
    (out / "compare.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{'page':38} {'legacy':>7} {'gen':>6} {'match':>6} {'diffs':>6}")
    for p in pages:
        print(f"{p['page'] + (' *' if p['getOnly'] else ''):38} {p['legacyElements']:7} {p['generatedElements']:6} "
              f"{p['matched']:6} {len(p['differences']):6}")
    print(f"{'total':38} {totals['legacyElements']:7} {totals['generatedElements']:6} {totals['matched']:6} "
          f"{totals['differences']:6}   (* get_only)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
