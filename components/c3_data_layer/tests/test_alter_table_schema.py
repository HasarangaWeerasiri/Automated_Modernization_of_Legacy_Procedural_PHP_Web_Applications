"""ALTER TABLE-style keys and tables without a primary key (S3b).

phpMyAdmin/mysqldump dumps create tables without keys and add them with
ALTER TABLE. Fixtures are written for this test only.
"""

import importlib.util
import json

from sqlalchemy import Table
from sqlalchemy.orm import clear_mappers, configure_mappers

from c3.conversion.model_builder import generate_models
from c3.mapping.schema_reader import read_schema, write_schema_json


DUMP = """
CREATE TABLE `authors` (
  `id` int(11) NOT NULL,
  `name` varchar(60) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

CREATE TABLE `books` (
  `id` int(11) NOT NULL,
  `author_id` int(11) DEFAULT NULL,
  `title` varchar(120) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

CREATE TABLE `tags` (
  `book_id` int(11) NOT NULL,
  `label` varchar(30) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

CREATE TABLE `audit_log` (
  `book_id` int(11) NOT NULL,
  `note` text NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

ALTER TABLE `authors`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `name` (`name`);

ALTER TABLE `books`
  ADD PRIMARY KEY (`ID`),
  ADD KEY `author_id` (`author_id`);

ALTER TABLE `tags`
  ADD PRIMARY KEY (`book_id`,`label`);

ALTER TABLE `authors`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=7;

ALTER TABLE `books`
  CHANGE `id` `id` int(11) NOT NULL AUTO_INCREMENT;

ALTER TABLE `books`
  ADD CONSTRAINT `books_ibfk_1` FOREIGN KEY (`author_id`) REFERENCES `authors` (`id`);

ALTER TABLE `audit_log`
  ADD CONSTRAINT `audit_fk` FOREIGN KEY (`book_id`) REFERENCES `books` (`id`);
"""


def _tables(tmp_path):
    schema_file = tmp_path / "dump.sql"
    schema_file.write_text(DUMP, encoding="utf-8")
    return {table.name: table for table in read_schema(schema_file).tables}


def test_alter_add_primary_key_single_and_composite(tmp_path):
    tables = _tables(tmp_path)

    def keys(name):
        return [c.name for c in tables[name].columns if c.primary_key]

    assert keys("authors") == ["id"]
    assert keys("books") == ["id"]  # column names are case-insensitive
    assert keys("tags") == ["book_id", "label"]
    assert keys("audit_log") == []


def test_alter_modify_and_change_set_auto_increment(tmp_path):
    tables = _tables(tmp_path)

    authors_id = tables["authors"].columns[0]
    books_id = tables["books"].columns[0]

    assert authors_id.auto_increment and authors_id.primary_key
    assert authors_id.nullable is False
    assert books_id.auto_increment and books_id.primary_key


def test_alter_add_constraint_foreign_key(tmp_path):
    tables = _tables(tmp_path)

    fk = tables["books"].foreign_keys

    assert len(fk) == 1
    assert (fk[0].column, fk[0].referenced_table, fk[0].referenced_column) == (
        "author_id", "authors", "id",
    )


def test_keyless_table_becomes_core_table_and_models_map(tmp_path):
    tables_dir = tmp_path
    schema_file = tables_dir / "dump.sql"
    schema_file.write_text(DUMP, encoding="utf-8")
    schema_json = tables_dir / "schema.json"
    write_schema_json(read_schema(schema_file), schema_json)

    models_file = tables_dir / "models.py"
    notes_file = tables_dir / "migration_notes.md"
    generate_models(schema_json, models_file, notes_file=notes_file)

    source = models_file.read_text(encoding="utf-8")
    assert "audit_log_table = Table(" in source
    assert "class AuditLog" not in source

    notes = notes_file.read_text(encoding="utf-8")
    assert "- `audit_log`" in notes

    spec = importlib.util.spec_from_file_location("s3b_models", models_file)
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
        configure_mappers()

        assert isinstance(module.audit_log_table, Table)
        # FK column kept on the Core table, but no relationship to it.
        assert list(module.audit_log_table.c.book_id.foreign_keys)
        assert not any(
            "audit" in rel.key for rel in module.Book.__mapper__.relationships
        )
        assert [r.key for r in module.Book.__mapper__.relationships] == ["author"]
        assert [c.name for c in module.Tag.__table__.primary_key] == ["book_id", "label"]
    finally:
        clear_mappers()

    assert json.loads(schema_json.read_text(encoding="utf-8"))["tables"][0]["name"] == "authors"
