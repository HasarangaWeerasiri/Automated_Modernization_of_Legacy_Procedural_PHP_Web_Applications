"""
Entry point for Component 2 (Frontend Migration).

Pipeline: legacy-PHP output timeline + OpenAPI contract -> Next.js/TSX components.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.pretty import Pretty
from rich.panel import Panel

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="component2-frontend",
        description="Generate Next.js TSX components from a legacy PHP output timeline and an OpenAPI contract.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help=(
            "Path to the output timeline JSON produced by the backend analysis "
            "(see mocks/timeline_*.json). The legacy node-list fixture "
            "(mocks/sample_ast.json) is still accepted and detected automatically."
        ),
    )
    parser.add_argument(
        "--contract",
        required=True,
        type=Path,
        help="Path to the OpenAPI contract JSON produced by Component 1 (see mocks/sample_contract.json).",
    )
    parser.add_argument(
        "--output",
        default=Path("output/generated"),
        type=Path,
        help="Directory to write generated Next.js components into (default: output/generated).",
    )
    return parser.parse_args()


SCHEMA_VERSION = "1.0"
TIMELINE_KEYS = ("entrypoint", "schemaVersion", "sequence", "queries")
OUTPUT_KINDS = ("Stmt_InlineHTML", "Stmt_Echo", "Expr_Print")
LOOP_KINDS = ("Stmt_While", "Stmt_Foreach")
BRANCHES = ("then", "elseif", "else")
SOURCE_KINDS = ("db_row_field", "session", "request", "server", "literal", "computed", "unresolved")
CONFIDENCES = ("resolved", "ambiguous", "unresolved")
CONCERNS = ("presentation", "business_logic", "data_access", "mixed", "undecided")
NODE_ID = re.compile(r"^[A-Za-z0-9_]+#\d+$")

LOOP_KEYS = {"nodeId", "kind", "role", "iterExpr", "iterSourceKind", "valueVar", "keyVar"}
BRANCH_KEYS = {"nodeId", "kind", "role", "branch", "condNodeId"}
READ_KEYS = {"expr", "var", "path", "sourceKind", "confidence", "source"}
SOURCE_KEYS = {"fetchNodeId", "queryNodeId", "table", "column"}
QUERY_KEYS = {"line", "sql", "tables", "columns", "resultVar"}
LABEL_KEYS = {"concern", "basis", "ruleId", "reason"}


def _check_node_id(value, where: str) -> None:
    if not isinstance(value, str) or not NODE_ID.match(value):
        raise ValueError(f"{where}: {value!r} is not a '<fileId>#<ordinal>' node id")


def _check_read(read: dict, queries: dict, where: str) -> None:
    keys = READ_KEYS | ({"derivedFrom"} if read.get("sourceKind") == "computed" else set())
    if set(read) != keys:
        raise ValueError(f"{where}: read keys {sorted(read)} != {sorted(keys)}")
    where = f"{where} read {read['expr']!r}"
    kind, confidence = read["sourceKind"], read["confidence"]
    if kind not in SOURCE_KINDS:
        raise ValueError(f"{where}: sourceKind {kind!r} not in {SOURCE_KINDS}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"{where}: confidence {confidence!r} not in {CONFIDENCES}")
    if kind in ("literal", "computed"):
        if read["var"] is not None or read["path"] is not None:
            raise ValueError(f"{where}: {kind} reads have null var and path")
    elif not isinstance(read["path"], list):
        raise ValueError(f"{where}: path must be a list")
    if kind == "unresolved" and confidence != "unresolved":
        raise ValueError(f"{where}: sourceKind 'unresolved' needs confidence 'unresolved'")

    source = read["source"]
    if kind == "db_row_field":
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise ValueError(f"{where}: db_row_field source needs keys {sorted(SOURCE_KEYS)}")
        _check_node_id(source["fetchNodeId"], where)
        if source["queryNodeId"] not in queries:
            raise ValueError(f"{where}: queryNodeId {source['queryNodeId']!r} is not in queries")
        null_target = source["table"] is None and source["column"] is None
        if confidence == "ambiguous" and not null_target:
            raise ValueError(f"{where}: ambiguous db reads have null table and column")
        if confidence == "resolved" and (source["table"] is None or source["column"] is None):
            raise ValueError(f"{where}: resolved db reads name their table and column")
    elif source is not None:
        raise ValueError(f"{where}: only db_row_field reads carry a source")

    if kind == "computed":
        if not read["derivedFrom"]:
            raise ValueError(f"{where}: computed reads list what they derive from")
        for inner in read["derivedFrom"]:
            _check_read(inner, queries, f"{where} derivedFrom")


def load_timeline(path: Path) -> dict:
    """Load an output timeline and check it against schema 1.0.

    Raises ValueError naming the first offending key, so a malformed mock
    or a format drift in the real analysis output fails loudly here rather
    than deep inside a pipeline step.
    """
    # newline="" keeps "\r\n" inside JSON strings untouched; Stmt_InlineHTML
    # raw values must stay byte-exact.
    with path.open("r", encoding="utf-8", newline="") as f:
        timeline = json.load(f)

    if "guards" in timeline or "loops" in timeline:
        raise ValueError(f"{path}: schema 0.1 timeline (guards/loops maps); migrate it to schema {SCHEMA_VERSION}")
    missing = [k for k in TIMELINE_KEYS if k not in timeline]
    if missing:
        raise ValueError(f"{path}: timeline is missing top-level keys {missing}")
    if timeline["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"{path}: schemaVersion {timeline['schemaVersion']!r}, expected {SCHEMA_VERSION!r}")

    queries = timeline["queries"]
    for qid, query in queries.items():
        _check_node_id(qid, f"{path}: queries")
        if set(query) != QUERY_KEYS:
            raise ValueError(f"{path}: query {qid} keys {sorted(query)} != {sorted(QUERY_KEYS)}")

    seen, loops_seen = set(), {}
    for entry in timeline["sequence"]:
        eid = entry.get("id", "<no id>")
        where = f"{path}: {eid}"
        _check_node_id(eid, where)
        if eid in seen:
            raise ValueError(f"{where}: duplicate sequence id")
        seen.add(eid)
        if entry.get("kind") not in OUTPUT_KINDS:
            raise ValueError(f"{where}: kind {entry.get('kind')!r}, expected one of {OUTPUT_KINDS}")
        payload = "raw" if entry["kind"] == "Stmt_InlineHTML" else "reads"
        if set(entry) != {"id", "kind", "enclosedBy", payload}:
            raise ValueError(f"{where}: {entry['kind']} keys {sorted(entry)}; expected id/kind/enclosedBy/{payload}")
        for read in entry.get("reads", []):
            _check_read(read, queries, where)

        for enc in entry["enclosedBy"]:
            _check_node_id(enc.get("nodeId"), where)
            if enc.get("role") == "iteration":
                if set(enc) != LOOP_KEYS or enc["kind"] not in LOOP_KINDS or enc["iterSourceKind"] not in SOURCE_KINDS:
                    raise ValueError(f"{where}: malformed loop {enc}")
                # The loop object is repeated on every entry it encloses; copies must agree.
                if loops_seen.setdefault(enc["nodeId"], enc) != enc:
                    raise ValueError(f"{where}: loop {enc['nodeId']} differs from its earlier copy")
            elif enc.get("role") == "branch":
                if set(enc) != BRANCH_KEYS or enc["kind"] != "Stmt_If" or enc["branch"] not in BRANCHES:
                    raise ValueError(f"{where}: malformed branch {enc}")
                _check_node_id(enc["condNodeId"], where)
            else:
                raise ValueError(f"{where}: enclosedBy role {enc.get('role')!r}, expected 'iteration' or 'branch'")

    return timeline


def load_labels(path: Path) -> dict:
    """Load a concern-labels file (labels_*.json) and check it against schema 1.0."""
    with path.open("r", encoding="utf-8") as f:
        labels = json.load(f)
    if set(labels) != {"schemaVersion", "labels"} or labels["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"{path}: expected {{schemaVersion: {SCHEMA_VERSION!r}, labels}}")
    for node_id, lab in labels["labels"].items():
        _check_node_id(node_id, f"{path}: labels")
        if set(lab) != LABEL_KEYS:
            raise ValueError(f"{path}: {node_id} label keys {sorted(lab)} != {sorted(LABEL_KEYS)}")
        if lab["concern"] not in CONCERNS:
            raise ValueError(f"{path}: {node_id} concern {lab['concern']!r} not in {CONCERNS}")
    return labels


def load_legacy_ast(path: Path) -> dict:
    """Load the pre-timeline node-list fixture (mocks/sample_ast.json).

    Kept so the old fixture still loads; new work should use load_timeline.
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_input(path: Path) -> tuple[str, dict]:
    """Detect which input format `path` holds and load it with the matching loader."""
    with path.open("r", encoding="utf-8") as f:
        top_level = json.load(f)
    if "sequence" in top_level:
        return "timeline", load_timeline(path)
    if "nodes" in top_level:
        return "legacy_ast", load_legacy_ast(path)
    raise ValueError(f"{path}: neither a timeline ('sequence') nor a legacy AST ('nodes')")


def load_contract(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        console.print(f"[red]Timeline input not found:[/red] {args.input}")
        sys.exit(1)
    if not args.contract.exists():
        console.print(f"[red]Contract input not found:[/red] {args.contract}")
        sys.exit(1)

    try:
        input_format, timeline = load_input(args.input)
    except ValueError as e:
        console.print(f"[red]Invalid input:[/red] {e}")
        sys.exit(1)
    contract = load_contract(args.contract)

    console.print(Panel.fit(f"Loaded {input_format}: {args.input}", style="green"))
    console.print(Pretty(timeline, max_length=10))
    if input_format == "legacy_ast":
        console.print(
            "[yellow]Legacy node-list fixture: the pipeline steps below target "
            "the timeline format and will not run against it.[/yellow]"
        )

    console.print(Panel.fit(f"Loaded contract: {args.contract}", style="green"))
    console.print(Pretty(contract, max_length=10))

    # ------------------------------------------------------------------
    # TODO Step 1: Isolate presentation nodes
    # The timeline's `sequence` is already presentation-only (every entry
    # is a Stmt_InlineHTML / Stmt_Echo / Expr_Print). Step 1 reduces to
    # grouping entries by their `enclosedBy` context. The matching
    # labels_*.json gives each node's concern; "undecided" nodes need a flag.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 2: Trace data dependencies (backward slicing)
    # For each Stmt_Echo / Expr_Print, each `reads` object gives sourceKind,
    # confidence and, for db_row_field, source.{fetchNodeId, queryNodeId,
    # table, column}; `queries[queryNodeId]` holds the raw SQL. Computed
    # reads carry their underlying reads in `derivedFrom`.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 3: Infer component boundaries (recursive pattern matching)
    # A run of entries sharing an enclosedBy object with role "iteration"
    # -> list component (match on the role, never on Stmt_While vs
    # Stmt_Foreach: timeline_list_while / timeline_list_foreach must give the
    # same tree). A run sharing role "branch" -> conditional component;
    # then/elseif/else arms of one Stmt_If (same nodeId) form one ternary.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 4: Reconcile against contract (set comparison)
    # needs(C) = {last path element of each db_row_field read in C,
    # including those inside a computed read's derivedFrom}.
    # Reads with sourceKind "session" are EXCLUDED from matching: $_SESSION
    # values are not API fields and must not be flagged as missing.
    # Flag needs(C) - provides(E) as missing fields; carry each read's
    # confidence into the flag ("ambiguous" = SELECT * the analysis could
    # not map to a table/column).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 5: Convert HTML to JSX
    # Translate inline_html fragments + echo expressions into JSX,
    # handling attribute renaming (class -> className, etc).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 6: Convert data access to API calls
    # Replace data_access nodes with calls against the reconciled
    # OpenAPI operation (fetch / generated client call).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 7: Determine rendering mode (Server vs Client Component)
    # Components with no client-only concerns (interactivity, browser
    # APIs) default to Server Components; else mark "use client".
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 8: Emit component files
    # Write one .tsx file per inferred component into args.output.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 9: Emit data-fetching layer
    # Write the fetch/client wrapper module(s) that Step 6's API calls
    # import from.
    # ------------------------------------------------------------------

    console.print("[bold green]Pipeline scaffold ran successfully (all steps are TODO).[/bold green]")


if __name__ == "__main__":
    main()
