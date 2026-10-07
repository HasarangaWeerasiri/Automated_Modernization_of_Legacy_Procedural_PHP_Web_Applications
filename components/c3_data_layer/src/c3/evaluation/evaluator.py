"""
Evaluation utilities for the Data Layer Migration component.

Compares manually prepared benchmark ground truth against the
actual SQL recovery and column-use outputs produced by the tool.
"""

import json
from pathlib import Path
from typing import Any


def _as_set(values: list[str] | None) -> set[str]:
    return set(values or [])


def evaluate_queries(
    ground_truth: list[dict[str, Any]],
    recovered_queries: list[dict[str, Any]],
    column_usage: list[dict[str, Any]],
) -> dict[str, Any]:

    recovered_by_id = {
        item["query_id"]: item
        for item in recovered_queries
    }

    usage_by_id = {
        item["query_id"]: item
        for item in column_usage
    }

    details = []

    recovery_decisions_correct = 0

    true_positive = 0
    false_positive = 0
    false_negative = 0
    true_negative = 0

    operation_correct = 0
    table_correct = 0
    read_correct = 0
    write_correct = 0
    filter_correct = 0

    expected_recoverable_count = 0
    expected_abstention_count = 0
    actual_recovered_count = 0
    actual_abstention_count = 0

    analyzable_count = 0

    for expected in ground_truth:
        query_id = expected["query_id"]

        actual_query = recovered_by_id.get(query_id)
        actual_usage = usage_by_id.get(query_id)

        expected_recoverable = bool(
            expected["expected_recoverable"]
        )

        if expected_recoverable:
            expected_recoverable_count += 1
        else:
            expected_abstention_count += 1

        actual_recoverable = (
            actual_query is not None
            and actual_query.get("status") == "RECOVERED"
        )

        if actual_recoverable:
            actual_recovered_count += 1
        else:
            actual_abstention_count += 1

        recovery_correct = (
            expected_recoverable == actual_recoverable
        )

        if recovery_correct:
            recovery_decisions_correct += 1

        if expected_recoverable and actual_recoverable:
            true_positive += 1
        elif not expected_recoverable and actual_recoverable:
            false_positive += 1
        elif expected_recoverable and not actual_recoverable:
            false_negative += 1
        else:
            true_negative += 1

        detail = {
            "query_id": query_id,
            "expected_recoverable": expected_recoverable,
            "actual_recoverable": actual_recoverable,
            "recovery_decision_correct": recovery_correct,
        }

        if expected_recoverable:
            analyzable_count += 1

            actual_operation = (
                actual_query.get("operation")
                if actual_query
                else None
            )

            operation_match = (
                actual_operation
                == expected.get("expected_operation")
            )

            if operation_match:
                operation_correct += 1

            actual_tables = (
                actual_usage.get("tables", [])
                if actual_usage
                else []
            )

            expected_table = expected.get("expected_table")

            table_match = (
                expected_table in actual_tables
                if expected_table is not None
                else len(actual_tables) == 0
            )

            if table_match:
                table_correct += 1

            actual_read = _as_set(
                actual_usage.get("read_columns", [])
                if actual_usage
                else []
            )

            actual_write = _as_set(
                actual_usage.get("write_columns", [])
                if actual_usage
                else []
            )

            actual_filter = _as_set(
                actual_usage.get("filter_columns", [])
                if actual_usage
                else []
            )

            expected_read = _as_set(
                expected.get("expected_read_columns", [])
            )

            expected_write = _as_set(
                expected.get("expected_write_columns", [])
            )

            expected_filter = _as_set(
                expected.get("expected_filter_columns", [])
            )

            read_match = actual_read == expected_read
            write_match = actual_write == expected_write
            filter_match = actual_filter == expected_filter

            if read_match:
                read_correct += 1

            if write_match:
                write_correct += 1

            if filter_match:
                filter_correct += 1

            detail.update(
                {
                    "operation_correct": operation_match,
                    "table_correct": table_match,
                    "read_columns_correct": read_match,
                    "write_columns_correct": write_match,
                    "filter_columns_correct": filter_match,
                }
            )

        details.append(detail)

    total = len(ground_truth)

    def percentage(
        correct: int,
        denominator: int,
    ) -> float:
        if denominator == 0:
            return 0.0

        return round(
            (correct / denominator) * 100,
            2,
        )

    def ratio_percentage(
        numerator: int,
        denominator: int,
    ) -> float | None:
        if denominator == 0:
            return None

        return round(
            (numerator / denominator) * 100,
            2,
        )

    precision = ratio_percentage(
        true_positive,
        true_positive + false_positive,
    )

    recall = ratio_percentage(
        true_positive,
        true_positive + false_negative,
    )

    if precision is None or recall is None:
        f1_score = None
    elif precision + recall == 0:
        f1_score = 0.0
    else:
        f1_score = round(
            2 * precision * recall
            / (precision + recall),
            2,
        )

    correct_abstention_rate = ratio_percentage(
        true_negative,
        expected_abstention_count,
    )

    return {
        "total_queries": total,
        "expected_recoverable": expected_recoverable_count,
        "expected_abstentions": expected_abstention_count,
        "actual_recovered": actual_recovered_count,
        "actual_abstentions": actual_abstention_count,

        "recovery_confusion_matrix": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_negative": true_negative,
        },

        "recovery_precision": precision,
        "recovery_recall": recall,
        "recovery_f1": f1_score,
        "correct_abstention_rate": correct_abstention_rate,

        "recovery_decision_accuracy": percentage(
            recovery_decisions_correct,
            total,
        ),

        "operation_accuracy": percentage(
            operation_correct,
            analyzable_count,
        ),

        "table_accuracy": percentage(
            table_correct,
            analyzable_count,
        ),

        "read_column_accuracy": percentage(
            read_correct,
            analyzable_count,
        ),

        "write_column_accuracy": percentage(
            write_correct,
            analyzable_count,
        ),

        "filter_column_accuracy": percentage(
            filter_correct,
            analyzable_count,
        ),

        "abstention_rate": percentage(
            actual_abstention_count,
            total,
        ),

        "details": details,
    }


def evaluate_files(
    ground_truth_file: Path,
    queries_file: Path,
    column_usage_file: Path,
    output_file: Path | None = None,
) -> dict[str, Any]:

    ground_truth = json.loads(
        ground_truth_file.read_text(
            encoding="utf-8"
        )
    )

    recovered_queries = json.loads(
        queries_file.read_text(
            encoding="utf-8"
        )
    )

    column_usage = json.loads(
        column_usage_file.read_text(
            encoding="utf-8"
        )
    )

    result = evaluate_queries(
        ground_truth,
        recovered_queries,
        column_usage,
    )

    if output_file is not None:
        output_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_file.write_text(
            json.dumps(
                result,
                indent=4,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    return result