"""S14: prepared statements convert by naming their placeholders.

Fixtures are written for this test only.
"""

import importlib.util
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from c3.conversion.query_converter import convert_query
from c3.pipeline import run_pipeline

BASE = {"query_id": "Q1", "source_file": "x.php", "source_line": 1, "status": "RECOVERED",
        "safe_for_conversion": True, "validated_for_recovery": True}


def convert(executor, sql):
    return convert_query({**BASE, "executor": executor, "recovered_sql": sql, "operation": sql.split()[0].upper()})


@pytest.mark.parametrize("executor,sql,parameters,expected_sql", [
    ("mysqli_prepare", "SELECT id FROM users WHERE id = ?", ["param_1"], "SELECT id FROM users WHERE id = :param_1"),
    ("prepare", "INSERT INTO users (name, email) VALUES (?, ?)", ["param_1", "param_2"],
     "INSERT INTO users (name, email) VALUES (:param_1, :param_2)"),
    ("prepare", "SELECT id FROM users WHERE email = :email AND status = :status", ["email", "status"],
     "SELECT id FROM users WHERE email = :email AND status = :status"),
    ("prepare", "SELECT id FROM users WHERE name = '?' AND id = ?", ["param_1"],
     "SELECT id FROM users WHERE name = '?' AND id = :param_1"),
])
def test_prepared_placeholders_become_named_binds(executor, sql, parameters, expected_sql):
    result = convert(executor, sql)

    assert result["status"] == "CONVERTED"
    assert result["parameters"] == parameters
    assert result["parameterized_sql"] == expected_sql
    assert result["conversion_rule"] == "general"


@pytest.mark.parametrize("executor,sql", [
    ("prepare", "SELECT id FROM users ORDER BY ?"),            # structural position
    ("prepare", "SELECT id FROM ? WHERE id = 1"),               # table position
    ("prepare", "SELECT id FROM users WHERE a = ? AND b = :b"),  # mixed styles
    ("mysqli_query", "SELECT id FROM users WHERE id = ?"),       # not a prepared statement
])
def test_unsupported_prepared_shapes_still_abstain(executor, sql):
    assert convert(executor, sql)["status"] == "UNRESOLVED"


def test_mysqli_prepare_site_generates_a_working_function(tmp_path):
    php = tmp_path / "php"
    php.mkdir()
    (php / "find.php").write_text(
        "<?php\n$stmt = mysqli_prepare($conn, \"SELECT id, name FROM items WHERE name = ? AND id > ?\");\n"
        "mysqli_stmt_bind_param($stmt, 'si', $name, $min);\nmysqli_stmt_execute($stmt);\n",
        encoding="utf-8",
    )
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE items (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, name VARCHAR(20) NOT NULL);",
        encoding="utf-8",
    )
    run_pipeline(php, tmp_path / "schema.sql", tmp_path / "out")
    converted = json.loads((tmp_path / "out" / "converted_queries.json").read_text(encoding="utf-8"))
    assert converted[0]["status"] == "CONVERTED"
    assert converted[0]["prepared_statement_parameters"] == ["param_1", "param_2"]

    spec = importlib.util.spec_from_file_location("s14_da", tmp_path / "out" / "data_access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = create_engine(f"sqlite:///{tmp_path / 's14.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO items VALUES (1, 'a'), (2, 'a'), (3, 'b')"))
    with Session(engine) as session:
        rows = module.select_items_q001(session, param_1="a", param_2=1)
    engine.dispose()

    assert [row["id"] for row in rows] == [2]
