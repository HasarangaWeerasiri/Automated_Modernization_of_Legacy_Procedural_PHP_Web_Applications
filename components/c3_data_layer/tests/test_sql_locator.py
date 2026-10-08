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


def test_cross_file_function_metadata_is_extracted():
    from pathlib import Path
    from c3.mapping.sql_locator import analyze_php_ast

    php_directory = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    data = analyze_php_ast(php_directory)

    assert len(data["sql_calls"]) == 21
    assert len(data["functions"]) == 1

    function = data["functions"][0]

    assert function["name"] == "makeDoctorQuery"
    assert function["parameters"] == ["departmentId"]
    assert function["simple_return"] is True
    assert Path(function["source_file"]).name == "query_factory.php"


def test_cross_file_function_return_expression_is_preserved():
    from pathlib import Path
    from c3.mapping.sql_locator import analyze_php_ast

    php_directory = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    data = analyze_php_ast(php_directory)
    function = data["functions"][0]

    assert function["return_expression"] == (
        '"SELECT id, full_name FROM doctors WHERE department_id = "'
        ' . $departmentId'
    )


def test_q015_function_call_argument_metadata():
    from pathlib import Path
    from c3.mapping.sql_locator import analyze_php_ast

    root = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    data = analyze_php_ast(root)

    assignment = next(
        item for item in data["assignments"]
        if Path(item["source_file"]).name == "15_cross_file.php"
        and item["variable"] == "$sql"
    )

    function_call = assignment["function_call"]

    assert function_call["name"] == "makeDoctorQuery"
    assert len(function_call["arguments"]) == 1

    argument = function_call["arguments"][0]

    assert argument["kind"] == "COALESCE"
    assert argument["input_source"] == "_GET"
    assert argument["input_key"] == "department_id"
    assert argument["default_expression"] == "'1'"
