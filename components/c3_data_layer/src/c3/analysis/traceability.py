"""
Data-Layer Migration Traceability Report

Connects each discovered SQL execution site to its recovery and
column-use analysis results.

This makes it possible to trace generated migration information
back to the original PHP source file and line.
"""

import json
from pathlib import Path
from typing import Any


def build_traceability(
    queries: list[dict[str, Any]],
    column_usage: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Combine query recovery and column-use information using query_id.
    """

    usage_by_id = {
        item.get("query_id"): item
        for item in column_usage
    }

    traceability: list[dict[str, Any]] = []

    for query in queries:
        query_id = query.get("query_id")
        usage = usage_by_id.get(query_id)

        recovery_status = query.get("status")

        if recovery_status == "RECOVERED":
            action = "ready_for_conversion"
        else:
            action = "manual_review_required"

        traceability.append(
            {
                "query_id": query_id,
                "source_file": query.get("source_file"),
                "source_line": query.get("source_line"),
                "executor": query.get("executor"),
                "recovery_status": recovery_status,
                "recovered_sql": query.get("recovered_sql"),
                "operation": query.get("operation"),
                "analysis_status": (
                    usage.get("status")
                    if usage
                    else "NOT_ANALYZED"
                ),
                "tables": (
                    usage.get("tables", [])
                    if usage
                    else []
                ),
                "read_columns": (
                    usage.get("read_columns", [])
                    if usage
                    else []
                ),
                "write_columns": (
                    usage.get("write_columns", [])
                    if usage
                    else []
                ),
                "filter_columns": (
                    usage.get("filter_columns", [])
                    if usage
                    else []
                ),
                "reason": (
                    query.get("reason")
                    or (
                        usage.get("reason")
                        if usage
                        else None
                    )
                ),
                "action": action,
            }
        )

    return traceability


def generate_traceability_report(
    queries_file: Path,
    column_usage_file: Path,
    output_file: Path,
) -> list[dict[str, Any]]:
    """
    Read recovery and column-use files and write traceability.json.
    """

    queries = json.loads(
        queries_file.read_text(
            encoding="utf-8",
        )
    )

    column_usage = json.loads(
        column_usage_file.read_text(
            encoding="utf-8",
        )
    )

    traceability = build_traceability(
        queries,
        column_usage,
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        json.dumps(
            traceability,
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return traceability