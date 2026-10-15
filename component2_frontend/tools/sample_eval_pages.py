"""The pages of one evaluation app that the gold standard annotates (docs/gold-standard-guide.md section 2).

Lists the page-like `.php` files under <app_dir>, sorts their paths, and prints all of them if there are at
most 10, otherwise `random.Random(20261015).sample(sorted_paths, 10)`, one path per line, each prefixed by the
app folder's name. Commit the output as `gold/sample.txt` **before** annotating.

Page-like: a `.php` file none of whose folders is a library, an installer or an include folder
(EXCLUDED_DIRS, compared case-insensitively). The files those pages include are annotated with them (guide
section 2), not sampled.

Usage, from component2_frontend/ (only once the rules are frozen and only on an evaluation app's folder):
    venv/Scripts/python.exe tools/sample_eval_pages.py <app_dir>
"""

import random
import sys
from pathlib import Path

SEED = 20261015
SAMPLE_SIZE = 10
EXCLUDED_DIRS = frozenset((
    "vendor", "node_modules", "install", "installer", "lib", "libs", "library", "libraries", "include",
    "includes", "inc", "tcpdf", "fpdf", "phpmailer",
))


def page_like(app_dir: Path) -> list[str]:
    """Every page-like .php file under app_dir, as a sorted list of paths relative to it."""
    paths = []
    for path in app_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".php":
            relative = path.relative_to(app_dir)
            if not any(part.lower() in EXCLUDED_DIRS for part in relative.parts[:-1]):
                paths.append(relative.as_posix())
    return sorted(paths)


def sample(paths: list[str]) -> list[str]:
    """All of them if there are at most 10; otherwise the seeded random 10, in the order the sample gives."""
    paths = sorted(paths)
    return paths if len(paths) <= SAMPLE_SIZE else random.Random(SEED).sample(paths, SAMPLE_SIZE)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    app_dir = Path(argv[0])
    paths = page_like(app_dir)
    chosen = sample(paths)
    print(f"{len(paths)} page-like files, {len(chosen)} sampled (seed {SEED})", file=sys.stderr)
    for path in chosen:
        print(f"{app_dir.name}/{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
