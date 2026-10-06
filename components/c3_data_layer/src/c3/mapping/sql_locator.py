"""
AST-based SQL Execution Locator
Component 3 - Data Layer Migration

Uses the PHP AST extractor to locate database execution
points and collect variable assignments from legacy PHP.
"""

import json
import subprocess
from pathlib import Path


class SQLLocatorError(Exception):
    """Raised when the PHP AST extractor cannot be executed."""


def _run_ast_extractor(
    php_path: str | Path,
    php_executable: str = "php",
) -> dict:
    """
    Run the PHP AST extractor and return its complete JSON result.
    """

    php_path = Path(php_path)

    if not php_path.exists():
        raise FileNotFoundError(
            f"PHP input path does not exist: {php_path}"
        )

    component_root = (
        Path(__file__)
        .resolve()
        .parents[3]
    )

    extractor = (
        component_root
        / "tools"
        / "php_ast"
        / "extract_sql_calls.php"
    )

    if not extractor.exists():
        raise FileNotFoundError(
            f"PHP AST extractor does not exist: {extractor}"
        )

    command = [
        php_executable,
        str(extractor),
        str(php_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

    except FileNotFoundError as exc:
        raise SQLLocatorError(
            f"PHP executable was not found: {php_executable}"
        ) from exc

    if result.returncode != 0:
        raise SQLLocatorError(
            "PHP AST extractor failed:\n"
            + result.stderr.strip()
        )

    try:
        ast_data = json.loads(result.stdout)

    except json.JSONDecodeError as exc:
        raise SQLLocatorError(
            "PHP AST extractor returned invalid JSON."
        ) from exc

    if not isinstance(ast_data, dict):
        raise SQLLocatorError(
            "Expected AST extractor to return a JSON object."
        )

    sql_calls = ast_data.get(
        "sql_calls",
        [],
    )

    assignments = ast_data.get(
        "assignments",
        [],
    )

    if not isinstance(sql_calls, list):
        raise SQLLocatorError(
            "Expected 'sql_calls' to be a JSON array."
        )

    if not isinstance(assignments, list):
        raise SQLLocatorError(
            "Expected 'assignments' to be a JSON array."
        )

    return {
        "sql_calls": sql_calls,
        "assignments": assignments,
    }


def analyze_php_ast(
    php_path: str | Path,
    php_executable: str = "php",
) -> dict:
    """
    Return complete AST information.

    Output structure:

        {
            "sql_calls": [...],
            "assignments": [...]
        }
    """

    return _run_ast_extractor(
        php_path=php_path,
        php_executable=php_executable,
    )


def locate_sql_calls(
    php_path: str | Path,
    php_executable: str = "php",
) -> list[dict]:
    """
    Locate SQL execution points.

    Examples:

        mysqli_query(...)
        mysql_query(...)
        $pdo->query(...)
        $pdo->prepare(...)
        $pdo->exec(...)
    """

    ast_data = _run_ast_extractor(
        php_path=php_path,
        php_executable=php_executable,
    )

    return ast_data["sql_calls"]


def write_sql_locations(
    calls: list[dict],
    output_file: str | Path,
) -> None:
    """
    Write SQL execution locations to JSON.
    """

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            calls,
            indent=4,
        ),
        encoding="utf-8",
    )