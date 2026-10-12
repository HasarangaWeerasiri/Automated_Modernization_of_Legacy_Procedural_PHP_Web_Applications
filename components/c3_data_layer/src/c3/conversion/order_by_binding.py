"""Validated ORDER BY identifiers for the supported Q012 query."""


ALLOWED_SORT_COLUMNS = frozenset({"id", "full_name"})


def resolve_q012_sort(inputs: dict) -> str:
    """Resolve a safe column using Q012's PHP null-coalescing fallback."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get("sort")

    if value is None:
        value = "full_name"

    if not isinstance(value, str):
        raise ValueError("Sort column must be a string.")

    if value not in ALLOWED_SORT_COLUMNS:
        raise ValueError("Unsupported ORDER BY column.")

    return value
