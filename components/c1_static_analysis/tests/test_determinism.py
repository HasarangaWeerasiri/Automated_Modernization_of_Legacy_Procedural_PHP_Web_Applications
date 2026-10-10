"""The core's output must be byte-identical for the same input.

Everything downstream (labels.json keys, endpoint map, C2's node ids) depends on this.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from conftest import requires_php


def _same_tree(a: Path, b: Path) -> list[str]:
    """Return a list of differences (empty means byte-identical)."""
    diffs: list[str] = []

    def walk(cmp: filecmp.dircmp, prefix: str = "") -> None:
        diffs.extend(f"only in one side: {prefix}{n}" for n in cmp.left_only + cmp.right_only)
        for name in cmp.common_files:
            if not filecmp.cmp(Path(cmp.left) / name, Path(cmp.right) / name, shallow=False):
                diffs.append(f"differs: {prefix}{name}")
        for sub, sub_cmp in cmp.subdirs.items():
            walk(sub_cmp, f"{prefix}{sub}/")

    walk(filecmp.dircmp(a, b))
    return diffs


@requires_php
def test_two_runs_are_byte_identical(fixture_tree, run_extract):
    first = run_extract(fixture_tree, "run1")
    second = run_extract(fixture_tree, "run2")
    assert _same_tree(first, second) == []


@requires_php
def test_output_does_not_depend_on_where_the_app_lives(fixture_tree, tmp_path, run_extract):
    """Same app copied to a different absolute path -> identical output (no absolute paths leak)."""
    elsewhere = tmp_path / "some" / "other" / "place" / "legacy_copy"
    shutil.copytree(fixture_tree, elsewhere)
    a = run_extract(fixture_tree, "here")
    b = run_extract(elsewhere, "there")
    assert _same_tree(a, b) == []


@requires_php
def test_no_absolute_paths_or_timestamps_in_output(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    root = str(fixture_tree)
    for f in out.rglob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert root not in text, f"absolute path leaked into {f.name}"
        assert str(out) not in text, f"output path leaked into {f.name}"


@requires_php
def test_file_ids_follow_sorted_path_order(fixture_tree, run_extract):
    import json

    out = run_extract(fixture_tree)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in manifest["files"]]
    assert paths == sorted(paths, key=lambda p: p.encode("utf-8"))
    assert [f["fileId"] for f in manifest["files"]] == [f"f{i:03d}" for i in range(len(paths))]
