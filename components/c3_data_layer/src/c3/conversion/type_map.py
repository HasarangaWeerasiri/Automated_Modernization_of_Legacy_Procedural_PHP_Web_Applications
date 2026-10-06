"""
MySQL to SQLAlchemy Type Mapping
Component 3 - Data Layer Migration

Provides deterministic conversion from recovered
MySQL column types to SQLAlchemy type expressions.

Unsupported types are not guessed. Instead, a
ValueError is raised so the migration pipeline can
flag the type for manual review.
"""

import re


def mysql_to_sqlalchemy(mysql_type: str) -> str:
    """
    Convert a MySQL data type into a SQLAlchemy
    type expression.

    Examples:
        INT             -> Integer
        BIGINT          -> BigInteger
        VARCHAR(100)    -> String(100)
        CHAR(10)        -> String(10)
        DECIMAL(10, 2)  -> Numeric(10, 2)
        DATE            -> Date
        DATETIME        -> DateTime
    """

    if not mysql_type:
        raise ValueError(
            "MySQL type cannot be empty."
        )

    normalized = " ".join(
        mysql_type.strip().upper().split()
    )

    # Remove UNSIGNED because SQLAlchemy's
    # generic Integer type does not require it
    # for the initial portable model.
    normalized = re.sub(
        r"\s+UNSIGNED$",
        "",
        normalized,
    )

    # VARCHAR(n)
    match = re.fullmatch(
        r"VARCHAR\s*\(\s*(\d+)\s*\)",
        normalized,
    )

    if match:
        return f"String({match.group(1)})"

    # CHAR(n)
    match = re.fullmatch(
        r"CHAR\s*\(\s*(\d+)\s*\)",
        normalized,
    )

    if match:
        return f"String({match.group(1)})"

    # DECIMAL(p, s) / NUMERIC(p, s)
    match = re.fullmatch(
        r"(?:DECIMAL|NUMERIC)\s*"
        r"\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        normalized,
    )

    if match:
        precision = match.group(1)
        scale = match.group(2)

        return (
            f"Numeric({precision}, {scale})"
        )

    # TINYINT(1) is commonly used as a boolean.
    if re.fullmatch(
        r"TINYINT\s*\(\s*1\s*\)",
        normalized,
    ):
        return "Boolean"

    simple_types = {
        "INT": "Integer",
        "INTEGER": "Integer",
        "TINYINT": "Integer",
        "SMALLINT": "SmallInteger",
        "MEDIUMINT": "Integer",
        "BIGINT": "BigInteger",

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

        "BLOB": "LargeBinary",
        "TINYBLOB": "LargeBinary",
        "MEDIUMBLOB": "LargeBinary",
        "LONGBLOB": "LargeBinary",
    }

    if normalized in simple_types:
        return simple_types[normalized]

    raise ValueError(
        f"Unsupported MySQL type: {mysql_type}"
    )