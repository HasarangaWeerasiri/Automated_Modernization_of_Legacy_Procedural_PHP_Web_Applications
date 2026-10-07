from c3.evaluation.evaluator import evaluate_queries


def test_perfect_recovered_query():
    ground_truth = [
        {
            "query_id": "Q001",
            "expected_recoverable": True,
            "expected_operation": "SELECT",
            "expected_table": "users",
            "expected_read_columns": ["id", "name"],
            "expected_write_columns": [],
            "expected_filter_columns": ["id"],
        }
    ]

    recovered = [
        {
            "query_id": "Q001",
            "status": "RECOVERED",
            "operation": "SELECT",
        }
    ]

    usage = [
        {
            "query_id": "Q001",
            "tables": ["users"],
            "read_columns": ["id", "name"],
            "write_columns": [],
            "filter_columns": ["id"],
        }
    ]

    result = evaluate_queries(
        ground_truth,
        recovered,
        usage,
    )

    assert result["recovery_decision_accuracy"] == 100.0
    assert result["operation_accuracy"] == 100.0
    assert result["table_accuracy"] == 100.0
    assert result["read_column_accuracy"] == 100.0
    assert result["write_column_accuracy"] == 100.0
    assert result["filter_column_accuracy"] == 100.0


def test_correct_abstention():
    ground_truth = [
        {
            "query_id": "Q006",
            "expected_recoverable": False,
            "expected_operation": None,
            "expected_table": None,
            "expected_read_columns": [],
            "expected_write_columns": [],
            "expected_filter_columns": [],
        }
    ]

    recovered = [
        {
            "query_id": "Q006",
            "status": "UNRESOLVED",
            "operation": None,
        }
    ]

    usage = [
        {
            "query_id": "Q006",
            "status": "UNRESOLVED",
            "tables": [],
            "read_columns": [],
            "write_columns": [],
            "filter_columns": [],
        }
    ]

    result = evaluate_queries(
        ground_truth,
        recovered,
        usage,
    )

    assert result["recovery_decision_accuracy"] == 100.0
    assert result["actual_abstentions"] == 1
    assert result["abstention_rate"] == 100.0


def test_wrong_column_usage_reduces_accuracy():
    ground_truth = [
        {
            "query_id": "Q001",
            "expected_recoverable": True,
            "expected_operation": "UPDATE",
            "expected_table": "users",
            "expected_read_columns": [],
            "expected_write_columns": ["name"],
            "expected_filter_columns": ["id"],
        }
    ]

    recovered = [
        {
            "query_id": "Q001",
            "status": "RECOVERED",
            "operation": "UPDATE",
        }
    ]

    usage = [
        {
            "query_id": "Q001",
            "tables": ["users"],
            "read_columns": [],
            "write_columns": ["email"],
            "filter_columns": ["id"],
        }
    ]

    result = evaluate_queries(
        ground_truth,
        recovered,
        usage,
    )

    assert result["write_column_accuracy"] == 0.0
    assert result["filter_column_accuracy"] == 100.0


def test_missing_recovered_query_is_incorrect():
    ground_truth = [
        {
            "query_id": "Q001",
            "expected_recoverable": True,
            "expected_operation": "SELECT",
            "expected_table": "users",
            "expected_read_columns": ["id"],
            "expected_write_columns": [],
            "expected_filter_columns": [],
        }
    ]

    result = evaluate_queries(
        ground_truth,
        [],
        [],
    )

    assert result["recovery_decision_accuracy"] == 0.0
    assert result["actual_abstentions"] == 1