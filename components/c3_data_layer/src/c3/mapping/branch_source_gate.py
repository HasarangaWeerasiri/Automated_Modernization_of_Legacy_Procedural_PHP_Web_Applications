"""Strict, fail-closed PHP source-shape gate for Q014."""

import re
from pathlib import Path


SUPPORTED_Q014_PATTERN = re.compile(
    r"""
    \A
    <\?php
    \s*
    require_once\s+__DIR__\s*\.\s*['"]/db\.php['"]\s*;
    \s*
    (?:
        //\s*S14\ multiple\ assignments\ across\ branches:
        \s*conservative\ abstention[^\r\n]*
        \r?\n
        \s*
    )?
    \$activeOnly\s*=\s*isset\s*\(
        \s*\$_GET\s*\[\s*['"]active['"]\s*\]\s*
    \)\s*;
    \s*
    if\s*\(\s*\$activeOnly\s*\)\s*\{
    \s*
    \$sql\s*=\s*["']SELECT\s+id,\s*full_name\s+FROM\s+doctors\s+WHERE\s+active\s*=\s*1["']\s*;
    \s*
    \}\s*else\s*\{
    \s*
    \$sql\s*=\s*["']SELECT\s+id,\s*full_name\s+FROM\s+doctors["']\s*;
    \s*
    \}
    \s*
    \$result\s*=\s*mysqli_query\s*\(
        \s*\$conn\s*,\s*\$sql\s*
    \)\s*;
    \s*
    \Z
    """,
    re.VERBOSE,
)


def validate_q014_source_shape(source_file: str | Path) -> bool:
    """Accept only the explicitly supported Q014 PHP source shape."""

    try:
        source = Path(source_file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, TypeError, ValueError):
        return False

    return SUPPORTED_Q014_PATTERN.fullmatch(source.strip()) is not None
