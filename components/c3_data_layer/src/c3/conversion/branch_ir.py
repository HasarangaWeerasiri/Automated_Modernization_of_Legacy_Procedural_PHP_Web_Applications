"""Validation of diagnostic conditional SQL query representations."""

from sqlglot import exp, parse
from sqlglot.errors import ParseError


def validate_conditional_select_ir(ir: dict) -> bool:
    """Validate the supported structure of a conditional SELECT IR."""

    if not isinstance(ir, dict):
        return False

    if ir.get("query_shape") != "CONDITIONAL_SELECT":
        return False

    if ir.get("operation") != "SELECT":
        return False

    if ir.get("status") != "DIAGNOSTIC_ONLY":
        return False

    if ir.get("safe_for_conversion") is not False:
        return False

    if ir.get("unsupported_operations_checked") is not True:
        return False

    condition = ir.get("condition")

    if not isinstance(condition, dict):
        return False

    if (
        condition.get("kind") != "ISSET"
        or condition.get("input_source") != "_GET"
        or condition.get("true_when") != "present_and_not_null"
        or not isinstance(condition.get("input_key"), str)
        or not condition.get("input_key")
    ):
        return False

    branches = ir.get("branches")

    if not isinstance(branches, dict):
        return False

    if set(branches) != {"IF", "ELSE"}:
        return False

    for sql in branches.values():
        if not isinstance(sql, str) or not sql.strip():
            return False

        try:
            statements = parse(sql, read="mysql")
        except (ParseError, ValueError):
            return False

        if len(statements) != 1:
            return False

        statement = statements[0]

        if not isinstance(statement, exp.Select):
            return False

        if len(list(statement.find_all(exp.Table))) != 1:
            return False

        if statement.args.get("joins"):
            return False

        if statement.args.get("into") is not None:
            return False

        if statement.args.get("locks"):
            return False

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

        if any(
            isinstance(node, forbidden)
            for node in statement.walk()
        ):
            return False

    return True
