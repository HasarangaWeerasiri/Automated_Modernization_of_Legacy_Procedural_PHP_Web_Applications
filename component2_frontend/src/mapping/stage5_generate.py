"""Stage 5: generation (docs/generation-spec.md v0.2).

Turns the Stage 2 component tree, the Stage 3 needs and the Stage 4 results
into Next.js (App Router) Server Components:

  app/<route>/page.tsx        the Page
  components/<Name>.tsx       one per List, Item, Empty, ConditionalComponent
  lib/api-types.ts            the response types the page uses
  lib/api.ts                  one typed fetch function per mapped endpoint (none: no file)
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
from dataclasses import dataclass, field, replace
from pathlib import Path

from src.mapping import jsx, nesting
from src.mapping.conditions import Scope, translate
from src.mapping.markup import (
    HTML_SPACE,
    GenerationError,
    Markup,
    MAbstain,
    MAttrGap,
    MAttrPresence,
    MBranch,
    MCall,
    MDynamic,
    MElement,
    MIf,
    MLoop,
    MText,
    build_page,
    starts_body,
    build_region,
    php_literal_text,
)
from src.mapping import app_units
from src.mapping import tags as tagtrack
from src.mapping.repair import repair
from src.mapping.stage3_requirements import _base_reads, _wrapper
from src.model import (
    BoundaryConfig,
    ComponentNode,
    ComponentTree,
    Contract,
    EndpointRef,
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
# flags that stand for a gap, like a C2Todo
TODO_FLAGS = ("attribute_omitted", "output_dropped", "non_html_response")
FLAGGED = ("missing", "cannot_reconcile")
_SPACE_RUN = re.compile(f"[{re.escape(HTML_SPACE)}]+")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_EVENT_ATTRIBUTE = re.compile(r"on[a-z]+")
_PLAIN_VARIABLE = re.compile(r"\$[A-Za-z_]\w*")
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


def severity_of(kind: str, flag: str | None, status: str, in_abstained: bool) -> tuple[str, str]:
    """(severity, severity if rendered), spec v0.4 section 12. A gap (C2Todo, unresolvedCondition, omitted
    attribute, dropped output) is todo; any other flag keeps its status. Everything found only in abstained,
    never-rendered code counts as info: the Abstain's own C2Todo is the one todo for the region. The entry is
    still listed, with what it would be if rendered."""
    if_rendered = "todo" if kind != "flag" or flag in TODO_FLAGS else status
    return ("info" if in_abstained else if_rendered), if_rendered


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


# The root layout of app/(plain)/: pages that use no shell pair (boundary-rules R-F2, "Route groups").
PLAIN_LAYOUT = """import type { ReactNode } from "react";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html>
      <body>{children}</body>
    </html>
  );
}
"""


@dataclass(frozen=True)
class UnitLayout:
    """Where a timeline's code goes in a whole app (roadmap Phase 2b). The default is one page on its own,
    as for a mock: app/<file name>/page.tsx."""

    mode: str = "page"  # page | component (R-F1: a function) | shell (R-F2: a header / footer pair)
    route: str = ""  # page: its path under app/, e.g. "(plain)/users/login"
    stem: str = ""  # PascalCase prefix of the List / Section components this unit owns
    name: str = ""  # component / shell: the exported function
    props: tuple = ()  # component / shell: typed props, ((name, "string" | "number"), ...)
    group: str = ""  # shell: its route group, e.g. "(our_header)"


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


@dataclass(frozen=True)
class _Query:
    """A List's own endpoint (reconciliation-spec v0.3, spec v0.13): what the function rendering it fetches."""

    ref: EndpointRef
    schema: SchemaType
    fetch_name: str  # e.g. getApiC2ReplayF00200138
    response_type: str
    local: str  # the awaited result, e.g. apiC2ReplayF00200138
    array: str  # the response's array property that holds the rows


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
    outer: _Row | None = None  # R-L4: the row of the loop around the List being written (its Item's `outer`)
    preserve: bool = False
    in_abstained: bool = False
    silent: bool = False  # inside an R-I7 region: nothing is rendered, only its flags are collected
    uses: set = field(default_factory=set)  # props the function reads: items, row, data
    fetches: set = field(default_factory=set)  # spec v0.13: ids of Lists whose own endpoint this function fetches
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
                 contract: Contract, timeline: Timeline, boundary_config: BoundaryConfig, config: GenerationConfig,
                 units=None, layout: UnitLayout | None = None):
        self.tree, self.report, self.timeline = tree, report, timeline
        self.boundary_config, self.config, self.react = boundary_config, config, config.react
        self.units, self.layout = units, layout or UnitLayout()
        self.route = self.layout.route or route_of(timeline.entrypoint)
        self.root_name = "Page" if self.layout.mode == "page" else self.layout.name
        self.root_path = f"app/{self.route}/page.tsx" if self.layout.mode == "page" else \
            f"components/{self.layout.name}.tsx"
        self.props = dict(self.layout.props)  # typed props of the function component being written
        self.calls = {c.id: c for c in timeline.calls}
        self.shell = tree.root.shell  # R-F2: the shell this page's content is wrapped in
        self.layout_head, self.layout_attrs, self.layout_marks = [], {}, {}  # shell: what goes in layout.tsx
        merged = app_units.outputs_with_slots(timeline) if units is not None else list(timeline.sequence)
        self.outputs = [n for n in merged if n.id in set(tree.root.node_ids)]
        self.output_by_id = {n.id: n for n in timeline.sequence}
        self.parts_by_id = {p.node_id: p for n in timeline.sequence for p in n.parts}
        self.part_owner = {p.node_id: n for n in timeline.sequence for p in n.parts}
        self.files: dict[str, _File] = {}
        self.generated: dict[int, tuple[str, set]] = {}  # id(component) -> (name, props it takes)
        self.cond_marks: dict[int, str] = {}  # id(Stage 2 conditional) -> (file path, mark) of its first if
        self.emitted_in: dict[str, tuple[_File, str, str, bool]] = {}  # node id -> file, function, mark, abstained
        self.pending_flags: dict[int, list] = {}  # id(markup anchor) -> markup flags not yet placed
        self.roots: dict[int, object] = {}  # id(component) -> its markup root (an MBranch for an Empty)
        self.failed: dict[int, GenerationError] = {}  # id(List) -> why its root could not be determined
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
        for n in nodes:  # consecutive unresolved calls are one Abstain node: each of its slots leads to it
            if n.type == "Abstain" and n.reason == "unresolved_call":
                self.abstain.update(dict.fromkeys(n.source_ids, n))
        self.lists = [n for n in nodes if n.type == "List"]
        self.r_l5 = {n.source_ids[0] for n in nodes if n.rule == "R-L5"}
        self.r_i7 = {n.source_ids[0] for n in nodes if n.rule == "R-I7"}  # only inline JS: not a boundary
        # R-I8: an if whose arms that print all exit after a redirect (nothing rendered), and an if whose
        # `then` arm exits after other output (`cond ? arm : rest`)
        self.r_i8_guard = {n.source_ids[0] for n in nodes if n.rule == "R-I8" and n.type == "Static"}
        self.r_i8_exit = {n.source_ids[0] for n in nodes if n.rule == "R-I8" and n.type == "InlineConditional"}
        # outside R-I8's scope (v0.14): its redirect arms are not treated as guards in the abstained code
        self.r_i8_out = {n.source_ids[0] for n in nodes if n.reason == "exit_arm_not_covered"}
        self.r_i2b = {n.source_ids[0]: n for n in nodes if n.rule == "R-I2b" and n.type == "InlineConditional"}
        # R-I8c: in a called function or an included file an arm that exits is not generated (flagged at its
        # include and call sites)
        self.r_i8c = {n.source_ids[0] for n in nodes if n.rule == "R-I8c"}
        self.redirects = {r.node_id: r for r in self.timeline.redirects}  # spec v0.10: header_redirect
        self.exit_silent = self.timeline.function is not None or self.layout.mode == "shell" or (
            self.units is not None and self.timeline.entrypoint in self.units.functions)
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
        # reconciliation-spec v0.3 / spec v0.13 (post-freeze): a List mapped to its loop query's own endpoint
        self.query_data = {}
        for list_id, list_ref in self.report.list_endpoints:
            mapped = self.collections.get(list_id)
            found = contract.endpoints.get((list_ref.method, list_ref.path, list_ref.status, list_ref.media_type))
            if mapped is None or mapped.result != "mapped" or found is None or found.method != "get" \
                    or "{" in list_ref.path:
                continue
            list_stem = pascal(list_ref.path)
            self.query_data[list_id] = _Query(list_ref, found.schema, f"get{list_stem}",
                                              found.schema.ref or f"{list_stem}Response",
                                              list_stem[:1].lower() + list_stem[1:], mapped.schema_property)

    def _names(self) -> None:
        self.names, used, lists, sections = {}, set(), 0, 0
        route = self.layout.stem or pascal(self.route)

        def unique(name):
            candidate, n = name, 2
            while candidate in used:
                candidate, n = f"{name}{n}", n + 1
            used.add(candidate)
            return candidate

        guards = [node for node in self.order if node.rule == "R-I2b" and node.type == "InlineConditional"]
        for node in self.order:
            if node.type == "List":
                lists += 1
                mapped = self.collections.get(self.ids[id(node)])
                # (a List with its own endpoint (spec v0.13) keeps the route-based name: every such endpoint's
                # array is `rows`, so the array's name would give two pages' different Lists one file name)
                if mapped is not None and mapped.result == "mapped" and self.ids[id(node)] not in self.query_data:
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
        for node in guards:  # R-I2b: the Empty of several Lists is named after the first of them
            stem = next((self.names[id(n)] for n in _walk(node) if n.type == "List"), f"{route}Lists")
            for child in node.children:
                if child.type == "Empty":
                    self.names[id(child)] = unique(stem + "Empty")

    def row_scope(self, list_node: ComponentNode) -> _Row:
        list_id = self.ids[id(list_node)]
        loop = next((e for n in self.outputs for e in n.enclosed_by if e.node_id == list_node.source_ids[0]), None)
        mapped = self.collections.get(list_id)
        fields, type_code = None, "Record<string, unknown>"
        query = self.query_data.get(list_id)
        if query is not None:  # spec v0.13: the rows come from the List's own endpoint
            array = dict(query.schema.properties).get(query.array)
            if array is not None and array.kind == "array" and array.items is not None \
                    and array.items.kind == "object":
                fields = array.items.properties
                type_code = array.items.ref or f"{query.response_type}[{jsx.js_string(query.array)}][number]"
        elif mapped is not None and mapped.result == "mapped" and self.schema is not None:
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
            self.flag(ctx, "output_dropped", REVIEW, _dynamic_id(node), "inside a dropped element", reason=why)
        for part in getattr(node, "content", None) or []:
            if isinstance(part, MDynamic):
                self.flag(ctx, "output_dropped", REVIEW, _dynamic_id(part), "inside a dropped element", reason=why)
        for attr in getattr(node, "attrs", []):
            for part in attr.parts or []:
                if isinstance(part, MDynamic):
                    self.flag(ctx, "output_dropped", REVIEW, _dynamic_id(part),
                              f"in {attr.name} of a dropped element", reason=why)
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
        for index, item in enumerate(items):
            if isinstance(item, MIf) and item.if_id in self.r_i8_exit and not ctx.silent:
                # R-I8, case 2: everything after the if, to the end of its block, is its implicit else
                out += self.exit_conditional(item, lambda: self.nodes(items[index + 1:], ctx), ctx)
                break
            out += self.node(item, ctx)
        return self.spaces(out)

    def exit_conditional(self, mif: MIf, rest, ctx: _Ctx) -> list:
        """`cond ? <the arm that exits> : <rest>` (spec v0.8 section 6a). Stage 2 made sure the exiting arm is
        the `then` arm; any other arm of the if is part of what follows when the condition is false."""
        marks, arm = [], mif.branches[0]
        self.place_markup_flags(mif, ctx, marks)
        self.place_markup_flags(arm, ctx, marks)
        condition = self.condition(mif, arm, ctx, marks)
        content = self.nodes(arm.children, ctx)
        branches, otherwise = [], None
        for branch in mif.branches[1:]:
            self.place_markup_flags(branch, ctx, marks)
            inner = self.nodes(branch.children, ctx)
            if branch.enclosure.branch == "else":
                otherwise = inner
            else:
                branches.append((self.condition(mif, branch, ctx, marks), inner))
        tail = [jsx.Cond(branches, otherwise)] if branches else list(otherwise or [])
        tail += rest()
        node = self.conds.get(mif.if_id)
        if node is not None and id(node) not in self.cond_marks:
            mark = ctx.file.mark()
            marks.append(mark)
            self.cond_marks[id(node)] = (ctx.file, mark, ctx.function, ctx.in_abstained)
        return [jsx.Cond([(condition, content)], tail or None, marks=tuple(marks))]

    # ---- R-I8, case 1: redirect guards

    def arm_outputs(self, mif: MIf, branch: MBranch) -> list:
        enclosure = branch.enclosure
        return [n for n in self.outputs if any(
            e.node_id == mif.if_id and (e.branch, e.cond_node_id) == (enclosure.branch, enclosure.cond_node_id)
            for e in n.enclosed_by)]

    def redirect_arm(self, mif: MIf, branch: MBranch) -> bool:
        """The arm always exits and prints only a redirect (the test Stage 2 makes, on the same text)."""
        nodes = self.arm_outputs(mif, branch)
        return bool(branch.enclosure.exits and nodes and not any(app_units.is_slot(n) for n in nodes)
                    and tagtrack.only_redirect("".join(tagtrack.output_text(n) for n in nodes)))

    def redirect_flag(self, mif: MIf, branch: MBranch, ctx: _Ctx) -> None:
        nodes = self.arm_outputs(mif, branch)
        target = tagtrack.redirect_target("".join(tagtrack.output_text(n) for n in nodes))
        self.flag(ctx, "redirect_guard", REVIEW, nodes[0].id,
                  ("redirect to " + target if target else "redirect") + ", then exit: not rendered; the redirect "
                  "belongs to Component 1 (R-I8)")

    def redirect_guard(self, mif: MIf, ctx: _Ctx) -> list:
        """An if that only redirects and exits: nothing is rendered for it and what follows renders."""
        silent = ctx.child(silent=True, row=None)
        for branch in mif.branches:
            if self.redirect_arm(mif, branch):
                self.redirect_flag(mif, branch, ctx)
                self.place_markup_flags(branch, silent, [])
                self.nodes(branch.children, silent)
            else:  # its arms are guards themselves
                self.place_markup_flags(branch, silent, [])
                for child in branch.children:
                    if isinstance(child, MIf):
                        self.redirect_guard(child, ctx)
        self.place_markup_flags(mif, silent, [])
        return []

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
        if isinstance(item, MCall):
            self.emitted_in.setdefault(item.node.id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
            return self.slot(item, ctx)
        if isinstance(item, MElement):
            return self.element(item, ctx)
        if isinstance(item, MLoop):
            return self.loop(item, ctx)
        if isinstance(item, MIf):
            return self.if_(item, ctx)
        if isinstance(item, MAbstain):
            return self.abstained(item, ctx)
        raise TypeError(item)

    def slot(self, item: MCall, ctx: _Ctx) -> list:
        """What is written at a call site (boundary-rules section 4, the call-entry table)."""
        node = item.node
        if self.shell is not None and node.id in (self.shell.header_call, self.shell.footer_call):
            return []  # the shell component is written around the page's content instead (page_body)
        if node.kind == app_units.CHILDREN:
            ctx.uses.add("children")
            return [jsx.Expr("children")]
        call = self.calls[node.id]
        unit = self.units.functions.get(call.callee) if call.callee else None
        if unit is None or unit.component is None:  # Stage 2 abstains on these; kept as a last line of defence
            return [self.todo(node.id, "unresolved_call", ctx)]
        ctx.file.use(self.component_module(ctx.file, unit.component), unit.component)
        return [jsx.Element(unit.component, self.prop_attrs(unit.props, call))]

    def prop_attrs(self, props, call) -> list:
        """A call's literal arguments as the props of the function component it calls."""
        values = app_units.prop_values(props, self.units.timelines[call.callee], call)
        return [jsx.Attr(name, value) if kind == "string" else jsx.Attr(name, code=repr(value))
                for name, kind, value in values]

    def page_body(self, children: list, ctx: _Ctx) -> list:
        """A page's content; inside a shell pair (R-F2) what lies between the two calls is the shell's children."""
        shell = self.shell
        calls = [i for i, c in enumerate(children) if isinstance(c, MCall) and shell is not None
                 and c.node.id in (shell.header_call, shell.footer_call)]
        exiting = next((i for i, c in enumerate(children) if isinstance(c, MIf) and c.if_id in self.r_i8_exit), None)
        if exiting is not None and len(calls) == 2 and exiting < calls[0]:
            # R-I8 before the shell's header: the shell is part of what renders when the arm does not exit
            return [*self.nodes(children[:exiting], ctx), *self.exit_conditional(
                children[exiting], lambda: self.page_body(children[exiting + 1:], ctx), ctx)]
        if len(calls) != 2:
            return self.nodes(children, ctx)
        first, last = calls
        unit = self.units.shells[shell.header]
        before, inside, after = (self.nodes(part, ctx) for part in (
            children[:first], children[first + 1:last], children[last + 1:]))
        attrs = []
        for function, call_id in ((unit.header, shell.header_call), (unit.footer, shell.footer_call)):
            attrs += self.prop_attrs([(n, t) for n, t, owner in unit.props if owner == function], self.calls[call_id])
        ctx.file.use(self.component_module(ctx.file, unit.name), unit.name)
        for child in (children[first], children[last]):
            self.emitted_in.setdefault(child.node.id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
        return [*before, jsx.Element(unit.name, attrs, inside), *after]

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
        outer = ctx.outer
        if outer is not None and outer.php_var and (ctx.row is None or ctx.row.php_var != outer.php_var) and any(
                base.var == outer.php_var for r in output.reads for base, _ in _base_reads(r)):
            ctx.uses.add("outer")  # R-L4: a read of the outer loop's row inside the inner loop
        if len(output.reads) != 1:
            return _Value(todo="expression_not_translatable")
        read, wrappers = output.reads[0], []
        while read.source_kind == "computed":
            name = _wrapper(read.expr)
            copy = name is None and _PLAIN_VARIABLE.fullmatch(read.expr)  # $v after `$v = $row['k'];`: the same value
            if (name is None and not copy) or len(read.derived_from) != 1:
                return _Value(todo=self.worst_reason(output.id, read), wrappers=tuple(wrappers))
            if name is not None and name not in self.config.escape_wrappers:
                wrappers.append(name)
            read = read.derived_from[0]
        if read.source_kind == "literal":
            text = php_literal_text(read.expr)
            if text is None or wrappers:
                return _Value(todo="expression_not_translatable")
            return _Value(text=text)
        if read.source_kind == "unresolved" and read.var and not read.path and read.var[1:] in self.props:
            return _Value(code=read.var[1:], wrappers=tuple(wrappers))  # a parameter that is a typed prop (R-F1)
        result = self.results.get((output.id, read.expr))
        if result is None:
            return _Value(todo="no_stage4_result", wrappers=tuple(wrappers))
        if result.result == "excluded":
            return _Value(todo=self.config.context_reasons.get(result.reason, result.reason), wrappers=tuple(wrappers))
        if result.result != "matched":
            return _Value(todo=result.reason, wrappers=tuple(wrappers))
        column = result.need.column
        if result.need.row_of is not None:
            if ctx.row is not None and ctx.row.list_id == result.need.row_of and ctx.row.fields is not None:
                schema, code = dict(ctx.row.fields)[column], prop_access("row", column)
                ctx.uses.add("row")
            elif ctx.outer is not None and ctx.outer.list_id == result.need.row_of and ctx.outer.fields is not None:
                schema, code = dict(ctx.outer.fields)[column], prop_access("outer", column)  # R-L4 (v0.14)
                ctx.uses.add("outer")
            else:
                raise GenerationError("markup_not_generatable", f"{output.id}: row field outside its Item")
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
        node_id = _dynamic_id(item)  # spec v0.6: one expression of an echo string has its own (AST) id
        marks = []
        if not self.escaped(output):
            marks.append(self.flag(ctx, "escaping_changed", INFO, node_id,
                                   "unescaped in PHP; React escapes it (an expected difference)"))
        value = self.value(output, ctx)
        if value.text is not None:
            return [jsx.Text(value.text, marks=tuple(marks))]
        if value.code is None:
            detail = f"wrappers: {', '.join(value.wrappers)}" if value.wrappers else ""
            if item.part is not None:  # one expression of an echo string: say which
                detail = f"part {item.part.expr}" + (f"; {detail}" if detail else "")
            node = self.todo(node_id, value.todo, ctx, detail)
            node.marks = tuple(marks) + node.marks
            return [node]
        code = value.code
        for wrapper in value.wrappers:
            code += f" /* TODO(C2): {wrapper} */"
            marks.append(self.flag(ctx, "unsupported_wrapper", REVIEW, node_id, f"{wrapper}() not applied"))
        return [jsx.Expr(code, marks=tuple(marks))]

    # ---- elements and attributes

    def element(self, el: MElement, ctx: _Ctx) -> list:
        tag, marks = el.tag, []
        self.emitted_in.setdefault(el.node_id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
        if self.layout.mode == "shell" and tag == "head":  # R-F2: its literal parts go to the group's layout.tsx
            return self.shell_head(el, ctx)
        if self.layout.mode == "shell" and tag in self.config.document_shell_tags:
            self.layout_attrs[tag], self.layout_marks[tag] = self.shell_attributes(el, ctx)
            self.place_markup_flags(el, ctx, [])
            return self.nodes(el.children, ctx)
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

    # ---- the shell's document parts (R-F2): app/(<h>)/layout.tsx

    def layout_ctx(self, ctx: _Ctx) -> _Ctx:
        file = self.file(f"app/{self.layout.group}/layout.tsx")
        if not file.functions:
            file.functions.append(_Function("RootLayout", "", [], [], file.mark()))
        return _Ctx(file=file, markup=ctx.markup, boundary={}, function="RootLayout",
                    function_key=file.functions[0].key)

    def shell_head(self, el: MElement, ctx: _Ctx) -> list:
        """<head> of a shell header: every child that is literal goes into the layout; a child with anything
        dynamic in it is left out and flagged document_shell."""
        layout, literal = self.layout_ctx(ctx), []
        for child in el.children:
            if _is_static(child):
                literal.append(child)
            else:
                self.flag(ctx, "document_shell", REVIEW, getattr(child, "node_id", el.node_id),
                          "dynamic content in <head> is not in app/layout.tsx")
                self.drop_subtree_flags(child, ctx, "document_shell")
        self.layout_head = self.nodes(literal, layout)
        self.place_markup_flags(el, layout, [])
        return []

    def shell_attributes(self, el: MElement, ctx: _Ctx) -> tuple[list, tuple]:
        """The literal attributes of <html> / <body>, written for the layout; a dynamic one is flagged."""
        layout, marks = self.layout_ctx(ctx), []
        for a in el.attrs:
            if any(not isinstance(part, str) for part in a.parts or []):
                self.flag(ctx, "document_shell", REVIEW, a.node_id,
                          f"dynamic {a.name or 'attribute'} on <{el.tag}> is not in app/layout.tsx")
        literal = replace(el, attrs=[a for a in el.attrs if all(isinstance(p, str) for p in a.parts or [])])
        return self.attributes(literal, layout, marks), tuple(marks)

    def write_layout(self) -> None:
        """The root layout of the shell's route group: <html>, <head> and <body> as the header wrote them."""
        file = self.file(f"app/{self.layout.group}/layout.tsx")
        if not file.functions:
            file.functions.append(_Function("RootLayout", "", [], [], file.mark()))
        function = file.functions[0]
        file.use("react", "ReactNode", type_only=True)
        function.header = "export default function RootLayout({ children }: { children: ReactNode }) {"
        head = [jsx.Element("head", [], self.layout_head)] if self.layout_head else []
        body = jsx.Element("body", self.layout_attrs.get("body", []), [jsx.Expr("children")],
                           marks=self.layout_marks.get("body", ()))
        function.body = [jsx.Element("html", self.layout_attrs.get("html", []), [*head, body],
                                     marks=self.layout_marks.get("html", ()))]

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

    def fixed_value(self, el: MElement) -> bool:
        """Spec v0.7 section 5: `value` on a submit / button / reset / image / hidden / checkbox / radio input is
        a label or a fixed value, not form state. A `type` that is not literal is not known to be one of them."""
        kind = next((a for a in el.attrs if a.name == "type"), None)
        if el.tag != "input" or kind is None or not all(isinstance(p, str) for p in kind.parts or []):
            return False
        return html.unescape("".join(kind.parts or [])).strip().lower() in self.config.fixed_value_input_types

    def prop_name(self, el: MElement, name: str) -> str | None:
        tag = el.tag
        renamed = self.config.form_state.get(tag, {}).get(name) or self.config.attribute_renames.get(name)
        if renamed and name == "value" and self.fixed_value(el):
            renamed = None  # stays `value`
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
        """The JSX props of an element. Each attribute name is settled at its first occurrence in the tag, with
        every later occurrence of the same name (boundary-rules R-I3b; spec section 5b, duplicate attribute)."""
        occurrences: dict[str, list] = {}
        for a in el.attrs:
            if not isinstance(a, MAttrGap):
                for name in _attribute_names(a):
                    occurrences.setdefault(name, []).append(a)
        out, settled, conditions = [], set(), {}
        for a in el.attrs:
            if isinstance(a, MAttrGap):  # spec v0.8 section 5b: only this tag's attribute is lost
                marks.append(self.flag(ctx, "attribute_omitted", REVIEW, a.node_id, f"an attribute of <{el.tag}>",
                                       reason=a.reason))
                continue
            for name in _attribute_names(a):
                if name not in settled:
                    settled.add(name)
                    out += self.settle(name, occurrences[name], el, ctx, marks, conditions)
        return out

    def settle(self, name: str, found: list, el: MElement, ctx: _Ctx, marks: list, conditions: dict) -> list:
        """All occurrences of one attribute name in a tag, in order. A browser keeps the first one that is
        there: a written attribute ends the search; an if (R-I3b) applies when its condition holds, and the
        next occurrence otherwise (v0.14: `cond ? "conditional" : "later"`)."""
        chain = []
        for occurrence in found:
            chain.append(occurrence)
            if not isinstance(occurrence, MAttrPresence):
                break
        for later in found[len(chain):]:  # after a written one: no effect in a browser
            node_id = later.node_id if not isinstance(later, MAttrPresence) else _presence_attr(later, name).node_id
            what = "conditional" if isinstance(later, MAttrPresence) else "second"
            marks.append(self.flag(ctx, "duplicate_attribute", INFO, node_id,
                                   f"{what} {name} on <{el.tag}> has no effect: a browser keeps the first"))
        if len(chain) == 1 and not isinstance(chain[0], MAttrPresence):
            attr = self.static_attribute(chain[0], el, ctx, marks)
            return [attr] if attr is not None else []
        if _EVENT_ATTRIBUTE.fullmatch(name):
            marks.append(self.flag(ctx, "inline_js_dropped", REVIEW, _presence_attr(chain[0], name).node_id,
                                   f"{name} on <{el.tag}>"))
            return []
        prop, values = None, []  # per occurrence: [value code per arm] for an if, the value code for a written one
        for occurrence in chain:
            written = [_presence_attr(occurrence, name, arm) for arm in range(len(occurrence.branches))] \
                if isinstance(occurrence, MAttrPresence) else [occurrence]
            codes = []
            for attribute in written:
                attr = self.static_attribute(attribute, el, ctx, marks) if attribute is not None else None
                if attribute is not None and attr is None:
                    return []  # dropped or omitted for its own reason (flagged), e.g. `selected` on <option>
                if attr is not None:
                    prop = attr.name
                codes.append(None if attr is None else _value_code(attr))
            values.append(codes)
        code = "undefined"
        for occurrence, codes in reversed(list(zip(chain, values))):
            if not isinstance(occurrence, MAttrPresence):
                code = codes[0]
                continue
            if id(occurrence) not in conditions:
                conditions[id(occurrence)] = self.presence_conditions(occurrence, ctx, marks)
            code = _presence_code(conditions[id(occurrence)], codes, code)
        return [jsx.Attr(prop, code=code)] if prop is not None else []

    def presence_conditions(self, a: MAttrPresence, ctx: _Ctx, marks: list) -> tuple:
        """(the condition of each arm, None for an `else`; the condition of a `then` arm that prints nothing)."""
        conditions = []
        for enclosure, _ in a.branches:
            if enclosure.branch == "else":
                conditions.append(None)
                continue
            node_id = a.if_id if enclosure.branch == "then" else enclosure.cond_node_id
            translation = translate(enclosure.cond_expr, self.scope(ctx), self.config.condition_context_reasons)
            if translation.code is not None:
                self.translated(translation, node_id, ctx, marks)
                conditions.append(translation.code)
            else:
                conditions.append(self.unresolved(node_id, translation.reason, ctx, marks))
        silent_then = None  # the if's `then` arm prints nothing: its condition is not in the timeline
        if a.branches[0][0].branch != "then":
            silent_then = self.unresolved(a.if_id, "condition_unavailable", ctx, marks)
        return conditions, silent_then

    def static_attribute(self, a, el: MElement, ctx: _Ctx, marks: list):
        """One written attribute as a JSX prop, or None when it is dropped or omitted (flagged)."""
        name = a.name
        if _EVENT_ATTRIBUTE.fullmatch(name):
            marks.append(self.flag(ctx, "inline_js_dropped", REVIEW, a.node_id, f"{name} on <{el.tag}>"))
            return None
        omitted = self.config.omitted_attributes.get(el.tag, {}).get(name)
        if omitted:
            marks.append(self.flag(ctx, omitted, REVIEW, a.node_id, f"{name} on <{el.tag}> omitted"))
            return None
        if name == "style":
            return self.style(a, el, ctx, marks)
        prop = self.prop_name(el, name)
        kinds = self.prop_kinds(el.tag, prop) if prop is not None else None
        if prop is not None and kinds is None and "-" in prop and not prop.startswith("aria-"):
            kinds = "s"  # TypeScript does not check hyphenated JSX attribute names it does not know
        if kinds is None:
            marks.append(self.flag(ctx, "attribute_dropped", REVIEW, a.node_id,
                                   f"{name} is not in React's types for <{el.tag}>"))
            return None
        return self.attribute(a, el, prop, kinds, ctx, marks)

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
                output, node_id = part.output, _dynamic_id(part)
                self.emitted_in.setdefault(output.id, (ctx.file, ctx.function, ctx.function_key, ctx.in_abstained))
                if not self.escaped(output):
                    marks.append(self.flag(ctx, "escaping_changed", INFO, node_id,
                                           "unescaped in PHP; React escapes it (an expected difference)"))
                value = self.value(output, ctx)
                if value.text is not None:
                    pieces.append(("text", value.text))
                elif value.code is None:
                    failures.append((value.todo, node_id))
                else:
                    code = value.code
                    for wrapper in value.wrappers:
                        code += f" /* TODO(C2): {wrapper} */"
                        marks.append(self.flag(ctx, "unsupported_wrapper", REVIEW, node_id,
                                               f"{wrapper}() not applied"))
                    pieces.append(("code", code))
            elif isinstance(part, MAttrGap):  # start-tag fallback: something in the value cannot be written
                failures.append((part.reason, part.node_id))
            else:  # MAttrIf: an if inside the value (R-I3)
                code, inner = self.attribute_if(part, ctx, marks)
                failures += inner
                pieces.append(("code", code or ""))
        return (None, failures) if failures else (jsx.template_literal(pieces), [])

    def attribute_if(self, part, ctx: _Ctx, marks: list):
        branches, otherwise, failures = [], '""', []
        if part.branches and part.branches[0][0].branch != "then":
            # The `then` arm prints nothing, so the timeline does not carry its condition: when the other arms
            # apply is not known.
            failures.append(("condition_unavailable", part.if_id))
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
                     guard_var=guard_var, props=tuple(self.props.items()))

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
            return self.page_body(mif.branches[0].children, ctx)
        if mif.if_id in self.r_i8_guard and not ctx.silent:  # R-I8: a redirect guard is not a component
            return self.redirect_guard(mif, ctx)
        if mif.if_id in self.r_i7 or mif.if_id in self.r_i8c or ctx.silent:
            # R-I7: only inline JS inside; R-I8c: an exit arm of a function or an included file. Its flags are
            # placed, nothing more
            silent = ctx.child(silent=True, row=None)
            for branch in mif.branches:
                self.place_markup_flags(branch, silent, [])
                self.nodes(branch.children, silent)
            self.place_markup_flags(mif, silent, [])
            return []
        lst = ctx.list_node
        if lst is not None and len(lst.source_ids) > 1 and lst.source_ids[1] == mif.if_id:
            return self.guard(mif, ctx)
        return self.conditional(mif, ctx, self.conds.get(mif.if_id))

    def conditional(self, mif: MIf, ctx: _Ctx, stage2_node: ComponentNode | None) -> list:
        """One Cond per if: then/elseif/else of one if -> a ternary chain; never merged with a sibling if (R-I6)."""
        marks = []
        self.place_markup_flags(mif, ctx, marks)
        contents = []
        guard = self.r_i2b.get(mif.if_id)  # R-I2b: one guard around several Lists
        empties = [c for c in guard.children if c.type == "Empty"] if guard is not None else []
        for branch in mif.branches:
            self.place_markup_flags(branch, ctx, marks)
            if branch.enclosure.exits and self.exit_silent:  # R-I8c: not generated, flagged at its sites
                self.nodes(branch.children, ctx.child(silent=True, row=None))
                contents.append([])
            elif branch.enclosure.exits and mif.if_id not in self.r_i8_out and self.redirect_arm(mif, branch):
                # R-I8 beside arms that print something else: this arm renders nothing
                self.redirect_flag(mif, branch, ctx)
                self.nodes(branch.children, ctx.child(silent=True, row=None))
                contents.append([])
            elif guard is not None and not any(isinstance(n, MLoop) for n in _markup_walk(branch)) and empties:
                empty = empties.pop(0)  # the guard's other arm is the Lists' Empty
                self.roots[id(empty)] = branch
                contents.append(self.call(empty, ctx))
            else:
                contents.append(self.nodes(branch.children, ctx))
        if not any(contents):
            return []  # renders nothing whatever the condition says
        branches, otherwise = self.silent_then(mif, ctx, marks), None
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

    def silent_then(self, mif: MIf, ctx: _Ctx, marks: list) -> list:
        """The `then` arm of an if whose `then` prints nothing: [(its condition, no content)], else [].

        Only arms that print are in the timeline, and an `else` carries no condition text (Member 01 Q2). So an
        if that prints only in its `elseif` / `else` arms still starts with its `then` arm here, which renders
        nothing; its condition is not available (`condition_unavailable`)."""
        if not mif.branches or mif.branches[0].enclosure.branch == "then":
            return []
        return [(self.unresolved(mif.if_id, "condition_unavailable", ctx, marks), [])]

    def guard(self, mif: MIf, ctx: _Ctx) -> list:
        """R-I2: the List's guard. The loop's branch renders the List; any other branch is an Empty."""
        lst, marks = ctx.list_node, []
        loop = ctx.markup.loops[lst.source_ids[0]]
        empties = [c for c in lst.children if c.type == "Empty"]
        iter_expr = loop.enclosure.iter_expr
        guard_var = iter_expr if iter_expr and re.fullmatch(r"\$[A-Za-z_]\w*", iter_expr) else None
        branches, otherwise = self.silent_then(mif, ctx, marks), None
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
        if loop_id in self.r_i7 or ctx.silent:  # R-I7: the same, for a loop that prints only inline JS
            silent = ctx.child(silent=True, row=None)
            self.place_markup_flags(mloop, silent, [])
            self.nodes(mloop.children, silent)
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

    def iterates_over(self, loop, outer: _Row) -> bool:
        """The inner loop's iterable uses the outer row: its expression names the outer loop's variable, or it
        walks the result of a query whose SQL does (`... WHERE id='$row[post_id]'`)."""
        if not outer.php_var or loop.value_var == outer.php_var:
            return False
        uses = re.compile(re.escape(outer.php_var) + r"(?![A-Za-z0-9_])")
        text = loop.iter_expr or ""
        if uses.search(text):
            return True
        return any(q.result_var and re.search(re.escape(q.result_var) + r"(?![A-Za-z0-9_])", text)
                   and uses.search(q.sql) for q in self.timeline.queries.values())

    def list_map(self, mloop: MLoop, ctx: _Ctx) -> list:
        lst, out, marks = ctx.list_node, [], []
        self.place_markup_flags(mloop, ctx, marks)
        collection = self.collections.get(self.ids[id(lst)])
        if collection is not None and collection.result != "mapped":
            out.append(self.todo(mloop.enclosure.node_id, collection.reason, ctx, "collection"))
        ctx.uses.add("items")
        item = next((c for c in lst.children if c.type == "Item"), None)
        row = self.row_scope(lst)
        if ctx.outer is not None and self.iterates_over(mloop.enclosure, ctx.outer):
            ctx.uses.add("outer")  # R-L4: what the inner loop iterates over comes from the outer row
        if item is None and "R-L3b" in lst.rules:  # a text-only body: an inline map of keyed Fragments (spec 5a)
            had_row = "row" in ctx.uses
            body = self.nodes(mloop.children, ctx.child(row=row))
            if not had_row:
                ctx.uses.discard("row")
            ctx.file.use("react", "Fragment")
            body = [jsx.Fragment(body, key="i")]
        elif item is not None:
            self.roots[id(item)] = mloop
            name, props = self.component(item, ctx, row)
            ctx.file.use(self.component_module(ctx.file, name), name)
            attrs = [jsx.Attr("key", code="i"), jsx.Attr("row", code="row")]
            if "outer" in props:
                attrs.append(jsx.Attr("outer", code="outer"))
                ctx.uses.add("outer")
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
            key = jsx.Attr("key", code="i")
            if elements[0].tag == "C2Todo":  # keep every C2Todo written as <C2Todo id=... reason=... />
                elements[0].attrs.append(key)
            else:
                elements[0].attrs.insert(0, key)
        out.append(jsx.Map("items", "row, i", body, marks=tuple(marks)))
        return out

    # ---- abstained regions

    def abstained(self, item: MAbstain, ctx: _Ctx) -> list:
        node = item.node
        source = node.source_ids[0]
        if isinstance(item.region, OutputNode) and app_units.is_slot(item.region):
            count = len(node.source_ids)  # consecutive unresolved calls are one gap, listing all their ids
            kind = "an include" if item.region.kind == app_units.INCLUDE else "a call"
            detail = f"{kind} whose output is not generated here" if count == 1 else \
                f"{count} consecutive calls / includes whose output is not generated here: one gap"
            return [self.todo(",".join(node.source_ids), node.reason, ctx, detail)]
        todo = self.todo(source, node.reason, ctx, "Stage 2 abstained; content in " + f"Abstained_{safe_id(source)}()")
        ctx.pending.append((item, ctx.row))  # the row in scope where the region stands (e.g. an R-L3 row)
        return [todo]

    def abstained_function(self, pending: tuple, ctx: _Ctx) -> None:
        item, row = pending
        node, file = item.node, ctx.file
        source = node.source_ids[0]
        name = f"Abstained_{safe_id(source)}"
        try:
            markup = build_region(item.region, self.outputs, self.boundary_config, self.abstain)
            repair(markup)
        except GenerationError as e:
            self.flag(ctx, "abstained_code_not_generated", INFO, source, e.detail, mark=ctx.function_key)
            return
        key = file.mark()
        inner = _Ctx(file=file, markup=markup, boundary=self.boundaries(markup), function=name, function_key=key,
                     row=row, in_abstained=True)
        self.queue_markup_flags(markup)
        slot = _Function(name, "", [], [], key)
        file.functions.append(slot)
        entries = len(file.entries)
        try:
            body = self.nodes(markup.children, inner)
        except GenerationError as e:  # leave no trace of the half-written function, only the flag
            del file.entries[entries:]
            file.functions.remove(slot)
            self.flag(ctx, "abstained_code_not_generated", INFO, source, e.detail, mark=ctx.function_key)
            return
        self.place_markup_flags(None, inner, [])
        fetches = self.fetch_locals(inner)
        slot.header = f"{'async ' if fetches else ''}function {name}({self.signature(inner.uses, inner.row, required=())}) {{"
        slot.locals, slot.body = fetches + inner.locals, body
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
            try:
                root = self.list_root(lst, loop, markup)
            except GenerationError as error:
                # The List cannot own its container (spec v0.4 section 12: the smallest enclosing component is
                # replaced, never the page). Its loop marks its place; the component becomes one C2Todo.
                root = loop
                self.failed[id(lst)] = error
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
            owner = inner.output.id if isinstance(inner, MDynamic) else inner.node.id if isinstance(inner, MCall) else (
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
            query = self.query_data.get(self.ids[id(component)])
            if query is not None:  # spec v0.13: the function rendering the List fetches its own endpoint
                attrs.append(jsx.Attr("items", code=prop_access(query.local, query.array)))
                ctx.fetches.add(self.ids[id(component)])
            elif collection is not None and collection.result == "mapped" and self.schema is not None:
                attrs.append(jsx.Attr("items", code=prop_access("data", collection.schema_property)))
                ctx.uses.add("data")
            else:
                attrs.append(jsx.Attr("items", code="[]"))
        if "row" in props and component.type != "List":
            attrs.append(jsx.Attr("row", code="row"))
            ctx.uses.add("row")
        if "outer" in props and component.type == "List" and ctx.row is not None:
            attrs.append(jsx.Attr("outer", code="row"))  # R-L4: the inner List is given the outer Item's row
            ctx.uses.add("row")
        elif "outer" in props and component.type != "List":
            attrs.append(jsx.Attr("outer", code="outer"))
            ctx.uses.add("outer")
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
                   root=root, row=row, in_abstained=caller.in_abstained,
                   outer=row if component.type == "List" else caller.outer)
        if component.type == "Item" and "outer" in caller.uses:
            ctx.uses.add("outer")  # R-L4: the inner Item receives the outer row whenever its List does
        required = {"List": ("items",), "Item": ("row",)}.get(component.type, ())
        if component.type == "List":
            ctx.list_node = component
        elif component.type == "Item":
            ctx.row = self.row_scope(self.parents[id(component)])
        files, generated = set(self.files), set(self.generated)
        pending_flags = {k: list(v) for k, v in self.pending_flags.items()}
        try:
            if id(component) in self.failed:  # its place in the markup could not be determined
                raise self.failed[id(component)]
            body = self.nodes(root.children if component.type in ("Item", "Empty") else [root], ctx)
        except GenerationError as e:
            body = self.component_fallback(component, name, ctx, e, files, generated, pending_flags)
        fetches = self.fetch_locals(ctx)
        slot = _Function(name, "", fetches + ctx.locals, body, key)
        file.functions.insert(0, slot)
        props = set(ctx.uses) | set(required)
        slot.header = (f"export {'async ' if fetches else ''}function {name}("
                       f"{self.signature(props, ctx.row, required, component, ctx.outer)}) {{")
        for pending in ctx.pending:
            self.abstained_function(pending, ctx)
        self.generated[id(component)] = (name, props)
        return name, props

    def component_fallback(self, component: ComponentNode, name: str, ctx: _Ctx, error: GenerationError,
                           files: set, generated: set, pending_flags: dict) -> list:
        """Spec v0.4 section 12: a component that cannot be generated becomes one C2Todo, never the whole page.

        Everything the attempt wrote is undone (its own file's code and entries, any component file it created),
        and markup flags it had placed are queued again so they still reach the flag file."""
        file = ctx.file
        for path in set(self.files) - files:
            del self.files[path]
        for key in set(self.generated) - generated:
            del self.generated[key]
        for state in (file.entries, file.functions, file.imports, file.type_imports):
            state.clear()
        ctx.uses.clear()
        ctx.fetches.clear()
        ctx.locals.clear()
        ctx.pending.clear()
        self.pending_flags = pending_flags
        live = {id(f) for f in self.files.values()}
        for key, (old_file, *_rest) in list(self.cond_marks.items()):  # reviews now point at the fallback
            if old_file is file or id(old_file) not in live:
                self.cond_marks[key] = (file, ctx.function_key, name, ctx.in_abstained)
        for node_id, (old_file, *_rest) in list(self.emitted_in.items()):
            if old_file is file or id(old_file) not in live:
                self.emitted_in[node_id] = (file, name, ctx.function_key, ctx.in_abstained)
        source = component.source_ids[0] if component.source_ids else component.node_ids[0]
        return [self.todo(source, error.reason, ctx, f"{component.type} {name}: {error.detail}")]

    def unit_signature(self, file: _File) -> str:
        """The props of a function component (its typed props) or of a shell (children, then its typed props)."""
        names, types = [], []
        if self.layout.mode == "shell":
            file.use("react", "ReactNode", type_only=True)
            names.append("children")
            types.append("children?: ReactNode")  # optional: a page may print nothing between header and footer
        for name, kind in self.layout.props:
            names.append(name)
            types.append(f"{name}: {kind}")
        if not names:
            return ""
        return "{ " + ", ".join(names) + " }: { " + "; ".join(types) + " }"

    def signature(self, props: set, row: _Row | None, required: tuple = (), component=None,
                  outer: _Row | None = None) -> str:
        names, types = [], []
        if "items" in props:
            item_type = self.row_scope(component).type_code if component is not None and component.type == "List" \
                else "Record<string, unknown>"
            names.append("items")
            types.append(f"items: {item_type}[]")
        if "row" in props:
            names.append("row")
            types.append(f"row: {row.type_code if row else 'Record<string, unknown>'}")
        if "outer" in props:
            names.append("outer")
            types.append(f"outer: {outer.type_code if outer else 'Record<string, unknown>'}")
        if "data" in props:
            names.append("data")
            types.append(f"data: {self.response_type}")
        if not names:
            return ""
        return "{ " + ", ".join(names) + " }: { " + "; ".join(types) + " }"

    def fetch_locals(self, ctx: _Ctx) -> list:
        """Spec v0.13: `const <local> = await <fetch>();` for each List endpoint the function's Lists read (once per
        endpoint, by name), importing the fetch functions. Empty without per-query endpoints."""
        lines = {}
        for list_id in ctx.fetches:
            query = self.query_data[list_id]
            ctx.file.use(self.lib(ctx.file, "api"), query.fetch_name)
            lines[query.local] = f"const {query.local} = await {query.fetch_name}();"
        return [lines[local] for local in sorted(lines)]

    def file(self, path: str) -> _File:
        if path not in self.files:
            self.files[path] = _File(path)
        return self.files[path]

    def queue_markup_flags(self, markup: Markup) -> None:
        for f in markup.flags:
            self.pending_flags.setdefault(id(f.anchor), []).append(f)

    # ---- the page

    def error_output_arms(self) -> list | None:
        """Spec v0.11 section 6a: the source lines of a file's top level whose only output is exit arms that
        R-I8 / R-I8c decide (attendance `connect.php`, phpBlog `core.php`), or None. Not a page: what it prints
        is an error stop or a redirect, and its include sites carry the R-I8c flags."""
        if self.timeline.function is not None or self.layout.mode != "page" or not self.timeline.sequence:
            return None
        decided = {i for n in _walk(self.tree.root) if n.rule in ("R-I8", "R-I8c") and n.status != "abstain"
                   for i in n.node_ids}
        if any(n.id not in decided for n in self.timeline.sequence):
            return None
        if any(c.outputs_inside is not False and not any(e.exits for e in c.enclosed_by)
               for c in self.timeline.calls):
            return None  # something it calls or includes may print outside the arms
        return sorted({n.loc.start_line for n in self.timeline.sequence if n.loc is not None})

    def error_output_only(self, lines: list) -> GenerationResult:
        detail = ("the file prints only in arms that exit (R-I8 / R-I8c" + (", lines " + ", ".join(
            map(str, lines)) if lines else "") + "): an error stop or a redirect, not a page. Its include and "
            "call sites carry exit_output_not_generated")
        entry = GenerationEntry(
            kind="flag", flag="error_output_only", reason="error_output_only", status=REVIEW, file="", line=0,
            node_id="page", detail=detail, function="", in_abstained=False, source_line=lines[0] if lines else None,
            severity="review", severity_if_rendered="review")
        return GenerationResult(entrypoint=self.timeline.entrypoint, route=self.route, files=(), components=(),
                                todos=(), flags=(entry,), not_generated={"reason": "error_output_only",
                                                                         "lines": lines})

    def not_generated(self) -> GenerationResult:
        """Section 5 / spec v0.8 section 6a: a unit that is not an HTML response is no page. Nothing is written
        for it; it is listed under notGenerated, with one entry that counts as a gap."""
        media_type = self.timeline.content_type
        ajax = "ajax_endpoint" in self.tree.root.flags  # spec v0.12: no markup, only json_encode(...)
        detail = (("an AJAX endpoint: no markup of its own, its only output is json_encode(...)" if ajax else
                   f"Content-Type {media_type}: not an HTML page") + ". An API route (Next.js Route Handler), "
                  "owned by Component 1")
        entry = GenerationEntry(
            kind="flag", flag="non_html_response", reason="non_html_response", status=REVIEW, file="", line=0,
            node_id="page", detail=detail, function="", in_abstained=False, source_line=None, severity="todo",
            severity_if_rendered="todo")
        return GenerationResult(entrypoint=self.timeline.entrypoint, route=self.route, files=(), components=(),
                                todos=(), flags=(entry,),
                                not_generated={"reason": "non_html_response", "contentType": media_type,
                                               **({"subtype": "ajax_endpoint"} if ajax else {}),
                                               "owner": "Component 1 (Route Handler)"})

    def run(self) -> GenerationResult:
        if self.tree.root.status == "abstain" and self.tree.root.reason == "non_html_response":
            return self.not_generated()
        if (lines := self.error_output_arms()) is not None:
            return self.error_output_only(lines)
        page = self.file(self.root_path)
        key = page.mark()
        try:
            markup = build_page(self.outputs, self.boundary_config, self.abstain, *self.stray_repair(),
                                body_slots=self.body_slots())
            repair(markup)  # spec v0.6 section 5b: what a browser builds from this markup
            ctx = _Ctx(file=page, markup=markup, boundary=self.boundaries(markup), function=self.root_name,
                       function_key=key)
            self.queue_markup_flags(markup)
            body = self.page_body(markup.children, ctx)
            self.place_markup_flags(None, ctx, [])
        except GenerationError as e:  # the last resort: the whole page is one C2Todo, never a guess
            self.files = {page.path: page}
            for state in (page.entries, page.functions, page.imports, page.type_imports, self.generated,
                          self.cond_marks, self.emitted_in, self.pending_flags):
                state.clear()
            self.layout_head, self.layout_attrs, self.layout_marks = [], {}, {}
            ctx = _Ctx(file=page, markup=Markup([], {}, {}, []), boundary={}, function=self.root_name,
                       function_key=key)
            body = [self.todo("page", e.reason, ctx, e.detail)]
        unit = self.layout.mode != "page"  # a function component or a shell: no fetch, its own props
        if self.page_gap is not None and not unit:
            body.insert(0, self.todo("page", self.page_gap, ctx))
        fetch = "data" in ctx.uses and not unit
        fetches = self.fetch_locals(ctx)  # spec v0.13: a function component fetches its Lists' own endpoints too
        if unit:
            header = f"export {'async ' if fetches else ''}function {self.root_name}({self.unit_signature(page)}) {{"
        else:
            header = f"export default {'async ' if fetch or fetches else ''}function Page() {{"
        page.functions.insert(0, _Function(self.root_name, header, (
            [f"const data = await {self.fetch_name}();"] if fetch else []) + fetches + ctx.locals, body, key))
        if self.layout.mode == "shell":
            self.write_layout()
        if fetch:
            page.use(self.lib(page, "api"), self.fetch_name)
        for pending in ctx.pending:
            self.abstained_function(pending, ctx)
        for flags in self.pending_flags.values():  # anchored in markup that was never written: the page's line
            for f in flags:
                self.flag(ctx, f.flag, f.status, f.node_id, f.detail, mark=key)
        self.pending_flags.clear()
        for call in self.timeline.calls:  # R-I8c: what an exit arm reached from here prints is not generated
            if call.exit_output:
                where = ", ".join(f"{re.sub(r'^legacy-apps/[^/]+/', '', file)}:{line}" for file, line in call.exit_output)
                what = "include" if call.kind == app_units.INCLUDE else "call"
                arms = ("the exit arm at {} prints and exits" if len(call.exit_output) == 1
                        else "the exit arms at {} print and exit")
                self.flag(ctx, "exit_output_not_generated", REVIEW, call.id,
                          f"{what}: {arms.format(where)}; that output is not generated (R-I8c)", mark=key)
        for redirect in self.timeline.redirects:  # spec v0.10: no output, no component; the redirect is C1's
            guard = next((e for e in reversed(redirect.enclosed_by) if e.role == "branch"), None)
            self.flag(ctx, "header_redirect", INFO, redirect.node_id,
                      f"header(\"Location: {redirect.target}\"): a redirect, nothing rendered; "
                      + (f"guarded by condition {guard.cond_node_id}" if guard else "not guarded (always runs)")
                      + "; the redirect belongs to C1", mark=key)
        self.stage2_reviews(page, key)
        for query in self.timeline.queries.values():  # reporting only: the query's shape came from its fixed text
            if query.values_interpolated:
                page.entries.append(_Entry("flag", "sql_interpolated_values", "sql_interpolated_values", INFO,
                                           query.node_id, " ".join(query.sql.split())[:120], key, "Page", False))
        return self.result()

    def body_slots(self) -> frozenset:
        """Spec v0.15: the call / include slots of this unit whose target certainly prints body content (its
        unconditional top-level output holds a non-head element or text), so a later <head> is ignored."""
        if self.units is None or self.boundary_config.rules_version == "1.0":
            return frozenset()
        found = set()
        for call in self.timeline.calls:
            target = self.units.timelines.get(call.callee or "")
            if target is None or call.outputs_inside is not True:
                continue
            text = "".join(tagtrack.output_text(n) for n in target.sequence
                           if not n.enclosed_by and n.kind != "call" and not app_units.is_slot(n))
            if starts_body(text):
                found.add(call.id)
        return frozenset(found)

    def stray_repair(self) -> tuple[bool, frozenset]:
        """Whether a stray </p> / </br> may be repaired in this unit, and the slots after which it may not.

        A function's stray end tag may close an element its caller opened, so only a file's top level is
        repaired; and not after the call of a shell header that leaves a <p> open."""
        stray = self.timeline.function is None and self.layout.mode != "component"
        opaque = set()
        if self.shell is not None and "p" in self.units.functions[self.shell.header].open_tags:
            opaque.add(self.shell.header_call)
        return stray, frozenset(opaque)

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
                    page, self.root_name, page_key, False)
            file.entries.append(_Entry("flag", "stage2_review", node.reason, REVIEW, ",".join(node.source_ids),
                                       f"{node.type} {self.ids[id(node)]}", mark, function, in_abstained))

    # ---- files

    def lib_files(self) -> dict[str, str]:
        """Every file is an ES module (spec v0.5 section 8): a page with no endpoint has nothing to fetch, so it
        gets no lib/api.ts at all rather than a file with no export (a global script)."""
        files = {"lib/c2-todo.tsx": C2TODO_SOURCE}
        types = self.api_types()
        files["lib/api-types.ts"] = types
        if self.schema is not None or self.query_data:
            files["lib/api.ts"] = self.api_module()
        return files

    def responses(self) -> list:
        """(response type, schema, path, fetch name) of every endpoint this unit fetches: the page's, then the
        Lists' own (spec v0.13), each once."""
        found = {}
        if self.schema is not None:
            found[self.response_type] = (self.response_type, self.schema, self.report.endpoint.path, self.fetch_name)
        for query in sorted(self.query_data.values(), key=lambda q: q.fetch_name):
            found.setdefault(query.response_type, (query.response_type, query.schema, query.ref.path, query.fetch_name))
        return list(found.values())

    def api_types(self) -> str:
        if self.schema is None and not self.query_data:
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

        bodies = {name: ts(schema, 0, top=True) for name, schema, _, _ in self.responses()}
        blocks = [f"export type {name} = {body};" for name, body in named.items() if name not in bodies]
        blocks += [f"export type {name} = {body};" for name, body in bodies.items() if name not in named]
        blocks[:0] = [f"export type {name} = {named[name]};" for name in bodies if name in named]
        header = ("// Response types for this page, from Component 1's contract (docs/generation-spec.md section 8).\n"
                  "// Fields not in the contract do not appear: a missing field is a C2Todo, never row.<field>.\n")
        return header + "\n" + "\n\n".join(blocks) + "\n"

    def api_module(self) -> str:
        """lib/api.ts for a page with a mapped endpoint: the typed fetch function, and authHeaders (exported)."""
        responses = self.responses()
        lines = [f'import type {{ {", ".join(sorted(r[0] for r in responses))} }} from "./api-types";', "",
                 "// TODO(C2): token forwarding is Component 1's design (docs/generation-spec.md section 8).",
                 "export function authHeaders(): Record<string, string> {", "  return {};", "}"]
        for response_type, _, path, fetch_name in responses:
            lines += ["", f"export async function {fetch_name}(): Promise<{response_type}> {{",
                      f"  const response = await fetch(`${{process.env.{self.config.api_base_url_env}}}{path}`, {{",
                      '    cache: "no-store",', "    headers: authHeaders(),", "  });", "  return response.json();",
                      "}"]
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
        for query in self.query_data.values():  # spec v0.13: the Lists' own endpoints
            names.add(query.response_type)
            collect(query.schema)
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
                elif isinstance(item, jsx.Element) and item.tag[:1].isupper() and item.tag not in functions:
                    # a function component or a shell of another unit: checked with its own unit; here only
                    # what this unit puts inside it
                    out += build(item.children, file, function)
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

        roots = [name for name in functions if name == self.root_name or name.startswith("Abstained_")]
        for root in sorted(roots, key=lambda name: (name != self.root_name, name)):
            top, top_file = functions[root]
            in_abstained = root != self.root_name
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
                node = self.output_by_id.get(e.node_id) or self.part_owner.get(e.node_id) \
                    or self.calls.get(e.node_id.split(",")[0]) \
                    or self.redirects.get(e.node_id)  # a call site (the first of a merged run); a redirect
                part = self.parts_by_id.get(e.node_id)
                if part is not None and part.loc is not None:  # an expression part: its own position
                    node = replace(node, loc=part.loc)
                severity, if_rendered = severity_of(e.kind, e.flag, e.status, e.in_abstained)
                entry = GenerationEntry(
                    kind=e.kind, flag=e.flag, reason=e.reason, status=e.status, file=path, line=line,
                    node_id=e.node_id, detail=e.detail, function=e.function, in_abstained=e.in_abstained,
                    source_line=node.loc.start_line if node is not None and node.loc is not None else (
                        self.timeline.queries[e.node_id].line if e.node_id in self.timeline.queries else None),
                    source_col=node.loc.column if node is not None and node.loc is not None else None,
                    severity=severity, severity_if_rendered=if_rendered)
                (flags if e.kind == "flag" else todos).append(entry)
        for path, text in sorted(self.lib_files().items()):
            files.append(GeneratedFile(path, text))
        files.sort(key=lambda f: f.path)
        order = lambda e: (e.file, e.line, e.kind, e.node_id, e.flag or "", e.reason, e.detail)  # noqa: E731
        components = []
        for node in [self.tree.root] + [n for n in self.order if n.type in COMPONENT_FILES]:
            if node is self.tree.root:
                name, path = self.root_name, self.root_path
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


def _attribute_names(a) -> list:
    """The attribute names an attribute entry of a start tag writes: one, or every name an if prints (R-I3b)."""
    if isinstance(a, MAttrPresence):
        return list(dict.fromkeys(m.name for _, found in a.branches for m in found))
    return [a.name]


def _presence_attr(a: MAttrPresence, name: str, arm: int | None = None):
    """The attribute `name` as an arm of an if prints it (the first arm that does when `arm` is None)."""
    arms = a.branches if arm is None else [a.branches[arm]]
    return next((m for _, found in arms for m in found if m.name == name), None)


def _value_code(attr: jsx.Attr) -> str:
    return attr.code if attr.code is not None else jsx.js_string(attr.value) if attr.value is not None else "true"


def _presence_code(conditions: tuple, codes: list, fallback: str) -> str:
    """`cond ? value : (next arm, or what the browser takes when no arm prints the attribute)`."""
    arms, silent_then = conditions
    code = fallback
    for condition, value in reversed(list(zip(arms, codes))):
        if condition is None:  # the else arm
            code = value if value is not None else fallback
        elif value is not None or code != fallback:
            code = f"{condition} ? {value if value is not None else fallback} : {code}"
    if silent_then is not None:
        code = f"{silent_then} ? {fallback} : {code}"
    elif len(arms) == 1 and codes == ["true"] and fallback == "undefined":
        code = arms[0]  # a boolean attribute under one condition
    return code


def _is_static(node) -> bool:
    """Markup with nothing dynamic anywhere in it: text and elements only, literal attributes and content."""
    if isinstance(node, MText):
        return True
    if not isinstance(node, MElement):
        return False
    if any(not isinstance(part, str) for a in node.attrs for part in a.parts or []):
        return False
    if any(not isinstance(part, str) for part in node.content or []):
        return False
    return all(_is_static(child) for child in node.children)


def _dynamic_id(item: MDynamic) -> str:
    """The id a C2Todo or flag for this output carries: the part's AST node for one expression of an echo
    string (spec v0.6), the output statement's id otherwise."""
    return item.part.node_id if item.part is not None else item.output.id


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
             timeline: Timeline, boundary_config: BoundaryConfig, config: GenerationConfig, units=None,
             layout: UnitLayout | None = None) -> GenerationResult:
    """`units` and `layout` place the timeline in a whole app (function components, shells, route groups);
    without them it is generated as one page on its own."""
    return Generator(tree, requirements, report, contract, timeline, boundary_config, config, units, layout).run()


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
    out |= {"reason": e.reason, "severity": e.severity, "severityIfRendered": e.severity_if_rendered,
            "status": e.status, "file": e.file, "line": e.line,
            "nodeId": e.node_id, "function": e.function, "inAbstained": e.in_abstained, "sourceLine": e.source_line, "sourceCol": e.source_col,
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
        **({"notGenerated": result.not_generated} if result.not_generated else {}),
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
