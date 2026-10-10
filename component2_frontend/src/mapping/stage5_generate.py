"""Stage 5: generation (docs/generation-spec.md v0.2).

Turns the Stage 2 component tree, the Stage 3 needs and the Stage 4 results
into Next.js (App Router) Server Components:

  app/<route>/page.tsx        the Page
  components/<Name>.tsx       one per List, Item, Empty, ConditionalComponent
  lib/api-types.ts            the response types the page uses
  lib/api.ts                  one typed fetch function per mapped endpoint
  lib/c2-todo.tsx             C2Todo (renders nothing) and unresolvedCondition (false)

Core rule (section 1): a part that cannot be generated faithfully becomes a
C2Todo, an unresolvedCondition() or an omitted attribute, and every one of
them is listed with file, line and reason. Nothing is guessed: a value is
`row.x` / `data.x` only when Stage 4 matched it, a condition is translated
only by the whitelist in conditions.py, and every attribute value must be one
React's types accept (config/react_dom.json).

How the pieces fit: markup.py reads the page's output into one tree of
elements, loops and ifs. The Stage 2 tree says where the components are: a
List owns its container element (R-L2a), its guard (R-I2), or just its loop
(R-L2b); an Item is the loop body; an Empty the guard's other branch. Each
component is written from its part of the markup tree, and a parent calls it
where its part was. An Abstain region becomes a C2Todo in place; its content
is generated on its own into an unused Abstained_<nodeId>() function in the
same file.

No 'use client' and no event handler is ever written, so every file is a
Server Component by construction. No LLM. Output is byte-identical between
runs: every order comes from the timeline or the tree.
"""

import html
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.mapping import jsx, nesting
from src.mapping.conditions import Scope, translate
from src.mapping.markup import (
    HTML_SPACE,
    GenerationError,
    Markup,
    MAbstain,
    MBranch,
    MDynamic,
    MElement,
    MIf,
    MLoop,
    MText,
    build_page,
    build_region,
    php_literal_text,
)
from src.mapping.stage3_requirements import _base_reads, _wrapper
from src.model import (
    BoundaryConfig,
    ComponentNode,
    ComponentTree,
    Contract,
    GeneratedComponent,
    GeneratedFile,
    GenerationConfig,
    GenerationEntry,
    GenerationResult,
    OutputNode,
    ReconciliationReport,
    RequirementsResult,
    SchemaType,
    Timeline,
)

COMPONENT_FILES = ("List", "Item", "Empty", "ConditionalComponent")
REVIEW, INFO = "review", "info"
SEVERITIES = ("todo", "review", "info")
TODO_FLAGS = ("attribute_omitted", "output_dropped")  # flags that stand for a gap, like a C2Todo
FLAGGED = ("missing", "cannot_reconcile")
_SPACE_RUN = re.compile(f"[{re.escape(HTML_SPACE)}]+")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_EVENT_ATTRIBUTE = re.compile(r"on[a-z]+")
C2TODO_SOURCE = """\
// Component 2, Stage 5 (docs/generation-spec.md section 1).
// C2Todo marks a part the generator could not produce faithfully. It renders nothing;
// every use is listed, with its reason, in output/stage5_<mock>.json.
export function C2Todo(props: { id: string; reason: string }): null {
  void props;
  return null;
}

// A condition the generator could not translate (section 6). Always false.
export function unresolvedCondition(nodeId: string, reason: string): boolean {
  void nodeId;
  void reason;
  return false;
}
"""


def pascal(text: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", text) if p]
    name = "".join(p[0].upper() + p[1:] for p in parts)
    return name if name and not name[0].isdigit() else "P" + name


def route_of(entrypoint: str) -> str:
    """The entrypoint's file name without .php (a hand-written mock names its file first)."""
    if entrypoint.startswith("hand-written:"):
        name = entrypoint.removeprefix("hand-written:").split()[0]
    else:
        name = entrypoint.rsplit("/", 1)[-1]
    return name.removesuffix(".php")


def prop_access(base: str, name: str) -> str:
    return f"{base}.{name}" if _IDENTIFIER.fullmatch(name) else f"{base}[{jsx.js_string(name)}]"


def safe_id(node_id: str) -> str:
    return re.sub(r"\W", "_", node_id)


def css_property(name: str) -> str:
    """React's style key for a CSS property: margin-left -> marginLeft, -webkit-x -> WebkitX, -ms-x -> msX."""
    if name.startswith("-ms-"):
        head, rest = "ms", name[4:]
    elif name.startswith("-"):
        vendor, _, rest = name[1:].partition("-")
        head = vendor[:1].upper() + vendor[1:]
    else:
        head, _, rest = name.partition("-")
        rest = rest if rest else ""
        return head + "".join(p[:1].upper() + p[1:] for p in rest.split("-") if p)
    return head + "".join(p[:1].upper() + p[1:] for p in rest.split("-") if p)


# ----------------------------------------------------------------------------- per-file and per-function state


@dataclass
class _Entry:
    kind: str
    flag: str | None
    reason: str
    status: str
    node_id: str
    detail: str
    mark: str
    function: str
    in_abstained: bool


@dataclass
class _Function:
    name: str
    header: str  # e.g. "export function AppointmentsItem({ row }: { row: Appointment }) {"
    locals: list
    body: list  # JSX nodes
    key: str  # mark of its first line


@dataclass
class _File:
    path: str
    functions: list = field(default_factory=list)  # of _Function, in output order
    entries: list = field(default_factory=list)  # of _Entry
    imports: dict = field(default_factory=dict)  # module -> set of names
    type_imports: dict = field(default_factory=dict)
    count: int = 0

    def mark(self) -> str:
        self.count += 1
        return f"m{self.count}"

    def use(self, module: str, name: str, type_only: bool = False) -> None:
        (self.type_imports if type_only else self.imports).setdefault(module, set()).add(name)


@dataclass(frozen=True)
class _Row:
    """The row an Item (or an R-L3 inline map) renders."""

    list_id: str  # the List's component id
    php_var: str | None  # the loop's value variable, e.g. "$row"
    fields: tuple[tuple[str, SchemaType], ...] | None  # the item type; None when the collection is not mapped
    type_code: str  # the row's TypeScript type


@dataclass
class _Ctx:
    file: _File
    markup: Markup
    boundary: dict  # id(markup node) -> the Stage 2 component whose root it is
    function: str
    function_key: str
    root: object = None  # the markup node of the component being written
    list_node: ComponentNode | None = None  # the List being written
    row: _Row | None = None
    preserve: bool = False
    in_abstained: bool = False
    uses: set = field(default_factory=set)  # props the function reads: items, row, data
    locals: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # abstained functions to write after this one

    def child(self, **changes) -> "_Ctx":
        """The same function, with some scope changed: uses, locals and pending stay shared."""
        return _Ctx(**{**self.__dict__, **changes})


class _Space:
    """Whitespace-only text; kept as {" "} only between two inline nodes (spec section 5)."""

    def __init__(self, text: str):
        self.text = text


@dataclass
class _Value:
    code: str | None = None
    text: str | None = None
    todo: str | None = None
    wrappers: tuple[str, ...] = ()


# ----------------------------------------------------------------------------- the generator


class Generator:
    def __init__(self, tree: ComponentTree, requirements: RequirementsResult, report: ReconciliationReport,
                 contract: Contract, timeline: Timeline, boundary_config: BoundaryConfig, config: GenerationConfig):
        self.tree, self.report, self.timeline = tree, report, timeline
        self.boundary_config, self.config, self.react = boundary_config, config, config.react
        self.route = route_of(timeline.entrypoint)
        self.outputs = [n for n in timeline.sequence if n.id in set(tree.root.node_ids)]
        self.output_by_id = {n.id: n for n in timeline.sequence}
        self.files: dict[str, _File] = {}
        self.generated: dict[int, tuple[str, set]] = {}  # id(component) -> (name, props it takes)
        self.cond_marks: dict[int, str] = {}  # id(Stage 2 conditional) -> (file path, mark) of its first if
        self.emitted_in: dict[str, tuple[_File, str, str, bool]] = {}  # node id -> file, function, mark, abstained
        self.pending_flags: dict[int, list] = {}  # id(markup anchor) -> markup flags not yet placed
        self.roots: dict[int, object] = {}  # id(component) -> its markup root (an MBranch for an Empty)
        self._index_tree()
        self._index_results()
        self._endpoint(contract)
        self._names()

    # ---- inputs

    def _index_tree(self) -> None:
        self.ids, self.parents, self.order = {}, {}, []

        def walk(node, cid, parent):
            self.ids[id(node)] = cid
            self.parents[id(node)] = parent
            self.order.append(node)
            for i, child in enumerate(node.children):
                walk(child, f"{cid}.{i}", node)

        walk(self.tree.root, "0", None)
        nodes = self.order
        self.abstain = {n.source_ids[0]: n for n in nodes if n.type == "Abstain"}
        self.lists = [n for n in nodes if n.type == "List"]
        self.r_l5 = {n.source_ids[0] for n in nodes if n.rule == "R-L5"}
        self.conds = {}
        for n in nodes:
            if n.type in ("InlineConditional", "ConditionalComponent"):
                for source in n.source_ids:
                    self.conds[source] = n
        root = self.tree.root
        self.page_guard = root.source_ids[0] if root.rule == "R-I1" else None

    def _index_results(self) -> None:
        self.results = {}
        for r in self.report.results:
            for ref in r.need.references:
                self.results.setdefault((ref.node_id, ref.expr), r)
        self.collections = {r.component_id: r for r in self.report.collection_results}

    def _endpoint(self, contract: Contract) -> None:
        ref = self.report.endpoint
        endpoint = contract.endpoints.get((ref.method, ref.path, ref.status, ref.media_type)) if ref else None
        self.schema = endpoint.schema if endpoint is not None and endpoint.method == "get" else None
        if ref is None:
            self.page_gap = "no_endpoint"
        elif self.schema is None or "{" in ref.path:
            self.page_gap, self.schema = "endpoint_not_in_contract" if endpoint is None else "endpoint_not_callable", None
        else:
            self.page_gap = None
        stem = pascal(ref.path) if ref else ""
        self.fetch_name = f"get{stem}"
        self.response_type = (self.schema.ref or f"{stem}Response") if self.schema else None

    def _names(self) -> None:
        self.names, used, lists, sections = {}, set(), 0, 0
        route = pascal(self.route)

        def unique(name):
            candidate, n = name, 2
            while candidate in used:
                candidate, n = f"{name}{n}", n + 1
            used.add(candidate)
            return candidate

        for node in self.order:
            if node.type == "List":
                lists += 1
                mapped = self.collections.get(self.ids[id(node)])
                if mapped is not None and mapped.result == "mapped":  # AppointmentsList / Item / Empty
                    stem = pascal(mapped.schema_property)
                    self.names[id(node)] = unique(stem + "List")
                else:  # ViewList1 / ViewList1Item / ViewList1Empty
                    stem = self.names[id(node)] = unique(f"{route}List{lists}")
                for child in node.children:
                    if child.type in ("Item", "Empty"):
                        self.names[id(child)] = unique(stem + child.type)
            elif node.type == "ConditionalComponent":
                sections += 1
                self.names[id(node)] = unique(f"{route}Section{sections}")

    def row_scope(self, list_node: ComponentNode) -> _Row:
        list_id = self.ids[id(list_node)]
        loop = next((e for n in self.outputs for e in n.enclosed_by if e.node_id == list_node.source_ids[0]), None)
        mapped = self.collections.get(list_id)
        fields, type_code = None, "Record<string, unknown>"
        if mapped is not None and mapped.result == "mapped" and self.schema is not None:
            array = dict(self.schema.properties).get(mapped.schema_property)
            if array is not None and array.kind == "array" and array.items is not None \
                    and array.items.kind == "object":
                fields = array.items.properties
                type_code = array.items.ref or f"{self.response_type}[{jsx.js_string(mapped.schema_property)}][number]"
        return _Row(list_id, loop.value_var if loop else None, fields, type_code)

    # ---- entries

    def todo(self, node_id: str, reason: str, ctx: _Ctx, detail: str = "") -> jsx.Element:
        mark = ctx.file.mark()
        ctx.file.entries.append(_Entry("C2Todo", None, reason, REVIEW, node_id, detail, mark, ctx.function,
                                       ctx.in_abstained))
        ctx.file.use(self.lib(ctx.file, "c2-todo"), "C2Todo")
        return jsx.Element("C2Todo", [jsx.Attr("id", node_id), jsx.Attr("reason", reason)], marks=(mark,))

    def unresolved(self, node_id: str, reason: str, ctx: _Ctx, marks: list) -> str:
        mark = ctx.file.mark()
        ctx.file.entries.append(_Entry("unresolvedCondition", None, reason, REVIEW, node_id, "", mark, ctx.function,
                                       ctx.in_abstained))
        ctx.file.use(self.lib(ctx.file, "c2-todo"), "unresolvedCondition")
        marks.append(mark)
        return f"unresolvedCondition({jsx.js_string(node_id)}, {jsx.js_string(reason)})"

    def flag(self, ctx: _Ctx, flag: str, status: str, node_id: str, detail: str = "", reason: str | None = None,
             mark: str | None = None) -> str:
        mark = mark or ctx.file.mark()
        ctx.file.entries.append(_Entry("flag", flag, reason or flag, status, node_id, detail, mark, ctx.function,
                                       ctx.in_abstained))
        return mark

    def place_markup_flags(self, anchor, ctx: _Ctx, marks: list) -> None:
        for f in self.pending_flags.pop(id(anchor), []):
            marks.append(self.flag(ctx, f.flag, f.status, f.node_id, f.detail))

    def drop_subtree_flags(self, node, ctx: _Ctx, why: str) -> None:
        """A dropped element: the flags anchored inside it go to the function's line, and every output
        inside it is flagged output_dropped (with why), so no read disappears unlisted."""
        self.place_markup_flags(node, ctx, [])
        if isinstance(node, MDynamic):
            self.flag(ctx, "output_dropped", REVIEW, node.output.id, "inside a dropped element", reason=why)
        for part in getattr(node, "content", None) or []:
            if isinstance(part, MDynamic):
                self.flag(ctx, "output_dropped", REVIEW, part.output.id, "inside a dropped element", reason=why)
        for attr in getattr(node, "attrs", []):
            for part in attr.parts or []:
                if isinstance(part, MDynamic):
                    self.flag(ctx, "output_dropped", REVIEW, part.output.id, f"in {attr.name} of a dropped element",
                              reason=why)
        for child in getattr(node, "children", []):
            self.drop_subtree_flags(child, ctx, why)
        for branch in getattr(node, "branches", []):
            if isinstance(branch, MBranch):
                self.drop_subtree_flags(branch, ctx, why)

    def lib(self, file: _File, name: str) -> str:
        depth = file.path.count("/")
        return "../" * depth + "lib/" + name

    def component_module(self, file: _File, name: str) -> str:
        return ("./" if file.path.startswith("components/") else "../" * file.path.count("/") + "components/") + name

    # ---- markup -> JSX

    def nodes(self, items: list, ctx: _Ctx) -> list:
        out = []
        for item in items:
            out += self.node(item, ctx)
        return self.spaces(out)

    def spaces(self, items: list) -> list:
        out, i = [], 0
        while i < len(items):
            if not isinstance(items[i], _Space):
                out.append(items[i])
                i += 1
                continue
            j = i
            while j < len(items) and isinstance(items[j], _Space):
                j += 1
            before = out[-1] if out else None
            after = items[j] if j < len(items) else None
            if before is not None and after is not None and self.inline(before) and self.inline(after):
                out.append(jsx.Text(" "))
            i = j
        return out

    def inline(self, node) -> bool:
        if isinstance(node, (jsx.Text, jsx.Expr)):
            return True
        if isinstance(node, jsx.Element):
            return node.tag == "C2Todo" or node.tag in self.config.inline_tags
        if isinstance(node, jsx.Fragment):
            return all(self.inline(c) for c in node.children)
        if isinstance(node, jsx.Cond):
            contents = [c for _, content in node.branches for c in content] + list(node.otherwise or [])
            return all(self.inline(c) for c in contents)
        return False

    def node(self, item, ctx: _Ctx) -> list:
        component = ctx.boundary.get(id(item))
        if component is not None and item is not ctx.root:
            return self.call(component, ctx)
        if isinstance(item, MText):
            self.emitted_in.setdefault(item.node_id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
            return self.text(item, ctx)
        if isinstance(item, MDynamic):
            self.emitted_in.setdefault(item.output.id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
            return self.dynamic(item, ctx)
        if isinstance(item, MElement):
            return self.element(item, ctx)
        if isinstance(item, MLoop):
            return self.loop(item, ctx)
        if isinstance(item, MIf):
            return self.if_(item, ctx)
        if isinstance(item, MAbstain):
            return self.abstained(item, ctx)
        raise TypeError(item)

    def text(self, item: MText, ctx: _Ctx) -> list:
        decoded = html.unescape(item.text)
        if ctx.preserve:
            return [jsx.Text(decoded)]
        if decoded.strip(HTML_SPACE) == "":
            return [_Space(decoded)]
        return [jsx.Text(_SPACE_RUN.sub(" ", decoded))]

    def escaped(self, output: OutputNode) -> bool:
        top = output.reads[0] if output.reads else None
        if top is None or top.source_kind == "literal":
            return True
        return top.source_kind == "computed" and _wrapper(top.expr) in self.config.escape_wrappers

    def value(self, output: OutputNode, ctx: _Ctx) -> _Value:
        """What an echo prints: a TypeScript expression, a literal text, or the reason it is a gap."""
        if len(output.reads) != 1:
            return _Value(todo="expression_not_translatable")
        read, wrappers = output.reads[0], []
        while read.source_kind == "computed":
            name = _wrapper(read.expr)
            if name is None or len(read.derived_from) != 1:
                return _Value(todo=self.worst_reason(output.id, read), wrappers=tuple(wrappers))
            if name not in self.config.escape_wrappers:
                wrappers.append(name)
            read = read.derived_from[0]
        if read.source_kind == "literal":
            text = php_literal_text(read.expr)
            if text is None or wrappers:
                return _Value(todo="expression_not_translatable")
            return _Value(text=text)
        result = self.results.get((output.id, read.expr))
        if result is None:
            return _Value(todo="no_stage4_result", wrappers=tuple(wrappers))
        if result.result == "excluded":
            return _Value(todo=self.config.context_reasons.get(result.reason, result.reason), wrappers=tuple(wrappers))
        if result.result != "matched":
            return _Value(todo=result.reason, wrappers=tuple(wrappers))
        column = result.need.column
        if result.need.row_of is not None:
            if ctx.row is None or ctx.row.list_id != result.need.row_of or ctx.row.fields is None:
                raise GenerationError("markup_not_generatable", f"{output.id}: row field outside its Item")
            schema, code = dict(ctx.row.fields)[column], prop_access("row", column)
            ctx.uses.add("row")
        else:
            schema = dict(self.schema.properties)[column]
            code = prop_access("data", column)
            ctx.uses.add("data")
        if schema.kind == "boolean":
            return _Value(todo="boolean_output_not_converted", wrappers=tuple(wrappers))
        if schema.kind not in ("string", "number", "integer"):
            return _Value(todo="non_scalar_output", wrappers=tuple(wrappers))
        return _Value(code=code, wrappers=tuple(wrappers))

    def worst_reason(self, node_id: str, read) -> str:
        for base, _ in _base_reads(read):
            if base.source_kind == "literal":
                continue
            result = self.results.get((node_id, base.expr))
            if result is not None and result.result in FLAGGED:
                return result.reason
            if result is not None and result.result == "excluded":
                return self.config.context_reasons.get(result.reason, result.reason)
        return "expression_not_translatable"

    def dynamic(self, item: MDynamic, ctx: _Ctx) -> list:
        output = item.output
        marks = []
        if not self.escaped(output):
            marks.append(self.flag(ctx, "escaping_changed", INFO, output.id,
                                   "unescaped in PHP; React escapes it (an expected difference)"))
        value = self.value(output, ctx)
        if value.text is not None:
            return [jsx.Text(value.text, marks=tuple(marks))]
        if value.code is None:
            detail = f"wrappers: {', '.join(value.wrappers)}" if value.wrappers else ""
            node = self.todo(output.id, value.todo, ctx, detail)
            node.marks = tuple(marks) + node.marks
            return [node]
        code = value.code
        for wrapper in value.wrappers:
            code += f" /* TODO(C2): {wrapper} */"
            marks.append(self.flag(ctx, "unsupported_wrapper", REVIEW, output.id, f"{wrapper}() not applied"))
        return [jsx.Expr(code, marks=tuple(marks))]

    # ---- elements and attributes

    def element(self, el: MElement, ctx: _Ctx) -> list:
        tag, marks = el.tag, []
        self.emitted_in.setdefault(el.node_id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
        if tag in self.config.dropped_tags:
            flag = self.config.dropped_tags[tag]
            self.flag(ctx, flag, REVIEW, el.node_id, f"<{tag}> and its content dropped")
            self.drop_subtree_flags(el, ctx, flag)
            return []
        if tag in self.config.document_shell_tags:
            attrs = " ".join(a.name for a in el.attrs)
            self.flag(ctx, "document_shell", REVIEW, el.node_id,
                      f"<{tag}{' ' + attrs if attrs else ''}> dropped, children kept: it belongs in app/layout.tsx")
            self.place_markup_flags(el, ctx, [])
            return self.nodes(el.children, ctx)
        if tag not in self.react.elements:
            self.drop_subtree_flags(el, ctx, "element_not_in_react_types")
            return [self.todo(el.node_id, "element_not_in_react_types", ctx, f"<{tag}>")]
        attrs = self.attributes(el, ctx, marks)
        children = []
        if tag in self.config.content_as_prop:
            attr = self.content_prop(el, ctx, marks)
            if attr is not None:
                attrs.append(attr)
        elif el.content is not None:
            parts = el.content
            if any(not isinstance(p, str) for p in parts):
                self.drop_subtree_flags(el, ctx, "dynamic_raw_text")
                return [self.todo(el.node_id, "dynamic_raw_text", ctx, f"output inside <{tag}>")]
            if "".join(parts):
                children = [jsx.Text("".join(parts))]
        else:
            inner = ctx.child(preserve=ctx.preserve or tag in self.config.preserve_whitespace_tags)
            children = self.nodes(el.children, inner)
            if inner.preserve and not ctx.preserve and children and isinstance(children[0], jsx.Text) \
                    and children[0].text.startswith("\n"):
                children[0] = jsx.Text(children[0].text[1:], marks=children[0].marks)  # HTML drops that newline
        self.place_markup_flags(el, ctx, marks)
        return [jsx.Element(tag, attrs, children, marks=tuple(marks))]

    def prop_kinds(self, tag: str, prop: str) -> str | None:
        stack, seen = [self.react.elements[tag], "AriaAttributes"], set()
        while stack:
            name = stack.pop(0)
            if name in seen:
                continue
            seen.add(name)
            if prop in self.react.props.get(name, {}):
                return self.react.props[name][prop]
            stack += list(self.react.extends.get(name, ()))
        return None

    def prop_name(self, tag: str, name: str) -> str | None:
        renamed = self.config.form_state.get(tag, {}).get(name) or self.config.attribute_renames.get(name)
        if renamed:
            return renamed
        stack, seen = [self.react.elements[tag]], set()
        while stack:
            interface = stack.pop(0)
            if interface in seen:
                continue
            seen.add(interface)
            for prop in self.react.props.get(interface, {}):
                if prop.lower() == name and "-" not in prop:
                    return prop
            stack += list(self.react.extends.get(interface, ()))
        return name if "-" in name else None

    def attributes(self, el: MElement, ctx: _Ctx, marks: list) -> list:
        out, seen = [], set()
        for a in el.attrs:
            name = a.name
            if _EVENT_ATTRIBUTE.fullmatch(name):
                marks.append(self.flag(ctx, "inline_js_dropped", REVIEW, a.node_id, f"{name} on <{el.tag}>"))
                continue
            if name in seen:
                marks.append(self.flag(ctx, "duplicate_attribute_dropped", INFO, a.node_id,
                                       f"second {name} on <{el.tag}> (HTML keeps the first)"))
                continue
            seen.add(name)
            omitted = self.config.omitted_attributes.get(el.tag, {}).get(name)
            if omitted:
                marks.append(self.flag(ctx, omitted, REVIEW, a.node_id, f"{name} on <{el.tag}> omitted"))
                continue
            if name == "style":
                attr = self.style(a, el, ctx, marks)
                if attr is not None:
                    out.append(attr)
                continue
            prop = self.prop_name(el.tag, name)
            kinds = self.prop_kinds(el.tag, prop) if prop is not None else None
            if prop is not None and kinds is None and "-" in prop and not prop.startswith("aria-"):
                kinds = "s"  # TypeScript does not check hyphenated JSX attribute names it does not know
            if kinds is None:
                marks.append(self.flag(ctx, "attribute_dropped", REVIEW, a.node_id,
                                       f"{name} is not in React's types for <{el.tag}>"))
                continue
            attr = self.attribute(a, el, prop, kinds, ctx, marks)
            if attr is not None:
                out.append(attr)
        return out

    def attribute(self, a, el: MElement, prop: str, kinds: str, ctx: _Ctx, marks: list):
        letters, _, literal_text = kinds.partition(":")
        literals = literal_text.split("|") if literal_text else []
        parts = a.parts or []
        static = all(isinstance(p, str) for p in parts)
        if static:
            value = html.unescape("".join(parts))
            self.legacy_link(a, value, ctx, marks)
            if "s" in letters:
                return jsx.Attr(prop, value)
            if value in literals or value.lower() in literals:
                return jsx.Attr(prop, value if value in literals else value.lower())
            if "b" in letters:
                return jsx.Attr(prop)  # an HTML boolean attribute: present means true
            if "n" in letters and re.fullmatch(r"\s*[+-]?\d+\s*", value):
                return jsx.Attr(prop, code=str(int(value)))
            marks.append(self.flag(ctx, "attribute_dropped", REVIEW, a.node_id,
                                   f"{a.name}={value!r} on <{el.tag}>: not a value React's types accept"))
            return None
        if "b" in letters and "s" not in letters and not literals:
            return jsx.Attr(prop)  # a boolean attribute is decided by its presence, not its value
        if "s" not in letters:
            marks.append(self.flag(ctx, "attribute_dropped", REVIEW, a.node_id,
                                   f"{a.name} on <{el.tag}>: an output value is not one React's types accept"))
            return None
        template, failures = self.template(parts, ctx, marks)
        if failures:
            self.omitted(failures, f"{a.name} on <{el.tag}>", ctx, marks)
            return None
        self.legacy_link(a, "".join(p for p in parts if isinstance(p, str)), ctx, marks)
        return jsx.Attr(prop, code=template)

    def omitted(self, failures: list, what: str, ctx: _Ctx, marks: list) -> None:
        """One attribute_omitted flag per part that could not be written, so every gap keeps its node id."""
        for reason, node_id in failures:
            marks.append(self.flag(ctx, "attribute_omitted", REVIEW, node_id, what, reason=reason))

    def legacy_link(self, a, static_text: str, ctx: _Ctx, marks: list) -> None:
        if a.name in self.config.legacy_link_attributes and re.search(self.config.legacy_link_pattern, static_text):
            marks.append(self.flag(ctx, "legacy_link", INFO, a.node_id, f"{a.name} points at a .php page"))

    def template(self, parts: list, ctx: _Ctx, marks: list):
        """(template literal, []) or (None, [(reason, node id), ...] for every part that cannot be written)."""
        pieces, failures = [], []
        for part in parts:
            if isinstance(part, str):
                pieces.append(("text", html.unescape(part)))
            elif isinstance(part, MDynamic):
                output = part.output
                self.emitted_in.setdefault(output.id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
                if not self.escaped(output):
                    marks.append(self.flag(ctx, "escaping_changed", INFO, output.id,
                                           "unescaped in PHP; React escapes it (an expected difference)"))
                value = self.value(output, ctx)
                if value.text is not None:
                    pieces.append(("text", value.text))
                elif value.code is None:
                    failures.append((value.todo, output.id))
                else:
                    code = value.code
                    for wrapper in value.wrappers:
                        code += f" /* TODO(C2): {wrapper} */"
                        marks.append(self.flag(ctx, "unsupported_wrapper", REVIEW, output.id,
                                               f"{wrapper}() not applied"))
                    pieces.append(("code", code))
            else:  # MAttrIf: an if inside the value (R-I3)
                code, inner = self.attribute_if(part, ctx, marks)
                failures += inner
                pieces.append(("code", code or ""))
        return (None, failures) if failures else (jsx.template_literal(pieces), [])

    def attribute_if(self, part, ctx: _Ctx, marks: list):
        branches, otherwise, failures = [], '""', []
        for enclosure, parts in part.branches:
            value, inner = self.template(parts, ctx, marks)
            failures += inner
            if enclosure.branch == "else":
                otherwise = value
                continue
            translation = translate(enclosure.cond_expr, self.scope(ctx), self.config.condition_context_reasons)
            if translation.code is None:
                node_id = part.if_id if enclosure.branch == "then" else enclosure.cond_node_id
                failures.append((translation.reason, node_id))
                continue
            node_id = part.if_id if enclosure.branch == "then" else enclosure.cond_node_id
            self.translated(translation, node_id, ctx, marks)
            branches.append((translation.code, value))
        if failures:
            return None, failures
        code = otherwise
        for condition, value in reversed(branches):
            code = f"{condition} ? {value} : {code}"
        return code, []

    def style(self, a, el: MElement, ctx: _Ctx, marks: list):
        parts = a.parts or []
        problem = None
        if not all(isinstance(p, str) for p in parts):
            problem = "output inside style"
        entries = {}
        if problem is None:
            for declaration in _split_declarations(html.unescape("".join(parts))):
                if not declaration.strip():
                    continue
                name, colon, value = declaration.partition(":")
                name, value = name.strip().lower(), value.strip()
                kinds = self.react.css.get(name)
                if not colon or not name or not value or "!important" in value.lower():
                    problem = f"cannot read {declaration.strip()!r}"
                    break
                if kinds is None:
                    problem = f"{name} is not a CSS property React's types know"
                    break
                letters, _, literal_text = kinds.partition(":")
                literals = literal_text.split("|") if literal_text else []
                if "s" not in letters:
                    if value.lower() in literals:
                        value = value.lower()
                    else:
                        problem = f"{name}: {value} is not a value React's types accept"
                        break
                key = css_property(name)
                entries.pop(key, None)  # CSS: the last declaration wins
                entries[key] = value
        if problem is not None:
            marks.append(self.flag(ctx, "unsupported_style", REVIEW, a.node_id, f"style on <{el.tag}>: {problem}"))
            return None
        if not entries:
            return None
        return jsx.Attr("style", code="{ " + ", ".join(f"{k}: {jsx.js_string(v)}" for k, v in entries.items()) + " }")

    def content_prop(self, el: MElement, ctx: _Ctx, marks: list):
        prop = self.config.content_as_prop[el.tag]
        parts = list(el.content or [])
        if parts and isinstance(parts[0], str) and parts[0].startswith("\n"):
            parts[0] = parts[0][1:]  # HTML drops a newline right after <textarea>
        parts = [p for p in parts if p != ""]
        if not parts:
            return None
        if all(isinstance(p, str) for p in parts):
            return jsx.Attr(prop, html.unescape("".join(parts)))
        template, failures = self.template(parts, ctx, marks)
        if failures:
            self.omitted(failures, f"<{el.tag}> content", ctx, marks)
            return None
        return jsx.Attr(prop, code=template)

    # ---- conditions

    def scope(self, ctx: _Ctx, guard_var: str | None = None) -> Scope:
        row = ctx.row
        return Scope(row_var=row.php_var if row else None, row_fields=row.fields if row else None,
                     guard_var=guard_var)

    def note_condition_uses(self, code: str, ctx: _Ctx) -> None:
        if re.search(r"\brow\b", code):
            ctx.uses.add("row")
        if re.search(r"\bitems\b", code):
            ctx.uses.add("items")

    def condition(self, mif: MIf, branch: MBranch, ctx: _Ctx, marks: list, guard_var: str | None = None) -> str:
        translation = translate(branch.enclosure.cond_expr, self.scope(ctx, guard_var),
                                self.config.condition_context_reasons)
        node_id = mif.if_id if branch.enclosure.branch == "then" else branch.enclosure.cond_node_id
        if translation.code is not None:
            self.translated(translation, node_id, ctx, marks)
            return translation.code
        return self.unresolved(node_id, translation.reason, ctx, marks)

    def translated(self, translation, node_id: str, ctx: _Ctx, marks: list) -> None:
        """A condition that was translated: note the props it reads, and flag every string == / != in it."""
        self.note_condition_uses(translation.code, ctx)
        for comparison in translation.notes:
            marks.append(self.flag(ctx, "numeric_string_compare", INFO, node_id,
                                   f"{comparison}: PHP compares two numeric strings as numbers"))

    def if_(self, mif: MIf, ctx: _Ctx) -> list:
        if mif.if_id == self.page_guard:  # R-I1: the page renders normally
            return self.nodes(mif.branches[0].children, ctx)
        lst = ctx.list_node
        if lst is not None and len(lst.source_ids) > 1 and lst.source_ids[1] == mif.if_id:
            return self.guard(mif, ctx)
        return self.conditional(mif, ctx, self.conds.get(mif.if_id))

    def conditional(self, mif: MIf, ctx: _Ctx, stage2_node: ComponentNode | None) -> list:
        """One Cond per if: then/elseif/else of one if -> a ternary chain; never merged with a sibling if (R-I6)."""
        marks = []
        self.place_markup_flags(mif, ctx, marks)
        contents = []
        for branch in mif.branches:
            self.place_markup_flags(branch, ctx, marks)
            contents.append(self.nodes(branch.children, ctx))
        if not any(contents):
            return []  # renders nothing whatever the condition says
        branches, otherwise = [], None
        for branch, content in zip(mif.branches, contents):
            if branch.enclosure.branch == "else":
                otherwise = content
            else:
                branches.append((self.condition(mif, branch, ctx, marks), content))
        if stage2_node is not None and stage2_node.source_ids[0] == mif.if_id and id(stage2_node) not in self.cond_marks:
            mark = ctx.file.mark()
            marks.append(mark)
            self.cond_marks[id(stage2_node)] = (ctx.file, mark, ctx.function, ctx.in_abstained)
        return [jsx.Cond(branches, otherwise, marks=tuple(marks))]

    def guard(self, mif: MIf, ctx: _Ctx) -> list:
        """R-I2: the List's guard. The loop's branch renders the List; any other branch is an Empty."""
        lst, marks = ctx.list_node, []
        loop = ctx.markup.loops[lst.source_ids[0]]
        empties = [c for c in lst.children if c.type == "Empty"]
        iter_expr = loop.enclosure.iter_expr
        guard_var = iter_expr if iter_expr and re.fullmatch(r"\$[A-Za-z_]\w*", iter_expr) else None
        branches, otherwise = [], None
        for branch in mif.branches:
            self.place_markup_flags(branch, ctx, marks)
            enc = branch.enclosure
            if _contains(branch, loop):
                content = self.nodes(branch.children, ctx)
            elif any(e.node_id == mif.if_id and (e.branch, e.cond_node_id) == (enc.branch, enc.cond_node_id)
                     for n in self.outputs for e in n.enclosed_by):  # Stage 2's test: the branch prints anything
                empty = empties.pop(0)
                self.roots[id(empty)] = branch
                content = self.call(empty, ctx)
            else:
                content = []
            if branch.enclosure.branch == "else":
                otherwise = content
            else:
                branches.append((self.condition(mif, branch, ctx, marks, guard_var), content))
        return [jsx.Cond(branches, otherwise, marks=tuple(marks))]

    # ---- loops

    def loop(self, mloop: MLoop, ctx: _Ctx) -> list:
        loop_id = mloop.enclosure.node_id
        lst = ctx.list_node
        if lst is not None and lst.source_ids[0] == loop_id:
            return self.list_map(mloop, ctx)
        if loop_id in self.r_l5:  # R-L5: a loop that prints nothing is not a boundary
            return []
        if ctx.in_abstained:  # an abstained loop, written out for the developer over an empty list
            rows = f"rows_{safe_id(loop_id)}"
            ctx.locals.append(f"const {rows}: Record<string, unknown>[] = [];")
            ctx.file.use("react", "Fragment")
            marks = []
            self.place_markup_flags(mloop, ctx, marks)
            body = self.nodes(mloop.children, ctx.child(row=None))
            return [jsx.Map(rows, "_row, i", [jsx.Fragment(body, key="i")], marks=tuple(marks))]
        raise GenerationError("markup_not_generatable", f"loop {loop_id} is not part of a List")

    def list_map(self, mloop: MLoop, ctx: _Ctx) -> list:
        lst, out, marks = ctx.list_node, [], []
        self.place_markup_flags(mloop, ctx, marks)
        collection = self.collections.get(self.ids[id(lst)])
        if collection is not None and collection.result != "mapped":
            out.append(self.todo(mloop.enclosure.node_id, collection.reason, ctx, "collection"))
        ctx.uses.add("items")
        item = next((c for c in lst.children if c.type == "Item"), None)
        row = self.row_scope(lst)
        if item is not None:
            self.roots[id(item)] = mloop
            name, props = self.component(item, ctx, row)
            ctx.file.use(self.component_module(ctx.file, name), name)
            attrs = [jsx.Attr("key", code="i"), jsx.Attr("row", code="row")]
            if "data" in props:
                attrs.append(jsx.Attr("data", code="data"))
                ctx.uses.add("data")
            body = [jsx.Element(name, attrs)]
        else:  # R-L3: the one-element body stays an inline map; its row is the map's parameter
            had_row = "row" in ctx.uses
            body = self.nodes(mloop.children, ctx.child(row=row))
            if not had_row:
                ctx.uses.discard("row")
            elements = [b for b in body if isinstance(b, jsx.Element)]
            if len(body) != 1 or len(elements) != 1:
                raise GenerationError("markup_not_generatable", f"inline map {mloop.enclosure.node_id}")
            elements[0].attrs.insert(0, jsx.Attr("key", code="i"))
        out.append(jsx.Map("items", "row, i", body, marks=tuple(marks)))
        return out

    # ---- abstained regions

    def abstained(self, item: MAbstain, ctx: _Ctx) -> list:
        node = item.node
        source = node.source_ids[0]
        todo = self.todo(source, node.reason, ctx, "Stage 2 abstained; content in " + f"Abstained_{safe_id(source)}()")
        ctx.pending.append(item)
        return [todo]

    def abstained_function(self, item: MAbstain, ctx: _Ctx) -> None:
        node, file = item.node, ctx.file
        source = node.source_ids[0]
        name = f"Abstained_{safe_id(source)}"
        try:
            markup = build_region(item.region, self.outputs, self.boundary_config, self.abstain)
        except GenerationError as e:
            self.flag(ctx, "abstained_code_not_generated", INFO, source, e.detail, mark=ctx.function_key)
            return
        key = file.mark()
        inner = _Ctx(file=file, markup=markup, boundary=self.boundaries(markup), function=name, function_key=key,
                     row=ctx.row, in_abstained=True)
        self.queue_markup_flags(markup)
        slot = _Function(name, "", [], [], key)
        file.functions.append(slot)
        body = self.nodes(markup.children, inner)
        self.place_markup_flags(None, inner, [])
        slot.header = f"function {name}({self.signature(inner.uses, inner.row, required=())}) {{"
        slot.locals, slot.body = inner.locals, body
        for pending in inner.pending:
            self.abstained_function(pending, inner)

    # ---- components

    def boundaries(self, markup: Markup) -> dict:
        """Where each component whose markup is in this tree starts: id(markup node) -> Stage 2 node."""
        out = {}
        for lst in self.lists:
            loop = markup.loops.get(lst.source_ids[0])
            if loop is None:
                continue
            root = self.list_root(lst, loop, markup)
            self.roots[id(lst)] = root
            out[id(root)] = lst
        for source, node in self.conds.items():
            if node.type == "ConditionalComponent" and source == node.source_ids[0] and source in markup.ifs:
                self.roots[id(node)] = markup.ifs[source]
                out[id(markup.ifs[source])] = node
        return out

    def list_root(self, lst: ComponentNode, loop: MLoop, markup: Markup):
        guard = markup.ifs.get(lst.source_ids[1]) if len(lst.source_ids) > 1 else None
        container = None
        if lst.container is not None and lst.container.has_wrapper:
            container = loop.parent
            while container is not None and not (isinstance(container, MElement)
                                                 and container.tag not in self.boundary_config.table_section_tags):
                container = container.parent
            if container is None or container.tag != lst.container.tag:
                raise GenerationError("markup_not_generatable", f"List {lst.source_ids[0]}: container not found")
        candidates = [c for c in (container, guard) if c is not None]
        if not candidates:
            root = loop
        elif len(candidates) == 1:
            root = candidates[0]
        elif _is_ancestor(container, guard):
            root = container
        elif _is_ancestor(guard, container):
            root = guard
        else:
            raise GenerationError("markup_not_generatable", f"List {lst.source_ids[0]}: guard and container apart")
        # Everything inside the List's own loop is its row (an R-L3 row is a Stage 2 leaf, so the ifs in it
        # have no Stage 2 node); outside the loop, the container may hold only what Stage 2 put in this List.
        allowed = set(lst.node_ids) | {s for n in _walk(lst) for s in n.source_ids}
        for inner in _markup_walk(root, skip=loop):
            owner = inner.output.id if isinstance(inner, MDynamic) else (
                inner.enclosure.node_id if isinstance(inner, MLoop) else inner.if_id if isinstance(inner, MIf) else
                inner.node.source_ids[0] if isinstance(inner, MAbstain) else None)
            if owner is not None and owner not in allowed:
                raise GenerationError("markup_not_generatable",
                                      f"List {lst.source_ids[0]}: its container also holds {owner}")
        return root

    def call(self, component: ComponentNode, ctx: _Ctx) -> list:
        name, props = self.component(component, ctx, ctx.row)
        attrs = []
        if component.type == "List":
            collection = self.collections.get(self.ids[id(component)])
            if collection is not None and collection.result == "mapped" and self.schema is not None:
                attrs.append(jsx.Attr("items", code=prop_access("data", collection.schema_property)))
                ctx.uses.add("data")
            else:
                attrs.append(jsx.Attr("items", code="[]"))
        if "row" in props and component.type != "List":
            attrs.append(jsx.Attr("row", code="row"))
            ctx.uses.add("row")
        if "data" in props:
            attrs.append(jsx.Attr("data", code="data"))
            ctx.uses.add("data")
        ctx.file.use(self.component_module(ctx.file, name), name)
        return [jsx.Element(name, attrs)]

    def component(self, component: ComponentNode, caller: _Ctx, row: _Row | None) -> tuple[str, set]:
        if id(component) in self.generated:
            return self.generated[id(component)]
        name = self.names[id(component)]
        file = self.file(f"components/{name}.tsx")
        key = file.mark()
        root = self.roots[id(component)]
        ctx = _Ctx(file=file, markup=caller.markup, boundary=caller.boundary, function=name, function_key=key,
                   root=root, row=row, in_abstained=caller.in_abstained)
        if component.type == "List":
            ctx.list_node = component
            body = self.nodes([root], ctx)
            required = ("items",)
        elif component.type == "Item":
            ctx.row = self.row_scope(self.parents[id(component)])
            body = self.nodes(root.children, ctx)
            required = ("row",)
        elif component.type == "Empty":
            body = self.nodes(root.children, ctx)
            required = ()
        else:  # ConditionalComponent
            body = self.nodes([root], ctx)
            required = ()
        slot = _Function(name, "", ctx.locals, body, key)
        file.functions.insert(0, slot)
        props = set(ctx.uses) | set(required)
        slot.header = f"export function {name}({self.signature(props, ctx.row, required, component)}) {{"
        for pending in ctx.pending:
            self.abstained_function(pending, ctx)
        self.generated[id(component)] = (name, props)
        return name, props

    def signature(self, props: set, row: _Row | None, required: tuple = (), component=None) -> str:
        names, types = [], []
        if "items" in props:
            item_type = self.row_scope(component).type_code if component is not None and component.type == "List" \
                else "Record<string, unknown>"
            names.append("items")
            types.append(f"items: {item_type}[]")
        if "row" in props:
            names.append("row")
            types.append(f"row: {row.type_code if row else 'Record<string, unknown>'}")
        if "data" in props:
            names.append("data")
            types.append(f"data: {self.response_type}")
        if not names:
            return ""
        return "{ " + ", ".join(names) + " }: { " + "; ".join(types) + " }"

    def file(self, path: str) -> _File:
        if path not in self.files:
            self.files[path] = _File(path)
        return self.files[path]

    def queue_markup_flags(self, markup: Markup) -> None:
        for f in markup.flags:
            self.pending_flags.setdefault(id(f.anchor), []).append(f)

    # ---- the page

    def run(self) -> GenerationResult:
        page = self.file(f"app/{self.route}/page.tsx")
        key = page.mark()
        try:
            markup = build_page(self.outputs, self.boundary_config, self.abstain)
            ctx = _Ctx(file=page, markup=markup, boundary=self.boundaries(markup), function="Page", function_key=key)
            self.queue_markup_flags(markup)
            body = self.nodes(markup.children, ctx)
            self.place_markup_flags(None, ctx, [])
        except GenerationError as e:  # the last resort: the whole page is one C2Todo, never a guess
            self.files = {page.path: page}
            for state in (page.entries, page.functions, page.imports, page.type_imports, self.generated,
                          self.cond_marks, self.emitted_in, self.pending_flags):
                state.clear()
            ctx = _Ctx(file=page, markup=Markup([], {}, {}, []), boundary={}, function="Page", function_key=key)
            body = [self.todo("page", e.reason, ctx, e.detail)]
        if self.page_gap is not None:
            body.insert(0, self.todo("page", self.page_gap, ctx))
        fetch = "data" in ctx.uses
        header = f"export default {'async ' if fetch else ''}function Page() {{"
        page.functions.insert(0, _Function("Page", header, (
            [f"const data = await {self.fetch_name}();"] if fetch else []) + ctx.locals, body, key))
        if fetch:
            page.use(self.lib(page, "api"), self.fetch_name)
        for pending in ctx.pending:
            self.abstained_function(pending, ctx)
        for flags in self.pending_flags.values():  # anchored in markup that was never written: the page's line
            for f in flags:
                self.flag(ctx, f.flag, f.status, f.node_id, f.detail, mark=key)
        self.pending_flags.clear()
        self.stage2_reviews(page, key)
        return self.result()

    def stage2_reviews(self, page: _File, page_key: str) -> None:
        """Stage 2's review statuses carry through to the flag file (spec section 6)."""
        for node in self.order:
            if node.status != "review":
                continue
            if id(node) in self.cond_marks:
                file, mark, function, in_abstained = self.cond_marks[id(node)]
            else:
                first = next((n for n in node.node_ids if n in self.emitted_in), None)
                file, function, mark, in_abstained = self.emitted_in[first] if first else (
                    page, "Page", page_key, False)
            file.entries.append(_Entry("flag", "stage2_review", node.reason, REVIEW, ",".join(node.source_ids),
                                       f"{node.type} {self.ids[id(node)]}", mark, function, in_abstained))

    # ---- files

    def lib_files(self) -> dict[str, str]:
        files = {"lib/c2-todo.tsx": C2TODO_SOURCE}
        types = self.api_types()
        files["lib/api-types.ts"] = types
        files["lib/api.ts"] = self.api_module()
        return files

    def api_types(self) -> str:
        if self.schema is None:
            return "// No mapped endpoint: this page uses no response types.\nexport {};\n"
        named: dict[str, str] = {}

        def ts(schema: SchemaType, depth: int, top: bool = False) -> str:
            if schema.ref and not top:
                if schema.ref not in named:
                    named[schema.ref] = ""
                    named[schema.ref] = ts(schema, 0, top=True)
                code = schema.ref
            elif schema.kind == "object":
                ind = "  " * (depth + 1)
                required = set(schema.required)
                lines = [f"{ind}{name if _IDENTIFIER.fullmatch(name) else jsx.js_string(name)}"
                         f"{'' if name in required else '?'}: {ts(sub, depth + 1)};"
                         for name, sub in schema.properties]
                code = "{\n" + "\n".join(lines) + "\n" + "  " * depth + "}" if lines else "Record<string, never>"
            elif schema.kind == "array":
                inner = ts(schema.items, depth) if schema.items is not None else "unknown"
                code = f"({inner})[]" if " | " in inner else f"{inner}[]"
            else:
                code = {"string": "string", "number": "number", "integer": "number", "boolean": "boolean"}.get(
                    schema.kind, "unknown")
            return f"{code} | null" if schema.nullable and not top else code

        response = ts(self.schema, 0, top=True)
        blocks = [f"export type {name} = {body};" for name, body in named.items() if name != self.response_type]
        if self.response_type not in named:
            blocks.append(f"export type {self.response_type} = {response};")
        else:
            blocks.insert(0, f"export type {self.response_type} = {named[self.response_type]};")
        header = ("// Response types for this page, from Component 1's contract (docs/generation-spec.md section 8).\n"
                  "// Fields not in the contract do not appear: a missing field is a C2Todo, never row.<field>.\n")
        return header + "\n" + "\n\n".join(blocks) + "\n"

    def api_module(self) -> str:
        lines = []
        if self.schema is not None:
            lines += [f'import type {{ {self.response_type} }} from "./api-types";', ""]
        lines += ["// TODO(C2): token forwarding is Component 1's design (docs/generation-spec.md section 8).",
                  "function authHeaders(): Record<string, string> {", "  return {};", "}"]
        if self.schema is not None:
            path = self.report.endpoint.path
            lines += ["", f"export async function {self.fetch_name}(): Promise<{self.response_type}> {{",
                      f"  const response = await fetch(`${{process.env.{self.config.api_base_url_env}}}{path}`, {{",
                      '    cache: "no-store",', "    headers: authHeaders(),", "  });", "  return response.json();", "}"]
        else:
            lines += ["", f"// No fetch function: {self.page_gap or 'no mapped endpoint'}."]
        return "\n".join(lines) + "\n"

    def render(self, file: _File) -> tuple[str, dict]:
        out = jsx.Lines()
        imports = []
        modules = sorted(set(file.imports) | set(file.type_imports), key=lambda m: (m != "react", m))
        for module in modules:
            if module in file.imports:
                imports.append(f'import {{ {", ".join(sorted(file.imports[module]))} }} from "{module}";')
            if module in file.type_imports:
                imports.append(f'import type {{ {", ".join(sorted(file.type_imports[module]))} }} from "{module}";')
        for line in imports:
            out.add(line)
        for function in file.functions:
            if out.lines:
                out.add("")
            out.add(function.header, (function.key,))
            for local in function.locals:
                out.add("  " + local)
            if function.body:
                out.add("  return (")
                out.extend(jsx.render_expression(jsx._root(function.body), 2))
                out.add("  );")
            else:
                out.add("  return null;")
            out.add("}")
        return out.text(), out.marks

    def type_imports(self) -> None:
        for file in self.files.values():
            text = "\n".join(f.header for f in file.functions)
            names = set(re.findall(r"\b([A-Z]\w*)\b", text)) & self.type_names()
            for name in names:
                file.use(self.lib(file, "api-types"), name, type_only=True)

    def type_names(self) -> set:
        names = set()
        if self.response_type:
            names.add(self.response_type)

        def collect(schema):
            if schema is None:
                return
            if schema.ref:
                names.add(schema.ref)
            for _, sub in schema.properties:
                collect(sub)
            collect(schema.items)

        collect(self.schema)
        return names

    def nesting_check(self) -> None:
        """Spec v0.2 section 10: flag every place an HTML5 parser would nest the rendered page differently.

        Each tree checked is what a function renders: the Page, and each Abstained_ function on its own (not
        rendered, but generated code; its flags are marked in_abstained). Component calls are expanded in place,
        every branch of a conditional and one row of a map are included, C2Todo renders nothing."""
        functions = {f.name: (f, file) for file in self.files.values() for f in file.functions}

        def build(items: list, file: _File, function: str) -> list:
            out = []
            for item in items:
                if isinstance(item, jsx.Text):
                    if item.text.strip(HTML_SPACE):
                        out.append(nesting.TEXT)
                elif isinstance(item, jsx.Expr):
                    out.append(nesting.TEXT)
                elif isinstance(item, jsx.Element) and item.tag == "C2Todo":
                    continue
                elif isinstance(item, jsx.Element) and item.tag[:1].isupper():  # a component: its output here
                    called, called_file = functions[item.tag]
                    out += build(called.body, called_file, called.name)
                elif isinstance(item, jsx.Element):
                    element_id = next((a.value for a in item.attrs if a.name == "id" and a.value), None)
                    out.append(nesting.Node(item.tag, build(item.children, file, function),
                                            item.tag + (f"#{element_id}" if element_id else ""), (item, file, function)))
                elif isinstance(item, jsx.Fragment):
                    out += build(item.children, file, function)
                elif isinstance(item, jsx.Cond):
                    for _, content in item.branches:
                        out += build(content, file, function)
                    out += build(item.otherwise or [], file, function)
                elif isinstance(item, jsx.Map):
                    out += build(item.body, file, function)
            return out

        roots = [name for name in functions if name == "Page" or name.startswith("Abstained_")]
        for root in sorted(roots, key=lambda name: (name != "Page", name)):
            top, top_file = functions[root]
            in_abstained = root != "Page"
            for difference in nesting.compare(build(top.body, top_file, root),
                                              frozenset(self.boundary_config.void_tags)):
                element, file, function = difference.owner or (None, top_file, root)
                mark = file.mark() if element is not None else top.key
                if element is not None:
                    element.marks = element.marks + (mark,)
                detail = (f"{difference.path}: generated children [{', '.join(difference.ours)}]; an HTML5 parser "
                          f"builds [{', '.join(difference.parsed)}]")
                file.entries.append(_Entry("flag", "html_nesting_changed", "html_nesting_changed", REVIEW, "",
                                           detail, mark, function, in_abstained))

    def result(self) -> GenerationResult:
        self.type_imports()
        self.nesting_check()
        files, todos, flags = [], [], []
        for path in sorted(self.files):
            file = self.files[path]
            text, marks = self.render(file)
            files.append(GeneratedFile(path, text))
            fallback = {f.name: marks.get(f.key, 1) for f in file.functions}
            for e in file.entries:
                line = marks.get(e.mark) or fallback.get(e.function, 1)
                node = self.output_by_id.get(e.node_id)
                entry = GenerationEntry(
                    kind=e.kind, flag=e.flag, reason=e.reason, status=e.status, file=path, line=line,
                    node_id=e.node_id, detail=e.detail, function=e.function, in_abstained=e.in_abstained,
                    source_line=node.loc.start_line if node is not None and node.loc is not None else None,
                    severity="todo" if e.kind != "flag" or e.flag in TODO_FLAGS else e.status)
                (flags if e.kind == "flag" else todos).append(entry)
        for path, text in sorted(self.lib_files().items()):
            files.append(GeneratedFile(path, text))
        files.sort(key=lambda f: f.path)
        order = lambda e: (e.file, e.line, e.kind, e.node_id, e.flag or "", e.reason, e.detail)  # noqa: E731
        components = []
        for node in [self.tree.root] + [n for n in self.order if n.type in COMPONENT_FILES]:
            if node is self.tree.root:
                name, path = "Page", f"app/{self.route}/page.tsx"
            elif id(node) in self.generated:
                name = self.names[id(node)]
                path = f"components/{name}.tsx"
            else:
                continue
            count = sum(1 for t in todos if t.file == path and (t.function == name or t.function.startswith(
                "Abstained_")))
            components.append(GeneratedComponent(self.ids[id(node)], node.type, name, path, count))
        return GenerationResult(entrypoint=self.timeline.entrypoint, route=self.route, files=tuple(files),
                                components=tuple(components), todos=tuple(sorted(todos, key=order)),
                                flags=tuple(sorted(flags, key=order)))


def _split_declarations(style: str) -> list[str]:
    out, depth, quote, start = [], 0, None, 0
    for i, ch in enumerate(style):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            out.append(style[start:i])
            start = i + 1
    out.append(style[start:])
    return out


def _is_ancestor(outer, inner) -> bool:
    node = inner.parent
    while node is not None:
        if node is outer:
            return True
        node = node.parent
    return False


def _contains(branch: MBranch, target) -> bool:
    node = target.parent
    while node is not None:
        if node is branch:
            return True
        node = node.parent
    return False


def _walk(node: ComponentNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _markup_walk(node, skip=None):
    """Every markup node under `node`, without descending into `skip` (which is still yielded)."""
    yield node
    if node is skip:
        return
    for child in getattr(node, "children", []):
        yield from _markup_walk(child, skip)
    for branch in getattr(node, "branches", []):
        if isinstance(branch, MBranch):
            yield from _markup_walk(branch, skip)


# ----------------------------------------------------------------------------- entry point and output


def generate(tree: ComponentTree, requirements: RequirementsResult, report: ReconciliationReport, contract: Contract,
             timeline: Timeline, boundary_config: BoundaryConfig, config: GenerationConfig) -> GenerationResult:
    return Generator(tree, requirements, report, contract, timeline, boundary_config, config).run()


def write_files(result: GenerationResult, directory: Path) -> list[Path]:
    """Write the generated files into `directory`, replacing whatever an earlier run left there."""
    if directory.exists():
        shutil.rmtree(directory)
    written = []
    for f in result.files:
        path = directory / f.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as out:
            out.write(f.text)
        written.append(path)
    return written


def _entry_dict(e: GenerationEntry) -> dict:
    out = {"kind": e.kind}
    if e.kind == "flag":
        out["flag"] = e.flag
    out |= {"reason": e.reason, "severity": e.severity, "status": e.status, "file": e.file, "line": e.line,
            "nodeId": e.node_id, "function": e.function, "inAbstained": e.in_abstained, "sourceLine": e.source_line,
            "detail": e.detail}
    return out


def flag_key(e: GenerationEntry) -> str:
    return e.flag if e.flag == e.reason else f"{e.flag}:{e.reason}"


def by_severity(result: GenerationResult) -> dict[str, dict[str, int]]:
    """severity -> {reason: count}. C2Todos count by their reason, flags by flag_key."""
    out = {severity: {} for severity in SEVERITIES}
    for e in result.todos + result.flags:
        key = e.reason if e.kind != "flag" else flag_key(e)
        out[e.severity][key] = out[e.severity].get(key, 0) + 1
    return {severity: dict(sorted(reasons.items())) for severity, reasons in out.items()}


def counts(result: GenerationResult) -> dict:
    todo_reasons, flag_reasons = {}, {}
    for t in result.todos:
        todo_reasons[t.reason] = todo_reasons.get(t.reason, 0) + 1
    for f in result.flags:
        flag_reasons[flag_key(f)] = flag_reasons.get(flag_key(f), 0) + 1
    severity = {s: sum(n.values()) for s, n in by_severity(result).items()}
    components = len(result.components)
    without = sum(c.todos == 0 for c in result.components)
    return {
        "components": components,
        "componentsWithoutC2Todo": without,
        "metrics": {"componentsWithoutC2Todo": f"{without}/{components}",
                    "flagRate": severity["todo"] + severity["review"]},
        "severity": severity,
        "c2todos": dict(sorted(todo_reasons.items())),
        "flags": dict(sorted(flag_reasons.items())),
    }


def result_to_dict(result: GenerationResult, output_dir: str) -> dict:
    return {
        "stage": 5,
        "entrypoint": result.entrypoint,
        "route": result.route,
        "outputDir": output_dir,
        "counts": counts(result),
        "files": [f.path for f in result.files],
        "components": [{"componentId": c.component_id, "type": c.type, "name": c.name, "file": c.file,
                        "c2todos": c.todos} for c in result.components],
        "c2todos": [_entry_dict(t) for t in result.todos],
        "flags": [_entry_dict(f) for f in result.flags],
    }


def dumps(result: GenerationResult, output_dir: str) -> str:
    """Deterministic JSON: fixed key order, no timestamps or absolute paths."""
    return json.dumps(result_to_dict(result, output_dir), indent=2, ensure_ascii=False) + "\n"


def write_report(result: GenerationResult, path: Path, output_dir: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(result, output_dir))


def summary(result: GenerationResult) -> str:
    c = counts(result)
    s = c["severity"]
    return (f"[Stage 5] {c['metrics']['componentsWithoutC2Todo']} components without C2Todo · "
            f"{s['todo']} todo, {s['review']} review, {s['info']} info · flag rate {c['metrics']['flagRate']} · "
            f"{len(result.files)} files")
