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

    recovered_queries = []

    for index, sql_call in enumerate(
        sql_calls,
        start=1,
    ):
        query_id = f"Q{index:03d}"

        sql_argument = sql_call.get(
            "sql_argument"
        )

        relevant_assignments = (
            _find_relevant_assignments(
                sql_call,
                assignments,
            )
        )

        assignment_chain = (
            _select_assignment_chain(
                relevant_assignments
            )
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
                "status": status,
                "reason_code": reason_code,
                "reason": reason,
            }
        )

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