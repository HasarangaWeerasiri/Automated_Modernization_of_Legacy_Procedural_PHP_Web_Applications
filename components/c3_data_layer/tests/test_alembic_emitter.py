"""Alembic emitter (S10, FR9). Fixtures are written for this test only."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from c3.conversion.model_builder import mysql_only_defaults
from c3.generation.alembic_emitter import dependency_order
from c3.pipeline import run_pipeline

SCHEMA = """
CREATE TABLE orders (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT NOT NULL,
  note VARCHAR(40) NOT NULL DEFAULT 'none',
  KEY idx_orders_customer (customer_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
);
CREATE TABLE customers (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(120) NOT NULL UNIQUE,
  referrer_id INT NULL,
  FOREIGN KEY (referrer_id) REFERENCES customers (id)
);
CREATE TABLE notes (
  order_id INT NOT NULL,
  body TEXT NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders (id)
);
"""


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    directory = tmp_path_factory.mktemp("alembic_app")
    php = directory / "php"
    php.mkdir()
    (php / "list.php").write_text(
        '<?php\n$r = mysqli_query($conn, "SELECT id, email FROM customers");\n',
        encoding="utf-8",
    )
    (directory / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    out = directory / "out"
    run_pipeline(php, directory / "schema.sql", out)
    return out


def test_files_are_generated_without_credentials(output):
    alembic_dir = output / "alembic"

    for name in ("alembic.ini", "env.py", "script.py.mako", "versions/0001_initial_schema.py"):
        assert (alembic_dir / name).is_file()

    ini = (alembic_dir / "alembic.ini").read_text(encoding="utf-8")
    assert "sqlalchemy.url" not in ini
    assert "C3_ALEMBIC_URL" in ini


def test_revision_creates_tables_in_dependency_order(output):
    revision = (output / "alembic" / "versions" / "0001_initial_schema.py").read_text(encoding="utf-8")

    created = [line.strip() for line in revision.splitlines() if line.strip().startswith("'") and line.strip().endswith("',")]
    order = [name.strip("',") for name in created if name.strip("',") in {"orders", "customers", "notes"}]

    assert order == ["customers", "orders", "notes"]
    assert "op.create_index('idx_orders_customer', 'orders', ['customer_id'])" in revision
    assert "ondelete='CASCADE'" in revision
    assert revision.index("op.drop_table('notes')") < revision.index("op.drop_table('customers')")


def test_revision_is_deterministic(output, tmp_path):
    first = (output / "alembic" / "versions" / "0001_initial_schema.py").read_text(encoding="utf-8")

    run_pipeline(output.parent / "php", output.parent / "schema.sql", tmp_path)
    second = (tmp_path / "alembic" / "versions" / "0001_initial_schema.py").read_text(encoding="utf-8")

    assert first == second


def test_cross_table_cycle_is_deferred():
    tables = [
        {"name": "a", "foreign_keys": [{"column": "b_id", "referenced_table": "b", "referenced_column": "id"}]},
        {"name": "b", "foreign_keys": [{"column": "a_id", "referenced_table": "a", "referenced_column": "id"}]},
        {"name": "c", "foreign_keys": [{"column": "c_id", "referenced_table": "c", "referenced_column": "id"}]},
    ]

    ordered, deferred = dependency_order(tables)

    assert sorted(ordered) == ["a", "b", "c"]
    assert [(name, fk["column"]) for name, fk in deferred] == [("a", "b_id")]


@pytest.mark.parametrize("password", [None, "set-in-session"])
def test_sqlite_round_trip_upgrade_check_downgrade(output, tmp_path, monkeypatch, password):
    # With C3_DB_PASSWORD set (as in the MySQL test sessions) SQLite must still work.
    if password:
        monkeypatch.setenv("C3_DB_PASSWORD", password)
    else:
        monkeypatch.delenv("C3_DB_PASSWORD", raising=False)
    database = tmp_path / "roundtrip.db"
    monkeypatch.setenv("C3_ALEMBIC_URL", f"sqlite:///{database}")
    config = Config(str(output / "alembic" / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    assert {"customers", "orders", "notes", "alembic_version"} <= tables

    command.check(config)  # raises if autogenerate finds differences

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
    engine.dispose()


def test_mysql_only_defaults_are_detected_and_kept():
    schema = {"tables": [{"name": "t", "columns": [
        {"name": "a", "default": "current_timestamp()"},
        {"name": "b", "default": "CURRENT_TIMESTAMP", "on_update": "CURRENT_TIMESTAMP"},
        {"name": "c", "default": "'x'"},
        {"name": "d", "default": "CURRENT_TIMESTAMP"},
    ]}]}

    assert [item["column"] for item in mysql_only_defaults(schema)] == ["a", "b"]
