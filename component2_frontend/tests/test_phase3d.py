"""Roadmap Phase 3, step 4: the corrections after attendance (boundary-rules v0.15, generation-spec v0.10).

a) R-I8 case 1, a redirect guard, at any depth in a page; b) `X or die(m)` normalised to `if (!X) { die(m); }`
(rules section 1); c) methods of PHP's own mysqli classes are not call sites (rules section 4); d) a
`header_redirect` flag for every literal `header("Location: ...")`. Then the reserve app, warehouse.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import run_apps  # noqa: E402

from src.mapping.loader import load_timeline  # noqa: E402
from tests.test_harness import harness_app, page, pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402
from tests.test_phase3 import abstains, flat, run  # noqa: E402

REDIRECT = "echo '<meta http-equiv=\"refresh\" content=\"0; url=list.php\">'; exit;"


def flags(result: dict, key: str, name: str) -> list:
    return [f for f in result["stage"](key, 5)["flags"] if f["flag"] == name]


def entries(result: dict, key: str, kind: str) -> list:
    return [e for e in result["timelines"][key]["sequence"] if e["kind"] == kind]


# ---------------------------------------------------------------- a) R-I8 case 1 at any depth (v0.15)


def test_a_redirect_guard_nested_in_an_if_with_content_is_a_guard(tmp_path):
    """phpBlog admin/comments.php 19: `if (isset($_GET['edit-id'])) { ... if (!$row) { <redirect>; exit; } ... }`."""
    php = ("<?php if (isset($_GET['edit-id'])) { echo '<h2>Edit</h2>'; if (!$row) { " + REDIRECT + " } "
           "echo '<form>f</form>'; } ?><p>list</p>")
    result = stages(tmp_path, php)
    assert [n for n in result["nodes"] if n["status"] == "abstain"] == []
    assert [(n["type"], n["rule"], n["flags"]) for n in result["nodes"] if n["rule"] == "R-I8"] == [
        ("Static", "R-I8", ["redirect_guard"])]
    text = flat(result["files"]["app/page/page.tsx"])
    assert "<h2>Edit</h2>" in text and "<form>f</form>" in text and "meta" not in text


def test_a_redirect_guard_in_a_loop_is_a_guard(tmp_path):
    php = "<ul><?php foreach ($rows as $r) { if (!$r) { " + REDIRECT + " } echo '<li>x</li>'; } ?></ul>"
    result = stages(tmp_path, php)
    assert "R-I8" in [n["rule"] for n in result["nodes"]] and not [
        n for n in result["nodes"] if n["status"] == "abstain"]


def test_an_exit_arm_that_is_not_a_redirect_still_has_the_narrow_scope(tmp_path):
    php = "<?php if ($a) { echo '<i>x</i>'; if ($b) { echo '<b>stop</b>'; exit; } } ?><p>r</p>"
    result = stages(tmp_path, php)
    assert [(n["reason"], n["rule"]) for n in result["nodes"] if n["status"] == "abstain"][-1] == (
        "exit_arm_not_covered", "R-I8")


# ---------------------------------------------------------------- b) `X or die(m)` is an if (rules v0.15 section 1)


@pytest.mark.parametrize("statement", [
    "mysql_connect('h', 'u', '') or die('Cannot connect');",
    "mysql_connect('h', 'u', '') || die('Cannot connect');",
    "mysql_connect('h', 'u', '') or exit('Cannot connect');",
    "$link = mysql_connect('h', 'u', '') or die('Cannot connect');",
])
def test_an_or_die_in_an_included_file_is_r_i8c_and_flagged_at_the_include(tmp_path, statement):
    """attendance connect.php 3: `mysql_connect(...) or die('Cannot connect to server')`."""
    files = {"connect.php": "<?php\n" + statement + "\n", "page.php": "<?php include 'connect.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert (include["outputsInside"], include["exitOutput"]) == (False, [
        {"file": "legacy-apps/tiny/connect.php", "line": 2}])
    assert abstains(result, "page.php") == []
    (flag,) = flags(result, "page.php", "exit_output_not_generated")
    assert flag["detail"] == ("include: the exit arm at connect.php:2 prints and exits; that output is not "
                              "generated (R-I8c)")
    # connect.php itself prints its message (when requested on its own): an R-I8c arm there too
    assert [(n["type"], n["rule"]) for n in result["nodes"]("connect.php") if n["rule"]] == [("Static", "R-I8c")]


def test_an_or_die_in_a_function_is_flagged_at_its_calls(tmp_path):
    """WackoPicko include/pictures.php 57: `$res = mysql_query($query) or die(mysql_error());`."""
    files = {"lib.php": "<?php function pics() {\n  $res = mysql_query('q') or die(mysql_error());\n  return $res;\n}",
             "page.php": "<?php include 'lib.php'; $p = pics(); ?><p>p</p>"}
    result = run(tmp_path, files)
    (call,) = entries(result, "page.php", "call")
    assert (call["outputsInside"], call["exitOutput"]) == (False, [{"file": "legacy-apps/tiny/lib.php", "line": 2}])
    assert len(flags(result, "page.php", "exit_output_not_generated")) == 1


def test_an_or_die_without_a_message_prints_nothing_and_is_not_flagged(tmp_path):
    files = {"connect.php": "<?php mysql_connect('h') or die();", "page.php": "<?php include 'connect.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert include["outputsInside"] is False and "exitOutput" not in include
    assert "connect.php" in result["silent"]


def test_an_or_die_at_a_page_top_level_is_r_i8_case_2(tmp_path):
    php = "<?php $c = mysql_connect('h') or die('<b>no db</b>'); ?><p>page</p>"
    result = stages(tmp_path, php)
    (die,) = [e for e in result["timeline"]["sequence"] if e["kind"] == "Expr_Exit"]
    assert die["enclosedBy"][0]["condExpr"] == "!($c = mysql_connect('h'))"
    assert [(n["type"], n["rule"]) for n in result["nodes"] if n["rule"]] == [("InlineConditional", "R-I8")]
    text = flat(result["files"]["app/page/page.tsx"])
    assert "<b>no db</b>" in text and "<p>page</p>" in text and "unresolvedCondition" in text


def test_an_or_die_inside_a_loop_is_decided_by_the_rules(tmp_path):
    """phpBlog install/done.php 43, inside `foreach ($sql_dump as $line)`: no longer dropped silently."""
    php = "<?php foreach ($lines as $l) { mysqli_query($db, $l) or die('Problem <b>' . $l . '</b>'); } ?><p>p</p>"
    result = stages(tmp_path, php)
    assert [e["kind"] for e in result["timeline"]["sequence"]] == ["Expr_Exit", "Stmt_InlineHTML"]
    assert [n for n in result["nodes"] if n["status"] == "abstain"]  # the loop has no container: abstains


def test_an_or_die_inside_an_expression_is_not_an_if(tmp_path):
    timeline = page(tmp_path, "<?php $ok = f() || die('x'); echo 'y';")
    assert [e["kind"] for e in timeline["sequence"]] == ["Stmt_Echo"]
    assert [n["kind"] for n in timeline["harnessNotes"] if "exit" in n["kind"]] == ["exit_with_output"]


# ---------------------------------------------------------------- c) built-in mysqli methods (rules v0.15 section 4)


def test_methods_of_a_mysqli_connection_and_its_results_are_not_calls(tmp_path):
    """phpBlog config.tpl 7 `$connect = new mysqli(...)`; core.php 30-34 `$querych = mysqli_query($connect,
    ...)` and, in an arm below it, `$querych->fetch_array()`; admin/add_category.php 8 `$connect->query(...)`."""
    files = {"config.php": "<?php $connect = new mysqli('h', 'u', 'p', 'd');",
             "core.php": "<?php include 'config.php';\n$q = mysqli_query($connect, 'select 1');\n"
                         "if ($q) { $row = $q->fetch_array(); }\n$v = $connect->query('select 2');\n"
                         "$w = $v->fetch_assoc();\n$s = $connect->prepare('x');\n$s->execute();",
             "page.php": "<?php include 'core.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert include["outputsInside"] is False and abstains(result, "page.php") == []
    assert "core.php" in result["silent"]


@pytest.mark.parametrize("core, why", [
    ("<?php include 'config.php'; $connect = new Wrapper(); $connect->query('x');", "defined another way too"),
    ("<?php include 'config.php'; $connect->fetch_object('App');", "fetch_object may build an app object"),
    ("<?php include 'config.php'; $connect->send('x');", "not a mysqli method"),
    ("<?php include 'config.php'; if ($a) { $q = $connect->query('x'); } $q->fetch_array();",
     "the result is set in an arm the call is not in"),
    ("<?php include 'config.php'; $q = $other->query('x'); $q->fetch_array();", "not from a connection"),
    ("<?php include 'config.php'; $db->query('x');", "the receiver is not certainly a connection"),
])
def test_other_method_calls_stay_unresolved(tmp_path, core, why):
    files = {"config.php": "<?php $connect = new mysqli('h', 'u', 'p', 'd');", "core.php": core,
             "page.php": "<?php include 'core.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert include["outputsInside"] == "unknown", why
    assert abstains(result, "page.php") == [("unresolved_call", None, 1)], why


def test_a_dropped_method_call_is_noted(tmp_path):
    files = {"page.php": "<?php $c = mysqli_connect('h'); $r = $c->query('x'); echo 'a';"}
    result = run(tmp_path, files)
    assert entries(result, "page.php", "call") == []
    assert [(n["kind"], n["detail"]) for n in result["timelines"]["page.php"]["harnessNotes"]
            if n["kind"] == "builtin_mysqli_method"] == [("builtin_mysqli_method", "mysqli::query")]
    assert "builtinMethods" in result["timelines"]["page.php"]["_provenance"]


# ---------------------------------------------------------------- d) header_redirect (spec v0.10)


def test_a_literal_location_header_is_flagged_for_c1(tmp_path):
    """attendance index.php 30: `header('location: teacher/index.php');` (no exit after it)."""
    php = "<?php if ($_SESSION['type'] == 'teacher') { header('location: teacher/index.php'); }\n" \
          "header('Location:  ../login.php ');\nheader('Content-Type: text/html');\nheader(\"Location: $url\"); ?><p>p</p>"
    result = stages(tmp_path, php)
    redirects = result["timeline"]["redirects"]
    assert [(r["target"], r["loc"]["startLine"]) for r in redirects] == [("teacher/index.php", 1), ("../login.php", 2)]
    cond = redirects[0]["enclosedBy"][0]["condNodeId"]
    found = [(f["severity"], f["sourceLine"], f["detail"]) for f in result["stage5"]["flags"]
             if f["flag"] == "header_redirect"]
    assert found == [
        ("info", 1, f"header(\"Location: teacher/index.php\"): a redirect, nothing rendered; guarded by condition "
                    f"{cond}; the redirect belongs to C1"),
        ("info", 2, "header(\"Location: ../login.php\"): a redirect, nothing rendered; not guarded (always runs); "
                    "the redirect belongs to C1")]
    assert "<p>p</p>" in flat(result["files"]["app/page/page.tsx"])  # nothing else changes


def test_the_loader_reads_and_checks_redirects(tmp_path):
    timeline = page(tmp_path, "<?php header('Location: a.php'); echo 'x';")
    path = tmp_path / "t.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    (redirect,) = load_timeline(path).redirects
    assert (redirect.target, redirect.enclosed_by, redirect.loc.start_line) == ("a.php", (), 1)
    del timeline["redirects"][0]["target"]
    path.write_text(json.dumps(timeline), encoding="utf-8")
    with pytest.raises(ValueError, match="redirect keys"):
        load_timeline(path)


def test_a_redirect_in_a_file_that_prints_nothing_is_listed_in_the_index(tmp_path):
    """attendance logout.php: `session_destroy(); header('location: index.php');` has no unit."""
    files = {"logout.php": "<?php session_destroy();\nheader('location: index.php');",
             "page.php": "<?php if (!$u) { header('Location: logout.php'); } ?><p>p</p>"}
    result = run(tmp_path, files)
    assert result["silent"] == ["logout.php"]  # no unit, so no Stage 5 flag: run_apps lists it (next test)
    assert len(flags(result, "page.php", "header_redirect")) == 1


def test_silent_redirects_are_collected_from_the_harness(tmp_path):
    from tests.test_phase3 import FileHarness, SourceFile, parse_sources
    path = tmp_path / "logout.php"
    path.write_bytes(b"<?php session_destroy();\nif ($x) { header('location: index.php'); }")
    source = SourceFile(path, "t001", "legacy-apps/tiny/logout.php")
    (ast,), _ = parse_sources([source])
    harness = FileHarness(path.read_bytes(), ast, source.path, {})
    assert harness_app.silent_redirects({"logout.php": harness}, ["logout.php"]) == {
        "logout.php": [{"line": 2, "target": "index.php", "guarded": True}]}


# ---------------------------------------------------------------- spec v0.11: error_output_only


def test_a_file_that_prints_only_in_exit_arms_is_not_a_page(tmp_path):
    """attendance connect.php: `mysql_connect(...) or die(...)`; phpBlog core.php 6-9: a redirect guard."""
    files = {"connect.php": "<?php\nmysql_connect('h') or die('Cannot connect');\nmysql_select_db('d') or die('No db');\n",
             "guard.php": "<?php if (!file_exists('c')) {\n  echo '<meta http-equiv=\"refresh\" content=\"0; url=i\">';"
                          "\n  exit();\n}\nfunction head() { echo '<p>h</p>'; }",
             "page.php": "<?php include 'connect.php'; include 'guard.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    not_generated = {n["path"]: n for n in result["index"]["notGenerated"]}
    assert not_generated == {
        "connect.php": {"key": "connect.php", "path": "connect.php", "reason": "error_output_only", "lines": [2, 3]},
        "guard.php": {"key": "guard.php", "path": "guard.php", "reason": "error_output_only", "lines": [2]}}
    (flag,) = result["stage"]("connect.php", 5)["flags"]
    assert (flag["flag"], flag["severity"], flag["sourceLine"]) == ("error_output_only", "review", 2)
    assert "lines 2, 3" in flag["detail"]
    assert not (result["folder"] / run_apps.NEXT_APP / "app" / "(plain)" / "connect").exists()
    # the including page keeps its flags (one per include)
    assert len(flags(result, "page.php", "exit_output_not_generated")) == 2


@pytest.mark.parametrize("php", [
    "<?php if (!$c) { die('stop'); } ?><p>rest</p>",  # other output too
    "<?php include 'other.php'; if (!$c) { die('stop'); }",  # an include that prints, outside the arms
    "<?php if ($a) { if (!$c) { echo '<b>x</b>'; exit; } }",  # an arm R-I8 does not cover (abstains)
])
def test_a_file_with_anything_else_is_still_a_page(tmp_path, php):
    result = run(tmp_path, {"page.php": php, "other.php": "<i>other</i>"})
    assert "page.php" not in {n["path"] for n in result["index"]["notGenerated"]}


# ---------------------------------------------------------------- warehouse (Phase 3, app 5: the reserve app)


warehouse_config = harness_app.APPS["warehouse"]
warehouse_present = (warehouse_config["root"] / "index.php").exists()


def test_the_manifest_check_refuses_a_missing_an_extra_or_a_changed_file(tmp_path):
    root = tmp_path / "app"
    (root / "inc").mkdir(parents=True)
    (root / "a.php").write_bytes(b"<?php echo 1;")
    (root / "inc" / "b.php").write_bytes(b"<p>b</p>")
    manifest = tmp_path / "m.sha256"
    manifest.write_text("".join(f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  {n}\n"
                                for n in ("a.php", "inc/b.php")), encoding="utf-8")
    assert harness_app.verify_manifest(root, manifest) == 2
    (root / "inc" / "b.php").write_bytes(b"<p>B</p>")
    with pytest.raises(RuntimeError, match="differ from the manifest: \\['inc/b.php'\\]"):
        harness_app.verify_manifest(root, manifest)
    (root / "inc" / "b.php").unlink()
    (root / "c.php").write_bytes(b"")
    with pytest.raises(RuntimeError, match="missing \\['inc/b.php'\\], not in the manifest \\['c.php'\\]"):
        harness_app.verify_manifest(root, manifest)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    if not warehouse_present:
        pytest.skip("warehouse archive not unpacked (legacy-apps/PINS.md)")
    output = tmp_path_factory.mktemp("warehouse")
    tsc = compile_check.unavailable() is None
    return output, run_apps.run_app(harness_app.app_timelines("warehouse"), output, tsc=tsc)


def test_warehouse_is_pinned_and_keeps_its_licence():
    if not warehouse_present:
        pytest.skip("warehouse archive not unpacked (legacy-apps/PINS.md)")
    # supplied as a git archive + manifest (no .git): every file is checked against the committed manifest
    assert harness_app.verify_manifest(warehouse_config["root"], warehouse_config["manifest"]) == 63
    assert harness_app.source_commit("warehouse") == warehouse_config["commit"] == \
        "69fdd1ca1364a37e6dfbc7d90ded693964d87efe"
    assert "The MIT License" in (warehouse_config["root"] / "LICENSE").read_text(encoding="utf-8")
    pins = (ROOT / "legacy-apps" / "PINS.md").read_text(encoding="utf-8")
    assert warehouse_config["commit"] in pins and "oswa_inv.sql" in pins and "archive + manifest" in pins
    assert warehouse_config["docroot"] == ""


def test_warehouse_runs_whole_without_a_crash_and_compiles(warehouse):
    output, index = warehouse
    assert index["files"] == 48 and index["parseErrors"] == {} and index["collisions"] == []
    assert [p["key"] for p in index["pages"] if p["crash"]] == []
    assert len([p for p in index["pages"] if p["role"] == "page"]) == 41
    assert [s["name"] for s in index["shells"]] == ["LayoutsHeaderShell"]
    assert index["schemaFiles"] == ["oswa_inv.sql"] and index["notGenerated"] == []
    if compile_check.unavailable() is None:
        assert index["tscRan"] and index["tscUnattributed"] == 0, index["tscOutput"]
        # (shell parts have no result of their own: they are checked in their shell)
        assert [(p["key"], p["tsc"]) for p in index["pages"] if p["tsc"] not in (0, None)] == []
    rows = [apps_results_table.page_row(output / "warehouse", e) for e in index["pages"] if e["role"] != "shell_part"]
    reasons = {}
    for row in rows:
        for reason, n in row["abstains"].items():
            reasons[reason] = reasons.get(reason, 0) + n
    assert reasons == {"unresolved_call": 76, "cross_file_output": 40, "output_outside_shell": 21,
                       "tag_crosses_function": 1}
