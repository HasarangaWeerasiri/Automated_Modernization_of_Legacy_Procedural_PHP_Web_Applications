"""Strict source validation for Q016 conditional SQL concatenation."""

import re
from pathlib import Path


Q016_PATTERN = re.compile(
    r"""
    \A
    <\?php
    \s*
    require_once\s+__DIR__\s*\.\s*'/db\.php'\s*;
    \s*
    (?: //[^\r\n]* \r?\n \s* )?
    \$sql\s*=\s*
    "SELECT[ ]id,[ ]full_name[ ]FROM[ ]patients[ ]WHERE[ ]1=1"
    \s*;
    \s*
    if\s*\(\s*isset\s*\(
        \s*\$_GET\s*\[\s*'department_id'\s*\]\s*
    \)\s*\)\s*
    \{
        \s*
        \$sql\s*\.\=\s*
        "[ ]AND[ ]department_id[ ]=[ ]"
        \s*\.\s*
        \$_GET\s*\[\s*'department_id'\s*\]
        \s*;
        \s*
    \}
    \s*
    \$result\s*=\s*
    mysqli_query\s*\(\s*\$conn\s*,\s*\$sql\s*\)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q016_source_shape(source_file: str | Path) -> bool:
    """Allow only the supported Q016 conditional PHP structure."""

    try:
        source = Path(source_file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return Q016_PATTERN.fullmatch(source.strip()) is not None
