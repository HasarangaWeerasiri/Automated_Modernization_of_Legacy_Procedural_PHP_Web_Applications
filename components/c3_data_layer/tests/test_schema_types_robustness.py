"""Schema types never abort the pipeline (S3).

ENUM/SET keep their values' case, integer display widths are accepted, and
a genuinely unsupported type keeps its MySQL DDL verbatim
(UnmappedMySQLType) and is documented in migration_notes.md. Fixtures are written for this test only.
"""

import importlib.util

import pytest
from sqlalchemy import Enum
from sqlalchemy.dialects.mysql import SET
from sqlalchemy.orm import clear_mappers, configure_mappers

from c3.conversion.type_map import mysql_to_sqlalchemy
from c3.mapping.schema_reader import parse_column
from c3.pipeline import run_pipeline


@pytest.mark.parametrize(
    "mysql_type,expected",
    [
        ("INT(11)", "Integer"),
        ("int(4) unsigned", "Integer().with_variant(mysql.INTEGER(unsigned=True), 'mysql')"),
        ("BIGINT(20) UNSIGNED", "BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mysql')"),
        ("SMALLINT(6)", "SmallInteger"),
        ("TINYINT(4)", "Integer().with_variant(mysql.TINYINT(), 'mysql')"),
        ("TINYINT(1)", "Boolean"),
        ("ENUM('active','Blocked')", "Enum('active', 'Blocked')"),
        ("ENUM('it''s','x')", "Enum(\"it's\", 'x')"),
        ("SET('Read','write')", "SET('Read', 'write')"),
        ("DECIMAL(10, 2)", "Numeric(10, 2)"),
        ("DECIMAL", "Numeric"),
        ("DATETIME(6)", "DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')"),
        ("DOUBLE(8,2)", "Float().with_variant(mysql.DOUBLE(), 'mysql')"),
        ("CHAR", "String(1).with_variant(mysql.CHAR(1), 'mysql')"),
        ("JSON", "JSON"),
        ("DATETIME", "DateTime"),
        ("DATETIME(0)", "DateTime"),
        ("TIMESTAMP", "DateTime().with_variant(mysql.TIMESTAMP(), 'mysql')"),
        ("MEDIUMTEXT", "Text().with_variant(mysql.MEDIUMTEXT(), 'mysql')"),
        ("LONGBLOB", "LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql')"),
        ("VARBINARY(16)", "LargeBinary(16).with_variant(mysql.VARBINARY(16), 'mysql')"),
        ("YEAR", "Integer().with_variant(mysql.YEAR(), 'mysql')"),
    ],
)
def test_supported_types(mysql_type, expected):
    assert mysql_to_sqlalchemy(mysql_type) == expected


@pytest.mark.parametrize(
    "mysql_type",
    ["GEOMETRY", "POINT", "VARCHAR", "ENUM", "ENUM(1,2)", "INT(abc)", ""],
)
def test_unsupported_types_raise(mysql_type):
    with pytest.raises(ValueError):
        mysql_to_sqlalchemy(mysql_type)


def test_schema_reader_preserves_enum_value_case():
    column = parse_column(
        "status enum('active','Blocked') NOT NULL DEFAULT 'active'"
    )

    assert column.data_type == "ENUM('active','Blocked')"
    assert column.nullable is False


SCHEMA = """
CREATE TABLE accounts (
  id int(11) NOT NULL AUTO_INCREMENT,
  status ENUM('active','Blocked') NOT NULL DEFAULT 'active',
  perms SET('Read','write') NULL,
  location GEOMETRY NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB;
"""


def test_pipeline_completes_with_enum_set_and_unsupported_type(tmp_path):
    php_dir = tmp_path / "php"
    php_dir.mkdir()
    (php_dir / "list.php").write_text(
        '<?php\n$r = mysqli_query($conn, "SELECT id, status FROM accounts");\n',
        encoding="utf-8",
    )
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text(SCHEMA, encoding="utf-8")
    output = tmp_path / "out"

    summary = run_pipeline(php_dir, schema_file, output)

    assert summary["converted_queries"] == 1
    assert (output / "data_access.py").is_file()

    notes = (output / "migration_notes.md").read_text(encoding="utf-8")
    assert "| accounts | location | `GEOMETRY` | `UnmappedMySQLType (DDL passthrough)` |" in notes

    spec = importlib.util.spec_from_file_location("s3_models", output / "models.py")
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
        configure_mappers()

        table = module.Account.__table__
        assert isinstance(table.c.status.type, Enum)
        assert list(table.c.status.type.enums) == ["active", "Blocked"]
        assert isinstance(table.c.perms.type, SET)
        assert type(table.c.location.type).__name__ == "UnmappedMySQLType"
        assert table.c.location.type.get_col_spec() == "GEOMETRY"
        assert [c.name for c in table.primary_key] == ["id"]
    finally:
        clear_mappers()


def test_migration_notes_without_unsupported_types(tmp_path):
    php_dir = tmp_path / "php"
    php_dir.mkdir()
    (php_dir / "list.php").write_text(
        '<?php\n$r = mysqli_query($conn, "SELECT id FROM accounts");\n',
        encoding="utf-8",
    )
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text(
        "CREATE TABLE accounts (id INT NOT NULL PRIMARY KEY);",
        encoding="utf-8",
    )

    run_pipeline(php_dir, schema_file, tmp_path / "out")

    notes = (tmp_path / "out" / "migration_notes.md").read_text(encoding="utf-8")
    assert "None. Every column type has a SQLAlchemy mapping." in notes


def test_exact_mysql_types_render_as_the_original_on_mysql():
    """E2: generic types that would change the MySQL type keep the exact one."""

    from sqlalchemy import DateTime, Float, Integer, LargeBinary, String, Text  # noqa: F401
    from sqlalchemy.dialects import mysql

    expected = {
        "TIMESTAMP": "TIMESTAMP", "DATETIME(6)": "DATETIME(6)", "TINYINT(4)": "TINYINT",
        "MEDIUMINT": "MEDIUMINT", "DOUBLE": "DOUBLE", "CHAR(2)": "CHAR(2)", "YEAR": "YEAR",
        "LONGTEXT": "LONGTEXT", "MEDIUMBLOB": "MEDIUMBLOB", "BINARY(4)": "BINARY(4)",
    }
    for mysql_type, rendered in expected.items():
        sa_type = eval(mysql_to_sqlalchemy(mysql_type))  # generated-code expression
        assert sa_type.compile(dialect=mysql.dialect()) == rendered, mysql_type
