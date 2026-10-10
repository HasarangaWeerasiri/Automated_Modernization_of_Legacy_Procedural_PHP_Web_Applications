from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from c1.extract import EXTRACTOR, extract_tree

FIXTURES = Path(__file__).parent / "fixtures" / "php"


def _php_ready() -> str | None:
    """None when the extractor can run, else the reason to skip."""
    if shutil.which("php") is None:
        return "php is not installed"
    if not (EXTRACTOR.parents[2] / "vendor" / "autoload.php").exists():
        return "run `composer install` in this folder first"
    proc = subprocess.run(["php", str(EXTRACTOR), "--version"], capture_output=True, check=False)
    return None if proc.returncode == 0 else "extractor cannot start: " + proc.stderr.decode("utf-8", "replace")


_REASON = _php_ready()
requires_php = pytest.mark.skipif(_REASON is not None, reason=_REASON or "")


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> Path:
    """A fresh copy of the committed fixtures plus byte-sensitive files made at test time.

    CRLF and Latin-1 files are written here, not committed, because git can rewrite
    line endings in committed files and silently weaken these tests.
    """
    root = tmp_path / "app"
    shutil.copytree(FIXTURES, root)
    (root / "crlf_page.php").write_bytes(b"<?php\r\n$a = 1;\r\n?>\r\n<p>line one\r\nline two</p>\r\n")
    (root / "latin1_page.php").write_bytes(b"<?php echo 'x'; ?>caf\xe9 <b>latin1</b>\n")
    return root


@pytest.fixture()
def run_extract(tmp_path: Path):
    def _run(src: Path, name: str = "out") -> Path:
        out = tmp_path / name
        extract_tree(src, out)
        return out
    return _run


def load_ast(out: Path, rel_path: str) -> dict:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    entry = next(f for f in manifest["files"] if f["path"] == rel_path)
    return json.loads((out / entry["ast"]).read_text(encoding="utf-8"))
