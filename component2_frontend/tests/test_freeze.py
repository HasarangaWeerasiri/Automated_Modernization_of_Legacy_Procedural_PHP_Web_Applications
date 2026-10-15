"""The boundary rules are frozen (v1.0, 15 Oct 2026): a guard that fails if the rules document stops saying so.

A change after the freeze is not forbidden, but it must be visible: the rules header keeps saying FROZEN and the
change is logged in docs/post-freeze-log.md and in boundary-rules section 10 as post-freeze.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "docs" / "php-analysis" / "boundary-rules.md"
LOG = ROOT / "docs" / "post-freeze-log.md"


def test_the_boundary_rules_say_they_are_frozen():
    lines = RULES.read_text(encoding="utf-8").splitlines()
    assert "FROZEN" in lines[0], lines[0]
    status = next(line for line in lines if line.startswith("| Status |"))
    assert "**Frozen**" in status and "15 Oct 2026" in status


def test_the_post_freeze_log_exists_with_its_columns():
    text = LOG.read_text(encoding="utf-8")
    assert "| Date | Change | Reason | Evaluation re-run? |" in text
