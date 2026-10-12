"""Strict source-shape validator for the supported Q012 PHP query."""

import re
from pathlib import Path


Q012_PATTERN = re.compile(
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
    \$sort\s*=\s*\$_GET\s*\[\s*'sort'\s*\]
    \s*\?\?\s*'full_name'\s*;
    \s*
    \$result\s*=\s*mysqli_query\s*\(
        \s*\$conn\s*,
        \s*"
        SELECT[ ]id,[ ]full_name[ ]FROM[ ]patients[ ]ORDER[ ]BY[ ]
        \$sort
        "
    \s*\)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q012_source_shape(source_file: str | Path) -> bool:
    """Accept only the explicitly supported Q012 PHP structure."""

    try:
        source = Path(source_file).read_text(
            encoding="utf-8-sig"
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return Q012_PATTERN.fullmatch(source.strip()) is not None
