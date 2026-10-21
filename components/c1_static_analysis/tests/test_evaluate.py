"""Evaluation harness (src/c1/evaluate.py)."""

from __future__ import annotations

import pytest

from c1.evaluate import score


LABELS = {
    "f000#00001": {"concern": "presentation"},
    "f000#00002": {"concern": "data_access"},
    "f000#00003": {"concern": "business_logic"},
    "f000#00004": {"concern": "undecided"},
    "f000#00005": {"concern": "presentation"},
}


def rows(*gold):
    return [{"nodeId": f"f000#0000{i + 1}", "gold": g, "unsure": ""} for i, g in enumerate(gold)]


def test_perfect_score():
    rep = score(LABELS, rows("presentation", "data_access", "business_logic", "mixed", "presentation"))
    assert rep["accuracyOnDecided"] == 1.0 and rep["coverage"] == 0.8
    assert rep["accuracyCountingAbstainAsWrong"] == 0.8
    assert rep["abstainedByGold"] == {"mixed": 1}
    assert rep["perClass"]["presentation"] == {"support": 2, "precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_errors_show_in_confusion_and_precision():
    rep = score(LABELS, rows("business_logic", "data_access", "business_logic", "presentation", "skip"))
    assert rep["rows"] == 4
    assert rep["confusion"]["business_logic"]["presentation"] == 1
    assert rep["perClass"]["presentation"]["precision"] == 0.0
    assert rep["accuracyOnDecided"] == round(2 / 3, 4)


def test_unknown_gold_value_is_rejected():
    with pytest.raises(ValueError):
        score(LABELS, rows("logic"))
