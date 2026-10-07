from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass
class BehaviorDifference:
    """
    Represents one observable difference between the expected
    and actual database state.
    """

    row_index: int
    field: str
    expected: Any
    actual: Any


@dataclass
class BehaviorValidationResult:
    """
    Result of comparing expected and actual database behavior.
    """

    equivalent: bool
    expected_row_count: int
    actual_row_count: int
    differences: list[BehaviorDifference]


def _normalize_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    Convert SQLAlchemy RowMapping objects or normal mappings
    into plain dictionaries.
    """
    return [dict(row) for row in rows]


def compare_database_state(
    expected_rows: Sequence[Mapping[str, Any]],
    actual_rows: Sequence[Mapping[str, Any]],
    *,
    key: str | None = None,
) -> BehaviorValidationResult:
    """
    Compare expected database rows with actual database rows.

    If a key is supplied, both collections are sorted by that
    field before comparison so row ordering does not create a
    false behavioral difference.
    """

    expected = _normalize_rows(expected_rows)
    actual = _normalize_rows(actual_rows)

    if key is not None:
        expected = sorted(expected, key=lambda row: row[key])
        actual = sorted(actual, key=lambda row: row[key])

    differences: list[BehaviorDifference] = []

    if len(expected) != len(actual):
        differences.append(
            BehaviorDifference(
                row_index=-1,
                field="__row_count__",
                expected=len(expected),
                actual=len(actual),
            )
        )

    comparable_count = min(
        len(expected),
        len(actual),
    )

    for index in range(comparable_count):
        expected_row = expected[index]
        actual_row = actual[index]

        fields = sorted(
            set(expected_row.keys())
            | set(actual_row.keys())
        )

        for field in fields:
            expected_value = expected_row.get(field)
            actual_value = actual_row.get(field)

            if expected_value != actual_value:
                differences.append(
                    BehaviorDifference(
                        row_index=index,
                        field=field,
                        expected=expected_value,
                        actual=actual_value,
                    )
                )

    return BehaviorValidationResult(
        equivalent=len(differences) == 0,
        expected_row_count=len(expected),
        actual_row_count=len(actual),
        differences=differences,
    )