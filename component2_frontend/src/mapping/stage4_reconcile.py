"""Stage 4: contract reconciliation (docs/reconciliation-spec.md section 2).

Checks the needs Stage 3 recovered against the response Component 1's
contract offers for the page, as named by the endpoint map.

  context need                 -> excluded, reason = its sourceKind
  ambiguous / unresolved need  -> cannot_reconcile, reason = its flag
  field need, page unmapped    -> cannot_reconcile, reason no_endpoint
  field need                   -> matched when a property has exactly the
                                  column's name, else missing
                                  (missing_in_contract). A property equal
                                  except for case is given as a hint only.

A field need in a List's rows is matched against the item schema of the
response's array; any other field need against the response's top level.
Each List's collection need gets its own result: mapped (to the response's
one array, when the page has exactly one List and the response one array) or
cannot_reconcile. Collections are counted apart from field needs.

Matched needs carry type "unverified": the source column types come from
C3's schema, which is not available yet. Contract properties no output read
references are listed as unused: information only, and a lower bound (see
UNUSED_NOTE). A need from abstained content keeps its result, marked
fromAbstained.
"""

import json
from pathlib import Path

from src.model import (
    Contract,
    EndpointRef,
    Need,
    NeedResult,
    ReconciliationCounts,
    ReconciliationReport,
    RequirementsResult,
)
from src.mapping.stage3_requirements import need_dict

FLAGGED = ("missing", "cannot_reconcile")
UNUSED_NOTE = ("unused = not referenced by output reads; condition reads are not visible yet (Member 01 Q2). "
               "Not proof the property is unused: never use this list to remove fields from the contract.")


def _match(column: str, properties: tuple[str, ...], prefix: str):
    """(result, reason, matched property, hint): exact name only, never case folding."""
    if column in properties:
        return "matched", "matched", prefix + column, None
    near = [p for p in properties if p.lower() == column.lower()]
    return "missing", "missing_in_contract", None, (near[0] if near else None)


def reconcile(requirements: RequirementsResult, contract: Contract,
              endpoint_map: dict[str, EndpointRef]) -> ReconciliationReport:
    ref = endpoint_map.get(requirements.entrypoint)
    endpoint = contract.endpoints.get((ref.method, ref.path, ref.status, ref.media_type)) if ref else None
    shape = endpoint.response if endpoint else None

    # Which array of the response holds each List's rows: decided only when there is no choice to make,
    # i.e. the page has exactly one (not nested) List and the response exactly one array.
    lists = [c for c in requirements.components if c.type == "List"]
    nested = {c.component_id for c in lists for n in c.needs if n.kind == "collection" and n.row_of is not None}
    top_lists = [c.component_id for c in lists if c.component_id not in nested]
    arrays = {}
    if shape is not None and len(shape.arrays) == 1 and len(top_lists) == 1:
        arrays[top_lists[0]] = shape.arrays[0]

    collection_results = []
    for component in lists:
        for need in component.needs:
            if need.kind == "collection":
                result, reason, prop = _map_collection(component.component_id, ref, endpoint, shape, arrays, nested)
                collection_results.append(NeedResult(
                    component_id=component.component_id, component_type=component.type, need=need, result=result,
                    reason=reason, schema_property=prop))

    results, used_top, used_items = [], set(), {}
    for component in requirements.components:
        for need in component.needs:
            if need.kind == "collection":
                continue
            outcome = _decide(need, ref, endpoint, shape, arrays, nested)
            result, reason, prop, hint = outcome
            if result == "matched":
                if need.row_of is None:
                    used_top.add(need.column)
                else:
                    used_items.setdefault(arrays[need.row_of][0], set()).add(need.column)
            results.append(NeedResult(
                component_id=component.component_id, component_type=component.type, need=need, result=result,
                reason=reason, schema_property=prop, type="unverified" if result == "matched" else None, hint=hint))

    unused = []
    if shape is not None:
        mapped = {array for array, _ in arrays.values()}
        unused += [p for p in shape.properties if p not in used_top and p not in mapped]
        for array, items in shape.arrays:
            if array in mapped:
                unused += [f"{array}[].{p}" for p in items if p not in used_items.get(array, set())]

    counts = ReconciliationCounts(
        needs=len(results), matched=sum(r.result == "matched" for r in results),
        missing=sum(r.result == "missing" for r in results), excluded=sum(r.result == "excluded" for r in results),
        cannot_reconcile=sum(r.result == "cannot_reconcile" for r in results), unused=len(unused),
        collections_mapped=sum(r.result == "mapped" for r in collection_results),
        collections_cannot_reconcile=sum(r.result == "cannot_reconcile" for r in collection_results))
    return ReconciliationReport(entrypoint=requirements.entrypoint, endpoint=ref, results=tuple(results),
                                collection_results=tuple(collection_results), unused=tuple(unused), counts=counts)


def _map_collection(list_id, ref, endpoint, shape, arrays, nested):
    """(result, reason, array property) for one List's collection need."""
    if ref is None:
        return "cannot_reconcile", "no_endpoint", None
    if endpoint is None or shape is None:
        return "cannot_reconcile", "endpoint_not_in_contract", None
    if list_id in nested:
        return "cannot_reconcile", "nested_list", None
    if list_id not in arrays:
        return "cannot_reconcile", "list_not_mapped_to_array", None
    return "mapped", "mapped", arrays[list_id][0]


def _decide(need: Need, ref, endpoint, shape, arrays, nested):
    if need.kind == "context":
        return "excluded", need.source_kind, None, None
    if need.kind in ("ambiguous", "unresolved"):
        return "cannot_reconcile", need.flag, None, None
    # a field need
    if ref is None:
        return "cannot_reconcile", "no_endpoint", None, None
    if endpoint is None or shape is None:
        return "cannot_reconcile", "endpoint_not_in_contract", None, None
    if need.row_of is None:
        return _match(need.column, shape.properties, "")
    if need.row_of in nested:
        return "cannot_reconcile", "nested_list", None, None
    if need.row_of not in arrays:
        return "cannot_reconcile", "list_not_mapped_to_array", None, None
    array, items = arrays[need.row_of]
    return _match(need.column, items, f"{array}[].")


# ----------------------------------------------------------------------------- serialisation


def report_to_dict(report: ReconciliationReport) -> dict:
    ref = report.endpoint
    return {
        "stage": 4,
        "entrypoint": report.entrypoint,
        "endpoint": None if ref is None else {"method": ref.method, "path": ref.path, "status": ref.status,
                                              "mediaType": ref.media_type},
        "counts": {"needs": report.counts.needs, "matched": report.counts.matched, "missing": report.counts.missing,
                   "excluded": report.counts.excluded, "cannotReconcile": report.counts.cannot_reconcile,
                   "unused": report.counts.unused, "collectionsMapped": report.counts.collections_mapped,
                   "collectionsCannotReconcile": report.counts.collections_cannot_reconcile},
        "results": [_result_dict(r) for r in report.results],
        "collections": [_result_dict(r) for r in report.collection_results],
        "unused": list(report.unused),
        "unusedNote": UNUSED_NOTE,
    }


def _result_dict(r: NeedResult) -> dict:
    return {"componentId": r.component_id, "componentType": r.component_type, "result": r.result,
            "flagged": r.result in FLAGGED, "reason": r.reason, "fromAbstained": r.need.from_abstained,
            "schemaProperty": r.schema_property, "type": r.type, "hint": r.hint, "need": need_dict(r.need)}


def dumps(report: ReconciliationReport) -> str:
    """Deterministic JSON: fixed key order, no timestamps or paths."""
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"


def write_report(report: ReconciliationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(report))


def summary(report: ReconciliationReport) -> str:
    c = report.counts
    lists = c.collections_mapped + c.collections_cannot_reconcile
    if c.collections_cannot_reconcile:
        collections = f"{c.collections_mapped} of {lists} collections mapped"
    else:
        collections = f"{c.collections_mapped} collection{'' if c.collections_mapped == 1 else 's'} mapped"
    return (f"[Stage 4] {c.needs} needs: {c.matched} matched, {c.missing} missing, {c.excluded} excluded, "
            f"{c.cannot_reconcile} cannot reconcile · {collections} · {c.unused} unused")


def flagged(report: ReconciliationReport) -> list[NeedResult]:
    return [r for r in report.results if r.result in FLAGGED]
