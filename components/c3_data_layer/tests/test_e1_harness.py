"""E1 harness (scripts/equivalence): slicer, comparison and generated-side call.

No MySQL needed: the PHP side is used in build-only mode and the generated
side runs on SQLite. Fixtures are written for this test only.
"""

import datetime
import decimal
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "equivalence"))

import compare  # noqa: E402
import migrated_runner  # noqa: E402

RUNNER = ROOT / "scripts" / "equivalence" / "legacy_runner.php"
needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="php not on PATH")

PHP = r"""<?php
include 'config.php';
$id = mysqli_real_escape_string($conn, $_GET['id']);
$status = 'open';
if ($_GET['all']) { $status = 'any'; }
$sql = "SELECT id, title FROM tickets WHERE id = '$id'";
$sql .= " AND status = '$status'";
$r = mysqli_query($conn, $sql);

$row = mysqli_fetch_assoc($r);
$name = trim($_POST['name']);
mysqli_query($conn, "UPDATE tickets SET title = '" . $name . "' WHERE id = " . intval($row['id']));

function load($db, $owner) {
    $q = "SELECT * FROM tickets WHERE owner = '" . addslashes($owner) . "'";
    return $db->query($q);
}
mysqli_query($conn, "SELECT * FROM tickets WHERE id = " . lookup_id());
"""


def run_php(*args):
    completed = subprocess.run(["php", str(RUNNER), *args], capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def php_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("e1") / "page.php"
    path.write_text(PHP, encoding="utf-8")
    return path


def build(php_file, tmp_path, line, call, mode, inputs):
    spec = tmp_path / f"spec_{mode}.json"
    spec.write_text(json.dumps({"file": str(php_file), "line": line, "call": call, "mode": mode,
                                "inputs": inputs, "execute": False}), encoding="utf-8")
    return run_php("run", str(spec))


@needs_php
def test_slice_keeps_straight_line_statements_and_lists_inputs(php_file):
    sliced = run_php("slice", str(php_file), "8", "mysqli_query")

    assert sliced["status"] == "ok"
    assert [s["line"] for s in sliced["statements"]] == [3, 6, 7]
    names = {i["name"]: i for i in sliced["inputs"]}
    assert set(names) == {"$_GET['id']", "$status"}
    assert "control flow" in names["$status"]["note"]
    assert sliced["connection_variables"] == ["conn"]


@needs_php
def test_variable_from_app_call_becomes_array_input(php_file):
    sliced = run_php("slice", str(php_file), "12", "mysqli_query")

    names = {i["name"]: i for i in sliced["inputs"]}
    assert names["$row"]["kind"] == "array" and names["$row"]["keys"] == ["id"]
    assert "mysqli_fetch_assoc" in names["$row"]["note"]
    assert "$_POST['name']" in names


@needs_php
def test_function_scope_parameters_are_inputs(php_file):
    sliced = run_php("slice", str(php_file), "16", "->query")

    assert sliced["scope"] == "function load"
    assert [i["name"] for i in sliced["inputs"]] == ["$owner"]


@needs_php
def test_app_function_in_sql_expression_is_not_rebuilt(php_file):
    sliced = run_php("slice", str(php_file), "18", "mysqli_query")

    assert sliced["status"] == "sql_expression_not_pure"


@needs_php
def test_legacy_mode_escapes_and_raw_mode_does_not(php_file, tmp_path):
    inputs = {"$_GET['id']": "1' OR '1'='1", "$status": "open"}

    legacy = build(php_file, tmp_path, 8, "mysqli_query", "legacy", inputs)
    raw = build(php_file, tmp_path, 8, "mysqli_query", "raw", inputs)

    assert legacy["sql"] == "SELECT id, title FROM tickets WHERE id = '1\\' OR \\'1\\'=\\'1' AND status = 'open'"
    assert raw["variables"]["id"] == "1' OR '1'='1"


@needs_php
def test_missing_input_is_reported(php_file, tmp_path):
    result = build(php_file, tmp_path, 8, "mysqli_query", "legacy", {"$_GET['id']": "1"})

    assert result["status"] == "missing_input" and result["missing"] == "$status"


def test_mysql_text_normalisation():
    assert compare.mysql_text(5) == "5"
    assert compare.mysql_text(decimal.Decimal("10.50")) == "10.50"
    assert compare.mysql_text(datetime.datetime(2026, 10, 15, 9, 5)) == "2026-10-15 09:05:00"
    assert compare.mysql_text(datetime.timedelta(hours=26, minutes=3)) == "26:03:00"
    assert compare.mysql_text(2.0) == "2"
    assert compare.mysql_text(None) is None


def _legacy(rows=None, **extra):
    base = {"status": "executed", "sql": "SELECT a FROM t", "checksums_after": {"t": 1}}
    if rows is not None:
        base.update(columns=["a"], rows=rows)
    return base | extra


def _migrated(rows=None, **extra):
    base = {"status": "ok", "checksums_after": {"t": 1}}
    if rows is not None:
        base.update(columns=["a"], rows=rows)
    return base | extra


def test_compare_pass_unordered_and_fail_on_rows():
    assert compare.compare_case("normal", _legacy([["1"], ["2"]]), _migrated([[2], [1]]), {})["verdict"] == "PASS"
    assert compare.compare_case("normal", _legacy([["1"]]), _migrated([[2]]), {})["verdict"] == "FAIL"


def test_compare_order_by_is_respected():
    legacy = _legacy([["1"], ["2"]], sql="SELECT a FROM t ORDER BY a")
    assert compare.compare_case("normal", legacy, _migrated([[2], [1]]), {})["verdict"] == "FAIL"


def test_compare_writes_use_affected_rows_and_table_state():
    legacy = {"status": "executed", "sql": "UPDATE t", "affected_rows": 1, "insert_id": 0, "checksums_after": {"t": 5}}
    same = {"status": "ok", "affected_rows": 1, "insert_id": 0, "checksums_after": {"t": 5}}
    other = same | {"checksums_after": {"t": 6}}

    assert compare.compare_case("normal", legacy, same, {})["verdict"] == "PASS"
    assert compare.compare_case("normal", legacy, other, {})["verdict"] == "FAIL"


def test_compare_hostile_and_inconclusive_cases():
    payload = {"$_GET['id']": "' OR '1'='1"}
    legacy = _legacy([["1"], ["2"]], sql="SELECT a FROM t WHERE id = '' OR '1'='1'")

    assert compare.compare_case("hostile_quote", legacy, _migrated([]), payload)["verdict"] == "LEGACY_INJECTABLE"
    assert compare.compare_case("normal", {"status": "sql_error", "error": "1054"}, _migrated([]), {})["verdict"] == "INCONCLUSIVE"
    assert compare.compare_case("normal", _legacy([]), {"status": "unbound_parameter", "missing": ["x"]}, {})["verdict"] == "INCONCLUSIVE"
    assert compare.compare_case("normal", _legacy([["1"]]), {"status": "error", "error": "boom"}, {})["verdict"] == "FAIL"


def test_site_verdict_rules():
    assert compare.site_verdict([{"verdict": "PASS"}, {"verdict": "LEGACY_INJECTABLE"}]) == "PASS"
    assert compare.site_verdict([{"verdict": "PASS"}, {"verdict": "FAIL"}]) == "FAIL"
    assert compare.site_verdict([{"verdict": "PASS"}, {"verdict": "INCONCLUSIVE"}]) == "INCONCLUSIVE"


def test_generated_function_is_called_with_bound_php_variables(tmp_path):
    (tmp_path / "data_access.py").write_text(
        "from sqlalchemy import text\n\n"
        "def select_items_q001(session, name):\n"
        "    return session.execute(text('SELECT id FROM items WHERE name = :name'), {'name': name}).mappings().all()\n\n"
        "def unresolved_query_q002(session, **kwargs):\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO items VALUES (1, 'a'), (2, 'b')"))

    module = migrated_runner.load_module(tmp_path / "data_access.py")
    function = migrated_runner.function_for(module, "Q001")

    assert migrated_runner.function_for(module, "Q002") is None
    assert migrated_runner.call(function, engine, {"name": "b", "other": 1})["rows"] == [[2]]
    assert migrated_runner.call(function, engine, {})["status"] == "unbound_parameter"
    engine.dispose()
