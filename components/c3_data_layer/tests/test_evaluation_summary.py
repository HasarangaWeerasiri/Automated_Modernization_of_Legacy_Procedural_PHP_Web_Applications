from c3.evaluation.summary import build_summary


def test_combines_recovery_counts():
    results = {
        "simple": {
            "total_queries": 4,
            "expected_recoverable": 4,
            "expected_abstentions": 0,
            "actual_recovered": 4,
            "actual_abstentions": 0,
            "recovery_confusion_matrix": {
                "true_positive": 4,
                "false_positive": 0,
                "false_negative": 0,
                "true_negative": 0,
            },
            "recovery_precision": 100.0,
            "recovery_recall": 100.0,
            "recovery_f1": 100.0,
            "abstention_rate": 0.0,
            "details": [],
        },
        "mixed": {
            "total_queries": 6,
            "expected_recoverable": 5,
            "expected_abstentions": 1,
            "actual_recovered": 5,
            "actual_abstentions": 1,
            "recovery_confusion_matrix": {
                "true_positive": 5,
                "false_positive": 0,
                "false_negative": 0,
                "true_negative": 1,
            },
            "recovery_precision": 100.0,
            "recovery_recall": 100.0,
            "recovery_f1": 100.0,
            "abstention_rate": 16.67,
            "details": [],
        },
    }

    summary = build_summary(results)

    assert summary["benchmark_count"] == 2
    assert summary["total_queries"] == 10
    assert summary["expected_recoverable"] == 9
    assert summary["expected_abstentions"] == 1

    matrix = summary["recovery_confusion_matrix"]

    assert matrix["true_positive"] == 9
    assert matrix["false_positive"] == 0
    assert matrix["false_negative"] == 0
    assert matrix["true_negative"] == 1


def test_combined_recovery_metrics():
    results = {
        "fixture": {
            "total_queries": 4,
            "expected_recoverable": 2,
            "expected_abstentions": 2,
            "actual_recovered": 2,
            "actual_abstentions": 2,
            "recovery_confusion_matrix": {
                "true_positive": 2,
                "false_positive": 0,
                "false_negative": 0,
                "true_negative": 2,
            },
            "recovery_precision": 100.0,
            "recovery_recall": 100.0,
            "recovery_f1": 100.0,
            "abstention_rate": 50.0,
            "details": [],
        }
    }

    summary = build_summary(results)

    assert summary["recovery_precision"] == 100.0
    assert summary["recovery_recall"] == 100.0
    assert summary["recovery_f1"] == 100.0
    assert summary["recovery_decision_accuracy"] == 100.0
    assert summary["correct_abstention_rate"] == 100.0
    assert summary["observed_abstention_rate"] == 50.0


def test_semantic_metrics_are_combined_from_queries():
    results = {
        "fixture": {
            "total_queries": 2,
            "expected_recoverable": 2,
            "expected_abstentions": 0,
            "actual_recovered": 2,
            "actual_abstentions": 0,
            "recovery_confusion_matrix": {
                "true_positive": 2,
                "false_positive": 0,
                "false_negative": 0,
                "true_negative": 0,
            },
            "recovery_precision": 100.0,
            "recovery_recall": 100.0,
            "recovery_f1": 100.0,
            "abstention_rate": 0.0,
            "details": [
                {
                    "expected_recoverable": True,
                    "operation_correct": True,
                    "table_correct": True,
                    "read_columns_correct": True,
                    "write_columns_correct": True,
                    "filter_columns_correct": True,
                },
                {
                    "expected_recoverable": True,
                    "operation_correct": True,
                    "table_correct": True,
                    "read_columns_correct": False,
                    "write_columns_correct": True,
                    "filter_columns_correct": True,
                },
            ],
        }
    }

    summary = build_summary(results)

    assert summary["operation_accuracy"] == 100.0
    assert summary["table_accuracy"] == 100.0
    assert summary["read_column_accuracy"] == 50.0
    assert summary["write_column_accuracy"] == 100.0
    assert summary["filter_column_accuracy"] == 100.0