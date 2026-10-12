"""Guarded conversion of the supported Q011 dynamic IN-list query."""

from c3.conversion.in_list_source_gate import validate_q011_source_shape


EXPECTED_SQL = (
    "SELECT id, full_name FROM patients "
    "WHERE id IN ({{ids}})"
)

PARAMETERIZED_SQL = (
    "SELECT id, full_name FROM patients WHERE id IN :ids"
)


def convert_q011_in_list(query: dict) -> dict | None:
    """Return Q011 expanding-bind IR only when all checks succeed."""

    if not isinstance(query, dict):
        return None

    if (
        query.get("query_id") != "Q011"
        or query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "STRUCTURAL"
        or query.get("recovered_sql") != EXPECTED_SQL
        or query.get("dynamic_variables") != ["ids"]
        or query.get("executor") != "mysqli_query"
        or query.get("sql_argument")
        != '"SELECT id, full_name FROM patients WHERE id IN ({$ids})"'
    ):
        return None

    source_file = query.get("source_file")

    if not isinstance(source_file, str):
        return None

    if not validate_q011_source_shape(source_file):
        return None

    return {
        "query_id": "Q011",
        "source_file": source_file,
        "source_line": query.get("source_line"),
        "operation": "SELECT",
        "table": "patients",
        "query_shape": "EXPANDING_IN_SELECT",
        "parameterized_sql": PARAMETERIZED_SQL,
        "parameters": ["ids"],
        "binding_type": "expanding_integer_list",
        "input_source": "_GET",
        "input_key": "ids",
        "fallback": "1,2",
        "status": "CONVERTED",
        "reason": None,
    }
