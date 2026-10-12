"""The whole-app test harness (docs/c2-roadmap.md Phase 1): tools/harness.

Unit tests parse small PHP snippets with nikic/php-parser and check the timeline the harness
emits: one test per row of the read table, one per enclosure kind, function and include
recording. Then the regression check against the existing mocks, and a smoke test that runs
every page of HMS and WackoPicko through Stages 1-5.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import app as harness_app  # noqa: E402
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import pattern_log  # noqa: E402
import regress  # noqa: E402
import run_apps  # noqa: E402
from harness import FileHarness, parse_select  # noqa: E402

from src.mapping.loader import load_timeline  # noqa: E402

pytestmark = pytest.mark.skipif(harness_app.unavailable() is not None,
                                reason=f"harness skipped: {harness_app.unavailable()}")
clones = all((config["root"] / ".git").exists() for config in harness_app.APPS.values())
needs_clones = pytest.mark.skipif(not clones, reason="HMS or WackoPicko clone not present")


def timelines(tmp_path, php: str, name: str = "page.php") -> dict:
    """{function name or None for the page: timeline} for one PHP source text."""
    source = tmp_path / name
    source.write_bytes(php.encode("utf-8"))
    (parsed,) = harness_app.parse_files([source])
    harness = FileHarness(source.read_bytes(), parsed, "t001", f"legacy-apps/tiny/{name}")
    pages, _ = harness_app.build_timelines({name: harness}, lambda path: {"generator": "c2-harness"})
    return {page.function: page.timeline for page in pages}


def page(tmp_path, php: str) -> dict:
    return timelines(tmp_path, php)[None]


def reads(timeline: dict) -> list[dict]:
    return [r for entry in timeline["sequence"] for r in entry.get("reads", [])]


def kinds(timeline: dict) -> list[tuple]:
    return [(r["expr"], r["sourceKind"], r["confidence"]) for r in reads(timeline)]


FETCH = """<?php
$result = mysqli_query($con, %s);
while ($row = mysqli_fetch_array($result)) { echo $row['name']; }
"""


# ---------------------------------------------------------------- reads, one test per row of the table


def test_row_of_a_single_table_select_is_a_resolved_db_field(tmp_path):
    (read,) = reads(page(tmp_path, FETCH % '"select id, name from users where id = 3"'))
    assert (read["sourceKind"], read["confidence"]) == ("db_row_field", "resolved")
    assert (read["source"]["table"], read["source"]["column"]) == ("users", "name")
    assert read["source"]["fetchNodeId"].startswith("t001#") and read["source"]["queryNodeId"].startswith("t001#")


def test_select_star_on_one_table_is_resolved_like_the_mocks(tmp_path):
    timeline = page(tmp_path, FETCH % '"select * from users;"')
    (read,) = reads(timeline)
    assert (read["confidence"], read["source"]["table"], read["source"]["column"]) == ("resolved", "users", "name")
    (query,) = timeline["queries"].values()
    assert (query["tables"], query["columns"], query["resultVar"]) == (["users"], None, "$result")


def test_sql_in_a_variable_assigned_a_literal_is_literal_sql(tmp_path):
    php = """<?php
$query = "select name from users";
$result = mysqli_query($con, $query);
$row = mysqli_fetch_assoc($result);
echo $row['name'];
"""
    assert kinds(page(tmp_path, php)) == [("$row['name']", "db_row_field", "resolved")]


def test_mysql_functions_are_traced_too(tmp_path):
    php = """<?php
$res = mysql_query("select name from users");
while ($r = mysql_fetch_assoc($res)) { echo $r['name']; }
"""
    (read,) = reads(page(tmp_path, php))
    assert (read["sourceKind"], read["source"]["table"]) == ("db_row_field", "users")


def test_a_join_is_ambiguous(tmp_path):
    (read,) = reads(page(tmp_path, FETCH % '"select * from a inner join b on a.id = b.id"'))
    assert (read["sourceKind"], read["confidence"]) == ("db_row_field", "ambiguous")
    assert (read["source"]["table"], read["source"]["column"]) == (None, None)


@pytest.mark.parametrize("sql", ['"select * from users where id = \'$id\'"', '"select * from users where id=" . $id',
                                 "$sql"])
def test_interpolated_or_built_sql_is_unresolved(tmp_path, sql):
    timeline = page(tmp_path, FETCH % sql)
    assert kinds(timeline) == [("$row['name']", "unresolved", "unresolved")]
    assert [n["kind"] for n in timeline["harnessNotes"]] == ["row_read_not_traced"]


def test_a_column_not_in_the_select_list_is_unresolved(tmp_path):
    assert kinds(page(tmp_path, FETCH % '"select id from users"')) == [("$row['name']", "unresolved", "unresolved")]


def test_superglobals(tmp_path):
    php = "<?php echo $_SESSION['u']; echo $_GET['a']; echo $_POST['b']; echo $_REQUEST['c']; echo $_COOKIE['d']; " \
          "echo $_SERVER['PHP_SELF'];"
    assert [(r["sourceKind"], r["confidence"], r["source"]) for r in reads(page(tmp_path, php))] == [
        ("session", "resolved", None), ("request", "resolved", None), ("request", "resolved", None),
        ("request", "resolved", None), ("request", "resolved", None), ("server", "resolved", None)]


def test_literals(tmp_path):
    assert kinds(page(tmp_path, "<?php echo 'Active'; echo \"Done\"; echo 5; print 'x';")) == [
        ("'Active'", "literal", "resolved"), ('"Done"', "literal", "resolved"), ("5", "literal", "resolved"),
        ("'x'", "literal", "resolved")]


@pytest.mark.parametrize("wrapper", ["h", "htmlspecialchars", "htmlentities"])
def test_escape_wrappers_are_computed_with_derived_from(tmp_path, wrapper):
    (read,) = reads(page(tmp_path, f"<?php echo {wrapper}($_SESSION['u']);"))
    assert (read["sourceKind"], read["confidence"], read["var"], read["path"]) == ("computed", "resolved", None, None)
    assert [(d["expr"], d["sourceKind"]) for d in read["derivedFrom"]] == [("$_SESSION['u']", "session")]


def test_escape_wrapper_around_a_concatenation_keeps_only_the_reads(tmp_path):
    (read,) = reads(page(tmp_path, "<?php echo h(Cart::$URL . '?id=' . $pic['id']);"))
    assert [d["expr"] for d in read["derivedFrom"]] == ["Cart::$URL", "$pic['id']"]
    assert read["confidence"] == "unresolved"


@pytest.mark.parametrize("expr", ["$name", "$user['login']", "date('Y')", "Users::current()", "$db->title",
                                  "$a ? 'x' : 'y'", "$row[$key]", "COUNT"])
def test_anything_else_is_unresolved(tmp_path, expr):
    assert kinds(page(tmp_path, f"<?php echo {expr};")) == [(expr, "unresolved", "unresolved")]


def test_an_interpolated_string_is_one_unresolved_read_and_noted(tmp_path):
    timeline = page(tmp_path, '<?php echo "<td>$name</td>";')
    assert kinds(timeline) == [('"<td>$name</td>"', "unresolved", "unresolved")]
    (note,) = timeline["harnessNotes"]
    assert (note["kind"], note["markup"]) == ("interpolated_string_output", True)


def test_a_concatenated_echo_gives_one_read_per_piece(tmp_path):
    timeline = page(tmp_path, "<?php echo '<td>' . $_GET['q'] . '</td>', 'x';")
    assert [r["sourceKind"] for r in reads(timeline)] == ["literal", "request", "literal", "literal"]
    assert [n["kind"] for n in timeline["harnessNotes"]] == ["markup_in_concatenated_echo"]


# ---------------------------------------------------------------- "comes from" is decided only when certain


@pytest.mark.parametrize("php", [
    # the query is chosen in a branch the fetch is not in
    "<?php if ($a) { $result = mysqli_query($c, 'select name from users'); }\n"
    "$row = mysqli_fetch_assoc($result); echo $row['name'];",
    # an include between the query and the fetch may have changed $result
    "<?php $result = mysqli_query($c, 'select name from users'); include 'x.php';\n"
    "$row = mysqli_fetch_assoc($result); echo $row['name'];",
    # the row variable is also assigned from something else
    "<?php $result = mysqli_query($c, 'select name from users'); $row = mysqli_fetch_assoc($result);\n"
    "echo $row['name']; $row = other();",
    # the row is read after its loop
    "<?php $result = mysqli_query($c, 'select name from users');\n"
    "while ($row = mysqli_fetch_assoc($result)) { } echo $row['name'];",
    # the SQL variable is overwritten later in the loop both are in
    "<?php while ($x) { $result = mysqli_query($c, $q); $row = mysqli_fetch_assoc($result); echo $row['name'];\n"
    "$q = 'select name from users'; }",
    # inside a function, $result comes from outside
    "<?php function f($result) { $row = mysqli_fetch_assoc($result); echo $row['name']; }",
])
def test_uncertain_data_flow_is_unresolved(tmp_path, php):
    found = timelines(tmp_path, php)
    (timeline,) = found.values()
    assert kinds(timeline) == [("$row['name']", "unresolved", "unresolved")]


def test_foreach_over_fetched_rows(tmp_path):
    php = """<?php
$result = mysqli_query($c, "select name from users");
$rows = mysqli_fetch_all($result, MYSQLI_ASSOC);
foreach ($rows as $i => $row) { echo $row['name']; }
"""
    timeline = page(tmp_path, php)
    assert kinds(timeline) == [("$row['name']", "db_row_field", "resolved")]
    (loop,) = timeline["sequence"][0]["enclosedBy"]
    assert (loop["iterSourceKind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"]) == (
        "db_row_field", "$rows", "$row", "$i")


@pytest.mark.parametrize("sql, table, columns", [
    ("select * from t", "t", None),
    ("SELECT a, t.b, `c`, count(*) as n FROM `t` WHERE x = 1 ORDER BY a;", "t", ["a", "b", "c", "n"]),
    ("select distinct a from t u where u.x = 1", "t", ["a"]),
    ("select * from a, b", None, None),
    ("select * from a left join b on a.x = b.x", None, None),
    ("select a from (select a from t) s", None, None),
    ("update t set a = 1", None, None),
])
def test_parse_select(sql, table, columns):
    shape = parse_select(sql)
    assert (shape.table, shape.columns) == (table, columns)


# ---------------------------------------------------------------- enclosures, one test per kind


def enclosures(timeline: dict) -> list[dict]:
    return timeline["sequence"][0]["enclosedBy"]


def test_while_enclosure(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php while ($row = mysqli_fetch_array($r)) { echo 'x'; }"))
    assert loop == {"nodeId": "t001#00001", "kind": "Stmt_While", "role": "iteration",
                    "iterExpr": "$row = mysqli_fetch_array($r)", "iterSourceKind": "db_row_field",
                    "valueVar": "$row", "keyVar": None}


def test_foreach_enclosure(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php foreach ($items as $k => $v) { echo 'x'; }"))
    assert (loop["kind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"], loop["iterSourceKind"]) == (
        "Stmt_Foreach", "$items", "$v", "$k", "unresolved")


def test_for_enclosure_leaves_the_variables_null(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php for ($i = 0; $i < 3; $i++) { echo 'x'; }"))
    assert (loop["kind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"]) == (
        "Stmt_For", "$i = 0; $i < 3; $i++", None, None)


def test_do_enclosure(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php do { echo 'x'; } while ($row = next_row());"))
    assert (loop["kind"], loop["role"], loop["iterExpr"], loop["valueVar"]) == (
        "Stmt_Do", "iteration", "$row = next_row()", "$row")


def test_if_elseif_else_enclosures_carry_condexpr(tmp_path):
    php = "<?php if ( ($a == 1) && $b ) { echo 'x'; } elseif ($c) { echo 'y'; } else { echo 'z'; }"
    then, elseif, otherwise = (entry["enclosedBy"][0] for entry in page(tmp_path, php)["sequence"])
    assert (then["branch"], then["condExpr"]) == ("then", "($a == 1) && $b")
    assert (elseif["branch"], elseif["condExpr"]) == ("elseif", "$c")
    assert otherwise["branch"] == "else" and "condExpr" not in otherwise
    assert then["nodeId"] == elseif["nodeId"] == otherwise["nodeId"]
    assert otherwise["condNodeId"] == then["condNodeId"] != elseif["condNodeId"]


def test_else_if_is_a_nested_if(tmp_path):
    php = "<?php if ($a) { echo 'x'; } else if ($b) { echo 'y'; }"
    second = page(tmp_path, php)["sequence"][1]["enclosedBy"]
    assert [(e["branch"], e.get("condExpr")) for e in second] == [("else", None), ("then", "$b")]


def test_alternative_syntax_gives_the_same_enclosures(tmp_path):
    php = "<?php if ($a): ?><p>x</p><?php endif; foreach ($l as $v): ?><li></li><?php endforeach; ?>"
    first, second = page(tmp_path, php)["sequence"]
    assert (first["kind"], first["raw"], first["enclosedBy"][0]["condExpr"]) == ("Stmt_InlineHTML", "<p>x</p>", "$a")
    assert second["enclosedBy"][0]["kind"] == "Stmt_Foreach"


def test_switch_and_try_enclosures(tmp_path):
    php = "<?php switch ($x) { case 1: echo 'a'; break; default: echo 'b'; }\n" \
          "try { echo 'c'; } catch (Exception $e) { echo 'd'; } finally { echo 'e'; }"
    sequence = page(tmp_path, php)["sequence"]
    cases = [entry["enclosedBy"][0] for entry in sequence[:2]]
    assert [(c["kind"], c["role"], c["caseExpr"]) for c in cases] == [
        ("Stmt_Switch", "switch_case", "1"), ("Stmt_Switch", "switch_case", None)]
    assert cases[0]["nodeId"] == cases[1]["nodeId"] and cases[0]["caseNodeId"] != cases[1]["caseNodeId"]
    assert [(e["enclosedBy"][0]["role"], e["enclosedBy"][0]["part"]) for e in sequence[2:]] == [
        ("try_catch", "try"), ("try_catch", "catch"), ("try_catch", "finally")]


def test_entries_have_byte_exact_raw_ids_and_loc(tmp_path):
    php = "<p>café</p>\r\n<?php echo $x; ?>\r\n<b><?= $y ?></b>"
    first, echo, tag, short, close = page(tmp_path, php)["sequence"]
    assert (first["kind"], first["raw"]) == ("Stmt_InlineHTML", "<p>café</p>\r\n")
    assert (echo["kind"], echo["loc"]) == ("Stmt_Echo", {"startLine": 2, "endLine": 2, "startCol": 7, "endCol": 14})
    assert (short["kind"], short["loc"]["startCol"], short["loc"]["endCol"]) == ("Stmt_Echo", 4, 9)  # "<?= $y"
    assert [e["id"] for e in (first, echo, tag, short, close)] == sorted(e["id"] for e in (first, echo, tag, short,
                                                                                         close))


# ---------------------------------------------------------------- functions and includes: recorded, not inlined

FUNCTIONS = """<?php
include 'header.php';
require_once("lib/" . $name);
function row($r) { echo '<tr>'; }
function table($rows) { foreach ($rows as $r) { row($r); } }
function helper() { return 1; }
class View { static function title() { ?><h1>T</h1><?php } }
?>
<div><?php table($rows); helper(); strlen('x'); View::title(); ?></div>
"""


def test_each_printing_function_gets_its_own_timeline(tmp_path):
    found = timelines(tmp_path, FUNCTIONS)
    assert sorted(k for k in found if k) == ["View.title", "row"]  # table() and helper() print nothing themselves
    assert found["row"]["entrypoint"] == "legacy-apps/tiny/page.php#row"
    assert found["View.title"]["sequence"][0]["raw"] == "<h1>T</h1>"
    assert [e["kind"] for e in found[None]["sequence"]] == ["Stmt_InlineHTML", "Stmt_InlineHTML"]  # not inlined


def test_calls_are_recorded_with_whether_the_callee_prints(tmp_path):
    calls = timelines(tmp_path, FUNCTIONS)[None]["calls"]
    assert [(c["callee"], c["producesOutput"], c["line"]) for c in calls] == [
        ("table", True, 9), ("helper", False, 9), ("view::title", True, 9)]  # table prints through row()
    assert all(c["nodeId"].startswith("t001#") and c["enclosedBy"] == [] for c in calls)  # strlen: not an app function


def test_includes_are_recorded(tmp_path):
    includes = timelines(tmp_path, FUNCTIONS)[None]["includes"]
    assert [(i["kind"], i["expr"], i["literal"], i["line"]) for i in includes] == [
        ("include", "'header.php'", "header.php", 2), ("require_once", '"lib/" . $name', None, 3)]


def test_output_the_schema_cannot_express_is_noted(tmp_path):
    notes = page(tmp_path, "<?php echo 'x'; printf('%d', 1); die('stop'); ob_start();")["harnessNotes"]
    assert sorted(n["kind"] for n in notes) == ["exit_with_output", "output_buffering", "output_function"]


def test_provenance_marks_the_harness_and_its_extensions(tmp_path):
    provenance = page(tmp_path, "<?php echo 'x';")["_provenance"]
    assert provenance["generator"] == "c2-harness"
    assert "Member 01 Q4" in provenance["callsExtension"] and "Member 01 Q2" in provenance["condExprExtension"]


def test_a_file_that_prints_nothing_has_no_page_timeline(tmp_path):
    assert timelines(tmp_path, "<?php $x = 1; function f() { return 2; }") == {}


def test_the_loader_and_all_five_stages_accept_harness_output(tmp_path):
    php = """<table><?php
$result = mysqli_query($con, "select name from users");
while ($row = mysqli_fetch_array($result)) { ?><tr><td><?php echo $row['name']; ?></td></tr><?php }
switch ($x) { case 1: echo 'a'; }
?></table>"""
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(harness_app.dump_timeline(page(tmp_path, php)), encoding="utf-8")
    assert len(load_timeline(timeline_path).sequence) == 6
    folder = tmp_path / "out"
    folder.mkdir()
    from src.mapping.boundary_config import load_boundary_config
    from src.mapping.generation_config import load_generation_config
    from src.mapping.loader import load_contract

    run_apps.run_page(timeline_path, folder, load_boundary_config(), load_generation_config(),
                      load_contract(run_apps.EMPTY_CONTRACT))
    stage2 = json.loads((folder / "stage2.json").read_text(encoding="utf-8"))
    nodes = list(apps_results_table._walk(stage2["tree"]))
    assert [n["reason"] for n in nodes if n["status"] == "abstain"] == ["switch_case_enclosure"]
    assert [n["type"] for n in nodes if n["type"] in ("List", "Item")] == ["List", "Item"]
    assert (folder / "generated" / "lib" / "c2-todo.tsx").exists()


# ---------------------------------------------------------------- regression against the mocks (brief section 2)


@needs_clones
def test_harness_reproduces_the_mocks_except_where_the_read_rules_differ(tmp_path):
    differences = regress.run(tmp_path)
    assert {mock: len(d) for mock, d in differences.items()} == {
        "list_while": 0, "detail": 5, "wp_guestbook": 0, "wp_view": 1, "wp_login": 0, "wp_header": 0,
        "wp_thumbnails": 0}
    # detail: its SQL is interpolated (where fname ='$fname'), which the read table makes unresolved; the mock's
    # table and columns were given by hand.
    assert all(d["field"] == "reads" and d["harness"][0]["sourceKind"] == "unresolved"
               and d["mock"][0]["sourceKind"] == "db_row_field" for d in differences["detail"])
    # wp_view: date(...) inside h() is a function result (unresolved); the mock made it a computed read.
    (view,) = differences["wp_view"]
    assert view["id"] == "f007#00406" and view["harness"][0]["derivedFrom"][0]["sourceKind"] == "unresolved"


# ---------------------------------------------------------------- whole apps (brief section 3)


@pytest.fixture(scope="module")
def apps_output(tmp_path_factory):
    if not clones:
        pytest.skip("HMS or WackoPicko clone not present")
    output = tmp_path_factory.mktemp("apps")
    tsc = compile_check.unavailable() is None
    return output, {app: run_apps.run_app(harness_app.app_timelines(app), output, tsc=tsc)
                    for app in harness_app.APPS}


def test_every_page_of_both_apps_runs_without_a_crash(apps_output):
    _, indexes = apps_output
    assert {app: (index["files"], len(index["pages"])) for app, index in indexes.items()} == {
        "hms": (29, 33), "wackopicko": (49, 33)}
    for index in indexes.values():
        assert [p["key"] for p in index["pages"] if p["crash"]] == []
        assert index["parseErrors"] == {}


def test_every_page_of_both_apps_compiles(apps_output):
    _, indexes = apps_output
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    for index in indexes.values():
        assert index["tscRan"] and [(p["key"], p["tsc"]) for p in index["pages"] if p["tsc"] != 0] == []


def test_libraries_are_excluded_and_only_discovery_apps_are_configured(apps_output):
    _, indexes = apps_output
    assert sorted(harness_app.APPS) == ["hms", "wackopicko"]
    assert not any(p["path"].startswith(("TCPDF/", "vendor/")) for p in indexes["hms"]["pages"])


@needs_clones
def test_two_harness_runs_give_byte_identical_timelines():
    first, second = harness_app.app_timelines("wackopicko"), harness_app.app_timelines("wackopicko")
    assert [(p.key, harness_app.dump_timeline(p.timeline)) for p in first.pages] == \
        [(p.key, harness_app.dump_timeline(p.timeline)) for p in second.pages]


def test_reports_are_deterministic_and_only_read_the_outputs(apps_output, tmp_path):
    output, _ = apps_output
    for module in (apps_results_table, pattern_log):
        first = module.render(output, tmp_path / "a.md")
        assert first == module.render(output, tmp_path / "b.md")
    table = apps_results_table.render(output, tmp_path / "a.md")
    assert "| **hms total** |" in table and "| **wackopicko total** |" in table and "not from Member 01" in table
    log = pattern_log.render(output, tmp_path / "a.md")
    assert "Page output inside the list's container, outside the loop" in log and "§8 mapping" in log


def test_batch_compile_check_attributes_a_planted_error_to_its_page(apps_output, tmp_path):
    output, _ = apps_output
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    import shutil

    for key in ("index.php", "error.php"):
        shutil.copytree(output / "hms" / key / "generated", tmp_path / key)
    planted = tmp_path / "error.php" / "lib" / "api.ts"
    planted.write_text(planted.read_text(encoding="utf-8") + 'const wrong: number = "text";\n', encoding="utf-8")
    errors, _ = compile_check.check_many("planted_many", {key: tmp_path / key for key in ("index.php", "error.php")})
    assert errors == {"index.php": 0, "error.php": 1}
