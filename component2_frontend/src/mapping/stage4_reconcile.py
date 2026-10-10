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
Matched needs carry type "unverified": the source column types come from
C3's schema, which is not available yet. Contract properties no need uses
are listed as unused (information only).
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

    # Which array of the response holds each List's rows: decided only when there is no choice to make.
    lists = [c for c in requirements.components if c.type == "List"]
    nested = {c.component_id for c in lists for n in c.needs if n.kind == "collection" and n.row_of is not None}
    row_lists = []
    for component in requirements.components:
        for need in component.needs:
            if need.kind == "field" and need.row_of is not None and need.row_of not in row_lists:
                row_lists.append(need.row_of)
    arrays = {}
    if shape is not None and len(shape.arrays) == 1 and len(row_lists) == 1 and row_lists[0] not in nested:
        arrays[row_lists[0]] = shape.arrays[0]

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

    collections = tuple((c.component_id, arrays[c.component_id][0] if c.component_id in arrays else None)
                        for c in lists)
    counts = ReconciliationCounts(
        needs=len(results), matched=sum(r.result == "matched" for r in results),
        missing=sum(r.result == "missing" for r in results), excluded=sum(r.result == "excluded" for r in results),
        cannot_reconcile=sum(r.result == "cannot_reconcile" for r in results), unused=len(unused))
    return ReconciliationReport(entrypoint=requirements.entrypoint, endpoint=ref, results=tuple(results),
                                collections=collections, unused=tuple(unused), counts=counts)


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
                   "unused": report.counts.unused},
        "results": [
            {"componentId": r.component_id, "componentType": r.component_type, "result": r.result,
             "flagged": r.result in FLAGGED, "reason": r.reason, "schemaProperty": r.schema_property,
             "type": r.type, "hint": r.hint, "need": need_dict(r.need)}
            for r in report.results
        ],
        "collections": [{"componentId": c, "arrayProperty": a} for c, a in report.collections],
        "unused": list(report.unused),
    }


def dumps(report: ReconciliationReport) -> str:
    """Deterministic JSON: fixed key order, no timestamps or paths."""
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"


def write_report(report: ReconciliationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(report))


def summary(report: ReconciliationReport) -> str:
    c = report.counts
    return (f"[Stage 4] {c.needs} needs: {c.matched} matched, {c.missing} missing, {c.excluded} excluded, "
            f"{c.cannot_reconcile} cannot reconcile · {c.unused} unused")


def flagged(report: ReconciliationReport) -> list[NeedResult]:
    return [r for r in report.results if r.result in FLAGGED]
