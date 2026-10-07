"""
Entry point for Component 2 (Frontend Migration).

Pipeline: legacy-PHP output timeline + OpenAPI contract -> Next.js/TSX components.

    python src/main.py --timeline mocks/timeline_admin.json --labels mocks/labels_admin.json --stage 1
    python src/main.py --timeline mocks/timeline_admin.json --labels mocks/labels_admin.json --stage 2
    python src/main.py --input mocks/timeline_admin.json --contract mocks/sample_contract.json
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))  # run as `python src/main.py`: make the `src` package importable

from rich.console import Console  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.pretty import Pretty  # noqa: E402
from rich.tree import Tree  # noqa: E402

from src.mapping import loader  # noqa: E402
from src.mapping import stage2_boundaries as stage2  # noqa: E402
from src.mapping.boundary_config import DEFAULT_PATH as DEFAULT_BOUNDARY_CONFIG  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.stage1_isolation import isolate_presentation, summary, write_result  # noqa: E402
from src.model import Status  # noqa: E402

# JSON-level loaders under their pre-Stage-1 names, for tests/test_mock_timelines.py.
# All format knowledge lives in src/mapping/loader.py.
load_timeline = loader.load_timeline_json
load_labels = loader.load_labels_json
load_legacy_ast = loader.load_legacy_ast
load_input = loader.load_input
load_contract = loader.load_contract_json

__all__ = ["load_contract", "load_input", "load_labels", "load_legacy_ast", "load_timeline", "main"]

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="component2-frontend",
        description="Generate Next.js TSX components from a legacy PHP output timeline and an OpenAPI contract.",
    )
    parser.add_argument("--timeline", type=Path, help="Output timeline JSON (mocks/timeline_*.json). Used with --stage.")
    parser.add_argument("--labels", type=Path, help="Concern labels JSON (mocks/labels_*.json). Used with --stage.")
    parser.add_argument(
        "--stage",
        type=int,
        choices=[1, 2],
        help=(
            "Run one pipeline stage and write output/stage<N>_<name>.json. "
            "1 = presentation isolation, 2 = boundary inference (component tree)."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_BOUNDARY_CONFIG,
        help="Boundary-rule thresholds for --stage 2 (default: config/boundary_rules.json).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help=(
            "Path to the output timeline JSON produced by the backend analysis "
            "(see mocks/timeline_*.json). The legacy node-list fixture "
            "(mocks/sample_ast.json) is still accepted and detected automatically."
        ),
    )
    parser.add_argument(
        "--contract",
        type=Path,
        help="Path to the OpenAPI contract JSON produced by Component 1 (see mocks/sample_contract.json).",
    )
    parser.add_argument(
        "--output",
        default=Path("output/generated"),
        type=Path,
        help="Directory to write generated Next.js components into (default: output/generated).",
    )
    args = parser.parse_args()
    if args.stage is not None and (args.timeline is None or args.labels is None):
        parser.error("--stage needs --timeline and --labels")
    if args.stage is None and (args.input is None or args.contract is None):
        parser.error("give --timeline/--labels/--stage, or --input and --contract")
    return args


def load_stage_inputs(timeline_path: Path, labels_path: Path):
    for path in (timeline_path, labels_path):
        if not path.exists():
            console.print(f"[red]Input not found:[/red] {path}")
            sys.exit(1)
    try:
        return loader.load_timeline(timeline_path), loader.load_labels(labels_path)
    except ValueError as e:
        console.print(f"[red]Invalid input:[/red] {escape(str(e))}")
        sys.exit(1)


def stage_output_path(stage: int, timeline_path: Path) -> Path:
    return ROOT / "output" / f"stage{stage}_{timeline_path.stem.removeprefix('timeline_')}.json"


def run_stage1(timeline_path: Path, labels_path: Path) -> None:
    timeline, labels = load_stage_inputs(timeline_path, labels_path)
    result = isolate_presentation(timeline, labels)
    out_path = stage_output_path(1, timeline_path)
    write_result(result, out_path)

    console.print(escape(summary(result)), style="bold")
    for node in result.kept:
        if node.status is Status.REVIEW:
            console.print(f"  review  {node.node_id}  {node.node_type} {node.kind}: {node.reason}", style="yellow")
    console.print(f"  wrote {out_path.relative_to(ROOT).as_posix()}", style="dim")


STATUS_STYLE = {"ok": "green", "review": "yellow", "abstain": "red"}


def component_label(node) -> str:
    """One line of the printed component tree."""
    parts = [f"[bold]{node.type}[/bold]"]
    if node.rules:
        parts.append(escape(f"[{' '.join(node.rules)}]"))
    if node.container and node.container.has_wrapper:
        element_id = f"#{node.container.element_id}" if node.container.element_id else ""
        parts.append(f"container <{node.container.tag}{element_id}>")
    elif node.container:
        parts.append("no wrapper")
    if node.root_tags:
        parts.append("item " + " ".join(f"<{tag}>" for tag in node.root_tags))
    if node.elements is not None:
        parts.append(f"{node.elements} elements")
    parts.append(f"[{STATUS_STYLE[node.status]}]{node.status}: {escape(node.reason)}[/]")
    parts.append(f"[dim]{len(node.node_ids)} nodes[/dim]")
    if node.flags:
        parts.append(f"[magenta]flags: {', '.join(node.flags)}[/magenta]")
    return "  ".join(parts)


def run_stage2(timeline_path: Path, labels_path: Path, config_path: Path) -> None:
    timeline, labels = load_stage_inputs(timeline_path, labels_path)
    try:
        config = load_boundary_config(config_path)
    except (OSError, ValueError) as e:
        console.print(f"[red]Invalid boundary config:[/red] {escape(str(e))}")
        sys.exit(1)
    tree = stage2.infer_boundaries(isolate_presentation(timeline, labels), timeline, config)
    out_path = stage_output_path(2, timeline_path)
    stage2.write_tree(tree, out_path)

    def add(branch, node):
        child = branch.add(component_label(node))
        for sub in node.children:
            add(child, sub)

    printed = Tree(component_label(tree.root))
    for sub in tree.root.children:
        add(printed, sub)
    console.print(printed)
    console.print(escape(stage2.summary(tree)), style="bold")
    console.print(f"  wrote {out_path.relative_to(ROOT).as_posix()}", style="dim")


def main() -> None:
    args = parse_args()

    if args.stage == 1:
        run_stage1(args.timeline, args.labels)
        return
    if args.stage == 2:
        run_stage2(args.timeline, args.labels, args.config)
        return

    if not args.input.exists():
        console.print(f"[red]Timeline input not found:[/red] {args.input}")
        sys.exit(1)
    if not args.contract.exists():
        console.print(f"[red]Contract input not found:[/red] {args.contract}")
        sys.exit(1)

    try:
        input_format, timeline = load_input(args.input)
    except ValueError as e:
        console.print(f"[red]Invalid input:[/red] {escape(str(e))}")
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
    # Step 1: Isolate presentation nodes -- implemented in
    # src/mapping/stage1_isolation.py; run it with --stage 1.
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
