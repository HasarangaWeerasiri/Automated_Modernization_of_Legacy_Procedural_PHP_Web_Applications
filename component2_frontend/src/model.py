"""Internal types for Component 2. Plain data: no parsing, no logic.

src/mapping/loader.py is the only module that knows the backend member's JSON
format; it converts it into these types, and everything downstream works on
these types only.
"""

from dataclasses import dataclass
from enum import Enum


class Concern(str, Enum):
    PRESENTATION = "presentation"
    BUSINESS_LOGIC = "business_logic"
    DATA_ACCESS = "data_access"
    MIXED = "mixed"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class Label:
    """Component 1's concern classification of one node. Decisions use `concern` only."""

    concern: Concern
    basis: str  # how Component 1 decided ("rule" / "abstained"): reporting only
    rule_id: str  # reporting only; never used in a decision
    reason: str


@dataclass(frozen=True)
class ReadSource:
    fetch_node_id: str
    query_node_id: str
    table: str | None  # None when the query could not be mapped (e.g. SELECT * over a join)
    column: str | None


@dataclass(frozen=True)
class Read:
    """One value an output statement prints."""

    expr: str
    var: str | None  # None for literal and computed reads
    path: tuple[str, ...] | None  # array keys accessed on `var`; None for literal and computed reads
    source_kind: str  # db_row_field | session | request | server | literal | computed | unresolved
    confidence: str  # resolved | ambiguous | unresolved
    source: ReadSource | None  # only for db_row_field reads
    derived_from: tuple["Read", ...] = ()  # only for computed reads


@dataclass(frozen=True)
class Enclosure:
    """An if-branch or loop wrapping an output node."""

    node_id: str
    kind: str  # Stmt_If | Stmt_While | Stmt_Foreach
    role: str  # "iteration" or "branch"
    # role "iteration"
    iter_expr: str | None = None
    iter_source_kind: str | None = None
    value_var: str | None = None
    key_var: str | None = None
    # role "branch"
    branch: str | None = None  # then | elseif | else
    cond_node_id: str | None = None
    # The enclosing node's own label. The loader leaves it None; Stage 1 attaches it.
    label: Label | None = None


@dataclass(frozen=True)
class OutputNode:
    """One output statement (Stmt_InlineHTML / Stmt_Echo / Expr_Print)."""

    id: str
    kind: str
    raw: str | None  # Stmt_InlineHTML only, byte-exact
    reads: tuple[Read, ...]  # Stmt_Echo / Expr_Print only
    enclosed_by: tuple[Enclosure, ...]  # outermost first


@dataclass(frozen=True)
class Query:
    node_id: str
    line: int
    sql: str  # source text of the SQL argument, byte-exact
    tables: tuple[str, ...]
    columns: tuple[str, ...] | None  # None when unknown (SELECT * the analysis could not expand)
    result_var: str


@dataclass(frozen=True)
class Timeline:
    """Every output statement of one PHP entrypoint, in execution order."""

    entrypoint: str
    schema_version: str
    sequence: tuple[OutputNode, ...]
    queries: dict[str, Query]  # by query node id
    provenance: dict  # opaque metadata about how the mock was made; never used in decisions


# ----------------------------------------------------------------------------- Stage 1 result


class Status(str, Enum):
    OK = "ok"
    REVIEW = "review"


@dataclass(frozen=True)
class KeptNode:
    node_id: str
    node_type: str  # "output", "enclosure", "query" or "fetch"
    kind: str
    status: Status
    reason: str
    label: Label | None  # None when the node has no label (status review, reason "unlabelled")
    enclosed_by: tuple[Enclosure, ...]  # outermost first, each carrying its own label
    output: OutputNode | None  # the statement itself, for node_type "output"


@dataclass(frozen=True)
class ExcludedNode:
    node_id: str
    node_type: str
    kind: str
    label: Label
    reason: str


@dataclass(frozen=True)
class StageCounts:
    """total == kept + review + excluded. `kept` counts status-ok nodes; `review` counts
    kept nodes flagged for review (they are in PresentationResult.kept too)."""

    total: int
    kept: int
    review: int
    excluded: int


@dataclass(frozen=True)
class PresentationResult:
    entrypoint: str
    kept: tuple[KeptNode, ...]  # in order of first appearance; output nodes in sequence order
    excluded: tuple[ExcludedNode, ...]
    counts: StageCounts
