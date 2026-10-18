"""Roadmap Phase 5R2: docs/eval-mapping.md v1.2 in tools/eval/score.py — "wrong needs output that renders; a non-gold
result that renders nothing is abstain" — one test per new or changed mapping row (correct / abstain / wrong where the
row has them), the guide v1.1 function rows (call line; a never-called function on its definition line), and the check
that the source tree read for definitions is still the tree that was run. Synthetic input only.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "eval"))
import score  # noqa: E402

from tests.test_phase5r import ELSE_PAGE, EMPTY_PAGE, HIDDEN_PAGE, fake_run  # noqa: E402

IF = "f1#00002"


def run_with(tmp_path, pages=None, roles=None, source=None):
    run = fake_run(tmp_path / "run", pages or {"a.php": EMPTY_PAGE}, roles)
    run.printing_arms, run.calls, run.source = {}, {}, source
    for key, unit in run.units.items():
        unit["entrypoint"] = f"app/{key}"
    return run


def conditional(run, rules=("R-I4",), key="a.php"):
    run.nodes[IF] = [(key, {"type": "InlineConditional", "rules": list(rules), "status": "review",
                            "reason": "unlabelled", "sourceIds": [IF]}, False)]


def unresolved(run, reason="condition_not_translatable"):
    run.flags.setdefault(IF, []).append({"kind": "unresolvedCondition", "reason": reason, "severity": "todo",
                                         "detail": "", "nodeId": IF})


# ---------------------------------------------------------------- if not_ui (changed row)


@pytest.mark.parametrize("setup, result", [
    ("none", "correct"),  # no node
    ("redirect", "correct"),  # header() only: no node, an info flag
    ("r_i7", "correct"),
    ("then_hidden", "abstain"),  # then-only, unresolvedCondition: renders nothing (HMS admin-panel.php 551)
    ("no_arm", "abstain"),  # spec v0.16: no arm chosen
    ("else_renders", "wrong"),  # v1.0 if / else: the else arm renders on every request
    ("translated", "wrong"),  # a translated condition: its content renders when it holds
])
def test_if_not_ui(tmp_path, setup, result):
    run = run_with(tmp_path)
    if setup == "redirect":
        run.redirects[IF] = [{"nodeId": "f1#00009"}]
    elif setup == "r_i7":
        conditional(run, ("R-I7",))
    elif setup in ("then_hidden", "no_arm", "else_renders", "translated"):
        conditional(run)
        if setup != "translated":
            unresolved(run)
        run.printing_arms[IF] = {"then", "else"} if setup in ("else_renders", "no_arm") else {"then"}
        if setup == "no_arm":
            run.flags[IF].append({"kind": "C2Todo", "reason": "condition_not_translatable", "severity": "todo",
                                  "nodeId": IF, "detail": "untranslatable condition: no arm is chosen (spec v0.16)"})
    assert score.if_result(run, {"decision": "not_ui"}, IF)[0] == result


# ---------------------------------------------------------------- if request_handler (changed row)


@pytest.mark.parametrize("page, kind, result", [
    (EMPTY_PAGE, "R-I7", "correct"),
    (EMPTY_PAGE, "R-I1", "abstain"),  # v1.0 R-I1 on a JS-only handler: renders nothing (HMS func.php 4)
    (ELSE_PAGE, "R-I1", "wrong"),  # v1.0 R-I1 on a POST-only page that renders (HMS search.php 4)
])
def test_if_request_handler(tmp_path, page, kind, result):
    run = run_with(tmp_path, {"a.php": page})
    node_type = "Page" if kind == "R-I1" else "Static"
    run.nodes[IF] = [("a.php", {"type": node_type, "rules": [kind], "status": "ok", "reason": ""}, False)]
    assert score.if_result(run, {"decision": "request_handler"}, IF)[0] == result


# ---------------------------------------------------------------- loop not_ui (changed row)


@pytest.mark.parametrize("found, role, result", [
    (None, "page", "correct"),  # no node
    ("R-L5", "page", "correct"),
    ("List", "not_called", "abstain"),  # a List nothing renders (HMS func1.php 44)
    ("List", "page", "wrong"),
])
def test_loop_not_ui(tmp_path, found, role, result):
    run = run_with(tmp_path, {"a.php": EMPTY_PAGE}, {"a.php": role})
    if found == "List":
        run.nodes["f1#00005"] = [("a.php", {"type": "List", "rules": ["R-L1", "R-L3"], "status": "ok", "reason": "",
                                            "children": []}, False)]
    elif found:
        run.nodes["f1#00005"] = [("a.php", {"type": "Static", "rules": [found], "status": "ok", "reason": ""}, False)]
    assert score.loop_result(run, {"decision": "not_ui"}, "f1#00005")[0] == result


# ---------------------------------------------------------------- unit not_ui (changed row)


@pytest.mark.parametrize("page, generated, result", [
    (None, False, "correct"),  # notGenerated
    (EMPTY_PAGE, True, "abstain"),  # an empty generated page (HMS contact.php)
    (HIDDEN_PAGE, True, "abstain"),
    (ELSE_PAGE, True, "wrong"),
])
def test_unit_not_ui(tmp_path, page, generated, result):
    run = run_with(tmp_path, {"a.php": page or EMPTY_PAGE})
    if not generated:
        run.units["a.php"]["notGenerated"] = {"reason": "non_html_response"}
    assert score.unit_result(run, {"file": "a.php", "decision": "not_ui"})[0] == result


# ---------------------------------------------------------------- function not_ui (new row), guide v1.1 lines

LIB = "<?php\n// helpers\nfunction quiet() { return 1; }\nfunction card() { echo '<b>x</b>'; }\n"


def function_row(line, decision="not_ui", file="lib.php"):
    return {"file": file, "line": line, "construct": "function", "decision": decision}


@pytest.mark.parametrize("line, role, result", [
    (3, None, "correct"),  # quiet() prints nothing: no component
    (4, "not_called", "abstain"),  # card() nothing calls: a component never rendered (HMS func.php 66)
    (4, "component", "wrong"),  # card() rendered at its calls
])
def test_function_not_ui_on_its_definition_line(tmp_path, line, role, result):
    source = tmp_path / "src"
    source.mkdir()
    (source / "lib.php").write_text(LIB, encoding="utf-8")
    run = run_with(tmp_path, {"index.php": EMPTY_PAGE}, source=source)
    if role:
        run.units["lib.php#card"] = {"key": "lib.php#card", "path": "lib.php", "role": role, "function": "card",
                                     "component": "Card"}
    out = score.score_row(run, function_row(line), set())
    assert out["result"] == result
    assert score.score_row(run, function_row(2), set())["result"] == "miss"  # no function defined on line 2


@pytest.mark.parametrize("outputs, component, decision, result", [
    (False, False, "not_ui", "correct"),  # a call that prints nothing: no component
    (True, True, "not_ui", "wrong"),  # a component rendered on the page
    (True, True, "component", "correct"),  # guide v1.1: the component row on the call line (HMS admin-panel.php 331)
])
def test_function_rows_match_on_the_call_line(tmp_path, outputs, component, decision, result):
    run = run_with(tmp_path, {"index.php": EMPTY_PAGE})
    call = {"id": "f1#00030", "kind": "call", "callee": "f2#00001", "outputsInside": outputs}
    run.calls[("index.php", 7)] = [("index.php", call)]
    if component:
        run.nodes["f1#00030"] = [("index.php", {"type": "FunctionCall", "rules": ["R-F1"], "status": "ok",
                                                "reason": "", "sourceIds": ["f1#00030"]}, False)]
    assert score.score_row(run, function_row(7, decision, "index.php"), set())["result"] == result


def test_function_not_ui_is_a_mapped_decision():
    assert "not_ui" in score.SCORED["function"]


# ---------------------------------------------------------------- the source tree read for definitions


def test_the_source_tree_must_still_be_the_tree_that_was_run(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "lib.php").write_text(LIB, encoding="utf-8")
    runs = tmp_path / "runs" / "app"
    runs.mkdir(parents=True)
    (runs / "run.json").write_text(json.dumps({"source": source.as_posix(), "sourceDigest": score.source_digest(source)}),
                                   encoding="utf-8")
    assert score.run_source(tmp_path / "runs", "app") == source
    (source / "lib.php").write_text(LIB + "// edited\n", encoding="utf-8")
    with pytest.raises(score.Refused, match="no longer the tree that was run"):
        score.run_source(tmp_path / "runs", "app")
