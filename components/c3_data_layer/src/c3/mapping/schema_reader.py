"""
Schema Reader
Component 3 - Data Layer Migration

Reads a MySQL schema dump (CREATE TABLE statements, plus the ALTER TABLE
statements that phpMyAdmin/mysqldump use to add keys afterwards) and
converts it into the Component 3 intermediate representation (IR).

Recovered: columns (type, nullability, default, ON UPDATE, auto-increment,
inline UNIQUE), primary keys (incl. composite), foreign keys (name,
ON DELETE, ON UPDATE), unique constraints, indexes and CHECK constraints.

Recognised but not migrated (recorded, never silently dropped): views,
triggers, routines, events, partitioning, generated columns,
FULLTEXT/SPATIAL indexes and composite foreign keys.
"""

import json
import re
from dataclasses import asdict
from pathlib import Path

from c3.ir import Column, DatabaseSchema, ForeignKey, Table


IDENTIFIER = r"`?([A-Za-z_][A-Za-z0-9_$]*)`?"

CREATE_TABLE_START = re.compile(
    r"CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:`?[A-Za-z_][A-Za-z0-9_$]*`?\.)?" + IDENTIFIER + r"\s*\(",
    re.IGNORECASE,
)

FOREIGN_KEY_PATTERN = re.compile(
    r"FOREIGN\s+KEY\s*(?:" + IDENTIFIER + r"\s*)?\(([^)]*)\)"
    r"\s*REFERENCES\s+" + IDENTIFIER + r"\s*\(([^)]*)\)"
    r"((?:\s+ON\s+(?:DELETE|UPDATE)\s+"
    r"(?:CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION))*)",
    re.IGNORECASE,
)

CONSTRAINT_NAME = re.compile(r"^CONSTRAINT\s+" + IDENTIFIER + r"\s+", re.IGNORECASE)

UNSUPPORTED_OBJECTS = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?"
    r"(VIEW|TRIGGER|PROCEDURE|FUNCTION|EVENT)\s+" + IDENTIFIER,
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

def _scan_to_closing_paren(text: str, start: int) -> int:
    """Index of the ')' matching the '(' just before start; quotes respected."""

    depth = 1
    quote = None
    index = start

    while index < len(text):
        character = text[index]

        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in ("'", '"', "`"):
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index

        index += 1

    return -1


def split_table_definitions(body: str) -> list[str]:
    """
    Split CREATE TABLE contents using top-level commas.

    Commas inside parentheses (DECIMAL(10, 2), ENUM('a','b')) or quotes
    do not split.
    """

    definitions = []
    current = []
    depth = 0
    quote = None

    for character in body:
        if quote:
            if character == quote:
                quote = None
            current.append(character)
            continue

        if character in ("'", '"', "`"):
            quote = character
        elif character == "(":
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


def _key_columns(raw: str) -> list[str]:
    """`a`, b(10) DESC -> ['a', 'b'] (prefix lengths and order dropped)."""

    columns = []

    for part in split_table_definitions(raw):
        match = re.match(r"\s*`?([A-Za-z_][A-Za-z0-9_$]*)`?", part)
        if match:
            columns.append(match.group(1))

    return columns


def _referential_actions(text: str) -> tuple[str | None, str | None]:
    on_delete = re.search(
        r"ON\s+DELETE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)",
        text, re.IGNORECASE,
    )
    on_update = re.search(
        r"ON\s+UPDATE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)",
        text, re.IGNORECASE,
    )

    def norm(match):
        return " ".join(match.group(1).upper().split()) if match else None

    return norm(on_delete), norm(on_update)


# --------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------

def parse_column(definition: str) -> Column | None:
    """
    Parse one MySQL column definition.

    Example:

        id INT AUTO_INCREMENT PRIMARY KEY

    becomes name=id, data_type=INT, primary_key=True,
    auto_increment=True, nullable=False.
    """

    stripped = definition.strip()
    upper = stripped.upper()

    # These are table constraints, not column definitions.
    constraint_prefixes = (
        "PRIMARY KEY",
        "FOREIGN KEY",
        "CONSTRAINT",
        "UNIQUE",
        "KEY ",
        "KEY`",
        "INDEX ",
        "INDEX`",
        "FULLTEXT",
        "SPATIAL",
        "CHECK",
    )

    if upper.startswith(constraint_prefixes):
        return None

    match = re.match(
        r"`?([A-Za-z_][A-Za-z0-9_$]*)`?\s+"
        r"([A-Za-z]+(?:\s*\((?:[^()']|'(?:[^'\\]|''|\\.)*')*\))?"
        r"(?:\s+(?:UNSIGNED|SIGNED|ZEROFILL))*)"
        r"(.*)$",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return None

    name = match.group(1)

    # Upper-case the type keywords but keep the parenthesised arguments
    # verbatim: ENUM/SET values are case-sensitive data.
    type_match = re.fullmatch(
        r"([A-Za-z]+)\s*(\(.*\))?(.*)",
        match.group(2).strip(),
        re.DOTALL,
    )

    data_type = type_match.group(1).upper() + (type_match.group(2) or "")
    modifiers = " ".join(type_match.group(3).split()).upper()

    if modifiers:
        data_type += " " + modifiers

    options = match.group(3)
    # Option keywords are matched outside quoted literals (DEFAULT 'NOT NULL').
    options_unquoted = re.sub(r"'(?:[^'\\]|''|\\.)*'", "''", options)

    primary_key = bool(re.search(r"\bPRIMARY\s+KEY\b", options_unquoted, re.IGNORECASE))
    auto_increment = bool(re.search(r"\bAUTO_INCREMENT\b", options_unquoted, re.IGNORECASE))
    nullable = not bool(re.search(r"\bNOT\s+NULL\b", options_unquoted, re.IGNORECASE))
    unique = bool(re.search(r"\bUNIQUE\b", options_unquoted, re.IGNORECASE))

    # A primary key is semantically non-null.
    if primary_key:
        nullable = False

    default = None

    default_match = re.search(
        r"\bDEFAULT\s+"
        r"((?:'(?:[^'\\]|''|\\.)*')|(?:\"[^\"]*\")|(?:\([^)]*\))|"
        r"(?:[A-Za-z_]+\(\s*\d*\s*\))|(?:[^\s,]+))",
        options,
        re.IGNORECASE,
    )

    if default_match:
        default = default_match.group(1)

    on_update_match = re.search(
        r"\bON\s+UPDATE\s+(CURRENT_TIMESTAMP(?:\s*\(\s*\d*\s*\))?|NOW\(\s*\d*\s*\))",
        options_unquoted,
        re.IGNORECASE,
    )

    return Column(
        name=name,
        data_type=data_type,
        nullable=nullable,
        primary_key=primary_key,
        auto_increment=auto_increment,
        default=default,
        unique=unique and not primary_key,
        on_update=on_update_match.group(1).upper() if on_update_match else None,
    )


# --------------------------------------------------------------------------
# Table-level definitions (shared by CREATE TABLE and ALTER TABLE ADD ...)
# --------------------------------------------------------------------------

def _find_column(table: Table, name: str) -> Column | None:
    # MySQL column names are case-insensitive.
    for column in table.columns:
        if column.name.lower() == name.lower():
            return column

    return None


def _mark_primary_key(table: Table, columns: list[str]) -> None:
    for name in columns:
        column = _find_column(table, name)

        if column is not None:
            column.primary_key = True
            column.nullable = False
            column.unique = False


def _apply_table_definition(table: Table, definition: str) -> bool:
    """
    Apply a table-level definition (key, constraint, index).

    Returns True when the definition was a table-level construct
    (handled or recorded as unsupported).
    """

    text = definition.strip()
    name_match = CONSTRAINT_NAME.match(text)
    constraint_name = name_match.group(1) if name_match else None
    body = text[name_match.end():] if name_match else text

    primary_key = re.match(r"PRIMARY\s+KEY\s*(?:USING\s+\w+\s*)?\((.*)\)", body, re.IGNORECASE | re.DOTALL)
    if primary_key:
        _mark_primary_key(table, _key_columns(primary_key.group(1)))
        return True

    foreign_key = FOREIGN_KEY_PATTERN.match(body)
    if foreign_key:
        columns = _key_columns(foreign_key.group(2))
        referenced = _key_columns(foreign_key.group(4))
        on_delete, on_update = _referential_actions(foreign_key.group(5) or "")
        name = constraint_name or foreign_key.group(1)

        if len(columns) != 1 or len(referenced) != 1:
            table.unsupported_features.append(
                f"composite foreign key {name or ''} ({', '.join(columns)}) -> "
                f"{foreign_key.group(3)} ({', '.join(referenced)})".replace("key  (", "key (")
            )
        else:
            table.foreign_keys.append(
                ForeignKey(
                    column=columns[0],
                    referenced_table=foreign_key.group(3),
                    referenced_column=referenced[0],
                    name=name,
                    on_delete=on_delete,
                    on_update=on_update,
                )
            )
        return True

    unique = re.match(
        r"UNIQUE\s*(?:KEY|INDEX)?\s*(?:" + IDENTIFIER + r"\s*)?(?:USING\s+\w+\s*)?\((.*)\)",
        body, re.IGNORECASE | re.DOTALL,
    )
    if unique:
        table.unique_constraints.append(
            {"name": constraint_name or unique.group(1), "columns": _key_columns(unique.group(2))}
        )
        return True

    check = re.match(r"CHECK\s*\((.*)\)\s*(?:(?:NOT\s+)?ENFORCED)?\s*$", body, re.IGNORECASE | re.DOTALL)
    if check:
        table.check_constraints.append(
            {"name": constraint_name, "expression": " ".join(check.group(1).split())}
        )
        return True

    special = re.match(r"(FULLTEXT|SPATIAL)\s*(?:KEY|INDEX)?\s*(?:" + IDENTIFIER + r"\s*)?\((.*)\)", body, re.IGNORECASE | re.DOTALL)
    if special:
        table.unsupported_features.append(
            f"{special.group(1).upper()} index {special.group(2) or ''} "
            f"({', '.join(_key_columns(special.group(3)))})".replace("index  (", "index (")
        )
        return True

    index = re.match(
        r"(?:KEY|INDEX)\s*(?:" + IDENTIFIER + r"\s*)?(?:USING\s+\w+\s*)?\((.*)\)",
        body, re.IGNORECASE | re.DOTALL,
    )
    if index:
        table.indexes.append({"name": index.group(1), "columns": _key_columns(index.group(2))})
        return True

    return False


def parse_create_table(
    table_name: str,
    body: str,
    options: str = "",
) -> Table:
    """
    Convert one CREATE TABLE statement into a Table IR object.
    """

    table = Table(name=table_name)
    deferred = []

    for definition in split_table_definitions(body):
        column = parse_column(definition)

        if column is not None:
            table.columns.append(column)

            if re.search(r"\bGENERATED\s+ALWAYS\b|\bAS\s*\(.*\)\s*(?:VIRTUAL|STORED)\b", definition, re.IGNORECASE):
                table.unsupported_features.append(
                    f"generated column {column.name} (expression not migrated)"
                )

            inline_check = re.search(r"\bCHECK\s*\((.*)\)", definition, re.IGNORECASE | re.DOTALL)
            if inline_check:
                table.check_constraints.append(
                    {"name": None, "expression": " ".join(inline_check.group(1).split())}
                )
            continue

        deferred.append(definition)

    # Table-level keys may refer to any column, so apply them afterwards.
    for definition in deferred:
        _apply_table_definition(table, definition)

    if re.search(r"\bPARTITION\s+BY\b", options, re.IGNORECASE):
        table.unsupported_features.append("table partitioning (PARTITION BY)")

    return table


# --------------------------------------------------------------------------
# ALTER TABLE
# --------------------------------------------------------------------------

ALTER_TABLE_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?" + IDENTIFIER + r"\s+(.*?);",
    re.IGNORECASE | re.DOTALL,
)


def _find_table(tables: list[Table], name: str) -> Table | None:
    for table in tables:
        if table.name == name:
            return table

    for table in tables:
        if table.name.lower() == name.lower():
            return table

    return None


def apply_alter_table_statements(
    sql_text: str,
    tables: list[Table],
) -> None:
    """
    Apply key and column changes made by ALTER TABLE statements.

    phpMyAdmin/mysqldump-style dumps often create tables without keys
    and add them afterwards:

        ALTER TABLE `t` ADD PRIMARY KEY (`id`), ADD UNIQUE KEY `u` (`email`);
        ALTER TABLE `t` MODIFY `id` int(11) NOT NULL AUTO_INCREMENT,
          AUTO_INCREMENT=14;
        ALTER TABLE `c` ADD CONSTRAINT `fk` FOREIGN KEY (`p_id`)
          REFERENCES `p` (`id`) ON DELETE CASCADE;

    Supported clauses: ADD [CONSTRAINT] PRIMARY KEY / UNIQUE / KEY / INDEX /
    FOREIGN KEY / CHECK, MODIFY [COLUMN], CHANGE [COLUMN]. Table options
    (AUTO_INCREMENT=n etc.) do not change the IR.
    """

    for match in ALTER_TABLE_PATTERN.finditer(sql_text):
        table = _find_table(tables, match.group(1))

        if table is None:
            continue

        for clause in split_table_definitions(match.group(2)):
            _apply_alter_clause(table, clause.strip())


def _apply_alter_clause(table: Table, clause: str) -> None:
    add = re.match(r"ADD\s+(?:COLUMN\s+)?(.*)", clause, re.IGNORECASE | re.DOTALL)

    if add and _apply_table_definition(table, add.group(1)):
        return

    modify = re.match(
        r"(?:MODIFY|CHANGE)\s+(?:COLUMN\s+)?(.*)",
        clause,
        re.IGNORECASE | re.DOTALL,
    )

    if not modify:
        return

    definition = modify.group(1)

    if re.match(r"CHANGE\b", clause, re.IGNORECASE):
        # CHANGE old_name new_name definition
        old_name, _, definition = definition.partition(" ")
        target = _find_column(table, old_name.strip().strip("`"))
    else:
        target = None

    new_column = parse_column(definition)

    if new_column is None:
        return

    if target is None:
        target = _find_column(table, new_column.name)

    if target is None:
        return

    target.name = new_column.name
    target.data_type = new_column.data_type
    target.auto_increment = new_column.auto_increment
    target.default = new_column.default
    target.on_update = new_column.on_update
    target.nullable = False if target.primary_key else new_column.nullable

    if new_column.primary_key:
        target.primary_key = True
        target.nullable = False


# --------------------------------------------------------------------------
# Whole schema
# --------------------------------------------------------------------------

def _strip_comments(sql_text: str) -> str:
    """Remove -- and /* */ comments outside quoted literals (keep /*! */ hints out too)."""

    result = []
    index = 0
    quote = None

    while index < len(sql_text):
        character = sql_text[index]

        if quote:
            result.append(character)
            if character == "\\" and index + 1 < len(sql_text):
                result.append(sql_text[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue

        if character in ("'", '"', "`"):
            quote = character
            result.append(character)
            index += 1
            continue

        if sql_text.startswith("--", index) and (index + 2 >= len(sql_text) or sql_text[index + 2] in " \t\r\n"):
            end = sql_text.find("\n", index)
            index = len(sql_text) if end == -1 else end
            continue

        if character == "#":
            end = sql_text.find("\n", index)
            index = len(sql_text) if end == -1 else end
            continue

        if sql_text.startswith("/*", index):
            end = sql_text.find("*/", index + 2)
            index = len(sql_text) if end == -1 else end + 2
            continue

        result.append(character)
        index += 1

    return "".join(result)


def read_schema(
    schema_file: str | Path,
) -> DatabaseSchema:
    """
    Read a MySQL .sql schema file.

    Returns:
        DatabaseSchema containing all recovered tables, columns, keys,
        constraints and the unsupported objects found in the dump.
    """

    schema_path = Path(schema_file)

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file does not exist: {schema_path}"
        )

    sql_text = _strip_comments(
        schema_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    )

    tables: list[Table] = []

    for match in CREATE_TABLE_START.finditer(sql_text):
        close = _scan_to_closing_paren(sql_text, match.end())

        if close == -1:
            continue

        end_of_statement = sql_text.find(";", close)
        options = sql_text[close + 1:end_of_statement if end_of_statement != -1 else len(sql_text)]

        tables.append(
            parse_create_table(
                table_name=match.group(1),
                body=sql_text[match.end():close],
                options=options,
            )
        )

    apply_alter_table_statements(sql_text, tables)

    unsupported_objects = [
        f"{match.group(1).upper()} {match.group(2)}"
        for match in UNSUPPORTED_OBJECTS.finditer(sql_text)
    ]

    return DatabaseSchema(
        tables=tables,
        unsupported_objects=unsupported_objects,
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
