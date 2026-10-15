"""LIMIT / OFFSET values for generated code (S8).

Legacy PHP pastes the value into the SQL text (`LIMIT $limit`), where MySQL
accepts only a non-negative integer literal. A bound string parameter would
be sent as a quoted string and fail, so generated code coerces the value
here and rejects anything else before executing.
"""

import re

ROW_COUNT_PATTERN = re.compile(r"[0-9]+")
MAX_ROW_COUNT = 18446744073709551615  # MySQL's largest LIMIT value


def to_row_count(value, name: str = "LIMIT/OFFSET value") -> int:
    """Return value as a non-negative int, or raise ValueError."""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer.")

    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and ROW_COUNT_PATTERN.fullmatch(value.strip()):
        number = int(value.strip())
    else:
        raise ValueError(f"{name} must be a non-negative integer.")

    if number < 0 or number > MAX_ROW_COUNT:
        raise ValueError(f"{name} is out of range.")

    return number
