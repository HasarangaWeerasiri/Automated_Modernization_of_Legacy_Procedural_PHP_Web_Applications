"""Input binding utilities for migrated legacy queries."""


def resolve_binding_value(binding: dict, inputs: dict):
    """Resolve a supported null-coalescing input binding."""

    if binding.get("binding_type") != "scalar":
        raise ValueError("Unsupported binding type.")

    if binding.get("fallback_policy") != "null_coalescing":
        raise ValueError("Unsupported fallback policy.")

    if binding.get("source") != "_GET":
        raise ValueError("Unsupported input source.")

    key = binding.get("key")

    if not isinstance(key, str) or not key:
        raise ValueError("Invalid input key.")

    fallback = binding.get("fallback")

    if not isinstance(fallback, str):
        raise ValueError("Invalid fallback value.")

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    value = inputs.get(key)

    if value is None:
        return fallback

    return value


def validate_numeric_identifier(value: str) -> int:
    """Validate a non-negative decimal identifier."""

    if not isinstance(value, str):
        raise ValueError("Identifier must be a string.")

    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("Invalid numeric identifier.")

    return int(value)


def resolve_validated_bindings(
    binding_plan: list[dict],
    inputs: dict,
) -> dict:
    """Resolve and validate supported SQL input bindings."""

    if not isinstance(binding_plan, list):
        raise ValueError("Invalid binding plan.")

    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a dictionary.")

    resolved = {}

    for binding in binding_plan:
        if not isinstance(binding, dict):
            raise ValueError("Invalid binding entry.")

        parameter = binding.get("parameter")

        if not isinstance(parameter, str) or not parameter:
            raise ValueError("Invalid parameter name.")

        if parameter in resolved:
            raise ValueError("Duplicate binding parameter.")

        if binding.get("requires_validation") is not True:
            raise ValueError("Unvalidated input binding.")

        value = resolve_binding_value(binding, inputs)

        # Currently supported for Q015's numeric department ID.
        resolved[parameter] = validate_numeric_identifier(value)

    return resolved
