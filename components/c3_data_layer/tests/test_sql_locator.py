from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast


BENCHMARK_PATH = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "mixed_crud"
    / "legacy"
)


def test_locator_finds_all_sql_calls():
    result = analyze_php_ast(BENCHMARK_PATH)

    assert len(result["sql_calls"]) == 6


def test_locator_detects_mysqli_query():
    result = analyze_php_ast(BENCHMARK_PATH)

    executors = [
        call["executor"]
        for call in result["sql_calls"]
    ]

    assert executors == ["mysqli_query"] * 6


def test_locator_captures_assignments():
    result = analyze_php_ast(BENCHMARK_PATH)

    assert len(result["assignments"]) == 17


def test_locator_detects_concat_assignment():
    result = analyze_php_ast(BENCHMARK_PATH)

    concat_assignments = [
        assignment
        for assignment in result["assignments"]
        if assignment["assignment_type"]
        == "CONCAT_ASSIGN"
    ]

    assert len(concat_assignments) == 1
    assert concat_assignments[0]["variable"] == "$sql"