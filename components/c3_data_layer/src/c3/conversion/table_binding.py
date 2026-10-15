"""Validated table identifiers for the supported Q013 query.

The allowlist is passed in by the generated code; it is derived from the
recovered schema (see c3.conversion.identifier_allowlist).
"""

from c3.conversion.identifier_allowlist import resolve_identifier


def resolve_q013_table(inputs: dict, allowed_tables) -> str:
    """Resolve a Q013 table name through a schema-derived allowlist."""

    return resolve_identifier(
        inputs,
        key="table",
        fallback="patients",
        allowed=allowed_tables,
        description="table identifier",
    )
