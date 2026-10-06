"""
Schema Reader
Component 3 - Data Layer Migration

Reads a MySQL CREATE TABLE schema dump and converts it
into the Component 3 intermediate representation (IR).
"""

import json
import re
from dataclasses import asdict
from pathlib import Path

from c3.ir import Column, DatabaseSchema, ForeignKey, Table


CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*"
    r"\((.*?)\)\s*"
    r"(?:ENGINE\s*=\s*\w+[^;]*)?;",
    re.IGNORECASE | re.DOTALL,
)


FOREIGN_KEY_PATTERN = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\)"
    r"\s+REFERENCES\s+`?([A-Za-z_][A-Za-z0-9_]*)`?"
    r"\s*\(\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\)",
    re.IGNORECASE,
)


TABLE_PRIMARY_KEY_PATTERN = re.compile(
    r"PRIMARY\s+KEY\s*\((.*?)\)",
    re.IGNORECASE,
)


def split_table_definitions(body: str) -> list[str]:
    """
    Split CREATE TABLE contents using top-level commas.

    We cannot simply use split(',') because SQL types such as
    DECIMAL(10, 2) can contain commas inside parentheses.
    """

    definitions = []
    current = []
    depth = 0

    for character in body:
        if character == "(":
            depth += 1

        elif character == ")":
            depth -= 1

        if character == "," and depth == 0:
            definition = "".join(current).strip()

            if definition:
                definitions.append(definition)

            current = []

        else:
            current.append(character)

    final_definition = "".join(current).strip()

    if final_definition:
        definitions.append(final_definition)

    return definitions

def parse_column(definition: str) -> Column | None:
    """
    Parse one MySQL column definition.

    Example:

        id INT AUTO_INCREMENT PRIMARY KEY

    becomes:

        name = id
        data_type = INT
        primary_key = True
        auto_increment = True
        nullable = False
    """

    stripped = definition.strip()
    upper = stripped.upper()

    # These are table constraints, not column definitions.
    constraint_prefixes = (
        "PRIMARY KEY",
        "FOREIGN KEY",
        "CONSTRAINT",
        "UNIQUE KEY",
        "UNIQUE INDEX",
        "KEY ",
        "INDEX ",
        "CHECK ",
    )

    if upper.startswith(constraint_prefixes):
        return None

    # Read:
    #
    # column_name
    # data_type
    # remaining SQL options
    #
    # Examples:
    #
    # id INT AUTO_INCREMENT PRIMARY KEY
    # name VARCHAR(100) NOT NULL
    # amount DECIMAL(10,2) NOT NULL
    #
    # Important:
    # AUTO_INCREMENT and NOT NULL must NOT become part
    # of the data type.

    match = re.match(
        r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+"
        r"([A-Za-z]+(?:\s*\([^)]*\))?(?:\s+UNSIGNED)?)"
        r"(.*)$",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return None

    name = match.group(1)

    data_type = " ".join(
        match.group(2).split()
    ).upper()

    options = match.group(3)

    primary_key = bool(
        re.search(
            r"\bPRIMARY\s+KEY\b",
            options,
            re.IGNORECASE,
        )
    )

    auto_increment = bool(
        re.search(
            r"\bAUTO_INCREMENT\b",
            options,
            re.IGNORECASE,
        )
    )

    nullable = not bool(
        re.search(
            r"\bNOT\s+NULL\b",
            options,
            re.IGNORECASE,
        )
    )

    # A primary key is semantically non-null.
    if primary_key:
        nullable = False

    default = None

    default_match = re.search(
        r"\bDEFAULT\s+"
        r"((?:'[^']*')|(?:\"[^\"]*\")|(?:[^\s,]+))",
        options,
        re.IGNORECASE,
    )

    if default_match:
        default = default_match.group(1)

    return Column(
        name=name,
        data_type=data_type,
        nullable=nullable,
        primary_key=primary_key,
        auto_increment=auto_increment,
        default=default,
    )

def parse_create_table(
    table_name: str,
    body: str,
) -> Table:
    """
    Convert one CREATE TABLE statement into a Table IR object.
    """

    definitions = split_table_definitions(body)

    columns: list[Column] = []
    foreign_keys: list[ForeignKey] = []

    for definition in definitions:
        column = parse_column(definition)

        if column is not None:
            columns.append(column)

        foreign_key_match = FOREIGN_KEY_PATTERN.search(
            definition
        )

        if foreign_key_match:
            foreign_keys.append(
                ForeignKey(
                    column=foreign_key_match.group(1),
                    referenced_table=foreign_key_match.group(2),
                    referenced_column=foreign_key_match.group(3),
                )
            )

    # Handle table-level primary keys:
    #
    # PRIMARY KEY (`id`)
    #
    # instead of:
    #
    # id INT PRIMARY KEY

    for primary_key_match in TABLE_PRIMARY_KEY_PATTERN.finditer(body):
        raw_columns = primary_key_match.group(1)

        primary_key_columns = [
            value.strip().strip("`")
            for value in raw_columns.split(",")
        ]

        for column in columns:
            if column.name in primary_key_columns:
                column.primary_key = True
                column.nullable = False

    return Table(
        name=table_name,
        columns=columns,
        foreign_keys=foreign_keys,
    )

def read_schema(
    schema_file: str | Path,
) -> DatabaseSchema:
    """
    Read a MySQL .sql schema file.

    Returns:
        DatabaseSchema containing all recovered tables,
        columns and foreign-key relationships.
    """

    schema_path = Path(schema_file)

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file does not exist: {schema_path}"
        )

    sql_text = schema_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    tables: list[Table] = []

    for match in CREATE_TABLE_PATTERN.finditer(sql_text):

        table_name = match.group(1)
        table_body = match.group(2)

        table = parse_create_table(
            table_name=table_name,
            body=table_body,
        )

        tables.append(table)

    return DatabaseSchema(
        tables=tables
    )

def write_schema_json(
    schema: DatabaseSchema,
    output_file: str | Path,
) -> None:
    """
    Save the recovered database schema as JSON.
    """

    output_path = Path(output_file)

    # Create output directory if it does not exist
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    schema_data = asdict(schema)

    output_path.write_text(
        json.dumps(
            schema_data,
            indent=4,
        ),
        encoding="utf-8",
    )