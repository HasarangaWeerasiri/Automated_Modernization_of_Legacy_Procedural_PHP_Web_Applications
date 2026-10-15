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
    converted: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Produce one manual-review record per query that was not converted.

    With conversion results, the report lists every query that is not
    CONVERTED exactly once, whether it failed at recovery
    (stage "recovery") or at conversion (stage "conversion"). Queries that
    were converted, including by a guarded template after an unresolved
    recovery, are not listed. Without conversion results, only
    recovery-unresolved queries are listed (original behaviour).
    """

    unresolved: list[dict[str, Any]] = []

    conversion_by_id = (
        {item.get("query_id"): item for item in converted}
        if converted is not None
        else None
    )

    for query in queries:
        conversion = (
            conversion_by_id.get(query.get("query_id"), {})
            if conversion_by_id is not None
            else None
        )

        if conversion is not None and conversion.get("status") == "CONVERTED":
            continue

        if query.get("status") != "UNRESOLVED":
            if conversion is None:
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
                    "status": "CONVERSION_UNRESOLVED",
                    "stage": "conversion",
                    "reason_code": "CONVERSION_UNSUPPORTED",
                    "reason": (
                        conversion.get("reason")
                        or "Query could not be safely converted."
                    ),
                    "action": "manual_review_required",
                }
            )
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
                **({"stage": "recovery"} if conversion is not None else {}),
"reason_code": (
    query.get("reason_code")
    or "UNKNOWN_EXPR"
),
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
    converted_file: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Read queries.json (and converted_queries.json when given) and write
    unresolved_queries.json.
    """

    with queries_file.open(
        "r",
        encoding="utf-8",
    ) as file:
        queries = json.load(file)

    converted = None

    if converted_file is not None:
        with Path(converted_file).open("r", encoding="utf-8") as file:
            converted = json.load(file)

    unresolved = build_unresolved_report(queries, converted)

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