"""Guarded conversion of Q012 dynamic ORDER BY."""

from pathlib import Path

from c3.conversion.order_by_source_gate import (
    validate_q012_source_shape,
)


EXPECTED_SQL = (
    "SELECT id, full_name FROM patients ORDER BY {{sort}}"
)

BASE_SQL = "SELECT id, full_name FROM patients"


def convert_q012_order_by(query: dict) -> dict | None:
    """Create Q012 IR only for its validated legacy source."""

    if not isinstance(query, dict):
        return None

    if (
        query.get("query_id") != "Q012"
        or query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "STRUCTURAL"
        or query.get("recovered_sql") != EXPECTED_SQL
        or query.get("dynamic_variables") != ["sort"]
        or query.get("executor") != "mysqli_query"
        or query.get("sql_argument")
        != '"SELECT id, full_name FROM patients ORDER BY {$sort}"'
    ):
        return None

    source_file = query.get("source_file")

    if (
        not isinstance(source_file, str)
        or Path(source_file).name != "12_order_by.php"
        or not validate_q012_source_shape(source_file)
    ):
        return None

    return {
        "query_id": "Q012",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "patients",
        "query_shape": "ORDER_BY_SELECT",
        "base_sql": BASE_SQL,
        "allowed_columns": ["id", "full_name"],
        "input_source": "_GET",
        "input_key": "sort",
        "fallback": "full_name",
        "status": "CONVERTED",
        "reason": None,
    }
