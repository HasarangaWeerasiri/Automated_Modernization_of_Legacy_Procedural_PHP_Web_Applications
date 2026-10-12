"""Guarded conversion for the narrowly supported Q014 conditional SELECT."""

from pathlib import Path

from c3.conversion.branch_ir import validate_conditional_select_ir
from c3.mapping.branch_source_gate import validate_q014_source_shape


EXPECTED_CONDITION = {
    "kind": "ISSET",
    "input_source": "_GET",
    "input_key": "active",
    "condition_variable": "activeOnly",
    "true_when": "present_and_not_null",
}

EXPECTED_BRANCHES = {
    "IF": "SELECT id, full_name FROM doctors WHERE active = 1",
    "ELSE": "SELECT id, full_name FROM doctors",
}


def convert_q014_conditional(query: dict) -> dict | None:
    """
    Convert only the verified Q014 source shape.

    Return None when any required evidence is missing or invalid.
    This function does not modify the input query.
    """

    if not isinstance(query, dict):
        return None

    if (
        query.get("query_id") != "Q014"
        or query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "BRANCHING"
    ):
        return None

    candidate = query.get("conditional_candidate")

    if not isinstance(candidate, dict):
        return None

    if (
        candidate.get("query_id") != "Q014"
        or candidate.get("source_file") != query.get("source_file")
        or candidate.get("validation_status") != "STRUCTURAL_CANDIDATE"
        or candidate.get("unsupported_operations_checked") is not True
        or candidate.get("source_shape_verified") is not True
        or candidate.get("safe_for_conversion") is not False
    ):
        return None

    source_file = query.get("source_file")

    if not isinstance(source_file, str):
        return None

    source_path = Path(source_file)

    if source_path.name != "14_branch_query.php":
        return None

    # Re-read the actual PHP source. Never trust the stored flag alone.
    if not validate_q014_source_shape(source_path):
        return None

    if candidate.get("condition") != EXPECTED_CONDITION:
        return None

    if candidate.get("branches") != EXPECTED_BRANCHES:
        return None

    diagnostic_ir = {
        "query_shape": "CONDITIONAL_SELECT",
        "query_id": "Q014",
        "operation": "SELECT",
        "status": "DIAGNOSTIC_ONLY",
        "safe_for_conversion": False,
        "unsupported_operations_checked": True,
        "condition": candidate["condition"],
        "branches": candidate["branches"],
    }

    if not validate_conditional_select_ir(diagnostic_ir):
        return None

    return {
        "query_id": "Q014",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "doctors",
        "query_shape": "CONDITIONAL_SELECT",
        "condition": dict(EXPECTED_CONDITION),
        "branches": dict(EXPECTED_BRANCHES),
        "parameters": [],
        "status": "CONVERTED",
        "reason": None,
    }
