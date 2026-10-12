"""Guarded conversion of Q013 dynamic table identifiers."""

from pathlib import Path

from c3.conversion.table_source_gate import (
    validate_q013_source_shape,
)


EXPECTED_SQL = "SELECT id FROM {{table}} WHERE id = 1"
SQL_TEMPLATE = "SELECT id FROM {table} WHERE id = 1"


def convert_q013_table(query: dict) -> dict | None:
    """Return a converted Q013 representation only for validated source."""

    if not isinstance(query, dict):
        return None

    if (
        query.get("query_id") != "Q013"
        or query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "STRUCTURAL"
        or query.get("recovered_sql") != EXPECTED_SQL
        or query.get("dynamic_variables") != ["table"]
        or query.get("executor") != "mysqli_query"
        or query.get("sql_argument")
        != '"SELECT id FROM {$table} WHERE id = 1"'
    ):
        return None

    source_file = query.get("source_file")

    if (
        not isinstance(source_file, str)
        or Path(source_file).name != "13_dynamic_table.php"
        or not validate_q013_source_shape(source_file)
    ):
        return None

    return {
        "query_id": "Q013",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "patients",
        "query_shape": "TABLE_IDENTIFIER_SELECT",
        "sql_template": SQL_TEMPLATE,
        "allowed_tables": ["patients"],
        "input_source": "_GET",
        "input_key": "table",
        "fallback": "patients",
        "status": "CONVERTED",
        "reason": None,
    }
