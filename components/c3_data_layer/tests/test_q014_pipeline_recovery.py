"""Q014 integration tests for main C3 SQL recovery."""

from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "clinic_inventory_legacy_hard"
    / "legacy"
)


def test_q014_diagnostic_candidate_in_main_recovery():
    ast_data = analyze_php_ast(LEGACY_DIR)
    queries = recover_queries_from_ast(ast_data)

    assert len(queries) == 21

    q014 = next(
        q for q in queries
        if q["query_id"] == "Q014"
    )

    assert q014["status"] == "UNRESOLVED"
    assert q014["reason_code"] == "BRANCHING"

    candidate = q014["conditional_candidate"]

    assert candidate["query_id"] == "Q014"
    assert candidate["unsupported_operations_checked"] is True
    assert candidate["safe_for_conversion"] is False
    assert candidate["validation_status"] == "STRUCTURAL_CANDIDATE"

    assert candidate["condition"]["kind"] == "ISSET"
    assert candidate["condition"]["input_key"] == "active"

    assert candidate["branches"] == {
        "IF": (
            "SELECT id, full_name FROM doctors "
            "WHERE active = 1"
        ),
        "ELSE": "SELECT id, full_name FROM doctors",
    }


def test_q014_guarded_conversion_in_main_pipeline():
    ast_data = analyze_php_ast(LEGACY_DIR)
    queries = recover_queries_from_ast(ast_data)
    converted = convert_queries(queries)

    assert len(converted) == 21

    q014 = next(
        q for q in converted
        if q["query_id"] == "Q014"
    )

    assert q014["status"] == "CONVERTED"
    assert q014["query_shape"] == "CONDITIONAL_SELECT"
    assert q014["operation"] == "SELECT"
    assert q014["table"] == "doctors"

    assert sum(
        q["status"] == "CONVERTED"
        for q in converted
    ) == 18