"""A small JSX tree and its one fixed formatter (docs/generation-spec.md section 9).

No Prettier: its version drift would break determinism. The format is fixed:
2-space indent, double quotes, one child per line, ASCII-only output (other
characters are written as escapes inside string literals). A child that is
JSX text is written as bare text only when JSX cannot change it (printable
ASCII, no leading or trailing space, none of `{ } < > &`); any other text is a
string literal, so the rendered characters are exactly the decoded HTML.

Every node and attribute can carry marks: keys whose generated line number the
formatter records (the line where the node starts), so Stage 5 can report
each C2Todo and flag with file and line.
"""

import json
from dataclasses import dataclass, field

INDENT = "  "
MAX_INLINE = 100  # an element with one short child is written on one line up to this width


@dataclass
class Text:
    text: str  # the characters to render (entities already decoded)
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Expr:
    code: str  # a TypeScript expression, written as {code}
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Attr:
    name: str
    value: str | None = None  # a static value; None with code None = a bare boolean attribute
    code: str | None = None  # a TypeScript expression, written as name={code}
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Element:
    tag: str
    attrs: list[Attr] = field(default_factory=list)
    children: list = field(default_factory=list)
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Fragment:
    children: list = field(default_factory=list)
    key: str | None = None  # a TypeScript expression: written as <Fragment key={key}>
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Cond:
    """{c1 ? (A) : c2 ? (B) : (C)}; one branch and no else -> {c1 && (A)}."""

    branches: list[tuple[str, list]]  # (condition expression, content nodes), in source order
    otherwise: list | None = None  # the else content; None when there is no else
    marks: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass
class Map:
    """{items.map((params) => (body))}."""

    items: str
    params: str
    body: list
    marks: tuple[str, ...] = field(default=(), kw_only=True)


# ----------------------------------------------------------------------------- literals


def js_string(text: str) -> str:
    """A double-quoted TypeScript string literal, ASCII only."""
    return json.dumps(text, ensure_ascii=True)


def template_literal(parts: list[tuple[str, str]]) -> str:
    """A template literal from ("text", s) and ("code", expression) parts, ASCII only."""
    out = []
    for kind, value in parts:
        if kind == "code":
            out.append("${" + value + "}")
            continue
        for index, ch in enumerate(value):
            if ch == "\\":
                out.append("\\\\")
            elif ch == "`":
                out.append("\\`")
            elif ch == "$" and value[index + 1:index + 2] == "{":
                out.append("\\$")
            elif 0x20 <= ord(ch) < 0x7F:
                out.append(ch)
            elif ord(ch) > 0xFFFF:
                out.append("\\u{%x}" % ord(ch))
            else:
                out.append("\\u%04x" % ord(ch))
    return "`" + "".join(out) + "`"


def is_bare_text(text: str) -> bool:
    """True if `text` can be written as bare JSX text and render exactly these characters."""
    return (bool(text) and text == text.strip() and all(0x20 <= ord(ch) < 0x7F for ch in text)
            and not any(ch in "{}<>&" for ch in text))


def is_bare_attribute_value(value: str) -> bool:
    """True if `value` can be written as name="value" (JSX attribute strings decode entities, not escapes)."""
    return all(0x20 <= ord(ch) < 0x7F for ch in value) and '"' not in value and "&" not in value


# ----------------------------------------------------------------------------- formatter


class Lines:
    """Lines of one file, with the line number of every mark."""

    def __init__(self):
        self.lines: list[str] = []
        self.marks: dict[str, int] = {}

    def add(self, text: str, marks: tuple[str, ...] = ()) -> None:
        self.lines.append(text)
        for mark in marks:
            self.marks.setdefault(mark, len(self.lines))

    def extend(self, rendered: list[tuple[str, tuple[str, ...]]]) -> None:
        for text, marks in rendered:
            self.add(text, marks)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _attrs(attrs: list[Attr]) -> str:
    out = []
    for a in attrs:
        if a.code is not None:
            out.append(f" {a.name}={{{a.code}}}")
        elif a.value is None:
            out.append(f" {a.name}")
        elif is_bare_attribute_value(a.value):
            out.append(f' {a.name}="{a.value}"')
        else:
            out.append(f" {a.name}={{{js_string(a.value)}}}")
    return "".join(out)


def _marks(node) -> tuple[str, ...]:
    marks = tuple(node.marks)
    if isinstance(node, Element):
        for a in node.attrs:
            marks += tuple(a.marks)
    return marks


def _one_line_child(node) -> str | None:
    """The node as a one-line JSX child, or None if it needs several lines."""
    if isinstance(node, Text):
        return node.text if is_bare_text(node.text) else "{" + js_string(node.text) + "}"
    if isinstance(node, Expr):
        return "{" + node.code + "}"
    if isinstance(node, Element) and not node.children:
        return f"<{node.tag}{_attrs(node.attrs)} />"
    return None


def _one_line_expression(node) -> str | None:
    """The node as a one-line expression (inside {cond && ...}), or None."""
    if isinstance(node, Text):
        return js_string(node.text)
    if isinstance(node, Expr):
        return node.code
    if isinstance(node, Element) and not node.children:
        return f"<{node.tag}{_attrs(node.attrs)} />"
    return None


def _root(nodes: list):
    """One node standing for `nodes` in an expression position."""
    if len(nodes) == 1 and not isinstance(nodes[0], (Cond, Map)):
        return nodes[0]
    return Fragment(list(nodes))


def render_child(node, depth: int) -> list[tuple[str, tuple[str, ...]]]:
    """`node` written as a JSX child at `depth`."""
    ind = INDENT * depth
    marks = _marks(node)
    if isinstance(node, (Text, Expr)):
        return [(ind + _one_line_child(node), marks)]
    if isinstance(node, Element):
        opening = f"<{node.tag}{_attrs(node.attrs)}"
        if not node.children:
            return [(ind + opening + " />", marks)]
        if len(node.children) == 1:
            child = _one_line_child(node.children[0])
            if child is not None:
                line = f"{ind}{opening}>{child}</{node.tag}>"
                if len(line) <= MAX_INLINE:
                    return [(line, marks + _marks(node.children[0]))]
        out = [(ind + opening + ">", marks)]
        for child in node.children:
            out += render_child(child, depth + 1)
        return out + [(f"{ind}</{node.tag}>", ())]
    if isinstance(node, Fragment):
        opening, closing = ("<>", "</>") if node.key is None else (f"<Fragment key={{{node.key}}}>", "</Fragment>")
        out = [(ind + opening, marks)]
        for child in node.children:
            out += render_child(child, depth + 1)
        return out + [(ind + closing, ())]
    if isinstance(node, Cond):
        if len(node.branches) == 1 and node.otherwise is None and len(node.branches[0][1]) == 1:
            value = _one_line_expression(node.branches[0][1][0])
            line = f"{ind}{{{_guarded(node.branches[0][0])} && {value}}}" if value is not None else None
            if line is not None and len(line) <= MAX_INLINE:
                return [(line, marks + _marks(node.branches[0][1][0]))]
        out = []
        for index, (condition, content) in enumerate(node.branches):
            joiner = " && (" if len(node.branches) == 1 and node.otherwise is None else " ? ("
            if index == 0:
                out.append((f"{ind}{{{_guarded(condition) if joiner == ' && (' else condition}{joiner}", marks))
            else:
                out.append((f"{ind}) : {condition} ? (", ()))
            out += render_expression(_root(content), depth + 1) if content else [(INDENT * (depth + 1) + "null", ())]
        if node.otherwise is not None:
            out.append((f"{ind}) : (", ()))
            out += (render_expression(_root(node.otherwise), depth + 1) if node.otherwise
                    else [(INDENT * (depth + 1) + "null", ())])
            out.append((ind + ")}", ()))
        elif len(node.branches) > 1:
            out.append((ind + ") : null}", ()))
        else:
            out.append((ind + ")}", ()))
        return out
    if isinstance(node, Map):
        out = [(f"{ind}{{{node.items}.map(({node.params}) => (", marks)]
        out += render_expression(_root(node.body), depth + 1)
        return out + [(ind + "))}", ())]
    raise TypeError(f"not a JSX node: {node!r}")


def render_expression(node, depth: int) -> list[tuple[str, tuple[str, ...]]]:
    """`node` written in an expression position (a return value, a branch, a map body)."""
    ind = INDENT * depth
    if isinstance(node, Text):
        return [(ind + js_string(node.text), _marks(node))]
    if isinstance(node, Expr):
        return [(ind + node.code, _marks(node))]
    if isinstance(node, (Cond, Map)):
        return render_expression(Fragment([node]), depth)
    return render_child(node, depth)


def _guarded(condition: str) -> str:
    """A condition before `&&`: parenthesised only if an operator weaker than && is at its top level."""
    depth, quote, escaped = 0, None, False
    for index, ch in enumerate(condition):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and (ch == "?" or condition.startswith("||", index)):
            return f"({condition})"
    return condition
