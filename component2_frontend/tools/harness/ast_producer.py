# What this file does: runs parse.php on every PHP file of an app.
"""AST producer of the Component 2 test harness: PHP files -> AST files in Member 01's format.

The other half of the harness (harness.py) builds timelines from an AST read through
src/mapping/loader.py, so it runs unchanged on these files and on Member 01's own.
tools/harness/parse.php does the parsing (nikic/php-parser, pinned in composer.lock); PHP is
parsed, never executed.

Usage, from component2_frontend/ (writes ast/<fileId>.json under the output folder):
    venv/Scripts/python.exe tools/harness/ast_producer.py <out folder> <fileId>=<php file> [...]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.mapping import loader  # noqa: E402
from src.model import Ast  # noqa: E402

PARSE_PHP = Path(__file__).with_name("parse.php")
VENDOR = Path(__file__).with_name("vendor")


@dataclass(frozen=True)
class SourceFile:
    source: Path  # the PHP file to parse
    file_id: str  # e.g. "f001"
    path: str  # the path recorded in the AST header


def find_php() -> str | None:
    """The PHP executable: PHP_BIN, then php on the PATH, then XAMPP's (None if there is none)."""
    for candidate in (os.environ.get("PHP_BIN"), shutil.which("php"), r"C:\xampp\php\php.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def unavailable() -> str | None:
    """Why the harness cannot run here, or None when it can."""
    if find_php() is None:
        return "PHP not found: set PHP_BIN or put php on PATH"
    if not (VENDOR / "autoload.php").exists():
        return "tools/harness/vendor is missing: run `composer install` in tools/harness"
    return None


def write_asts(files: list[SourceFile], folder: Path) -> tuple[list[Path], dict]:
    """Write <folder>/<fileId>.json for each file (one PHP process for all); returns the paths and parser facts."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / f"{f.file_id}.json" for f in files]
    with tempfile.TemporaryDirectory() as work:
        meta_path = Path(work) / "meta.json"
        manifest = {"meta": str(meta_path),
                    "files": [{"source": str(f.source), "out": str(out), "fileId": f.file_id, "path": f.path}
                              for f, out in zip(files, paths)]}
        (Path(work) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        subprocess.run([find_php(), str(PARSE_PHP), str(Path(work) / "manifest.json")], check=True,
                       capture_output=True)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return paths, meta


def parse_sources(files: list[SourceFile]) -> tuple[list[Ast], dict]:
    """Each file's AST, produced by the harness and read back through the loader, plus the parser facts."""
    with tempfile.TemporaryDirectory() as work:
        paths, meta = write_asts(files, Path(work))
        return [loader.load_ast(path) for path in paths], meta


def main(argv: list[str]) -> int:
    """Command-line entry: parse the given PHP files and write their ASTs."""
    reason = unavailable()
    if reason or len(argv) < 2:
        print(reason or __doc__)
        return 1
    files = []
    for item in argv[1:]:
        file_id, _, source = item.partition("=")
        files.append(SourceFile(Path(source), file_id, Path(source).as_posix()))
    paths, meta = write_asts(files, Path(argv[0]) / "ast")
    for path in paths:
        print(f"wrote {path} ({loader.load_ast(path).node_count} nodes, {meta['parser']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
