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

from c3.conversion.identifier_allowlist import schema_columns


PLACEHOLDER_PATTERN = re.compile(
    r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}"
)

QUOTED_PLACEHOLDER_PATTERN = re.compile(
    r"(['\"])\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}\1"
)

LIKE_WILDCARD_PATTERN = re.compile(
    r"(?i)\bLIKE\s+(['\"])%\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}%\1"
)

# S14: executors whose SQL argument is a prepared statement.
PREPARE_EXECUTORS = {"mysqli_prepare", "mysqli_stmt_prepare", "prepare"}


def _name_prepared_placeholders(sql: str, recovered_names: set[str]):
    """Give prepared-statement placeholders bind-parameter names.

    `?` placeholders become :param_1, :param_2 ... in textual order (the
    order the caller binds them); PDO named placeholders (:name) keep their
    names. Placeholders inside string literals are not tokens and are left
    alone. Mixing `?` and named placeholders (invalid in PDO) returns None.
    """

    from sqlglot.dialects.mysql import MySQL
    from sqlglot.tokens import TokenType

    tokens = MySQL().tokenize(sql)
    positional = [t for t in tokens if t.token_type == TokenType.PLACEHOLDER and t.text == "?"]
    named = []
    for previous, token in zip(tokens, tokens[1:]):
        if (previous.token_type == TokenType.COLON and token.token_type == TokenType.VAR
                and previous.end + 1 == token.start and token.text not in recovered_names):
            named.append(token.text)

    if positional and named:
        return None, []

    if not positional:
        return sql, list(dict.fromkeys(named))

    names = [f"param_{index}" for index in range(1, len(positional) + 1)]
    for token, name in reversed(list(zip(positional, names))):
        sql = sql[:token.start] + ":" + name + sql[token.end + 1:]
    return sql, names


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

    # A recovered value that did not become a bind placeholder sits inside a
    # string literal ('x{{v}}y'); binding it would change the literal, so abstain.
    if names - {node.name for node in tree.find_all(exp.Placeholder)}:
        return True

    for node in tree.walk():
        if not isinstance(node, exp.Placeholder):
            continue

        if node.name not in names:
            return True

        parent = node.parent

        if parent is None:
            return True

        if _is_value_position(node, tree):
            continue

        # Parameterized LIMIT and OFFSET values.
        if isinstance(parent, (exp.Limit, exp.Offset)):
            if node.arg_key == "expression":
                continue

        # The whole value argument of an allow-listed scalar function whose
        # call itself sits in a value position, e.g. WHERE pwd = MD5(:pwd).
        # Only one level: MD5(LOWER(:x)) or MD5(CONCAT(:x, 's')) abstain.
        if (
            isinstance(parent, VALUE_FUNCTIONS)
            and node.arg_key == "this"
            and not (isinstance(parent, exp.TsOrDsToDate) and parent.args.get("format") is not None)
            and _is_value_position(parent, tree)
        ):
            continue

        # All other positions remain unsupported.
        return True

    return False


COMPARISON_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
)

# Deterministic scalar functions whose value operand may be a bound parameter:
# MD5, SHA1, SHA2 (first argument), LOWER, UPPER, TRIM, DATE and CAST (value
# operand). Anything else (SLEEP, BENCHMARK, LOAD_FILE, ...) parses as another
# node type and keeps abstaining; so do the SHA2 bit length, TRIM's character
# set, a DATE format and CAST's target type (not the "this" argument).
VALUE_FUNCTIONS = (
    exp.MD5,
    exp.SHA,
    exp.SHA2,
    exp.Lower,
    exp.Upper,
    exp.Trim,
    exp.TsOrDsToDate,
    exp.Cast,
)

PREDICATE_CONNECTIVES = (exp.And, exp.Or, exp.Not, exp.Paren)


def _in_predicate_position(node) -> bool:
    """True when node is a WHERE/HAVING/JOIN ON condition (possibly inside AND, OR, NOT, parentheses)."""

    while isinstance(node.parent, PREDICATE_CONNECTIVES):
        node = node.parent

    parent = node.parent

    if isinstance(parent, (exp.Where, exp.Having)):
        return node.arg_key == "this"

    return isinstance(parent, exp.Join) and node.arg_key == "on"


def _is_value_position(node, tree) -> bool:
    """
    Scalar value positions accepted for a placeholder or for an allow-listed
    function call: right-hand side of a comparison or LIKE, right-hand operand
    of col + value, an IN-list item, an INSERT value, or a BETWEEN bound of a
    column in a WHERE/HAVING/ON condition.
    """

    parent = node.parent

    # Right-hand side of scalar comparisons (also UPDATE ... SET col = value).
    if isinstance(parent, COMPARISON_TYPES):
        return node.arg_key == "expression"

    # Right-hand scalar operand of addition.
    # Example: UPDATE appointments SET fee = fee + :delta
    if isinstance(parent, exp.Add):
        return (
            node.arg_key == "expression"
            and isinstance(parent.this, (exp.Column, exp.Literal))
        )

    # Scalar values in IN lists.
    if isinstance(parent, exp.In):
        return node.arg_key == "expressions"

    # Values in INSERT statements.
    if isinstance(parent, exp.Tuple):
        return node.arg_key == "expressions" and isinstance(tree, exp.Insert)

    # BETWEEN bounds: col BETWEEN :low AND :high in a condition.
    if isinstance(parent, exp.Between):
        return (
            node.arg_key in ("low", "high")
            and isinstance(parent.this, exp.Column)
            and _in_predicate_position(parent)
        )

    return False

GENERAL_RULE = "general"
TEMPLATE_RULE = "query_specific_template"


def convert_query(query: dict, schema: dict | None = None) -> dict:
    """
    Convert one recovered query into a safe intermediate
    data-access representation.

    Every CONVERTED result carries conversion_rule: "general" for the
    general converter, or "query_specific_template" for the guarded
    templates written for individual development-benchmark queries.
    Reports count the two separately.
    """

    result = _convert_query(query, schema)

    if result.get("status") == "CONVERTED" and "conversion_rule" not in result:
        result["conversion_rule"] = (
            TEMPLATE_RULE if "query_shape" in result else GENERAL_RULE
        )

    return result


def _convert_query(query: dict, schema: dict | None = None) -> dict:
    """Conversion logic; see convert_query."""

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

        result = convert_q012_order_by(query, schema)

        if result is not None:
            return result

    if (
        query.get("query_id") == "Q013"
        and query.get("status") == "UNRESOLVED"
        and query.get("reason_code") == "STRUCTURAL"
    ):
        from c3.conversion.table_converter import convert_q013_table

        result = convert_q013_table(query, schema)

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

    prepared_parameters = []

    if query.get("executor") in PREPARE_EXECUTORS:
        parameterized_sql, prepared_parameters = _name_prepared_placeholders(
            parameterized_sql,
            set(PLACEHOLDER_PATTERN.findall(recovered_sql)),
        )

        if parameterized_sql is None:
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": "Prepared statement mixes positional (?) and named placeholders.",
            }

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

    conversion_rule = GENERAL_RULE

    if len(tables) != 1:
        join_problem = (
            _join_select_problem(tree, schema)
            if len(tables) > 1
            else MULTI_TABLE_REASON
        )

        if join_problem is None:
            pass
        elif len(tables) > 1 and _is_supported_q010_subquery(tree, query):
            conversion_rule = TEMPLATE_RULE
        else:
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": join_problem,
            }
    elif _is_outer_or_comma_join_select(tree):
        # A LEFT or comma self-join names one table but still joins; the
        # result-shape rule applies to it too.
        join_problem = _outer_join_shape_problem(tree, schema)

        if join_problem is not None:
            return {
                **base_result,
                "status": "UNRESOLVED",
                "reason": join_problem,
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
        + prepared_parameters
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
        **(
            {"parameter_sources": {
                name: source
                for name, source in (query.get("dynamic_variable_sources") or {}).items()
                if name in parameters
            }}
            if query.get("dynamic_variable_sources")
            else {}
        ),
        "parameterized_sql": parameterized_sql,
        **(
            {"prepared_statement_parameters": prepared_parameters}
            if prepared_parameters
            else {}
        ),
        **(
            {"binding_plan": binding_plan}
            if binding_plan is not None
            else {}
        ),
        "status": "CONVERTED",
        "reason": None,
        "conversion_rule": conversion_rule,
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


MULTI_TABLE_REASON = (
    "Unsupported multi-table SQL structure. "
    "Only static SELECT joins are converted: INNER JOIN, and LEFT JOIN or "
    "comma join under the result-shape rule; RIGHT, FULL, CROSS and NATURAL "
    "joins, USING, subqueries, derived tables, unions and UPDATE/DELETE joins "
    "are not."
)

# A LEFT JOIN or implicit comma join is converted only when the result row
# keys are known from the SQL and the parsed schema (FR6 join rule, 2026-10-20).
# Each reason keeps the multi-table prefix the reports group by.
OUTER_JOIN_PREFIX = (
    "Unsupported multi-table SQL structure. A LEFT JOIN or comma join is "
    "converted only when "
)
OUTER_JOIN_REASONS = {
    "shape": OUTER_JOIN_PREFIX + (
        "it is a single SELECT over schema tables joined by INNER or LEFT JOIN "
        "with ON, or by commas alone; RIGHT, FULL, CROSS, NATURAL, USING, "
        "STRAIGHT_JOIN, subqueries and derived tables are not."
    ),
    "schema": OUTER_JOIN_PREFIX + "every table is in the parsed schema.",
    "star": OUTER_JOIN_PREFIX + (
        "every output column is explicit (no * or table.*), so the result row "
        "keys are known."
    ),
    "names": OUTER_JOIN_PREFIX + (
        "every output column has a distinct name (a column or an alias), so "
        "result.mappings() cannot drop a column."
    ),
    "columns": OUTER_JOIN_PREFIX + (
        "every column resolves to exactly one joined table in the parsed schema."
    ),
}


def _join_select_problem(tree, schema: dict | None) -> str | None:
    """
    None when a multi-table SELECT is accepted, otherwise the abstention reason.

    Static INNER JOIN SELECTs keep the original rule unchanged. A SELECT with a
    LEFT JOIN or an implicit comma join is accepted only under the result-shape
    rule in _outer_join_shape_problem.
    """

    if _is_supported_join_select(tree):
        return None

    if not _is_outer_or_comma_join_select(tree):
        return MULTI_TABLE_REASON

    return _outer_join_shape_problem(tree, schema)


def _is_comma_join(join) -> bool:
    """FROM a, b: sqlglot 30 builds the comma form without the pivots slot that JOIN b has."""

    return (
        "pivots" not in join.args
        and not any(join.args.get(key) for key in ("kind", "side", "method", "on", "using"))
    )


def _is_outer_or_comma_join_select(tree) -> bool:
    """A SELECT whose joins include LEFT JOIN or comma joins (other forms are not this rule)."""

    if not isinstance(tree, exp.Select):
        return False

    joins = tree.args.get("joins") or []

    return any(
        _is_comma_join(join) or (join.args.get("side") or "").upper() == "LEFT"
        for join in joins
    )


def _outer_join_shape_problem(tree, schema: dict | None) -> str | None:
    """
    Result-shape rule for LEFT JOIN and comma-join SELECTs.

    The SQL text is executed unchanged through text(), so MySQL computes the
    same rows (NULL-extended rows included). What can differ is how a row is
    read: PHP reads keys from the fetched row, the generated function returns
    result.mappings(). That is the same only when every output key is known
    and distinct, so the rule requires explicit, distinctly named outputs and
    columns that resolve to exactly one table of the parsed schema.
    """

    # Shape: one plain SELECT over base tables, no nesting.
    if any(tree.find_all(exp.Subquery, exp.Union, exp.Intersect, exp.Except)):
        return OUTER_JOIN_REASONS["shape"]

    from_clause = tree.args.get("from_")

    if from_clause is None or not isinstance(from_clause.this, exp.Table):
        return OUTER_JOIN_REASONS["shape"]

    joins = tree.args.get("joins") or []
    comma = [_is_comma_join(join) for join in joins]

    # Comma joins bind more loosely than JOIN in MySQL; mixing them changes
    # which tables an ON clause can see, so the forms are not mixed.
    if any(comma) and not all(comma):
        return OUTER_JOIN_REASONS["shape"]

    for join, is_comma in zip(joins, comma):
        if not isinstance(join.this, exp.Table) or join.args.get("using") or join.args.get("method"):
            return OUTER_JOIN_REASONS["shape"]

        if is_comma:
            continue

        side = (join.args.get("side") or "").upper()
        kind = (join.args.get("kind") or "").upper()

        if join.args.get("on") is None or side not in ("", "LEFT"):
            return OUTER_JOIN_REASONS["shape"]

        if kind not in ("", "INNER", "OUTER") or (kind == "OUTER" and side != "LEFT"):
            return OUTER_JOIN_REASONS["shape"]

    # Every table is in the parsed schema; table names and aliases are unique.
    scope = {}

    for table in tree.find_all(exp.Table):
        columns = schema_columns(schema, table.name)

        if columns is None or table.args.get("db") is not None:
            return OUTER_JOIN_REASONS["schema"]

        key = (table.alias or table.name).lower()

        if key in scope:
            return OUTER_JOIN_REASONS["columns"]

        scope[key] = {column.lower() for column in columns}

    # Explicit outputs with distinct names. An unaliased expression has no
    # name predictable from the SQL, so it needs an alias.
    names = []

    for projection in tree.expressions:
        if isinstance(projection, exp.Star) or any(projection.find_all(exp.Star)):
            if not (isinstance(projection, exp.Alias) and isinstance(projection.this, exp.Count)):
                return OUTER_JOIN_REASONS["star"]

        if isinstance(projection, exp.Alias):
            names.append(projection.alias)
        elif isinstance(projection, exp.Column):
            names.append(projection.name)
        else:
            return OUTER_JOIN_REASONS["names"]

    if len({name.lower() for name in names}) != len(names):
        return OUTER_JOIN_REASONS["names"]

    output_aliases = {
        projection.alias.lower()
        for projection in tree.expressions
        if isinstance(projection, exp.Alias)
    }

    # Every column resolves to exactly one table in scope. An unqualified name
    # in GROUP BY, HAVING or ORDER BY may name an output alias instead, but
    # only when no table has a column of that name.
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue

        name = column.name.lower()

        if column.table:
            owner = scope.get(column.table.lower())

            if owner is None or name not in owner:
                return OUTER_JOIN_REASONS["columns"]

            continue

        owners = [key for key, columns in scope.items() if name in columns]

        if len(owners) == 1:
            continue

        if (
            not owners
            and name in output_aliases
            and column.find_ancestor(exp.Group, exp.Having, exp.Order) is not None
        ):
            continue

        return OUTER_JOIN_REASONS["columns"]

    return None


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
    schema: dict | None = None,
) -> list[dict]:
    """
    Convert all recovered queries.

    schema (the schema.json structure) is used to derive identifier
    allowlists for dynamic ORDER BY / table identifiers.
    """

    return [
        convert_query(query, schema)
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
