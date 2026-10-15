"""Guarded conversion of Q016 conditional SQL concatenation."""

from pathlib import Path

from c3.conversion.conditional_concat_source_gate import (
    validate_q016_source_shape,
)


BASE_SQL = "SELECT id, full_name FROM patients WHERE 1=1"

FILTERED_SQL = (
    "SELECT id, full_name FROM patients "
    "WHERE 1=1 AND department_id = :department_id"
)

# Since H1 (2026-10-15) recovery names array elements ($_GET['department_id']
# -> {{GET_department_id}}) instead of leaving them unresolved; the gate
# checks the new, more precise representation (template audit: Sunday).
EXPECTED_RECOVERED_SQL = (
    "SELECT id, full_name FROM patients WHERE 1=1 AND "
    "department_id = {{GET_department_id}}"
)


def convert_q016_conditional_concat(query: dict) -> dict | None:
    """Convert only the validated Q016 conditional SQL pattern."""

    if not isinstance(query, dict):
        return None

    if (
        query.get("query_id") != "Q016"
        or query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "BRANCHING"
        or query.get("executor") != "mysqli_query"
        or query.get("sql_argument") != "$sql"
        or query.get("recovered_sql") != EXPECTED_RECOVERED_SQL
        or query.get("dynamic_variables") != ["GET_department_id"]
    ):
        return None

    variants = query.get("sql_variants")

    if not isinstance(variants, list) or len(variants) != 2:
        return None

    expected_base_variant = (
        "SELECT id, full_name FROM patients WHERE 1=1"
    )
    expected_filtered_variant = EXPECTED_RECOVERED_SQL

    if not all(isinstance(v, dict) for v in variants):
        return None

    if (
        variants[0].get("control_flow") != []
        or variants[0].get("fully_supported") is not True
        or variants[0].get("sql") != expected_base_variant
        or variants[1].get("control_flow") != ["IF"]
        or variants[1].get("fully_supported") is not True
        or variants[1].get("sql") != expected_filtered_variant
    ):
        return None

    source_file = query.get("source_file")

    if (
        not isinstance(source_file, str)
        or Path(source_file).name != "16_conditional_concat.php"
        or not validate_q016_source_shape(source_file)
    ):
        return None

    return {
        "query_id": "Q016",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "patients",
        "query_shape": "CONDITIONAL_CONCAT_SELECT",
        "base_sql": BASE_SQL,
        "filtered_sql": FILTERED_SQL,
        "condition_source": "_GET",
        "condition_key": "department_id",
        "condition_type": "isset",
        "parameter": "department_id",
        "status": "CONVERTED",
        "reason": None,
    }
