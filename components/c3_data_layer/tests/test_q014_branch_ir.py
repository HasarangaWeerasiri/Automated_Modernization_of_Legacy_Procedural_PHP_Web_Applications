"""Tests for Q014 conditional SELECT diagnostic IR."""

import copy
import json
from pathlib import Path

import pytest

from c3.conversion.branch_ir import validate_conditional_select_ir


@pytest.fixture
def valid_ir():
    path = Path(__file__).parent / "fixtures" / "q014_branch_diagnostic.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_q014_diagnostic_ir(valid_ir):
    assert validate_conditional_select_ir(valid_ir) is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("query_shape", "SINGLE_SELECT"),
        ("operation", "UPDATE"),
        ("status", "CONVERTED"),
        ("safe_for_conversion", True),
        ("unsupported_operations_checked", False),
        ("branches", {}),
        ("branches", {"IF": "SELECT id FROM doctors"}),
    ],
)
def test_invalid_ir_fields_rejected(valid_ir, field, value):
    ir = copy.deepcopy(valid_ir)
    ir[field] = value

    assert validate_conditional_select_ir(ir) is False


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM doctors",
        "UPDATE doctors SET active = 0",
        "SELECT id FROM doctors; DELETE FROM doctors",
        "SELECT id FROM doctors JOIN patients ON doctors.id = patients.id",
        "SELECT id FROM doctors WHERE id IN (SELECT doctor_id FROM appointments)",
    ],
)
def test_unsafe_branch_sql_rejected(valid_ir, sql):
    ir = copy.deepcopy(valid_ir)
    ir["branches"]["IF"] = sql

    assert validate_conditional_select_ir(ir) is False


@pytest.mark.parametrize(
    "condition_change",
    [
        {"kind": "TRUTHY"},
        {"input_source": "_POST"},
        {"input_key": ""},
        {"true_when": "truthy"},
    ],
)
def test_unsupported_conditions_rejected(valid_ir, condition_change):
    ir = copy.deepcopy(valid_ir)
    ir["condition"].update(condition_change)

    assert validate_conditional_select_ir(ir) is False


def test_ir_does_not_authorize_conversion(valid_ir):
    assert valid_ir["safe_for_conversion"] is False
    assert valid_ir["status"] == "DIAGNOSTIC_ONLY"
