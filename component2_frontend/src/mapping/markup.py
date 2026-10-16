"""A page's output as one markup tree, for Stage 5.

The timeline gives output statements in order: inline HTML, echoes and prints,
each with its enclosing loops and ifs. Tags are often split across them
(`<a href="x?id=` + echo + `">`), so one tokenizer reads the whole page in
order and builds a single tree of:

  MElement   an HTML element (tag lower-cased), its attributes and children
  MText      character data, as written (entities are decoded later)
  MDynamic   an echo/print whose value is not a literal
  MLoop      a loop, holding what its body prints
  MIf        an if, one MBranch per then/elseif/else arm
  MAbstain   a region Stage 2 abstained on: opaque here, built on its own

An attribute value is a list of parts: text, MDynamic, or MAttrIf (an if
inside the value, boundary rule R-I3).

The tree follows the same simple model as Stage 2's tag tracker
(src/mapping/tags.py): a closing tag closes the nearest open element with
that name; an unmatched closing tag is dropped and flagged (R-L6); a `/>`
closes any element; script, style, textarea and title hold raw text. A loop
or an if must open and close its own tags (Stage 2 abstains otherwise), so
each becomes one node. Built on its own (`strict=False`, for an abstained
region), elements still open at the end of a branch are closed there and
flagged instead.

Anything the tokenizer cannot place (output inside a tag name, a loop inside
an attribute value, a tracker disagreement) raises GenerationError: Stage 5
then replaces the part with a C2Todo instead of guessing.
"""

import re
from dataclasses import dataclass, field, replace

from src.mapping import tags as tagtrack
from src.mapping.stage2_boundaries import _If, _Loop, _Other, _regions
from src.model import BoundaryConfig, ComponentNode, Enclosure, OutputNode

RAWTEXT_TAGS = tagtrack.RAWTEXT_TAGS
HTML_SPACE = " \t\n\f\r"
_IN_TAG = ("tag_name", "before_attr_name", "attr_name", "after_attr_name", "before_attr_value", "value_dq",
           "value_sq", "value_unq", "after_attr_value", "self_closing")
_ATTR_VALUE = ("value_dq", "value_sq", "value_unq")
_BETWEEN_ATTRIBUTES = ("before_attr_name", "after_attr_name", "after_attr_value")
# HTML5 "special" elements: a start tag looking for an open <li> / <dd> / <dt> to close stops at one of them,
# except address, div and p (section 13.2.6.4.7, "in body").
_SPECIAL = frozenset((
    "applet", "area", "article", "aside", "base", "basefont", "bgsound", "blockquote", "body", "br", "button",
    "caption", "center", "col", "colgroup", "dd", "details", "dir", "dl", "dt", "embed", "fieldset", "figcaption",
    "figure", "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup",
    "hr", "html", "iframe", "img", "input", "keygen", "li", "link", "listing", "main", "marquee", "menu", "meta",
    "nav", "noembed", "noframes", "noscript", "object", "ol", "param", "plaintext", "pre", "script", "search",
    "section", "select", "source", "style", "summary", "table", "tbody", "td", "template", "textarea", "tfoot", "th",
    "thead", "title", "tr", "track", "ul", "wbr", "xmp"))
# Closing one of these makes a browser open it again in what follows ("reconstruct the active formatting
# elements"): the tree is then not simply the one with the element closed, so no repair is made.
_FORMATTING = frozenset(("a", "b", "big", "code", "em", "font", "i", "nobr", "s", "small", "strike", "strong",
                         "tt", "u"))
_IMPLIED_END = {"li": ("li",), "dd": ("dd", "dt"), "dt": ("dd", "dt")}  # start tag -> the open elements it ends
# Spec v0.14 section 5 (post-freeze change 2): what stays inside <head>. Any other element, or text that is not
# whitespace, ends <head> in every browser (HTML5 "in head", anything else) and is body content.
HEAD_CONTENT = frozenset(("base", "link", "meta", "noscript", "script", "style", "template", "title"))
HEADINGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))


class GenerationError(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


@dataclass(eq=False)
class MText:
    text: str
    node_id: str
    parent: object = None


@dataclass(eq=False)
class MDynamic:
    output: OutputNode  # for one part of an echo: that echo with this part's read only
    parent: object = None
    part: object = None  # the EchoPart, when this is one expression of an echo that has parts


@dataclass(eq=False)
class MCall:
    """The place of a call that may print, or of a shell's children: what stands there is Stage 5's to write."""

    node: OutputNode  # the slot's pseudo node (kind "call" or "children")
    parent: object = None


@dataclass(eq=False)
class MAttrIf:
    if_id: str
    branches: list  # of (Enclosure, parts)


@dataclass(eq=False)
class MAttrGap:
    """Something written inside a start tag that cannot be generated (spec v0.8 section 5b, start-tag fallback):
    the attribute it belongs to is left out and flagged; the element and its children are kept. Stands in a
    tag's attribute list (between attributes) or among the parts of one attribute value."""

    node_id: str
    reason: str
    name: str = ""
    parts: list = None  # itself: never static text, so whatever asks "is this attribute literal?" says no

    def __post_init__(self):
        self.parts = [self]


@dataclass(eq=False)
class MAttrPresence:
    """An if between the attributes of a start tag whose every arm prints complete literal attributes (R-I3b)."""

    if_id: str
    branches: list  # of (Enclosure, [MAttr])
    name: str = ""
    parts: list = None

    def __post_init__(self):
        self.parts = [self]

    @property
    def node_id(self) -> str:
        return self.if_id


@dataclass(eq=False)
class MAttr:
    name: str  # lower-cased
    parts: list | None  # str / MDynamic / MAttrIf; None for an attribute written without "="
    node_id: str


@dataclass(eq=False)
class MElement:
    tag: str  # lower-cased
    attrs: list
    node_id: str  # the output node in which its opening tag starts
    children: list = field(default_factory=list)
    content: list | None = None  # raw-text elements: their text and MDynamic parts
    parent: object = None
    end: str = "open"  # how it was closed: "explicit" (its own end tag) | "implied" (an outer end tag) | "open"
    clean: bool = True  # nothing was still open inside it when it closed (void elements: always)


@dataclass(eq=False)
class MLoop:
    enclosure: Enclosure
    children: list = field(default_factory=list)
    parent: object = None


@dataclass(eq=False)
class MBranch:
    enclosure: Enclosure
    children: list = field(default_factory=list)
    parent: object = None


@dataclass(eq=False)
class MIf:
    if_id: str
    branches: list = field(default_factory=list)  # of MBranch
    parent: object = None


@dataclass(eq=False)
class MAbstain:
    node: ComponentNode  # the Stage 2 Abstain node
    region: object  # the _Loop / _If / _Other it stands for
    parent: object = None


@dataclass
class MarkupFlag:
    flag: str
    status: str
    node_id: str
    detail: str
    anchor: object = None  # the MElement it happened in (None: the top level)


@dataclass
class Markup:
    children: list
    loops: dict  # loop id -> MLoop
    ifs: dict  # if id -> MIf
    flags: list


@dataclass(eq=False)
class _Frame:
    """A region's branch on the open-element stack: closing tags never reach past it."""

    owner: object  # MLoop / MBranch
    children: list


def region_id(item) -> str:
    if isinstance(item, OutputNode):  # an output node Stage 2 abstained on by itself
        return item.id
    return item.node_id if isinstance(item, _If) else item.enclosure.node_id


def php_literal_text(expr: str) -> str | None:
    """What a PHP literal prints, or None when that is not certain (interpolation, rare escapes, floats)."""
    if len(expr) >= 2 and expr[0] == expr[-1] == "'":
        return re.sub(r"\\([\\'])", r"\1", expr[1:-1])
    if len(expr) >= 2 and expr[0] == expr[-1] == '"':
        inner = expr[1:-1]
        if "$" in inner.replace("\\$", ""):
            return None
        simple = {"n": "\n", "t": "\t", "r": "\r", "v": "\v", "f": "\f", "e": "\x1b", "\\": "\\", "$": "$", '"': '"'}
        out, i = [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                nxt = inner[i + 1]
                if nxt in simple:
                    out.append(simple[nxt])
                    i += 2
                    continue
                if nxt in "01234567xu":
                    return None
            out.append(inner[i])
            i += 1
        return "".join(out)
    if re.fullmatch(r"0|[1-9]\d*", expr):
        return expr
    return None


class Builder:
    """Builds the markup tree of a run of timeline items (see the module docstring)."""

    def __init__(self, outputs: list[OutputNode], config: BoundaryConfig, abstain: dict[str, ComponentNode],
                 strict: bool = True, stray: bool = False, opaque: frozenset = frozenset()):
        self.void = frozenset(config.void_tags)
        self.abstain_by_source = abstain
        self.strict = strict
        # Spec v0.7 section 5b: a stray </p> or </br> is repaired only while every element that may be open is
        # known. That ends at an abstained region, at a shell's children, and at a slot named in `opaque`.
        self.certain = strict and stray
        self.opaque = opaque
        # Spec v0.14 (rules v1.1): <head> content and heading end tags as a browser builds them
        self.browser_v014 = config.rules_version != "1.0"
        self.tracker = tagtrack.track(outputs, config)
        self.root: list = []
        self.stack: list = []  # MElement / _Frame
        self.state = "data"
        self.node = None  # id of the output node being read
        self.text, self.text_node = [], None
        self.tag = None  # the MElement being opened
        self.tag_name, self.tag_node = "", None
        self.attr = None
        self.end_name = ""
        self.decl = ""
        self.comment = ""
        self.raw_end = ""
        self.loops, self.ifs, self.flags = {}, {}, []

    # ---- entry points

    def build(self, items: list) -> Markup:
        self.walk(items)
        if self.state != "data":
            raise GenerationError("markup_not_generatable", f"output ends inside markup (state {self.state})")
        self.flush_text()
        while self.stack:
            entry = self.stack.pop()
            if isinstance(entry, _Frame):
                raise GenerationError("markup_not_generatable", "region left open")
            entry.clean = False  # never closed: nothing is certain about what it holds
            self.flag("unclosed_at_end", "info", entry.node_id, f"<{entry.tag}> is never closed; closed at the end",
                      self.stack[-1] if self.stack and isinstance(self.stack[-1], MElement) else None)
        return Markup(self.root, self.loops, self.ifs, self.flags)

    def walk(self, items: list) -> None:
        for item in items:
            if isinstance(item, OutputNode):
                if item.id in self.abstain_by_source:  # e.g. markup_from_expression: opaque, its text is not read
                    self.abstained(item, self.abstain_by_source[item.id])
                elif item.kind in ("call", "children", "include"):
                    self.slot(item)
                else:
                    self.output(item)
                continue
            rid = region_id(item)
            first = _first_output(item)
            if self.strict and first is not None and self.tracker[first.id].in_tag_before != (self.state in _IN_TAG):
                raise GenerationError("markup_not_generatable", f"tag tracker disagrees at {rid}")
            if rid in self.abstain_by_source:
                self.abstained(item, self.abstain_by_source[rid])
            elif isinstance(item, _Loop):
                self.loop(item)
            elif isinstance(item, _If):
                self.if_(item)
            else:
                raise GenerationError("markup_not_generatable", f"{item.enclosure.role} {rid} was not abstained")

    # ---- regions

    def container(self) -> list:
        if not self.stack:
            return self.root
        top = self.stack[-1]
        return top.children

    def parent(self):
        if not self.stack:
            return None
        top = self.stack[-1]
        return top.owner if isinstance(top, _Frame) else top

    def append(self, node) -> None:
        node.parent = self.parent()
        self.container().append(node)

    def abstained(self, item, node: ComponentNode) -> None:
        if self.state != "data":  # inside a start tag: only that tag's attribute is lost (start-tag fallback)
            self.attribute_gap(_outputs([item]), node.source_ids[0], node.reason,
                               f"abstained region {region_id(item)} inside a tag")
            return
        self.flush_text()
        self.certain = False
        container = self.container()
        if container and isinstance(container[-1], MAbstain) and container[-1].node is node:
            return  # consecutive unresolved calls are one Stage 2 node: one gap (spec v0.7 section 8a)
        self.append(MAbstain(node, item))

    def slot(self, node: OutputNode) -> None:
        if self.state != "data":
            raise GenerationError("markup_not_generatable", f"call {node.id} inside a tag")
        self.flush_text()
        if node.kind == "children" or node.id in self.opaque:
            self.certain = False
        self.append(MCall(node))

    def loop(self, item: _Loop) -> None:
        if self.state != "data":
            raise GenerationError("markup_not_generatable", f"loop {item.enclosure.node_id} inside a tag")
        self.flush_text()
        node = MLoop(item.enclosure)
        self.append(node)
        self.loops[item.enclosure.node_id] = node
        self.region(node, node.children, item.items)

    def if_(self, item: _If) -> None:
        if self.state in _ATTR_VALUE:
            self.attribute_if(item)
            return
        if self.state == "rawtext":
            # An if inside <style> / <script> / <title> / <textarea> content: like an echo there, the element
            # has content that is not text (Stage 5: dynamic_raw_text, or document_shell in a shell's <head>).
            nodes = _outputs([item])
            if not nodes or any(self.tracker[n.id].events for n in nodes):
                raise GenerationError("markup_not_generatable", f"if {item.node_id} ends a raw-text element")
            self.tag.content.append(MAttrGap(item.node_id, "dynamic_raw_text"))
            return
        if self.state != "data":
            self.attribute_presence(item)
            return
        self.flush_text()
        node = MIf(item.node_id)
        self.append(node)
        self.ifs[item.node_id] = node
        for arm in item.branches:
            branch = MBranch(arm.enclosure, parent=node)
            node.branches.append(branch)
            self.region(branch, branch.children, arm.items)

    def region(self, owner, children: list, items: list) -> None:
        frame = _Frame(owner, children)
        self.stack.append(frame)
        self.walk(items)
        if self.state != "data":
            raise GenerationError("markup_not_generatable", "a region ends inside a tag")
        self.flush_text()
        while self.stack[-1] is not frame:
            entry = self.stack.pop()
            if self.strict:
                raise GenerationError("markup_not_generatable", f"<{entry.tag}> crosses a region boundary")
            entry.clean = False
            self.flag("implicit_close", "info", entry.node_id, f"<{entry.tag}> closed at the end of its branch",
                      entry.parent if isinstance(entry.parent, MElement) else None)
        self.stack.pop()

    def stays_in_tag(self, nodes: list) -> bool:
        """These nodes start and end inside the tag being read and cannot end the value or the tag: leaving
        them out keeps the reading of everything after them right."""
        tracked = [self.tracker[n.id] for n in nodes]
        if not tracked or not all(t.in_tag_before and t.in_tag_after and not t.events for t in tracked):
            return False
        text = "".join(tagtrack.output_text(n) for n in nodes)
        if self.state == "value_dq":
            return '"' not in text
        if self.state == "value_sq":
            return "'" not in text
        if self.state == "value_unq":
            return not any(ch in HTML_SPACE for ch in text)
        return True

    def attribute_gap(self, nodes: list, node_id: str, reason: str, what: str) -> None:
        """Spec v0.8 section 5b: what cannot be generated inside a start tag costs only that tag's attribute."""
        if self.state in ("tag_name", "tag_open", "end_tag_open", "end_tag_name", "end_tag_rest") \
                or self.tag is None or not self.stays_in_tag(nodes):
            raise GenerationError("markup_not_generatable", what)
        if self.state in _ATTR_VALUE:
            self.attr.parts.append(MAttrGap(node_id, reason))
            return
        if self.state in ("attr_name", "before_attr_value") and self.attr in self.tag.attrs:
            self.tag.attrs.remove(self.attr)  # the attribute being written is cut in two: it goes too
        self.tag.attrs.append(MAttrGap(node_id, reason))

    def attribute_presence(self, item: _If) -> None:
        """An if between attributes (R-I3b): conditional attributes when every arm prints complete literal
        ones; otherwise that tag's attribute gap (Stage 2 abstained on it: partial_attribute_output)."""
        arms = []
        for arm in item.branches:
            direct = all(isinstance(inner, OutputNode) for inner in arm.items)
            found = tagtrack.literal_attributes(tagtrack.literal_text(arm.items)) if direct else None
            if found is None or self.state not in _BETWEEN_ATTRIBUTES:
                self.attribute_gap(_outputs([item]), item.node_id, "partial_attribute_output",
                                   f"if {item.node_id} inside a tag, outside a value")
                return
            arms.append((arm.enclosure, [MAttr(name, None if value is None else [value], arm.items[0].id)
                                         for name, value in found]))
        self.tag.attrs.append(MAttrPresence(item.node_id, arms))

    def attribute_if(self, item: _If) -> None:
        attr, state, outer = self.attr, self.state, self.attr.parts
        part = MAttrIf(item.node_id, [])
        outer.append(part)
        for arm in item.branches:
            parts = []
            attr.parts = parts
            self.walk(arm.items)
            if self.state != state or self.attr is not attr:
                raise GenerationError("markup_not_generatable", f"if {item.node_id} leaves its attribute value")
            part.branches.append((arm.enclosure, parts))
        attr.parts = outer

    # ---- output nodes

    def output(self, node: OutputNode) -> None:
        self.node = node.id
        if node.raw is not None:
            self.feed(node.raw)
            return
        if node.parts:  # boundary-rules section 1: literal pieces are markup, each expression is one value
            for part in node.parts:
                if part.kind == "literal":
                    self.feed(part.value)
                else:
                    self.dynamic(replace(node, reads=(node.reads[part.read],), parts=()), part)
            return
        literal = php_literal_text(node.reads[0].expr) if len(node.reads) == 1 and \
            node.reads[0].source_kind == "literal" else None
        if literal is not None:
            self.feed(literal)
        else:
            self.dynamic(node)

    def feed(self, text: str) -> None:
        for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
            self.char(ch)

    def dynamic(self, node: OutputNode, echo_part=None) -> None:
        part = MDynamic(node, part=echo_part)
        state = self.state
        if state == "tag_open":
            self.text_append("<")
            self.state = state = "data"
        if state == "data":
            self.flush_text()
            self.append(part)
        elif state in _ATTR_VALUE:
            self.attr.parts.append(part)
        elif state == "before_attr_value":
            self.attr.parts = [part]
            self.state = "value_unq"
        elif state == "rawtext":
            self.tag.content.append(part)
        elif state in ("comment", "bogus", "markup_decl"):
            self.flag("html_comment_dropped", "info", node.id, "output inside an HTML comment", self.anchor())
        elif state in _BETWEEN_ATTRIBUTES or state in ("attr_name", "self_closing"):
            # A value where an attribute (or the rest of its name) is written: start-tag fallback. A value is
            # text, so it cannot end the tag; reading goes on after it.
            if state == "attr_name" and self.attr in self.tag.attrs:
                self.tag.attrs.remove(self.attr)
            self.tag.attrs.append(MAttrGap(echo_part.node_id if echo_part is not None else node.id,
                                           "partial_attribute_output"))
        else:
            raise GenerationError("markup_not_generatable", f"output {node.id} inside a tag name")

    # ---- the tokenizer

    def text_append(self, ch: str) -> None:
        if not self.text:
            self.text_node = self.node
        self.text.append(ch)

    def flush_text(self) -> None:
        if self.text:
            self.append(MText("".join(self.text), self.text_node))
            self.text = []

    def anchor(self):
        for entry in reversed(self.stack):
            if isinstance(entry, MElement):
                return entry
            if isinstance(entry, _Frame):
                return entry.owner
        return None

    def flag(self, flag, status, node_id, detail, anchor=None) -> None:
        self.flags.append(MarkupFlag(flag, status, node_id, detail, anchor))

    def char(self, ch: str) -> None:
        s = self.state
        space = ch in HTML_SPACE
        if s == "data":
            if ch == "<":
                self.state, self.tag_node = "tag_open", self.node
            else:
                if not space and self.open_head():
                    self.flush_text()  # whitespace stays in <head>; this text is body content (spec v0.14)
                    self.end_head("text")
                self.text_append(ch)
        elif s == "tag_open":
            if ch.isascii() and ch.isalpha():
                self.flush_text()
                self.state, self.tag_name = "tag_name", ch
                self.tag = MElement("", [], self.tag_node)
            elif ch == "/":
                self.state = "end_tag_open"
            elif ch in "!?":
                self.flush_text()
                self.state, self.decl = ("markup_decl", "") if ch == "!" else ("bogus", "?")
            else:
                self.text_append("<")
                self.state = "data"
                self.char(ch)
        elif s == "end_tag_open":
            if ch.isascii() and ch.isalpha():
                self.flush_text()
                self.state, self.end_name = "end_tag_name", ch
            elif ch == ">":
                self.state = "data"
            else:
                self.flush_text()
                self.state, self.decl = "bogus", "/" + ch
        elif s == "end_tag_name":
            if ch == ">":
                self.end_tag()
            elif space or ch == "/":
                self.state = "end_tag_rest"
            else:
                self.end_name += ch
        elif s == "end_tag_rest":
            if ch == ">":
                self.end_tag()
        elif s == "markup_decl":
            self.decl += ch
            if self.decl == "--":
                self.state, self.comment = "comment", ""
            elif not "--".startswith(self.decl):
                self.state = "bogus"
                if ch == ">":
                    self.bogus_end()
        elif s == "comment":
            self.comment += ch
            if self.comment.endswith("-->"):
                text = self.comment[:-3]
                kind = "conditional comment" if text.lstrip().startswith("[if") else "comment"
                self.flag("html_comment_dropped", "info", self.tag_node, kind, self.anchor())
                self.state = "data"
        elif s == "bogus":
            if ch == ">":
                self.bogus_end()
            else:
                self.decl += ch
        elif s == "tag_name":
            if space:
                self.state = "before_attr_name"
            elif ch == "/":
                self.state = "self_closing"
            elif ch == ">":
                self.start_tag()
            else:
                self.tag_name += ch
        elif s in ("before_attr_name", "after_attr_name"):
            if space:
                pass
            elif ch == "/":
                self.state = "self_closing"
            elif ch == ">":
                self.start_tag()
            elif ch == "=" and s == "after_attr_name":
                self.state = "before_attr_value"
            else:
                self.attr = MAttr(ch.lower(), None, self.node)
                self.tag.attrs.append(self.attr)
                self.state = "attr_name"
        elif s == "attr_name":
            if space:
                self.state = "after_attr_name"
            elif ch == "/":
                self.state = "self_closing"
            elif ch == ">":
                self.start_tag()
            elif ch == "=":
                self.state = "before_attr_value"
            else:
                self.attr.name += ch.lower()
        elif s == "before_attr_value":
            if space:
                pass
            elif ch == '"':
                self.attr.parts, self.state = [], "value_dq"
            elif ch == "'":
                self.attr.parts, self.state = [], "value_sq"
            elif ch == ">":
                self.attr.parts = []
                self.start_tag()
            else:
                self.attr.parts, self.state = [], "value_unq"
                self.char(ch)
        elif s in ("value_dq", "value_sq"):
            if ch == ('"' if s == "value_dq" else "'"):
                self.state = "after_attr_value"
            else:
                self.value_append(ch)
        elif s == "value_unq":
            if space:
                self.state = "before_attr_name"
            elif ch == ">":
                self.start_tag()
            else:
                self.value_append(ch)
        elif s == "after_attr_value":
            if space:
                self.state = "before_attr_name"
            elif ch == "/":
                self.state = "self_closing"
            elif ch == ">":
                self.start_tag()
            else:
                self.state = "before_attr_name"
                self.char(ch)
        elif s == "self_closing":
            if ch == ">":
                self.start_tag(self_closing=True)
            else:
                self.state = "before_attr_name"
                self.char(ch)
        elif s == "rawtext":
            content = self.tag.content
            if content and isinstance(content[-1], str):
                content[-1] += ch
            else:
                content.append(ch)
            self.raw_end = (self.raw_end + ch.lower())[-(len(self.tag.tag) + 2):]
            if self.raw_end == "</" + self.tag.tag:
                content[-1] = content[-1][:-(len(self.tag.tag) + 2)]
                if not content[-1]:
                    content.pop()
                self.state = "rawclose"
        elif s == "rawclose":
            if ch == ">":
                self.stack.pop()
                self.state, self.tag = "data", None

    def value_append(self, ch: str) -> None:
        parts = self.attr.parts
        if parts and isinstance(parts[-1], str):
            parts[-1] += ch
        else:
            parts.append(ch)

    def implied_end_tags(self, tag: str) -> None:
        """Spec v0.8 section 5b: the end tags a start tag implies in every browser. `<li>` ends an open `<li>`,
        `<dt>` / `<dd>` an open `<dt>` / `<dd>`, `<option>` an open `<option>`; and `<body>` ends an open
        `<head>` (boundary-rules v0.13, R-F2). Only inside one region's static markup: never across a loop or
        an if, never when what would be closed holds anything but text and elements, and never when a
        formatting element would be closed with it (a browser reopens those)."""
        index = None
        if tag == "option":
            if self.stack and isinstance(self.stack[-1], MElement) and self.stack[-1].tag == "option":
                index = len(self.stack) - 1
        elif tag in _IMPLIED_END:
            for position in range(len(self.stack) - 1, -1, -1):
                entry = self.stack[position]
                if isinstance(entry, _Frame):
                    break
                if entry.tag in _IMPLIED_END[tag]:
                    index = position
                    break
                if entry.tag in _SPECIAL:  # (address, div and p are not in the set: the search goes past them)
                    break
        elif tag == "body":
            for position in range(len(self.stack) - 1, -1, -1):
                entry = self.stack[position]
                if isinstance(entry, _Frame):
                    break
                if entry.tag == "head":
                    index = position
                    break
        if index is None:
            return
        closing = self.stack[index:]
        if any(isinstance(e, _Frame) or e.tag in _FORMATTING for e in closing) or not all(
                isinstance(child, (MText, MElement)) for e in closing for child in e.children):
            return
        for inner in closing:
            inner.end, inner.clean = "implied", True
        self.flag("implied_end_tag", "info", closing[0].node_id,
                  f"<{closing[0].tag}> ended by the next <{tag}>, as a browser does",
                  closing[0].parent if isinstance(closing[0].parent, MElement) else None)
        del self.stack[index:]

    def open_head(self) -> bool:
        """Spec v0.14: <head> is the current element and everything in it is text or elements (a browser's tree
        is certain), so the next element that is not head content, or text that is not whitespace, ends it."""
        top = self.stack[-1] if self.stack else None
        return bool(self.browser_v014 and isinstance(top, MElement) and top.tag == "head"
                    and all(isinstance(child, (MText, MElement)) for child in top.children))

    def end_head(self, by: str) -> None:
        head = self.stack.pop()
        head.end, head.clean = "implied", True
        self.flag("implied_end_tag", "info", head.node_id,
                  f"<head> ended by {by}, as a browser does: it and what follows are body content (spec v0.14)",
                  head.parent if isinstance(head.parent, MElement) else None)

    def start_tag(self, self_closing: bool = False) -> None:
        element = self.tag
        element.tag = self.tag_name.lower()
        self.state = "data"
        if element.tag not in HEAD_CONTENT | {"html", "head", "body"} and self.open_head():
            self.end_head(f"<{element.tag}>")
        self.implied_end_tags(element.tag)
        self.append(element)
        if element.tag in self.void or self_closing:
            return
        self.stack.append(element)
        if element.tag in RAWTEXT_TAGS:
            element.content, self.raw_end, self.state = [], "", "rawtext"

    def close_at(self, index: int, name: str) -> None:
        """`</name>` closes stack[index] and every element open inside it."""
        entry, open_inside = self.stack[index], self.stack[index + 1:]
        for inner in open_inside:
            self.flag("implicit_close", "review", inner.node_id, f"<{inner.tag}> closed by </{name}>", entry)
            inner.end, inner.clean = "implied", inner is open_inside[-1]
        entry.end, entry.clean = "explicit", not open_inside
        del self.stack[index:]

    def end_tag(self) -> None:
        self.state = "data"
        name = self.end_name.strip().lower()
        if self.browser_v014 and name in HEADINGS:
            # Spec v0.14 section 5b: </h1>-</h6> closes the nearest open heading of any level (HTML5 "in body")
            for index in range(len(self.stack) - 1, -1, -1):
                entry = self.stack[index]
                if isinstance(entry, _Frame):
                    break
                if entry.tag in HEADINGS:
                    if entry.tag != name:
                        self.flag("implied_end_tag", "info", entry.node_id,
                                  f"</{name}> closes the open <{entry.tag}>, as a browser does (spec v0.14)",
                                  entry.parent if isinstance(entry.parent, MElement) else None)
                    self.close_at(index, name)
                    return
        for index in range(len(self.stack) - 1, -1, -1):
            entry = self.stack[index]
            if isinstance(entry, _Frame):
                break
            if entry.tag == name:
                self.close_at(index, name)
                return
        if self.certain and name == "br":  # HTML5 "in body": an end tag </br> is treated as a <br> start tag
            self.append(MElement("br", [], self.tag_node))
            self.flag("stray_end_tag_repaired", "info", self.tag_node, "</br> is a <br />, as a browser builds it",
                      self.anchor())
            return
        if self.certain and name == "p" and not any(isinstance(e, MElement) and e.tag == "p" for e in self.stack):
            # HTML5 "in body": </p> with no <p> open inserts a <p> element and closes it at once
            self.append(MElement("p", [], self.tag_node, end="explicit"))
            self.flag("stray_end_tag_repaired", "info", self.tag_node,
                      "</p> with no open <p>: an empty <p></p>, as a browser builds it", self.anchor())
            return
        self.flag("unmatched_close", "info", self.tag_node, f"</{name}> closes nothing open; dropped (R-L6)",
                  self.anchor())

    def bogus_end(self) -> None:
        self.state = "data"
        if self.decl.lower().startswith("doctype"):
            self.flag("document_shell", "review", self.tag_node, "<!DOCTYPE> dropped: it belongs in app/layout.tsx",
                      self.anchor())
        else:
            self.flag("html_comment_dropped", "info", self.tag_node, f"<{self.decl[:20]}...> dropped", self.anchor())


def _outputs(items: list) -> list[OutputNode]:
    """Every output node of these items, in order."""
    out = []
    for item in items:
        if isinstance(item, OutputNode):
            out.append(item)
        elif isinstance(item, _If):
            for branch in item.branches:
                out += _outputs(branch.items)
        else:
            out += _outputs(item.items)
    return out


def _first_output(item) -> OutputNode | None:
    if isinstance(item, OutputNode):
        return item
    groups = [b.items for b in item.branches] if isinstance(item, _If) else [item.items]
    for group in groups:
        for inner in group:
            found = _first_output(inner)
            if found is not None:
                return found
    return None


def build_page(outputs: list[OutputNode], config: BoundaryConfig, abstain: dict[str, ComponentNode],
               stray: bool = False, opaque: frozenset = frozenset()) -> Markup:
    """The markup tree of a whole page; abstained regions are opaque. `stray` turns on the repair of stray
    </p> and </br> end tags (spec v0.7 section 5b); `opaque` names the slots after which it stops."""
    return Builder(outputs, config, abstain, strict=True, stray=stray, opaque=opaque).build(_regions(outputs))


def build_region(region, outputs: list[OutputNode], config: BoundaryConfig,
                 abstain: dict[str, ComponentNode]) -> Markup:
    """The markup tree of one abstained region on its own (for its Abstained_ function)."""
    inner = {k: v for k, v in abstain.items() if k != region_id(region)}
    if isinstance(region, _Other):  # a switch case / try block: no structure of its own, only what it holds
        return Builder(outputs, config, inner, strict=False).build(region.items)
    return Builder(outputs, config, inner, strict=False).build([region])
