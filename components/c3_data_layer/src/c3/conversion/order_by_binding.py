"""Validated ORDER BY identifiers for the supported Q012 query.

The allowlist is passed in by the generated code; it is derived from the
recovered schema (see c3.conversion.identifier_allowlist).
"""

from c3.conversion.identifier_allowlist import resolve_identifier


def resolve_q012_sort(inputs: dict, allowed_columns) -> str:
    """Resolve a safe column using Q012's PHP null-coalescing fallback."""

    return resolve_identifier(
        inputs,
        key="sort",
        fallback="full_name",
        allowed=allowed_columns,
        description="ORDER BY column",
    )
