from c3.validation.behavior_validator import (
    compare_database_state,
)


def test_identical_states_are_equivalent():
    expected = [
        {
            "id": 1,
            "name": "Alice",
            "status": "active",
        }
    ]

    actual = [
        {
            "id": 1,
            "name": "Alice",
            "status": "active",
        }
    ]

    result = compare_database_state(
        expected,
        actual,
        key="id",
    )

    assert result.equivalent is True
    assert result.differences == []


def test_changed_value_is_detected():
    expected = [
        {
            "id": 1,
            "name": "Alice",
            "status": "active",
        }
    ]

    actual = [
        {
            "id": 1,
            "name": "Alice Updated",
            "status": "active",
        }
    ]

    result = compare_database_state(
        expected,
        actual,
        key="id",
    )

    assert result.equivalent is False
    assert len(result.differences) == 1

    difference = result.differences[0]

    assert difference.field == "name"
    assert difference.expected == "Alice"
    assert difference.actual == "Alice Updated"


def test_missing_row_is_detected():
    expected = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
    ]

    actual = [
        {"id": 1, "name": "Alice"},
    ]

    result = compare_database_state(
        expected,
        actual,
        key="id",
    )

    assert result.equivalent is False
    assert result.expected_row_count == 2
    assert result.actual_row_count == 1

    assert any(
        difference.field == "__row_count__"
        for difference in result.differences
    )


def test_row_order_does_not_affect_comparison():
    expected = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
    ]

    actual = [
        {"id": 2, "name": "Bob"},
        {"id": 1, "name": "Alice"},
    ]

    result = compare_database_state(
        expected,
        actual,
        key="id",
    )

    assert result.equivalent is True


def test_extra_field_is_detected():
    expected = [
        {
            "id": 1,
            "name": "Alice",
        }
    ]

    actual = [
        {
            "id": 1,
            "name": "Alice",
            "status": "active",
        }
    ]

    result = compare_database_state(
        expected,
        actual,
        key="id",
    )

    assert result.equivalent is False

    assert any(
        difference.field == "status"
        for difference in result.differences
    )