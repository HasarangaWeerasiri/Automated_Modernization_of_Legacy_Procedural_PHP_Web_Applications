import copy

import pytest

from c3.mapping.branch_recovery import (
    recover_simple_if_else_candidate,
)


@pytest.fixture
def q014_data():
    source = "14_branch_query.php"

    query = {
        "query_id": "Q014",
        "source_file": source,
        "source_line": 10,
        "sql_argument": "$sql",
        "status": "UNRESOLVED",
        "reason_code": "BRANCHING",
    }

    assignments = [
        {
            "source_file": source,
            "source_line": 4,
            "variable": "$activeOnly",
            "assignment_type": "ASSIGN",
            "expression": "isset($_GET['active'])",
            "control_flow": [],
        },
        {
            "source_file": source,
            "source_line": 6,
            "variable": "$sql",
            "assignment_type": "ASSIGN",
            "control_flow": ["IF"],
            "expression": '"SELECT id, full_name FROM doctors WHERE active = 1"',
            "branch_conditions": [
                {
                    "branch": "IF",
                    "condition": "$activeOnly",
                    "branch_group_line": 5,
                }
            ],
        },
        {
            "source_file": source,
            "source_line": 8,
            "variable": "$sql",
            "assignment_type": "ASSIGN",
            "control_flow": ["ELSE", "IF"],
            "expression": '"SELECT id, full_name FROM doctors"',
            "branch_conditions": [
                {
                    "branch": "ELSE",
                    "condition": "$activeOnly",
                    "branch_group_line": 5,
                }
            ],
        },
    ]

    return query, assignments


def test_valid_q014_structural_candidate(q014_data):
    query, assignments = q014_data

    result = recover_simple_if_else_candidate(query, assignments)

    assert result is not None
    assert result["validation_status"] == "STRUCTURAL_CANDIDATE"
    assert result["safe_for_conversion"] is False
    assert result["branch_group_line"] == 5
    assert set(result["branches"]) == {"IF", "ELSE"}


@pytest.mark.parametrize(
    "mutation",
    [
        "different_group",
        "different_condition",
        "missing_else",
        "duplicate_if",
        "nested_branch",
        "dynamic_sql",
        "sql_concatenation",
        "unsafe_sql",
        "extra_sql_assignment",
        "late_assignment",
        "unsupported_condition",
        "condition_reassignment",
        "condition_post_increment",
        "condition_pre_decrement",
        "sql_post_increment",
        "sql_add_assignment",
    ],
)
def test_unsafe_branch_candidate_rejected(q014_data, mutation):
    query, assignments = copy.deepcopy(q014_data)

    if mutation == "different_group":
        assignments[2]["branch_conditions"][0]["branch_group_line"] = 7

    elif mutation == "different_condition":
        assignments[2]["branch_conditions"][0]["condition"] = "$other"

    elif mutation == "missing_else":
        assignments.pop(2)

    elif mutation == "duplicate_if":
        assignments[2]["branch_conditions"][0]["branch"] = "IF"

    elif mutation == "nested_branch":
        assignments[1]["branch_conditions"].append({
            "branch": "IF",
            "condition": "$other",
            "branch_group_line": 3,
        })

    elif mutation == "dynamic_sql":
        assignments[1]["expression"] = '"SELECT * FROM doctors " . $filter'

    elif mutation == "sql_concatenation":
        assignments[1]["assignment_type"] = "CONCAT_ASSIGN"

    elif mutation == "unsafe_sql":
        assignments[1]["expression"] = '"DELETE FROM doctors"'

    elif mutation == "extra_sql_assignment":
        assignments.append(copy.deepcopy(assignments[1]))

    elif mutation == "late_assignment":
        assignments[1]["source_line"] = 11

    elif mutation == "unsupported_condition":
        assignments[0]["expression"] = "$request->get('active')"

    elif mutation == "condition_reassignment":
        assignments.append({
            "source_file": query["source_file"],
            "source_line": 5,
            "variable": "$activeOnly",
            "assignment_type": "ASSIGN",
            "expression": "false",
            "control_flow": [],
        })

    elif mutation == "condition_post_increment":
        assignments.append({
            "source_file": query["source_file"],
            "source_line": 5,
            "variable": "$activeOnly",
            "assignment_type": "UNSUPPORTED_MUTATION",
            "expression": None,
            "control_flow": [],
        })

    elif mutation == "condition_pre_decrement":
        assignments.append({
            "source_file": query["source_file"],
            "source_line": 5,
            "variable": "$activeOnly",
            "assignment_type": "UNSUPPORTED_MUTATION",
            "expression": None,
            "control_flow": [],
        })

    elif mutation == "sql_post_increment":
        assignments.append({
            "source_file": query["source_file"],
            "source_line": 9,
            "variable": "$sql",
            "assignment_type": "UNSUPPORTED_MUTATION",
            "expression": None,
            "control_flow": [],
        })

    elif mutation == "sql_add_assignment":
        assignments.append({
            "source_file": query["source_file"],
            "source_line": 9,
            "variable": "$sql",
            "assignment_type": "UNSUPPORTED_MUTATION",
            "expression": "1",
            "control_flow": [],
        })

    assert recover_simple_if_else_candidate(query, assignments) is None
