"""Strict PHP source validation for Q013 dynamic table queries."""

import re
from pathlib import Path


Q013_PATTERN = re.compile(
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
    \$table\s*=\s*\$_GET\s*\[\s*'table'\s*\]
    \s*\?\?\s*'patients'\s*;
    \s*
    \$result\s*=\s*mysqli_query\s*\(
        \s*\$conn\s*,
        \s*"
        SELECT[ ]id[ ]FROM[ ]\$table[ ]WHERE[ ]id[ ]=[ ]1
        "
    \s*\)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q013_source_shape(source_file: str | Path) -> bool:
    """Accept only the validated Q013 PHP source structure."""

    try:
        source = Path(source_file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return Q013_PATTERN.fullmatch(source.strip()) is not None
