"""
SQL Parsing and Column-Use Analysis
Component 3 - Data Layer Migration

Parses recovered SQL using sqlglot and extracts:
- SQL operation
- referenced tables
- referenced columns
- dynamic variables
"""

import json
import re
from pathlib import Path

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


PLACEHOLDER_PATTERN = re.compile(
    r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}"
)


def prepare_sql_for_parsing(sql: str) -> str:
    """
    Convert recovery placeholders into SQL-valid parameters.

    Example:
        WHERE id = {{id}}

    becomes:
        WHERE id = :id
    """

    return PLACEHOLDER_PATTERN.sub(
        lambda match: ":" + match.group(1),
        sql,
    )


def _unique(values: list[str]) -> list[str]:
    """Return values without duplicates while preserving order."""

    result = []

    for value in values:
        if value not in result:
            result.append(value)

    return result


def parse_recovered_query(query: dict) -> dict:
    """
    Parse one recovered query and extract table/column usage.
    """

    query_id = query.get("query_id")
    recovered_sql = query.get("recovered_sql")

    if (
        query.get("status") != "RECOVERED"
        or not recovered_sql
    ):
        return {
            "query_id": query_id,
            "source_file": query.get("source_file"),
            "source_line": query.get("source_line"),
            "operation": query.get("operation"),
            "tables": [],
            "columns": [],
            "dynamic_variables": query.get(
                "dynamic_variables",
                [],
            ),
            "status": "UNRESOLVED",
            "reason": query.get("reason")
            or "Query was not recovered.",
        }

    parseable_sql = prepare_sql_for_parsing(
        recovered_sql
    )

    try:
        tree = parse_one(
            parseable_sql,
            dialect="mysql",
        )

    except ParseError as exc:
        return {
            "query_id": query_id,
            "source_file": query.get("source_file"),
            "source_line": query.get("source_line"),
            "operation": query.get("operation"),
            "tables": [],
            "columns": [],
            "dynamic_variables": query.get(
                "dynamic_variables",
                [],
            ),
            "status": "UNRESOLVED",
            "reason": f"SQL parse error: {exc}",
        }

    tables = [
        table.name
        for table in tree.find_all(exp.Table)
        if table.name
    ]

    columns = [
        column.name
        for column in tree.find_all(exp.Column)
        if column.name
    ]

    # INSERT statements store their target columns differently
    # in the sqlglot AST.
    if isinstance(tree, exp.Insert):
        insert_target = tree.this

        if isinstance(insert_target, exp.Schema):
            insert_columns = []

            for expression in insert_target.expressions:
                if isinstance(
                    expression,
                    exp.Identifier,
                ):
                    insert_columns.append(
                        expression.name
                    )

                elif isinstance(
                    expression,
                    exp.Column,
                ):
                    insert_columns.append(
                        expression.name
                    )

            columns = (
                insert_columns
                + columns
            )

    columns = _unique(columns)

    return {
        "query_id": query_id,
        "source_file": query.get("source_file"),
        "source_line": query.get("source_line"),
        "operation": query.get("operation"),
        "tables": _unique(tables),
        "columns": columns,
        "dynamic_variables": query.get(
            "dynamic_variables",
            [],
        ),
        "status": "PARSED",
        "reason": None,
    }


def analyze_column_usage(
    queries: list[dict],
) -> list[dict]:
    """
    Analyze all recovered queries.
    """

    return [
        parse_recovered_query(query)
        for query in queries
    ]


def load_queries(
    input_file: str | Path,
) -> list[dict]:
    """Load recovered queries from JSON."""

    input_path = Path(input_file)

    return json.loads(
        input_path.read_text(
            encoding="utf-8"
        )
    )


def write_column_usage(
    usage: list[dict],
    output_file: str | Path,
) -> None:
    """Write column-use analysis to JSON."""

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            usage,
            indent=4,
        ),
        encoding="utf-8",
    )