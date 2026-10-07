"""
Unresolved Query Report

Produces an explicit report for SQL queries that the deterministic
recovery stage could not safely reconstruct.

The report preserves source traceability and marks each unresolved
query for manual review instead of guessing its SQL semantics.
"""

import json
from pathlib import Path
from typing import Any


def build_unresolved_report(
    queries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Extract unresolved queries and produce manual-review records.
    """

    unresolved: list[dict[str, Any]] = []

    for query in queries:
        if query.get("status") != "UNRESOLVED":
            continue

        unresolved.append(
            {
                "query_id": query.get("query_id"),
                "source_file": query.get("source_file"),
                "source_line": query.get("source_line"),
                "executor": query.get("executor"),
                "sql_argument": query.get("sql_argument"),
                "recovered_sql": query.get("recovered_sql"),
                "operation": query.get("operation"),
                "status": "UNRESOLVED",
                "reason": (
                    query.get("reason")
                    or "Query could not be safely recovered."
                ),
                "action": "manual_review_required",
            }
        )

    return unresolved


def generate_unresolved_report(
    queries_file: Path,
    output_file: Path,
) -> list[dict[str, Any]]:
    """
    Read queries.json and write unresolved_queries.json.
    """

    with queries_file.open(
        "r",
        encoding="utf-8",
    ) as file:
        queries = json.load(file)

    unresolved = build_unresolved_report(queries)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            unresolved,
            file,
            indent=4,
            ensure_ascii=False,
        )

    return unresolved


def main() -> None:
    component_root = Path(__file__).resolve().parents[3]
    repository_root = component_root.parents[1]

    queries_file = (
        repository_root
        / "output"
        / "mixed_crud"
        / "queries.json"
    )

    output_file = (
        repository_root
        / "output"
        / "mixed_crud"
        / "unresolved_queries.json"
    )

    if not queries_file.exists():
        raise FileNotFoundError(
            f"Queries file not found: {queries_file}"
        )

    unresolved = generate_unresolved_report(
        queries_file,
        output_file,
    )

    print(
        f"Unresolved query report generated: {output_file}"
    )
    print(f"Unresolved queries: {len(unresolved)}")
    print(
        "Manual review required: "
        f"{sum(1 for item in unresolved if item['action'] == 'manual_review_required')}"
    )


if __name__ == "__main__":
    main()