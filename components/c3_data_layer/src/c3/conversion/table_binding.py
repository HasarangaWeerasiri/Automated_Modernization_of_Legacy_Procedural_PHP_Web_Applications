


# Q013: allow only the validated legacy table.
ALLOWED_Q013_TABLES = frozenset({"patients"})


def resolve_q013_table(inputs: dict) -> str:
    """Resolve a Q013 table name through a strict allowlist."""

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get("table")

    if value is None:
        value = "patients"

    if not isinstance(value, str):
        raise ValueError("Table name must be a string.")

    if value not in ALLOWED_Q013_TABLES:
        raise ValueError("Unsupported table identifier.")

    return value
