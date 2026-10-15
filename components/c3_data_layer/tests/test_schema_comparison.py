"""E2 schema comparison (scripts/schema_comparison.py). Fixtures written for this test."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import schema_comparison as e2  # noqa: E402
from c3.pipeline import run_pipeline  # noqa: E402

DUMP = """
-- phpMyAdmin style dump
CREATE TABLE `people` (
  `id` int(11) NOT NULL,
  `code` char(4) NOT NULL,
  `seen` timestamp NOT NULL DEFAULT current_timestamp(),
  `score` double DEFAULT NULL,
  `team_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `teams` (
  `id` int(10) UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `name` varchar(40) NOT NULL UNIQUE
);

ALTER TABLE `people`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `code_u` (`code`),
  ADD KEY `team_idx` (`team_id`);

ALTER TABLE `people`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=4;
"""


def test_norm_type_spellings():
    assert e2.norm_type("int(11)") == "INT"
    assert e2.norm_type("INTEGER") == "INT"
    assert e2.norm_type("DECIMAL(10, 2)") == "DECIMAL(10,2)"
    assert e2.norm_type("varchar(40) COLLATE utf8mb4_bin") == "VARCHAR(40)"
    assert e2.norm_type("INTEGER UNSIGNED") == "INT UNSIGNED"


def test_independent_parse_reads_alter_table_keys(tmp_path):
    schema = tmp_path / "schema.sql"
    schema.write_text(DUMP, encoding="utf-8")

    tables = e2.sqlglot_schema(schema)

    assert tables["people"]["primary_key"] == ["id"]
    assert ("code",) in tables["people"]["unique"]
    assert ("team_id",) in tables["people"]["indexes"]
    assert tables["people"]["columns"]["seen"]["type"] == "TIMESTAMP"
    assert tables["teams"]["columns"]["id"]["type"] == "INT UNSIGNED"
    assert ("name",) in tables["teams"]["unique"]


def test_all_views_match_for_the_fixture(tmp_path):
    php = tmp_path / "php"
    php.mkdir()
    (php / "a.php").write_text('<?php mysqli_query($c, "SELECT id FROM people");\n', encoding="utf-8")
    schema = tmp_path / "schema.sql"
    schema.write_text(DUMP, encoding="utf-8")
    run_pipeline(php, schema, tmp_path / "out")

    a = e2.sqlglot_schema(schema)
    assert e2.compare(a, e2.json_schema(tmp_path / "out" / "schema.json")) == []
    assert e2.compare(a, e2.orm_schema(tmp_path / "out" / "models.py")) == []


def test_compare_reports_type_and_key_differences():
    reference = {"t": {"columns": {"a": {"type": "TIMESTAMP", "nullable": False}}, "primary_key": ["a"],
                       "foreign_keys": [], "unique": [("a",)], "indexes": []}}
    candidate = {"t": {"columns": {"a": {"type": "DATETIME", "nullable": False}}, "primary_key": [],
                       "foreign_keys": [], "unique": [], "indexes": []}}

    kinds = {d["kind"] for d in e2.compare(reference, candidate)}

    assert kinds == {"type", "primary_key", "unique"}


def test_query_schema_drift_reports_missing_tables_and_columns():
    schema = {"tables": [{"name": "t", "columns": [{"name": "id"}, {"name": "a"}]}]}
    usage = [
        {"query_id": "Q1", "status": "ANALYZED", "source_file": "x.php", "source_line": 3, "tables": ["t"],
         "qualified_columns": {"write": ["t.payment"], "filter": ["t.id"], "read": ["t.*"]}},
        {"query_id": "Q2", "status": "ANALYZED", "source_file": "y.php", "source_line": 5, "tables": ["users"],
         "qualified_columns": {"read": ["users.fullName"]}},
        {"query_id": "Q3", "status": "UNRESOLVED", "tables": ["nope"], "qualified_columns": {}},
    ]

    drift = e2.query_schema_drift(usage, schema)

    assert [(d["query_id"], d["kind"], d.get("column")) for d in drift] == [
        ("Q1", "column_not_in_schema", "payment"),
        ("Q2", "table_not_in_schema", None),
    ]
