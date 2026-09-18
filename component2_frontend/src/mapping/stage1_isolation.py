"""Stage 1: presentation isolation.

Decides, for every node the timeline mentions, whether it stays in the
presentation layer:

    presentation                -> keep (status ok)
    undecided                   -> keep, status review, with the label's reason
    mixed                       -> keep, status review, reason "mixed_concern"
    business_logic, data_access -> exclude, with the label's reason
    no label                    -> keep, status review, reason "unlabelled"

"Every node the timeline mentions" means the output statements plus the
if-branches and loops that enclose them and the query/fetch calls their reads
come from. Only the label's `concern` drives the decision; `basis` and
`ruleId` are carried through for reporting.

A node's status comes from its own label only. An excluded if or loop still
appears, with its label, in the enclosure chain of every kept node it wraps:
Stage 2 needs to know whether a wrapping guard is an authorization check or a
display conditional.
"""

import json
from dataclasses import replace
from pathlib import Path

from src.model import (
    Concern,
    Enclosure,
    ExcludedNode,
    KeptNode,
    Label,
    OutputNode,
    PresentationResult,
    Read,
    StageCounts,
    Status,
    Timeline,
)

CALL_KIND = "Expr_FuncCall"  # mysqli_query(...) / mysqli_fetch_*(...) calls, named as nikic/PHP-Parser does


def _classify(label: Label | None) -> tuple[Status | None, str]:
    """(status, reason) for a node; status None means exclude."""
    if label is None:
        return Status.REVIEW, "unlabelled"
    if label.concern is Concern.PRESENTATION:
        return Status.OK, label.reason
    if label.concern is Concern.UNDECIDED:
        return Status.REVIEW, label.reason
    if label.concern is Concern.MIXED:
        return Status.REVIEW, "mixed_concern"
    return None, label.reason  # business_logic, data_access


def _flat_reads(reads: tuple[Read, ...]):
    for read in reads:
        yield read
        yield from _flat_reads(read.derived_from)


def _nodes(timeline: Timeline):
    """Every node the timeline mentions, once each, in order of first appearance.

    Yields (node_id, node_type, kind, enclosing chain, OutputNode or None).
    Output nodes come out in sequence order; an enclosing if/loop comes out
    just before the first output it wraps, with the chain outside it.
    """
    seen = set()

    def first(node_id):
        if node_id in seen:
            return False
        seen.add(node_id)
        return True

    for node in timeline.sequence:
        for depth, enc in enumerate(node.enclosed_by):
            if first(enc.node_id):
                yield enc.node_id, "enclosure", enc.kind, node.enclosed_by[:depth], None
        for read in _flat_reads(node.reads):
            if read.source:
                if first(read.source.query_node_id):
                    yield read.source.query_node_id, "query", CALL_KIND, (), None
                if first(read.source.fetch_node_id):
                    yield read.source.fetch_node_id, "fetch", CALL_KIND, (), None
        if first(node.id):
            yield node.id, "output", node.kind, node.enclosed_by, node
    for query_id in sorted(timeline.queries):  # queries no read refers to
        if first(query_id):
            yield query_id, "query", CALL_KIND, (), None


def isolate_presentation(timeline: Timeline, labels: dict[str, Label]) -> PresentationResult:
    def labelled(chain: tuple[Enclosure, ...]) -> tuple[Enclosure, ...]:
        return tuple(replace(enc, label=labels.get(enc.node_id)) for enc in chain)

    kept, excluded = [], []
    for node_id, node_type, kind, chain, output in _nodes(timeline):
        label = labels.get(node_id)
        status, reason = _classify(label)
        if status is None:
            excluded.append(ExcludedNode(node_id=node_id, node_type=node_type, kind=kind, label=label, reason=reason))
            continue
        chain = labelled(chain)
        kept.append(KeptNode(
            node_id=node_id,
            node_type=node_type,
            kind=kind,
            status=status,
            reason=reason,
            label=label,
            enclosed_by=chain,
            output=replace(output, enclosed_by=chain) if output else None,
        ))

    review = sum(k.status is Status.REVIEW for k in kept)
    counts = StageCounts(total=len(kept) + len(excluded), kept=len(kept) - review, review=review,
                         excluded=len(excluded))
    return PresentationResult(entrypoint=timeline.entrypoint, kept=tuple(kept), excluded=tuple(excluded),
                              counts=counts)


# ----------------------------------------------------------------------------- serialisation


def _label_dict(label: Label | None) -> dict | None:
    if label is None:
        return None
    return {"concern": label.concern.value, "basis": label.basis, "ruleId": label.rule_id, "reason": label.reason}


def _enclosure_dict(enc: Enclosure) -> dict:
    if enc.role == "iteration":
        d = {"nodeId": enc.node_id, "kind": enc.kind, "role": enc.role, "iterExpr": enc.iter_expr,
             "iterSourceKind": enc.iter_source_kind, "valueVar": enc.value_var, "keyVar": enc.key_var}
    else:
        d = {"nodeId": enc.node_id, "kind": enc.kind, "role": enc.role, "branch": enc.branch,
             "condNodeId": enc.cond_node_id}
    return d | {"label": _label_dict(enc.label)}


def _read_dict(read: Read) -> dict:
    d = {
        "expr": read.expr,
        "var": read.var,
        "path": None if read.path is None else list(read.path),
        "sourceKind": read.source_kind,
        "confidence": read.confidence,
        "source": None if read.source is None else {
            "fetchNodeId": read.source.fetch_node_id, "queryNodeId": read.source.query_node_id,
            "table": read.source.table, "column": read.source.column},
    }
    if read.derived_from:
        d["derivedFrom"] = [_read_dict(r) for r in read.derived_from]
    return d


def _output_dict(output: OutputNode) -> dict:
    if output.raw is not None:
        return {"raw": output.raw}
    return {"reads": [_read_dict(r) for r in output.reads]}


def result_to_dict(result: PresentationResult) -> dict:
    return {
        "stage": 1,
        "entrypoint": result.entrypoint,
        "counts": {"total": result.counts.total, "kept": result.counts.kept, "review": result.counts.review,
                   "excluded": result.counts.excluded},
        "kept": [
            {"nodeId": k.node_id, "nodeType": k.node_type, "kind": k.kind, "status": k.status.value,
             "reason": k.reason, "label": _label_dict(k.label),
             "enclosedBy": [_enclosure_dict(e) for e in k.enclosed_by]}
            | ({"output": _output_dict(k.output)} if k.output else {})
            for k in result.kept
        ],
        "excluded": [
            {"nodeId": x.node_id, "nodeType": x.node_type, "kind": x.kind, "reason": x.reason,
             "label": _label_dict(x.label)}
            for x in result.excluded
        ],
    }


def dumps(result: PresentationResult) -> str:
    """Deterministic JSON: fixed key order, no timestamps or paths."""
    return json.dumps(result_to_dict(result), indent=2, ensure_ascii=False) + "\n"


def write_result(result: PresentationResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(result))


def summary(result: PresentationResult) -> str:
    c = result.counts
    return f"[Stage 1] {c.total} nodes -> {c.kept} kept, {c.review} review, {c.excluded} excluded"
