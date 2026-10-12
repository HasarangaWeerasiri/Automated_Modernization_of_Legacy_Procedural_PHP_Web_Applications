"""Strict, fail-closed PHP source validation for Q011."""

import re
from pathlib import Path


Q011_PATTERN = re.compile(
    r"""
    \A
    <\?php
    \s*
    require_once\s+__DIR__\s*\.\s*'/db\.php'\s*;
    \s*
    (?:
        //[^\r\n]*
        \r?\n
        \s*
    )?
    \$ids\s*=\s*\$_GET\s*\[\s*'ids'\s*\]
    \s*\?\?\s*'1,2'\s*;
    \s*
    \$result\s*=\s*mysqli_query\s*\(
        \s*\$conn\s*,
        \s*"
        SELECT[ ]id,[ ]full_name[ ]FROM[ ]patients[ ]WHERE[ ]id[ ]IN[ ]
        \(\$ids\)
        "
    \s*\)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q011_source_shape(source_file: str | Path) -> bool:
    """Accept only the explicitly supported Q011 PHP structure."""

    try:
        source = Path(source_file).read_text(
            encoding="utf-8-sig"
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return Q011_PATTERN.fullmatch(source.strip()) is not None
