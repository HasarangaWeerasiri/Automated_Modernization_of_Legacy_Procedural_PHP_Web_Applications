"""User-defined SQL wrapper calls (`function run($sql) { return mysqli_query($c, $sql); }`).

Synthetic fixtures only (no benchmark app). A wrapper call is an SQL site at
the call's file and line; everything ambiguous abstains (not a wrapper).
"""

import json
import subprocess
from pathlib import Path

from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast

EXTRACTOR = Path(__file__).resolve().parents[1] / "tools" / "php_ast" / "extract_sql_calls.php"

WRAPPER = "<?php\nfunction run($sql) {\n    global $conn;\n    return mysqli_query($conn, $sql);\n}\n"


def write(tmp_path, files):
    for name, code in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return tmp_path


def extract(root):
    completed = subprocess.run(["php", str(EXTRACTOR), str(root)], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def recover(root):
    queries = recover_queries_from_ast(analyze_php_ast(root))
    return queries, convert_queries(queries)


def wrapper_sites(queries):
    return [q for q in queries if q.get("wrapper")]


def test_wrapper_calls_are_sites_at_the_call(tmp_path):
    root = write(tmp_path, {
        "lib.php": WRAPPER,
        "pages/list.php": "<?php\n$rows = run(\"SELECT id, name FROM items\");\n",
        "pages/find.php": "<?php\n$id = $_GET['id'];\n$row = run(\"SELECT id FROM items WHERE id = '$id'\");\n",
    })
    queries, converted = recover(root)
    sites = {Path(q["source_file"]).name: q for q in wrapper_sites(queries)}

    assert set(sites) == {"list.php", "find.php"}
    assert sites["list.php"]["source_line"] == 2 and sites["find.php"]["source_line"] == 3
    assert sites["find.php"]["executor"] == "mysqli_query"
    assert sites["find.php"]["wrapper"]["name"] == "run"
    by_id = {c["query_id"]: c for c in converted}
    assert by_id[sites["find.php"]["query_id"]]["parameterized_sql"] == "SELECT id FROM items WHERE id = :id"
    assert by_id[sites["list.php"]["query_id"]]["status"] == "CONVERTED"


def test_wrapper_internal_executor_line_stays_unresolved(tmp_path):
    root = write(tmp_path, {"lib.php": WRAPPER, "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n"})
    queries, _ = recover(root)
    internal = [q for q in queries if Path(q["source_file"]).name == "lib.php"]

    assert len(internal) == 1 and internal[0]["status"] == "UNRESOLVED"


def test_direct_site_ids_come_first(tmp_path):
    root = write(tmp_path, {
        "lib.php": WRAPPER,
        "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\nmysqli_query($conn, \"SELECT 2 FROM t\");\n",
    })
    queries, _ = recover(root)

    assert [bool(q.get("wrapper")) for q in queries] == [False, False, True]


def test_identical_definitions_in_two_files_are_one_wrapper(tmp_path):
    root = write(tmp_path, {"admin/lib.php": WRAPPER, "lib.php": WRAPPER, "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n"})
    scan = extract(root)["sql_wrappers"]

    assert list(scan["wrappers"]) == ["run"] and len(scan["wrappers"]["run"]["definitions"]) == 2
    assert len([c for c in extract(root)["sql_calls"] if c.get("wrapper")]) == 1


def test_wrapper_sql_injection_value_is_bound(tmp_path):
    root = write(tmp_path, {
        "lib.php": WRAPPER,
        "a.php": "<?php\n$n = $_POST['name'];\nrun(\"DELETE FROM items WHERE name = '\" . $n . \"'\");\n",
    })
    queries, converted = recover(root)
    site = wrapper_sites(queries)[0]
    conversion = next(c for c in converted if c["query_id"] == site["query_id"])

    assert conversion["parameterized_sql"] == "DELETE FROM items WHERE name = :n"
    assert conversion["parameters"] == ["n"]


# ---------------------------------------------------------------- negatives

def assert_not_a_wrapper(root, reason=None):
    output = extract(root)
    assert not [c for c in output["sql_calls"] if c.get("wrapper")]
    if reason is None:
        assert "sql_wrappers" not in output
    else:
        assert not output["sql_wrappers"]["wrappers"]  # PHP encodes an empty map as []
        assert next(iter(output["sql_wrappers"]["rejected"].values()))["reason"] == reason


def test_parameter_concatenated_into_larger_string_is_not_a_wrapper(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "lib.php": "<?php\nfunction run($where) { global $c; return mysqli_query($c, \"SELECT * FROM t WHERE \" . $where); }\n",
        "a.php": "<?php\nrun(\"id = 1\");\n",
    }))


def test_variable_function_call_is_not_detected(tmp_path):
    root = write(tmp_path, {"lib.php": WRAPPER, "a.php": "<?php\n$f = 'run';\n$f(\"SELECT 1 FROM t\");\n"})
    output = extract(root)

    assert list(output["sql_wrappers"]["wrappers"]) == ["run"]       # the definition is a wrapper,
    assert not [c for c in output["sql_calls"] if c.get("wrapper")]  # but no direct call exists


def test_dynamic_method_name_is_not_detected(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "lib.php": "<?php\nclass Db { function run($sql) { return mysqli_query($this->c, $sql); } }\n",
        "a.php": "<?php\n$m = 'run';\n$db = new Db();\n$db->$m(\"SELECT 1 FROM t\");\n",
    }))


def test_conditional_definition_is_not_a_wrapper(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "lib.php": "<?php\nif (!function_exists('run')) {\n  function run($sql) { global $c; return mysqli_query($c, $sql); }\n}\n",
        "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n",
    }), reason="defined conditionally or inside a namespace/class")


def test_definitions_with_different_bodies_are_not_a_wrapper(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "admin/lib.php": WRAPPER,
        "lib.php": "<?php\nfunction run($sql) { global $conn; log_it($sql); return mysqli_query($conn, $sql); }\n",
        "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n",
    }), reason="defined more than once with different bodies")


def test_recursive_function_is_not_a_wrapper(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "lib.php": "<?php\nfunction run($sql, $n = 0) { global $c; if ($n > 0) { return run($sql, $n - 1); } return mysqli_query($c, $sql); }\n",
        "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n",
    }), reason="recursive")


def test_modified_parameter_is_not_a_wrapper(tmp_path):
    assert_not_a_wrapper(write(tmp_path, {
        "lib.php": "<?php\nfunction run($sql) { global $c; $sql = trim($sql); return mysqli_query($c, $sql); }\n",
        "a.php": "<?php\nrun(\"SELECT 1 FROM t\");\n",
    }), reason="SQL parameter is modified before execution")


def test_function_using_parameter_inside_sql_behaves_as_before(tmp_path):
    root = write(tmp_path, {
        "lib.php": "<?php\nfunction find($id) { global $c; return mysqli_query($c, \"SELECT name FROM t WHERE id = $id\"); }\n",
        "a.php": "<?php\nfind(5);\n",
    })
    output = extract(root)
    queries, converted = recover(root)

    assert "sql_wrappers" not in output and len(output["sql_calls"]) == 1
    assert len(queries) == 1 and "wrapper" not in queries[0]
    assert Path(queries[0]["source_file"]).name == "lib.php"
    assert queries[0]["recovered_sql"] == "SELECT name FROM t WHERE id = {{id}}"
    assert converted[0]["status"] == "CONVERTED"
