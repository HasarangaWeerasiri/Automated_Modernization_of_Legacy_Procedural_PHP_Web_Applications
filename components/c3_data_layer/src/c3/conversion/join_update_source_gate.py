"""Strict source-shape validation for Q020."""

import re
from pathlib import Path


Q020_PATTERN = re.compile(
    r"""
    \A
    <\?php
    \s*
    require_once\s+__DIR__\s*\.\s*'/db\.php'\s*;
    \s*
    (?: //[^\r\n]* \r?\n \s* )?
    \$id\s*=\s*\$_POST\s*\[\s*'id'\s*\]
    \s*\?\?\s*'1'\s*;
    \s*
    \$sql\s*=\s*"
    UPDATE[ ]appointments[ ]a[ ]JOIN[ ]doctors[ ]d
    [ ]ON[ ]d\.id[ ]=[ ]a\.doctor_id
    [ ]SET[ ]a\.fee[ ]=[ ]d\.hourly_rate
    [ ]WHERE[ ]a\.id[ ]=[ ]\$id
    "\s*;
    \s*
    \$result\s*=\s*mysqli_query
    \s*\(\s*\$conn\s*,\s*\$sql\s*\)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q020_source_shape(source_file: str | Path) -> bool:
    """Accept only the validated Q020 PHP source shape."""

    try:
        source = Path(source_file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return Q020_PATTERN.fullmatch(source.strip()) is not None
