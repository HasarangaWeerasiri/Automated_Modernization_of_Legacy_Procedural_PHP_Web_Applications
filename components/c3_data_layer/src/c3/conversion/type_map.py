"""
MySQL to SQLAlchemy Type Mapping
Component 3 - Data Layer Migration

Provides deterministic conversion from recovered
MySQL column types to SQLAlchemy type expressions.

Unsupported types are not guessed. Instead, a
ValueError is raised; the model builder records the
column in migration_notes.md and keeps going.
"""

import re


TYPE_PATTERN = re.compile(
    r"([A-Za-z]+)\s*(?:\((.*)\))?((?:\s+(?:UNSIGNED|SIGNED|ZEROFILL))*)",
    re.IGNORECASE | re.DOTALL,
)

QUOTED_VALUE_PATTERN = re.compile(
    r"'((?:[^'\\]|''|\\.)*)'",
    re.DOTALL,
)

INTEGER_TYPES = {
    "INT": "Integer",
    "INTEGER": "Integer",
    "TINYINT": "Integer",
    "SMALLINT": "SmallInteger",
    "MEDIUMINT": "Integer",
    "BIGINT": "BigInteger",
}

SIMPLE_TYPES = {
    "FLOAT": "Float",
    "DOUBLE": "Float",
    "REAL": "Float",

    "BOOLEAN": "Boolean",
    "BOOL": "Boolean",

    "TEXT": "Text",
    "TINYTEXT": "Text",
    "MEDIUMTEXT": "Text",
    "LONGTEXT": "Text",

    "DATE": "Date",
    "DATETIME": "DateTime",
    "TIMESTAMP": "DateTime",
    "TIME": "Time",
    "YEAR": "Integer",

    "BLOB": "LargeBinary",
    "TINYBLOB": "LargeBinary",
    "MEDIUMBLOB": "LargeBinary",
    "LONGBLOB": "LargeBinary",

    "JSON": "JSON",
}

# Types whose parenthesised argument is only a display width or a
# fractional-seconds precision and does not change the mapping.
IGNORED_ARGUMENT_TYPES = {
    "FLOAT", "DOUBLE", "REAL", "DATETIME", "TIMESTAMP", "TIME", "YEAR",
}


def _enum_values(arguments: str, mysql_type: str) -> list[str]:
    """Parse the quoted value list of ENUM(...) / SET(...), preserving case."""

    values = []
    position = 0

    for match in QUOTED_VALUE_PATTERN.finditer(arguments):
        between = arguments[position:match.start()].strip()

        if between not in ("", ","):
            raise ValueError(f"Unsupported MySQL type: {mysql_type}")

        raw = match.group(1)
        values.append(raw.replace("''", "'").replace("\\'", "'"))
        position = match.end()

    if arguments[position:].strip() or not values:
        raise ValueError(f"Unsupported MySQL type: {mysql_type}")

    return values


# Generic types that would render as a DIFFERENT MySQL type are emitted as
# Generic().with_variant(<exact MySQL type>, 'mysql'): portable elsewhere,
# exact on MySQL (found by the E2 schema comparison, e.g. TIMESTAMP).
MYSQL_EXACT_TYPES = {
    "DOUBLE": "DOUBLE", "REAL": "DOUBLE", "TIMESTAMP": "TIMESTAMP", "YEAR": "YEAR",
    "TINYTEXT": "TINYTEXT", "MEDIUMTEXT": "MEDIUMTEXT", "LONGTEXT": "LONGTEXT",
    "TINYBLOB": "TINYBLOB", "MEDIUMBLOB": "MEDIUMBLOB", "LONGBLOB": "LONGBLOB",
}
FRACTIONAL_SECONDS_TYPES = {"DATETIME", "TIMESTAMP", "TIME"}


def _mysql_exact_type(base: str, numbers: list[str]) -> str | None:
    fsp = numbers[0] if base in FRACTIONAL_SECONDS_TYPES and numbers and numbers[0] != "0" else None
    if fsp:
        return f"mysql.{base}(fsp={fsp})"
    if base in MYSQL_EXACT_TYPES:
        return f"mysql.{MYSQL_EXACT_TYPES[base]}()"
    return None


def mysql_to_sqlalchemy(mysql_type: str, key: bool = False) -> str:
    """
    Convert a MySQL data type into a SQLAlchemy
    type expression.

    Examples:
        INT, INT(11)            -> Integer
        BIGINT(20) UNSIGNED     -> BigInteger
        VARCHAR(100)            -> String(100)
        CHAR(10)                -> String(10)
        DECIMAL(10, 2)          -> Numeric(10, 2)
        TINYINT(1)              -> Boolean (a flag; key=True keeps it an integer)
        ENUM('a','B')           -> Enum('a', 'B')
        SET('x','y')            -> SET('x', 'y')   (sqlalchemy.dialects.mysql)
        DATETIME(6)             -> DateTime
    """

    if not mysql_type or not mysql_type.strip():
        raise ValueError(
            "MySQL type cannot be empty."
        )

    match = TYPE_PATTERN.fullmatch(mysql_type.strip())

    if not match:
        raise ValueError(f"Unsupported MySQL type: {mysql_type}")

    base = match.group(1).upper()
    arguments = match.group(2)
    unsigned = "UNSIGNED" in (match.group(3) or "").upper()
    numbers = (
        [part.strip() for part in arguments.split(",")]
        if arguments is not None
        else []
    )

    if base in ("ENUM", "SET"):
        if arguments is None:
            raise ValueError(f"Unsupported MySQL type: {mysql_type}")

        values = ", ".join(repr(value) for value in _enum_values(arguments, mysql_type))
        return f"Enum({values})" if base == "ENUM" else f"SET({values})"

    if arguments is not None and not all(n.isdigit() for n in numbers):
        raise ValueError(f"Unsupported MySQL type: {mysql_type}")

    if base == "VARCHAR" and len(numbers) == 1:
        return f"String({numbers[0]})"

    if base == "CHAR" and len(numbers) <= 1:
        # Fixed-length CHAR stays CHAR on MySQL (String alone renders VARCHAR).
        length = numbers[0] if numbers else "1"
        return f"String({length}).with_variant(mysql.CHAR({length}), 'mysql')"

    if base in ("DECIMAL", "NUMERIC", "DEC", "FIXED") and len(numbers) <= 2:
        generic = f"Numeric({', '.join(numbers)})" if numbers else "Numeric()"
        if unsigned:
            mysql_type = f"mysql.DECIMAL({', '.join(numbers + ['unsigned=True'])})"
            return f"{generic}.with_variant({mysql_type}, 'mysql')"
        return generic if numbers else "Numeric"

    # TINYINT(1) is MySQL's usual flag column. A key column (primary key, auto-increment,
    # foreign key source or target) is an identifier, not a flag: it stays an integer.
    if base == "TINYINT" and numbers == ["1"] and not key:
        return "Boolean"

    if base == "BIT" and numbers in ([], ["1"]):
        return "Boolean"

    if base in INTEGER_TYPES and len(numbers) <= 1:
        if unsigned:
            # Keep UNSIGNED on MySQL (a generic Integer would allow negative
            # values and halve the range); stay portable on other backends.
            mysql_name = "INTEGER" if base in ("INT", "INTEGER") else base
            return (
                f"{INTEGER_TYPES[base]}().with_variant("
                f"mysql.{mysql_name}(unsigned=True), 'mysql')"
            )
        if base in ("TINYINT", "MEDIUMINT"):
            # No generic type of this size: keep the exact MySQL type (E2).
            return f"Integer().with_variant(mysql.{base}(), 'mysql')"
        return INTEGER_TYPES[base]


    if base in ("BINARY", "VARBINARY") and len(numbers) == 1:
        return f"LargeBinary({numbers[0]}).with_variant(mysql.{base}({numbers[0]}), 'mysql')"

    if base in SIMPLE_TYPES:
        if arguments is None or (base in IGNORED_ARGUMENT_TYPES and len(numbers) <= 2):
            exact = _mysql_exact_type(base, numbers)
            if exact:
                return f"{SIMPLE_TYPES[base]}().with_variant({exact}, 'mysql')"
            return SIMPLE_TYPES[base]

    raise ValueError(
        f"Unsupported MySQL type: {mysql_type}"
    )
