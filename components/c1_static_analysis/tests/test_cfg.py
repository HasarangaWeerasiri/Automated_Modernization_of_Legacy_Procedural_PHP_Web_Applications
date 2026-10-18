"""CFG construction (src/c1/cfg.py). Each test names the rule it checks and its source."""

from __future__ import annotations

import json

import pytest

from c1.astdoc import AstDoc
from c1.cfg import build_file_cfg, build_project_cfgs, run, validate_unit
from c1.dominance import dominates
from conftest import php_ast, requires_php


# -- helpers ----------------------------------------------------------------------
def build(src: str, file_id: str = "f000"):
    ast = AstDoc(php_ast(src, file_id))
    return ast, build_file_cfg(ast)


def main_unit(cfg):
    return cfg["units"][0]


def text(ast: AstDoc, src: str, nid: str) -> str:
    loc = ast.loc(nid)
    return src.encode("utf-8")[loc["startByte"]:loc["endByte"]].decode("utf-8")


def block_of(ast, src, unit, needle: str) -> str:
    """Block whose items include a node whose source text starts with `needle`."""
    for b in unit["blocks"]:
        for i in b["items"]:
            if text(ast, src, i).startswith(needle):
                return b["id"]
    raise AssertionError(f"no item starting with {needle!r}")


def edges(unit, frm=None, to=None, label=None):
    return [e for e in unit["edges"]
            if (frm is None or e["from"] == frm) and (to is None or e["to"] == to)
            and (label is None or e["label"] == label)]


def assert_valid(ast, cfg):
    for u in cfg["units"]:
        assert validate_unit(ast, u) == [], u["name"]


# -- straight line and branches ---------------------------------------------------
def test_straight_line_is_one_block():
    src = "<?php\n$a = 1;\necho $a;\n?>\n<p>hi</p>\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert len(u["blocks"][0]["items"]) == 3  # assignment, echo, inline HTML
    assert edges(u, frm="B0", to=u["exit"], label="end")


def test_if_else_labels_edges_with_the_predicate():
    # Xie & Aiken Sec. 3: a conditional edge is labelled with the branch predicate
    src = "<?php\nif ($x > 1) { echo 'big'; } else { echo 'small'; }\necho 'done';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    cond_block = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch")
    pred = cond_block["terminator"]["node"]
    assert text(ast, src, pred) == "$x > 1"
    t = edges(u, frm=cond_block["id"], label="true")
    f = edges(u, frm=cond_block["id"], label="false")
    assert len(t) == 1 and len(f) == 1 and t[0]["node"] == pred
    done = block_of(ast, src, u, "echo 'done'")
    assert dominates(u["idom"] | {u["entry"]: u["entry"]}, cond_block["id"], done)


def test_elseif_chain():
    src = "<?php\nif ($a) { echo 1; } elseif ($b) { echo 2; } else { echo 3; }\necho 4;\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    branches = [b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch"]
    assert [text(ast, src, b["terminator"]["node"]) for b in branches] == ["$a", "$b"]
    four = block_of(ast, src, u, "echo 4")
    assert len(edges(u, to=four)) == 3


# -- exit / die -------------------------------------------------------------------
def test_exit_ends_the_block_and_following_code_is_unreachable():
    # PHP manual (exit): "Terminates execution of the script."
    src = "<?php\necho 'a';\nexit;\necho 'never';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert edges(u, frm="B0", to=u["exit"], label="exit")
    never = block_of(ast, src, u, "echo 'never'")
    assert next(b for b in u["blocks"] if b["id"] == never)["reachable"] is False
    assert len(u["unreachableItems"]) == 1


def test_or_die_is_a_branch():
    # PHP manual (logical operators): `or` short-circuits; `$r = q() or die()` assigns first
    src = "<?php\n$r = mysqli_query($c, $q) or die('db error');\necho 'ok';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    src_block = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch")
    assert text(ast, src, src_block["terminator"]["node"]) == "$r = mysqli_query($c, $q)"
    die_block = edges(u, frm=src_block["id"], label="false")[0]["to"]
    assert edges(u, frm=die_block, to=u["exit"], label="exit")
    ok = block_of(ast, src, u, "echo 'ok'")
    assert edges(u, frm=src_block["id"], to=ok, label="true")
    assert not edges(u, frm=ok, to=u["exit"], label="exit")


def test_and_die_runs_die_on_true():
    src = "<?php\n$bad && die('no');\necho 'ok';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    src_block = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch")
    die_block = edges(u, frm=src_block["id"], label="true")[0]["to"]
    assert edges(u, frm=die_block, to=u["exit"], label="exit")


def test_assignment_of_boolean_or_die():
    # `||` binds tighter than `=`, so the die sits inside the assignment
    src = "<?php\n$r = q() || die('x');\necho 'ok';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert any(b.get("terminator", {}).get("kind") == "branch" for b in u["blocks"])
    assert u["flags"] == []


def test_ternary_with_exit_and_coalesce_with_exit():
    src = "<?php\n$a = $ok ? 1 : exit('x');\n$b = $_GET['id'] ?? die('missing');\necho $a;\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    kinds = sorted(b["terminator"]["kind"] for b in u["blocks"] if "terminator" in b)
    assert kinds == ["branch", "coalesce"]
    assert len(edges(u, to=u["exit"], label="exit")) == 2


def test_exit_in_unsplittable_position_is_flagged_not_dropped():
    src = "<?php\nfoo(bar() ?: 1, exit(2));\necho 'x';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert any("may exit" in f["reason"] for f in u["flags"])
    assert edges(u, to=u["exit"], label="exit")


# -- the authorization guard pattern ------------------------------------------------
GUARD = """<?php
session_start();
if (!isset($_SESSION['uid'])) {
    header('Location: login.php');
    exit();
}
echo 'secret';
"""


def test_terminating_guard_dominates_protected_code():
    # RoleCast: a failed check that ends in exit makes the rest of the page guarded
    ast, cfg = build(GUARD)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    guard = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch")
    secret = block_of(ast, GUARD, u, "echo 'secret'")
    false_target = edges(u, frm=guard["id"], label="false")[0]["to"]
    idom = u["idom"] | {u["entry"]: u["entry"]}
    assert dominates(idom, false_target, secret)
    # and no path reaches 'secret' through the true (failed-check) branch
    true_target = edges(u, frm=guard["id"], label="true")[0]["to"]
    assert not dominates(idom, true_target, secret)
    assert edges(u, frm=true_target, to=u["exit"], label="exit")


def test_redirect_without_exit_does_not_terminate():
    # header('Location') alone does not stop PHP: the protected code stays reachable
    src = GUARD.replace("    exit();\n", "")
    ast, cfg = build(src)
    u = main_unit(cfg)
    guard = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch")
    true_target = edges(u, frm=guard["id"], label="true")[0]["to"]
    secret = block_of(ast, src, u, "echo 'secret'")
    assert edges(u, frm=true_target, to=secret)


def test_call_to_always_exiting_function_is_cut():
    # Xie & Aiken Sec. 3.1.6: remove statements after a call that always exits
    src = """<?php
function deny() { header('Location: login.php'); exit; }
if (!$ok) { deny(); echo 'never'; }
echo 'after';
"""
    ast = AstDoc(php_ast(src))
    cfgs, summary = build_project_cfgs([ast])
    assert summary["alwaysExitFunctions"] == ["deny"]
    u = cfgs[0]["units"][0]
    assert validate_unit(ast, u) == []
    never = block_of(ast, src, u, "echo 'never'")
    assert next(b for b in u["blocks"] if b["id"] == never)["reachable"] is False
    deny_unit = next(x for x in cfgs[0]["units"] if x["name"] == "deny")
    assert deny_unit["alwaysExits"] is True


def test_always_exit_propagates_through_calls():
    src = "<?php\nfunction a() { die('x'); }\nfunction b() { a(); }\nb();\necho 'never';\n"
    ast = AstDoc(php_ast(src))
    cfgs, summary = build_project_cfgs([ast])
    assert summary["alwaysExitFunctions"] == ["a", "b"]
    assert summary["fixpointRounds"] >= 2


def test_function_defined_twice_is_not_cut():
    one = "<?php\nfunction deny() { exit; }\n"
    two = "<?php\nfunction deny() { return 1; }\ndeny();\necho 'reached';\n"
    a1, a2 = AstDoc(php_ast(one, "f000")), AstDoc(php_ast(two, "f001"))
    cfgs, summary = build_project_cfgs([a1, a2])
    assert summary["alwaysExitFunctions"] == []
    assert summary["ambiguousFunctionNames"] == ["deny"]


def test_conditional_exit_function_is_not_always_exiting():
    src = "<?php\nfunction maybe($x) { if ($x) { exit; } }\nmaybe(1);\necho 'reached';\n"
    ast = AstDoc(php_ast(src))
    cfgs, summary = build_project_cfgs([ast])
    assert summary["alwaysExitFunctions"] == []


# -- loops ----------------------------------------------------------------------------
def test_while_with_break_and_continue():
    src = "<?php\nwhile ($i < 10) {\n  if ($i == 5) { break; }\n  if ($i == 2) { continue; }\n  echo $i;\n}\necho 'end';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    head = next(b["id"] for b in u["blocks"] if b.get("terminator", {}).get("node") and
                text(ast, src, b["terminator"]["node"]) == "$i < 10")
    end = block_of(ast, src, u, "echo 'end'")
    assert edges(u, to=end, label="break")
    assert edges(u, to=head, label="continue")
    assert edges(u, to=head, label="back")
    assert edges(u, frm=head, to=end, label="false")


def test_foreach_labels_and_loop_variables():
    src = "<?php\nforeach ($rows as $k => $row) { echo $row['name']; }\necho 'done';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    head = next(b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "foreach")
    body = edges(u, frm=head["id"], label="iterate")[0]["to"]
    items = next(b for b in u["blocks"] if b["id"] == body)["items"]
    assert [text(ast, src, i) for i in items[:2]] == ["$k", "$row"]
    assert edges(u, frm=head["id"], label="done")


def test_do_while_and_for():
    src = "<?php\ndo { $i++; } while ($i < 3);\nfor ($j = 0; $j < 3; $j++) { echo $j; }\nfor (;;) { break; }\necho 'x';\n"
    ast, cfg = build(src)
    assert_valid(ast, cfg)
    u = main_unit(cfg)
    assert len(edges(u, label="back")) == 2  # for(;;) breaks before reaching the step block
    assert block_of(ast, src, u, "echo 'x'")


def test_break_2_leaves_switch_and_loop():
    # PHP manual (break): "break 2; /* Exit the switch and the while. */"
    src = """<?php
while (true) {
  switch ($i) {
    case 5: echo 'five'; break 1;
    case 10: echo 'ten'; break 2;
    default: break;
  }
  echo 'in loop';
}
echo 'out';
"""
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    out = block_of(ast, src, u, "echo 'out'")
    in_loop = block_of(ast, src, u, "echo 'in loop'")
    ten = block_of(ast, src, u, "echo 'ten'")
    five = block_of(ast, src, u, "echo 'five'")
    assert edges(u, frm=ten, to=out, label="break")
    assert edges(u, frm=five, to=in_loop, label="break")


def test_switch_fallthrough_and_default():
    src = "<?php\nswitch ($a) {\n case 1:\n case 2: echo 'one-or-two';\n default: echo 'dflt';\n}\necho 'end';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    one_two = block_of(ast, src, u, "echo 'one-or-two'")
    dflt = block_of(ast, src, u, "echo 'dflt'")
    assert edges(u, frm=one_two, to=dflt, label="next")  # no break: falls through
    tests = [b for b in u["blocks"] if b.get("terminator", {}).get("kind") == "branch"]
    assert len(tests) == 2


def test_continue_inside_switch_acts_like_break_and_is_flagged():
    # PHP manual (continue): in a switch, continue behaves like break (E_WARNING)
    src = "<?php\nswitch ($a) { case 1: continue; }\necho 'x';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert any(f["reason"].startswith("continue-targets-switch") for f in u["flags"])


def test_break_outside_loop_is_an_error_edge():
    src = "<?php\nif ($a) { break; }\necho 'x';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert edges(u, to=u["exit"], label="error")
    assert u["flags"]


# -- return, units, includes, try --------------------------------------------------
def test_return_and_unreachable_code_in_function():
    src = "<?php\nfunction f($a) {\n  return $a;\n  echo 'dead';\n}\n"
    ast, cfg = build(src)
    assert_valid(ast, cfg)
    f = next(u for u in cfg["units"] if u["name"] == "f")
    assert f["kind"] == "function"
    assert edges(f, to=f["exit"], label="return")
    assert len(f["unreachableItems"]) == 1


def test_units_for_methods_closures_and_arrow_functions():
    src = """<?php
class Repo { public function find($id) { return $id; } abstract function x(); }
$f = function ($x) { return $x + 1; };
$g = fn($x) => $x * 2;
"""
    ast, cfg = build(src)
    assert_valid(ast, cfg)
    names = [(u["kind"], u["name"]) for u in cfg["units"]]
    assert names == [("main", "{main}"), ("method", "Repo::find"), ("closure", "{closure}"),
                     ("arrow-fn", "{arrow-fn}")]


def test_include_is_a_marked_point_at_a_block_boundary():
    # Xie & Aiken: include is spliced at its program point; the resolver does the splice
    src = "<?php\ninclude 'header.php';\necho 'body';\nrequire_once('db.php');\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert len(u["includeSites"]) == 2
    first = u["includeSites"][0]
    blk = next(b for b in u["blocks"] if b["id"] == first["block"])
    assert text(ast, src, blk["items"][-1]).startswith("include 'header.php'")
    body = block_of(ast, src, u, "echo 'body'")
    assert body != first["block"]


def test_try_catch_edges():
    src = "<?php\ntry { $a = f(); echo $a; } catch (Exception $e) { echo 'err'; }\necho 'end';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    err = block_of(ast, src, u, "$e")
    assert edges(u, to=err, label="catch")
    end = block_of(ast, src, u, "echo 'end'")
    assert len(edges(u, to=end)) == 2


def test_throw_outside_try_leaves_the_unit():
    src = "<?php\nfunction f() { throw new Exception('x'); }\n"
    ast, cfg = build(src)
    f = cfg["units"][1]
    assert edges(f, to=f["exit"], label="throw")


def test_goto_and_label():
    src = "<?php\ngoto end;\necho 'skipped';\nend:\necho 'end';\n"
    ast, cfg = build(src)
    u = main_unit(cfg)
    assert_valid(ast, cfg)
    assert edges(u, label="goto")
    skipped = block_of(ast, src, u, "echo 'skipped'")
    assert next(b for b in u["blocks"] if b["id"] == skipped)["reachable"] is False


# -- determinism -----------------------------------------------------------------------
def test_cfg_is_deterministic():
    _, a = build(GUARD)
    _, b = build(GUARD)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@requires_php
def test_cfg_stage_on_fixture_tree_is_byte_identical(fixture_tree, run_extract):
    out1, out2 = run_extract(fixture_tree, "o1"), run_extract(fixture_tree, "o2")
    i1, i2 = run(out1), run(out2)
    assert i1 == i2
    for f in i1["files"]:
        assert (out1 / f["cfg"]).read_bytes() == (out2 / f["cfg"]).read_bytes()
    m = json.loads((out1 / "manifest.json").read_text(encoding="utf-8"))
    assert i1["generatedFor"] == m["contentHash"]
    for f in m["files"]:
        ast = AstDoc.load(out1 / f["ast"])
        cfg = json.loads((out1 / "cfg" / f"{f['fileId']}.json").read_text(encoding="utf-8"))
        for u in cfg["units"]:
            assert validate_unit(ast, u) == [], (f["path"], u["name"])


@pytest.mark.parametrize("bad", ["<?php\nif ($a) {\n"])
def test_broken_file_still_gets_a_cfg_for_what_parsed(bad):
    ast, cfg = build(bad)
    assert cfg["units"][0]["kind"] == "main"
