"""Roadmap Phase 3, step 3: the corrections from the step-2 review (boundary-rules v0.14, generation-spec v0.9)
and attendance, the fourth rule-discovery app.

a) R-I8c, an exit arm in a called function or an included file; R-I8's scope (v0.14); b) R-F2 condition 2 with
the HTML5 block-group end tags only; c) R-I3b, the same attribute later in the tag (tests/test_phase3b.py);
d) R-L4, a read attributed to the List whose loop fetched it, generated as `outer.<field>`.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import pattern_log  # noqa: E402
import run_apps  # noqa: E402

from src.mapping import app_units  # noqa: E402
from tests.test_harness import harness_app, page, pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402
from tests.test_phase3 import abstains, flat, run  # noqa: E402
from tests.test_stage5 import load_contract, load_endpoint_map, pipeline  # noqa: E402

attendance_clone = (harness_app.APPS["attendance"]["root"] / ".git").exists()


def entries(result: dict, key: str, kind: str) -> list:
    return [e for e in result["timelines"][key]["sequence"] if e["kind"] == kind]


def site_flags(result: dict, key: str) -> list:
    return [(f["nodeId"], f["severity"], f["detail"]) for f in result["stage"](key, 5)["flags"]
            if f["flag"] == "exit_output_not_generated"]


# ---------------------------------------------------------------- a) R-I8c


def test_an_include_whose_file_prints_only_in_an_exit_arm_is_no_gap_and_is_flagged(tmp_path):
    """phpBlog config.tpl 10-13: `if (mysqli_connect_errno()) { printf(...); exit(); }`."""
    files = {"config.php": "<?php $db = 1;\nif (mysqli_connect_errno()) {\n    printf('failed: %s', 'x');\n"
                           "    exit();\n}\n",
             "page.php": "<?php include 'config.php'; ?><p>page</p>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert (include["outputsInside"], include["exitOutput"]) == (False, [
        {"file": "legacy-apps/tiny/config.php", "line": 4}])
    assert abstains(result, "page.php") == []  # no gap: the file counts as non-printing
    ((node_id, severity, detail),) = site_flags(result, "page.php")
    assert (node_id, severity) == (include["id"], "review")
    assert "the exit arm at config.php:4 prints and exits" in detail and "R-I8c" in detail


def test_a_call_of_a_function_whose_only_output_is_a_die_message_is_flagged(tmp_path):
    """WackoPicko include/users.php 108: `die(mysql_error())` in Users::check_login. The page keeps its page
    guard (R-I1) and its shell: the call is not a printing call."""
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }\n"
                        "function check_login($u) {\n  if (!$res) {\n    die(mysql_error());\n  }\n  return 1;\n}",
             "login.php": "<?php include 'lib.php'; if (isset($_POST['u'])) { $ok = check_login($_POST['u']); }\n"
                          "if ($bad) { head(); ?><form><input name='u'></form><?php foot(); } ?>"}
    result = run(tmp_path, files)
    names = result["timelines"]["login.php"]["callNames"]
    (call,) = [e for e in entries(result, "login.php", "call") if names[e["id"]] == "check_login"]
    assert (call["outputsInside"], call["exitOutput"]) == (False, [{"file": "legacy-apps/tiny/lib.php", "line": 5}])
    page_node = result["nodes"]("login.php")[0]
    assert page_node["rules"] == ["R-I1", "R-F2"] and abstains(result, "login.php") == []
    assert [f[0] for f in site_flags(result, "login.php")] == [call["id"]]


def test_exit_arms_are_followed_through_what_a_function_includes(tmp_path):
    """phpBlog: post_author() includes config.php; its 22 call sites were unresolved_call in step 2."""
    files = {"config.php": "<?php if (!$c) {\n  printf('no db');\n  exit;\n}",
             "lib.php": "<?php function author($id) { include 'config.php'; return 'x'; }",
             "page.php": "<?php include 'lib.php'; ?><p><?php echo author(1); ?></p>"}
    result = run(tmp_path, files)
    (call,) = entries(result, "page.php", "call")
    assert (call["outputsInside"], call["exitOutput"]) == (False, [{"file": "legacy-apps/tiny/config.php", "line": 3}])
    assert "unresolved_call" not in [r for r, _, _ in abstains(result, "page.php")]


def test_a_file_that_also_prints_elsewhere_still_prints(tmp_path):
    files = {"part.php": "<?php if ($e) { echo '<b>error</b>'; exit; } ?><i>part</i>",
             "page.php": "<div><?php include 'part.php'; ?></div>"}
    result = run(tmp_path, files)
    (include,) = entries(result, "page.php", "include")
    assert include["outputsInside"] is True and include["exitOutput"]
    assert abstains(result, "page.php") == [("cross_file_output", None, 1)]
    assert len(site_flags(result, "page.php")) == 1


def test_an_exit_arm_in_an_included_file_is_not_generated_there(tmp_path):
    files = {"part.php": "<?php if ($e) { echo '<b>error</b>'; exit; } ?><i>part</i>",
             "page.php": "<div><?php include 'part.php'; ?></div>"}
    result = run(tmp_path, files)
    assert [(n["type"], n["rule"], n["reason"]) for n in result["nodes"]("part.php") if n["rule"]] == [
        ("Static", "R-I8c", "unlabelled")]
    text = flat(result["file"]("app/(plain)/part/page.tsx"))
    assert "<i>part</i>" in text and "error" not in text and "unresolvedCondition" not in text


def test_an_exit_arm_in_a_function_beside_another_arm_renders_only_the_other(tmp_path):
    files = {"lib.php": "<?php function box($ok) { if ($ok) { echo '<b>fine</b>'; } else { echo '<i>bad</i>'; exit; }"
                        " }",
             "page.php": "<?php include 'lib.php'; ?><div><?php box(1); ?></div>"}
    result = run(tmp_path, files)
    component = flat(result["file"]("components/Box.tsx"))
    assert "<b>fine</b>" in component and "bad" not in component


# ---------------------------------------------------------------- a) R-I8 scope (v0.14; case 1 corrected in v0.15)


@pytest.mark.parametrize("php", [
    "<?php if ($a) { echo '<i>x</i>'; if ($b) { echo '<b>stop</b>'; exit; } } ?><p>r</p>",  # nested
    "<?php if ($ok) { echo '<i>fine</i>'; } else { echo '<b>stop</b>'; exit; } ?><p>r</p>",  # an else arm
])
def test_an_exit_arm_with_other_output_that_is_not_a_top_level_then_arm_abstains(tmp_path, php):
    result = stages(tmp_path, php)
    assert "exit_arm_not_covered" in [n["reason"] for n in result["nodes"] if n["status"] == "abstain"]


def test_a_redirect_guard_at_the_top_level_inside_a_shell_still_applies(tmp_path):
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }",
             "page.php": "<?php include 'lib.php'; head(); if (empty($id)) { echo '<meta http-equiv=\"refresh\" "
                         "content=\"0; url=list.php\">'; exit; } ?><p>record</p><?php foot(); ?>"}
    result = run(tmp_path, files)
    assert [(n["type"], n["rule"]) for n in result["nodes"]("page.php") if n["rule"] == "R-I8"] == [("Static", "R-I8")]
    assert "<HeadShell><p>record</p></HeadShell>" in flat(result["file"]("app/(head)/page/page.tsx"))


def test_an_exit_arm_with_output_inside_a_shell_abstains(tmp_path):
    """Case 2 inside a shell's children: the footer after it would still render."""
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }",
             "page.php": "<?php include 'lib.php'; head(); if ($e) { echo '<b>stop</b>'; exit; } ?><p>r</p>"
                         "<?php foot(); ?>"}
    result = run(tmp_path, files)
    assert ("exit_arm_not_covered", "R-I8", 1) in abstains(result, "page.php")


# ---------------------------------------------------------------- b) R-F2 condition 2, exact (v0.14)


def test_only_block_group_end_tags_close_what_is_open_inside_them():
    assert app_units.closes(("html", "body", "div", "main"), ("div", "body", "html"))  # </div> ends <main>
    assert app_units.closes(("html", "body", "section", "nav"), ("section", "html"))
    for tag in ("span", "a", "b", "form", "p"):  # these do not close what is open inside them
        assert not app_units.closes(("html", "body", tag, "div"), (tag, "body", "html")), tag
        assert app_units.closes(("html", "body", tag), (tag, "body", "html")), tag  # but close themselves
    # not in scope: a <table> stands between the open <div> and the end tag
    assert not app_units.closes(("html", "body", "div", "table"), ("div", "body", "html"))
    assert app_units.closes(("html", "body", "div", "table"), ("table", "div", "body", "html"))


def test_the_three_apps_pair_as_before_with_the_exact_end_tag_rule(tmp_path):
    """WackoPicko our_header/our_footer and phpBlog's three pairs close only with block-group end tags."""
    assert app_units.closes(("html", "body", "div"), ("div", "body", "html"))  # WackoPicko
    assert app_units.closes(("html", "head", "body", "div", "div", "main"), ("div", "div", "body", "html"))  # admin


# ---------------------------------------------------------------- d) R-L4: a read of the outer loop's row


def outer_loop_run(tmp_path):
    def read(expr, var, column, fetch, query, table):
        return {"expr": expr, "var": var, "path": [column], "sourceKind": "db_row_field", "confidence": "resolved",
                "source": {"fetchNodeId": fetch, "queryNodeId": query, "table": table, "column": column}}

    outer = {"nodeId": "t001#00010", "kind": "Stmt_While", "role": "iteration",
             "iterExpr": "$row = mysqli_fetch_array($q)", "iterSourceKind": "db_row_field", "valueVar": "$row",
             "keyVar": None, "fetchNodeId": "t001#00011"}
    inner = {"nodeId": "t001#00020", "kind": "Stmt_While", "role": "iteration",
             "iterExpr": "$t = mysqli_fetch_array($q2)", "iterSourceKind": "db_row_field", "valueVar": "$t",
             "keyVar": None, "fetchNodeId": "t001#00021"}
    name = read("$row['name']", "$row", "name", "t001#00011", "t001#00012", "posts")
    tag = read("$t['tag']", "$t", "tag", "t001#00021", "t001#00022", "tags")
    sequence = [
        {"id": "t001#00001", "kind": "Stmt_InlineHTML", "raw": "<ul>", "enclosedBy": []},
        {"id": "t001#00002", "kind": "Stmt_InlineHTML", "raw": "<li><b>", "enclosedBy": [outer]},
        {"id": "t001#00003", "kind": "Stmt_Echo", "reads": [name], "enclosedBy": [outer]},
        {"id": "t001#00004", "kind": "Stmt_InlineHTML", "raw": "</b><ol>", "enclosedBy": [outer]},
        {"id": "t001#00005", "kind": "Stmt_InlineHTML", "raw": "<li><i>", "enclosedBy": [outer, inner]},
        {"id": "t001#00006", "kind": "Stmt_Echo", "reads": [tag], "enclosedBy": [outer, inner]},
        {"id": "t001#00007", "kind": "Stmt_InlineHTML", "raw": "</i> of <u>", "enclosedBy": [outer, inner]},
        {"id": "t001#00008", "kind": "Stmt_Echo", "reads": [name], "enclosedBy": [outer, inner]},
        {"id": "t001#00009", "kind": "Stmt_InlineHTML", "raw": "</u></li>", "enclosedBy": [outer, inner]},
        {"id": "t001#00030", "kind": "Stmt_InlineHTML", "raw": "</ol></li>", "enclosedBy": [outer]},
        {"id": "t001#00031", "kind": "Stmt_InlineHTML", "raw": "</ul>", "enclosedBy": []},
    ]
    labels = {e["id"]: {"concern": "presentation", "basis": "rule", "ruleId": "R01", "reason": "x"} for e in sequence}
    labels |= {loop: {"concern": "presentation", "basis": "rule", "ruleId": "R05", "reason": "x"}
               for loop in ("t001#00010", "t001#00020")}
    contract = {"openapi": "3.1.0", "info": {"title": "tiny", "version": "1"},
                "paths": {"/api/posts": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object", "required": ["posts"], "properties": {"posts": {"type": "array", "items": {
                        "type": "object", "required": ["name"], "properties": {
                            "name": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "object", "properties": {
                                "tag": {"type": "string"}}}}}}}}}}}}}}}}}
    entrypoint = "legacy-apps/tiny/page.php"
    (tmp_path / "t.json").write_text(json.dumps({"entrypoint": entrypoint, "schemaVersion": "1.0",
                                                 "sequence": sequence}), encoding="utf-8")
    (tmp_path / "l.json").write_text(json.dumps({"schemaVersion": "1.0", "labels": labels}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps(contract), encoding="utf-8")
    (tmp_path / "m.json").write_text(json.dumps({"endpoints": {entrypoint: {
        "method": "get", "path": "/api/posts", "status": "200", "mediaType": "application/json"}}}), encoding="utf-8")
    return pipeline(tmp_path / "t.json", tmp_path / "l.json", load_contract(tmp_path / "c.json"),
                    load_endpoint_map(tmp_path / "m.json"))


def test_a_read_is_attributed_to_the_list_whose_loop_fetched_it(tmp_path):
    tree, report, result = outer_loop_run(tmp_path)
    outer_list = next(r for r in report.results if r.need.references[0].node_id == "t001#00008")
    assert outer_list.need.row_of == "0.1"  # the outer List (page child 1), not the inner one (0.1.0.1)
    assert outer_list.result == "matched"


def test_a_read_of_the_outer_row_is_generated_as_outer_field(tmp_path):
    _, _, result = outer_loop_run(tmp_path)
    files = {f.path: f.text for f in result.files}
    inner_item = next(text for path, text in files.items() if path.endswith("List2Item.tsx"))
    assert "{outer.name}" in inner_item and "export function" in inner_item and "outer:" in inner_item
    assert not [t for t in result.todos if t.node_id == "t001#00008"]  # no gap for it any more


def test_the_harness_gives_a_while_loop_its_fetch(tmp_path):
    timeline = page(tmp_path, "<?php $q = mysqli_query($c, 'select a from t');\n"
                              "while ($r = mysqli_fetch_assoc($q)) { echo $r['a']; }")
    (echo,) = [e for e in timeline["sequence"] if e["kind"] == "Stmt_Echo"]
    (loop,) = echo["enclosedBy"]
    assert loop["fetchNodeId"] == echo["reads"][0]["source"]["fetchNodeId"]


# ---------------------------------------------------------------- attendance (Phase 3, app 4)


@pytest.fixture(scope="module")
def attendance(tmp_path_factory):
    if not attendance_clone:
        pytest.skip("attendance clone not present (legacy-apps/PINS.md)")
    output = tmp_path_factory.mktemp("attendance")
    tsc = compile_check.unavailable() is None
    return output, run_apps.run_app(harness_app.app_timelines("attendance"), output, tsc=tsc)


def test_attendance_is_pinned_and_keeps_its_licence():
    if not attendance_clone:
        pytest.skip("attendance clone not present (legacy-apps/PINS.md)")
    config = harness_app.APPS["attendance"]
    assert harness_app.head_commit(config["root"]) == config["commit"] == "31781040c50f2be20514ef18c1b0d4ae7b2bf76e"
    assert "MIT License" in (config["root"] / "LICENSE").read_text(encoding="utf-8")
    pins = (ROOT / "legacy-apps" / "PINS.md").read_text(encoding="utf-8")
    assert config["commit"] in pins and "database/attsystem.sql" in pins and config["docroot"] == ""


def test_attendance_runs_whole_without_a_crash_and_compiles(attendance):
    output, index = attendance
    assert index["files"] == 19 and index["parseErrors"] == {} and index["collisions"] == []
    assert [p["key"] for p in index["pages"] if p["crash"]] == []
    # 14 pages and, since step 4, the four connect.php: their `or die` messages are output (rules v0.15 section 1)
    assert len([p for p in index["pages"] if p["role"] == "page"]) == 18 and index["shells"] == []
    assert index["schemaFiles"] == ["database/attsystem.sql"]
    assert index["silentFileRedirects"] == {"logout.php": [{"line": 4, "target": "index.php", "guarded": False}]}
    if compile_check.unavailable() is None:
        assert index["tscRan"] and index["tscUnattributed"] == 0, index["tscOutput"]
        assert [(p["key"], p["tsc"]) for p in index["pages"] if p["tsc"] != 0] == []
    rows = [apps_results_table.page_row(output / "attendance", e) for e in index["pages"]]
    reasons = {}
    for row in rows:
        for reason, n in row["abstains"].items():
            reasons[reason] = reasons.get(reason, 0) + n
    assert reasons == {"try_catch_enclosure": 6, "guarded_loop_abstained": 2, "container_undetermined": 2,
                       "tag_crosses_branch": 1}


def test_an_abstained_try_region_gets_its_abstained_function(attendance):
    output, _ = attendance
    stage5 = json.loads((output / "attendance" / "index.php" / "stage5.json").read_text(encoding="utf-8"))
    assert not [f for f in stage5["flags"] if f["flag"] == "abstained_code_not_generated"]
    page_text = (output / "attendance" / "next-app" / "app" / "(plain)" / "page.tsx").read_text(encoding="utf-8")
    assert 'reason="try_catch_enclosure"' in page_text and "function Abstained_" in page_text


def test_the_pattern_log_has_an_attendance_section_with_the_owners_verdict(attendance, tmp_path):
    output, _ = attendance
    apps = pattern_log.APPS
    pattern_log.APPS = ("attendance",)
    try:
        log = pattern_log.render(output, tmp_path / "log.md")
    finally:
        pattern_log.APPS = apps
    assert "attendance: constructs against the §8 definition of a new pattern (Phase 3)" in log
    assert "### (a) Candidates the owner judged (rules v0.15)" in log  # step 4: decided, counter 1 / 2
    assert "Loop whose whole body is one `if` (filter-in-loop) — **2**" in log
    assert "Output inside try / catch" in log and "Output buffering (`ob_start`)" in log
