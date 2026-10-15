"""The pages a second annotator labels for the agreement check (docs/gold-standard-guide.md section 6).

Reads the sampled pages (`gold/sample.txt`: one `<app>/<path>` per line; blank lines and `#` comments are
ignored), sorts them, and prints 20 % of them, rounded up, chosen by `random.Random(20261016).sample`. The
second annotator labels these pages without seeing the first annotator's labels.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/sample_agreement.py gold/sample.txt
"""

import math
import random
import sys
from pathlib import Path

SEED = 20261016
SHARE = 0.20


def pages(text: str) -> list[str]:
    lines = (line.strip() for line in text.splitlines())
    return sorted({line for line in lines if line and not line.startswith("#")})


def sample(sampled: list[str]) -> list[str]:
    """20 % of the sampled pages, rounded up (at least one page when there are any)."""
    sampled = sorted(sampled)
    return random.Random(SEED).sample(sampled, math.ceil(SHARE * len(sampled))) if sampled else []


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    sampled = pages(Path(argv[0]).read_text(encoding="utf-8"))
    chosen = sample(sampled)
    print(f"{len(chosen)} of {len(sampled)} sampled pages (seed {SEED})", file=sys.stderr)
    for page in chosen:
        print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
