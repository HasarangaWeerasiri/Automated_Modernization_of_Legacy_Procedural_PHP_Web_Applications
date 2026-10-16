"""make_handoff.py: php_site is always the path relative to the analysed app root.

Synthetic app with a file in a subdirectory. Each way of passing --php must give
the same labels; a bare file name must never be written.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from c3.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "make_handoff.py"
EXPECTED = {"page.php:2", "include/header.php:2"}


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    base = tmp_path_factory.mktemp("handoff_labels")
    php = base / "app"
    (php / "include").mkdir(parents=True)
    (php / "page.php").write_text('<?php\nmysqli_query($c, "SELECT id FROM t");\n', encoding="utf-8")
    (php / "include" / "header.php").write_text("<?php\n$q = mysqli_query($c, \"SELECT id FROM t WHERE id = '$id'\");\n", encoding="utf-8")
    (base / "schema.sql").write_text("CREATE TABLE t (id INT PRIMARY KEY);", encoding="utf-8")
    out = base / "out"
    run_pipeline(php, base / "schema.sql", out)
    return base, php, out


def handoff(base, out, php_argument, tmp_path):
    target = tmp_path / "out"
    shutil.copytree(out, target)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "components" / "c3_data_layer" / "src"))
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(target), "--php", php_argument, "--schema", str(base / "schema.sql"),
         "--title", "T", "--name", "T"],
        capture_output=True, text=True, cwd=base, env=env,
    )
    return completed, target


@pytest.mark.parametrize("form", ["relative", "absolute", "trailing_slash", "windows_separators", "dot_segments",
                                  "subdirectory", "other_copy"])
def test_php_site_is_relative_to_the_app_root(app, tmp_path, form):
    base, php, out = app
    other = base / "copy_of_app"
    if not other.exists():
        shutil.copytree(php, other)
    argument = {
        "relative": "app",
        "absolute": str(php),
        "trailing_slash": "app/",
        "windows_separators": str(php).replace("/", "\\"),
        "dot_segments": "./out/../app",
        "subdirectory": str(php / "include"),
        "other_copy": str(other),
    }[form]

    completed, target = handoff(base, out, argument, tmp_path)

    assert completed.returncode == 0, completed.stderr
    labels = {entry["php_site"] for entry in json.loads((target / "signatures.json").read_text(encoding="utf-8")).values()}
    assert labels == EXPECTED


def test_no_bare_file_name_without_a_pipeline_label(app, tmp_path):
    base, php, out = app
    stripped = tmp_path / "stripped"
    shutil.copytree(out, stripped)
    data_access = stripped / "data_access.py"
    data_access.write_text(
        "\n".join(line for line in data_access.read_text(encoding="utf-8").splitlines() if "Migrated from" not in line) + "\n",
        encoding="utf-8",
    )

    completed, _ = handoff(base, stripped, str(php / "include"), tmp_path / "second")

    assert completed.returncode != 0
    assert "is not under --php" in (completed.stdout + completed.stderr)
