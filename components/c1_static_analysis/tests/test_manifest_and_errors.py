"""manifest.json contents, and the sound-or-abstain rule for broken files."""

from __future__ import annotations

import hashlib
import json

from conftest import load_ast, requires_php


@requires_php
def test_manifest_records_parser_and_file_hashes(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["schemaVersion"] == "1.0"
    assert m["parser"]["name"] == "nikic/php-parser"
    assert m["parser"]["version"] not in ("", "unknown")
    assert m["parser"]["targetPhpVersion"] == "8.3"
    for f in m["files"]:
        src = (fixture_tree / f["path"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(src).hexdigest()
        assert f["bytes"] == len(src)
        ast_bytes = (out / f["ast"]).read_bytes()
        assert f["astSha256"] == hashlib.sha256(ast_bytes).hexdigest()
    assert m["totals"]["files"] == len(m["files"])
    assert m["totals"]["nodes"] == sum(f["nodeCount"] for f in m["files"])


@requires_php
def test_content_hash_changes_when_a_source_file_changes(fixture_tree, run_extract):
    before = json.loads((run_extract(fixture_tree, "a") / "manifest.json").read_text(encoding="utf-8"))
    (fixture_tree / "guard_page.php").write_bytes(b"<?php\necho 'changed';\n")
    after = json.loads((run_extract(fixture_tree, "b") / "manifest.json").read_text(encoding="utf-8"))
    assert before["contentHash"] != after["contentHash"]


@requires_php
def test_syntax_error_is_reported_and_other_files_still_processed(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    broken = next(f for f in m["files"] if f["path"] == "broken.php")
    assert broken["parseOk"] is False
    assert broken["parseErrorCount"] >= 1
    assert m["totals"]["parseFailures"] == 1
    doc = load_ast(out, "broken.php")
    assert doc["parseOk"] is False
    assert doc["parseErrors"][0]["startLine"] is not None
    # the other files are unaffected
    assert load_ast(out, "list_page.php")["parseOk"] is True


@requires_php
def test_nested_folders_use_posix_relative_paths(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "includes/db.php" in [f["path"] for f in m["files"]]


@requires_php
def test_rerun_into_same_folder_leaves_no_stale_ast_files(fixture_tree, tmp_path):
    from c1.extract import extract_tree

    out = tmp_path / "same_out"
    extract_tree(fixture_tree, out)
    (fixture_tree / "crlf_page.php").unlink()
    extract_tree(fixture_tree, out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    on_disk = sorted(p.name for p in (out / "ast").glob("*.json"))
    assert on_disk == sorted(f["ast"].split("/")[1] for f in m["files"])


@requires_php
def test_excluded_directories_are_recorded_not_silently_skipped(fixture_tree, tmp_path):
    from c1.extract import extract_tree

    lib = fixture_tree / "TCPDF" / "fonts"
    lib.mkdir(parents=True)
    (lib / "a.php").write_bytes(b"<?php $x = [1,2,3];\n")
    (lib / "b.php").write_bytes(b"<?php $y = 2;\n")
    out = tmp_path / "ex"
    m = extract_tree(fixture_tree, out, exclude_dirs=(".git", "node_modules", "TCPDF"))
    assert m["excluded"] == [{"dir": "TCPDF", "files": 2}]
    assert not any(f["path"].startswith("TCPDF/") for f in m["files"])
    # and without the exclusion they are analysed
    m2 = extract_tree(fixture_tree, tmp_path / "ex2")
    assert m2["excluded"] == []
    assert any(f["path"].startswith("TCPDF/") for f in m2["files"])


@requires_php
def test_exclusion_changes_the_content_hash(fixture_tree, tmp_path):
    from c1.extract import extract_tree

    (fixture_tree / "lib").mkdir()
    (fixture_tree / "lib" / "x.php").write_bytes(b"<?php $x = 1;\n")
    a = extract_tree(fixture_tree, tmp_path / "a")
    b = extract_tree(fixture_tree, tmp_path / "b", exclude_dirs=(".git", "node_modules", "lib"))
    assert a["contentHash"] != b["contentHash"]
