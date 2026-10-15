"""App-level facts for the function and layout rules (boundary-rules.md v0.12 section 4).

R-F1, R-F1b and R-F2 need more than one timeline: a function's own timeline, and the call
entries in the timelines that call it. An included file is a unit too (R-F2: "functions, or
included files"): its timeline is the file's top level, and its call sites are the include
entries that name it. This module reads all the timelines of one app and answers, for every
function that has a timeline and every included file that has one:

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

CALL, CHILDREN, INCLUDE = "call", "children", "include"
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
# Names a prop may not take: JavaScript's reserved words and the props the generator uses itself.
_RESERVED = frozenset((
    "break case catch class const continue debugger default delete do else enum export extends false finally for "
    "function if implements import in instanceof interface let new null package private protected public return "
    "static super switch this throw true try typeof var void while with yield await async of "
    "children items row data key ref").split())


@dataclass(frozen=True)
class FunctionUnit:
    node_id: str  # a function's node id; an included file's entrypoint
    name: str  # the PHP name, e.g. "our_header" or "View.title"; for a file its path without ".php"
    entrypoint: str  # its timeline
    balanced: bool  # every tag it opens it closes, and it closes nothing else
    open_tags: tuple[str, ...]  # elements still open at its end, outermost first
    unmatched: tuple[str, ...]  # closing tags that close nothing of its own, in order
    recursive: bool
    call_sites: int  # resolved calls of it, in every timeline of the app
    props: tuple[tuple[str, str], ...]  # typed props: (name, "string" | "number")
    component: str | None = None  # R-F1: its component's name; None when it is not a component
    kind: str = "function"  # "function" | "file" (an included file: never an R-F1 component)


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
    functions: dict[str, FunctionUnit] = field(default_factory=dict)  # by function node id / file entrypoint
    shells: dict[str, ShellUnit] = field(default_factory=dict)  # by header node id / entrypoint
    timelines: dict[str, Timeline] = field(default_factory=dict)  # the same keys -> the unit's timeline

    @property
    def shell_functions(self) -> frozenset:
        """Every function or included file that is the header or the footer of a pair."""
        return frozenset(s.header for s in self.shells.values()) | frozenset(s.footer for s in self.shells.values())

    def shell_of_footer(self, footer: str) -> ShellUnit | None:
        return next((s for s in self.shells.values() if s.footer == footer), None)


def pascal(text: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", text) if part)


def slots(timeline: Timeline) -> list[CallEntry]:
    """The call entries that take a place in the output: a call that may print, or a shell's children."""
    return [c for c in timeline.calls if c.kind == CHILDREN or c.outputs_inside is not False]


def include_text(timeline: Timeline) -> list[OutputNode]:
    """boundary-rules v1.0 section 4: the whitespace of an included file that prints only whitespace, as inline
    HTML at the include site (the include's id), so the spec section 5 whitespace rules decide it."""
    return [OutputNode(c.id, "Stmt_InlineHTML", c.whitespace, (), c.enclosed_by, c.loc)
            for c in timeline.calls if c.kind == INCLUDE and c.whitespace]


def outputs_with_slots(timeline: Timeline) -> list[OutputNode]:
    """The timeline's output statements, with one empty pseudo node (kind "call" / "children") per slot and
    the text of whitespace-only includes, in sequence order. A slot prints nothing here: what stands in its
    place is decided by Stage 2."""
    pseudo = [OutputNode(c.id, c.kind, "", (), c.enclosed_by, c.loc) for c in slots(timeline)] + include_text(timeline)
    if not pseudo:
        return list(timeline.sequence)
    order = {node_id: index for index, node_id in enumerate(timeline.order)}
    return sorted([*timeline.sequence, *pseudo], key=lambda n: order[n.id])


def is_slot(node: OutputNode) -> bool:
    return node.kind in (CALL, CHILDREN, INCLUDE)


def _tag_facts(timeline: Timeline, config: BoundaryConfig) -> tuple[bool, tuple, tuple]:
    if not timeline.sequence:
        return True, (), ()
    tracked = tagtrack.track(timeline.sequence, config)
    run = [tracked[node.id] for node in timeline.sequence]
    open_tags = tuple(e.tag for e in run[-1].stack_after)
    # The end tag of a void element (`</meta>`, `</br>`) can close nothing, here or in a caller: R-L6 ignores it.
    unmatched = tuple(e.tag for t in run for e in t.events
                      if e.kind == "unmatched_close" and e.tag not in config.void_tags)
    balanced = not open_tags and not unmatched and not run[-1].in_tag_after
    return balanced, open_tags, unmatched


# HTML5 "in body": the end tags that close the nearest open element of their name, and every element open
# inside it, when that element is in scope (boundary-rules v0.14, R-F2 condition 2 (b)).
BLOCK_END_TAGS = frozenset((
    "address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "header", "hgroup", "listing", "main", "menu", "nav", "ol", "pre", "section",
    "summary", "ul"))
# Elements that end the "default scope" of that search (HTML5 "has an element in scope").
SCOPE_LIMITS = frozenset(("applet", "caption", "html", "table", "td", "th", "marquee", "object", "template"))


def closes(open_tags: tuple, unmatched: tuple) -> bool:
    """R-F2 condition 2 as made exact in boundary-rules v0.14: the footer's closing tags close the header's open
    elements once the implicit closes every browser applies are counted.

    (a) `<head>` is closed by the `<body>` tag. (b) An end tag of the HTML5 block group closes the nearest open
    element of its name, if it is in scope, and every element open inside it (`</div>` ends an unclosed `<main>`).
    (c) `</body>` and `</html>` close everything still open inside them; `<body>` and `<html>` themselves may be
    left to the end of the file. Any other end tag (`</span>`, `</a>`, `</form>`, `</p>` ...) must close the
    element open last: it does not close what is open inside it. Anything else left open is no pair."""
    stack = list(open_tags)
    if "head" in stack and "body" in stack[stack.index("head") + 1:]:
        stack.remove("head")
    for tag in unmatched:
        if tag in ("body", "html"):
            if tag not in stack:
                return False
            del stack[stack.index(tag):]
        elif tag in BLOCK_END_TAGS:
            index = next((i for i in range(len(stack) - 1, -1, -1) if stack[i] == tag or stack[i] in SCOPE_LIMITS),
                         None)
            if index is None or stack[index] != tag:
                return False  # not open, or not in scope
            del stack[index:]
        elif stack and stack[-1] == tag:
            stack.pop()
        else:
            return False
    return all(tag in ("html", "body") for tag in stack)


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
    if timeline.function is None:  # an included file takes no arguments
        return out
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


def file_name(entrypoint: str) -> str:
    return entrypoint.removesuffix(".php")


def analyse(timelines: list[Timeline], config: BoundaryConfig, file_names: dict | None = None) -> AppUnits:
    """The function and shell facts of one app, from all of its timelines.

    `file_names` gives an included file's name (its path from the app's document root, without ".php") by
    entrypoint; a file not in it is named by its entrypoint."""
    units = AppUnits()
    pages = {t.entrypoint: t for t in timelines if t.function is None}
    included = {c.callee for t in timelines for c in t.calls if c.kind == INCLUDE and c.callee in pages}
    files = {entrypoint: pages[entrypoint] for entrypoint in sorted(included)}
    functions = {t.function.node_id: t for t in timelines if t.function is not None} | files
    units.timelines = functions
    sites: dict[str, list] = {node_id: [] for node_id in functions}
    callers: dict[str, set] = {node_id: set() for node_id in functions}  # the timelines each unit is called in
    for timeline in timelines:
        for call in timeline.calls:
            if call.callee in sites:
                sites[call.callee].append(call)
                callers[call.callee].add(timeline.entrypoint)
    graph = {node_id: {c.callee for c in t.calls if c.callee in functions} for node_id, t in functions.items()}
    recursive = _recursive(graph)

    facts = {}
    for node_id in sorted(functions):
        timeline = functions[node_id]
        balanced, open_tags, unmatched = _tag_facts(timeline, config)
        if timeline.function is None:  # an included file
            name, props, kind = (file_names or {}).get(node_id) or file_name(node_id), (), "file"
        else:
            name, props, kind = timeline.function.name, _typed_props(timeline, sites[node_id]), "function"
        facts[node_id] = FunctionUnit(
            node_id=node_id, name=name, entrypoint=timeline.entrypoint, balanced=balanced,
            open_tags=open_tags, unmatched=unmatched, recursive=node_id in recursive,
            call_sites=len(sites[node_id]), props=props, kind=kind)

    # R-F2, conditions 1 and 2: H leaves S open and closes nothing else; F closes exactly S, in reverse.
    headers = [u for u in facts.values() if u.open_tags and not u.unmatched and u.call_sites]
    footers = [u for u in facts.values() if u.unmatched and not u.open_tags and u.call_sites]
    used, shell_names = set(), set()
    for header in headers:
        # Condition 3 is about pages that call both: a footer that no caller of this header ever calls is not its
        # footer, however well the tags fit (phpBlog: core.php's footer() and admin/footer.php close the same tags).
        matching = [f for f in footers if closes(header.open_tags, f.unmatched) and f.node_id not in used
                    and callers[f.node_id] & callers[header.node_id]]
        if len(matching) != 1:
            continue  # no footer, or more than one: nothing certain
        footer = matching[0]
        used.add(footer.node_id)
        props = [(n, t, header.node_id) for n, t in header.props]
        props += [(n, t, footer.node_id) for n, t in footer.props if n not in dict(header.props)]
        stem, slug = pascal(header.name) + "Shell", re.sub(r"[^A-Za-z0-9_-]+", "-", header.name)
        name, group, n = stem, f"({slug})", 2
        while name in shell_names:  # two headers of one name (head() in two files): a numeric suffix (spec 3)
            name, group, n = f"{stem}{n}", f"({slug}-{n})", n + 1
        shell_names.add(name)
        units.shells[header.node_id] = ShellUnit(header.node_id, footer.node_id, name, group, tuple(props))

    # R-F1: a function with output, called at least once, tag-balanced on its own, not recursive. (The rule
    # is written for functions: an included file is a unit only for R-F2.)
    taken = {shell.name for shell in units.shells.values()}
    in_shell = units.shell_functions
    for node_id in sorted(facts):
        unit = facts[node_id]
        component = None
        if unit.kind == "function" and unit.balanced and unit.call_sites and not unit.recursive \
                and node_id not in in_shell:
            component, n = pascal(unit.name), 2
            while component in taken:
                component, n = f"{pascal(unit.name)}{n}", n + 1
            taken.add(component)
        units.functions[node_id] = replace(unit, component=component)
    return units


def unit_label(timeline: Timeline) -> str:
    """A function's name, or an included file's base name: the short label of a unit."""
    return timeline.function.name if timeline.function is not None else timeline.entrypoint.rsplit("/", 1)[-1]


def shell_timeline(shell: ShellUnit, units: AppUnits) -> Timeline:
    """One timeline for a shell pair: the header's output, the place of the page, the footer's output."""
    header, footer = units.timelines[shell.header], units.timelines[shell.footer]
    children = CallEntry(id=f"{shell.header}.children", callee=None, outputs_inside=True, enclosed_by=(),
                         kind=CHILDREN)
    return Timeline(
        entrypoint=f"{header.entrypoint}+{unit_label(footer)}",
        schema_version=header.schema_version,
        sequence=header.sequence + footer.sequence,
        queries={**header.queries, **footer.queries},
        provenance={"shell": {"header": header.entrypoint, "footer": footer.entrypoint}},
        calls=header.calls + (children,) + footer.calls,
        function=None,
        order=header.order + (children.id,) + footer.order)
