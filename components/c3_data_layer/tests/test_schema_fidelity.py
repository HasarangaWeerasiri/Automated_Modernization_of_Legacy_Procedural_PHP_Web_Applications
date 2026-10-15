"""Schema fidelity and ORM mapping (S9). Fixtures are written for this test only."""

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, clear_mappers, configure_mappers
from sqlalchemy.schema import CreateTable

from c3.conversion.model_builder import generate_models
from c3.mapping.schema_reader import read_schema, write_schema_json

SCHEMA = """
CREATE TABLE teams (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(80) NOT NULL,
  parent_id INT UNSIGNED NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_team_name (name),
  CONSTRAINT fk_team_parent FOREIGN KEY (parent_id) REFERENCES teams(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE people (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(120) NOT NULL UNIQUE,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  balance DECIMAL(10,2) UNSIGNED NOT NULL DEFAULT 0.00,
  age TINYINT UNSIGNED NULL,
  bio TEXT,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  team_id INT UNSIGNED NULL,
  full_name VARCHAR(160) GENERATED ALWAYS AS (email) VIRTUAL,
  KEY idx_people_status (status),
  FULLTEXT KEY ft_bio (bio),
  CONSTRAINT chk_age CHECK (age >= 0),
  CONSTRAINT fk_people_team FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tasks (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_by INT NOT NULL,
  assigned_to INT NULL,
  title VARCHAR(100) NOT NULL,
  FOREIGN KEY (created_by) REFERENCES people(id),
  FOREIGN KEY (assigned_to) REFERENCES people(id)
) ENGINE=InnoDB;

CREATE TABLE task_tags (
  task_id INT NOT NULL,
  tag VARCHAR(30) NOT NULL,
  PRIMARY KEY (task_id, tag),
  CONSTRAINT fk_tag_task FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE audit_pairs (
  a INT NOT NULL,
  b INT NOT NULL,
  note VARCHAR(20) NOT NULL,
  FOREIGN KEY (a, b) REFERENCES task_tags (task_id, tag)
) ENGINE=InnoDB PARTITION BY HASH(a) PARTITIONS 2;

ALTER TABLE `tasks`
  ADD UNIQUE KEY `uq_task_title` (`title`),
  ADD KEY `idx_assigned` (`assigned_to`);

CREATE VIEW open_tasks AS SELECT id FROM tasks;
CREATE TRIGGER trg_tasks BEFORE INSERT ON tasks FOR EACH ROW SET NEW.title = TRIM(NEW.title);
"""


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    directory = tmp_path_factory.mktemp("fidelity")
    schema_file = directory / "schema.sql"
    schema_file.write_text(SCHEMA, encoding="utf-8")

    schema_json = directory / "schema.json"
    write_schema_json(read_schema(schema_file), schema_json)
    generate_models(schema_json, directory / "models.py", notes_file=directory / "migration_notes.md")

    spec = importlib.util.spec_from_file_location("fidelity_models", directory / "models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configure_mappers()

    yield {
        "module": module,
        "schema": json.loads(schema_json.read_text(encoding="utf-8")),
        "notes": (directory / "migration_notes.md").read_text(encoding="utf-8"),
    }

    clear_mappers()


def _ddl(module, table_name):
    return str(CreateTable(module.Base.metadata.tables[table_name]).compile(dialect=mysql.dialect()))


def test_reader_recovers_constraints(generated):
    tables = {t["name"]: t for t in generated["schema"]["tables"]}

    assert tables["teams"]["unique_constraints"] == [{"name": "uq_team_name", "columns": ["name"]}]
    assert tables["teams"]["foreign_keys"][0]["on_delete"] == "SET NULL"
    people = tables["people"]
    assert people["indexes"] == [{"name": "idx_people_status", "columns": ["status"]}]
    assert people["check_constraints"] == [{"name": "chk_age", "expression": "age >= 0"}]
    assert people["foreign_keys"][0] == {
        "column": "team_id", "referenced_table": "teams", "referenced_column": "id",
        "name": "fk_people_team", "on_delete": "CASCADE", "on_update": "RESTRICT",
    }
    columns = {c["name"]: c for c in people["columns"]}
    assert columns["email"]["unique"] is True
    assert columns["updated_at"]["on_update"] == "CURRENT_TIMESTAMP"
    assert tables["tasks"]["unique_constraints"] == [{"name": "uq_task_title", "columns": ["title"]}]
    assert tables["tasks"]["indexes"] == [{"name": "idx_assigned", "columns": ["assigned_to"]}]
    assert [c["name"] for c in tables["task_tags"]["columns"] if c["primary_key"]] == ["task_id", "tag"]


def test_mysql_ddl_preserves_features(generated):
    module = generated["module"]

    people = _ddl(module, "people")
    assert "UNIQUE (email)" in people
    assert "DEFAULT 'active'" in people
    assert "DEFAULT '0.00'" in people
    assert "DECIMAL(10, 2) UNSIGNED" in people
    assert "TINYINT UNSIGNED" in people
    assert "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in people
    assert "CONSTRAINT chk_age CHECK (age >= 0)" in people
    assert "CONSTRAINT fk_people_team FOREIGN KEY(team_id) REFERENCES teams (id) ON DELETE CASCADE ON UPDATE RESTRICT" in people
    assert {i.name for i in module.Base.metadata.tables["people"].indexes} == {"idx_people_status"}

    teams = _ddl(module, "teams")
    assert "CONSTRAINT uq_team_name UNIQUE (name)" in teams
    assert "ON DELETE SET NULL" in teams


RELATIONS_SCHEMA = """
CREATE TABLE teams (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  parent_id INT UNSIGNED NULL,
  FOREIGN KEY (parent_id) REFERENCES teams(id)
);
CREATE TABLE people (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(120) NOT NULL UNIQUE,
  team_id INT UNSIGNED NULL,
  FOREIGN KEY (team_id) REFERENCES teams(id)
);
CREATE TABLE tasks (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_by INT NOT NULL,
  assigned_to INT NULL,
  title VARCHAR(100) NOT NULL,
  FOREIGN KEY (created_by) REFERENCES people(id),
  FOREIGN KEY (assigned_to) REFERENCES people(id)
);
"""


def test_self_reference_and_two_fks_to_same_table_map_and_traverse(tmp_path):
    # Runs on SQLite, so this schema avoids MySQL-only DDL (ON UPDATE ...).
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text(RELATIONS_SCHEMA, encoding="utf-8")
    write_schema_json(read_schema(schema_file), tmp_path / "schema.json")
    generate_models(tmp_path / "schema.json", tmp_path / "models.py")

    spec = importlib.util.spec_from_file_location("relations_models", tmp_path / "models.py")
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
        configure_mappers()
        engine = create_engine("sqlite://")
        module.Base.metadata.create_all(engine)

        with Session(engine) as session:
            root = module.Team(name="root")
            child = module.Team(name="child", parent=root)
            alice = module.People(email="a@x.test", team=child)
            bob = module.People(email="b@x.test")
            task = module.Task(title="t1", created_by_ref=alice, assigned_to_ref=bob)
            session.add_all([root, child, alice, bob, task])
            session.commit()

            assert child.parent is root
            assert root.teams == [child]
            assert alice.team is child and child.people == [alice]
            assert alice.tasks_via_created_by == [task]
            assert bob.tasks_via_assigned_to == [task]
            assert task.created_by == alice.id and task.assigned_to == bob.id
    finally:
        clear_mappers()


def test_key_less_table_and_notes(generated):
    module = generated["module"]
    notes = generated["notes"]

    assert "audit_pairs" in module.Base.metadata.tables
    assert not hasattr(module, "AuditPair")
    assert "- `audit_pairs`" in notes
    assert "composite foreign key" in notes
    assert "table partitioning" in notes
    assert "FULLTEXT index ft_bio (bio)" in notes
    assert "generated column full_name" in notes
    assert "- VIEW open_tasks" in notes
    assert "- TRIGGER trg_tasks" in notes


@pytest.mark.parametrize(
    "app",
    [
        "simple_crud", "mixed_crud", "hard_crud",
        "clinic_inventory_legacy_hard", "helpdesk_legacy_holdout",
    ],
)
def test_benchmark_models_map_and_compile_for_mysql(tmp_path, app):
    root = Path(__file__).resolve().parents[3] / "benchmarks" / "apps" / app
    schema_file = next(p for p in (root / "schema.sql", root / "database" / "schema.sql") if p.exists())
    schema_json = tmp_path / "schema.json"
    write_schema_json(read_schema(schema_file), schema_json)
    generate_models(schema_json, tmp_path / "models.py")

    spec = importlib.util.spec_from_file_location(f"bench_{app}", tmp_path / "models.py")
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
        configure_mappers()
        for table in module.Base.metadata.sorted_tables:
            assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=mysql.dialect()))
    finally:
        clear_mappers()
