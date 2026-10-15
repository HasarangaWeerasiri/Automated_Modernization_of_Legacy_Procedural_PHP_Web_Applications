"""Roadmap Phase 3, step 5: the last changes before the freeze (boundary-rules v1.0, generation-spec v0.12).

a) Include paths built from constants are resolved when statically certain (rules section 4); b) an included
file that prints only whitespace hands that text to the spec section 5 whitespace rules at the include site; c)
an AJAX endpoint (no markup, only `json_encode(...)`) is `non_html_response`, subtype `ajax_endpoint`. Then the
evaluation's sampling scripts (docs/gold-standard-guide.md), tested on synthetic input only.
"""

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import sample_agreement  # noqa: E402
import sample_eval_pages  # noqa: E402

from tests.test_harness import harness_app, pytestmark  # noqa: E402, F401
from tests.test_phase3 import abstains, flat, run  # noqa: E402


def includes(result: dict, key: str) -> list:
    return [(e["path"], e["outputsInside"], e.get("whitespace"))
            for e in result["timelines"][key]["sequence"] if e["kind"] == "include"]


# ---------------------------------------------------------------- a) include paths (rules v1.0 section 4)


@pytest.mark.parametrize("load", [
    # warehouse includes/load.php 13-22
    "<?php defined('SITE_ROOT') ? null : define('SITE_ROOT', realpath(dirname(__FILE__)));\n"
    "define('DS', DIRECTORY_SEPARATOR);\ndefine('LIB', SITE_ROOT.DS);\nrequire_once(LIB.'conf.php');",
    "<?php require_once(__DIR__ . '/conf.php');",
    "<?php include dirname(__FILE__) . DIRECTORY_SEPARATOR . 'conf.php';",
])
def test_an_include_path_built_from_constants_is_resolved(tmp_path, load):
    files = {"inc/load.php": load, "inc/conf.php": "<?php echo '<b>conf</b>';",
             "page.php": "<?php include 'inc/load.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    assert includes(result, "inc__load.php") == [("legacy-apps/tiny/inc/conf.php", True, None)]
    (folded,) = result["timelines"]["inc__load.php"]["includes"]
    assert folded["literal"] is None and folded["folded"] == "/inc/conf.php"


@pytest.mark.parametrize("load", [
    "<?php define('ROOT', 'inc/'); define('ROOT', 'x/'); require ROOT . 'conf.php';",  # defined twice
    "<?php require realpath('inc') . '/conf.php';",  # relative to the working directory
    "<?php require $base . 'conf.php';",  # a variable
    "<?php require LIB . 'conf.php';",  # never defined
])
def test_an_include_path_that_is_not_certain_stays_unresolved(tmp_path, load):
    files = {"inc/load.php": load, "inc/conf.php": "<?php echo '<b>conf</b>';",
             "page.php": "<?php include 'inc/load.php'; ?><p>p</p>"}
    result = run(tmp_path, files)
    assert includes(result, "page.php") == [("legacy-apps/tiny/inc/load.php", "unknown", None)]
    assert ("unresolved_call", None, 1) in abstains(result, "page.php")


# ---------------------------------------------------------------- b) whitespace-only included output


def test_an_include_that_prints_only_whitespace_is_text_at_its_site(tmp_path):
    """warehouse includes/load.php: an empty line before `<?php`."""
    files = {"load.php": "\n<?php $a = 1;", "page.php": "<p><b>a</b><?php include 'load.php'; ?><i>b</i></p>"}
    result = run(tmp_path, files)
    assert includes(result, "page.php") == [("legacy-apps/tiny/load.php", False, "\n")]
    assert "load.php" in result["silent"] and abstains(result, "page.php") == []
    assert '<p><b>a</b>{" "}<i>b</i></p>' in flat(result["file"]("app/(plain)/page/page.tsx"))  # between inline nodes


def test_whitespace_before_the_doctype_is_dropped(tmp_path):
    files = {"load.php": "\n\n<?php $a = 1;", "page.php": "<?php include 'load.php'; ?><!DOCTYPE html><div>d</div>"}
    result = run(tmp_path, files)
    assert includes(result, "page.php") == [("legacy-apps/tiny/load.php", False, "\n\n")]
    assert "cross_file_output" not in [r for r, _, _ in abstains(result, "page.php")]
    assert flat(result["file"]("app/(plain)/page/page.tsx")).count("{\" \"}") == 0


def test_whitespace_reached_through_a_silent_include_is_kept(tmp_path):
    files = {"load.php": "\n<?php $a = 1;", "lib.php": "<?php include 'load.php';",
             "page.php": "<pre>x<?php include 'lib.php'; ?>y</pre>"}
    result = run(tmp_path, files)
    assert includes(result, "page.php") == [("legacy-apps/tiny/lib.php", False, "\n")]
    assert "x\\ny" in result["file"]("app/(plain)/page/page.tsx")  # kept exactly inside <pre>


@pytest.mark.parametrize("other", [
    "\n<?php echo 'x';",  # prints something else too
    "\n<?php include 'printing.php';",  # includes a file that prints
])
def test_a_file_that_prints_more_than_whitespace_is_still_cross_file_output(tmp_path, other):
    files = {"load.php": other, "printing.php": "<b>p</b>", "page.php": "<div><?php include 'load.php'; ?></div>"}
    result = run(tmp_path, files)
    assert includes(result, "page.php")[0][1:] == (True, None)
    assert ("cross_file_output", None, 1) in abstains(result, "page.php")


# ---------------------------------------------------------------- c) ajax_endpoint (rules v1.0 section 5)


def test_a_unit_that_only_prints_json_is_an_ajax_endpoint(tmp_path):
    """warehouse ajax.php 26, 63: `echo json_encode($html);`, called by jQuery in libs/js/functions.js."""
    files = {"ajax.php": "<?php\nif (isset($_POST['q'])) {\n  echo json_encode($html);\n}\n?>\n",
             "page.php": "<p><?php echo json_encode($data); ?></p>"}  # markup of its own: a page
    result = run(tmp_path, files)
    entries = {n["path"]: n for n in result["index"]["notGenerated"]}
    assert set(entries) == {"ajax.php"}
    assert (entries["ajax.php"]["reason"], entries["ajax.php"]["subtype"], entries["ajax.php"]["calledFrom"]) == (
        "non_html_response", "ajax_endpoint", [])
    (flag,) = result["stage"]("ajax.php", 5)["flags"]
    assert flag["detail"].startswith("an AJAX endpoint: no markup of its own, its only output is json_encode(...)")
    assert result["stage"]("ajax.php", 2)["tree"]["flags"] == ["ajax_endpoint"]


def test_the_script_that_calls_an_endpoint_is_found(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "js" / "f.js").write_text("$.ajax({\n  url : 'ajax.php',\n});\n$.post(\"sub/b.php?x=1\");\n"
                                          "load('other.php');\n", encoding="utf-8")
    (tmp_path / "vendor" / "lib.js").write_text("get('ajax.php')", encoding="utf-8")
    found = harness_app.js_references(tmp_path, ["ajax.php", "sub/b.php"], exclude=("vendor/",))
    assert found == {"ajax.php": [{"file": "js/f.js", "line": 2}], "sub/b.php": [{"file": "js/f.js", "line": 4}]}
    # under a document root the path is read from it
    found = harness_app.js_references(tmp_path, ["website/ajax.php"], docroot="website")
    assert found == {"website/ajax.php": [{"file": "js/f.js", "line": 2}, {"file": "vendor/lib.js", "line": 1}]}


# ---------------------------------------------------------------- the evaluation's sampling (synthetic input only)


def synthetic_app(root: Path, pages: int) -> Path:
    for index in range(pages):
        (root / ("admin" if index % 3 == 0 else "") / f"p{index:02d}.php").parent.mkdir(parents=True, exist_ok=True)
        (root / ("admin" if index % 3 == 0 else "") / f"p{index:02d}.php").write_bytes(b"<?php")
    for library in ("vendor/x", "install", "includes", "Lib"):
        (root / library).mkdir(parents=True, exist_ok=True)
        (root / library / "f.php").write_bytes(b"<?php")
    (root / "notes.txt").write_bytes(b"")
    return root


def test_the_page_sample_takes_all_pages_when_there_are_ten_or_fewer(tmp_path):
    app = synthetic_app(tmp_path / "small-app", 7)
    assert sample_eval_pages.page_like(app) == sorted(
        ["admin/p00.php", "p01.php", "p02.php", "admin/p03.php", "p04.php", "p05.php", "admin/p06.php"])
    assert sample_eval_pages.sample(sample_eval_pages.page_like(app)) == sample_eval_pages.page_like(app)


def test_the_page_sample_is_the_seeded_ten(tmp_path, capsys):
    app = synthetic_app(tmp_path / "big-app", 25)
    paths = sample_eval_pages.page_like(app)
    assert len(paths) == 25
    chosen = sample_eval_pages.sample(paths)
    assert chosen == random.Random(20261015).sample(sorted(paths), 10) and len(set(chosen)) == 10
    assert sample_eval_pages.main([str(app)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"big-app/{p}" for p in chosen]
    assert sample_eval_pages.sample(list(reversed(paths))) == chosen  # the input order does not matter


def test_the_agreement_sample_is_a_seeded_fifth_rounded_up(tmp_path, capsys):
    sampled = [f"app{a}/p{i}.php" for a in range(5) for i in range(10)] + ["app0/extra.php"]  # 51 pages
    listing = tmp_path / "sample.txt"
    listing.write_text("# gold/sample.txt\n\n" + "\n".join(reversed(sampled)) + "\n", encoding="utf-8")
    assert sample_agreement.pages(listing.read_text(encoding="utf-8")) == sorted(sampled)
    chosen = sample_agreement.sample(sampled)
    assert len(chosen) == 11 and chosen == random.Random(20261016).sample(sorted(sampled), 11)
    assert sample_agreement.main([str(listing)]) == 0
    assert capsys.readouterr().out.splitlines() == chosen
    assert sample_agreement.sample([]) == [] and len(sample_agreement.sample(["a/b.php"])) == 1
