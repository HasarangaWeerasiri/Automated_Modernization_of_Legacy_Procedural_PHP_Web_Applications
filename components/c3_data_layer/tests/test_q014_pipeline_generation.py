from pathlib import Path
"""End-to-end Q014 pipeline generation and execution checks."""

import ast
from copy import deepcopy

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "clinic_inventory_legacy_hard"
    / "legacy"
)

SQL_ACTIVE = "SELECT id, full_name FROM doctors WHERE active = 1"
SQL_ALL = "SELECT id, full_name FROM doctors"


def build_pipeline():
    ast_data = analyze_php_ast(LEGACY_DIR)
    recovered = recover_queries_from_ast(ast_data)
    converted = convert_queries(recovered)
    source = generate_data_access_source(converted)
    return recovered, converted, source


class RecordingResult:
    def mappings(self):
        return self

    def all(self):
        return [{"id": 1, "full_name": "Example Doctor"}]


class RecordingSession:
    def __init__(self):
        self.executed_sql = []

    def execute(self, statement):
        self.executed_sql.append(str(statement))
        return RecordingResult()


def test_q014_generated_in_full_pipeline():
    recovered, converted, source = build_pipeline()

    assert len(recovered) == 21
    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 18

    q014 = next(q for q in converted if q["query_id"] == "Q014")

    assert q014["status"] == "CONVERTED"
    assert q014["query_shape"] == "CONDITIONAL_SELECT"

    ast.parse(source)
    assert source.count("def select_doctors_q014(") == 1


@pytest.mark.parametrize(
    "inputs,expected_sql",
    [
        ({}, SQL_ALL),
        ({"active": None}, SQL_ALL),
        ({"active": "1"}, SQL_ACTIVE),
        ({"active": "0"}, SQL_ACTIVE),
        ({"active": ""}, SQL_ACTIVE),
        ({"active": False}, SQL_ACTIVE),
    ],
)
def test_generated_q014_executes_correct_branch(inputs, expected_sql):
    _, _, source = build_pipeline()

    namespace = {}
    exec(compile(source, "<generated_data_access>", "exec"), namespace)

    function = namespace["select_doctors_q014"]
    session = RecordingSession()

    result = function(session, inputs)

    assert session.executed_sql == [expected_sql]
    assert result == [{"id": 1, "full_name": "Example Doctor"}]


@pytest.mark.parametrize(
    "modification",
    [
        lambda q: q["condition"].update(input_key="other"),
        lambda q: q["branches"].update(IF="DELETE FROM doctors"),
        lambda q: q.update(source_file="missing/14_branch_query.php"),
        lambda q: q.update(table="users"),
        lambda q: q.update(query_shape="UNKNOWN"),
    ],
)
def test_invalid_conditional_ir_rejected(modification):
    _, converted, _ = build_pipeline()

    q014 = deepcopy(
        next(q for q in converted if q["query_id"] == "Q014")
    )
    modification(q014)

    with pytest.raises(ValueError):
        generate_data_access_source([q014])
