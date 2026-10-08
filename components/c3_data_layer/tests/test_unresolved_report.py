import json

from c3.analysis.unresolved_report import (
    build_unresolved_report,
    generate_unresolved_report,
)


def test_only_unresolved_queries_are_reported():
    queries = [
        {
            "query_id": "Q001",
            "source_file": "01_select.php",
            "source_line": 4,
            "executor": "mysqli_query",
            "sql_argument": "$sql",
            "recovered_sql": (
                "SELECT id, title, price FROM products"
            ),
            "operation": "SELECT",
            "status": "RECOVERED",
            "reason": None,
        },
        {
            "query_id": "Q006",
            "source_file": "06_dynamic_function.php",
            "source_line": 8,
            "executor": "mysqli_query",
            "sql_argument": "$sql",
            "recovered_sql": (
                "{{UNRESOLVED:"
                "buildQuery($table, $condition)}}"
            ),
            "operation": None,
            "status": "UNRESOLVED",
            "reason": (
                "Query contains unsupported "
                "dynamic expression."
            ),
        },
    ]

    result = build_unresolved_report(queries)

    assert len(result) == 1

    unresolved = result[0]

    assert unresolved["query_id"] == "Q006"
    assert unresolved["status"] == "UNRESOLVED"
    assert unresolved["operation"] is None

    assert (
        unresolved["action"]
        == "manual_review_required"
    )

    assert (
        unresolved["reason"]
        == "Query contains unsupported dynamic expression."
    )


def test_unresolved_report_preserves_traceability():
    queries = [
        {
            "query_id": "Q006",
            "source_file": (
                "..\\..\\benchmarks\\apps\\mixed_crud\\"
                "legacy\\06_dynamic_function.php"
            ),
            "source_line": 8,
            "executor": "mysqli_query",
            "sql_argument": "$sql",
            "recovered_sql": (
                "{{UNRESOLVED:"
                "buildQuery($table, $condition)}}"
            ),
            "operation": None,
            "status": "UNRESOLVED",
            "reason": (
                "Query contains unsupported "
                "dynamic expression."
            ),
        }
    ]

    result = build_unresolved_report(queries)

    assert result[0]["source_line"] == 8

    assert result[0]["source_file"].endswith(
        "06_dynamic_function.php"
    )

    assert result[0]["executor"] == "mysqli_query"
    assert result[0]["sql_argument"] == "$sql"


def test_generate_unresolved_report_file(tmp_path):
    queries = [
        {
            "query_id": "Q006",
            "source_file": "06_dynamic_function.php",
            "source_line": 8,
            "executor": "mysqli_query",
            "sql_argument": "$sql",
            "recovered_sql": (
                "{{UNRESOLVED:"
                "buildQuery($table, $condition)}}"
            ),
            "operation": None,
            "status": "UNRESOLVED",
            "reason": (
                "Query contains unsupported "
                "dynamic expression."
            ),
        }
    ]

    queries_file = tmp_path / "queries.json"
    output_file = tmp_path / "unresolved_queries.json"

    queries_file.write_text(
        json.dumps(queries),
        encoding="utf-8",
    )

    result = generate_unresolved_report(
        queries_file,
        output_file,
    )

    assert output_file.exists()
    assert len(result) == 1

    saved = json.loads(
        output_file.read_text(
            encoding="utf-8",
        )
    )

    assert saved[0]["query_id"] == "Q006"

    assert (
        saved[0]["action"]
        == "manual_review_required"
    )


def test_empty_report_when_all_queries_recovered():
    queries = [
        {
            "query_id": "Q001",
            "status": "RECOVERED",
            "recovered_sql": "SELECT id FROM users",
        }
    ]

    result = build_unresolved_report(queries)

    assert result == []

def test_empty_report_when_all_queries_recovered():
    queries = [
        {
            "query_id": "Q001",
            "status": "RECOVERED",
            "recovered_sql": "SELECT id FROM users",
        }
    ]

    result = build_unresolved_report(queries)

    assert result == []


def test_unresolved_reason_code_is_preserved():
    queries = [
        {
            "query_id": "Q005",
            "source_file": "dynamic.php",
            "source_line": 10,
            "status": "UNRESOLVED",
            "reason_code": "BRANCHING",
            "reason": "SQL depends on conditional flow.",
        }
    ]

    result = build_unresolved_report(queries)

    assert len(result) == 1
    assert result[0]["reason_code"] == "BRANCHING"
    assert result[0]["action"] == "manual_review_required"


def test_missing_reason_code_uses_fallback():
    queries = [
        {
            "query_id": "Q006",
            "source_file": "dynamic.php",
            "source_line": 15,
            "status": "UNRESOLVED",
            "reason": "Unsupported dynamic expression.",
        }
    ]

    result = build_unresolved_report(queries)

    assert result[0]["reason_code"] == "UNKNOWN_EXPR"

def test_missing_reason_code_uses_fallback():
    queries = [
        {
            "query_id": "Q006",
            "source_file": "dynamic.php",
            "source_line": 15,
            "status": "UNRESOLVED",
            "reason": "Unsupported dynamic expression.",
        }
    ]

    result = build_unresolved_report(queries)

    assert result[0]["reason_code"] == "UNKNOWN_EXPR"