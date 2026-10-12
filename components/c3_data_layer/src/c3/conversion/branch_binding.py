"""Resolve supported PHP branch conditions."""


def resolve_isset_condition(condition: dict, inputs: dict) -> bool:
    """Evaluate a supported isset($_GET[key]) condition."""

    if not isinstance(condition, dict):
        raise ValueError("Invalid branch condition.")

    if (
        condition.get("kind") != "ISSET"
        or condition.get("input_source") != "_GET"
        or condition.get("true_when") != "present_and_not_null"
    ):
        raise ValueError("Unsupported branch condition.")

    key = condition.get("input_key")

    if not isinstance(key, str) or not key:
        raise ValueError("Invalid input key.")

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    return key in inputs and inputs[key] is not None
