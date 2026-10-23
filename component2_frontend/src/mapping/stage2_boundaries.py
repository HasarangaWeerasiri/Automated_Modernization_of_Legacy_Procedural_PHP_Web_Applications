"""Stage 2: boundary inference.

Turns the Stage 1 result into a component tree by applying the owner's
boundary rules (docs/php-analysis/boundary-rules.md, draft v0.3) as written.
Thresholds come from config/boundary_rules.json. Where no rule covers a
structure the node is an Abstain: nothing is guessed.

How the rules are applied:

  loops   R-L5 no output -> not a boundary. R-L7 body closes and reopens its
          container -> abstain. Otherwise R-L1: a List holding one Item.
          R-L2b when the item has several root elements (no wrapper), else
          R-L2a (container = nearest open tag, walking past table sections).
          R-L3: a one-element item stays inline, no Item component; an if
          inside it is still judged by the if rules (boundary-rules v0.7).
          R-L3b: a body of text and expressions with no element is an
          inline map too (a keyed Fragment), no Item component.
          R-L4 needs no code of its own: the walk is innermost first.
  ifs     R-I3 output in attribute position. R-L6 tags crossing a branch ->
          abstain. An if labelled business_logic is an authorization guard ->
          abstain, with or without a loop inside. R-I2 a branch holding a
          loop -> that List's guard; the other branch is its Empty. Then by
          size: R-I4 inline (up to small_if_max_elements), R-I5 its own
          component (fallback_component_min_elements or more). R-I6 merges
          adjacent small ifs in one element. R-I1 is decided at page level
          (rules v1.1: only when the path that skips the guard leaves the page).
  R-I7    An if or loop whose only output is inline JS (`<script>`, which
          Stage 5 drops) has nothing to render: not a boundary, like R-L5.

Labels and statuses (boundary-rules.md sections 4a and 5):
  - An abstaining if still shows what is inside it: its children are built.
  - A content if labelled data_access or mixed would move that logic into the
    UI, so it abstains.
  - A loop labelled mixed is still a List with status ok; the loop's concern
    is recorded on the List as loop_concern (information, not a status).
  - A Stage 1 review on an output node carries to the leaf that holds it. A
    review on an undecided or unlabelled if or loop carries to the node built
    from it (CONTEXT.md rules 3 and 4).
  - An R-I6 chain is always review: the conditions' text is not in the
    timeline, so their mutual exclusivity cannot be checked.

An echo that carries `parts` (boundary-rules section 1) is read piece by
piece: its literal text goes through the tag tracker like inline HTML. An
expression part where a tag name or an attribute name is written cannot be
generated: that echo abstains (`markup_from_expression`).

Functions and layout (boundary-rules section 4), only when the app's units
(src/mapping/app_units.py) are given - a single timeline cannot show them:

  calls   A call entry that may print takes its place among the output nodes
          as an empty slot. R-F1: a call of a function that is a component ->
          FunctionCall. No single callee, or output that cannot be followed ->
          abstain unresolved_call. A recursive function -> abstain
          recursive_function. A function whose tags are not balanced, outside
          a shell pair -> abstain tag_crosses_function.
  R-F1b   In a function's own timeline, a loop whose container is not open in
          the function (the caller opens it) -> a List without wrapper.
  R-F2    A page that calls a shell pair's header and, later, its footer, both
          at the page's top level (the level inside an R-I1 page guard counts)
          -> the Page is wrapped in that shell. Output before the header or
          after the footer -> abstain output_outside_shell.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from src.mapping import app_units
from src.mapping import tags as tagtrack
from src.model import (
    BoundaryConfig,
    ComponentNode,
    ComponentTree,
    Concern,
    Enclosure,
    KeptNode,
    Label,
    ListContainer,
    OutputNode,
    PresentationResult,
    ShellUse,
    Status,
    Timeline,
    TreeCounts,
)

COMPONENT_TYPES = ("Page", "Layout", "List", "Item", "Empty", "ConditionalComponent")
HTML_MEDIA_TYPES = ("text/html", "application/xhtml+xml")
HTML_SPACE = " \t\n\f\r"
BLOCKING_CONCERNS = (Concern.BUSINESS_LOGIC, Concern.DATA_ACCESS, Concern.MIXED)


# ----------------------------------------------------------------------------- nesting, rebuilt from the chains


@dataclass
class _Loop:
    enclosure: Enclosure
    items: list


@dataclass
class _Branch:
    enclosure: Enclosure
    items: list


@dataclass
class _If:
    node_id: str
    label: Label | None
    branches: list  # of _Branch, in source order


@dataclass
class _Other:  # switch_case / try_catch
    enclosure: Enclosure
    items: list


def _regions(nodes: list[KeptNode], depth: int = 0) -> list:
    """Group output nodes by what encloses them at `depth`: a list of output nodes and regions, in order."""
    items, i = [], 0
    while i < len(nodes):
        chain = nodes[i].enclosed_by
        if len(chain) <= depth:
            items.append(nodes[i])
            i += 1
            continue
        enclosure = chain[depth]
        j = i
        while (j < len(nodes) and len(nodes[j].enclosed_by) > depth
               and nodes[j].enclosed_by[depth].node_id == enclosure.node_id):
            j += 1
        group = nodes[i:j]
        if enclosure.role == "iteration":
            items.append(_Loop(enclosure, _regions(group, depth + 1)))
        elif enclosure.role == "branch":
            branches, k = [], 0
            while k < len(group):
                arm = group[k].enclosed_by[depth]
                m = k
                while m < len(group) and (group[m].enclosed_by[depth].branch, group[m].enclosed_by[depth].cond_node_id) == (
                        arm.branch, arm.cond_node_id):
                    m += 1
                branches.append(_Branch(arm, _regions(group[k:m], depth + 1)))
                k = m
            items.append(_If(enclosure.node_id, enclosure.label, branches))
        else:
            items.append(_Other(enclosure, _regions(group, depth + 1)))
        i = j
    return items


def _flat(items: list) -> list[KeptNode]:
    """Every output node in a list of regions (loops and if-branches opened up), in order."""
    nodes = []
    for item in items:
        if isinstance(item, KeptNode):
            nodes.append(item)
        elif isinstance(item, _If):
            for branch in item.branches:
                nodes.extend(_flat(branch.items))
        else:
            nodes.extend(_flat(item.items))
    return nodes


def _is_call(item) -> bool:
    """A call site's slot among the output nodes."""
    return isinstance(item, KeptNode) and item.node_type == "call"


def _blank(node: KeptNode) -> bool:
    """Whitespace-only inline HTML."""
    return not _is_call(node) and node.output.raw is not None and node.output.raw.strip() == ""


# ----------------------------------------------------------------------------- the rules

# Where each of the 23 boundary rules (docs/php-analysis/boundary-rules.md) is applied in _Builder below.
#
#   Loop rules (section 2), 9:
#     R-L1  Loop -> List + Item ......................... list_parts(), loop()
#     R-L2a Container = nearest open tag ................. list_parts()
#     R-L2b Item with several roots, no wrapper .......... list_parts()
#     R-L3  One-element body stays an inline map ......... list_parts()
#     R-L3b Text-only body, inline map ................... list_parts()
#     R-L4  Nested loops, innermost first ................ no code of its own: items() / loop() recurse inward
#     R-L5  Loop with no output, not a boundary .......... list_parts()
#     R-L6  Tags crossing a branch or loop -> abstain .... list_parts(), if_()
#     R-L7  Chunked loop -> abstain ...................... list_parts()
#
#   If rules (section 3), 11:
#     R-I1  Page guard ................................... page(), other_path_leaves()
#     R-I2  List guard (empty state) ..................... guarded_list()
#     R-I2b Multi-loop list guard ........................ if_()
#     R-I3  Attribute conditional ........................ if_(), leaves()
#     R-I3b Attribute-presence conditional ............... if_(), attribute_presence()
#     R-I4  Small content if -> inline ................... if_()
#     R-I5  Fallback -> own Conditional component ........ if_()
#     R-I6  Sibling chain ................................ items(), chainable()
#     R-I7  No output left (only inline JS) .............. if_(), list_parts()
#     R-I8  Exit-terminated arm .......................... exit_if(), shell()
#     R-I8c Exit arm in an included file or function ..... exit_if()
#
#   Function and layout rules (section 4), 3:
#     R-F1  Function component ........................... call()
#     R-F1b Caller-opened container ...................... list_parts()
#     R-F2  Shell pair (header + footer -> layout) ....... page(), shell(), call()


class _Builder:
    def __init__(self, outputs: list[KeptNode], review: dict[str, str], config: BoundaryConfig,
                 timeline: Timeline | None = None, units=None):
        """Keep the output nodes, their Stage 1 review reasons, the tag-tracker result and the timeline facts (calls,
        function, content type, top-level ifs) the rules read."""
        self.outputs = outputs
        self.review = review  # Stage 1 review reason by node id
        self.config = config
        self.tags = tagtrack.track([k.output for k in outputs], config)
        self.units = units  # the app's function and shell facts; None for a timeline on its own
        self.calls = {c.id: c for c in timeline.calls} if timeline is not None else {}
        self.in_function = timeline is not None and timeline.function is not None
        self.content_type = timeline.content_type if timeline is not None else None
        self.top_level_ifs = {t.node_id: t for t in timeline.top_level_ifs} if timeline is not None else {}
        self.is_function = timeline is not None and timeline.function is not None
        # A file that other files include is not certainly the whole response: an `exit` in it ends the
        # including page too, so R-I8's "rest of the enclosing block" is not all that stops.
        self.included = bool(timeline is not None and units is not None and timeline.entrypoint in units.functions)
        # A shell's header and footer are called functions or included files (R-I8c)
        self.included = self.included or bool(timeline is not None and "shell" in timeline.provenance)

    def run(self, nodes: list[KeptNode]) -> list[tagtrack.NodeTags]:
        """The tag-tracker result of each node."""
        return [self.tags[k.node_id] for k in nodes]

    def node(self, type_, rules, reason, nodes, sources=(), children=(), abstain=False, flags=(), review=(),
             **extra):
        """Make a component-tree node; its status is abstain, review (with every review reason it carries) or ok."""
        reviews = [*review, *(self.review[s] for s in sources if s in self.review)]
        if not children:  # a leaf also answers for its own output nodes
            reviews += [k.reason for k in nodes if k.status is Status.REVIEW]
            if nodes and tagtrack.has_unmatched_close(self.run(nodes)):
                flags = (*flags, "unmatched_close")
        status = "ok"
        if abstain:
            status = "abstain"
        elif reviews:
            status, reason = "review", ",".join(dict.fromkeys(reviews))
        return ComponentNode(
            type=type_, rule=rules[0] if rules else None, rules=tuple(rules), status=status, reason=reason,
            node_ids=tuple(k.node_id for k in nodes), source_ids=tuple(sources), children=tuple(children),
            flags=tuple(flags), **extra)

    def abstain(self, reason, nodes, sources, rules=(), children=(), flags=()):
        """Make an Abstain node: the tool does not decide this region and says why."""
        return self.node("Abstain", rules, reason, nodes, sources, children, abstain=True, flags=flags)

    # ---- sequences of items

    # Rules: R-I6 Sibling chain. Also sends each if to exit_if() (R-I8) or if_(), and each loop to loop().
    def items(self, items: list, top: bool = False, in_shell: bool = False) -> list[ComponentNode]:
        """`top`: these are the statements of a page's own top level; `in_shell`: they are a shell's children
        (see exit_if)."""
        out, i = [], 0
        while i < len(items):
            item = items[i]
            if isinstance(item, KeptNode):
                j = i
                while j < len(items) and isinstance(items[j], KeptNode):
                    j += 1
                out += self.leaves(items[i:j])
                i = j
            elif isinstance(item, _If) and (exiting := self.exit_if(item, items[i + 1:], top, in_shell)) is not None:
                out.append(exiting)
                i += 1
            elif isinstance(item, _If):
                chain = [item]
                if self.chainable(item):
                    j = i + 1
                    while (j < len(items) and isinstance(items[j], _If) and self.chainable(items[j])
                           and self.parent_element(items[j]) == self.parent_element(item)):
                        chain.append(items[j])
                        j += 1
                if len(chain) > 1:  # R-I6; the conditions' text is not in the timeline, so exclusivity is unchecked
                    out.append(self.conditional(chain, "InlineConditional", "R-I6", "exclusivity_not_verified",
                                                review=("exclusivity_not_verified",)))
                else:
                    out.append(self.if_(item))
                i += len(chain)
            elif isinstance(item, _Loop):
                out.append(self.loop(item))
                i += 1
            else:  # no rule covers switch_case / try_catch
                out.append(self.abstain(f"{item.enclosure.role}_enclosure", _flat([item]), (item.enclosure.node_id,)))
                i += 1
        return out

    # Rules: R-I3 Attribute conditional (dynamic output inside an attribute value).
    def leaves(self, nodes: list[KeptNode]) -> list[ComponentNode]:
        """Plain output nodes: dynamic output that lands inside a tag is an AttributeExpression, the rest Static."""
        out, run, run_attribute = [], [], None

        def flush():
            """Close the current run of plain output as one Static node, or as an AttributeExpression (R-I3) when it is
            inside an attribute."""
            if run and run_attribute:
                out.append(self.node("AttributeExpression", ("R-I3",), "attribute_output", list(run)))
            elif run:
                out.append(self.node("Static", (), "static", list(run)))
            run.clear()

        def flush_calls():
            """Close a run of consecutive unresolved calls as one Abstain gap (spec section 8a)."""
            if unresolved:  # spec v0.7 section 8a: consecutive unresolved calls are one gap
                out.append(self.abstain("unresolved_call", list(unresolved), tuple(k.node_id for k in unresolved)))
            unresolved.clear()

        unresolved: list[KeptNode] = []
        for k in nodes:
            if _is_call(k):
                flush()
                run_attribute = None
                node = self.call(k)
                if node.status == "abstain" and node.reason == "unresolved_call":
                    unresolved.append(k)
                else:
                    flush_calls()
                    out.append(node)
                continue
            flush_calls()
            if k.output.parts and self.tags[k.node_id].dynamic_in_name:
                # boundary-rules section 1: an expression in a tag-name or attribute-name position
                flush()
                run_attribute = None
                out.append(self.abstain("markup_from_expression", [k], (k.node_id,)))
                continue
            attribute = k.output.raw is None and self.tags[k.node_id].in_tag_before
            if attribute != run_attribute:
                flush()
            run.append(k)
            run_attribute = attribute
        flush()
        flush_calls()
        return out

    # Rules: R-F1 Function component; R-F2 Shell pair (the page's place inside a shell, unbalanced functions).
    def call(self, k: KeptNode) -> ComponentNode:
        """What stands at a call site (boundary-rules section 4, the call-entry table)."""
        entry, source = self.calls[k.node_id], (k.node_id,)
        if entry.kind == app_units.CHILDREN:  # the place of the page inside a shell component
            return self.node("Static", ("R-F2",), "shell_children", [k], source)
        if self.tags[k.node_id].in_tag_before:
            return self.abstain("call_in_attribute", [k], source)
        unit = self.units.functions.get(entry.callee) if entry.callee else None
        if entry.kind == app_units.INCLUDE:
            # An included file is a unit only as half of a shell pair (R-F2), which the page takes care of.
            # Anywhere else its output is not in this timeline, and no rule places it here (section 5).
            if entry.callee is None or entry.outputs_inside == "unknown":  # not a file of the app, or not followed
                return self.abstain("unresolved_call", [k], source)
            if unit is not None and not unit.balanced:
                return self.abstain("tag_crosses_function", [k], source, ("R-F2",))
            return self.abstain("cross_file_output", [k], source)  # the file prints (itself, or what it includes)
        if unit is None or entry.outputs_inside == "unknown":  # no single callee, or output that cannot be followed
            return self.abstain("unresolved_call", [k], source)
        if unit.recursive:
            return self.abstain("recursive_function", [k], source, ("R-F1",))
        if unit.component is None:  # its tags cross the function boundary, and this call is not part of a shell
            return self.abstain("tag_crosses_function", [k], source, ("R-F2",))
        return self.node("FunctionCall", ("R-F1",), "function_component", [k], source, callee=unit.node_id)

    # Rules: R-F2 Shell pair (header + footer -> layout); R-I8 Exit-terminated arm outside the shell.
    def shell(self, items: list):
        """R-F2 condition 3 for this page: (ShellUse, the page's children) or (None, None).

        The page calls the header once and, later, the footer once, both at this level, and calls no other
        header or footer anywhere. What is printed before the header or after the footer abstains."""
        def callee(item):
            """The function a call node calls."""
            return self.calls[item.node_id].callee

        tops = [(i, item) for i, item in enumerate(items) if _is_call(item)]
        headers = [(i, item) for i, item in tops if callee(item) in self.units.shells]
        if len(headers) != 1:
            return None, None
        h, header = headers[0]
        pair = self.units.shells[callee(header)]
        footers = [(i, item) for i, item in tops if callee(item) == pair.footer]
        if len(footers) != 1 or footers[0][0] < h:
            return None, None
        f, footer = footers[0]
        mine = {header.node_id, footer.node_id}
        if any(_is_call(k) and k.node_id not in mine and callee(k) in self.units.shell_functions
               for k in self.outputs):
            return None, None
        children = []
        for group, outside in ((items[:h], True), (items[h + 1:f], False), (items[f + 1:], True)):
            if not outside:
                children += self.items(group, top=self.top_level, in_shell=True)
                continue
            i = 0
            while i < len(group):
                item = group[i]
                if _is_call(item):
                    # A call or include that is itself a gap keeps its own reason (nothing of it is generated
                    # anyway); one that would print here is output outside the shell.
                    j = i
                    while j < len(group) and _is_call(group[j]):
                        j += 1
                    for node in self.leaves(group[i:j]):
                        children.append(node if node.status == "abstain" else self.abstain(
                            "output_outside_shell", [k for k in group[i:j] if k.node_id in node.node_ids],
                            node.source_ids, ("R-F2",)))
                    i = j
                    continue
                position = next(n for n, x in enumerate(items) if x is item)
                if isinstance(item, KeptNode) and _blank(item):
                    children += self.leaves([item])
                elif isinstance(item, KeptNode):
                    children.append(self.abstain("output_outside_shell", [item], (item.node_id,), ("R-F2",)))
                elif isinstance(item, _If) and (
                        exiting := self.exit_if(item, items[position + 1:], self.top_level)) is not None:
                    children.append(exiting)  # R-I8: a redirect guard renders nothing; an exit arm ends the page
                elif isinstance(item, _If) and self.exits_inside(item):
                    # an exit arm nested in this if: R-I8's "rest of the enclosing block" is not all it ends
                    children.append(self.abstain("exit_arm_not_covered", _flat([item]), (item.node_id,), ("R-I8",)))
                else:
                    source = item.node_id if isinstance(item, _If) else item.enclosure.node_id
                    children.append(self.abstain("output_outside_shell", _flat([item]), (source,), ("R-F2",)))
                i += 1
        return ShellUse(pair.header, header.node_id, footer.node_id), children

    # ---- ifs

    def facts(self, region: _If) -> dict:
        """What the if rules need to know about a region: its output nodes, element count, whether it starts inside a
        tag, crosses tags, holds loops, has a blocking label or prints only JS."""
        branch_nodes = [_flat(b.items) for b in region.branches]
        nodes = [k for group in branch_nodes for k in group]
        runs = [self.run(group) for group in branch_nodes]
        elements = [tagtrack.element_count(r) for r in runs]
        in_tag = [self.tags[k.node_id].in_tag_before for k in nodes]
        loops = [(b, i) for b in region.branches for i, item in enumerate(b.items)
                 if isinstance(item, _Loop) and not all(_blank(k) for k in _flat(item.items))]
        blocked = region.label is not None and region.label.concern in BLOCKING_CONCERNS
        return {
            "nodes": nodes,
            "elements": max(elements),
            "attribute_only": all(in_tag) and sum(elements) == 0 and len(nodes) == sum(
                isinstance(item, KeptNode) for b in region.branches for item in b.items),
            "starts_in_tag": in_tag[0],
            "crosses": any(not tagtrack.is_balanced(r) for r in runs),
            "loops": loops,
            "blocked": blocked,
            "in_value": self.tags[nodes[0].node_id].in_value_before,
            "only_js": tagtrack.only_inline_js(self.run(nodes)),
        }

    # Rules: R-I6 Sibling chain (which ifs may join one).
    def chainable(self, region: _If) -> bool:
        """True if a region can join an R-I6 sibling chain: small, inside one parent element, no loop, no blocking
        label, no exit."""
        f = self.facts(region)
        return (not f["starts_in_tag"] and not f["crosses"] and not f["loops"] and not f["blocked"]
                and not f["only_js"] and f["elements"] <= self.config.small_if_max_elements
                and not any(b.enclosure.exits for b in region.branches))

    def parent_element(self, region: _If):
        """The element a region prints into (the top of the open-tag stack before it)."""
        stack = self.tags[self.facts(region)["nodes"][0].node_id].stack_before
        return stack[-1].serial if stack else None

    def branch_children(self, region: _If) -> list[ComponentNode]:
        """The tree nodes built from every branch of a region, in order."""
        return [child for branch in region.branches for child in self.items(branch.items)]

    def conditional(self, regions: list[_If], type_, rule, reason, review=()) -> ComponentNode:
        """Make one conditional node (of `type_`, by `rule`) for one or more if regions."""
        facts = [self.facts(r) for r in regions]
        return self.node(
            type_, (rule,), reason, [k for f in facts for k in f["nodes"]], tuple(r.node_id for r in regions),
            [child for r in regions for child in self.branch_children(r)], review=review,
            elements=max(f["elements"] for f in facts))

    # ---- R-I8: an arm that ends the script

    def redirect_arm(self, branch: _Branch) -> bool:
        """The arm always exits and its only output is a redirect (R-I8, case 1)."""
        nodes = _flat(branch.items)
        return bool(branch.enclosure.exits and nodes and not any(_is_call(k) for k in nodes)
                    and not self.tags[nodes[0].node_id].in_tag_before
                    and tagtrack.only_redirect("".join(tagtrack.output_text(k.output) for k in nodes)))

    def redirect_only(self, region: _If) -> bool:
        """Everything the if prints is a redirect of an arm that exits, at any depth."""
        def silent(branch: _Branch) -> bool:
            """True if a branch only redirects, or holds nothing but ifs that only redirect."""
            if self.redirect_arm(branch):
                return True
            inner = [i for i in branch.items if not (isinstance(i, KeptNode) and _blank(i))]
            return bool(inner) and all(isinstance(i, _If) and self.redirect_only(i) for i in inner)

        return all(silent(branch) for branch in region.branches)

    def exits_inside(self, region: _If) -> bool:
        """Some arm of the if, at any depth, always exits."""
        return any(b.enclosure.exits and _flat(b.items) or any(
            isinstance(i, _If) and self.exits_inside(i) for i in b.items) for b in region.branches)

    # Rules: R-I8 Exit-terminated arm; R-I8c Exit arm in an included file or function.
    def exit_if(self, region: _If, rest: list, top: bool, in_shell: bool = False) -> ComponentNode | None:
        """R-I8 / R-I8c for an if with an arm that always ends in `exit` / `die`; None when the if has no such
        arm, or when the other rules judge it (its exiting arms then render nothing, Stage 5).

        R-I8c (v0.14): in a called function or an included file, an exit arm is not generated (its include and
        call sites carry the flag). R-I8 case 1, a redirect guard, at any depth in a page (v0.15): not a
        component, nothing rendered, the rest renders; beside arms with other output it renders nothing itself.
        Case 2, other output: `cond ? <arm> : <rest of the block>`, only for the `then` arm of an if among a
        page's own top-level statements whose rest closes nothing opened before the if, and not inside a
        shell's children (the footer after it would still render). Any other exit arm: abstain."""
        exiting = [b for b in region.branches if b.enclosure.exits and _flat(b.items)]
        if not exiting:
            return None
        nodes, source = _flat([region]), (region.node_id,)
        if self.in_function or self.included:  # R-I8c
            if all(b.enclosure.exits for b in region.branches if _flat(b.items)):
                return self.node("Static", ("R-I8c",), "exit_output_not_generated", nodes, source)
            return None
        if self.redirect_only(region):  # case 1
            return self.node("Static", ("R-I8",), "redirect_guard", nodes, source, flags=("redirect_guard",))
        plain = [b for b in exiting if not self.redirect_arm(b)]
        if not plain:
            return None  # case 1 beside other arms: those are judged by the usual rules
        f = self.facts(region)
        rest_nodes = _flat(rest)
        covered = (top and not in_shell and len(exiting) == 1 and plain[0] is region.branches[0]
                   and plain[0].enclosure.branch == "then" and not f["starts_in_tag"] and not f["crosses"]
                   and (not rest_nodes or tagtrack.is_balanced(self.run(rest_nodes))))
        if not covered:
            return self.abstain("exit_arm_not_covered", nodes, source, ("R-I8",), self.branch_children(region))
        return self.node("InlineConditional", ("R-I8",), "exit_arm", nodes, source, self.branch_children(region))

    # ---- R-I3b: an if between the attributes of a start tag

    # Rules: R-I3b Attribute-presence conditional (the check).
    def attribute_presence(self, region: _If) -> bool:
        """Every arm prints only complete literal attributes (`selected`, `class="nav-item"`)."""
        return all(tagtrack.literal_attributes(tagtrack.literal_text([k.output for k in _flat(branch.items)]))
                   is not None for branch in region.branches)

    # Rules, in this order: R-I3 Attribute conditional, R-I3b Attribute-presence conditional, R-I7 No output left,
    # R-L6 Tags crossing a branch, R-I2 List guard (via guarded_list), R-I2b Multi-loop list guard,
    # R-I4 Small content if, R-I5 Fallback. No rule fits -> abstain.
    def if_(self, region: _If) -> ComponentNode:
        """Classify one if region by the if rules (boundary-rules section 3), in order, or abstain."""
        f = self.facts(region)
        nodes, source = f["nodes"], (region.node_id,)
        if f["attribute_only"] and f["in_value"]:  # R-I3: never a boundary
            return self.node("AttributeExpression", ("R-I3",), "attribute_conditional", nodes, source)
        if f["attribute_only"]:  # between attributes, not inside a value
            if self.attribute_presence(region):  # R-I3b: conditional props
                return self.node("AttributeExpression", ("R-I3b",), "attribute_presence", nodes, source)
            return self.abstain("partial_attribute_output", nodes, source, ("R-I3b",))
        if f["starts_in_tag"]:
            return self.abstain("partial_attribute_output", nodes, source)
        if f["only_js"]:  # R-I7: nothing left to render, so no boundary (the JS is flagged by Stage 5)
            return self.node("Static", ("R-I7",), "only_inline_js", nodes, source)
        if f["crosses"]:
            return self.abstain("tag_crosses_branch", nodes, source, ("R-L6",), self.branch_children(region),
                                flags=("tag_crosses_branch",))
        if region.label is not None and region.label.concern is Concern.BUSINESS_LOGIC:
            # An authorization guard, not an empty-state check, whether or not it holds a loop (R-I2, section 5).
            # Its content, including any List, is still built underneath it.
            return self.abstain("cuts_across_business_logic", nodes, source, (), self.branch_children(region))
        if len(f["loops"]) == 1:
            return self.guarded_list(region, *f["loops"][0])
        if f["loops"] and len({id(branch) for branch, _ in f["loops"]}) == 1:
            # R-I2b: one guard around several Lists; its other arm is their Empty.
            list_branch, children = f["loops"][0][0], []
            for branch in region.branches:
                if branch is list_branch:
                    children += self.items(branch.items)
                elif _flat(branch.items):
                    children.append(self.node("Empty", ("R-I2b",), "empty_state", _flat(branch.items), source,
                                              self.items(branch.items)))
            return self.node("InlineConditional", ("R-I2b",), "multi_loop_list_guard", nodes, source, children)
        if f["loops"]:  # loops in different arms: neither R-I2 nor R-I2b says which List an arm is the Empty of
            return self.abstain("list_guard_multiple_loops", nodes, source, (), self.branch_children(region))
        if f["blocked"]:  # section 5: the boundary would cut across a data-access or mixed node
            return self.abstain(f"cuts_across_{region.label.concern.value}", nodes, source, (),
                                self.branch_children(region))
        if f["elements"] <= self.config.small_if_max_elements:
            return self.conditional([region], "InlineConditional", "R-I4", "small_content_if")
        if f["elements"] >= self.config.fallback_component_min_elements:
            return self.conditional([region], "ConditionalComponent", "R-I5", "fallback_size")
        return self.abstain("no_rule", nodes, source, (), self.branch_children(region))

    # Rules: R-I2 List guard (the if's other arm becomes the List's Empty state).
    def guarded_list(self, region: _If, list_branch: _Branch, index: int) -> ComponentNode:
        """R-I2: the if is the List's empty-state condition."""
        nodes, source = self.facts(region)["nodes"], (region.node_id,)
        parts = self.list_parts(list_branch.items[index])
        if isinstance(parts, ComponentNode):  # the loop abstained, so there is no List to guard
            return self.abstain("guarded_loop_abstained", nodes, source, (), self.branch_children(region))
        children = []
        for branch in region.branches:
            if branch is list_branch:  # headings and the like inside the guard belong to the List
                children += self.items(branch.items[:index]) + [parts["child"]] + self.items(branch.items[index + 1:])
            elif _flat(branch.items):
                children.append(self.node("Empty", ("R-I2",), "empty_state", _flat(branch.items), source,
                                          self.items(branch.items)))
        return self.node("List", (*parts["rules"], "R-I2"), parts["reason"], nodes,
                         (list_branch.items[index].enclosure.node_id, region.node_id), children,
                         container=parts["container"], loop_concern=parts["loop_concern"])

    # ---- loops

    # Rules, in this order: R-L5 Loop with no output, R-I7 No output left, R-L7 Chunked loop, R-L6 Tags crossing,
    # R-L2b Item with no wrapper / R-L2a Nearest open tag / R-F1b Caller-opened container (the container),
    # then R-L3b Text-only body, R-L3 One-element body, else R-L1 List + Item.
    def list_parts(self, region: _Loop):
        """The pieces of a List for this loop, or the node to use instead when it cannot be one."""
        nodes, source = _flat(region.items), (region.enclosure.node_id,)
        run = self.run(nodes)
        if all(_blank(k) for k in nodes):  # R-L5
            return self.node("Static", ("R-L5",), "loop_without_output", nodes, source)
        if run[0].in_tag_before:
            return self.abstain("loop_in_attribute", nodes, source)
        if tagtrack.only_inline_js(run):  # R-I7
            return self.node("Static", ("R-I7",), "only_inline_js", nodes, source)
        if tagtrack.closes_outer(run):
            reopened = [e.tag for e in run[-1].stack_after] == [e.tag for e in run[0].stack_before]
            if reopened and not run[-1].in_tag_after:  # R-L7
                return self.abstain("chunked_container", nodes, source, ("R-L7",))
            return self.abstain("container_undetermined", nodes, source, ("R-L6",))
        if tagtrack.leaves_open(run):
            return self.abstain("container_undetermined", nodes, source, ("R-L6",))

        roots = tagtrack.root_tags(run)
        if not roots and tagtrack.element_count(run):  # elements, but none at the body's own level
            return self.abstain("no_rule", nodes, source)
        if len(roots) > 1:  # R-L2b
            container, container_rule, reason = ListContainer(False, None, None, None), "R-L2b", "item_has_no_wrapper"
        else:  # R-L2a
            outer = [e for e in reversed(run[0].stack_before) if e.tag not in self.config.table_section_tags]
            if not outer and self.in_function:  # R-F1b: the caller opens the container, around the call site
                container = ListContainer(False, None, None, None)
                container_rule, reason = "R-F1b", "container_opened_by_caller"
            elif not outer:
                return self.abstain("container_undetermined", nodes, source)
            else:
                container = ListContainer(True, outer[0].tag, outer[0].element_id, outer[0].opened_in)
                container_rule, reason = "R-L2a", "container_is_nearest_open_tag"

        label = region.enclosure.label
        common = {"reason": reason, "container": container, "loop_concern": label.concern.value if label else None}
        def judged(item) -> bool:
            """Something in the row that has a node of its own: an if or loop, a call, or an echo with an
            expression where a tag or attribute name is written (section 1: markup_from_expression)."""
            return (not isinstance(item, KeptNode) or _is_call(item)
                    or bool(item.output.parts and self.tags[item.node_id].dynamic_in_name))

        if not roots:  # R-L3b: text and expressions only -> an inline map of keyed Fragments, no Item component
            regions = [item for item in region.items if judged(item)]
            children = self.items(region.items) if regions else ()
            return common | {"rules": ("R-L1", container_rule, "R-L3b"),
                             "child": self.node("Static", ("R-L3b",), "inline_text_map", nodes, (), children)}
        if len(roots) == 1 and tagtrack.element_count(run) == 1:  # R-L3: stays an inline map
            # v0.7 scope: R-L3 decides only that there is no Item component. An if in the row is judged by the
            # if rules like any other (an inline map whose body holds one gets those nodes as children).
            regions = [item for item in region.items if judged(item)]
            children = self.items(region.items) if regions else ()
            return common | {"rules": ("R-L1", container_rule, "R-L3"),
                             "child": self.node("Static", ("R-L3",), "inline_map", nodes, (), children)}
        item = self.node("Item", ("R-L1",), "loop_body", nodes, (), self.items(region.items), root_tags=roots)
        return common | {"rules": ("R-L1", container_rule), "child": item}

    # Rules: R-L1 Loop -> List + Item. R-L4 Nested loops: the Item's items() builds inner loops first.
    def loop(self, region: _Loop) -> ComponentNode:
        """Make a List node (R-L1) for a loop, with its container and its Item or inline child."""
        parts = self.list_parts(region)
        if isinstance(parts, ComponentNode):
            return parts
        return self.node("List", parts["rules"], parts["reason"], _flat(region.items), (region.enclosure.node_id,),
                         [parts["child"]], container=parts["container"], loop_concern=parts["loop_concern"])

    # ---- page

    def ajax_endpoint(self) -> bool:
        """Section 5 (v1.0), subtype ajax_endpoint: a file's top level with no markup of its own whose only output
        is `json_encode(...)` (whitespace aside), e.g. warehouse `ajax.php`. Calls and includes are not looked at:
        they are flagged where they stand."""
        own = [k.output for k in self.outputs if not app_units.is_slot(k.output)]
        echoes = [n for n in own if n.kind != "Stmt_InlineHTML"]
        return bool(not self.is_function and echoes
                    and all((n.raw or "").strip(HTML_SPACE) == "" for n in own if n.kind == "Stmt_InlineHTML")
                    and all(n.kind in ("Stmt_Echo", "Expr_Print") and n.reads and all(
                        r.expr.replace(" ", "").lower().startswith("json_encode(") for r in n.reads)
                        for n in echoes))

    # Rules: R-I1 Page guard (the v1.1 scope check).
    def other_path_leaves(self, guard: _If) -> bool:
        """Boundary rules v1.1, R-I1 scope (post-freeze change 1): the path that does not enter the guarded
        content leaves the page. It does when the statements after the if (in the same list) always exit or
        redirect, or when the if has an `else` and every arm other than the guarded one does (harness extension
        `topLevelIfs`: the R-I8 exit reading and the header_redirect detection). Nothing known -> it does not:
        the guard is then a conditional. v1.0 does not ask."""
        if self.config.rules_version == "1.0":
            return True
        top = self.top_level_ifs.get(guard.node_id)
        if top is None:
            return False
        arm = guard.branches[0].enclosure
        others = [leaves for branch, cond, leaves in top.arms if (branch, cond) != (arm.branch, arm.cond_node_id)]
        has_else = any(branch == "else" for branch, _, _ in top.arms)
        return top.after_leaves or (has_else and all(others))

    # Rules: R-I1 Page guard; R-F2 Shell pair (the Page wrapped in a layout).
    def page(self) -> ComponentNode:
        """Build the Page root: a non-HTML response or AJAX endpoint abstains (section 5); otherwise the page's regions
        become its children, after the page-guard check (R-I1)."""
        if self.content_type is not None and self.content_type not in HTML_MEDIA_TYPES:
            # Section 5: the unit is not an HTML response (RSS, a sitemap). It is an API route, owned by C1.
            return self.node("Page", (), "non_html_response", self.outputs, (), (), abstain=True)
        if self.ajax_endpoint():  # section 5 (v1.0): an AJAX endpoint, also an API route owned by C1
            return self.node("Page", (), "non_html_response", self.outputs, (), (), abstain=True,
                             flags=("ajax_endpoint",))
        items = _regions(self.outputs)
        regions = [item for item in items if not isinstance(item, KeptNode)]
        others_blank = all(_blank(item) for item in items if isinstance(item, KeptNode))
        rules, reason, sources, inner = (), "page", (), items
        if (len(regions) == 1 and isinstance(regions[0], _If) and len(regions[0].branches) == 1 and others_blank
                and not regions[0].branches[0].enclosure.exits and self.other_path_leaves(regions[0])):
            # R-I1: the if wraps everything the page prints, so it is not a component. An arm that always exits
            # is an error stop or a redirect, not the page: R-I8 / R-I8c decide it (precedence chosen in step 4).
            # v1.1: only when the path that skips it leaves the page; otherwise the if rules decide it below.
            guard = regions[0]
            inner = [x for item in items for x in (guard.branches[0].items if item is guard else [item])]
            rules, reason, sources = ("R-I1",), "page_guard", (guard.node_id,)
        self.top_level = not rules  # inside an R-I1 page guard is not the page's own top level (R-I8)
        shell, children = self.shell(inner) if self.units is not None else (None, None)
        if shell is None:
            return self.node("Page", rules, reason, self.outputs, sources, self.items(inner, top=not rules))
        return self.node("Page", (*rules, "R-F2"), reason, self.outputs, sources, children, shell=shell)


def _walk(node: ComponentNode):
    """Every node of a component tree, depth first."""
    yield node
    for child in node.children:
        yield from _walk(child)


def infer_boundaries(stage1_result: PresentationResult, timeline: Timeline, config: BoundaryConfig,
                     units=None) -> ComponentTree:
    """`units` (src/mapping/app_units.py) turns on the function and layout rules; without it call entries are
    not looked at, as for a timeline on its own."""
    order = {node.id: index for index, node in enumerate(timeline.sequence)}
    outputs = sorted((k for k in stage1_result.kept if k.node_type == "output"), key=lambda k: order[k.node_id])
    if units is not None and (app_units.slots(timeline) or app_units.include_text(timeline)):
        # Every call that may print gets an empty slot among the output nodes, at its place in the sequence; the
        # include of a whitespace-only file stands there as its text (rules v1.0 section 4)
        labels = {e.node_id: e.label for k in outputs for e in k.enclosed_by}
        order = {node_id: index for index, node_id in enumerate(timeline.order)}
        for slot in app_units.slots(timeline):
            enclosed = tuple(replace(e, label=labels.get(e.node_id)) for e in slot.enclosed_by)
            outputs.append(KeptNode(
                node_id=slot.id, node_type="call", kind=slot.kind, status=Status.OK, reason="call", label=None,
                enclosed_by=enclosed, output=OutputNode(slot.id, slot.kind, "", (), enclosed, slot.loc)))
        for text in app_units.include_text(timeline):
            enclosed = tuple(replace(e, label=labels.get(e.node_id)) for e in text.enclosed_by)
            outputs.append(KeptNode(
                node_id=text.id, node_type="output", kind=text.kind, status=Status.OK, reason="include_whitespace",
                label=None, enclosed_by=enclosed, output=replace(text, enclosed_by=enclosed)))
        outputs.sort(key=lambda k: order[k.node_id])
    # Reviews that carry from an enclosing if or loop to the node built from it. `mixed` is left out:
    # it is information only (section 4a) and is recorded as loop_concern instead. A review on an
    # output node is read from the node itself when its leaf is built.
    review = {
        k.node_id: k.reason for k in stage1_result.kept
        if k.status is Status.REVIEW and k.node_type != "output"
        and not (k.label is not None and k.label.concern is Concern.MIXED)
    }
    root = _Builder(outputs, review, config, timeline, units).page()
    nodes = list(_walk(root))
    counts = TreeCounts(
        components=sum(n.type in COMPONENT_TYPES for n in nodes),
        abstain=sum(n.status == "abstain" for n in nodes),
        review=sum(n.status == "review" for n in nodes),
    )
    return ComponentTree(entrypoint=timeline.entrypoint, root=root, counts=counts)


def leaves(tree: ComponentTree) -> list[ComponentNode]:
    """The tree's leaf nodes."""
    return [n for n in _walk(tree.root) if not n.children]


def find(tree: ComponentTree, **where) -> list[ComponentNode]:
    """Every node whose fields equal `where`, in tree order (e.g. find(tree, type="List"))."""
    return [n for n in _walk(tree.root) if all(getattr(n, key) == value for key, value in where.items())]


# ----------------------------------------------------------------------------- serialisation


def _node_dict(node: ComponentNode) -> dict:
    """A component-tree node as JSON, with its children."""
    container = node.container
    return {
        "type": node.type,
        "rule": node.rule,
        "rules": list(node.rules),
        "status": node.status,
        "reason": node.reason,
        "sourceIds": list(node.source_ids),
        "container": None if container is None else {
            "hasWrapper": container.has_wrapper, "tag": container.tag, "id": container.element_id,
            "openedIn": container.opened_in},
        "loopConcern": node.loop_concern,
        "rootTags": list(node.root_tags),
        "elements": node.elements,
        "flags": list(node.flags),
        "nodeIds": list(node.node_ids),
        **({"callee": node.callee} if node.callee is not None else {}),
        **({"shell": {"header": node.shell.header, "headerCall": node.shell.header_call,
                      "footerCall": node.shell.footer_call}} if node.shell is not None else {}),
        "children": [_node_dict(child) for child in node.children],
    }


def tree_to_dict(tree: ComponentTree) -> dict:
    """The whole Stage 2 tree as the JSON written to stage2.json."""
    return {
        "stage": 2,
        "entrypoint": tree.entrypoint,
        "counts": {"components": tree.counts.components, "abstain": tree.counts.abstain,
                   "review": tree.counts.review},
        "tree": _node_dict(tree.root),
    }


def dumps(tree: ComponentTree) -> str:
    """Deterministic JSON: fixed key order, no timestamps or paths."""
    return json.dumps(tree_to_dict(tree), indent=2, ensure_ascii=False) + "\n"


def write_tree(tree: ComponentTree, path: Path) -> None:
    """Write the Stage 2 tree as JSON to `path` (LF line endings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(tree))


def summary(tree: ComponentTree) -> str:
    """One summary line: components, abstain and review counts."""
    c = tree.counts
    return f"[Stage 2] {c.components} components, {c.abstain} abstain, {c.review} review"
