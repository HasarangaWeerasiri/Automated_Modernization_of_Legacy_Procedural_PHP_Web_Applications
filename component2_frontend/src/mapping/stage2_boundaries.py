"""Stage 2: boundary inference.

Turns the Stage 1 result into a component tree by applying the owner's
boundary rules (docs/php-analysis/boundary-rules.md, draft v0.2) as written.
Thresholds come from config/boundary_rules.json. Where no rule covers a
structure the node is an Abstain: nothing is guessed.

How the rules are applied:

  loops   R-L5 no output -> not a boundary. R-L7 body closes and reopens its
          container -> abstain. Otherwise R-L1: a List holding one Item.
          R-L2b when the item has several root elements (no wrapper), else
          R-L2a (container = nearest open tag, walking past table sections).
          R-L3: a one-element item stays inline, no Item component.
          R-L4 needs no code of its own: the walk is innermost first.
  ifs     R-I3 output in attribute position. R-L6 tags crossing a branch ->
          abstain. R-I2 a branch holding a loop -> that List's guard; the
          other branch is its Empty. Then by size: R-I4 inline, R-I5 its own
          component. R-I6 merges adjacent small ifs in one element.
          R-I1 is decided at page level.

Enclosure labels: a loop or list guard labelled mixed or business_logic is
still used as structure (CONTEXT.md rule 4a). A content if with such a label
would move that logic into the UI, so it abstains (boundary-rules.md section 5).
Stage 1 review statuses carry to the node built from the reviewed construct.

Not implemented: R-F1 and R-F2 (function and layout components), blocked on
whether a page's timeline includes the output of the functions it calls.
"""

import json
from dataclasses import dataclass
from pathlib import Path

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
    PresentationResult,
    Status,
    Timeline,
    TreeCounts,
)

COMPONENT_TYPES = ("Page", "Layout", "List", "Item", "Empty", "ConditionalComponent")
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


def _blank(node: KeptNode) -> bool:
    """Whitespace-only inline HTML."""
    return node.output.raw is not None and node.output.raw.strip() == ""


# ----------------------------------------------------------------------------- the rules


class _Builder:
    def __init__(self, outputs: list[KeptNode], review: dict[str, str], config: BoundaryConfig):
        self.outputs = outputs
        self.review = review  # Stage 1 review reason by node id
        self.config = config
        self.tags = tagtrack.track([k.output for k in outputs], config)

    def run(self, nodes: list[KeptNode]) -> list[tagtrack.NodeTags]:
        return [self.tags[k.node_id] for k in nodes]

    def node(self, type_, rules, reason, nodes, sources=(), children=(), abstain=False, flags=(), **extra):
        reviews = [self.review[s] for s in sources if s in self.review]
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
        return self.node("Abstain", rules, reason, nodes, sources, children, abstain=True, flags=flags)

    # ---- sequences of items

    def items(self, items: list) -> list[ComponentNode]:
        out, i = [], 0
        while i < len(items):
            item = items[i]
            if isinstance(item, KeptNode):
                j = i
                while j < len(items) and isinstance(items[j], KeptNode):
                    j += 1
                out += self.leaves(items[i:j])
                i = j
            elif isinstance(item, _If):
                chain = [item]
                if self.chainable(item):
                    j = i + 1
                    while (j < len(items) and isinstance(items[j], _If) and self.chainable(items[j])
                           and self.parent_element(items[j]) == self.parent_element(item)):
                        chain.append(items[j])
                        j += 1
                if len(chain) > 1:  # R-I6; the conditions' text is not in the timeline, so exclusivity is unchecked
                    out.append(self.conditional(chain, "InlineConditional", "R-I6", "exclusivity_not_verified"))
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

    def leaves(self, nodes: list[KeptNode]) -> list[ComponentNode]:
        """Plain output nodes: dynamic output that lands inside a tag is an AttributeExpression, the rest Static."""
        out, run, run_attribute = [], [], None

        def flush():
            if run and run_attribute:
                out.append(self.node("AttributeExpression", ("R-I3",), "attribute_output", list(run)))
            elif run:
                out.append(self.node("Static", (), "static", list(run)))
            run.clear()

        for k in nodes:
            attribute = k.output.raw is None and self.tags[k.node_id].in_tag_before
            if attribute != run_attribute:
                flush()
            run.append(k)
            run_attribute = attribute
        flush()
        return out

    # ---- ifs

    def facts(self, region: _If) -> dict:
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
        }

    def chainable(self, region: _If) -> bool:
        f = self.facts(region)
        return (not f["starts_in_tag"] and not f["crosses"] and not f["loops"] and not f["blocked"]
                and f["elements"] <= self.config.small_if_max_elements)

    def parent_element(self, region: _If):
        stack = self.tags[self.facts(region)["nodes"][0].node_id].stack_before
        return stack[-1].serial if stack else None

    def branch_children(self, region: _If) -> list[ComponentNode]:
        return [child for branch in region.branches for child in self.items(branch.items)]

    def conditional(self, regions: list[_If], type_, rule, reason) -> ComponentNode:
        facts = [self.facts(r) for r in regions]
        return self.node(
            type_, (rule,), reason, [k for f in facts for k in f["nodes"]], tuple(r.node_id for r in regions),
            [child for r in regions for child in self.branch_children(r)], elements=max(f["elements"] for f in facts))

    def if_(self, region: _If) -> ComponentNode:
        f = self.facts(region)
        nodes, source = f["nodes"], (region.node_id,)
        if f["attribute_only"]:  # R-I3: never a boundary
            return self.node("AttributeExpression", ("R-I3",), "attribute_conditional", nodes, source)
        if f["starts_in_tag"]:
            return self.abstain("partial_attribute_output", nodes, source)
        if f["crosses"]:
            return self.abstain("tag_crosses_branch", nodes, source, ("R-L6",), self.branch_children(region),
                                flags=("tag_crosses_branch",))
        if len(f["loops"]) == 1:
            return self.guarded_list(region, *f["loops"][0])
        if f["loops"]:  # R-I2 speaks of one List per guard
            return self.abstain("list_guard_multiple_loops", nodes, source, (), self.branch_children(region))
        if f["blocked"]:  # section 5: the boundary would cut across a logic / data-access node
            return self.abstain(f"cuts_across_{region.label.concern.value}", nodes, source, (),
                                self.branch_children(region))
        if f["elements"] <= self.config.small_if_max_elements:
            return self.conditional([region], "InlineConditional", "R-I4", "small_content_if")
        if f["elements"] >= self.config.fallback_component_min_elements:
            return self.conditional([region], "ConditionalComponent", "R-I5", "fallback_size")
        return self.abstain("no_rule", nodes, source, (), self.branch_children(region))

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
                         container=parts["container"])

    # ---- loops

    def list_parts(self, region: _Loop):
        """The pieces of a List for this loop, or the node to use instead when it cannot be one."""
        nodes, source = _flat(region.items), (region.enclosure.node_id,)
        run = self.run(nodes)
        if all(_blank(k) for k in nodes):  # R-L5
            return self.node("Static", ("R-L5",), "loop_without_output", nodes, source)
        if run[0].in_tag_before:
            return self.abstain("loop_in_attribute", nodes, source)
        if tagtrack.closes_outer(run):
            reopened = [e.tag for e in run[-1].stack_after] == [e.tag for e in run[0].stack_before]
            if reopened and not run[-1].in_tag_after:  # R-L7
                return self.abstain("chunked_container", nodes, source, ("R-L7",))
            return self.abstain("container_undetermined", nodes, source, ("R-L6",))
        if tagtrack.leaves_open(run):
            return self.abstain("container_undetermined", nodes, source, ("R-L6",))

        roots = tagtrack.root_tags(run)
        if not roots:  # a loop that prints text only: no rule describes its item
            return self.abstain("no_rule", nodes, source)
        if len(roots) > 1:  # R-L2b
            container, container_rule, reason = ListContainer(False, None, None, None), "R-L2b", "item_has_no_wrapper"
        else:  # R-L2a
            outer = [e for e in reversed(run[0].stack_before) if e.tag not in self.config.table_section_tags]
            if not outer:
                return self.abstain("container_undetermined", nodes, source)
            container = ListContainer(True, outer[0].tag, outer[0].element_id, outer[0].opened_in)
            container_rule, reason = "R-L2a", "container_is_nearest_open_tag"

        if len(roots) == 1 and tagtrack.element_count(run) == 1:  # R-L3: stays an inline map
            return {"rules": ("R-L1", container_rule, "R-L3"), "reason": reason, "container": container,
                    "child": self.node("Static", ("R-L3",), "inline_map", nodes)}
        item = self.node("Item", ("R-L1",), "loop_body", nodes, (), self.items(region.items), root_tags=roots)
        return {"rules": ("R-L1", container_rule), "reason": reason, "container": container, "child": item}

    def loop(self, region: _Loop) -> ComponentNode:
        parts = self.list_parts(region)
        if isinstance(parts, ComponentNode):
            return parts
        return self.node("List", parts["rules"], parts["reason"], _flat(region.items), (region.enclosure.node_id,),
                         [parts["child"]], container=parts["container"])

    # ---- page

    def page(self) -> ComponentNode:
        items = _regions(self.outputs)
        regions = [item for item in items if not isinstance(item, KeptNode)]
        others_blank = all(_blank(item) for item in items if isinstance(item, KeptNode))
        if len(regions) == 1 and isinstance(regions[0], _If) and len(regions[0].branches) == 1 and others_blank:
            # R-I1: the if wraps everything the page prints, so it is not a component.
            guard = regions[0]
            inner = [x for item in items for x in (guard.branches[0].items if item is guard else [item])]
            return self.node("Page", ("R-I1",), "page_guard", self.outputs, (guard.node_id,), self.items(inner))
        return self.node("Page", (), "page", self.outputs, (), self.items(items))


def _walk(node: ComponentNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def infer_boundaries(stage1_result: PresentationResult, timeline: Timeline, config: BoundaryConfig) -> ComponentTree:
    order = {node.id: index for index, node in enumerate(timeline.sequence)}
    outputs = sorted((k for k in stage1_result.kept if k.node_type == "output"), key=lambda k: order[k.node_id])
    review = {k.node_id: k.reason for k in stage1_result.kept if k.status is Status.REVIEW}
    root = _Builder(outputs, review, config).page()
    nodes = list(_walk(root))
    counts = TreeCounts(
        components=sum(n.type in COMPONENT_TYPES for n in nodes),
        abstain=sum(n.status == "abstain" for n in nodes),
        review=sum(n.status == "review" for n in nodes),
    )
    return ComponentTree(entrypoint=timeline.entrypoint, root=root, counts=counts)


def leaves(tree: ComponentTree) -> list[ComponentNode]:
    return [n for n in _walk(tree.root) if not n.children]


def find(tree: ComponentTree, **where) -> list[ComponentNode]:
    """Every node whose fields equal `where`, in tree order (e.g. find(tree, type="List"))."""
    return [n for n in _walk(tree.root) if all(getattr(n, key) == value for key, value in where.items())]


# ----------------------------------------------------------------------------- serialisation


def _node_dict(node: ComponentNode) -> dict:
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
        "rootTags": list(node.root_tags),
        "elements": node.elements,
        "flags": list(node.flags),
        "nodeIds": list(node.node_ids),
        "children": [_node_dict(child) for child in node.children],
    }


def tree_to_dict(tree: ComponentTree) -> dict:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(tree))


def summary(tree: ComponentTree) -> str:
    c = tree.counts
    return f"[Stage 2] {c.components} components, {c.abstain} abstain, {c.review} review"
