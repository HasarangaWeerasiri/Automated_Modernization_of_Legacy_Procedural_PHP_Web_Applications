"""
Data Access Code Generator
Component 3 - Data Layer Migration

Generates reusable SQLAlchemy data-access functions
from safely converted SQL queries.

Converted queries are emitted as executable functions.
Unresolved queries are emitted as manual-review stubs.
"""

import json
import re
from pathlib import Path

from c3.conversion.conditional_converter import (
    EXPECTED_BRANCHES,
    EXPECTED_CONDITION,
)
from c3.generation.branch_emitter import (
    generate_conditional_select_source,
)
from c3.mapping.branch_source_gate import (
    validate_q014_source_shape,
)


def _singularize(name: str) -> str:
    """Simple deterministic table-name singularization."""

    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"

    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]

    return name


def _safe_identifier(value: str) -> str:
    """
    Convert a value into a valid lowercase Python-style
    identifier.
    """

    value = re.sub(
        r"[^A-Za-z0-9_]+",
        "_",
        value,
    )

    return value.strip("_").lower()


def _function_name(query: dict) -> str:
    """
    Create a deterministic function name.

    Examples:
        SELECT users -> select_users_q004
        INSERT users -> insert_user_q001
        UPDATE users -> update_user_q003
        DELETE users -> delete_user_q002
    """

    operation = query["operation"].lower()
    table = _safe_identifier(query["table"])

    if operation == "select":
        entity = table
    else:
        entity = _singularize(table)

    query_id = _safe_identifier(
        query["query_id"]
    )

    return (
        f"{operation}_{entity}_{query_id}"
    )


def _build_function(query: dict) -> str:
    """
    Generate one SQLAlchemy data-access function.
    """

    function_name = _function_name(query)

    parameters = query.get(
        "parameters",
        [],
    )

    source_file = str(
        query.get("source_file") or "unknown"
    ).replace("\\", "/")

    source_line = query.get(
        "source_line"
    )

    signature_parts = ["session"]
    signature_parts.extend(parameters)

    signature = ", ".join(
        signature_parts
    )

    sql = query["parameterized_sql"]

    parameter_lines = []

    like_wildcard_parameters = set(
        query.get("like_wildcard_parameters", [])
    )

    for parameter in parameters:
        if parameter in like_wildcard_parameters:
            value_expression = f'f"%{{{parameter}}}%"'
        else:
            value_expression = parameter

        parameter_lines.append(
            f'        "{parameter}": {value_expression},'
        )

    lines = [
        f"def {function_name}({signature}):",
        '    """',
        (
    		f"    Migrated from "
    		f"{source_file}:"
    		f"{source_line}."
	),
        (
            f"    Original query ID: "
            f"{query['query_id']}."
        ),
        '    """',
        "",
        f"    statement = text({sql!r})",
        "",
    ]

    if parameters:
        lines.extend(
            [
                "    params = {",
                *parameter_lines,
                "    }",
                "",
                (
                    "    result = session.execute("
                    "statement, params)"
                ),
            ]
        )

    else:
        lines.append(
            "    result = session.execute(statement)"
        )

    lines.append("")

    if query["operation"] == "SELECT":
        lines.append(
            "    return result.mappings().all()"
        )
    else:
        lines.extend(
            [
                "    session.commit()",
                "    return result",
            ]
        )

    return "\n".join(lines)


def _build_unresolved_stub(query: dict) -> str:
    """Emit a safe function inventory entry without inventing SQL behavior."""
    query_id = _safe_identifier(str(query.get("query_id") or "unknown"))
    name = f"unresolved_query_{query_id}"
    source_file = str(query.get("source_file") or "unknown").replace("\\", "/")
    source_line = query.get("source_line")
    location = f"{source_file}:{source_line if source_line is not None else 'unknown'}"
    message = f"manual review: {location}"
    return "\n".join([
        f"def {name}(session, **kwargs):",
        f"    \"\"\"Unresolved query {query.get('query_id', 'unknown')}; {location}.\"\"\"",
        f"    raise NotImplementedError({message!r})",
    ])


def _build_conditional_function(query: dict) -> str:
    """Generate only the explicitly supported, validated Q014 function."""

    source_file = query.get("source_file")

    if (
        query.get("status") != "CONVERTED"
        or query.get("query_shape") != "CONDITIONAL_SELECT"
        or query.get("query_id") != "Q014"
        or query.get("operation") != "SELECT"
        or query.get("table") != "doctors"
        or not isinstance(source_file, str)
        or Path(source_file).name != "14_branch_query.php"
        or not validate_q014_source_shape(source_file)
        or query.get("condition") != EXPECTED_CONDITION
        or query.get("branches") != EXPECTED_BRANCHES
    ):
        raise ValueError("Invalid converted conditional SELECT IR.")

    diagnostic_ir = {
        "query_shape": "CONDITIONAL_SELECT",
        "query_id": "Q014",
        "operation": "SELECT",
        "status": "DIAGNOSTIC_ONLY",
        "safe_for_conversion": False,
        "unsupported_operations_checked": True,
        "condition": query["condition"],
        "branches": query["branches"],
    }

    generated = generate_conditional_select_source(diagnostic_ir)

    marker = "def select_doctors_q014("

    if generated.count(marker) != 1:
        raise ValueError("Unexpected conditional generator output.")

    return marker + generated.split(marker, 1)[1].rstrip()


def _build_expanding_in_function(query: dict) -> str:
    """Generate only the validated Q011 expanding-IN query."""

    from c3.conversion.in_list_converter import (
        PARAMETERIZED_SQL,
    )
    from c3.conversion.in_list_source_gate import (
        validate_q011_source_shape,
    )

    source_file = query.get("source_file")

    expected = {
        "query_id": "Q011",
        "status": "CONVERTED",
        "query_shape": "EXPANDING_IN_SELECT",
        "operation": "SELECT",
        "table": "patients",
        "parameterized_sql": PARAMETERIZED_SQL,
        "parameters": ["ids"],
        "binding_type": "expanding_integer_list",
        "input_source": "_GET",
        "input_key": "ids",
        "fallback": "1,2",
    }

    if (
        not isinstance(query, dict)
        or any(query.get(key) != value for key, value in expected.items())
        or not isinstance(source_file, str)
        or Path(source_file).name != "11_in_values.php"
        or not validate_q011_source_shape(source_file)
    ):
        raise ValueError("Invalid Q011 expanding-IN representation.")

    return "\n".join([
        "def select_patients_q011(session, inputs):",
        '    """Safely execute the supported Q011 integer ID list."""',
        "    ids = resolve_q011_ids(inputs)",
        f"    statement = text({PARAMETERIZED_SQL!r}).bindparams(",
        '        bindparam("ids", expanding=True)',
        "    )",
        '    return session.execute(statement, {"ids": ids}).mappings().all()',
    ])


def _build_order_by_function(query: dict) -> str:
    """Generate only the validated Q012 ORDER BY query."""

    from c3.conversion.order_by_converter import BASE_SQL
    from c3.conversion.order_by_source_gate import (
        validate_q012_source_shape,
    )

    source_file = query.get("source_file") if isinstance(query, dict) else None

    expected = {
        "query_id": "Q012",
        "status": "CONVERTED",
        "query_shape": "ORDER_BY_SELECT",
        "operation": "SELECT",
        "table": "patients",
        "base_sql": BASE_SQL,
        "allowed_columns": ["id", "full_name"],
        "input_source": "_GET",
        "input_key": "sort",
        "fallback": "full_name",
    }

    if (
        not isinstance(query, dict)
        or any(query.get(key) != value for key, value in expected.items())
        or not isinstance(source_file, str)
        or Path(source_file).name != "12_order_by.php"
        or not validate_q012_source_shape(source_file)
    ):
        raise ValueError("Invalid Q012 ORDER BY representation.")

    return "\n".join([
        "def select_patients_q012(session, inputs):",
        '    """Execute Q012 using a validated ORDER BY identifier."""',
        "    column = resolve_q012_sort(inputs)",
        f"    statement = text(f\"{BASE_SQL} ORDER BY {{column}}\")",
        "    return session.execute(statement).mappings().all()",
    ])


def _build_table_identifier_function(query: dict) -> str:
    """Generate Q013 only from its strictly validated representation."""

    from c3.conversion.table_converter import SQL_TEMPLATE
    from c3.conversion.table_source_gate import (
        validate_q013_source_shape,
    )

    source_file = query.get("source_file") if isinstance(query, dict) else None

    expected = {
        "query_id": "Q013",
        "status": "CONVERTED",
        "query_shape": "TABLE_IDENTIFIER_SELECT",
        "operation": "SELECT",
        "table": "patients",
        "sql_template": SQL_TEMPLATE,
        "allowed_tables": ["patients"],
        "input_source": "_GET",
        "input_key": "table",
        "fallback": "patients",
    }

    if (
        not isinstance(query, dict)
        or any(query.get(key) != value for key, value in expected.items())
        or not isinstance(source_file, str)
        or Path(source_file).name != "13_dynamic_table.php"
        or not validate_q013_source_shape(source_file)
    ):
        raise ValueError("Invalid Q013 table identifier representation.")

    return "\n".join([
        "def select_q013(session, inputs):",
        '    """Execute Q013 with a validated table identifier."""',
        "    table = resolve_q013_table(inputs)",
        '    statement = text(f"SELECT id FROM {table} WHERE id = 1")',
        "    return session.execute(statement).mappings().all()",
    ])


def _build_conditional_concat_function(query: dict) -> str:
    """Generate Q016 only from its guarded converted representation."""

    from c3.conversion.conditional_concat_converter import (
        BASE_SQL,
        FILTERED_SQL,
    )
    from c3.conversion.conditional_concat_source_gate import (
        validate_q016_source_shape,
    )

    source_file = query.get("source_file") if isinstance(query, dict) else None

    expected = {
        "query_id": "Q016",
        "status": "CONVERTED",
        "query_shape": "CONDITIONAL_CONCAT_SELECT",
        "operation": "SELECT",
        "table": "patients",
        "base_sql": BASE_SQL,
        "filtered_sql": FILTERED_SQL,
        "condition_source": "_GET",
        "condition_key": "department_id",
        "condition_type": "isset",
        "parameter": "department_id",
    }

    if (
        not isinstance(query, dict)
        or any(query.get(key) != value for key, value in expected.items())
        or not isinstance(source_file, str)
        or Path(source_file).name != "16_conditional_concat.php"
        or not validate_q016_source_shape(source_file)
    ):
        raise ValueError("Invalid Q016 conditional concatenation representation.")

    return "\n".join([
        "def select_patients_q016(session, inputs):",
        '    """Execute Q016 using safe conditional SQL binding."""',
        "    department_id = resolve_q016_department(inputs)",
        "    if department_id is None:",
        f"        statement = text({BASE_SQL!r})",
        "        return session.execute(statement).mappings().all()",
        f"    statement = text({FILTERED_SQL!r})",
        '    return session.execute(statement, {"department_id": department_id}).mappings().all()',
    ])


def generate_data_access_source(
    converted_queries: list[dict],
) -> str:
    """
    Generate complete data_access.py source code.
    """

    lines = [
        '"""',
        "Generated SQLAlchemy data-access functions.",
        "",
        (
            "Generated by Component 3 - "
            "Data Layer Migration."
        ),
        "",
        (
            "Dynamic legacy values are passed using "
            "SQLAlchemy named bind parameters."
        ),
        '"""',
        "",
        "from sqlalchemy import text",
        "",
        "",
    ]

    functions = []
    has_conditional = False
    has_expanding_in = False
    has_order_by = False
    has_table_identifier = False
    has_conditional_concat = False

    for query in converted_queries:
        if query.get("status") == "CONVERTED":
            shape = query.get("query_shape")

            if shape == "CONDITIONAL_SELECT":
                functions.append(_build_conditional_function(query))
                has_conditional = True
            elif shape == "EXPANDING_IN_SELECT":
                functions.append(_build_expanding_in_function(query))
                has_expanding_in = True
            elif shape == "ORDER_BY_SELECT":
                functions.append(_build_order_by_function(query))
                has_order_by = True
            elif shape == "TABLE_IDENTIFIER_SELECT":
                functions.append(_build_table_identifier_function(query))
                has_table_identifier = True
            elif shape == "CONDITIONAL_CONCAT_SELECT":
                functions.append(_build_conditional_concat_function(query))
                has_conditional_concat = True
            elif shape is None:
                functions.append(_build_function(query))
            else:
                raise ValueError("Unsupported converted query shape.")

        elif query.get("status") == "UNRESOLVED":
            functions.append(_build_unresolved_stub(query))

    if has_conditional:
        lines.insert(
            lines.index("from sqlalchemy import text") + 1,
            "from c3.conversion.branch_binding import resolve_isset_condition",
        )

    if has_expanding_in:
        lines.insert(
            lines.index("from sqlalchemy import text") + 1,
            "from sqlalchemy import bindparam",
        )
        lines.insert(
            lines.index("from sqlalchemy import bindparam") + 1,
            "from c3.conversion.in_list_binding import resolve_q011_ids",
        )

    if has_order_by:
        lines.insert(
            lines.index("from sqlalchemy import text") + 1,
            "from c3.conversion.order_by_binding import resolve_q012_sort",
        )

    if has_table_identifier:
        lines.insert(
            lines.index("from sqlalchemy import text") + 1,
            "from c3.conversion.table_binding import resolve_q013_table",
        )

    if has_conditional_concat:
        lines.insert(
            lines.index("from sqlalchemy import text") + 1,
            "from c3.conversion.conditional_concat_binding import resolve_q016_department",
        )

    lines.append("\n\n\n".join(functions))

    lines.append("")

    return "\n".join(lines)


def load_converted_queries(
    input_file: str | Path,
) -> list[dict]:
    """Load converted_queries.json."""

    input_path = Path(input_file)

    return json.loads(
        input_path.read_text(
            encoding="utf-8"
        )
    )


def generate_data_access(
    input_file: str | Path,
    output_file: str | Path,
) -> None:
    """
    Generate data_access.py from converted query IR.
    """

    converted_queries = (
        load_converted_queries(
            input_file
        )
    )

    source = generate_data_access_source(
        converted_queries
    )

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        source,
        encoding="utf-8",
    )
