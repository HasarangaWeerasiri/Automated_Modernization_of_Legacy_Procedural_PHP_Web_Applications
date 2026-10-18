"""Roadmap Phase 6E: the report's evidence pack (docs/report/). The facts are read from committed files, never typed in
(tools/report/facts.py); `tools/results_table.py report` writes facts.md; `tools/report/check_facts.py` re-derives every
number and fails on any mismatch; tools/report/build.py writes Appendix E, Appendix F and the figures.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "report"))
import build  # noqa: E402
import check_facts  # noqa: E402
import facts  # noqa: E402
import results_table  # noqa: E402

FACTS_MD = ROOT / "docs" / "report" / "facts.md"


@pytest.fixture(scope="module")
def report_text():
    return results_table.render_report()


# ---------------------------------------------------------------- results_table.py report


def test_the_report_mode_lists_every_fact_with_its_source_and_commit(report_text):
    for fact in facts.all_facts():
        row = next(line for line in report_text.split("\n") if line.startswith(f"| `{fact.id}` |"))
        assert f"**{fact.value()}**" in row and f"`{fact.source}`" in row
    assert "## Contents" in report_text and all(t in report_text for t in facts.table_of_contents())
    assert "not an evaluation result" in report_text  # the rehearsal heading


def test_the_report_mode_is_deterministic_and_writes_what_it_prints(report_text, tmp_path):
    assert results_table.render_report() == report_text
    assert results_table.main(["report", "--out", str(tmp_path / "facts.md")]) == 0
    assert (tmp_path / "facts.md").read_text(encoding="utf-8") == report_text + "\n"


def without_commits(text: str) -> str:
    """The page without its Commit column (it names the commit that last changed each source, so it moves with
    every commit of a source; the values must not)."""
    return re.sub(r"(?m)^(\| `[^`]+` \|.*?\|.*?\| `[^`]+` \|) [^|]+ (\|)", r"\1 \2", text)


def test_the_committed_facts_page_is_the_report_mode_output(report_text):
    assert without_commits(FACTS_MD.read_text(encoding="utf-8")) == without_commits(report_text + "\n")


# ---------------------------------------------------------------- the fact check


def test_every_fact_of_the_committed_page_re_derives():
    result = check_facts.check(FACTS_MD)
    assert result["problems"] == [] and result["checked"] == len(facts.all_facts())
    assert {line.split(":")[0] for line in result["notRecorded"]} == {"eval-results", "agreement",
                                                                         "render-match-eval"}


def test_a_changed_number_is_a_mismatch(tmp_path):
    text = FACTS_MD.read_text(encoding="utf-8")
    page = tmp_path / "facts.md"
    page.write_text(text.replace("| `hms-renderMatch-5` | HMS v1.1-final: render match | **0.996** |",
                                 "| `hms-renderMatch-5` | HMS v1.1-final: render match | **0.999** |"), encoding="utf-8")
    problems = check_facts.check(page)["problems"]
    assert problems[0] == "hms-renderMatch-5: facts.md says '0.999', docs/phase4-results.md gives '0.996'"
    assert problems[1:] in ([], ["hms-renderMatch-5: facts.md says '0.999', output/runtime/hms-final/classified.json "
                                 "gives '0.996'"])  # (the result file too, when it is on this machine)
    assert check_facts.main([str(page)]) == 1


def test_a_row_without_a_rule_and_a_rule_without_a_row_are_problems(tmp_path):
    text = FACTS_MD.read_text(encoding="utf-8")
    text = re.sub(r"^\| `sat-phpblog` \|.*\n", "", text, flags=re.M)  # a rule with no row
    text += "| `made-up` | A number nobody recorded | **42** | `x.md` | – | – |\n"  # a row with no rule
    page = tmp_path / "facts.md"
    page.write_text(text, encoding="utf-8")
    problems = check_facts.check(page)["problems"]
    assert "made-up: in facts.md but no rule derives it" in problems
    assert "sat-phpblog: a rule with no row in facts.md" in problems


def test_a_cross_check_that_disagrees_is_a_mismatch(monkeypatch):
    fact = next(f for f in facts.all_facts() if f.id == "hms-renderMatch-5")
    monkeypatch.setattr(fact, "checks", [("CONTEXT.md", lambda: "0.5")])  # a result file that says otherwise
    problems = check_facts.check(FACTS_MD)["problems"]
    assert problems == ["hms-renderMatch-5: facts.md says '0.996', CONTEXT.md gives '0.5'"]


def test_a_result_file_that_is_not_on_the_machine_is_skipped_not_failed(monkeypatch):
    fact = next(f for f in facts.all_facts() if f.id == "hms-renderMatch-5")
    monkeypatch.setattr(fact, "checks", [("output/runtime/no-such-run/classified.json", lambda: "0.5")])
    result = check_facts.check(FACTS_MD)
    assert result["problems"] == [] and result["skipped"] == [
        "hms-renderMatch-5: output/runtime/no-such-run/classified.json (not on this machine)"]


def test_the_source_readers_never_guess():
    with pytest.raises(LookupError):
        facts.find("CONTEXT.md", r"no such text (\d+)")
    with pytest.raises(LookupError):
        facts.cell("docs/phase4-results.md", "3. HMS results", "No such row", 1)
    assert facts.cell("docs/phase4-results.md", "5.3 The unsound decision", "`core.php` 652", 1) == \
        "Sign In \\| Register"  # an escaped pipe stays in its cell


# ---------------------------------------------------------------- Appendix E, Appendix F, figures


def test_appendix_e_copies_every_decision_in_date_order():
    rows = build.decisions()
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    text = build.appendix_e()
    for path, heading, (date_col, decision_col, _) in build.LOGS:
        for table in build.raw_tables(build.section(facts.text(path), heading)):
            if decision_col in table[0]:
                for cells in table[1:]:
                    assert cells[table[0].index(decision_col)] in text  # copied, not reworded
    assert f"{len(rows)} entries." in text


def test_appendix_f_is_the_commit_record():
    rows = build.commits()
    marked = {r["hash"] for r in rows if r["ai"]}
    assert {"aead7db", "ee21ff8", "0e0c61d"} <= marked
    text = build.appendix_f()
    assert f"{len(marked)} of the {len(rows)} commits" in text
    assert "written by the owner alone" in text


def test_the_figures_are_deterministic_and_drawn_from_the_facts():
    for name, draw in build.FIGURES.items():
        assert draw() == draw(), name
        assert draw().startswith("<svg ") and draw().rstrip().endswith("</svg>")
        assert (ROOT / "docs" / "report" / "fig" / name).read_text(encoding="utf-8") == draw(), name
    render = build.figure_render_match()
    assert ">0.947<" in render and ">0.996<" in render and ">0.308<" in render
    assert f"stop at {facts.find(facts.RULES, r'Saturation counter: (2 / 2)')}" in build.figure_saturation()
