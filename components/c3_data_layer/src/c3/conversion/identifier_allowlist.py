"""Identifier allowlists derived from the recovered schema.

Dynamic SQL identifiers (ORDER BY columns, table names) cannot be bound as
parameters. They are only allowed when the runtime value is one of a fixed
set of identifiers, and that set is derived from schema.json, never written
by hand for a particular application.
"""

import re

from sqlglot import exp, parse_one

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def schema_columns(schema: dict | None, table: str) -> list[str] | None:
    """Column names of a table in schema.json order, or None if unknown."""

    if not schema:
        return None

    for candidate in schema.get("tables", []):
        if candidate.get("name") == table:
            return [column["name"] for column in candidate.get("columns", [])]

    return None


def tables_with_columns(
    schema: dict | None,
    required_columns: set[str],
) -> list[str] | None:
    """Sorted names of tables that have every required column."""

    if not schema:
        return None

    required = {name.lower() for name in required_columns}

    return sorted(
        table["name"]
        for table in schema.get("tables", [])
        if required <= {column["name"].lower() for column in table.get("columns", [])}
    )


def columns_referenced(sql_with_table_placeholder: str) -> set[str]:
    """Columns referenced by a SQL template whose table is '{table}'."""

    tree = parse_one(
        sql_with_table_placeholder.format(table="placeholder_table"),
        dialect="mysql",
    )

    return {column.name for column in tree.find_all(exp.Column)}


def validate_identifier_allowlist(values, fallback: str) -> tuple[str, ...]:
    """
    Return the allowlist as a tuple, or raise ValueError.

    Every entry must be a plain SQL identifier, and the fallback value the
    legacy code uses when no input is given must itself be allowed.
    """

    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("Identifier allowlist must be a non-empty list.")

    for value in values:
        if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid identifier in allowlist: {value!r}")

    if fallback not in values:
        raise ValueError("Fallback identifier is not in the allowlist.")

    return tuple(values)


def resolve_identifier(
    inputs: dict,
    key: str,
    fallback: str,
    allowed,
    description: str,
) -> str:
    """Resolve an identifier input with PHP ?? fallback semantics."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    allowed = validate_identifier_allowlist(allowed, fallback)

    value = inputs.get(key)

    if value is None:
        value = fallback

    if not isinstance(value, str):
        raise ValueError(f"{description} must be a string.")

    if value not in allowed:
        raise ValueError(f"Unsupported {description}.")

    return value
