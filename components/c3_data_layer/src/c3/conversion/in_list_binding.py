"""Validated binding for legacy PHP SQL IN-list inputs."""

import re


ID_LIST_PATTERN = re.compile(r"[0-9]+(?:,[0-9]+)*")

MAX_ID = 9223372036854775807


def parse_integer_id_list(value: str) -> list[int]:
    """Parse a nonempty comma-separated list of positive integer IDs."""

    if not isinstance(value, str):
        raise ValueError("ID list must be a string.")

    if not ID_LIST_PATTERN.fullmatch(value):
        raise ValueError("Invalid comma-separated ID list.")

    ids = [int(part) for part in value.split(",")]

    if any(identifier < 1 or identifier > MAX_ID for identifier in ids):
        raise ValueError("ID outside supported range.")

    return ids


def resolve_q011_ids(inputs: dict) -> list[int]:
    """Apply the PHP null-coalescing fallback for the ids input."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get("ids")

    if value is None:
        value = "1,2"

    return parse_integer_id_list(value)
