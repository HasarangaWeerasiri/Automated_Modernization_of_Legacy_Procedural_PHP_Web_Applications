"""
Crude exploratory script: find `echo`/`print` statements in a raw PHP file
using plain string matching (no AST).

Deliberately naive. It will misfire on inline HTML, multi-line statements,
echo inside strings/comments, and short-echo tags (`<?= ... ?>`) -- that's
the point: it demonstrates why real AST parsing (Component 2's actual
input) is necessary instead of text scanning.
"""

import sys
from pathlib import Path


def find_echo_lines(path: Path) -> None:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if "echo" in stripped or "print" in stripped:
            print(f"{lineno:>5}: {stripped}")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <path-to-php-file>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    find_echo_lines(path)


if __name__ == "__main__":
    main()
