"""tools/results_table.py: docs/c2-results.md from the stage outputs, deterministic."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import results_table  # noqa: E402

needs_outputs = pytest.mark.skipif(
    not all((ROOT / "output" / f"stage{s}_{m}.json").exists() for s in range(1, 6) for m in results_table.MOCKS),
    reason="stage outputs missing: run src/main.py --stage 5 on every mock first")


@needs_outputs
def test_two_runs_write_byte_identical_markdown(tmp_path):
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    results_table.main([str(first)])
    results_table.main([str(second)])
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert "mocks, not real Member 01 / C1 input" in text and "commit `" in text
    assert text.count("\n| ") >= 10 + 1 + 1  # ten mocks, the totals row, Table B


@needs_outputs
def test_totals_row_adds_up_the_mocks():
    rows = [results_table.row(m, results_table.load(results_table.OUTPUT, m)) for m in results_table.MOCKS]
    text = results_table.render()
    totals = next(line for line in text.splitlines() if line.startswith("| **All 10**"))
    flag_rate = sum(r["s5"][3] for r in rows)
    without = sum(r["without"][0] for r in rows), sum(r["without"][1] for r in rows)
    assert f"/ {flag_rate} |" in totals and f"| {without[0]}/{without[1]} |" in totals


def test_missing_stage_output_is_reported_not_guessed(tmp_path):
    with pytest.raises(FileNotFoundError, match="stage1_list_while.json is missing"):
        results_table.load(tmp_path, "list_while")
