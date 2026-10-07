"""HTML tag tracking across a page's output nodes.

Tags are often split across output nodes by echo interpolations
(`<a href="x?id=` + echo + `">`), so one tracker runs over the whole kept
sequence in order; nothing is tracked per node.

For every output node it reports the open-tag stack just before it and
whether the node starts inside an open tag (attribute position), plus the tag
events the node causes. Unmatched closing tags are ignored and recorded
(boundary rule R-L6). The helpers below answer the questions Stage 2 asks
about a run of nodes: how many elements it has, and whether its tags balance.
"""

import re
from dataclasses import dataclass

from src.model import BoundaryConfig, OutputNode

DYNAMIC = "￼"  # stands in for output whose text is not known statically
RAWTEXT_TAGS = ("script", "style", "textarea", "title")  # their content is not markup
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9:-]*")
_ID_ATTR = re.compile(r"""(?:^|\s)id\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)


@dataclass(frozen=True)
class OpenTag:
    serial: int  # order of opening across the page; identifies the element
    tag: str  # lower-cased name
    element_id: str | None  # its id attribute, when that is static text
    opened_in: str  # the output node in which the opening tag starts


@dataclass(frozen=True)
class TagEvent:
    kind: str  # "open" | "void" | "close" | "unmatched_close"
    tag: str
    depth: int  # how many elements were open around it
    element: OpenTag | None = None  # open / void: the new element; close: the element closed
    implied: tuple[OpenTag, ...] = ()  # close: unclosed elements inside it that it closed too


@dataclass(frozen=True)
class NodeTags:
    node_id: str
    stack_before: tuple[OpenTag, ...]  # outermost first
    in_tag_before: bool  # the node starts inside an open tag: attribute position
    events: tuple[TagEvent, ...]
    stack_after: tuple[OpenTag, ...]
    in_tag_after: bool


class _Tracker:
    def __init__(self, void_tags):
        self.void = frozenset(void_tags)
        self.stack: list[OpenTag] = []
        self.serial = 0
        self.state = "text"  # text | lt | tag | close | bang | comment | rawtext | rawclose
        self.buf = ""
        self.quote = None  # the quote character of the attribute value being read
        self.raw_tag = None
        self.node = None  # node being fed
        self.tag_node = None  # node in which the tag being read started
        self.events: dict[str, list[TagEvent]] = {}

    def feed(self, node_id: str, text: str) -> None:
        self.node = node_id
        self.events.setdefault(node_id, [])
        for ch in text:
            self._char(ch)

    def _char(self, ch: str) -> None:
        state = self.state
        if state == "text":
            if ch == "<":
                self.state = "lt"
        elif state == "lt":
            if ch.isascii() and ch.isalpha():
                self.state, self.buf, self.quote, self.tag_node = "tag", ch, None, self.node
            elif ch == "/":
                self.state, self.buf, self.tag_node = "close", "", self.node
            elif ch in "!?":
                self.state, self.buf = "bang", ch
            else:  # a bare "<" in text
                self.state = "text"
                self._char(ch)
        elif state == "tag":
            if self.quote:
                if ch == self.quote:
                    self.quote = None
                self.buf += ch
            elif ch in "\"'":
                self.quote = ch
                self.buf += ch
            elif ch == ">":
                self._open(self.buf)
            else:
                self.buf += ch
        elif state == "close":
            if ch == ">":
                self.state = "text"
                self._close(self.buf.strip().lower())
            else:
                self.buf += ch
        elif state == "bang":  # <!DOCTYPE ...>, <![endif]>, <?xml ...>, or the start of a comment
            self.buf += ch
            if self.buf == "!--":
                self.state, self.buf = "comment", ""
            elif ch == ">":
                self.state = "text"
        elif state == "comment":
            self.buf = (self.buf + ch)[-3:]
            if self.buf == "-->":
                self.state = "text"
        elif state == "rawtext":
            end = "</" + self.raw_tag
            self.buf = (self.buf + ch.lower())[-len(end):]
            if self.buf == end:
                self.state = "rawclose"
        elif state == "rawclose":
            if ch == ">":
                self.state, self.tag_node = "text", self.node
                self._close(self.raw_tag)

    def _open(self, buf: str) -> None:
        name = _NAME.match(buf).group(0)
        rest = buf[len(name):]
        name = name.lower()
        found = _ID_ATTR.search(rest)
        element_id = next((g for g in found.groups() if g is not None), None) if found else None
        if element_id is not None and DYNAMIC in element_id:
            element_id = None
        element = OpenTag(self.serial, name, element_id, self.tag_node)
        self.serial += 1
        self.state = "text"
        if name in self.void or rest.rstrip().endswith("/"):
            self.events[self.tag_node].append(TagEvent("void", name, len(self.stack), element))
            return
        self.events[self.tag_node].append(TagEvent("open", name, len(self.stack), element))
        self.stack.append(element)
        if name in RAWTEXT_TAGS:
            self.state, self.raw_tag, self.buf = "rawtext", name, ""

    def _close(self, name: str) -> None:
        events = self.events[self.tag_node]
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == name:
                events.append(TagEvent("close", name, index, self.stack[index], tuple(self.stack[index + 1:])))
                del self.stack[index:]
                return
        events.append(TagEvent("unmatched_close", name, len(self.stack)))  # R-L6: ignored, but recorded


def _literal_text(expr: str) -> str:
    """The text a PHP string literal prints (enough for tag tracking)."""
    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
        return expr[1:-1].replace('\\"', '"').replace("\\'", "'")
    return expr


def output_text(node: OutputNode) -> str:
    """What the node prints, with DYNAMIC standing in for values not known statically."""
    if node.raw is not None:
        return node.raw
    parts = [_literal_text(r.expr) if r.source_kind == "literal" else DYNAMIC for r in node.reads]
    return "".join(parts) or DYNAMIC


def track(nodes: tuple[OutputNode, ...] | list[OutputNode], config: BoundaryConfig) -> dict[str, NodeTags]:
    """Tag state around every node, keyed by node id, in sequence order."""
    tracker = _Tracker(config.void_tags)
    snapshots = []
    for node in nodes:
        before = (tuple(tracker.stack), tracker.state == "tag")
        tracker.feed(node.id, output_text(node))
        snapshots.append((node.id, before, (tuple(tracker.stack), tracker.state == "tag")))
    return {
        node_id: NodeTags(node_id, before[0], before[1], tuple(tracker.events[node_id]), after[0], after[1])
        for node_id, before, after in snapshots
    }


# ----------------------------------------------------------------------------- questions about a run of nodes


def element_count(run: list[NodeTags]) -> int:
    """Number of HTML opening tags that start in these nodes (void elements included)."""
    return sum(e.kind in ("open", "void") for t in run for e in t.events)


def has_unmatched_close(run: list[NodeTags]) -> bool:
    return any(e.kind == "unmatched_close" for t in run for e in t.events)


def _opened(run: list[NodeTags]) -> set[int]:
    return {e.element.serial for t in run for e in t.events if e.kind == "open"}


def closes_outer(run: list[NodeTags]) -> bool:
    """True if these nodes close an element that was opened before them."""
    opened = _opened(run)
    return any(
        e.kind == "close" and (e.element.serial not in opened or any(x.serial not in opened for x in e.implied))
        for t in run for e in t.events
    )


def leaves_open(run: list[NodeTags]) -> bool:
    """True if an element or a tag opened in these nodes is still open after the last of them."""
    opened = _opened(run)
    return any(el.serial in opened for el in run[-1].stack_after) or run[0].in_tag_before != run[-1].in_tag_after


def is_balanced(run: list[NodeTags]) -> bool:
    """Every tag opened in these nodes closes in them, and they close nothing opened outside."""
    return not closes_outer(run) and not leaves_open(run)


def root_tags(run: list[NodeTags]) -> tuple[str, ...]:
    """Names of the elements these nodes open at their own top level."""
    depth = len(run[0].stack_before)
    return tuple(e.tag for t in run for e in t.events if e.kind in ("open", "void") and e.depth == depth)
