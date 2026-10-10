"""
Entry point for Component 2 (Frontend Migration).

Pipeline: legacy-PHP output timeline + OpenAPI contract -> Next.js/TSX components.

    python src/main.py --timeline mocks/timeline_admin.json --labels mocks/labels_admin.json --stage 1
    python src/main.py --timeline mocks/timeline_admin.json --labels mocks/labels_admin.json --stage 2
    python src/main.py --timeline mocks/timeline_list_while.json --labels mocks/labels_list_while.json --stage 5
    python src/main.py --timeline mocks/timeline_list_while.json --labels mocks/labels_list_while.json --stage all
    python src/main.py --input mocks/timeline_admin.json --contract mocks/sample_contract.json
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))  # run as `python src/main.py`: make the `src` package importable

from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.pretty import Pretty  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.tree import Tree  # noqa: E402

from src.mapping import loader  # noqa: E402
from src.mapping import stage2_boundaries as stage2  # noqa: E402
from src.mapping import stage3_requirements as stage3  # noqa: E402
from src.mapping import stage4_reconcile as stage4  # noqa: E402
from src.mapping import stage5_generate as stage5  # noqa: E402
from src.mapping.boundary_config import DEFAULT_PATH as DEFAULT_BOUNDARY_CONFIG  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.generation_config import DEFAULT_PATH as DEFAULT_GENERATION_CONFIG  # noqa: E402
from src.mapping.generation_config import load_generation_config  # noqa: E402
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

DEFAULT_CONTRACT = ROOT / "mocks" / "sample_contract.json"
DEFAULT_ENDPOINT_MAP = ROOT / "mocks" / "endpoint_map.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="component2-frontend",
        description="Generate Next.js TSX components from a legacy PHP output timeline and an OpenAPI contract.",
    )
    parser.add_argument("--timeline", type=Path, help="Output timeline JSON (mocks/timeline_*.json). Used with --stage.")
    parser.add_argument("--labels", type=Path, help="Concern labels JSON (mocks/labels_*.json). Used with --stage.")
    parser.add_argument(
        "--stage",
        choices=["1", "2", "3", "4", "5", "all"],
        help=(
            "Run the pipeline up to this stage (earlier stages run first) and write output/stage<N>_<name>.json "
            "for each. 1 = presentation isolation, 2 = boundary inference, 3 = data requirements, "
            "4 = contract reconciliation, 5 = generation (also writes output/generated/<name>/), "
            "all = print stages 1 to 5."
        ),
    )
    parser.add_argument(
        "--generation-config",
        type=Path,
        default=DEFAULT_GENERATION_CONFIG,
        help="Stage 5 settings (default: config/generation.json).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_BOUNDARY_CONFIG,
        help="Boundary-rule thresholds for stage 2 (default: config/boundary_rules.json).",
    )
    parser.add_argument(
        "--endpoint-map",
        type=Path,
        default=DEFAULT_ENDPOINT_MAP,
        help="Which contract response serves each timeline, for stage 4 (default: mocks/endpoint_map.json).",
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
        help=(
            "Path to the OpenAPI contract JSON produced by Component 1. Used with --input, and by stage 4 "
            "(default there: mocks/sample_contract.json)."
        ),
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


def load_or_exit(what: str, load, path: Path):
    try:
        return load(path)
    except (OSError, ValueError) as e:
        console.print(f"[red]Invalid {what}:[/red] {escape(str(e))}")
        sys.exit(1)


def wrote(path: Path) -> None:
    console.print(f"  wrote {path.relative_to(ROOT).as_posix()}", style="dim")


def print_stage1(result) -> None:
    console.print(escape(summary(result)), style="bold")
    for node in result.kept:
        if node.status is Status.REVIEW:
            console.print(f"  review  {node.node_id}  {node.node_type} {node.kind}: {node.reason}", style="yellow")


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
    if node.loop_concern:
        parts.append(f"loop: {node.loop_concern}")
    if node.root_tags:
        parts.append("item " + " ".join(f"<{tag}>" for tag in node.root_tags))
    if node.elements is not None:
        parts.append(f"{node.elements} elements")
    parts.append(f"[{STATUS_STYLE[node.status]}]{node.status}: {escape(node.reason)}[/]")
    parts.append(f"[dim]{len(node.node_ids)} nodes[/dim]")
    if node.flags:
        parts.append(f"[magenta]flags: {', '.join(node.flags)}[/magenta]")
    return "  ".join(parts)


def print_stage2(tree) -> None:
    def add(branch, node):
        child = branch.add(component_label(node))
        for sub in node.children:
            add(child, sub)

    printed = Tree(component_label(tree.root))
    for sub in tree.root.children:
        add(printed, sub)
    console.print(printed)
    console.print(escape(stage2.summary(tree)), style="bold")


def print_stage3(requirements) -> None:
    console.print(escape(stage3.summary(requirements)), style="bold")


def print_stage4(report) -> None:
    console.print(escape(stage4.summary(report)), style="bold")
    endpoint = report.endpoint
    console.print("  endpoint: " + (f"{endpoint.method.upper()} {endpoint.path}" if endpoint else "none mapped"),
                  style="dim")
    rows = stage4.flagged(report)
    if not rows:
        console.print("  no missing or cannot_reconcile needs", style="green")
    else:
        console.print(f"  file: {escape(report.entrypoint)}", style="dim")
        table = Table(box=box.SIMPLE_HEAD, show_edge=False)
        for column in ("Component", "Field", "File:line", "Result", "Reason", "Abstained"):
            table.add_column(column, overflow="fold")
        for row in rows:
            lines = sorted({ref.line for ref in row.need.references if ref.line is not None})
            unknown = any(ref.line is None for ref in row.need.references)
            where = ",".join([str(line) for line in lines] + (["?"] if unknown else []))
            reason = row.reason + (f" (hint: {row.hint})" if row.hint else "")
            table.add_row(f"{row.component_type} {row.component_id}", escape(row.need.name),
                          escape(f"{display_file(report.entrypoint)}:{where}"),
                          f"[{'red' if row.result == 'missing' else 'yellow'}]{row.result}[/]", escape(reason),
                          "[magenta]yes[/magenta]" if row.need.from_abstained else "")
        console.print(table)
        if any(ref.line is None for row in rows for ref in row.need.references):
            console.print("  line ? = no loc for that output node in the timeline (never guessed)", style="dim")
        if any(row.need.from_abstained for row in rows):
            console.print("  Abstained yes = read inside content Stage 2 abstained on: weaker evidence",
                          style="dim")
    if report.unused:
        console.print("  unused = not referenced by output reads; condition reads are not visible yet", style="dim")


def print_stage5(result) -> None:
    console.print(escape(stage5.summary(result)), style="bold")
    counts = stage5.counts(result)
    for title, by_reason in (("C2Todos", counts["c2todos"]), ("flags", counts["flags"])):
        text = ", ".join(f"{reason} {n}" for reason, n in by_reason.items()) or "none"
        console.print(f"  {title} by reason: {escape(text)}", style="dim" if title == "flags" else None)


def display_file(entrypoint: str) -> str:
    """Short file name for the table: the path's last part, or the hand-written mock's file name."""
    if entrypoint.startswith("hand-written:"):
        return entrypoint.removeprefix("hand-written:").split()[0]
    return entrypoint.rsplit("/", 1)[-1]


def run_stages(args) -> None:
    """Run stages 1 to the requested one, write each stage's JSON, print the requested one (or all)."""
    last = 5 if args.stage == "all" else int(args.stage)
    shown = {1, 2, 3, 4, 5} if args.stage == "all" else {last}
    timeline, labels = load_stage_inputs(args.timeline, args.labels)

    def stage(number, title, result, write, show):
        path = stage_output_path(number, args.timeline)
        write(result, path)
        if number in shown:
            if args.stage == "all":
                console.rule(f"Stage {number}: {title}")
            show(result)
            wrote(path)
        return result

    result1 = stage(1, "presentation isolation", isolate_presentation(timeline, labels), write_result, print_stage1)
    if last < 2:
        return
    config = load_or_exit("boundary config", load_boundary_config, args.config)
    tree = stage(2, "boundary inference", stage2.infer_boundaries(result1, timeline, config), stage2.write_tree,
                 print_stage2)
    if last < 3:
        return
    requirements = stage(3, "data requirements", stage3.recover_requirements(tree, timeline),
                         stage3.write_requirements, print_stage3)
    if last < 4:
        return
    contract = load_or_exit("contract", loader.load_contract, args.contract or DEFAULT_CONTRACT)
    endpoint_map = load_or_exit("endpoint map", loader.load_endpoint_map, args.endpoint_map)
    report = stage(4, "contract reconciliation", stage4.reconcile(requirements, contract, endpoint_map),
                   stage4.write_report, print_stage4)
    if last < 5:
        return
    generation_config = load_or_exit("generation config", load_generation_config, args.generation_config)
    result = stage5.generate(tree, requirements, report, contract, timeline, config, generation_config)
    name = args.timeline.stem.removeprefix("timeline_")
    output_root = args.output if args.output.is_absolute() else ROOT / args.output
    directory = output_root / name
    relative = directory.relative_to(ROOT).as_posix() if directory.is_relative_to(ROOT) else directory.as_posix()

    def write(result, path):
        stage5.write_report(result, path, relative)
        stage5.write_files(result, directory)

    def show(result):
        print_stage5(result)
        console.print(f"  wrote {len(result.files)} files to {relative}/", style="dim")

    stage(5, "generation", result, write, show)


def main() -> None:
    args = parse_args()

    if args.stage is not None:
        run_stages(args)
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
