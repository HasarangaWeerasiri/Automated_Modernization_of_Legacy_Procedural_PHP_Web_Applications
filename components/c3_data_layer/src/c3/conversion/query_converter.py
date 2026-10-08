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
        return {
            **base_result,
            "status": "UNRESOLVED",
            "reason": (
                "Initial converter supports exactly "
                "one referenced table."
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
        "status": "CONVERTED",
        "reason": None,
    }


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