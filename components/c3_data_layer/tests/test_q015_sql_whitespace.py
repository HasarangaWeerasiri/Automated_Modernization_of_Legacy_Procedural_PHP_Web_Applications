from c3.conversion.query_converter import (
    _parameterize_sql,
    convert_query,
)


def test_q015_sql_whitespace_is_preserved():
    original = (
        "SELECT id, full_name FROM doctors "
        "WHERE department_id = {{departmentId}}"
    )

    expected = (
        "SELECT id, full_name FROM doctors "
        "WHERE department_id = :departmentId"
    )

    assert _parameterize_sql(original) == expected


def test_q015_converted_sql_preserves_where_boundary():
    query = {
        "query_id": "Q015",
        "source_file": "15_cross_file.php",
        "source_line": 6,
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id, full_name FROM doctors "
            "WHERE department_id = {{departmentId}}"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert "WHERE department_id" in result["parameterized_sql"]
    assert "WHEREdepartment_id" not in result["parameterized_sql"]


def test_q015_actual_recovery_to_emitter_preserves_sql():
    from pathlib import Path

    from c3.mapping.sql_locator import analyze_php_ast
    from c3.mapping.sql_recovery import recover_queries_from_ast
    from c3.generation.emitter import _build_function

    root = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    queries = recover_queries_from_ast(analyze_php_ast(root))
    q015 = next(q for q in queries if q["query_id"] == "Q015")

    diagnostics = q015["cross_file_diagnostics"]

    expected_template = (
        "SELECT id, full_name FROM doctors "
        "WHERE department_id = {{departmentId}}"
    )

    assert diagnostics["sql_template"] == expected_template

    candidate = {
        **q015,
        "status": "RECOVERED",
        "operation": "SELECT",
        "recovered_sql": diagnostics["sql_template"],
        "binding_plan": diagnostics["binding_plan"],
    }

    converted = convert_query(candidate)

    expected_sql = (
        "SELECT id, full_name FROM doctors "
        "WHERE department_id = :departmentId"
    )

    assert converted["status"] == "CONVERTED"
    assert converted["parameterized_sql"] == expected_sql

    generated = _build_function(converted)

    assert f"statement = text({expected_sql!r})" in generated
