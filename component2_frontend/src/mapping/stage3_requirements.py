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
Timelines carry no source line per output node, so every reference's line
is None.
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
        self.component_id, self.node, self.row_of = component_id, node, row_of
        self.needs: dict[tuple, dict] = {}  # dedup key -> Need fields + references, in first-appearance order

    def add(self, key, fields, reference, row_of):
        key = (*key, row_of)
        entry = self.needs.setdefault(key, fields | {"row_of": row_of, "references": []})
        entry["references"].append(reference)


def recover_requirements(component_tree: ComponentTree, timeline: Timeline) -> RequirementsResult:
    outputs = {node.id: node for node in timeline.sequence}
    loops = {e.node_id: e for node in timeline.sequence for e in node.enclosed_by if e.role == "iteration"}
    components: list[_Component] = []
    collections: dict[str, dict] = {}  # List component id -> its collection need, filled as rows are read

    def visit(node: ComponentNode, component_id: str, owner: _Component | None, row_of: str | None,
              parent_list: str | None) -> None:
        if node.type in COMPONENT_TYPES:
            if node.type == "Item":
                row_of = parent_list
            owner = _Component(component_id, node, row_of)
            components.append(owner)
            if node.type == "List":
                loop = loops.get(node.source_ids[0]) if node.source_ids else None
                item = next((f"{component_id}.{i}" for i, c in enumerate(node.children) if c.type == "Item"), None)
                collections[component_id] = {
                    "loop": loop, "item": item, "row_of": row_of, "queries": [],
                    "reference": NeedReference(loop.node_id if loop else "", None, loop.iter_expr if loop else "")}
                parent_list = component_id
        if node.type == "Static" and node.rule == "R-L3":  # an inline map's body is the List's row
            row_of = parent_list
        if not node.children:
            for node_id in node.node_ids:
                for read in outputs[node_id].reads:
                    for base, wrappers in _base_reads(read):
                        classified = _classify(base)
                        if classified is None:
                            continue
                        key, fields = classified
                        owner.add(key, fields, NeedReference(node_id, None, base.expr, wrappers), row_of)
                        collection = collections.get(row_of)
                        if (collection and collection["loop"] and base.var == collection["loop"].value_var
                                and base.source is not None
                                and base.source.query_node_id not in collection["queries"]):
                            collection["queries"].append(base.source.query_node_id)
        for index, child in enumerate(node.children):
            visit(child, f"{component_id}.{index}", owner, row_of, parent_list)

    visit(component_tree.root, "0", None, None, None)

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
                flag=None if len(queries) == 1 else "unresolved_read", item_component=collection["item"]))
        for entry in component.needs.values():
            references = tuple(entry.pop("references"))
            needs.append(Need(references=references, **entry))
        result.append(ComponentNeeds(component.component_id, component.node.type, component.node.source_ids,
                                     tuple(needs)))
    return RequirementsResult(entrypoint=component_tree.entrypoint, components=tuple(result))


def need_counts(result: RequirementsResult) -> dict[str, int]:
    kinds = ("field", "ambiguous", "unresolved", "context", "collection")
    counts = {kind: 0 for kind in kinds}
    for component in result.components:
        for need in component.needs:
            counts[need.kind] += 1
    return counts


# ----------------------------------------------------------------------------- serialisation


def need_dict(need: Need) -> dict:
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
        "references": [{"nodeId": r.node_id, "line": r.line, "expr": r.expr, "wrappers": list(r.wrappers)}
                       for r in need.references],
    }


def requirements_to_dict(result: RequirementsResult) -> dict:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(result))


def summary(result: RequirementsResult) -> str:
    c = need_counts(result)
    total = c["field"] + c["ambiguous"] + c["unresolved"] + c["context"]
    return (f"[Stage 3] {len(result.components)} components, {total} needs: {c['field']} field, "
            f"{c['ambiguous']} ambiguous, {c['unresolved']} unresolved, {c['context']} context"
            f" · {c['collection']} collections")
