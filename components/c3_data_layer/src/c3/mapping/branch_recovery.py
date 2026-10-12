"""Conservative recovery of simple PHP IF/ELSE SQL assignments."""

import ast
import re
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from c3.mapping.sql_recovery import _resolve_isset_branch_condition


def _static_php_sql(expression):
    """Accept a literal containing one supported static SELECT."""

    if not isinstance(expression, str):
        return None

    expression = expression.strip()

    if (
        len(expression) < 2
        or expression[0] != expression[-1]
        or expression[0] not in ("'", '"')
    ):
        return None

    # Deliberately reject interpolated or escaped PHP strings.
    # This restricted decoder only supports simple literal SQL.
    body = expression[1:-1]

    if "\\" in body or "$" in body:
        return None

    if not body or any(token in body for token in (";", "--", "/*", "#")):
        return None

    try:
        statements = parse(body, read="mysql")
    except (ParseError, ValueError):
        return None

    if len(statements) != 1:
        return None

    tree = statements[0]

    if not isinstance(tree, exp.Select):
        return None

    # Reject nested queries and data-changing constructs.
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Create,
        exp.Alter,
        exp.Command,
        exp.Subquery,
        exp.Union,
    )

    if any(isinstance(node, forbidden) for node in tree.walk()):
        return None

    tables = list(tree.find_all(exp.Table))

    if len(tables) != 1:
        return None

    if tree.args.get("joins"):
        return None

    if tree.args.get("into") is not None:
        return None

    if tree.args.get("locks"):
        return None

    return body.strip()


def recover_simple_if_else_candidate(
    query,
    assignments,
    unsupported_operations=None,
):
    """Create a diagnostic candidate for a simple two-branch SQL query."""

    if not isinstance(query, dict) or not isinstance(assignments, list):
        return None

    if (
        query.get("status") != "UNRESOLVED"
        or query.get("reason_code") != "BRANCHING"
    ):
        return None

    source_file = query.get("source_file")
    sql_argument = query.get("sql_argument")
    call_line = query.get("source_line")

    if (
        not isinstance(source_file, str)
        or not isinstance(sql_argument, str)
        or not isinstance(call_line, int)
    ):
        return None

    sql_assignments = [
        a for a in assignments
        if isinstance(a, dict)
        and a.get("source_file") == source_file
        and a.get("variable") == sql_argument
    ]

    if len(sql_assignments) != 2:
        return None

    branches = {}

    for assignment in sql_assignments:
        if assignment.get("assignment_type") != "ASSIGN":
            return None

        line = assignment.get("source_line")

        if not isinstance(line, int) or line >= call_line:
            return None

        expected_flow = {
            "IF": ["IF"],
            "ELSE": ["ELSE", "IF"],
        }

        contexts = assignment.get("branch_conditions")

        if not isinstance(contexts, list) or len(contexts) != 1:
            return None

        context = contexts[0]

        if not isinstance(context, dict):
            return None

        branch = context.get("branch")
        if assignment.get("control_flow") != expected_flow.get(branch):
            return None

        if branch not in ("IF", "ELSE") or branch in branches:
            return None

        sql = _static_php_sql(assignment.get("expression"))

        if sql is None:
            return None

        branches[branch] = {
            "sql": sql,
            "line": line,
            "condition": context.get("condition"),
            "group_line": context.get("branch_group_line"),
        }

    if set(branches) != {"IF", "ELSE"}:
        return None

    if branches["IF"]["condition"] != branches["ELSE"]["condition"]:
        return None

    group_line = branches["IF"]["group_line"]

    if (
        not isinstance(group_line, int)
        or group_line != branches["ELSE"]["group_line"]
        or group_line >= call_line
    ):
        return None

    if not (
        group_line < branches["IF"]["line"] < call_line
        and group_line < branches["ELSE"]["line"] < call_line
    ):
        return None

    condition = _resolve_isset_branch_condition(
        branches["IF"]["condition"],
        source_file,
        group_line,
        assignments,
    )

    if condition is None:
        return None

    candidate = {
        "query_id": query["query_id"],
        "source_file": source_file,
        "branch_group_line": group_line,
        "condition": condition,
        "branches": {
            "IF": branches["IF"]["sql"],
            "ELSE": branches["ELSE"]["sql"],
        },
        "unsupported_operations_checked": False,
        "safe_for_conversion": False,
        "validation_status": "STRUCTURAL_CANDIDATE",
    }

    if unsupported_operations is None:
        return candidate

    if has_relevant_unsupported_operations(
        candidate,
        query,
        unsupported_operations,
    ):
        return None

    candidate["unsupported_operations_checked"] = True

    return candidate

def has_relevant_unsupported_operations(
    candidate: dict,
    query: dict,
    unsupported_operations: list[dict],
) -> bool:
    """Identify unsupported PHP operations affecting branch recovery."""

    if not isinstance(candidate, dict):
        return True

    if not isinstance(unsupported_operations, list):
        return True

    condition = candidate.get("condition")

    if not isinstance(condition, dict):
        return True

    condition_variable = condition.get("condition_variable")

    if not isinstance(condition_variable, str):
        return True

    protected_variables = {
        "$" + condition_variable,
        query.get("sql_argument"),
    }

    source_file = query.get("source_file")
    source_line = query.get("source_line")

    if not isinstance(source_file, str):
        return True

    if not isinstance(source_line, int):
        return True

    for operation in unsupported_operations:
        if not isinstance(operation, dict):
            return True

        if operation.get("source_file") != source_file:
            continue

        operation_line = operation.get("source_line")

        if not isinstance(operation_line, int):
            return True

        if operation_line > source_line:
            continue

        variables = operation.get("variables")

        if not isinstance(variables, list):
            return True

        # An unknown target is not evidence of safety.
        if not variables:
            return True

        if any(
            variable in protected_variables
            for variable in variables
        ):
            return True

    return False