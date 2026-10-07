import json

from c3.analysis.traceability import (
    build_traceability,
    generate_traceability_report,
)


def test_recovered_query_is_ready_for_conversion():
    queries = [
        {
            "query_id": "Q001",
            "source_file": "01_select.php",
            "source_line": 4,
            "executor": "mysqli_query",
            "recovered_sql": (
                "SELECT id, title, price FROM products"
            ),
            "operation": "SELECT",
            "status": "RECOVERED",
            "reason": None,
        }
    ]

    usage = [
        {
            "query_id": "Q001",
            "operation": "SELECT",
            "tables": ["products"],
            "read_columns": [
                "id",
                "title",
                "price",
            ],
            "write_columns": [],
            "filter_columns": [],
            "status": "ANALYZED",
            "reason": None,
        }
    ]

    result = build_traceability(
        queries,
        usage,
    )

    assert len(result) == 1

    trace = result[0]

    assert trace["query_id"] == "Q001"
    assert trace["recovery_status"] == "RECOVERED"
    assert trace["analysis_status"] == "ANALYZED"
    assert trace["tables"] == ["products"]

    assert trace["read_columns"] == [
        "id",
        "title",
        "price",
    ]

    assert trace["action"] == "ready_for_conversion"


def test_unresolved_query_requires_manual_review():
    queries = [
        {
            "query_id": "Q006",
            "source_file": "06_dynamic_function.php",
            "source_line": 8,
            "executor": "mysqli_query",
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

    usage = [
        {
            "query_id": "Q006",
            "operation": "UNKNOWN",
            "tables": [],
            "read_columns": [],
            "write_columns": [],
            "filter_columns": [],
            "status": "UNRESOLVED",
            "reason": (
                "Query contains unsupported "
                "dynamic expression."
            ),
        }
    ]

    result = build_traceability(
        queries,
        usage,
    )

    trace = result[0]

    assert trace["recovery_status"] == "UNRESOLVED"
    assert trace["analysis_status"] == "UNRESOLVED"

    assert trace["tables"] == []
    assert trace["read_columns"] == []
    assert trace["write_columns"] == []
    assert trace["filter_columns"] == []

    assert (
        trace["action"]
        == "manual_review_required"
    )


def test_source_traceability_is_preserved():
    queries = [
        {
            "query_id": "Q005",
            "source_file": (
                "..\\..\\benchmarks\\apps\\mixed_crud\\"
                "legacy\\05_update.php"
            ),
            "source_line": 11,
            "executor": "mysqli_query",
            "recovered_sql": (
                "UPDATE products SET title = "
                "'{{title}}' WHERE id = {{id}}"
            ),
            "operation": "UPDATE",
            "status": "RECOVERED",
            "reason": None,
        }
    ]

    usage = [
        {
            "query_id": "Q005",
            "tables": ["products"],
            "read_columns": [],
            "write_columns": ["title"],
            "filter_columns": ["id"],
            "status": "ANALYZED",
            "reason": None,
        }
    ]

    result = build_traceability(
        queries,
        usage,
    )

    trace = result[0]

    assert trace["source_line"] == 11

    assert trace["source_file"].endswith(
        "05_update.php"
    )

    assert trace["write_columns"] == ["title"]
    assert trace["filter_columns"] == ["id"]


def test_traceability_report_file_is_generated(
    tmp_path,
):
    queries = [
        {
            "query_id": "Q001",
            "source_file": "users.php",
            "source_line": 4,
            "executor": "mysqli_query",
            "recovered_sql": (
                "SELECT id FROM users"
            ),
            "operation": "SELECT",
            "status": "RECOVERED",
            "reason": None,
        }
    ]

    usage = [
        {
            "query_id": "Q001",
            "tables": ["users"],
            "read_columns": ["id"],
            "write_columns": [],
            "filter_columns": [],
            "status": "ANALYZED",
            "reason": None,
        }
    ]

    queries_file = tmp_path / "queries.json"
    usage_file = tmp_path / "column_usage.json"
    output_file = tmp_path / "traceability.json"

    queries_file.write_text(
        json.dumps(queries),
        encoding="utf-8",
    )

    usage_file.write_text(
        json.dumps(usage),
        encoding="utf-8",
    )

    result = generate_traceability_report(
        queries_file,
        usage_file,
        output_file,
    )

    assert output_file.exists()
    assert len(result) == 1

    saved = json.loads(
        output_file.read_text(
            encoding="utf-8",
        )
    )

    assert saved[0]["query_id"] == "Q001"

    assert (
        saved[0]["action"]
        == "ready_for_conversion"
    )