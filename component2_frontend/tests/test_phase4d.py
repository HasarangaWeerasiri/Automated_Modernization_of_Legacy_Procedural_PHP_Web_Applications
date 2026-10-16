"""Roadmap Phase 4d / Phase 5 preparation: the scorer (tools/eval/score.py) and the evaluation runner
(tools/eval/run_eval.py), on a SYNTHETIC app only (tests/fixtures/eval_synth/). No evaluation app is opened.

The gold standard is committed in a temporary git repository (dated before the run), as docs/eval-mapping.md
section 4.1 requires; the refusals are tested too.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "eval"))
import run_eval  # noqa: E402
import score  # noqa: E402

from tests.test_harness import pytestmark  # noqa: E402, F401 - the harness (PHP) is needed

SYNTH = ROOT / "tests" / "fixtures" / "eval_synth"
GOLD_DATE = "2026-10-16T00:00:00+05:30"


def git(repo: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ, **({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {}))
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org",
                    "-c", "core.autocrlf=false", *args], check=True, capture_output=True, env=env)


def gold_repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "gold").mkdir(parents=True)
    for name in ("synth.csv", "synth.second.csv"):
        shutil.copyfile(SYNTH / "gold" / name, repo / "gold" / name)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "gold standard", date=GOLD_DATE)
    return repo


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    base = tmp_path_factory.mktemp("eval")
    repo = gold_repo(base)
    result = run_eval.run_eval(SYNTH / "app", "synth", base / "runs", repo / "gold", tsc=False)
    report = score.score_app("synth", repo / "gold", base / "runs")
    return {"base": base, "repo": repo, "run": result, "report": report}


def results(report, version):
    return {(r["file"], r["line"], r["construct"], r["decision"] or r["binding"]):
            r["score"]["result"] if r["status"] == "scored" else r["status"]
            for r in report["versions"][version]["rows"]}


# ---------------------------------------------------------------- run_eval.py


def test_run_eval_writes_both_versions_and_keeps_the_first_run(scored, tmp_path):
    runs = scored["base"] / "runs" / "synth"
    for version in ("1.0", "1.1"):
        index = json.loads((runs / f"v{version}" / "synth" / "index.json").read_text(encoding="utf-8"))
        assert not any(p["crash"] for p in index["pages"])
    timeline = json.loads((runs / "v1.1" / "synth" / "guard.php" / "timeline.json").read_text(encoding="utf-8"))
    assert "topLevelIfs" in timeline  # v1.1's harness extension; not in v1.0
    assert "topLevelIfs" not in (runs / "v1.0" / "synth" / "guard.php" / "timeline.json").read_text(encoding="utf-8")
    run = json.loads((runs / "run.json").read_text(encoding="utf-8"))
    assert run["rules"] == ["1.0", "1.1"] and run["firstRun"] >= run["goldCommitted"]
    # a second run keeps the first run's time (section 4.1 compares the gold with it)
    copy = tmp_path / "runs"
    shutil.copytree(scored["base"] / "runs", copy)
    first = json.loads((copy / "synth" / "run.json").read_text(encoding="utf-8"))["firstRun"]
    again = run_eval.run_eval(SYNTH / "app", "synth", copy, scored["repo"] / "gold", tsc=False)
    assert again["run"]["firstRun"] == first


def test_run_eval_refuses_without_a_committed_gold(tmp_path):
    (tmp_path / "gold").mkdir()
    git(tmp_path, "init", "-q")
    with pytest.raises(score.Refused, match="does not exist"):
        run_eval.run_eval(SYNTH / "app", "synth", tmp_path / "runs", tmp_path / "gold", tsc=False)
    shutil.copyfile(SYNTH / "gold" / "synth.csv", tmp_path / "gold" / "synth.csv")
    with pytest.raises(score.Refused, match="not committed|uncommitted"):
        run_eval.run_eval(SYNTH / "app", "synth", tmp_path / "runs", tmp_path / "gold", tsc=False)


# ---------------------------------------------------------------- section 4.1 in the scorer


def test_the_scorer_refuses_an_uncommitted_or_later_gold(scored, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(scored["repo"], repo)
    runs = scored["base"] / "runs"
    gold = repo / "gold" / "synth.csv"
    gold.write_text(gold.read_text(encoding="utf-8") + "synth,index.php,99,if,inline,,,,\n", encoding="utf-8")
    with pytest.raises(score.Refused, match="uncommitted"):
        score.score_app("synth", repo / "gold", runs)
    git(repo, "commit", "-qam", "edited after the run", date="2030-01-01T00:00:00+00:00")
    with pytest.raises(score.Refused, match="after the first tool output"):
        score.score_app("synth", repo / "gold", runs)
    assert score.main(["synth", "--gold-dir", str(repo / "gold"), "--runs", str(runs),
                       "--out", str(tmp_path / "out"), "--doc", str(tmp_path / "doc.md")]) == 3
    assert not (tmp_path / "out").exists()  # nothing is written when it refuses


# ---------------------------------------------------------------- the mapping, row by row

EXPECTED = {  # (file, line, construct, gold) -> (v1.0, v1.1)
    ("index.php", 1, "unit", "page"): ("correct", "correct"),
    ("data.php", 1, "unit", "not_ui"): ("correct", "correct"),
    ("silent.php", 1, "unit", "page"): ("wrong", "wrong"),
    ("lib.php", 1, "unit", "layout_part"): ("not_in_mapping", "not_in_mapping"),
    ("index.php", 11, "loop", "list"): ("correct", "correct"),
    ("index.php", 17, "loop", "inline_map"): ("correct", "correct"),
    ("index.php", 18, "loop", "list"): ("correct", "correct"),
    ("index.php", 19, "loop", "list"): ("wrong", "wrong"),  # container div@19, gold section@19
    ("index.php", 20, "loop", "list"): ("abstain", "abstain"),  # inside an abstained try / catch
    ("index.php", 34, "loop", "not_ui"): ("correct", "correct"),
    ("guard.php", 2, "if", "page_guard"): ("correct", "abstain"),  # v1.1: a conditional, content hidden
    ("login.php", 2, "if", "page_guard"): ("correct", "correct"),  # its else redirects: R-I1 in both
    ("index.php", 18, "if", "list_guard"): ("correct", "correct"),
    ("lib.php", 4, "if", "attribute"): ("correct", "correct"),
    ("index.php", 24, "if", "attribute_presence"): ("correct", "correct"),
    ("index.php", 25, "if", "inline"): ("correct", "correct"),
    ("index.php", 27, "if", "own_component"): ("correct", "correct"),  # R-I4: size is a style choice
    ("index.php", 28, "if", "own_component"): ("correct", "correct"),
    ("index.php", 29, "if", "inline"): ("correct", "correct"),  # R-I5 with the note "large"
    ("index.php", 30, "if", "inline"): ("manual", "manual"),  # R-I5 without it: the owner judges
    ("index.php", 31, "if", "not_ui"): ("wrong", "wrong"),
    ("index.php", 32, "if", "not_ui"): ("wrong", "wrong"),
    ("index.php", 32, "if", "inline"): ("unsure", "unsure"),
    ("index.php", 22, "if", "auth_check"): ("correct", "correct"),
    ("index.php", 23, "if", "request_handler"): ("correct", "correct"),
    ("redirect.php", 3, "if", "redirect_guard"): ("correct", "correct"),
    ("redirect.php", 4, "if", "redirect_guard"): ("correct", "correct"),
    ("index.php", 35, "if", "not_ui"): ("correct", "correct"),
    ("index.php", 37, "if", "own_component"): ("unmapped", "unmapped"),
    ("index.php", 26, "if", "inline"): ("miss", "miss"),
    ("index.php", 8, "function", "shell"): ("correct", "correct"),
    ("index.php", 21, "function", "component"): ("correct", "correct"),
    ("index.php", 36, "function", "component"): ("abstain", "abstain"),
    ("index.php", 2, "include", "not_ui"): ("correct", "correct"),
    ("index.php", 3, "include", "not_ui"): ("correct", "correct"),
    ("index.php", 12, "read", "items.name"): ("correct", "correct"),
    ("index.php", 13, "read", "items.cost"): ("wrong", "wrong"),
    ("index.php", 14, "read", "unknown"): ("wrong", "wrong"),
    ("index.php", 17, "read", "items.name"): ("abstain", "abstain"),
    ("index.php", 18, "read", "users.name"): ("correct", "correct"),
    ("index.php", 23, "read", "literal"): ("correct", "correct"),
    ("index.php", 33, "read", "session"): ("correct", "correct"),
    ("index.php", 33, "read", "request"): ("correct", "correct"),
    ("index.php", 33, "read", "unknown"): ("correct", "correct"),
}


def test_every_gold_row_gets_the_mapped_result(scored):
    got = {key: (results(scored["report"], "1.0")[key], results(scored["report"], "1.1")[key]) for key in EXPECTED}
    assert got == EXPECTED
    assert len(scored["report"]["versions"]["1.0"]["rows"]) == len(EXPECTED)


def test_every_mapped_decision_and_every_outcome_is_exercised(scored):
    rows = scored["report"]["versions"]["1.0"]["rows"]
    decisions = {(r["construct"], r["decision"]) for r in rows if r["status"] == "scored" and r["construct"] != "read"}
    wanted = {(c, d) for c, ds in score.SCORED.items() if ds for d in ds} - {("include", "shell")}
    assert wanted <= decisions  # (function and include `shell` share one mapping row: the function row covers it)
    bindings = {r["binding"].split(".")[0] if "." in r["binding"] else r["binding"] for r in rows
                if r["construct"] == "read"}
    assert bindings >= {"items", "users", "unknown", "session", "request", "literal"}
    outcomes = set(results(scored["report"], "1.0").values()) | set(results(scored["report"], "1.1").values())
    assert outcomes == {"correct", "abstain", "wrong", "manual", "unmapped", "miss", "unsure", "not_in_mapping"}


def test_the_metrics_follow_section_3(scored):
    v10, v11 = (scored["report"]["versions"][v]["metrics"] for v in ("1.0", "1.1"))
    # boundary rows: 33 scored (24 correct, 4 wrong); abstain counts every scored row (2 boundary + 1 read)
    assert v10["counts"] == {"correct": 24, "wrong": 4, "abstain": 3, "readsCorrect": 6}
    assert (v10["boundaryPrecision"], v10["boundaryRecall"]) == (round(24 / 28, 4), round(24 / 33, 4))
    assert v10["abstainRate"] == round(3 / 42, 4)
    assert v11["counts"]["correct"] == 23 and v11["counts"]["abstain"] == 4  # guard.php under v1.1
    # strict: every wrong decision; flag-adjusted: not the two ifs whose node carries a specific review
    # (condition_not_translatable / exclusivity_not_verified): `unlabelled` and `no_endpoint` are on every node
    assert (v10["unsoundStrict"], v10["unsoundFlagAdjusted"]) == (6, 4)
    assert (v10["bindingAccuracy"], v10["wrongBindings"]) == (round(6 / 9, 4), 2)
    assert v10["compileRate"] is None  # tsc did not run
    assert scored["report"]["agreement"]["pairs"] == 8 and scored["report"]["agreement"]["agreement"] == 0.875
    assert scored["report"]["versions"]["1.0"]["extra"]  # tool decisions no gold row names: listed, not scored


def test_the_cli_writes_the_report_and_the_results_page(scored, tmp_path):
    out, doc = tmp_path / "out", tmp_path / "eval-results.md"
    assert score.main(["synth", "--gold-dir", str(scored["repo"] / "gold"), "--runs", str(scored["base"] / "runs"),
                       "--out", str(out), "--doc", str(doc)]) == 0
    report = json.loads((out / "synth.json").read_text(encoding="utf-8"))
    assert set(report["versions"]) == {"1.0", "1.1"}
    text = doc.read_text(encoding="utf-8")
    assert "| Metric | synth v1.0 | synth v1.1 | total v1.0 | total v1.1 |" in text
    assert "| `guard.php:2` | if | page_guard | correct — R-I1 | abstain — R-I5" in text


def test_the_kappa_helper():
    assert score.kappa([("a", "a"), ("b", "b")]) == {"pairs": 2, "agreement": 1.0, "kappa": 1.0}
    assert score.kappa([("a", "b"), ("b", "a")])["kappa"] == -1.0
