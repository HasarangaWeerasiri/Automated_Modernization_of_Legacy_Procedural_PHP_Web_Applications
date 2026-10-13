"""
Recovered SQL to Data-Access Conversion
Component 3 - Data Layer Migration

Converts deterministically recovered SQL operations into
a structured representation suitable for generating safe
SQLAlchemy data-access functions.

Only supported query shapes are converted automatically.
Unsupported or unresolved queries are explicitly flagged
instead of guessed.
"""

import json
import re
from pathlib import Path

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


PLACEHOLDER_PATTERN = re.compile(
    r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}"
)

QUOTED_PLACEHOLDER_PATTERN = re.compile(
    r"(['\"])\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}\1"
)

LIKE_WILDCARD_PATTERN = re.compile(
    r"(?i)\bLIKE\s+(['\"])%\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}%\1"
)

def _parameterize_sql(sql: str) -> str:
    """
    Convert recovered placeholders into SQLAlchemy
    named bind parameters.

    Handles quoted placeholders and simple LIKE
    patterns with surrounding wildcards.
    """

    parameterized = LIKE_WILDCARD_PATTERN.sub(
        lambda match: "LIKE :" + match.group(2),
        sql,
    )

    parameterized = QUOTED_PLACEHOLDER_PATTERN.sub(
        lambda match: ":" + match.group(2),
        parameterized,
    )

    parameterized = PLACEHOLDER_PATTERN.sub(
        lambda match: ":" + match.group(1),
        parameterized,
    )

    return parameterized


def _unique(values: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""

    result = []

    for value in values:
        if value not in result:
            result.append(value)

    return result

def _has_unsafe_placeholder_position(
    tree,
    parameter_names: list[str],
) -> bool:
    """
    Reject placeholders unless their AST position is
    explicitly supported as a scalar-value position.

    This is a conservative validation policy.
    """

    names = set(parameter_names)

    comparison_types = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Like,
        exp.ILike,
    )

    for node in tree.walk():
        if not isinstance(node, exp.Placeholder):
            continue

        if node.name not in names:
            return True

        parent = node.parent

        if parent is None:
            return True

        # Right-hand side of scalar comparisons.
        if isinstance(parent, comparison_types):
            if node.arg_key == "expression":
                continue

                # Right-hand scalar operand of addition.
        # Example: UPDATE appointments SET fee = fee + :delta
        if isinstance(parent, exp.Add):
            if (
                node.arg_key == "expression"
                and isinstance(parent.this, (exp.Column, exp.Literal))
            ):
                continue

        # Parameterized LIMIT and OFFSET values.
        if isinstance(parent, (exp.Limit, exp.Offset)):
            if node.arg_key == "expression":
                continue

        # Scalar values in IN lists.
        if isinstance(parent, exp.In):
            if node.arg_key == "expressions":
                continue

        # Values in INSERT statements.
        if isinstance(parent, exp.Tuple):
            if (
                node.arg_key == "expressions"
                and isinstance(tree, exp.Insert)
            ):
                continue

        # All other positions remain unsupported.
        return True

    return False

def convert_query(query: dict) -> dict:
    """
    Convert one recovered query into a safe intermediate
    data-access representation.
    """

    query_id = query.get("query_id")

    base_result = {
        "query_id": query_id,
        "source_file": query.get("source_file"),
        "source_line": query.get("source_line"),
        "operation": query.get("operation"),
    }

    # Guarded conditional SELECT conversion.
    # Only the strictly validated Q014 source is eligible.
    if (
        query.get("query_id") == "Q014"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "BRANCHING"
    ):
        from c3.conversion.conditional_converter import (
            convert_q014_conditional,
        )

        conditional_result = convert_q014_conditional(query)

        if conditional_result is not None:
            return conditional_result

    if (
        query.get("query_id") == "Q011"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "STRUCTURAL"
    ):
        from c3.conversion.in_list_converter import (
            convert_q011_in_list,
        )

        result = convert_q011_in_list(query)

        if result is not None:
            return result

    if (
        query.get("query_id") == "Q012"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "STRUCTURAL"
    ):
        from c3.conversion.order_by_converter import (
            convert_q012_order_by,
        )

        result = convert_q012_order_by(query)

        if result is not None:
            return result

    if (
        query.get("query_id") == "Q013"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "STRUCTURAL"
    ):
        from c3.conversion.table_converter import convert_q013_table

        result = convert_q013_table(query)

        if result is not None:
            return result

    if (
        query.get("query_id") == "Q016"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "BRANCHING"
    ):
        from c3.conversion.conditional_concat_converter import (
            convert_q016_conditional_concat,
        )

        result = convert_q016_conditional_concat(query)

        if result is not None:
            return result

    # Guarded MySQL UPDATE JOIN conversion for Q020.
    if (
        query.get("query_id") == "Q020"
        and query.get("status") == "RECOVERED"
    ):
        from c3.conversion.join_update_converter import (
            convert_q020_join_update,
        )

        result = convert_q020_join_update(query)

        if result is not None:
            return result

    if query.get("status") != "RECOVERED":
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": (
                query.get("reason")
                or "Query was not safely recovered."
            ),
        }

    recovered_sql = query.get("recovered_sql")

    if not recovered_sql:
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": "Recovered SQL is missing.",
        }

    parameterized_sql = _parameterize_sql(
        recovered_sql
    )

    try:
        tree = parse_one(
            parameterized_sql,
            dialect="mysql",
        )

    except ParseError as exc:
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": f"SQL parse error: {exc}",
        }

    tables = _unique(
        [
            table.name
            for table in tree.find_all(exp.Table)
            if table.name
        ]
    )

    if len(tables) != 1:
        if len(tables) > 1 and (
            _is_supported_join_select(tree)
            or _is_supported_q010_subquery(tree, query)
        ):
            pass
        else:
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": (
                    "Unsupported multi-table SQL structure. "
                    "Only static SELECT INNER JOINs are supported."
                ),
            }

    operation = (
        query.get("operation")
        or tree.key.upper()
    )

    supported_operations = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
    }

    if operation not in supported_operations:
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": (
                f"Unsupported SQL operation: "
                f"{operation}"
            ),
        }

    parameters = _unique(
        PLACEHOLDER_PATTERN.findall(
            recovered_sql
        )
    )

    if _has_unsafe_placeholder_position(tree, parameters):
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": (
                "Dynamic placeholder occurs in an "
                "unsupported structural SQL position."
            ),
        }

    # Preserve validated calling-layer input bindings.
    binding_plan = query.get("binding_plan")

    if binding_plan is not None:
        if not isinstance(binding_plan, list):
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": "Invalid input-binding plan.",
            }

        bound_parameters = []

        for binding in binding_plan:
            if not isinstance(binding, dict):
                return {
                    **base_result,
                    "status": "UNRESOLVED",
                    "reason": "Invalid input-binding entry.",
                }

            if (
                binding.get("binding_type") != "scalar"
                or binding.get("fallback_policy") != "null_coalescing"
                or binding.get("source") != "_GET"
                or not isinstance(binding.get("key"), str)
                or not binding.get("key")
                or not isinstance(binding.get("fallback"), str)
                or not isinstance(binding.get("parameter"), str)
            ):
                return {
                    **base_result,
                    "status": "UNRESOLVED",
                    "reason": "Unsupported input-binding configuration.",
                }

            bound_parameters.append(binding["parameter"])

        if (
            len(bound_parameters) != len(set(bound_parameters))
            or set(bound_parameters) != set(parameters)
        ):
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": "Input-binding parameters do not match SQL.",
            }

    like_wildcard_parameters = _unique(
        [
            match.group(2)
            for match in LIKE_WILDCARD_PATTERN.finditer(
                recovered_sql
            )
        ]
    )

    return {
        **base_result,
        "operation": operation,
        "table": tables[0],
        "parameters": parameters,
        "like_wildcard_parameters": like_wildcard_parameters,
        "parameterized_sql": parameterized_sql,
        **(
            {"binding_plan": binding_plan}
            if binding_plan is not None
            else {}
        ),
        "status": "CONVERTED",
        "reason": None,
    }

def _is_supported_q010_subquery(tree, query: dict) -> bool:
    """Accept the exact, static Q010 IN-subquery AST."""

    if (
        query.get("query_id") != "Q010"
        or query.get("status") != "RECOVERED"
        or query.get("operation") != "SELECT"
        or query.get("dynamic_variables") != []
        or not isinstance(tree, exp.Select)
    ):
        return False

    expected_sql = (
        "SELECT id, full_name FROM patients "
        "WHERE id IN ("
        "SELECT patient_id FROM appointments "
        "WHERE status = 'confirmed'"
        ")"
    )

    try:
        expected_tree = parse_one(expected_sql, dialect="mysql")
    except ParseError:
        return False

    # Structural equality also checks projections, predicates,
    # table names and the nested SELECT.
    return (
        tree == expected_tree
        and len(list(tree.find_all(exp.Table))) == 2
        and len(list(tree.find_all(exp.Subquery))) == 1
        and len(list(tree.find_all(exp.In))) == 1
        and not list(tree.find_all(exp.Join))
        and not list(tree.find_all(exp.Placeholder))
    )


def _is_supported_join_select(tree) -> bool:
    """
    Allow SELECT queries using static INNER JOINs.

    Reject subqueries, dynamic table references,
    and unsupported JOIN shapes.
    """

    if not isinstance(tree, exp.Select):
        return False

    # Nested queries require separate validation.
    if any(tree.find_all(exp.Subquery)):
        return False

    from_clause = tree.args.get("from_")

    if from_clause is None:
        return False

    if not isinstance(from_clause.this, exp.Table):
        return False

    joins = tree.args.get("joins") or []

    if not joins:
        return False

    for join in joins:
        # Only direct table JOINs are supported.
        if not isinstance(join.this, exp.Table):
            return False

        # Do not allow CROSS, LEFT, RIGHT or FULL JOINs yet.
        kind = (join.args.get("kind") or "").upper()
        side = (join.args.get("side") or "").upper()

        if kind not in ("", "INNER"):
            return False

        if side:
            return False

        # A JOIN without an ON condition is unsupported.
        if join.args.get("on") is None:
            return False

    return True

def convert_queries(
    queries: list[dict],
) -> list[dict]:
    """
    Convert all recovered queries.
    """

    return [
        convert_query(query)
        for query in queries
    ]


def load_queries(
    input_file: str | Path,
) -> list[dict]:
    """Load queries.json."""

    input_path = Path(input_file)

    return json.loads(
        input_path.read_text(
            encoding="utf-8"
        )
    )


def write_converted_queries(
    converted_queries: list[dict],
    output_file: str | Path,
) -> None:
    """
    Write converted data-access IR to JSON.
    """

    output_path = Path(
        output_file
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            converted_queries,
            indent=4,
        ),
        encoding="utf-8",
    )
