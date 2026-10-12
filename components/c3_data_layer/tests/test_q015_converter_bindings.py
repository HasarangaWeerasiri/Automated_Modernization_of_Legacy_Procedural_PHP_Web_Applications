from copy import deepcopy
from pathlib import Path

import pytest

from c3.conversion.query_converter import convert_query
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


@pytest.fixture
def q015_candidate():
    root = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    queries = recover_queries_from_ast(analyze_php_ast(root))
    q015 = next(q for q in queries if q["query_id"] == "Q015")
    diagnostics = q015["cross_file_diagnostics"]

    assert q015["status"] == "RECOVERED"
    assert diagnostics["resolution_status"] == "DIAGNOSTIC_ONLY"

    return {
        **q015,
        "status": "RECOVERED",
        "operation": "SELECT",
        "recovered_sql": diagnostics["sql_template"],
        "binding_plan": deepcopy(diagnostics["binding_plan"]),
        "reason": None,
        "reason_code": None,
    }


def test_q015_valid_binding_plan_is_preserved(q015_candidate):
    converted = convert_query(q015_candidate)

    assert converted["status"] == "CONVERTED"
    assert converted["parameters"] == ["departmentId"]
    assert converted["binding_plan"] == q015_candidate["binding_plan"]
    assert ":departmentId" in converted["parameterized_sql"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "_POST"),
        ("binding_type", "identifier"),
        ("fallback_policy", "truthy_fallback"),
        ("key", ""),
        ("fallback", None),
        ("parameter", "otherParameter"),
    ],
)
def test_q015_invalid_binding_rejected(q015_candidate, field, value):
    candidate = deepcopy(q015_candidate)
    candidate["binding_plan"][0][field] = value

    converted = convert_query(candidate)

    assert converted["status"] == "UNRESOLVED"
    assert converted["reason"]


def test_q015_duplicate_binding_rejected(q015_candidate):
    candidate = deepcopy(q015_candidate)
    candidate["binding_plan"].append(
        deepcopy(candidate["binding_plan"][0])
    )

    converted = convert_query(candidate)

    assert converted["status"] == "UNRESOLVED"


def test_q015_missing_binding_rejected(q015_candidate):
    candidate = deepcopy(q015_candidate)
    candidate["binding_plan"] = []

    converted = convert_query(candidate)

    assert converted["status"] == "UNRESOLVED"
