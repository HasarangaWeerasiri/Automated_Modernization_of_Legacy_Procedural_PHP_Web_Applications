import pytest

from c3.conversion.branch_binding import resolve_isset_condition


@pytest.fixture
def condition():
    return {
        "kind": "ISSET",
        "input_source": "_GET",
        "input_key": "active",
        "true_when": "present_and_not_null",
    }


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({}, False),
        ({"active": None}, False),
        ({"active": "1"}, True),
        ({"active": "0"}, True),
        ({"active": ""}, True),
        ({"active": "false"}, True),
    ],
)
def test_q014_isset_semantics(condition, inputs, expected):
    assert resolve_isset_condition(condition, inputs) is expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "BOOLEAN"),
        ("input_source", "_POST"),
        ("input_key", ""),
        ("true_when", "truthy"),
    ],
)
def test_q014_unsupported_condition_rejected(
    condition, field, value
):
    condition[field] = value

    with pytest.raises(ValueError):
        resolve_isset_condition(condition, {"active": "1"})


def test_q014_invalid_inputs_rejected(condition):
    with pytest.raises(ValueError):
        resolve_isset_condition(condition, None)
