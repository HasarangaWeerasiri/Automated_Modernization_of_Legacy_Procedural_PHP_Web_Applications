"""Stage 3: data requirement recovery (docs/reconciliation-spec.md section 1).

For every component of the Stage 2 tree, the data it needs: what its output
nodes read, classified by the read's sourceKind and confidence.

  db_row_field + resolved   -> field need {table, column, queryNodeId}
  db_row_field + ambiguous  -> ambiguous need, flagged ambiguous_needs_schema
  unresolved (any)          -> unresolved need, flagged unresolved_read
  session / request / server -> context need (Stage 4 excludes it)
  computed                  -> followed through derivedFrom to its base reads;
                               the wrapping functions are recorded on the need
  literal                   -> nothing

An output node belongs to the nearest component above it in the tree (Page,
List, Item, Empty, ConditionalComponent). Inline pieces (Static,
AttributeExpression, InlineConditional) and Abstain nodes are not components,
so their reads go to that component. A need inside an Item (or inside an
R-L3 inline map) belongs to a row of that List: `row_of` names the List. A
List also gets one collection need: the loop it iterates and the query that
loop reads, found from the reads of the loop's value variable.

Needs are de-duplicated per component and kept in first-appearance order.
Every reference carries the file (the timeline's entrypoint) and the line
from the output node's loc (startLine, and startCol + 1 as the 1-based col); a node without loc gets None,
never a guess. A collection's reference is its loop, which has no loc, so
its line is None.

Needs from content inside a Stage 2 Abstain node are kept (the data is still
rendered) and marked: each reference carries from_abstained, and a need is
from_abstained when all of its references are (spec section 1.2a).
"""

import json
import re
from pathlib import Path

from src.mapping.stage2_boundaries import COMPONENT_TYPES
from src.model import (
    ComponentNeeds,
    ComponentNode,
    ComponentTree,
    Need,
    NeedReference,
    Read,
    RequirementsResult,
    Timeline,
)

CONTEXT_KINDS = ("session", "request", "server")
_CALLEE = re.compile(r"\s*([A-Za-z_\\][\w\\]*(?:::[A-Za-z_]\w*)?)\s*\(")


def _wrapper(expr: str) -> str | None:
    """The function a computed read calls, e.g. "number_format" for number_format($row['x'], 2)."""
    found = _CALLEE.match(expr)
    return found.group(1) if found else None


def _base_reads(read: Read, wrappers: tuple[str, ...] = ()):
    """(read, wrappers) for every non-computed read under `read`, wrappers outermost first."""
    if read.source_kind == "computed":
        name = _wrapper(read.expr)
        for inner in read.derived_from:
            yield from _base_reads(inner, wrappers + ((name,) if name else ()))
    else:
        yield read, wrappers


def _classify(read: Read):
    """(dedup key, Need fields) for one base read, or None when it needs nothing (a literal)."""
    kind, confidence, source = read.source_kind, read.confidence, read.source
    if kind == "literal":
        return None
    if kind in CONTEXT_KINDS:
        return ("context", kind, read.var, read.path), {"kind": "context", "name": read.expr, "source_kind": kind}
    if kind == "db_row_field" and source is not None and confidence == "resolved":
        return ("field", source.table, source.column, source.query_node_id), {
            "kind": "field", "name": source.column, "table": source.table, "column": source.column,
            "query_node_id": source.query_node_id}
    if kind == "db_row_field" and source is not None and confidence == "ambiguous":
        name = read.path[-1] if read.path else read.expr
        return ("ambiguous", source.query_node_id, read.path), {
            "kind": "ambiguous", "name": name, "query_node_id": source.query_node_id,
            "flag": "ambiguous_needs_schema"}
    # sourceKind unresolved, a db_row_field read whose confidence is unresolved, or a read with no source
    return ("unresolved", read.expr), {"kind": "unresolved", "name": read.expr, "flag": "unresolved_read"}


class _Component:
    def __init__(self, component_id: str, node: ComponentNode, row_of: str | None):
        """A component being filled: its id, its tree node, the List whose row it reads, and its needs."""
        self.component_id, self.node, self.row_of = component_id, node, row_of
        self.needs: dict[tuple, dict] = {}  # dedup key -> Need fields + references, in first-appearance order

    def add(self, key, fields, reference, row_of):
        """Add one reference to a need, merging references to the same need (same key, same row owner)."""
        key = (*key, row_of)
        entry = self.needs.setdefault(key, fields | {"row_of": row_of, "references": []})
        entry["references"].append(reference)


def recover_requirements(component_tree: ComponentTree, timeline: Timeline) -> RequirementsResult:
    """Stage 3: walk the component tree and list, for each component, the data its output reads (field, ambiguous,
    unresolved or context needs, and each List's collection)."""
    outputs = {node.id: node for node in timeline.sequence}
    loops = {e.node_id: e for node in timeline.sequence for e in node.enclosed_by if e.role == "iteration"}
    components: list[_Component] = []
    collections: dict[str, dict] = {}  # List component id -> its collection need, filled as rows are read

    def fetched_by(base, ancestors: tuple, row_of: str | None) -> str | None:
        """R-L4 (v0.14): a read whose fetch is an outer loop's belongs to that loop's List, not the inner one."""
        fetch = base.source.fetch_node_id if base.source is not None else None
        if fetch is None:
            return row_of
        return next((lid for lid, loop in reversed(ancestors) if loop is not None and loop.fetch_node_id == fetch),
                    row_of)

    def visit(node: ComponentNode, component_id: str, owner: _Component | None, row_of: str | None,
              parent_list: str | None, abstained: bool, ancestors: tuple = ()) -> None:
        """Visit a tree node: start a new component for component types, then collect the needs of its output nodes and
        children."""
        abstained = abstained or node.type == "Abstain"
        if node.type in COMPONENT_TYPES:
            if node.type == "Item":
                row_of = parent_list
            owner = _Component(component_id, node, row_of)
            components.append(owner)
            if node.type == "List":
                loop = loops.get(node.source_ids[0]) if node.source_ids else None
                item = next((f"{component_id}.{i}" for i, c in enumerate(node.children) if c.type == "Item"), None)
                collections[component_id] = {
                    "loop": loop, "item": item, "row_of": row_of, "queries": [], "from_abstained": abstained,
                    "reference": NeedReference(node_id=loop.node_id if loop else "", line=None,
                                               expr=loop.iter_expr if loop else "", file=timeline.entrypoint,
                                               from_abstained=abstained)}
                parent_list = component_id
                ancestors = (*ancestors, (component_id, loop))
        if node.type == "Static" and node.rule in ("R-L3", "R-L3b"):  # an inline map's body is the List's row
            row_of = parent_list
        if not node.children:
            for node_id in node.node_ids:
                if node_id not in outputs:  # a call site (boundary rule R-F1): it reads nothing itself
                    continue
                loc = outputs[node_id].loc
                for read in outputs[node_id].reads:
                    for base, wrappers in _base_reads(read):
                        classified = _classify(base)
                        if classified is None:
                            continue
                        key, fields = classified
                        reference = NeedReference(
                            node_id=node_id, line=loc.start_line if loc else None, expr=base.expr,
                            wrappers=wrappers, file=timeline.entrypoint, from_abstained=abstained,
                            col=loc.column if loc else None)
                        read_row_of = fetched_by(base, ancestors, row_of)
                        owner.add(key, fields, reference, read_row_of)
                        collection = collections.get(read_row_of)
                        if (collection and collection["loop"] and base.var == collection["loop"].value_var
                                and base.source is not None
                                and base.source.query_node_id not in collection["queries"]):
                            collection["queries"].append(base.source.query_node_id)
        for index, child in enumerate(node.children):
            visit(child, f"{component_id}.{index}", owner, row_of, parent_list, abstained, ancestors)

    visit(component_tree.root, "0", None, None, None, False)

    result = []
    for component in components:
        needs = []
        collection = collections.get(component.component_id)
        if collection is not None:
            queries = collection["queries"]
            loop = collection["loop"]
            needs.append(Need(
                kind="collection", name=(loop.value_var or loop.node_id) if loop else "",
                references=(collection["reference"],), row_of=collection["row_of"],
                query_node_id=queries[0] if len(queries) == 1 else None,
                flag=None if len(queries) == 1 else "unresolved_read", item_component=collection["item"],
                from_abstained=collection["from_abstained"]))
        for entry in component.needs.values():
            references = tuple(entry.pop("references"))
            needs.append(Need(references=references, from_abstained=all(r.from_abstained for r in references),
                              **entry))
        result.append(ComponentNeeds(component.component_id, component.node.type, component.node.source_ids,
                                     tuple(needs)))
    return RequirementsResult(entrypoint=component_tree.entrypoint, components=tuple(result))


def need_counts(result: RequirementsResult) -> dict[str, int]:
    """How many needs of each kind (field, ambiguous, unresolved, context, collection)."""
    kinds = ("field", "ambiguous", "unresolved", "context", "collection")
    counts = {kind: 0 for kind in kinds}
    for component in result.components:
        for need in component.needs:
            counts[need.kind] += 1
    return counts


# ----------------------------------------------------------------------------- serialisation


def need_dict(need: Need) -> dict:
    """A need as JSON, with every reference (node, file, line, column, expression)."""
    return {
        "kind": need.kind,
        "name": need.name,
        "rowOf": need.row_of,
        "table": need.table,
        "column": need.column,
        "queryNodeId": need.query_node_id,
        "sourceKind": need.source_kind,
        "flag": need.flag,
        "itemComponent": need.item_component,
        "fromAbstained": need.from_abstained,
        "references": [{"nodeId": r.node_id, "file": r.file, "line": r.line, "col": r.col, "expr": r.expr,
                        "wrappers": list(r.wrappers), "fromAbstained": r.from_abstained}
                       for r in need.references],
    }


def requirements_to_dict(result: RequirementsResult) -> dict:
    """The whole Stage 3 result as the JSON written to stage3.json."""
    return {
        "stage": 3,
        "entrypoint": result.entrypoint,
        "counts": {"components": len(result.components), **need_counts(result)},
        "components": [
            {"componentId": c.component_id, "type": c.type, "sourceIds": list(c.source_ids),
             "needs": [need_dict(n) for n in c.needs]}
            for c in result.components
        ],
    }


def dumps(result: RequirementsResult) -> str:
    """Deterministic JSON: fixed key order, no timestamps or paths."""
    return json.dumps(requirements_to_dict(result), indent=2, ensure_ascii=False) + "\n"


def write_requirements(result: RequirementsResult, path: Path) -> None:
    """Write the Stage 3 result as JSON to `path` (LF line endings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(result))


def summary(result: RequirementsResult) -> str:
    """One summary line: components and needs by kind."""
    c = need_counts(result)
    total = c["field"] + c["ambiguous"] + c["unresolved"] + c["context"]
    return (f"[Stage 3] {len(result.components)} components, {total} needs: {c['field']} field, "
            f"{c['ambiguous']} ambiguous, {c['unresolved']} unresolved, {c['context']} context"
            f" · {c['collection']} collections")
