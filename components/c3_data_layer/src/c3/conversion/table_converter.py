"""Guarded conversion of Q013 dynamic table identifiers."""

from pathlib import Path

from c3.conversion.identifier_allowlist import (
    columns_referenced,
    tables_with_columns,
)
from c3.conversion.table_source_gate import (
    validate_q013_source_shape,
)


EXPECTED_SQL = "SELECT id FROM {{table}} WHERE id = 1"
SQL_TEMPLATE = "SELECT id FROM {table} WHERE id = 1"


def convert_q013_table(query: dict, schema: dict | None = None) -> dict | None:
    """
    Return a converted Q013 representation only for validated source.

    The table allowlist is every table in the recovered schema that has all
    columns the query references. Without a schema it is only the legacy
    fallback table (most restrictive safe choice).
    """

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

    if schema is None:
        allowed_tables = ["patients"]
    else:
        allowed_tables = tables_with_columns(
            schema,
            columns_referenced(SQL_TEMPLATE),
        )

        if not allowed_tables or "patients" not in allowed_tables:
            return None

    return {
        "query_id": "Q013",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "patients",
        "query_shape": "TABLE_IDENTIFIER_SELECT",
        "sql_template": SQL_TEMPLATE,
        "allowed_tables": allowed_tables,
        "allowlist_source": "schema" if schema is not None else "fallback_only",
        "input_source": "_GET",
        "input_key": "table",
        "fallback": "patients",
        "status": "CONVERTED",
        "reason": None,
    }
