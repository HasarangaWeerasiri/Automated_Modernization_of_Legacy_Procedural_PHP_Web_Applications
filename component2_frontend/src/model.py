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
    """A construct wrapping an output node: a loop, an if-branch, a switch case or a try/catch."""

    node_id: str
    kind: str  # nikic node name, e.g. Stmt_If | Stmt_While | Stmt_Foreach
    role: str  # "iteration" | "branch" | "switch_case" | "try_catch"
    # role "iteration"
    iter_expr: str | None = None
    iter_source_kind: str | None = None
    value_var: str | None = None
    key_var: str | None = None
    # role "branch"
    branch: str | None = None  # then | elseif | else
    cond_node_id: str | None = None
    # roles "switch_case" / "try_catch": their fields are not specified yet, so whatever the
    # timeline gives is carried here as (key, value) pairs sorted by key. Opaque: never used in a decision.
    extra: tuple[tuple[str, object], ...] = ()
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
    # By query node id. Optional extension: empty when the timeline has no queries map.
    # Raw SQL for reporting only; no stage may need it (reads carry source.query_node_id).
    queries: dict[str, Query]
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


# ----------------------------------------------------------------------------- Stage 2 config and result


@dataclass(frozen=True)
class BoundaryConfig:
    """Thresholds for docs/php-analysis/boundary-rules.md, read from config/boundary_rules.json."""

    small_if_max_elements: int  # R-I4: an if with at most this many elements stays inline
    fallback_component_min_elements: int  # R-I5 ("N"): an if with this many elements or more is its own component
    table_section_tags: tuple[str, ...]  # R-L2a walks past these
    void_tags: tuple[str, ...]  # elements that never have a closing tag


@dataclass(frozen=True)
class ListContainer:
    has_wrapper: bool  # False: the List renders a Fragment (R-L2b)
    tag: str | None  # the container element, e.g. "table"
    element_id: str | None  # its id attribute, if it has a static one
    opened_in: str | None  # the output node whose raw opens it


@dataclass(frozen=True)
class ComponentNode:
    """One node of the component tree. A node with no children is a leaf."""

    type: str  # Page | Layout | List | Item | Empty | InlineConditional | ConditionalComponent
    #            | AttributeExpression | Static | Abstain
    rule: str | None  # the boundary rule that made this node, e.g. "R-L1"; None when no rule was involved
    rules: tuple[str, ...]  # every boundary rule that fired for this node, in order
    status: str  # ok | review | abstain
    reason: str
    node_ids: tuple[str, ...]  # the timeline output nodes it covers, in sequence order
    source_ids: tuple[str, ...] = ()  # the loop / if nodes it was built from
    children: tuple["ComponentNode", ...] = ()
    container: ListContainer | None = None  # List only
    loop_concern: str | None = None  # List only: the concern Component 1 gave its loop. Information, not a status
    root_tags: tuple[str, ...] = ()  # Item only: the item's root elements
    elements: int | None = None  # conditionals only: largest number of opening tags in one branch
    flags: tuple[str, ...] = ()  # e.g. unmatched_close, tag_crosses_branch


@dataclass(frozen=True)
class TreeCounts:
    components: int  # Page, Layout, List, Item, Empty and ConditionalComponent nodes
    abstain: int  # nodes with status abstain
    review: int  # nodes with status review


@dataclass(frozen=True)
class ComponentTree:
    entrypoint: str
    root: ComponentNode
    counts: TreeCounts


# ----------------------------------------------------------------------------- contract and endpoint map


@dataclass(frozen=True)
class ResponseShape:
    """The fields an endpoint's JSON response offers, with $refs resolved."""

    properties: tuple[str, ...]  # top-level property names ("" when the response itself is an array)
    arrays: tuple[tuple[str, tuple[str, ...]], ...]  # (array property, its item schema's property names)


@dataclass(frozen=True)
class Endpoint:
    method: str  # lower case, e.g. "get"
    path: str
    status: str  # response status code the shape was read from, e.g. "200"
    media_type: str
    response: ResponseShape | None  # None when that response has no object or array schema


@dataclass(frozen=True)
class Contract:
    title: str
    endpoints: dict[tuple[str, str, str, str], Endpoint]  # by (method, path, status, media type)


@dataclass(frozen=True)
class EndpointRef:
    """One entry of the endpoint map: which response serves a timeline's entrypoint."""

    method: str
    path: str
    status: str
    media_type: str


# ----------------------------------------------------------------------------- Stage 3 result


@dataclass(frozen=True)
class NeedReference:
    node_id: str  # the output node whose read gave the need
    line: int | None  # its source line; None: schema 1.0 timelines carry no line per output node
    expr: str  # the read as written, e.g. "$row['contact']"
    wrappers: tuple[str, ...] = ()  # functions applied around it, outermost first, e.g. ("h",)


@dataclass(frozen=True)
class Need:
    """One piece of data a component needs (docs/reconciliation-spec.md section 1)."""

    kind: str  # field | ambiguous | unresolved | context | collection
    name: str  # what it is called: the column, the array key, the expression, or the collection's loop
    references: tuple[NeedReference, ...]
    row_of: str | None = None  # id of the List whose rows this need belongs to; None outside any row
    table: str | None = None  # field only
    column: str | None = None  # field only
    query_node_id: str | None = None  # field, ambiguous, collection
    source_kind: str | None = None  # context only: session | request | server
    flag: str | None = None  # ambiguous_needs_schema | unresolved_read
    item_component: str | None = None  # collection only: the List's Item component id


@dataclass(frozen=True)
class ComponentNeeds:
    component_id: str  # position in the Stage 2 tree, e.g. "0.1.0"
    type: str
    source_ids: tuple[str, ...]
    needs: tuple[Need, ...]


@dataclass(frozen=True)
class RequirementsResult:
    entrypoint: str
    components: tuple[ComponentNeeds, ...]  # in tree order


# ----------------------------------------------------------------------------- Stage 4 result


@dataclass(frozen=True)
class NeedResult:
    component_id: str
    component_type: str
    need: Need
    result: str  # matched | missing | excluded | cannot_reconcile
    reason: str  # e.g. missing_in_contract, session, no_endpoint
    schema_property: str | None = None  # the property matched, e.g. "appointments[].doctor"
    type: str | None = None  # matched only: "unverified" until C3's schema is available
    hint: str | None = None  # missing only: a property equal to the column except for case


@dataclass(frozen=True)
class ReconciliationCounts:
    needs: int
    matched: int
    missing: int
    excluded: int
    cannot_reconcile: int
    unused: int


@dataclass(frozen=True)
class ReconciliationReport:
    entrypoint: str
    endpoint: EndpointRef | None
    results: tuple[NeedResult, ...]  # in component order, then need order
    collections: tuple[tuple[str, str | None], ...]  # (List component id, array property it reads, or None)
    unused: tuple[str, ...]  # contract properties no component needs
    counts: ReconciliationCounts
