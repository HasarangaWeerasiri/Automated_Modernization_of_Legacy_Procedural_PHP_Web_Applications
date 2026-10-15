"""No silent drops: every database execution call yields exactly one row.

Fixtures are written for this test only. Each PHP file contains one call.
"""

from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


ROOT = Path(__file__).resolve().parents[3]

# file name -> (PHP body, expected executor, expected status, expected reason code)
CALLS = {
    "a_mysqli_proc.php": ('$r = mysqli_query($conn, "SELECT id FROM items WHERE id = $id");', "mysqli_query", "RECOVERED", None),
    "b_mysqli_oo.php": ('$r = $mysqli->query("SELECT id FROM items WHERE id = " . $id);', "query", "RECOVERED", None),
    "c_mysql_legacy.php": ('$r = mysql_query("SELECT id FROM items WHERE id = \'$id\'");', "mysql_query", "RECOVERED", None),
    "d_pdo_query.php": ('$r = $pdo->query("SELECT id FROM items");', "query", "RECOVERED", None),
    "e_pdo_prepare_pos.php": ('$s = $pdo->prepare("SELECT id FROM items WHERE id = ?");', "prepare", "RECOVERED", None),
    "f_pdo_prepare_named.php": ('$s = $pdo->prepare("UPDATE items SET name = :name WHERE id = :id");', "prepare", "RECOVERED", None),
    "g_mysqli_prepare_proc.php": ('$s = mysqli_prepare($conn, "SELECT id FROM items WHERE id = ?");', "mysqli_prepare", "RECOVERED", None),
    "h_mysqli_prepare_oo.php": ('$s = $mysqli->prepare("DELETE FROM items WHERE id = ?");', "prepare", "RECOVERED", None),
    "i_pdo_exec.php": ('$pdo->exec("DELETE FROM items WHERE qty = 0");', "exec", "RECOVERED", None),
    "j_mysqli_multi.php": ('mysqli_multi_query($conn, "SELECT id FROM items; SELECT id FROM orders");', "mysqli_multi_query", "UNRESOLVED", "MULTI_STATEMENT_EXECUTOR"),
    "k_mysqli_real.php": ('mysqli_real_query($conn, "SELECT id FROM items");', "mysqli_real_query", "RECOVERED", None),
    "l_stmt_prepare.php": ('mysqli_stmt_prepare($stmt, "SELECT id FROM items WHERE id = ?");', "mysqli_stmt_prepare", "RECOVERED", None),
    "m_execute_query.php": ('$r = mysqli_execute_query($conn, "SELECT id FROM items WHERE id = ?", [$id]);', "mysqli_execute_query", "RECOVERED", None),
    "n_oo_multi.php": ('$mysqli->multi_query("DELETE FROM items; DELETE FROM orders");', "multi_query", "UNRESOLVED", "MULTI_STATEMENT_EXECUTOR"),
    "o_named_args_reordered.php": ('$r = mysqli_query(query: "SELECT id FROM items", mysql: $conn);', "mysqli_query", "RECOVERED", None),
    "p_named_args_in_order.php": ('$r = mysqli_query(mysql: $conn, query: "SELECT id FROM items");', "mysqli_query", "RECOVERED", None),
    "q_postgres.php": ('$r = pg_query($pg, "SELECT id FROM items");', "pg_query", "UNRESOLVED", "UNSUPPORTED_EXECUTOR"),
    "r_spread_args.php": ('$r = mysqli_query(...$args);', "mysqli_query", "UNRESOLVED", "MISSING_SQL_ARGUMENT"),
}


@pytest.fixture(scope="module")
def recovered(tmp_path_factory):
    directory = tmp_path_factory.mktemp("executors")

    for name, (body, *_rest) in CALLS.items():
        (directory / name).write_text("<?php\n" + body + "\n", encoding="utf-8")

    rows = recover_queries_from_ast(analyze_php_ast(directory))
    return {Path(row["source_file"]).name: row for row in rows}, rows


def test_every_call_yields_exactly_one_row(recovered):
    by_file, rows = recovered

    assert len(rows) == len(CALLS)
    assert set(by_file) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
def test_executor_status_and_reason(recovered, name):
    by_file, _rows = recovered
    _body, executor, status, reason_code = CALLS[name]
    row = by_file[name]

    assert row["executor"] == executor
    assert row["status"] == status, row
    assert row["source_line"] == 2

    if status == "UNRESOLVED":
        assert row["reason_code"] == reason_code
        assert row["reason"]


def test_named_argument_sql_is_recovered(recovered):
    by_file, _rows = recovered

    assert by_file["o_named_args_reordered.php"]["recovered_sql"] == "SELECT id FROM items"


def test_clinic_prepared_statement_site_is_detected():
    legacy = ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard" / "legacy"

    rows = recover_queries_from_ast(analyze_php_ast(legacy))
    files = {Path(row["source_file"]).name for row in rows}

    assert "22_prepared_statement.php" in files
    assert len(rows) == 22
