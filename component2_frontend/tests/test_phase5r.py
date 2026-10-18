"""Roadmap Phase 5R (the Phase 5 dress rehearsal on HMS): the tools/eval fixes it needed, the results tables
(tools/results_table.py phase4 / phase5), and the owner's clarification of spec v0.17 ("reaching" = the only
definition that can reach the condition). Synthetic input only; no evaluation app is opened.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))
import results_table  # noqa: E402
import run_eval  # noqa: E402
import score  # noqa: E402

from tests.test_harness import pytestmark  # noqa: E402, F401 - the harness (PHP) is needed
from tests.test_phase4d import GOLD_DATE, SYNTH, git, gold_repo  # noqa: E402
from tests.test_phase4g import generate, guard_page  # noqa: E402


@pytest.fixture(scope="module")
def rehearsal(tmp_path_factory):
    """A gold repository with the real key and a practice key (both committed before the run), and one run."""
    base = tmp_path_factory.mktemp("rehearsal")
    repo = gold_repo(base)
    (repo / "gold" / "practice").mkdir()
    shutil.copyfile(SYNTH / "gold" / "synth.csv", repo / "gold" / "practice" / "synth-practice.csv")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "practice key", date=GOLD_DATE)
    result = run_eval.run_eval(SYNTH / "app", "synth", base / "runs", repo / "gold" / "practice", tsc=False,
                               key="synth-practice")
    return {"base": base, "repo": repo, "run": result["run"], "runs": base / "runs"}


# ---------------------------------------------------------------- section 4.1: anything uncommitted under gold/


@pytest.mark.parametrize("where", ["dummy.txt", "practice/dummy.csv"])
def test_any_uncommitted_file_under_the_gold_directory_is_refused(rehearsal, tmp_path, where):
    """Mapping section 4.1: "`gold/` has uncommitted changes". Before Phase 5R only the app's own files counted."""
    repo = tmp_path / "repo"
    shutil.copytree(rehearsal["repo"], repo)
    (repo / "gold" / where).write_text("not committed\n", encoding="utf-8")
    with pytest.raises(score.Refused, match="uncommitted changes"):
        score.score_app("synth", repo / "gold", rehearsal["runs"])
    with pytest.raises(score.Refused, match="uncommitted changes"):
        run_eval.run_eval(SYNTH / "app", "synth", tmp_path / "runs", repo / "gold", tsc=False)
    (repo / "gold" / where).unlink()  # removed again: accepted
    assert score.score_app("synth", repo / "gold", rehearsal["runs"])["app"] == "synth"


def test_a_practice_key_in_a_subfolder_still_checks_the_whole_gold_folder(rehearsal, tmp_path):
    """The practice key is in gold/practice/; an uncommitted file next to it in gold/ is still refused."""
    repo = tmp_path / "repo"
    shutil.copytree(rehearsal["repo"], repo)
    (repo / "gold" / "dummy.txt").write_text("not committed", encoding="utf-8")
    with pytest.raises(score.Refused, match="gold/ has uncommitted changes"):
        score.score_app("synth", repo / "gold" / "practice", rehearsal["runs"], key="synth-practice")


def test_corrections_written_after_the_run_are_not_a_refusal(rehearsal, tmp_path):
    """Gold-standard guide section 1.4: gold/corrections.md is written after the run."""
    repo = tmp_path / "repo"
    shutil.copytree(rehearsal["repo"], repo)
    (repo / "gold" / "corrections.md").write_text("row 7: typo\n", encoding="utf-8")
    assert score.score_app("synth", repo / "gold", rehearsal["runs"])["app"] == "synth"


# ---------------------------------------------------------------- --key: a practice key, scored like the real one


def test_a_practice_key_is_scored_against_the_apps_runs(rehearsal, tmp_path):
    practice = rehearsal["repo"] / "gold" / "practice"
    report = score.score_app("synth", practice, rehearsal["runs"], key="synth-practice")
    real = score.score_app("synth", rehearsal["repo"] / "gold", rehearsal["runs"])
    assert report["key"] == "synth-practice" and "key" not in real
    for version in score.VERSIONS:  # the same rows, so the same metrics
        assert report["versions"][version]["metrics"] == real["versions"][version]["metrics"]
    assert score.main(["synth", "--key", "synth-practice", "--gold-dir", str(practice), "--runs",
                       str(rehearsal["runs"]), "--out", str(tmp_path / "out"), "--doc", str(tmp_path / "d.md")]) == 0
    assert (tmp_path / "out" / "synth-practice.json").exists()
    doc = (tmp_path / "d.md").read_text(encoding="utf-8")
    assert "## synth-practice" in doc and "`535e2e6`" in doc and "changes 1–5" in doc


def test_the_run_records_the_code_it_ran_from(rehearsal):
    run = rehearsal["run"]
    assert len(run["codeCommit"]) == 40 and isinstance(run["codeChanges"], list) and run["key"] == "synth-practice"


# ---------------------------------------------------------------- tools/results_table.py phase4 / phase5


def classified(folder: Path, pages: list) -> None:
    folder.mkdir(parents=True)
    (folder / "classified.json").write_text(json.dumps({"rules": "1.1", "pages": pages}), encoding="utf-8")


def page(name, legacy, matched, classes):
    return {"page": name, "legacyElements": legacy, "matched": matched, "listMatched": 0, "listElements": 0,
            "staticMatched": matched, "staticElements": legacy, "unexplained": classes.count("UNEXPLAINED"),
            "flagButWrong": sum(c.startswith("flag-but-wrong") for c in classes),
            "differences": [{"class": c} for c in classes]}


def test_the_phase4_table_comes_from_the_classified_runs(tmp_path, monkeypatch):
    for n, folder in enumerate(("a0", "a1", "a2", "a3")):
        classified(tmp_path / folder, [page("x.php", 10, 6 + n, ["flag:C2Todo"] * (4 - n)),
                                       page("y.php", 10, 10, [])])
    monkeypatch.setattr(results_table, "PHASE4", (("App", ("a0", "a1", "a2", "a3"), ("x.php",)),))
    text = results_table.render_phase4(tmp_path)
    assert "| **Render match** | 0.600 | 0.700 | 0.800 | 0.900 |" in text  # only x.php counted
    assert "| `flag:C2Todo` | 4 | 3 | 2 | 1 |" in text and "| Pages compared | 1 | 1 | 1 | 1 |" in text
    assert text == results_table.render_phase4(tmp_path)  # deterministic
    monkeypatch.setattr(results_table, "PHASE4", (("App", ("a0", "missing", "a2", "a3"), None),))
    with pytest.raises(FileNotFoundError, match="missing"):
        results_table.render_phase4(tmp_path)


def test_the_phase5_table_lists_the_metrics_and_the_rows_to_review(rehearsal, tmp_path):
    report = score.score_app("synth", rehearsal["repo"] / "gold" / "practice", rehearsal["runs"], key="synth-practice")
    path = tmp_path / "synth-practice.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    text = results_table.render_phase5([path])
    m = report["versions"]["1.1"]["metrics"]
    assert f"| Boundary precision | {results_table.fmt(report['versions']['1.0']['metrics']['boundaryPrecision'])} | " \
           f"{results_table.fmt(m['boundaryPrecision'])} |" in text
    wrong = [r for r in report["versions"]["1.1"]["rows"] if r["status"] == "scored" and r["score"]["result"] == "wrong"]
    assert wrong and all(f"| v1.1 | `{r['file']}:{r['line']}` |" in text for r in wrong)
    assert "| rows `not_in_mapping` (not scored) |" in text
    assert results_table.main(["phase5", str(path), "--out", str(tmp_path / "t.md")]) == 0
    assert (tmp_path / "t.md").read_text(encoding="utf-8") == text + "\n"


# ---------------------------------------------------------------- spec v0.17 wording (owner, 18 Oct): "reaching"


@pytest.mark.parametrize("count", [
    "if ($x) { $count = mysqli_num_rows($run); } else { $count = mysqli_num_rows($run); }",  # one per arm
    "$count = mysqli_num_rows($run);\nif ($x) { $count = 0; }",  # a second definition may reach it
])
def test_two_definitions_that_can_reach_the_condition_keep_it_a_gap(tmp_path, count):
    """Only the one definition that reaches the condition on every path counts; two possible ones: a gap."""
    out = generate(tmp_path, guard_page(count=count))
    (detail,) = out["flags"]
    assert detail.startswith("$count: not translated: $count: the count assignment does not certainly reach the "
                             "condition")  # (the other assignment, after the count, is also (b))
    assert "items.length" not in out["list"].split("function Arms_")[0]


# ---------------------------------------------------------------- scorer fixes: "wrong" needs rendered output


EMPTY_PAGE = 'export default function Page() {\n  return (\n    <C2Todo id="page" reason="no_endpoint" />\n  );\n}\n'
HIDDEN_PAGE = ('export default function Page() {\n  return (\n    <>\n      <C2Todo id="page" reason="no_endpoint" />\n'
               '      {unresolvedCondition("f1#00002", "condition_not_translatable") && (\n        <>\n'
               '          {"Failed: "}\n        </>\n      )}\n    </>\n  );\n}\n')
ELSE_PAGE = ('export default function Page() {\n  return (\n    <>\n'
             '      {unresolvedCondition("f1#00002", "needs_request_context") ? (\n        <p>a</p>\n      ) : (\n'
             '        <p>b</p>\n      )}\n    </>\n  );\n}\n')


def fake_run(tmp_path, pages: dict, roles: dict | None = None) -> score.ToolRun:
    """A ToolRun with only what the fixed results read: units, their generated pages, Stage 2 nodes."""
    run = object.__new__(score.ToolRun)
    run.folder, run.units, run.nodes, run.flags, run.redirects, run.abstained, run.inside = tmp_path, {}, {}, {}, {}, {}, {}
    for key, text in pages.items():
        page = tmp_path / key / "generated" / "app" / "(plain)" / key.removesuffix(".php") / "page.tsx"
        page.parent.mkdir(parents=True)
        page.write_text(text, encoding="utf-8")
        run.units[key] = {"key": key, "path": key, "role": (roles or {}).get(key, "page"), "function": None}
    return run


def test_a_generated_page_renders_only_what_is_not_hidden_or_a_gap(tmp_path):
    run = fake_run(tmp_path, {"a.php": EMPTY_PAGE, "b.php": HIDDEN_PAGE, "c.php": ELSE_PAGE,
                              "d.php": EMPTY_PAGE.replace("<C2Todo", "<p>x</p><C2Todo")})
    assert [run.renders(k) for k in ("a.php", "b.php", "c.php", "d.php")] == [False, False, True, True]


@pytest.mark.parametrize("page, result", [(EMPTY_PAGE, "unmapped"), (HIDDEN_PAGE, "unmapped"), (ELSE_PAGE, "wrong")])
def test_a_not_ui_unit_is_wrong_only_when_its_page_renders_content(tmp_path, page, result):
    """Mapping: unit `not_ui` is wrong when "generated as a page that renders content" (HMS contact.php, func*.php,
    header.php, include/config.php: empty pages)."""
    run = fake_run(tmp_path, {"a.php": page})
    assert score.unit_result(run, {"file": "a.php", "decision": "not_ui"})[0] == result
    assert score.unit_result(run, {"file": "a.php", "decision": "page"})[0] == "correct"


@pytest.mark.parametrize("page, result", [(EMPTY_PAGE, "unmapped"), (ELSE_PAGE, "wrong")])
def test_an_r_i1_request_handler_is_wrong_only_when_its_output_renders(tmp_path, page, result):
    """Mapping: `request_handler` is wrong when the "handler output [is] rendered unconditionally" (v1.0 R-I1 on HMS
    search.php: wrong; on func.php, whose handler prints only a JS alert: nothing renders)."""
    run = fake_run(tmp_path, {"a.php": page})
    run.nodes["f1#00002"] = [("a.php", {"type": "Page", "rules": ["R-I1"], "status": "ok", "reason": ""}, False)]
    assert score.if_result(run, {"decision": "request_handler"}, "f1#00002")[0] == result


def test_an_if_that_only_redirects_by_header_is_no_node_for_not_ui(tmp_path):
    run = fake_run(tmp_path, {"a.php": EMPTY_PAGE})
    run.redirects["f1#00002"] = [{"nodeId": "f1#00009"}]
    assert score.if_result(run, {"decision": "not_ui"}, "f1#00002")[0] == "correct"
    run.nodes["f1#00002"] = [("a.php", {"type": "InlineConditional", "rules": ["R-I4"], "status": "review",
                                        "reason": "unlabelled"}, False)]
    assert score.if_result(run, {"decision": "not_ui"}, "f1#00002")[0] == "wrong"  # a conditional: unchanged


def test_a_list_in_a_function_nothing_calls_is_not_rendered_output(tmp_path):
    """Mapping: loop `not_ui` is wrong for "a List or inline map that renders output" (HMS func1.php 44: display_docs()
    is never called)."""
    run = fake_run(tmp_path, {"lib.php#f": EMPTY_PAGE}, roles={"lib.php#f": "not_called"})
    node = {"type": "List", "rules": ["R-L1", "R-L3"], "status": "ok", "reason": "", "children": []}
    run.nodes["f1#00005"] = [("lib.php#f", node, False)]
    assert score.loop_result(run, {"decision": "not_ui"}, "f1#00005")[0] == "unmapped"
    run.units["lib.php#f"]["role"] = "page"
    assert score.loop_result(run, {"decision": "not_ui"}, "f1#00005")[0] == "wrong"
