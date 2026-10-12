"""Tests for Q014 diagnostic SQLAlchemy generation."""

import copy
import json
import pytest

from pathlib import Path
from unittest.mock import MagicMock

from c3.generation.branch_emitter import (
    generate_conditional_select_source,
)


@pytest.fixture
def diagnostic_ir():
    path = (
        Path(__file__).parent
        / "fixtures"
        / "q014_branch_diagnostic.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def generated_function(diagnostic_ir):
    source = generate_conditional_select_source(diagnostic_ir)
    namespace = {}

    exec(compile(source, "<q014_generated>", "exec"), namespace)

    return namespace["select_doctors_q014"]


@pytest.mark.parametrize(
    "inputs,expected_active",
    [
        ({}, False),
        ({"active": None}, False),
        ({"active": "1"}, True),
        ({"active": "0"}, True),
        ({"active": ""}, True),
        ({"active": "false"}, True),
    ],
)
def test_generated_q014_branch_selection(
    generated_function,
    inputs,
    expected_active,
):
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []

    result = generated_function(session, inputs)

    session.execute.assert_called_once()

    statement = session.execute.call_args.args[0]

    expected_sql = (
        "SELECT id, full_name FROM doctors WHERE active = 1"
        if expected_active
        else "SELECT id, full_name FROM doctors"
    )

    assert str(statement) == expected_sql
    assert result == []


def test_generated_source_compiles(diagnostic_ir):
    source = generate_conditional_select_source(diagnostic_ir)

    compile(source, "<q014_generated>", "exec")


def test_generator_rejects_invalid_ir(diagnostic_ir):
    invalid = copy.deepcopy(diagnostic_ir)
    invalid["safe_for_conversion"] = True

    with pytest.raises(ValueError):
        generate_conditional_select_source(invalid)


def test_generator_rejects_wrong_query_id(diagnostic_ir):
    invalid = copy.deepcopy(diagnostic_ir)
    invalid["query_id"] = "Q999"

    with pytest.raises(ValueError):
        generate_conditional_select_source(invalid)
