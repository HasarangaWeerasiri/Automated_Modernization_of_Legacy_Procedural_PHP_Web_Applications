"""
SQL Recovery
Component 3 - Data Layer Migration

Reconstructs SQL queries from AST-derived PHP assignments.

Supported patterns in the current implementation:

    $sql = "SELECT ...";

    $sql = "UPDATE users SET name = '$name' WHERE id = $id";

    $sql = "DELETE FROM users ";
    $sql .= "WHERE id = " . $id;

Dynamic PHP values are represented using placeholders such as:

    {{name}}
    {{id}}

The recovery process is deterministic. If a query cannot be
safely reconstructed, it is marked UNRESOLVED rather than guessed.
"""

import json
import re
from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast


PHP_VARIABLE_PATTERN = re.compile(
    r"\{\$([A-Za-z_][A-Za-z0-9_]*)\}"
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"
)


def _variable_placeholder(match: re.Match) -> str:
    """
    Convert PHP variable syntax into a stable placeholder.

    Examples:

        $id       -> {{id}}
        {$name}   -> {{name}}
    """

    variable_name = (
        match.group(1)
        or match.group(2)
    )

    return "{{" + variable_name + "}}"


def _strip_php_string_quotes(value: str) -> str:
    """
    Remove surrounding PHP string quotes.

    This function does not remove quotes that are part of
    the SQL itself.
    """

    value = value.strip()

    if len(value) >= 2:
        if (
            value[0] == '"'
            and value[-1] == '"'
        ):
            return value[1:-1]

        if (
            value[0] == "'"
            and value[-1] == "'"
        ):
            return value[1:-1]

    return value


def _decode_php_string(value: str) -> str:
    """
    Convert escaped line endings and quotes from the
    pretty-printed PHP expression into readable text.
    """

    value = value.replace("\\r\\n", "\n")
    value = value.replace("\\n", "\n")
    value = value.replace("\\r", "\n")
    value = value.replace('\\"', '"')

    return value


def _split_concat_expression(
    expression: str,
) -> list[str]:
    """
    Split a PHP concatenation expression on '.' while
    respecting quoted strings.

    Example:

        "WHERE id = " . $id

    becomes:

        [
            '"WHERE id = "',
            '$id'
        ]
    """

    parts = []

    current = []
    quote = None
    escaped = False

    for character in expression:

        if escaped:
            current.append(character)
            escaped = False
            continue

        if character == "\\":
            current.append(character)
            escaped = True
            continue

        if quote is not None:
            current.append(character)

            if character == quote:
                quote = None

            continue

        if character in ('"', "'"):
            quote = character
            current.append(character)
            continue

        if character == ".":
            part = "".join(current).strip()

            if part:
                parts.append(part)

            current = []
            continue

        current.append(character)

    final_part = "".join(current).strip()

    if final_part:
        parts.append(final_part)

    return parts


def _expression_to_template(
    expression: str,
) -> tuple[str, list[str], bool]:
    """
    Convert a PHP expression into a recoverable SQL template.

    Returns:

        template
        dynamic_variables
        supported

    Example:

        "WHERE id = " . $id

    becomes:

        WHERE id = {{id}}
    """

    # Unsupported AST mutations may not have an expression.
    # Abstain from SQL recovery rather than raising TypeError.
    if not isinstance(expression, str):
        return "", [], False

    concat_parts = _split_concat_expression(
        expression
    )

    output_parts = []
    dynamic_variables = []
    supported = True

    for part in concat_parts:

        part = part.strip()

        if not part:
            continue

        # PHP quoted string.
        if (
            len(part) >= 2
            and part[0] in ('"', "'")
            and part[-1] == part[0]
        ):
            content = _strip_php_string_quotes(
                part
            )

            content = _decode_php_string(
                content
            )

            variables = (
                PHP_VARIABLE_PATTERN.findall(
                    content
                )
            )

            for variable_match in variables:
                variable_name = (
                    variable_match[0]
                    or variable_match[1]
                )

                if (
                    variable_name
                    not in dynamic_variables
                ):
                    dynamic_variables.append(
                        variable_name
                    )

            content = PHP_VARIABLE_PATTERN.sub(
                _variable_placeholder,
                content,
            )

            output_parts.append(content)

            continue

        # Direct variable concatenation:
        # "WHERE id = " . $id
        variable_match = re.fullmatch(
            r"\$([A-Za-z_][A-Za-z0-9_]*)",
            part,
        )

        if variable_match:
            variable_name = (
                variable_match.group(1)
            )

            if (
                variable_name
                not in dynamic_variables
            ):
                dynamic_variables.append(
                    variable_name
                )

            output_parts.append(
                "{{"
                + variable_name
                + "}}"
            )

            continue

        # Anything more complex is not guessed.
        supported = False
        output_parts.append(
            "{{UNRESOLVED:"
            + part
            + "}}"
        )

    return (
        "".join(output_parts),
        dynamic_variables,
        supported,
    )


def _normalize_sql(sql: str) -> str:
    """
    Normalize unnecessary whitespace while preserving
    the logical SQL structure.
    """

    return " ".join(
        sql.split()
    ).strip()


def _find_relevant_assignments(
    sql_call: dict,
    assignments: list[dict],
) -> list[dict]:
    """
    Find assignments that contribute to the SQL variable
    used at a particular execution point.

    Only assignments:
        - from the same source file,
        - to the SQL argument variable,
        - before the execution line

    are considered.
    """

    sql_argument = sql_call.get(
        "sql_argument"
    )

    source_file = sql_call.get(
        "source_file"
    )

    execution_line = sql_call.get(
        "source_line"
    )

    if not sql_argument:
        return []

    relevant = []

    for assignment in assignments:

        if (
            assignment.get("source_file")
            != source_file
        ):
            continue

        if (
            assignment.get("variable")
            != sql_argument
        ):
            continue

        assignment_line = assignment.get(
            "source_line"
        )

        if (
            assignment_line is None
            or execution_line is None
        ):
            continue

        if assignment_line >= execution_line:
            continue

        relevant.append(assignment)

    relevant.sort(
        key=lambda item: item["source_line"]
    )

    return relevant


def _select_assignment_chain(
    assignments: list[dict],
) -> list[dict]:
    """
    Select the assignment chain that actually builds the
    value used at the execution point.

    If a variable is assigned multiple times, only the most
    recent ASSIGN plus following CONCAT_ASSIGN operations
    are used.

    Example:

        $sql = "old query";
        $sql = "DELETE FROM users ";
        $sql .= "WHERE id = " . $id;

    recovers only:

        DELETE FROM users WHERE id = {{id}}
    """

    if not assignments:
        return []

    last_assign_index = None

    for index, assignment in enumerate(
        assignments
    ):
        if (
            assignment.get("assignment_type")
            == "ASSIGN"
        ):
            last_assign_index = index

    if last_assign_index is None:
        return []

    return assignments[
        last_assign_index:
    ]

def _collect_branch_sql_variants(
    relevant_assignments: list[dict],
) -> list[dict]:
    """
    Collect simple, literal SQL alternatives from conditional
    assignments for diagnostic purposes.

    Do not infer runtime conditions or mark queries recovered.
    """
    variants = []

    for assignment in relevant_assignments:
        control_flow = assignment.get("control_flow") or []

        if not control_flow:
            continue

        if assignment.get("assignment_type") != "ASSIGN":
            continue

        expression = (assignment.get("expression") or "").strip()

        # Accept only a complete, double-quoted static PHP string.
        # Dynamic expressions and concatenations remain unsupported.
        if not (
            len(expression) >= 2
            and expression.startswith('"')
            and expression.endswith('"')
            and "$" not in expression
        ):
            continue

        sql = expression[1:-1]

        if _detect_operation(sql) is None:
            continue

        variants.append({
            "source_line": assignment.get("source_line"),
            "control_flow": control_flow,
            "sql": sql,
        })

    return variants
def _collect_conditional_append_variants(
    relevant_assignments: list[dict],
) -> list[dict]:
    """
    Collect diagnostic SQL variants for a simple pattern:

        $sql = "base SQL";
        if (...) {
            $sql .= " fragment" . $dynamic_value;
        }

    Supports only one unconditional base assignment
    followed by one conditional CONCAT_ASSIGN.

    Does not generate executable SQL or infer conditions.
    """

    if len(relevant_assignments) != 2:
        return []

    base, append = relevant_assignments

    if (
        base.get("assignment_type") != "ASSIGN"
        or base.get("control_flow")
        or append.get("assignment_type") != "CONCAT_ASSIGN"
        or not append.get("control_flow")
        or base.get("source_file") != append.get("source_file")
    ):
        return []

    base_fragment, _, base_supported = _expression_to_template(
        base.get("expression", "")
    )

    append_fragment, _, append_supported = _expression_to_template(
        append.get("expression", "")
    )

    if not base_supported:
        return []

    base_sql = _normalize_sql(base_fragment)
    combined_sql = _normalize_sql(
        base_fragment + append_fragment
    )

    if _detect_operation(base_sql) is None:
        return []

    return [
        {
            "source_line": base.get("source_line"),
            "control_flow": [],
            "sql": base_sql,
            "fully_supported": True,
        },
        {
            "source_line": append.get("source_line"),
            "control_flow": append.get("control_flow"),
            "sql": combined_sql,
            "fully_supported": append_supported,
        },
    ]

def _detect_branching_assignment(
    assignment_chain: list[dict],
) -> tuple[str | None, str | None]:
    """
    Detect SQL construction that depends on control flow.

    If any assignment participating in the recovered SQL
    expression occurs inside a conditional branch, there may
    be multiple possible SQL shapes at runtime.

    The current deterministic recovery stage therefore
    abstains instead of choosing one branch.
    """

    for assignment in assignment_chain:
        control_flow = assignment.get(
            "control_flow",
            [],
        )

        if control_flow:
            return (
                "BRANCHING",
                "SQL construction depends on conditional "
                "control flow.",
            )

    return None, None

def _detect_structural_placeholder(
    sql: str,
) -> tuple[str | None, str | None]:
    """
    Detect dynamic placeholders used in SQL structural positions.

    Ordinary scalar values can later become bind parameters.
    Dynamic identifiers and dynamic SQL list fragments cannot
    safely be treated as ordinary scalar parameters.

    Returns:
        reason_code, reason

    If no unsupported structural placeholder is found:
        None, None
    """

        # Dynamic SQL identifiers must never become bind parameters.
    dynamic_table_pattern = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+"
        r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}",
        re.IGNORECASE,
    )

    if dynamic_table_pattern.search(sql):
        return (
            "STRUCTURAL",
            "Dynamic table identifier cannot be "
            "parameterized safely.",
        )

    order_by_pattern = re.compile(
        r"\bORDER\s+BY\s+"
        r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}",
        re.IGNORECASE,
    )

    if order_by_pattern.search(sql):
        return (
            "STRUCTURAL",
            "Dynamic value occurs in a structural "
            "ORDER BY position.",
        )

    dynamic_in_pattern = re.compile(
        r"\bIN\s*\(\s*"
        r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}"
        r"\s*\)",
        re.IGNORECASE,
    )

    if dynamic_in_pattern.search(sql):
        return (
            "STRUCTURAL",
            "Dynamic value represents an SQL IN-list "
            "fragment rather than a scalar value.",
        )

    return None, None

def _detect_operation(
    sql: str,
) -> str | None:
    """
    Determine the primary SQL operation.
    """

    match = re.match(
        r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b",
        sql,
        re.IGNORECASE,
    )

    if not match:
        return None

    return match.group(1).upper()




def _collect_cross_file_diagnostics(
    assignment_chain: list[dict],
    functions: list[dict],
    includes: list[dict],
) -> dict | None:
    """Collect non-executable diagnostics for simple function calls."""

    if len(assignment_chain) != 1:
        return None

    assignment = assignment_chain[0]
    function_call = assignment.get("function_call")

    if not isinstance(function_call, dict):
        return None

    function_name = function_call.get("name")
    arguments = function_call.get("arguments")

    if not isinstance(function_name, str):
        return None

    if not isinstance(arguments, list):
        return None

    matching_functions = [
        function
        for function in functions
        if isinstance(function, dict)
        and isinstance(function.get("name"), str)
        and function["name"].lower() == function_name.lower()
    ]

    if len(matching_functions) != 1:
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": (
                "Function definition is missing or ambiguous."
            ),
        }

    function = matching_functions[0]


    # Verify that the function's defining file was explicitly
    # included by the caller before the SQL assignment.
    caller_file = assignment.get("source_file")
    function_file = function.get("source_file")
    assignment_line = assignment.get("source_line")

    if (
        not isinstance(caller_file, str)
        or not isinstance(function_file, str)
        or not isinstance(assignment_line, int)
    ):
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Missing function/include location metadata.",
        }

    caller_path = Path(caller_file).resolve()
    function_path = Path(function_file).resolve()

    matching_includes = [
        include
        for include in includes
        if isinstance(include, dict)
        and isinstance(include.get("source_file"), str)
        and isinstance(include.get("target_file"), str)
        and Path(include["source_file"]).resolve() == caller_path
        and Path(include["target_file"]).resolve() == function_path
        and include.get("supported") is True
        and include.get("control_flow") == []
        and isinstance(include.get("source_line"), int)
        and include["source_line"] < assignment_line
    ]

    if not matching_includes:
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": (
                "Function definition is not available through "
                "a verified unconditional include."
            ),
        }

    parameters = function.get("parameters")
    return_expression = function.get("return_expression")

    if not function.get("simple_return"):
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Function does not have a simple return.",
        }

    if not isinstance(parameters, list):
        return None

    if len(parameters) != len(arguments):
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Function argument count does not match.",
        }

    if not isinstance(return_expression, str):
        return None

    template, variables, supported = (
        _expression_to_template(return_expression)
    )

    if not supported:
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Function return expression is unsupported.",
        }

    if sorted(variables) != sorted(parameters):
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Function parameter mapping is incomplete.",
        }

    if any(
        not isinstance(argument, dict)
        or argument.get("kind") != "COALESCE"
        or argument.get("input_source") != "_GET"
        or not isinstance(argument.get("input_key"), str)
        or not isinstance(argument.get("default_expression"), str)
        for argument in arguments
    ):
        return {
            "function_name": function_name,
            "resolution_status": "UNRESOLVED",
            "reason": "Function argument expression is unsupported.",
        }

    # Describe parameter binding without evaluating PHP input
    # or enabling automatic SQL conversion.
    binding_plan = []

    for parameter, argument in zip(parameters, arguments):
        default_expression = argument["default_expression"]

        # Currently support only single- or double-quoted
        # literal fallbacks, without escape sequences.
        if (
            len(default_expression) < 2
            or default_expression[0] not in ("'", '"')
            or default_expression[-1] != default_expression[0]
            or "\\" in default_expression
        ):
            return {
                "function_name": function_name,
                "resolution_status": "UNRESOLVED",
                "reason": "Unsupported fallback expression.",
            }

        fallback = default_expression[1:-1]

        binding_plan.append({
            "parameter": parameter,
            "source": "_GET",
            "key": argument["input_key"],
            "fallback": fallback,
            "fallback_policy": "null_coalescing",
            "binding_type": "scalar",
            "requires_validation": True,
        })

    return {
        "function_name": function_name,
        "function_source_file": function.get("source_file"),
        "resolution_status": "DIAGNOSTIC_ONLY",
        "binding_plan": binding_plan,
        "sql_template": _normalize_sql(template),
        "parameter_bindings": [
            {
                "parameter": parameter,
                "argument": argument,
            }
            for parameter, argument in zip(parameters, arguments)
        ],
        "requires_include_validation": False,
        "safe_for_conversion": False,
    }



def _validated_cross_file_candidate(
    diagnostics: dict | None,
    branching_reason_code: str | None,
) -> dict | None:
    """Prepare a cross-file SQL candidate without changing recovery status."""

    if branching_reason_code is not None:
        return None

    if not isinstance(diagnostics, dict):
        return None

    if diagnostics.get("resolution_status") != "DIAGNOSTIC_ONLY":
        return None

    if diagnostics.get("requires_include_validation") is not False:
        return None

    template = diagnostics.get("sql_template")
    binding_plan = diagnostics.get("binding_plan")

    if not isinstance(template, str) or not template.strip():
        return None

    if not isinstance(binding_plan, list) or not binding_plan:
        return None

    # Verify every input binding before preparing a candidate.
    for binding in binding_plan:
        if not isinstance(binding, dict):
            return None

        if (
            not isinstance(binding.get("parameter"), str)
            or not binding["parameter"]
            or binding.get("source") != "_GET"
            or not isinstance(binding.get("key"), str)
            or not binding["key"]
            or binding.get("fallback_policy") != "null_coalescing"
            or not isinstance(binding.get("fallback"), str)
            or binding.get("binding_type") != "scalar"
            or binding.get("requires_validation") is not True
        ):
            return None

    placeholders = set(
        re.findall(
            r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}",
            template,
        )
    )

    parameters = {
        binding.get("parameter")
        for binding in binding_plan
        if isinstance(binding, dict)
        and isinstance(binding.get("parameter"), str)
    }

    if not placeholders or placeholders != parameters:
        return None

    if len(parameters) != len(binding_plan):
        return None

    if _detect_operation(template) != "SELECT":
        return None

    structural_code, _ = _detect_structural_placeholder(template)

    if structural_code is not None:
        return None

    return {
        "recovered_sql": template,
        "operation": "SELECT",
        "dynamic_variables": sorted(parameters),
        "binding_plan": binding_plan,
    }




def _resolve_isset_branch_condition(
    condition: str,
    source_file: str,
    branch_line: int,
    assignments: list[dict],
) -> dict | None:
    """Resolve a simple PHP variable assigned from isset($_GET[...])."""

    if not isinstance(condition, str):
        return None

    if not isinstance(source_file, str):
        return None

    if not isinstance(branch_line, int):
        return None

    variable_match = re.fullmatch(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        condition.strip(),
    )

    if variable_match is None:
        return None

    variable = "$" + variable_match.group(1)

    # Reject competing definitions, including assignments inside branches.
    # The supported pattern has exactly one definition before the IF.
    matches = [
        assignment
        for assignment in assignments
        if isinstance(assignment, dict)
        and assignment.get("source_file") == source_file
        and assignment.get("variable") == variable
    ]

    if len(matches) != 1:
        return None

    definition = matches[0]

    if (
        definition.get("assignment_type") != "ASSIGN"
        or definition.get("control_flow") != []
        or not isinstance(definition.get("source_line"), int)
        or definition["source_line"] >= branch_line
    ):
        return None

    expression = matches[0].get("expression")

    if not isinstance(expression, str):
        return None

    isset_match = re.fullmatch(
        r"""isset\(\s*\$_GET\s*\[\s*(['"])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]\s*\)""",
        expression.strip(),
    )

    if isset_match is None:
        return None

    return {
        "condition_variable": variable_match.group(1),
        "kind": "ISSET",
        "input_source": "_GET",
        "input_key": isset_match.group(2),
        "true_when": "present_and_not_null",
    }


def recover_queries_from_ast(
    ast_data: dict,
) -> list[dict]:
    """
    Recover SQL queries from AST analysis data.
    """

    sql_calls = ast_data.get(
        "sql_calls",
        []
    )

    assignments = ast_data.get(
        "assignments",
        []
    )

    functions = ast_data.get("functions", [])
    includes = ast_data.get("includes", [])

    recovered_queries = []

    for index, sql_call in enumerate(
        sql_calls,
        start=1,
    ):
        query_id = f"Q{index:03d}"

        sql_argument = sql_call.get(
            "sql_argument"
        )

        # Determine whether SQL is passed directly or through a variable.
        sql_argument_text = (sql_argument or "").strip()

        direct_sql_expression = not bool(
            re.fullmatch(
                r"\$[A-Za-z_][A-Za-z0-9_]*",
                sql_argument_text,
            )
        )

        sql_variants = []
        cross_file_diagnostics = None

        if direct_sql_expression:
            # Direct literal, interpolation, or concatenated SQL.
            assignment_chain = [
                {
                    "source_file": sql_call.get("source_file"),
                    "source_line": sql_call.get("source_line"),
                    "assignment_type": "ASSIGN",
                    "expression": sql_argument_text,
                    "control_flow": sql_call.get("control_flow", []),
                }
            ]
        else:
            # Existing SQL-variable assignment recovery.
            relevant_assignments = _find_relevant_assignments(
                sql_call,
                assignments,
            )

            assignment_chain = _select_assignment_chain(
                relevant_assignments
            )

            sql_variants = _collect_branch_sql_variants(
                relevant_assignments
            )

            if not sql_variants:
                sql_variants = _collect_conditional_append_variants(
                    relevant_assignments
                )

            cross_file_diagnostics = _collect_cross_file_diagnostics(
                assignment_chain,
                functions,
                includes,
            )


        if not assignment_chain:
            recovered_queries.append(
                {
                    "query_id": query_id,
                    "source_file": sql_call.get(
                        "source_file"
                    ),
                    "source_line": sql_call.get(
                        "source_line"
                    ),
                    "executor": sql_call.get(
                        "executor"
                    ),
                    "sql_argument": sql_argument,
                    "recovered_sql": None,
                    "operation": None,
                    "dynamic_variables": [],
                    "status": "UNRESOLVED",
                    "reason": (
                        "No recoverable assignment "
                        "chain found."
                    ),
                }
            )

            continue

        (
            branching_reason_code,
            branching_reason,
        ) = _detect_branching_assignment(
            assignment_chain
        )

        sql_fragments = []
        dynamic_variables = []
        fully_supported = True

        for assignment in assignment_chain:

            if assignment.get("assignment_type") == "UNSUPPORTED_MUTATION":
                fully_supported = False
                continue

            expression = assignment.get(
                "expression",
                "",
            )

            (
                fragment,
                variables,
                supported,
            ) = _expression_to_template(
                expression
            )

            sql_fragments.append(fragment)

            for variable in variables:
                if (
                    variable
                    not in dynamic_variables
                ):
                    dynamic_variables.append(
                        variable
                    )

            if not supported:
                fully_supported = False

        recovered_sql = _normalize_sql(
            "".join(sql_fragments)
        )

        operation = _detect_operation(
            recovered_sql
        )

        # Promote only fully validated cross-file SQL candidates.
        cross_file_candidate = _validated_cross_file_candidate(
            cross_file_diagnostics,
            branching_reason_code,
        )

        if cross_file_candidate is not None:
            recovered_sql = cross_file_candidate["recovered_sql"]
            operation = cross_file_candidate["operation"]
            dynamic_variables = cross_file_candidate["dynamic_variables"]
            fully_supported = True

        reason_code = None

        if branching_reason_code is not None:
            status = "UNRESOLVED"
            reason_code = branching_reason_code
            reason = branching_reason
            operation = None

        elif not fully_supported:
            status = "UNRESOLVED"
            reason_code = "UNKNOWN_EXPR"
            reason = (
                "Query contains unsupported "
                "dynamic expression."
            )

        elif operation is None:
            status = "UNRESOLVED"
            reason_code = "UNSUPPORTED_SHAPE"
            reason = (
                "Recovered expression is not a "
                "recognized CRUD SQL operation."
            )

        else:
            (
                structural_reason_code,
                structural_reason,
            ) = _detect_structural_placeholder(
                recovered_sql
            )

            if structural_reason_code is not None:
                status = "UNRESOLVED"
                reason_code = structural_reason_code
                reason = structural_reason
                operation = None

            else:
                status = "RECOVERED"
                reason = None

        recovered_queries.append(
            {
                "query_id": query_id,
                "source_file": sql_call.get(
                    "source_file"
                ),
                "source_line": sql_call.get(
                    "source_line"
                ),
                "executor": sql_call.get(
                    "executor"
                ),
                "sql_argument": sql_argument,
                "recovered_sql": recovered_sql,
                "operation": operation,
                "dynamic_variables": (
                    dynamic_variables
                ),
                **(
                    {"binding_plan": cross_file_candidate["binding_plan"]}
                    if cross_file_candidate is not None
                    else {}
                ),
                "status": status,
                "reason_code": reason_code,
                "reason": reason,
                **(
                    {"cross_file_diagnostics": cross_file_diagnostics}
                    if cross_file_diagnostics is not None
                    else {}
                ),
                **(
                    {"sql_variants": sql_variants}
                    if reason_code == "BRANCHING" and sql_variants
                    else {}
                ),
            }
        )

    # Preserve validated conditional SQL as diagnostic metadata.
    # Do not promote unresolved queries to RECOVERED.
    from c3.mapping.branch_recovery import (
        recover_simple_if_else_candidate,
    )

    unsupported_operations = ast_data.get("unsupported_operations")

    for query in recovered_queries:
        if (
            query.get("status") != "UNRESOLVED"
            or query.get("reason_code") != "BRANCHING"
        ):
            continue

        candidate = recover_simple_if_else_candidate(
            query,
            assignments,
            unsupported_operations,
        )

        if candidate is not None:
            from c3.mapping.branch_source_gate import (
                validate_q014_source_shape,
            )

            source_file = query.get("source_file")

            candidate["source_shape_verified"] = (
                isinstance(source_file, str)
                and validate_q014_source_shape(source_file)
            )

            query["conditional_candidate"] = candidate

    return recovered_queries

def recover_queries(
    php_path: str | Path,
    php_executable: str = "php",
) -> list[dict]:
    """
    Run AST analysis and recover SQL queries.
    """

    ast_data = analyze_php_ast(
        php_path=php_path,
        php_executable=php_executable,
    )

    return recover_queries_from_ast(
        ast_data
    )


def write_recovered_queries(
    queries: list[dict],
    output_file: str | Path,
) -> None:
    """
    Write recovered SQL queries to JSON.
    """

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            queries,
            indent=4,
        ),
        encoding="utf-8",
    )
