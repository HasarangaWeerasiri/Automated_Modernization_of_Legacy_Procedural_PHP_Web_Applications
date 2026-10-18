"""
SQLAlchemy Model Builder
Component 3 - Data Layer Migration

Generates SQLAlchemy ORM models from the structured
schema recovered during the Mapping stage.

The generated models preserve:
- table names
- column types (incl. MySQL UNSIGNED, ENUM, SET)
- primary keys (incl. composite)
- nullability
- auto-increment properties
- server defaults and ON UPDATE CURRENT_TIMESTAMP
- foreign keys (name, ON DELETE, ON UPDATE)
- unique constraints, indexes and CHECK constraints
- ORM relationships (incl. self-references and several foreign keys
  to the same table)

Tables without a primary key are generated as SQLAlchemy Core Table
objects. Anything that is not migrated is listed in migration_notes.md.
"""

import json
import keyword
import re
from pathlib import Path

from c3.conversion.type_map import mysql_to_sqlalchemy
from c3.generation.identifiers import ORM_RESERVED, assign_names, python_name


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

    name = "".join(
        part.capitalize()
        for part in parts
        if part
    )

    if not name or name[0].isdigit():
        name = "Table" + name

    if keyword.iskeyword(name):  # None, True, False
        name += "_"

    return name


def column_attributes(table: dict) -> dict[str, str]:
    """Column name -> attribute name on the mapped class (unchanged unless invalid or reserved)."""

    return assign_names([column["name"] for column in table.get("columns", [])], ORM_RESERVED)


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


def has_primary_key(table: dict) -> bool:
    """True when at least one column of the table is a primary key."""

    return any(
        column.get("primary_key", False)
        for column in table.get("columns", [])
    )


# --------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------

NUMERIC_LITERAL = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _python_string(sql_literal: str) -> str:
    """'it''s' / 'it\\'s' -> it's"""

    body = sql_literal[1:-1]
    return body.replace("''", "'").replace("\\'", "'").replace("\\\\", "\\")


def _server_default(column: dict) -> str | None:
    """Return the server_default=... keyword, or None."""

    raw = column.get("default")
    on_update = column.get("on_update")

    if raw is None or raw.upper() == "NULL":
        return None

    if on_update:
        return f"server_default=text({raw + ' ON UPDATE ' + on_update!r})"

    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return f"server_default={_python_string(raw)!r}"

    if NUMERIC_LITERAL.fullmatch(raw):
        # Literal, as MySQL itself reflects numeric defaults ('0.00').
        return f"server_default={raw!r}"

    # Numbers, CURRENT_TIMESTAMP, functions, b'0', (expressions)
    return f"server_default=text({raw!r})"


def _column_type(column: dict) -> str:
    try:
        return mysql_to_sqlalchemy(column["data_type"])
    except ValueError:
        # Never abort the whole run for one column: the original MySQL
        # type is kept verbatim (DDL passthrough) and listed in
        # migration_notes.md for manual review.
        return f"UnmappedMySQLType({column['data_type']!r})"


def _foreign_key_expression(foreign_key: dict) -> str:
    arguments = [
        f'"{foreign_key["referenced_table"]}.{foreign_key["referenced_column"]}"'
    ]

    if foreign_key.get("name"):
        arguments.append(f"name={foreign_key['name']!r}")
    if foreign_key.get("on_delete"):
        arguments.append(f"ondelete={foreign_key['on_delete']!r}")
    if foreign_key.get("on_update"):
        arguments.append(f"onupdate={foreign_key['on_update']!r}")

    return f"ForeignKey({', '.join(arguments)})"


def _column_arguments(table: dict, column: dict) -> tuple[list[str], str]:
    """Positional + keyword arguments of a column, and its type expression."""

    sqlalchemy_type = _column_type(column)
    arguments = [sqlalchemy_type]

    for foreign_key in table.get("foreign_keys", []):
        if foreign_key["column"].lower() == column["name"].lower():
            arguments.append(_foreign_key_expression(foreign_key))

    if column.get("primary_key", False):
        arguments.append("primary_key=True")

    if column.get("auto_increment", False):
        arguments.append("autoincrement=True")

    if column.get("unique", False):
        arguments.append("unique=True")

    arguments.append(f'nullable={column.get("nullable", True)}')

    server_default = _server_default(column)

    if server_default:
        arguments.append(server_default)

    return arguments, sqlalchemy_type


def _build_column(
    table: dict,
    column: dict,
    core: bool = False,
) -> str:
    """
    Generate one SQLAlchemy mapped_column declaration, or a Core
    Column(...) entry when core is True (tables without primary key).
    """

    arguments, sqlalchemy_type = _column_arguments(table, column)
    column_name = column["name"]
    attribute = column_attributes(table)[column_name]

    if core:
        declaration = f'    Column("{column_name}", ' + ", ".join(arguments) + "),"
    elif attribute == column_name:
        declaration = f"    {column_name} = mapped_column(" + ", ".join(arguments) + ")"
    else:
        # Not a usable Python name: the attribute is renamed, the database column keeps its name.
        declaration = f"    {attribute} = mapped_column({json.dumps(column_name)}, " + ", ".join(arguments) + ")"

    if sqlalchemy_type.startswith("UnmappedMySQLType("):
        declaration += (
            f"  # UNSUPPORTED MySQL type {column['data_type']}; "
            "see migration_notes.md"
        )

    return declaration


def _table_constraints(table: dict) -> list[str]:
    """UniqueConstraint / Index / CheckConstraint expressions of a table."""

    constraints = []

    for unique in table.get("unique_constraints", []):
        columns = ", ".join(repr(c) for c in unique["columns"])
        name = f", name={unique['name']!r}" if unique.get("name") else ""
        constraints.append(f"UniqueConstraint({columns}{name})")

    for index in table.get("indexes", []):
        # MySQL names an unnamed index after its first column.
        name = index.get("name") or index["columns"][0]
        columns = ", ".join(repr(c) for c in index["columns"])
        constraints.append(f"Index({name!r}, {columns})")

    for check in table.get("check_constraints", []):
        name = f", name={check['name']!r}" if check.get("name") else ""
        constraints.append(f"CheckConstraint({check['expression']!r}{name})")

    return constraints


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------

def _unique_name(base: str, taken: set[str]) -> str:
    name = base
    counter = 2

    while name.lower() in taken:
        name = f"{base}_{counter}"
        counter += 1

    taken.add(name.lower())
    return name


def _build_relationships(
    schema: dict,
) -> dict[str, list[str]]:
    """
    Generate both sides of every foreign-key relationship between
    ORM-mapped tables.

    Child side (many-to-one): named after the FK column without "_id"
    (user_id -> user); if that clashes with a column or another
    relationship, "_ref" (and then a number) is appended.

    Parent side (one-to-many): named after the child table
    (users.appointments); when a child table has several FKs to the same
    parent, "<child_table>_via_<fk column>" keeps the names distinct.

    Several FKs to one table get explicit foreign_keys=...; a
    self-reference gets remote_side on its many-to-one side.
    """

    tables = schema.get("tables", [])
    mapped = {table["name"]: table for table in tables if has_primary_key(table)}

    relationships: dict[str, list[str]] = {table["name"]: [] for table in tables}
    attributes = {name: column_attributes(table) for name, table in mapped.items()}
    taken = {
        name: {column["name"].lower() for column in table.get("columns", [])}
        | {attribute.lower() for attribute in attributes[name].values()}
        | {reserved.lower() for reserved in ORM_RESERVED}
        for name, table in mapped.items()
    }

    for child_name, child in mapped.items():
        child_class = table_to_class_name(child_name)
        per_parent: dict[str, int] = {}

        for foreign_key in child.get("foreign_keys", []):
            per_parent[foreign_key["referenced_table"]] = (
                per_parent.get(foreign_key["referenced_table"], 0) + 1
            )

        for foreign_key in child.get("foreign_keys", []):
            parent_name = foreign_key["referenced_table"]

            if parent_name not in mapped:
                continue

            parent_class = table_to_class_name(parent_name)
            column = foreign_key["column"]
            self_reference = parent_name == child_name
            several = per_parent[parent_name] > 1

            base = column[:-3] if column.lower().endswith("_id") and len(column) > 3 else f"{column}_ref"
            if base.lower() in {c["name"].lower() for c in child.get("columns", [])}:
                base = f"{base}_ref"
            child_attribute = _unique_name(python_name(base), taken[child_name])

            parent_base = f"{child_name}_via_{column}" if several else child_name
            parent_attribute = _unique_name(python_name(parent_base), taken[parent_name])

            # Relationship arguments name the mapped attributes, which differ from the column only when renamed.
            fk_attribute = attributes[child_name].get(column, column)
            fk_spec = f'foreign_keys="[{child_class}.{fk_attribute}]"' if several or self_reference else None

            child_arguments = [f'"{parent_class}"', f'back_populates="{parent_attribute}"']
            parent_arguments = [f'"{child_class}"', f'back_populates="{child_attribute}"']

            if fk_spec:
                child_arguments.append(fk_spec)
                parent_arguments.append(fk_spec)

            if self_reference:
                referenced = foreign_key["referenced_column"]
                child_arguments.append(
                    f'remote_side="{parent_class}.{attributes[parent_name].get(referenced, referenced)}"'
                )

            relationships[child_name].append(
                f"    {child_attribute} = relationship({', '.join(child_arguments)})"
            )
            relationships[parent_name].append(
                f"    {parent_attribute} = relationship({', '.join(parent_arguments)})"
            )

    return relationships


# --------------------------------------------------------------------------
# Source generation
# --------------------------------------------------------------------------

HEADER = [
    '"""',
    "Generated SQLAlchemy ORM models.",
    "",
    "Generated by Component 3 - Data Layer Migration.",
    '"""',
    "",
    "from sqlalchemy import (",
    "    BigInteger,",
    "    Boolean,",
    "    CheckConstraint,",
    "    Column,",
    "    Date,",
    "    DateTime,",
    "    Enum,",
    "    Float,",
    "    ForeignKey,",
    "    Index,",
    "    Integer,",
    "    JSON,",
    "    LargeBinary,",
    "    Numeric,",
    "    SmallInteger,",
    "    String,",
    "    Table,",
    "    Text,",
    "    Time,",
    "    UniqueConstraint,",
    "    text,",
    ")",
    "from sqlalchemy.dialects import mysql",
    "from sqlalchemy.dialects.mysql import SET",
    "from sqlalchemy.types import UserDefinedType",
    "from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship",
    "",
    "",
    "class Base(DeclarativeBase):",
    "    pass",
    "",
    "",
    "class UnmappedMySQLType(UserDefinedType):",
    '    """MySQL type C3 could not map; DDL is emitted verbatim.',
    "",
    "    Values pass through without Python type conversion.",
    "    Each column using it is listed in migration_notes.md.",
    '    """',
    "",
    "    cache_ok = True",
    "",
    "    def __init__(self, mysql_type):",
    "        self.mysql_type = mysql_type",
    "",
    "    def get_col_spec(self, **kw):",
    "        return self.mysql_type",
    "",
    "",
]


def generate_models_source(
    schema: dict,
) -> str:
    """
    Generate complete SQLAlchemy ORM model
    source code from a recovered schema.
    """

    lines = list(HEADER)
    tables = schema.get("tables", [])
    relationships = _build_relationships(schema)
    blocks = []

    for table in tables:
        table_name = table["name"]
        block = []
        constraints = _table_constraints(table)

        if not has_primary_key(table):
            block.extend(
                [
                    "# No primary key in the schema: generated as a Core table,",
                    "# not ORM-mapped. See migration_notes.md.",
                    f"{python_name(f'{table_name}_table')} = Table(",
                    f'    "{table_name}",',
                    "    Base.metadata,",
                ]
            )
            block.extend(_build_column(table, column, core=True) for column in table.get("columns", []))
            block.extend(f"    {constraint}," for constraint in constraints)
            block.append(")")
            blocks.append(block)
            continue

        block.append(f"class {table_to_class_name(table_name)}(Base):")
        block.append(f'    __tablename__ = "{table_name}"')

        if constraints:
            block.append("    __table_args__ = (")
            block.extend(f"        {constraint}," for constraint in constraints)
            block.append("    )")

        block.append("")
        block.extend(_build_column(table, column) for column in table.get("columns", []))

        if relationships.get(table_name):
            block.append("")
            block.extend(relationships[table_name])

        blocks.append(block)

    for index, block in enumerate(blocks):
        lines.extend(block)
        if index < len(blocks) - 1:
            lines.extend(["", ""])

    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Migration notes
# --------------------------------------------------------------------------

def collect_unsupported_columns(
    schema: dict,
) -> list[dict]:
    """
    List columns whose MySQL type has no supported SQLAlchemy mapping.

    These columns are emitted as UnmappedMySQLType and must be reviewed.
    """

    unsupported = []

    for table in schema.get("tables", []):
        for column in table.get("columns", []):
            try:
                mysql_to_sqlalchemy(column.get("data_type", ""))
            except ValueError:
                unsupported.append(
                    {
                        "table": table.get("name"),
                        "column": column.get("name"),
                        "mysql_type": column.get("data_type"),
                        "emitted_type": "UnmappedMySQLType (DDL passthrough)",
                    }
                )

    return unsupported


MYSQL_ONLY_DEFAULT = re.compile(r"\w+\s*\(\s*\d*\s*\)", re.IGNORECASE)


def mysql_only_defaults(schema: dict) -> list[dict]:
    """
    Columns whose server default is MySQL-only DDL: DEFAULT ... ON UPDATE ...,
    or a function-call default such as current_timestamp() / now().
    """

    found = []

    for table in schema.get("tables", []):
        for column in table.get("columns", []):
            default = column.get("default") or ""
            if column.get("on_update") and default:
                ddl = f"DEFAULT {default} ON UPDATE {column['on_update']}"
            elif MYSQL_ONLY_DEFAULT.fullmatch(default.strip()):
                ddl = f"DEFAULT {default}"
            else:
                continue
            found.append({"table": table.get("name"), "column": column.get("name"), "ddl": ddl})

    return found


def build_migration_notes(
    schema: dict,
) -> str:
    """
    Document schema features that were not migrated automatically.
    """

    unsupported = collect_unsupported_columns(schema)

    lines = [
        "# Migration notes",
        "",
        "Generated by Component 3 - Data Layer Migration.",
        "Schema features listed here were not migrated automatically and",
        "need manual review. Nothing is dropped silently.",
        "",
        "## Unsupported column types",
        "",
    ]

    if unsupported:
        lines.append("| Table | Column | MySQL type | Emitted as |")
        lines.append("|---|---|---|---|")

        for item in unsupported:
            lines.append(
                f"| {item['table']} | {item['column']} | "
                f"`{item['mysql_type']}` | `{item['emitted_type']}` |"
            )
    else:
        lines.append("None. Every column type has a SQLAlchemy mapping.")

    lines.extend(["", "## Tables without a primary key", ""])

    keyless = [
        table.get("name")
        for table in schema.get("tables", [])
        if not has_primary_key(table)
    ]

    if keyless:
        lines.append(
            "Generated as SQLAlchemy Core `Table` objects (not ORM classes); "
            "no relationships are generated for them. A primary key was "
            "not invented."
        )
        lines.append("")
        lines.extend(f"- `{name}`" for name in keyless)
    else:
        lines.append("None.")

    lines.extend(["", "## Table features not migrated", ""])

    features = [
        (table.get("name"), feature)
        for table in schema.get("tables", [])
        for feature in table.get("unsupported_features", [])
    ]
    features += [
        (table.get("name"), f"ON UPDATE {column['on_update']} on {column['name']} without a DEFAULT (not emitted)")
        for table in schema.get("tables", [])
        for column in table.get("columns", [])
        if column.get("on_update") and not column.get("default")
    ]

    if features:
        lines.extend(f"- `{table}`: {feature}" for table, feature in features)
    else:
        lines.append("None.")

    lines.extend(["", "## MySQL-specific DDL (kept as-is)", ""])

    mysql_only = mysql_only_defaults(schema)

    if mysql_only:
        lines.append(
            "These server defaults are valid MySQL and are emitted unchanged. They do not "
            "run on other databases (e.g. SQLite), so a SQLite round trip is not applicable."
        )
        lines.append("")
        lines.extend(f"- `{item['table']}.{item['column']}`: `{item['ddl']}`" for item in mysql_only)
    else:
        lines.append("None.")

    lines.extend(["", "## Database objects not migrated", ""])

    objects = schema.get("unsupported_objects", [])

    if objects:
        lines.append("Found in the schema dump; not represented in models.py.")
        lines.append("")
        lines.extend(f"- {item}" for item in objects)
    else:
        lines.append("None (no views, triggers, routines or events in the dump).")

    lines.append("")

    return "\n".join(lines)


def generate_models(
    schema_file: str | Path,
    output_file: str | Path,
    notes_file: str | Path | None = None,
) -> None:
    """
    Load schema.json and generate models.py.

    When notes_file is given, migration_notes.md is written there too.
    """

    schema = load_schema(schema_file)

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_models_source(schema), encoding="utf-8")

    if notes_file is not None:
        Path(notes_file).write_text(
            build_migration_notes(schema),
            encoding="utf-8",
        )
