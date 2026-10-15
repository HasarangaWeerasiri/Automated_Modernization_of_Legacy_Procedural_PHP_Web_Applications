"""
Data-Layer Migration Traceability Report

Connects each discovered SQL execution site to its recovery and
column-use analysis results.

This makes it possible to trace generated migration information
back to the original PHP source file and line.
"""

import json
import re
from pathlib import Path
from typing import Any


def generated_function_names(data_access_source: str) -> dict[str, str]:
    """Map query IDs (upper case) to generated function names in data_access.py."""

    names = {}

    for match in re.finditer(r"^def ([A-Za-z_][A-Za-z0-9_]*_(q\d+))\(", data_access_source, re.MULTILINE):
        names[match.group(2).upper()] = match.group(1)

    return names


def build_traceability(
    queries: list[dict[str, Any]],
    column_usage: list[dict[str, Any]],
    converted: list[dict[str, Any]] | None = None,
    data_access_source: str | None = None,
) -> list[dict[str, Any]]:
    """
    Combine query recovery and column-use information using query_id.

    With conversion results and the generated data_access.py, each record
    also carries the conversion status/rule, the generated function (or
    unresolved stub) and the final action, so every PHP execution point
    can be followed to the generated code or to its manual-review item.
    """

    conversion_by_id = {item.get("query_id"): item for item in (converted or [])}
    functions = generated_function_names(data_access_source or "")

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

        extra = {}

        if converted is not None:
            conversion = conversion_by_id.get(query_id, {})
            is_converted = conversion.get("status") == "CONVERTED"
            action = "use_generated_function" if is_converted else "manual_review_required"
            extra = {
                "conversion_status": conversion.get("status"),
                "conversion_rule": conversion.get("conversion_rule"),
                "conversion_reason": conversion.get("reason"),
                "generated_function": functions.get(str(query_id).upper()),
                "generated_function_kind": (
                    "data_access" if is_converted else "unresolved_stub"
                ) if functions.get(str(query_id).upper()) else None,
            }

        diagnostics = query.get("cross_file_diagnostics") or {}

        if diagnostics.get("function_source_file"):
            extra["sql_built_in"] = {
                "function": diagnostics.get("function_name"),
                "source_file": diagnostics.get("function_source_file"),
                "source_line": diagnostics.get("function_source_line"),
            }

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
                **extra,
            }
        )

    return traceability


def generate_traceability_report(
    queries_file: Path,
    column_usage_file: Path,
    output_file: Path,
    converted_file: Path | None = None,
    data_access_file: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Read recovery and column-use files (and, when given, the conversion
    results and generated data_access.py) and write the traceability report.
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

    converted = (
        json.loads(Path(converted_file).read_text(encoding="utf-8"))
        if converted_file is not None
        else None
    )
    data_access_source = (
        Path(data_access_file).read_text(encoding="utf-8")
        if data_access_file is not None and Path(data_access_file).exists()
        else None
    )

    traceability = build_traceability(
        queries,
        column_usage,
        converted,
        data_access_source,
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