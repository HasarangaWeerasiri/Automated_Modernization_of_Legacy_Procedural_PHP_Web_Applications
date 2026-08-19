"""
Entry point for Component 2 (Frontend Migration).

Pipeline: legacy-PHP AST + OpenAPI contract -> Next.js/TSX components.
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.pretty import Pretty
from rich.panel import Panel

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="component2-frontend",
        description="Generate Next.js TSX components from a legacy PHP AST and an OpenAPI contract.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the AST JSON produced by the Static Analysis Core (see mocks/sample_ast.json).",
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


def load_ast(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_contract(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        console.print(f"[red]AST input not found:[/red] {args.input}")
        sys.exit(1)
    if not args.contract.exists():
        console.print(f"[red]Contract input not found:[/red] {args.contract}")
        sys.exit(1)

    ast = load_ast(args.input)
    contract = load_contract(args.contract)

    console.print(Panel.fit(f"Loaded AST: {args.input}", style="green"))
    console.print(Pretty(ast, max_length=10))

    console.print(Panel.fit(f"Loaded contract: {args.contract}", style="green"))
    console.print(Pretty(contract, max_length=10))

    # ------------------------------------------------------------------
    # TODO Step 1: Isolate presentation nodes
    # Walk `ast["nodes"]`, keep nodes tagged concern == "presentation",
    # and any logic node (if/while) that has presentation descendants.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 2: Trace data dependencies (backward slicing)
    # For each presentation node with a `dataSource`/`field`, walk backward
    # through the flat node list to the data_access node (function_call /
    # assignment) that produced that dataSource.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 3: Infer component boundaries (recursive pattern matching)
    # Recognize structural patterns: `while` wrapping presentation nodes
    # -> list component; `if` wrapping presentation nodes -> conditional
    # component; session-key conditions -> auth-gated component.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TODO Step 4: Reconcile against contract (set comparison)
    # Compare the set of (dataSource, field) pairs required by inferred
    # components against the fields available in the OpenAPI response
    # schemas. Flag any required field the contract does not provide.
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
