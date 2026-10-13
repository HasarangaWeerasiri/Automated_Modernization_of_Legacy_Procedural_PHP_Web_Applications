"""Guarded conversion of the Q020 MySQL UPDATE JOIN query."""

from pathlib import Path

from c3.conversion.join_update_source_gate import (
    validate_q020_source_shape,
)


EXPECTED_SQL = (
    "UPDATE appointments a JOIN doctors d ON d.id = a.doctor_id "
    "SET a.fee = d.hourly_rate WHERE a.id = {{id}}"
)

PARAMETERIZED_SQL = (
    "UPDATE appointments a JOIN doctors d ON d.id = a.doctor_id "
    "SET a.fee = d.hourly_rate WHERE a.id = :id"
)


def convert_q020_join_update(query: dict) -> dict | None:
    """Convert only the recognized, source-verified Q020 query."""

    if not isinstance(query, dict):
        return None

    expected = {
        "query_id": "Q020",
        "status": "RECOVERED",
        "operation": "UPDATE",
        "executor": "mysqli_query",
        "sql_argument": "$sql",
        "recovered_sql": EXPECTED_SQL,
        "dynamic_variables": ["id"],
    }

    for field, value in expected.items():
        if query.get(field) != value:
            return None

    if query.get("reason") is not None:
        return None

    if query.get("reason_code") is not None:
        return None

    source_file = query.get("source_file")

    if not isinstance(source_file, str):
        return None

    if Path(source_file).name != "20_cross_table_update.php":
        return None

    if not validate_q020_source_shape(source_file):
        return None

    return {
        "query_id": "Q020",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "UPDATE",
        "table": "appointments",
        "query_shape": "MYSQL_JOIN_UPDATE",
        "sql": PARAMETERIZED_SQL,
        "parameters": ["id"],
        "input_source": "_POST",
        "default_id": "1",
        "status": "CONVERTED",
        "reason": None,
    }
