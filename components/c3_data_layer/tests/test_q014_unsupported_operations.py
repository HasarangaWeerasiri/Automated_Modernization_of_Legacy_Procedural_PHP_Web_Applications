"""Tests for Q014 unsupported-operation rejection."""

import pytest

from c3.mapping.branch_recovery import (
    has_relevant_unsupported_operations,
)


@pytest.fixture
def candidate():
    return {
        "condition": {
            "condition_variable": "activeOnly",
        }
    }


@pytest.fixture
def query():
    return {
        "source_file": "14_branch_query.php",
        "source_line": 10,
        "sql_argument": "$sql",
    }


@pytest.mark.parametrize(
    "operation,expected",
    [
        (
            {
                "source_file": "14_branch_query.php",
                "source_line": 7,
                "operation_type": "UNSET",
                "variables": ["$activeOnly"],
            },
            True,
        ),
        (
            {
                "source_file": "14_branch_query.php",
                "source_line": 9,
                "operation_type": "REFERENCE_ASSIGN",
                "variables": ["$sql", "$otherSql"],
            },
            True,
        ),
        (
            {
                "source_file": "14_branch_query.php",
                "source_line": 9,
                "operation_type": "UNSET",
                "variables": [],
            },
            True,
        ),
        (
            {
                "source_file": "14_branch_query.php",
                "source_line": 12,
                "operation_type": "UNSET",
                "variables": ["$sql"],
            },
            False,
        ),
        (
            {
                "source_file": "unrelated.php",
                "source_line": 7,
                "operation_type": "UNSET",
                "variables": ["$sql"],
            },
            False,
        ),
        (
            {
                "source_file": "14_branch_query.php",
                "source_line": 7,
                "operation_type": "UNSET",
                "variables": ["$unrelated"],
            },
            False,
        ),
    ],
)
def test_unsupported_operation_relevance(
    candidate,
    query,
    operation,
    expected,
):
    result = has_relevant_unsupported_operations(
        candidate,
        query,
        [operation],
    )

    assert result is expected


def test_no_unsupported_operations(candidate, query):
    assert has_relevant_unsupported_operations(
        candidate,
        query,
        [],
    ) is False
