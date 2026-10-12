"""Validated inputs for Q016 conditional SQL concatenation."""

import re


INTEGER_PATTERN = re.compile(r"[0-9]+\Z")
MAX_ID = 2**63 - 1


def resolve_q016_department(inputs: dict) -> int | None:
    """Return a safe department ID, or None for an absent/null value."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get("department_id")

    # Corresponds to the original PHP isset() condition.
    if value is None:
        return None

    # Do not accept bool: bool is a subclass of int in Python.
    if isinstance(value, bool):
        raise ValueError("Department ID must be a positive integer.")

    if isinstance(value, int):
        department_id = value
    elif isinstance(value, str) and INTEGER_PATTERN.fullmatch(value):
        department_id = int(value)
    else:
        raise ValueError("Department ID must be a positive integer.")

    if not 1 <= department_id <= MAX_ID:
        raise ValueError("Department ID is outside the supported range.")

    return department_id
