import json

from c3.analysis.column_usage_generator import (
    generate_column_usage,
)


def test_generate_column_usage_file(tmp_path):
    queries = [
        {
            "query_id": "Q001",
            "source_file": "create_user.php",
            "source_line": 9,
            "recovered_sql": (
                "INSERT INTO users "
                "(name, email, status) "
                "VALUES "
                "('{{name}}', '{{email}}', 'active')"
            ),
            "operation": "INSERT",
            "dynamic_variables": [
                "name",
                "email",
            ],
            "status": "RECOVERED",
            "reason": None,
        },
        {
            "query_id": "Q002",
            "source_file": "delete_user.php",
            "source_line": 8,
            "recovered_sql": (
                "DELETE FROM users "
                "WHERE id = {{id}}"
            ),
            "operation": "DELETE",
            "dynamic_variables": ["id"],
            "status": "RECOVERED",
            "reason": None,
        },
    ]

    queries_file = tmp_path / "queries.json"
    output_file = tmp_path / "column_usage.json"

    queries_file.write_text(
        json.dumps(queries),
        encoding="utf-8",
    )

    result = generate_column_usage(
        queries_file,
        output_file,
    )

    assert output_file.exists()
    assert len(result) == 2

    saved = json.loads(
        output_file.read_text(
            encoding="utf-8",
        )
    )

    assert saved[0]["query_id"] == "Q001"
    assert saved[0]["operation"] == "INSERT"

    assert saved[0]["write_columns"] == [
        "name",
        "email",
        "status",
    ]

    assert saved[1]["query_id"] == "Q002"
    assert saved[1]["operation"] == "DELETE"
    assert saved[1]["filter_columns"] == ["id"]


def test_generator_preserves_unresolved_query(tmp_path):
    queries = [
        {
            "query_id": "Q006",
            "source_file": "dynamic.php",
            "source_line": 20,
            "recovered_sql": (
                "{{UNRESOLVED:"
                "buildQuery($table,$condition)}}"
            ),
            "operation": "UNKNOWN",
            "dynamic_variables": [],
            "status": "UNRESOLVED",
            "reason": (
                "Dynamic function call could not be resolved"
            ),
        }
    ]

    queries_file = tmp_path / "queries.json"
    output_file = tmp_path / "column_usage.json"

    queries_file.write_text(
        json.dumps(queries),
        encoding="utf-8",
    )

    result = generate_column_usage(
        queries_file,
        output_file,
    )

    assert len(result) == 1

    assert result[0]["query_id"] == "Q006"
    assert result[0]["status"] == "UNRESOLVED"
    assert result[0]["tables"] == []
    assert result[0]["read_columns"] == []
    assert result[0]["write_columns"] == []
    assert result[0]["filter_columns"] == []

    assert (
        result[0]["reason"]
        == "Dynamic function call could not be resolved"
    )