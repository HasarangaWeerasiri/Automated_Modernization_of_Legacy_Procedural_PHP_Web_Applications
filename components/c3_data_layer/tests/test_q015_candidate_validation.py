from copy import deepcopy
from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import (
    _validated_cross_file_candidate,
    recover_queries_from_ast,
)


@pytest.fixture
def diagnostics():
    root = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
    )

    queries = recover_queries_from_ast(analyze_php_ast(root))
    q015 = next(q for q in queries if q["query_id"] == "Q015")

    assert q015["status"] == "RECOVERED"

    return deepcopy(q015["cross_file_diagnostics"])


def test_q015_valid_candidate(diagnostics):
    candidate = _validated_cross_file_candidate(
        diagnostics,
        None,
    )

    assert candidate is not None
    assert candidate["operation"] == "SELECT"
    assert candidate["dynamic_variables"] == ["departmentId"]
    assert candidate["binding_plan"] == diagnostics["binding_plan"]


@pytest.mark.parametrize(
    "reason",
    ["BRANCHING", "CONDITIONAL_APPEND"],
)
def test_q015_branching_rejected(diagnostics, reason):
    assert _validated_cross_file_candidate(
        diagnostics,
        reason,
    ) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("resolution_status", "UNRESOLVED"),
        ("requires_include_validation", True),
        ("sql_template", ""),
        ("binding_plan", []),
        (
            "sql_template",
            "SELECT * FROM doctors WHERE id = {{other}}",
        ),
        (
            "sql_template",
            "SELECT * FROM {{table}} WHERE id = {{departmentId}}",
        ),
        (
            "sql_template",
            "DELETE FROM doctors WHERE id = {{departmentId}}",
        ),
    ],
)
def test_q015_unsafe_candidate_rejected(
    diagnostics, field, value
):
    modified = deepcopy(diagnostics)
    modified[field] = value

    assert _validated_cross_file_candidate(
        modified,
        None,
    ) is None


def test_q015_duplicate_binding_rejected(diagnostics):
    modified = deepcopy(diagnostics)
    modified["binding_plan"].append(
        deepcopy(modified["binding_plan"][0])
    )

    assert _validated_cross_file_candidate(
        modified,
        None,
    ) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "_POST"),
        ("key", ""),
        ("fallback_policy", "truthy_fallback"),
        ("fallback", None),
        ("binding_type", "identifier"),
        ("requires_validation", False),
    ],
)
def test_q015_invalid_binding_metadata_rejected(
    diagnostics, field, value
):
    modified = deepcopy(diagnostics)
    modified["binding_plan"][0][field] = value

    assert _validated_cross_file_candidate(
        modified,
        None,
    ) is None
