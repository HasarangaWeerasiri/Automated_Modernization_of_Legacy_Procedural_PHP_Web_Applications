"""End-to-end AST tests for Q014 unsupported operations."""

from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.mapping.branch_recovery import recover_simple_if_else_candidate


BASE_PHP = """<?php
$activeOnly = isset($_GET['active']);
if ($activeOnly) {
    $sql = "SELECT id, full_name FROM doctors WHERE active = 1";
} else {
    $sql = "SELECT id, full_name FROM doctors";
}
mysqli_query($conn, $sql);
"""


@pytest.mark.parametrize(
    "operation,expected_candidate",
    [
        ("", True),
        ("unset($activeOnly);\n", False),
        ("unset($sql);\n", False),
        ("$sql =& $otherSql;\n", False),
        ("$activeOnly =& $otherFlag;\n", False),
    ],
)
def test_q014_unsupported_php_operations(
    tmp_path: Path,
    operation: str,
    expected_candidate: bool,
):
    php = BASE_PHP.replace(
        "mysqli_query($conn, $sql);",
        operation + "mysqli_query($conn, $sql);",
    )

    php_file = tmp_path / "branch_test.php"
    php_file.write_text(php, encoding="utf-8")

    ast_data = analyze_php_ast(php_file)
    recovered = recover_queries_from_ast(ast_data)

    assert len(recovered) == 1
    assert recovered[0]["status"] == "UNRESOLVED"

    candidate = recover_simple_if_else_candidate(
        recovered[0],
        ast_data["assignments"],
        ast_data["unsupported_operations"],
    )

    assert (candidate is not None) == expected_candidate

    if candidate is not None:
        assert candidate["safe_for_conversion"] is False
