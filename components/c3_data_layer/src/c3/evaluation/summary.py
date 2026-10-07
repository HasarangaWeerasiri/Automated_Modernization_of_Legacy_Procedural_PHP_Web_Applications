"""
Combined evaluation summary for Data Layer Migration benchmarks.
"""

import json
from pathlib import Path
from typing import Any


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None

    return round((numerator / denominator) * 100, 2)


def build_summary(
    benchmark_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:

    total_queries = 0
    expected_recoverable = 0
    expected_abstentions = 0
    actual_recovered = 0
    actual_abstentions = 0

    tp = 0
    fp = 0
    fn = 0
    tn = 0

    semantic_total = 0

    operation_correct = 0
    table_correct = 0
    read_correct = 0
    write_correct = 0
    filter_correct = 0

    benchmark_summaries = {}

    for name, result in benchmark_results.items():
        total_queries += result["total_queries"]
        expected_recoverable += result["expected_recoverable"]
        expected_abstentions += result["expected_abstentions"]
        actual_recovered += result["actual_recovered"]
        actual_abstentions += result["actual_abstentions"]

        matrix = result["recovery_confusion_matrix"]

        tp += matrix["true_positive"]
        fp += matrix["false_positive"]
        fn += matrix["false_negative"]
        tn += matrix["true_negative"]

        for detail in result["details"]:
            if detail["expected_recoverable"]:
                semantic_total += 1

                operation_correct += int(
                    detail.get("operation_correct", False)
                )

                table_correct += int(
                    detail.get("table_correct", False)
                )

                read_correct += int(
                    detail.get("read_columns_correct", False)
                )

                write_correct += int(
                    detail.get("write_columns_correct", False)
                )

                filter_correct += int(
                    detail.get("filter_columns_correct", False)
                )

        benchmark_summaries[name] = {
            "total_queries": result["total_queries"],
            "expected_recoverable": result["expected_recoverable"],
            "expected_abstentions": result["expected_abstentions"],
            "actual_recovered": result["actual_recovered"],
            "actual_abstentions": result["actual_abstentions"],
            "recovery_precision": result["recovery_precision"],
            "recovery_recall": result["recovery_recall"],
            "recovery_f1": result["recovery_f1"],
            "abstention_rate": result["abstention_rate"],
        }

    precision = _percentage(tp, tp + fp)
    recall = _percentage(tp, tp + fn)

    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(
            2 * precision * recall / (precision + recall),
            2,
        )

    return {
        "benchmark_count": len(benchmark_results),
        "total_queries": total_queries,
        "expected_recoverable": expected_recoverable,
        "expected_abstentions": expected_abstentions,
        "actual_recovered": actual_recovered,
        "actual_abstentions": actual_abstentions,

        "recovery_confusion_matrix": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
        },

        "recovery_precision": precision,
        "recovery_recall": recall,
        "recovery_f1": f1,

        "recovery_decision_accuracy": _percentage(
            tp + tn,
            total_queries,
        ),

        "correct_abstention_rate": _percentage(
            tn,
            expected_abstentions,
        ),

        "observed_abstention_rate": _percentage(
            actual_abstentions,
            total_queries,
        ),

        "operation_accuracy": _percentage(
            operation_correct,
            semantic_total,
        ),

        "table_accuracy": _percentage(
            table_correct,
            semantic_total,
        ),

        "read_column_accuracy": _percentage(
            read_correct,
            semantic_total,
        ),

        "write_column_accuracy": _percentage(
            write_correct,
            semantic_total,
        ),

        "filter_column_accuracy": _percentage(
            filter_correct,
            semantic_total,
        ),

        "benchmarks": benchmark_summaries,

        "evaluation_scope_note": (
            "Results apply only to the currently annotated "
            "benchmark fixtures and should not be interpreted "
            "as general accuracy on arbitrary legacy PHP systems."
        ),
    }


def build_summary_from_files(
    evaluation_files: dict[str, Path],
    output_file: Path | None = None,
) -> dict[str, Any]:

    benchmark_results = {}

    for name, path in evaluation_files.items():
        benchmark_results[name] = json.loads(
            path.read_text(encoding="utf-8")
        )

    summary = build_summary(benchmark_results)

    if output_file is not None:
        output_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_file.write_text(
            json.dumps(
                summary,
                indent=4,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    return summary