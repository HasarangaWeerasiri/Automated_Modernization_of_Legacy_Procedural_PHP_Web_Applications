"""H1: array elements with literal keys are recovered as named values.

General rule (no app or query IDs): $_GET['ID'], $_SESSION["pid"], $row['id'],
$a[0] in a concatenation or in a double-quoted string become placeholders
named <array>_<key>. Dynamic keys, nested elements and object properties
still abstain. Single-quoted PHP strings are literal (no interpolation).
Fixtures are written for this test only.
"""

import pytest

from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


def recover(tmp_path, statement):
    (tmp_path / "page.php").write_text("<?php\n" + statement + "\n", encoding="utf-8")
    queries = recover_queries_from_ast(analyze_php_ast(tmp_path))
    assert len(queries) == 1
    return queries[0], convert_queries(queries)[0]


@pytest.mark.parametrize("statement,sql,variables,sources", [
    ("""mysqli_query($c, "UPDATE t SET s='0' WHERE ID = '".$_GET['ID']."'");""",
     "UPDATE t SET s='0' WHERE ID = :GET_ID", ["GET_ID"], {"GET_ID": "$_GET['ID']"}),
    ("""mysqli_query($c, "SELECT a FROM t WHERE p = '{$_SESSION['pid']}' AND q = '$row[id]'");""",
     "SELECT a FROM t WHERE p = :SESSION_pid AND q = :row_id", ["SESSION_pid", "row_id"],
     {"SESSION_pid": "$_SESSION['pid']", "row_id": "$row['id']"}),
    ("""mysqli_query($c, "SELECT a FROM t WHERE id = " . $_POST["id"]);""",
     "SELECT a FROM t WHERE id = :POST_id", ["POST_id"], {"POST_id": "$_POST[\"id\"]"}),
    ("""mysqli_query($c, "SELECT a FROM t WHERE id = " . $ids[0]);""",
     "SELECT a FROM t WHERE id = :ids_0", ["ids_0"], {"ids_0": "$ids[0]"}),
])
def test_array_elements_become_named_values(tmp_path, statement, sql, variables, sources):
    query, converted = recover(tmp_path, statement)

    assert query["status"] == "RECOVERED"
    assert query["dynamic_variables"] == variables
    assert query["dynamic_variable_sources"] == sources
    assert converted["status"] == "CONVERTED"
    assert converted["parameterized_sql"] == sql


@pytest.mark.parametrize("statement", [
    """mysqli_query($c, "SELECT a FROM t WHERE id = " . $_GET[$key]);""",          # dynamic key
    """mysqli_query($c, "SELECT a FROM t WHERE id = " . $data['user']['id']);""",  # nested element
    """mysqli_query($c, "SELECT a FROM t WHERE id = " . $user->id);""",            # object property
    """mysqli_query($c, "SELECT a FROM t ORDER BY " . $_GET['sort']);""",          # structural position
])
def test_unsupported_shapes_still_abstain(tmp_path, statement):
    query, converted = recover(tmp_path, statement)

    assert converted["status"] == "UNRESOLVED"


def test_single_quoted_php_string_is_not_interpolated(tmp_path):
    query, _ = recover(tmp_path, """mysqli_query($c, 'SELECT a FROM t WHERE note = "$x"');""")

    assert query["dynamic_variables"] == []
    assert query["recovered_sql"] == 'SELECT a FROM t WHERE note = "$x"'
