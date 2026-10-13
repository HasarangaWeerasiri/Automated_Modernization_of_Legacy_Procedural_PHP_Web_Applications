"""App-level facts for the function and layout rules (boundary-rules.md v0.11 section 4).

R-F1, R-F1b and R-F2 need more than one timeline: a function's own timeline, and the call
entries in the timelines that call it. This module reads all the timelines of one app and
answers, for every function that has a timeline:

  * is its markup tag-balanced on its own (R-F1), and if not, which elements it leaves open
    and which closing tags close nothing of its own (R-F2);
  * which parameters are typed props: read-only in the function and given a literal (or their
    literal default) at every call site;
  * is it recursive (R-F1: abstain);
  * which header / footer functions form a shell pair (R-F2, conditions 1 and 2). Condition 3
    is about one page and is checked by Stage 2 on that page.

It decides nothing about a page's structure; Stage 2 does, with these facts.
"""

import re
from dataclasses import dataclass, field, replace

from src.mapping import tags as tagtrack
from src.model import BoundaryConfig, CallEntry, OutputNode, Timeline

CALL, CHILDREN = "call", "children"
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
# Names a prop may not take: JavaScript's reserved words and the props the generator uses itself.
_RESERVED = frozenset((
    "break case catch class const continue debugger default delete do else enum export extends false finally for "
    "function if implements import in instanceof interface let new null package private protected public return "
    "static super switch this throw true try typeof var void while with yield await async of "
    "children items row data key ref").split())


@dataclass(frozen=True)
class FunctionUnit:
    node_id: str
    name: str  # the PHP name, e.g. "our_header" or "View.title"
    entrypoint: str  # its timeline
    balanced: bool  # every tag it opens it closes, and it closes nothing else
    open_tags: tuple[str, ...]  # elements still open at its end, outermost first
    unmatched: tuple[str, ...]  # closing tags that close nothing of its own, in order
    recursive: bool
    call_sites: int  # resolved calls of it, in every timeline of the app
    props: tuple[tuple[str, str], ...]  # typed props: (name, "string" | "number")
    component: str | None = None  # R-F1: its component's name; None when it is not a component


@dataclass(frozen=True)
class ShellUnit:
    """A header / footer pair (R-F2): H leaves S open, F closes exactly S."""

    header: str  # node id of H
    footer: str  # node id of F
    name: str  # the shell component, "<H>Shell"
    group: str  # the route group, "(<h>)"
    props: tuple[tuple[str, str, str], ...]  # (prop name, type, the function that takes it)


@dataclass
class AppUnits:
    functions: dict[str, FunctionUnit] = field(default_factory=dict)  # by function node id
    shells: dict[str, ShellUnit] = field(default_factory=dict)  # by header node id
    timelines: dict[str, Timeline] = field(default_factory=dict)  # function node id -> its timeline

    @property
    def shell_functions(self) -> frozenset:
        """Every function that is the header or the footer of a pair."""
        return frozenset(s.header for s in self.shells.values()) | frozenset(s.footer for s in self.shells.values())

    def shell_of_footer(self, footer: str) -> ShellUnit | None:
        return next((s for s in self.shells.values() if s.footer == footer), None)


def pascal(text: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", text) if part)


def slots(timeline: Timeline) -> list[CallEntry]:
    """The call entries that take a place in the output: a call that may print, or a shell's children."""
    return [c for c in timeline.calls if c.kind == CHILDREN or c.outputs_inside is not False]


def outputs_with_slots(timeline: Timeline) -> list[OutputNode]:
    """The timeline's output statements, with one empty pseudo node (kind "call" / "children") per slot,
    in sequence order. A slot prints nothing here: what stands in its place is decided by Stage 2."""
    pseudo = [OutputNode(c.id, c.kind, "", (), c.enclosed_by, c.loc) for c in slots(timeline)]
    if not pseudo:
        return list(timeline.sequence)
    order = {node_id: index for index, node_id in enumerate(timeline.order)}
    return sorted([*timeline.sequence, *pseudo], key=lambda n: order[n.id])


def is_slot(node: OutputNode) -> bool:
    return node.kind in (CALL, CHILDREN)


def _tag_facts(timeline: Timeline, config: BoundaryConfig) -> tuple[bool, tuple, tuple]:
    if not timeline.sequence:
        return True, (), ()
    tracked = tagtrack.track(timeline.sequence, config)
    run = [tracked[node.id] for node in timeline.sequence]
    open_tags = tuple(e.tag for e in run[-1].stack_after)
    unmatched = tuple(e.tag for t in run for e in t.events if e.kind == "unmatched_close")
    balanced = not open_tags and not unmatched and not run[-1].in_tag_after
    return balanced, open_tags, unmatched


def _typed_props(timeline: Timeline, calls: list[CallEntry]) -> tuple[tuple[str, str], ...]:
    """R-F1: a parameter is a typed prop when it is read-only in the function and every call site gives it a
    literal of one type (or leaves it to a literal default)."""
    props = []
    for index, param in enumerate(timeline.function.params):
        name = param.name[1:] if param.name and param.name.startswith("$") else ""
        if not calls or not param.read_only or not _IDENTIFIER.fullmatch(name) or name in _RESERVED:
            continue
        types = set()
        for call in calls:
            if call.args is None:
                types.add(None)
                continue
            arg = call.args[index] if index < len(call.args) else param.default
            types.add(arg.type if arg is not None and arg.kind == "literal" else None)
        if len(types) == 1 and None not in types:
            props.append((name, types.pop()))
    return tuple(props)


def prop_values(unit_props, timeline: Timeline, call: CallEntry) -> list[tuple[str, str, object]]:
    """(prop name, type, value) for one call of a function, from its literal arguments or defaults."""
    out = []
    wanted = dict(unit_props)
    for index, param in enumerate(timeline.function.params):
        name = param.name[1:] if param.name else ""
        if name in wanted:
            arg = call.args[index] if call.args is not None and index < len(call.args) else param.default
            out.append((name, wanted[name], arg.value))
    return out


def _recursive(graph: dict[str, set]) -> set:
    """Functions that can reach themselves through calls."""
    out = set()
    for start in graph:
        seen, todo = set(), list(graph[start])
        while todo:
            current = todo.pop()
            if current == start:
                out.add(start)
                break
            if current not in seen:
                seen.add(current)
                todo += list(graph.get(current, ()))
    return out


def analyse(timelines: list[Timeline], config: BoundaryConfig) -> AppUnits:
    """The function and shell facts of one app, from all of its timelines."""
    units = AppUnits()
    functions = {t.function.node_id: t for t in timelines if t.function is not None}
    units.timelines = functions
    sites: dict[str, list] = {node_id: [] for node_id in functions}
    for timeline in timelines:
        for call in timeline.calls:
            if call.callee in sites:
                sites[call.callee].append(call)
    graph = {node_id: {c.callee for c in t.calls if c.callee in functions} for node_id, t in functions.items()}
    recursive = _recursive(graph)

    facts = {}
    for node_id in sorted(functions):
        timeline = functions[node_id]
        balanced, open_tags, unmatched = _tag_facts(timeline, config)
        facts[node_id] = FunctionUnit(
            node_id=node_id, name=timeline.function.name, entrypoint=timeline.entrypoint, balanced=balanced,
            open_tags=open_tags, unmatched=unmatched, recursive=node_id in recursive,
            call_sites=len(sites[node_id]), props=_typed_props(timeline, sites[node_id]))

    # R-F2, conditions 1 and 2: H leaves S open and closes nothing else; F closes exactly S, in reverse.
    headers = [u for u in facts.values() if u.open_tags and not u.unmatched and u.call_sites]
    footers = [u for u in facts.values() if u.unmatched and not u.open_tags and u.call_sites]
    used = set()
    for header in headers:
        matching = [f for f in footers if f.unmatched == tuple(reversed(header.open_tags)) and f.node_id not in used]
        if len(matching) != 1:
            continue  # no footer, or more than one: nothing certain
        footer = matching[0]
        used.add(footer.node_id)
        props = [(n, t, header.node_id) for n, t in header.props]
        props += [(n, t, footer.node_id) for n, t in footer.props if n not in dict(header.props)]
        units.shells[header.node_id] = ShellUnit(header.node_id, footer.node_id, pascal(header.name) + "Shell",
                                                 "(" + re.sub(r"[^A-Za-z0-9_-]+", "-", header.name) + ")",
                                                 tuple(props))

    # R-F1: a function with output, called at least once, tag-balanced on its own, not recursive.
    taken = {shell.name for shell in units.shells.values()}
    in_shell = units.shell_functions
    for node_id in sorted(facts):
        unit = facts[node_id]
        component = None
        if unit.balanced and unit.call_sites and not unit.recursive and node_id not in in_shell:
            component, n = pascal(unit.name), 2
            while component in taken:
                component, n = f"{pascal(unit.name)}{n}", n + 1
            taken.add(component)
        units.functions[node_id] = replace(unit, component=component)
    return units


def shell_timeline(shell: ShellUnit, units: AppUnits) -> Timeline:
    """One timeline for a shell pair: the header's output, the place of the page, the footer's output."""
    header, footer = units.timelines[shell.header], units.timelines[shell.footer]
    children = CallEntry(id=f"{shell.header}.children", callee=None, outputs_inside=True, enclosed_by=(),
                         kind=CHILDREN)
    return Timeline(
        entrypoint=f"{header.entrypoint}+{footer.function.name}",
        schema_version=header.schema_version,
        sequence=header.sequence + footer.sequence,
        queries={**header.queries, **footer.queries},
        provenance={"shell": {"header": header.entrypoint, "footer": footer.entrypoint}},
        calls=header.calls + (children,) + footer.calls,
        function=None,
        order=header.order + (children.id,) + footer.order)
