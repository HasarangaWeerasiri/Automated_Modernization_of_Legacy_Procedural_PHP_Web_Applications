"""An interpolated variable that supplies SQL text (a clause fragment) is not a value.

Synthetic fixtures only. The rule only turns sites UNRESOLVED; it inspects the
literals that ARE the assigned value (whole right-hand side, or the
alternatives of a ternary, ?: or ??, or both branches of an if/else), never
array keys or function arguments.
"""

import pytest

from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import _value_literals, recover_queries_from_ast

WRAPPER = "<?php\nfunction run($sql) { global $conn; return mysqli_query($conn, $sql); }\n"


def site(tmp_path, page, *, wrapper=False):
    if wrapper:
        (tmp_path / "lib.php").write_text(WRAPPER, encoding="utf-8")
    (tmp_path / "page.php").write_text("<?php\n" + page + "\n", encoding="utf-8")
    queries = recover_queries_from_ast(analyze_php_ast(tmp_path))
    converted = {c["query_id"]: c for c in convert_queries(queries)}
    target = [q for q in queries if q["source_file"].endswith("page.php")][-1]
    return target, converted[target["query_id"]]


TERNARY_WHERE = "$where = !empty($s) ? \"WHERE name LIKE '%\" . esc($s) . \"%'\" : \"\";\n"


def test_ternary_where_fragment_unresolved_direct_site(tmp_path):
    query, _ = site(tmp_path, TERNARY_WHERE + 'mysqli_query($conn, "SELECT id FROM t $where ORDER BY id");')

    assert query["status"] == "UNRESOLVED" and query["reason_code"] == "STRUCTURAL"
    assert "$where" in query["reason"]


def test_ternary_where_fragment_unresolved_wrapper_call(tmp_path):
    query, _ = site(tmp_path, TERNARY_WHERE + 'run("SELECT id FROM t $where ORDER BY id");', wrapper=True)

    assert query.get("wrapper") and query["status"] == "UNRESOLVED" and query["reason_code"] == "STRUCTURAL"


def test_if_else_selected_fragment_unresolved(tmp_path):
    page = 'if ($x) { $f = " AND active = 1"; } else { $f = ""; }\nmysqli_query($conn, "SELECT id FROM t WHERE a = 1 $f");'
    query, _ = site(tmp_path, page)

    assert query["status"] == "UNRESOLVED" and query["reason_code"] == "STRUCTURAL"


def test_quoted_value_interpolation_still_recovered(tmp_path):
    query, converted = site(tmp_path, "$id = $_GET['id'];\nmysqli_query($conn, \"SELECT name FROM t WHERE id = '$id'\");")

    assert query["status"] == "RECOVERED"
    assert converted["parameterized_sql"] == "SELECT name FROM t WHERE id = :id"


def test_limit_offset_from_request_keys_still_recovered(tmp_path):
    # Regression for the false trigger on the array key 'limit' (first attempt).
    page = ("$limit = $_GET['limit'] ?? '10';\n$offset = $_GET['offset'] ?? '0';\n"
            'mysqli_query($conn, "SELECT id FROM t LIMIT $limit OFFSET $offset");')
    query, converted = site(tmp_path, page)

    assert query["status"] == "RECOVERED"
    assert converted["status"] == "CONVERTED"


@pytest.mark.parametrize("key", ["where", "order", "limit", "and"])
def test_request_keys_named_like_sql_never_trigger(tmp_path, key):
    page = (f"$v = isset($_POST['{key}']) ? $_POST['{key}'] : esc($_GET['{key}']);\n"
            "mysqli_query($conn, \"SELECT name FROM t WHERE code = '$v'\");")
    query, _ = site(tmp_path, page)

    assert query["status"] == "RECOVERED"


def test_identifier_interpolation_as_before(tmp_path):
    query, _ = site(tmp_path, "$table = $_GET['t'];\nmysqli_query($conn, \"SELECT id FROM $table\");")

    assert query["status"] == "UNRESOLVED"
    assert query["reason"] == "Dynamic table identifier cannot be parameterized safely."


def test_dot_concatenated_fragment_unresolved(tmp_path):
    page = TERNARY_WHERE + 'mysqli_query($conn, "SELECT id FROM t" . $where);'
    query, _ = site(tmp_path, page)

    assert query["status"] == "UNRESOLVED" and query["reason_code"] == "STRUCTURAL"


def test_value_literals_ignore_keys_and_arguments():
    assert _value_literals("$_GET['limit'] ?? '10'") == ["10"]
    assert _value_literals("esc($_POST['where'])") == []
    assert _value_literals('$x ? ($y ? "WHERE a" : "AND b") : ""') == ["WHERE a", "AND b", ""]


def test_fragment_starting_with_line_break_and_indentation_is_detected(tmp_path):
    # The literal begins with a real line break, which the PHP printer writes as \r\n.
    page = ('$f = !empty($s) ? "\r\n        WHERE name LIKE \'%" . esc($s) . "%\'" : "";\n'
            'mysqli_query($conn, "SELECT id FROM t $f ORDER BY id");')
    query, _ = site(tmp_path, page)

    assert query["status"] == "UNRESOLVED" and query["reason_code"] == "STRUCTURAL"


def test_ordinary_value_with_line_break_is_not_a_fragment(tmp_path):
    page = '$note = $x ? "\r\n  pending review" : "done";\nmysqli_query($conn, "SELECT id FROM t WHERE note = \'$note\'");'
    query, _ = site(tmp_path, page)

    assert query["status"] == "RECOVERED"


def test_leading_whitespace_alone_does_not_trigger(tmp_path):
    page = '$pad = $x ? "\r\n    " : "  ";\nmysqli_query($conn, "SELECT id FROM t WHERE code = \'$pad\'");'
    query, _ = site(tmp_path, page)

    assert query["status"] == "RECOVERED"
