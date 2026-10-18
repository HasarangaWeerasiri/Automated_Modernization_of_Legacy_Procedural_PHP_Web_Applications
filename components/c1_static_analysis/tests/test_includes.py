"""Include resolution (src/c1/includes.py).

Rules: PHP manual `include` page (path order) and Hills, Klint & Vinju ASE 2014
(fold the path, match fragments against real files, accept a unique match).
"""

from __future__ import annotations

from c1.astdoc import AstDoc
from c1.cfg import build_project_cfgs
from c1.includes import build_includes
from conftest import php_ast


def project(files: dict[str, str], excluded: list[str] | None = None):
    paths = sorted(files, key=lambda p: p.encode("utf-8"))
    asts = [AstDoc(php_ast(files[p], f"f{i:03d}", p)) for i, p in enumerate(paths)]
    manifest = {"files": [{"path": p, "fileId": f"f{i:03d}"} for i, p in enumerate(paths)],
                "excluded": [{"dir": d, "files": 1} for d in (excluded or [])], "contentHash": "test"}
    cfgs, _ = build_project_cfgs(asts)
    return build_includes(asts, cfgs, manifest)


def site(r, path):
    return [s for s in r["sites"] if s["path"] == path]


def closure(r, page):
    e = next(v for v in r["entries"].values() if v["path"] == page)
    by_id = {s["fileId"]: s["path"] for s in r["sites"]}
    return e


def test_literal_in_same_directory_and_block_link():
    r = project({"index.php": "<?php\ninclude 'header.php';\necho 1;\n", "header.php": "<?php echo 'h';\n"})
    s = site(r, "index.php")[0]
    assert s["self"]["status"] == "resolved" and s["self"]["target"] == "header.php"
    assert s["type"] == "include" and s["unit"] == "f001#00000" and s["block"]
    assert r["includedBy"] == {"f000": ["f001"]}
    assert list(r["entries"]) == ["f001"]  # header.php is included, so not a page


def test_dot_dot_is_relative_to_the_working_directory():
    # PHP manual: paths starting with ../ ignore include_path, relative to the current directory
    r = project({"users/login.php": "<?php require_once('../include/db.php');\n", "include/db.php": "<?php\n"})
    s = site(r, "users/login.php")[0]
    assert s["self"] == {"status": "resolved", "basis": "cwd-relative", "target": "include/db.php",
                         "targetFileId": "f000"}
    assert s["type"] == "require_once"


def test_plain_name_falls_back_to_the_calling_scripts_directory():
    # PHP manual: include_path ('.' = cwd) first, then the calling script's own directory.
    # WackoPicko: users/x.php -> ../include/users.php -> "ourdb.php" (lives in include/)
    files = {
        "users/x.php": "<?php include '../include/users.php';\n",
        "include/users.php": "<?php include 'ourdb.php';\n",
        "include/ourdb.php": "<?php\n",
    }
    r = project(files)
    e = next(v for v in r["entries"].values() if v["path"] == "users/x.php")
    assert e["cwd"] == "users"
    assert [x["to"] for x in e["edges"]] == ["include/users.php", "include/ourdb.php"]
    assert e["problems"] == []


def test_working_directory_wins_over_caller_directory():
    files = {
        "users/x.php": "<?php include '../include/users.php';\n",
        "include/users.php": "<?php include 'db.php';\n",
        "include/db.php": "<?php\n",
        "users/db.php": "<?php\n",
    }
    r = project(files)
    e = next(v for v in r["entries"].values() if v["path"] == "users/x.php")
    assert e["edges"][1]["to"] == "users/db.php"


def test_dir_constant_and_dirname_file():
    files = {
        "a/page.php": "<?php\nrequire __DIR__ . '/../lib/x.php';\ninclude dirname(__FILE__) . '/y.php';\n",
        "lib/x.php": "<?php\n", "a/y.php": "<?php\n",
    }
    r = project(files)
    got = [(s["self"]["status"], s["self"].get("target"), s["self"]["basis"]) for s in site(r, "a/page.php")]
    assert got == [("resolved", "lib/x.php", "absolute"), ("resolved", "a/y.php", "absolute")]


def test_define_constant_is_folded():
    files = {
        "config.php": "<?php define('ROOT', __DIR__);\n",
        "index.php": "<?php include 'config.php';\ninclude ROOT . '/lib/x.php';\n",
        "lib/x.php": "<?php\n",
    }
    r = project(files)
    assert r["constants"] == {"ROOT": "/"}
    s = site(r, "index.php")[1]
    assert s["self"]["target"] == "lib/x.php" and "const:ROOT" in s["notes"]


def test_unknown_prefix_matches_a_unique_file():
    # Hills et al.: match the literal fragments against the file system, accept one match
    files = {"lib/render.php": "<?php function r($base) { include $base . '/header.php'; }\n",
             "tpl/header.php": "<?php\n"}
    r = project(files)
    s = site(r, "lib/render.php")[0]
    assert s["self"]["status"] == "resolved" and s["self"]["basis"] == "pattern-unique"
    assert s["self"]["pattern"] == "*/header.php"


def test_unknown_prefix_with_two_matches_is_ambiguous():
    files = {"lib/render.php": "<?php function r($base) { include $base . '/header.php'; }\n",
             "tpl/header.php": "<?php\n", "admin/header.php": "<?php\n"}
    s = site(project(files), "lib/render.php")[0]
    assert s["self"]["status"] == "ambiguous"
    assert s["self"]["candidates"] == ["admin/header.php", "tpl/header.php"]


def test_request_input_in_path_is_dynamic_and_flagged():
    # WackoPicko admin/index.php: $page = $_GET['page'] . '.php'; require_once($page);
    files = {"admin/index.php": "<?php\n$page = $_GET['page'] . '.php';\nrequire_once($page);\n",
             "admin/login.php": "<?php\n"}
    s = site(project(files), "admin/index.php")[0]
    assert s["self"]["status"] == "dynamic" and s["self"]["basis"] == "user-input-in-path"


def test_single_assignment_variable_is_folded_twice_assigned_is_not():
    files = {"p.php": "<?php\n$f = 'a.php';\ninclude $f;\n$g = 'a.php';\n$g = 'b.php';\ninclude $g;\n",
             "a.php": "<?php\n", "b.php": "<?php\n"}
    s1, s2 = site(project(files), "p.php")
    assert s1["self"]["status"] == "resolved" and "single-assignment:$f" in s1["notes"]
    assert s2["self"]["status"] == "dynamic"


def test_excluded_missing_case_and_escaping_root():
    files = {"p.php": "<?php\ninclude 'TCPDF/tcpdf.php';\ninclude 'nope.php';\ninclude 'Header.PHP';\n"
                      "include '../../etc/passwd';\n", "header.php": "<?php\n"}
    r = project(files, excluded=["TCPDF"])
    st = [(s["self"]["status"], s["self"]["basis"]) for s in site(r, "p.php")]
    assert st == [("excluded", "include_path"), ("missing", "relative"),
                  ("resolved", "include_path+case-insensitive"), ("missing", "cwd-relative")]


def test_include_inside_function_records_the_function_unit():
    files = {"p.php": "<?php\nfunction load() { include 'a.php'; }\n", "a.php": "<?php\n"}
    s = site(project(files), "p.php")[0]
    assert s["unit"] != "f001#00000"


def test_output_is_deterministic():
    files = {"index.php": "<?php include 'h.php';\n", "h.php": "<?php\n"}
    assert project(files) == project(files)
