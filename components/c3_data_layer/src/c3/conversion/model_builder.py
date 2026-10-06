"""
SQLAlchemy Model Builder
Component 3 - Data Layer Migration

Generates SQLAlchemy ORM models from the structured
schema recovered during the Mapping stage.

The generated models preserve:
- table names
- column types
- primary keys
- nullability
- auto-increment properties
- foreign keys
- ORM relationships
"""

import json
import re
from pathlib import Path

from c3.conversion.type_map import mysql_to_sqlalchemy


def _singularize(name: str) -> str:
    """
    Apply simple deterministic singularization
    for common table-name patterns.
    """

    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"

    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]

    return name


def table_to_class_name(table_name: str) -> str:
    """
    Convert a table name into a Python class name.

    Examples:
        users -> User
        appointments -> Appointment
        user_accounts -> UserAccount
    """

    singular = _singularize(table_name)

    parts = re.split(
        r"[^A-Za-z0-9]+",
        singular,
    )

    return "".join(
        part.capitalize()
        for part in parts
        if part
    )


def _relationship_name_from_column(
    column_name: str,
) -> str:
    """
    Derive the child-side relationship name
    from a foreign-key column.

    Examples:
        user_id -> user
        category_id -> category
    """

    if column_name.endswith("_id"):
        return column_name[:-3]

    return column_name


def _child_collection_name(
    table_name: str,
) -> str:
    """
    Use the child table name as the parent-side
    collection relationship name.

    Example:
        appointments -> appointments
    """

    return table_name


def load_schema(
    schema_file: str | Path,
) -> dict:
    """
    Load schema information from schema.json.
    """

    schema_path = Path(schema_file)

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {schema_path}"
        )

    return json.loads(
        schema_path.read_text(
            encoding="utf-8"
        )
    )


def _find_foreign_key(
    table: dict,
    column_name: str,
) -> dict | None:
    """
    Find the foreign-key definition associated
    with a column.
    """

    for foreign_key in table.get(
        "foreign_keys",
        [],
    ):
        if (
            foreign_key["column"]
            == column_name
        ):
            return foreign_key

    return None


def _build_column(
    table: dict,
    column: dict,
) -> str:
    """
    Generate one SQLAlchemy mapped_column
    declaration.
    """

    column_name = column["name"]

    sqlalchemy_type = mysql_to_sqlalchemy(
        column["data_type"]
    )

    arguments = [
        sqlalchemy_type
    ]

    foreign_key = _find_foreign_key(
        table,
        column_name,
    )

    if foreign_key:
        target = (
            f'{foreign_key["referenced_table"]}.'
            f'{foreign_key["referenced_column"]}'
        )

        arguments.append(
            f'ForeignKey("{target}")'
        )

    keyword_arguments = []

    if column.get(
        "primary_key",
        False,
    ):
        keyword_arguments.append(
            "primary_key=True"
        )

    if column.get(
        "auto_increment",
        False,
    ):
        keyword_arguments.append(
            "autoincrement=True"
        )

    keyword_arguments.append(
        f'nullable={column.get("nullable", True)}'
    )

    all_arguments = (
        arguments
        + keyword_arguments
    )

    return (
        f"    {column_name} = mapped_column("
        + ", ".join(all_arguments)
        + ")"
    )


def _build_relationships(
    schema: dict,
) -> dict[str, list[str]]:
    """
    Build bidirectional SQLAlchemy relationship
    declarations from recovered foreign keys.

    Example:

        appointments.user_id -> users.id

    produces:

        User.appointments
        Appointment.user
    """

    relationships: dict[
        str,
        list[str],
    ] = {}

    tables = schema.get(
        "tables",
        [],
    )

    for table in tables:
        relationships[
            table["name"]
        ] = []

    for child_table in tables:
        child_table_name = (
            child_table["name"]
        )

        child_class = (
            table_to_class_name(
                child_table_name
            )
        )

        foreign_keys = (
            child_table.get(
                "foreign_keys",
                [],
            )
        )

        for foreign_key in foreign_keys:
            parent_table_name = (
                foreign_key[
                    "referenced_table"
                ]
            )

            parent_class = (
                table_to_class_name(
                    parent_table_name
                )
            )

            child_relationship = (
                _relationship_name_from_column(
                    foreign_key["column"]
                )
            )

            parent_relationship = (
                _child_collection_name(
                    child_table_name
                )
            )

            child_declaration = (
                f"    {child_relationship} = "
                f'relationship("{parent_class}", '
                f'back_populates="'
                f'{parent_relationship}")'
            )

            parent_declaration = (
                f"    {parent_relationship} = "
                f'relationship("{child_class}", '
                f'back_populates="'
                f'{child_relationship}")'
            )

            relationships.setdefault(
                child_table_name,
                [],
            ).append(
                child_declaration
            )

            relationships.setdefault(
                parent_table_name,
                [],
            ).append(
                parent_declaration
            )

    return relationships


def generate_models_source(
    schema: dict,
) -> str:
    """
    Generate complete SQLAlchemy ORM model
    source code from a recovered schema.
    """

    lines = [
        '"""',
        "Generated SQLAlchemy ORM models.",
        "",
        (
            "Generated by Component 3 - "
            "Data Layer Migration."
        ),
        '"""',
        "",
        "from sqlalchemy import (",
        "    BigInteger,",
        "    Boolean,",
        "    Date,",
        "    DateTime,",
        "    Float,",
        "    ForeignKey,",
        "    Integer,",
        "    LargeBinary,",
        "    Numeric,",
        "    SmallInteger,",
        "    String,",
        "    Text,",
        "    Time,",
        ")",
        (
            "from sqlalchemy.orm import "
            "DeclarativeBase, mapped_column, "
            "relationship"
        ),
        "",
        "",
        "class Base(DeclarativeBase):",
        "    pass",
        "",
        "",
    ]

    tables = schema.get(
        "tables",
        [],
    )

    relationships = (
        _build_relationships(
            schema
        )
    )

    for index, table in enumerate(
        tables
    ):
        table_name = table["name"]

        class_name = (
            table_to_class_name(
                table_name
            )
        )

        lines.append(
            f"class {class_name}(Base):"
        )

        lines.append(
            f'    __tablename__ = "{table_name}"'
        )

        lines.append("")

        columns = table.get(
            "columns",
            [],
        )

        if not columns:
            lines.append(
                "    pass"
            )

        else:
            for column in columns:
                lines.append(
                    _build_column(
                        table,
                        column,
                    )
                )

        table_relationships = (
            relationships.get(
                table_name,
                [],
            )
        )

        if table_relationships:
            lines.append("")

            lines.extend(
                table_relationships
            )

        if index < len(tables) - 1:
            lines.extend(
                [
                    "",
                    "",
                ]
            )

    lines.append("")

    return "\n".join(
        lines
    )


def generate_models(
    schema_file: str | Path,
    output_file: str | Path,
) -> None:
    """
    Load schema.json and generate models.py.
    """

    schema = load_schema(
        schema_file
    )

    source = (
        generate_models_source(
            schema
        )
    )

    output_path = Path(
        output_file
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        source,
        encoding="utf-8",
    )