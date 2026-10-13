"""Validated appointment IDs for Q020 cross-table UPDATE."""

import re

ID_PATTERN = re.compile(r"[0-9]+\Z")
MAX_ID = 2**31 - 1


def resolve_q020_id(inputs: dict) -> int:
    """Resolve the legacy default or a validated appointment ID."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get("id")

    if value is None:
        value = "1"

    if isinstance(value, bool):
        raise ValueError("Appointment ID must be a positive integer.")

    if isinstance(value, int):
        appointment_id = value
    elif isinstance(value, str) and ID_PATTERN.fullmatch(value):
        appointment_id = int(value)
    else:
        raise ValueError("Appointment ID must be a positive integer.")

    if not 1 <= appointment_id <= MAX_ID:
        raise ValueError("Appointment ID is outside the supported range.")

    return appointment_id
