import pytest

from c3.conversion.input_binding import resolve_binding_value


@pytest.fixture
def binding():
    return {
        "source": "_GET",
        "key": "department_id",
        "fallback": "1",
        "fallback_policy": "null_coalescing",
        "binding_type": "scalar",
    }


@pytest.mark.parametrize(
    "inputs, expected",
    [
        ({}, "1"),
        ({"department_id": None}, "1"),
        ({"department_id": "2"}, "2"),
        ({"department_id": "0"}, "0"),
        ({"department_id": ""}, ""),
    ],
)
def test_q015_null_coalescing(binding, inputs, expected):
    assert resolve_binding_value(binding, inputs) == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "_POST"),
        ("binding_type", "identifier"),
        ("fallback_policy", "truthy_fallback"),
        ("key", ""),
        ("fallback", None),
    ],
)
def test_unsupported_binding_is_rejected(binding, field, value):
    binding[field] = value

    with pytest.raises(ValueError):
        resolve_binding_value(binding, {"department_id": "2"})


def test_non_dictionary_input_is_rejected(binding):
    with pytest.raises(ValueError):
        resolve_binding_value(binding, None)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0),
        ("1", 1),
        ("2", 2),
        ("001", 1),
    ],
)
def test_numeric_identifier_valid(value, expected):
    from c3.conversion.input_binding import validate_numeric_identifier

    assert validate_numeric_identifier(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        "1 OR 1=1",
        "-1",
        "1.5",
        " 1",
        "1 ",
        None,
        1,
        "?",
    ],
)
def test_numeric_identifier_invalid(value):
    from c3.conversion.input_binding import validate_numeric_identifier

    with pytest.raises(ValueError):
        validate_numeric_identifier(value)


def test_q015_resolved_value_is_validated(binding):
    from c3.conversion.input_binding import validate_numeric_identifier

    value = resolve_binding_value(binding, {})
    assert validate_numeric_identifier(value) == 1

    value = resolve_binding_value(
        binding,
        {"department_id": "1 OR 1=1"},
    )

    with pytest.raises(ValueError):
        validate_numeric_identifier(value)


def test_q015_validated_binding_integration(binding):
    from c3.conversion.input_binding import resolve_validated_bindings

    binding["parameter"] = "departmentId"
    binding["requires_validation"] = True

    assert resolve_validated_bindings(
        [binding], {}
    ) == {"departmentId": 1}

    assert resolve_validated_bindings(
        [binding], {"department_id": "2"}
    ) == {"departmentId": 2}

    assert resolve_validated_bindings(
        [binding], {"department_id": "0"}
    ) == {"departmentId": 0}


@pytest.mark.parametrize(
    "value",
    ["", "abc", "1 OR 1=1", "-1", "1.5"],
)
def test_q015_validated_binding_rejects_unsafe_input(binding, value):
    from c3.conversion.input_binding import resolve_validated_bindings

    binding["parameter"] = "departmentId"
    binding["requires_validation"] = True

    with pytest.raises(ValueError):
        resolve_validated_bindings(
            [binding],
            {"department_id": value},
        )


def test_q015_validated_binding_rejects_duplicates(binding):
    from c3.conversion.input_binding import resolve_validated_bindings

    binding["parameter"] = "departmentId"
    binding["requires_validation"] = True

    with pytest.raises(ValueError):
        resolve_validated_bindings(
            [binding, dict(binding)],
            {},
        )


def test_q015_validated_binding_requires_validation_flag(binding):
    from c3.conversion.input_binding import resolve_validated_bindings

    binding["parameter"] = "departmentId"
    binding["requires_validation"] = False

    with pytest.raises(ValueError):
        resolve_validated_bindings([binding], {})
