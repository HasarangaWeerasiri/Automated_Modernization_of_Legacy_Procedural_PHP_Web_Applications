"""Roadmap Phase 4c: the post-freeze changes from the Phase 4b evidence, and the switch back to the frozen rules.

1) Boundary rules v1.1, R-I1 scope: a page guard only when the path that skips it leaves the page (redirect or
   exit); otherwise the if rules decide it (a conditional, condition by spec section 6).
2) Generation spec v0.14: `<head>` keeps only head content (the first other element or text ends it), and
   `</h1>`-`</h6>` closes the nearest open heading of any level.
3) `--rules v1.0` (config `rules_version`) reproduces the freeze commit's outputs byte for byte.
Plus the classifier's `flag-but-wrong` class on synthetic captures.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
import classify  # noqa: E402
import compare  # noqa: E402
import compile_check  # noqa: E402
import rules_guard  # noqa: E402
import run_apps  # noqa: E402
from ast_producer import SourceFile, parse_sources  # noqa: E402
from harness import FileHarness  # noqa: E402

from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from tests.test_harness import harness_app, pytestmark  # noqa: E402, F401
from tests.test_phase4b import flag, raw, unit_folder  # noqa: E402


def run_tiny(tmp_path, files: dict, rules: str = "1.1") -> dict:
    """The app `tiny` through the harness and Stages 1-5 with the given rules version (no contract)."""
    out = tmp_path / f"v{rules}"
    sources = []
    for index, (name, php) in enumerate(sorted(files.items()), start=1):
        (out / "src" / name).parent.mkdir(parents=True, exist_ok=True)
        (out / "src" / name).write_bytes(php.encode("utf-8"))
        sources.append((name, SourceFile(out / "src" / name, f"t{index:03d}", f"legacy-apps/tiny/{name}")))
    asts, _ = parse_sources([s for _, s in sources])
    harnesses = {name: FileHarness(s.source.read_bytes(), ast, s.path, {}, top_ifs=rules != "1.0")
                 for (name, s), ast in zip(sources, asts)}
    pages, silent = harness_app.build_timelines(harnesses, lambda path: {"generator": "c2-harness"})
    timelines = harness_app.AppTimelines("tiny", tuple(pages), tuple(silent), {}, len(files), (), {}, "",
                                         "legacy-apps/tiny")
    index = run_apps.run_app(timelines, out / "out", tsc=False, rules=rules)
    folder = out / "out" / "tiny"
    return {"index": index, "stage": lambda key, n: json.loads((folder / key / f"stage{n}.json").read_text(
        encoding="utf-8")), "page": lambda route: (folder / run_apps.NEXT_APP / "app" / "(plain)" / route
                                                   / "page.tsx").read_text(encoding="utf-8"),
        "file": lambda path: (folder / run_apps.NEXT_APP / path).read_text(encoding="utf-8"),
        "timeline": lambda key: json.loads((folder / key / "timeline.json").read_text(encoding="utf-8"))}


def rules_of(tree: dict) -> list:
    out = [(tree["type"], tuple(tree["rules"]))]
    for child in tree["children"]:
        out += rules_of(child)
    return out


# ---------------------------------------------------------------- 1) R-I1 scope (boundary rules v1.1)

WHOLE_PAGE = '<div class="page"><h3>Results</h3><table><tr><td>x</td></tr></table><a href="back.php">Back</a></div>\n'
REDIRECT_ELSE = ("<?php session_start();\nif (isset($_SESSION['user'])) { ?>\n" + WHOLE_PAGE
                 + '<?php } else { header("Location: login.php"); exit; } ?>\n')
REDIRECT_AFTER = "<?php if (isset($_SESSION['user'])) { ?>\n" + WHOLE_PAGE + '<?php }\nheader("Location: login.php"); ?>\n'
NOTHING = "<?php if (isset($_POST['search_submit'])) { ?>\n" + WHOLE_PAGE + "<?php } ?>\n"
REDIRECT_IN_ONE_ARM = ("<?php if (isset($_SESSION['user'])) { ?>\n" + WHOLE_PAGE
                       + '<?php } elseif ($x) { header("Location: login.php"); } else { $y = 1; } ?>\n')


@pytest.mark.parametrize("php", [REDIRECT_ELSE, REDIRECT_AFTER], ids=["else-arm", "after-the-if"])
def test_a_guard_whose_other_path_redirects_is_a_page_guard(tmp_path, php):
    result = run_tiny(tmp_path, {"page.php": php})
    root = result["stage"]("page.php", 2)["tree"]
    assert root["rules"] == ["R-I1"]  # (its reason shows the review: every whole-app node is unlabelled)
    assert "unresolvedCondition" not in result["page"]("page")


def test_the_harness_says_which_arms_leave_the_page(tmp_path):
    result = run_tiny(tmp_path, {"page.php": REDIRECT_IN_ONE_ARM})
    (top,) = result["timeline"]("page.php")["topLevelIfs"]
    assert [(a["branch"], a["leaves"]) for a in top["arms"]] == [("then", False), ("elseif", True), ("else", False)]
    assert top["afterLeaves"] is False
    root = result["stage"]("page.php", 2)["tree"]
    assert "R-I1" not in root["rules"]  # one other arm does not leave the page


def test_a_guard_whose_other_path_renders_nothing_is_a_conditional(tmp_path):
    """HMS search.php: nothing is printed on GET, so the guarded content is a conditional (R-I5 here: 6 elements),
    its condition untranslatable request input."""
    result = run_tiny(tmp_path, {"page.php": NOTHING})
    root = result["stage"]("page.php", 2)["tree"]
    assert root["rules"] == [] and ("ConditionalComponent", ("R-I5",)) in rules_of(root)
    assert "<PageSection1 />" in result["page"]("page")
    section = result["file"]("components/PageSection1.tsx")
    assert '{unresolvedCondition("t001#00001", "needs_request_context") && (' in section
    assert '<div className="page">' in section


def test_rules_v10_keeps_the_frozen_page_guard(tmp_path):
    result = run_tiny(tmp_path, {"page.php": NOTHING}, rules="1.0")
    root = result["stage"]("page.php", 2)["tree"]
    assert root["rules"] == ["R-I1"]  # (its reason shows the review: every whole-app node is unlabelled)
    assert "topLevelIfs" not in result["timeline"]("page.php")  # the v1.1 harness extension is not written


# ---------------------------------------------------------------- 2) browser equivalence (spec v0.14)

HEAD = ('<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n<title>T</title>\n'
        '<nav class="navbar"><a href="home.php">Home</a>\n<link rel="stylesheet" href="s.css">\n</nav>\n'
        '</head>\n<body>\n<p>Hi</p>\n</body>\n</html>\n')
HEAD_TEXT = "<html><head><title>T</title>\nHello there</head><body><p>Hi</p></body></html>\n"
HEADING = '<div class="box"><h4 class="t">My Appointments</h2>\n<p class="q">text</p></div>\n'


def test_the_first_element_that_is_not_head_content_ends_the_head(tmp_path):
    new, old = run_tiny(tmp_path, {"page.php": HEAD}), run_tiny(tmp_path, {"page.php": HEAD}, rules="1.0")
    page = new["page"]("page")
    assert '<nav className="navbar">' in page and '<link rel="stylesheet" href="s.css" />' in page
    assert "<meta" not in page and "<title" not in page  # head content stays dropped (document_shell)
    flags = new["stage"]("page.php", 5)["flags"]
    assert any(f["flag"] == "implied_end_tag" and f["detail"].startswith("<head> ended by <nav>") for f in flags)
    assert any(f["flag"] == "unmatched_close" and f["detail"].startswith("</head>") for f in flags)  # ignored
    assert "<nav" not in old["page"]("page")  # v1.0: dropped with <head>


def test_text_that_is_not_whitespace_ends_the_head(tmp_path):
    assert "Hello there" in run_tiny(tmp_path, {"page.php": HEAD_TEXT})["page"]("page")
    assert "Hello there" not in run_tiny(tmp_path, {"page.php": HEAD_TEXT}, rules="1.0")["page"]("page")


def test_a_heading_end_tag_closes_the_open_heading_of_any_level(tmp_path):
    new, old = run_tiny(tmp_path, {"page.php": HEADING}), run_tiny(tmp_path, {"page.php": HEADING}, rules="1.0")
    flat = lambda text: " ".join(text.split())  # noqa: E731
    assert '<h4 className="t">My Appointments</h4> <p className="q">text</p>' in flat(new["page"]("page"))
    assert '<h4 className="t"> My Appointments <p className="q">text</p> </h4>' in flat(old["page"]("page"))
    flags = new["stage"]("page.php", 5)["flags"]
    assert any(f["flag"] == "implied_end_tag" and "</h2> closes the open <h4>" in f["detail"] for f in flags)


# ---------------------------------------------------------------- 3) --rules v1.0 is the freeze, byte for byte


def test_the_config_names_the_rules_version():
    assert load_boundary_config().rules_version == "1.1"
    assert load_boundary_config(rules="v1.0").rules_version == "1.0"
    with pytest.raises(ValueError):
        load_boundary_config(rules="v2.0")


def test_rules_v10_reproduces_the_freeze_mocks():
    assert rules_guard.mocks_digest("1.0") == rules_guard.FREEZE_MOCKS
    assert rules_guard.mocks_digest("1.1") != rules_guard.FREEZE_MOCKS  # v1.1 does change the mocks


@pytest.mark.skipif(compile_check.unavailable() is not None, reason="tsc is part of the freeze outputs (index.json)")
def test_rules_v10_reproduces_the_freeze_apps():
    """The five discovery apps under --rules v1.0, with tsc, are the freeze commit's 2,079 output files (the 4b guard
    digest). They must be written to output/apps (its path is in every stage5.json): the current folder is moved
    aside and put back afterwards."""
    if any(not harness_app.APPS[app]["root"].exists() for app in harness_app.APPS):
        pytest.skip("a discovery app clone is not present")
    aside = rules_guard.APPS_OUTPUT.with_name(f"apps.aside-{os.getpid()}")
    if rules_guard.APPS_OUTPUT.exists():
        rules_guard.APPS_OUTPUT.rename(aside)
    try:
        digest, hashes = rules_guard.apps_digest("1.0")
        assert (len(hashes), digest) == (2079, rules_guard.FREEZE_APPS)
    finally:
        shutil.rmtree(rules_guard.APPS_OUTPUT, ignore_errors=True)
        if aside.exists():
            aside.rename(rules_guard.APPS_OUTPUT)


# ---------------------------------------------------------------- the classifier: flag-but-wrong


def test_a_head_drop_of_what_the_browser_puts_in_the_body_is_flag_but_wrong(tmp_path):
    sequence = [raw("n1", '<html><head><nav class="top"><a href="x">n</a></nav></head><body>'),
                raw("n2", '<p onclick="go()">x</p></body></html>')]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [
        flag("n1", "document_shell", "<head> and its content dropped"), flag("n2", "inline_js_dropped", "onclick on <p>")]))
    legacy, generated = '<body><nav class="top"><a href="x">n</a></nav><p onclick="go()">x</p></body>', "<body><p>x</p></body>"
    page = {"page": "p.php"} | compare.compare(legacy, generated)
    out = classify.classify_page(page, compare.body(legacy), compare.body(generated, generated=True), [unit], {}, {})
    assert sorted(d["class"] for d in out) == ["flag-but-wrong:document_shell", "flag:inline_js_dropped"]
    wrong = next(d for d in out if d["class"].startswith("flag-but-wrong"))
    assert wrong["contradiction"] == "dropped as <head> content, but the browser's tree has it in <body>"


def test_a_close_the_browser_did_not_make_is_flag_but_wrong(tmp_path):
    sequence = [raw("n1", '<div><h4 class="t">My</h2><p class="q">x</p></div>')]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [
        flag("n1", "implicit_close", "<h4> closed by </div>")]))
    legacy = '<body><div><h4 class="t">My</h4><p class="q">x</p></div></body>'
    generated = '<body><div><h4 class="t">My<p class="q">x</p></h4></div></body>'
    page = {"page": "p.php"} | compare.compare(legacy, generated)
    out = classify.classify_page(page, compare.body(legacy), compare.body(generated, generated=True), [unit], {}, {})
    assert out and {d["class"] for d in out} == {"flag-but-wrong:implicit_close"}


def test_a_gap_flag_stays_an_explained_difference():
    gap = {"kind": "document_shell", "detail": "<body style> dropped, children kept: it belongs in app/layout.tsx"}
    assert classify.contradiction(gap, {"kind": "attributes"}) is None
    assert classify.contradiction({"kind": "C2Todo", "detail": "collection"}, {"kind": "missing"}) is None


def test_content_under_an_untranslated_condition_is_explained_by_it(tmp_path):
    """HMS include/config.php under v1.1: the legacy page prints the error text, the generated one nothing."""
    sequence = [{"id": "n1", "kind": "Stmt_Echo", "enclosedBy": [{"nodeId": "if1", "role": "branch"}],
                 "parts": [{"nodeId": "n1.a", "kind": "literal", "value": "Failed to connect: "},
                           {"nodeId": "n1.b", "kind": "expr", "value": ""}]}]
    gap = {"kind": "unresolvedCondition", "reason": "condition_not_translatable", "nodeId": "if1", "detail": "",
           "file": "app/p/page.tsx", "line": 1}
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [], [gap]))
    found = classify.explain_text([unit], "Failed to connect: Unknown database 'hms'", "")
    assert (found["kind"], found["nodeId"]) == ("unresolvedCondition", "if1")
    assert classify.explain_text([unit], "Something else", "") is None
