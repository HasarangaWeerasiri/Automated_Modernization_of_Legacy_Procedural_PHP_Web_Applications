"""Roadmap Phase 4e: the runtime check on a second app (phpBlog). The comparison and classifier changes it needed,
on synthetic input, and the reproduction of phpBlog's installer-written configuration (tools/runtime/phpblog/).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import classify  # noqa: E402
import compare  # noqa: E402

from tests.test_phase4b import flag, raw, unit_folder  # noqa: E402

PHPBLOG = ROOT / "legacy-apps" / "phpblog"


# ---------------------------------------------------------------- compare.py: the sibling alignment


def test_a_missing_sibling_does_not_shift_the_pairing():
    legacy = '<body><div class="bar"><h5>Latest</h5></div><div class="main"><p>a</p><p>b</p></div></body>'
    generated = '<body><div class="main"><p>a</p><p>b</p></div></body>'
    result = compare.compare(legacy, generated)
    assert [(d["kind"], d["path"]) for d in result["differences"]] == [("missing", "body>div[0]")]
    assert result["matched"] == 3


def test_an_element_pairs_with_its_own_counterpart_first():
    """phpBlog's nav: the legacy menu <ul> (rows) must pair with the generated (empty) menu <ul>, not with the
    structurally richer second <ul>."""
    legacy = ('<body><ul class="menu"><li><a>A</a></li><li><a>B</a></li></ul>'
              '<ul class="user"><li><a>Sign in</a></li></ul></body>')
    generated = ('<body><ul class="menu"></ul>'
                 '<ul class="user"><li><a>Profile</a><ul><li><a>x</a></li><li><a>y</a></li></ul></li></ul></body>')
    matches = []
    compare.compare(legacy, generated, matches)
    assert ("body>ul[0]", "body>ul[0]") in matches and ("body>ul[1]", "body>ul[1]") in matches


def test_a_page_that_redirects_is_left_out(tmp_path, monkeypatch):
    out = tmp_path / "app"
    for side in ("legacy", "generated"):
        (out / side).mkdir(parents=True)
        (out / side / "a.php.html").write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
    rows = [{"page": "a.php", "getOnly": False, "captured": True},
            {"page": "admin.php", "getOnly": True, "captured": True, "legacyRefresh": "../login"}]
    (out / "capture.json").write_text(json.dumps({"pages": rows}), encoding="utf-8")
    monkeypatch.setattr(compare, "OUTPUT", tmp_path)
    assert compare.main(["app"]) == 0
    report = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert [p["page"] for p in report["pages"]] == ["a.php"]
    assert report["redirected"] == [{"page": "admin.php", "getOnly": True, "legacyRefresh": "../login"}]


def test_the_capture_reads_a_meta_refresh_target():
    capture = pytest.importorskip("capture")
    tag = capture.REFRESH_TAG.search('<p>x</p><meta http-equiv="refresh" content="0; url=../login" />').group(0)
    assert capture.REFRESH_URL.search(tag).group(1) == "../login"
    assert capture.REFRESH_TAG.search('<meta charset="utf-8">') is None


# ---------------------------------------------------------------- classify.py


def branch(if_id, arm):
    return {"nodeId": if_id, "role": "branch", "branch": arm}


def test_an_else_arm_rendered_by_an_untranslated_condition_is_flag_but_wrong(tmp_path):
    """phpBlog core.php 652: unresolvedCondition is false, so the else arm (the Profile menu) renders on every
    request; the anonymous browser shows the then arm."""
    sequence = [raw("n0", '<ul class="nav">'),
                dict(raw("n1", '<li class="signin"><a href="login">Sign In</a></li>'), enclosedBy=[branch("if1", "then")]),
                dict(raw("n2", '<li class="profile"><a href="#">Profile</a></li>'), enclosedBy=[branch("if1", "else")]),
                raw("n3", "</ul>")]
    todo = {"kind": "unresolvedCondition", "reason": "condition_not_translatable", "nodeId": "if1", "detail": "",
            "file": "components/Shell.tsx", "line": 3}
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [], [todo]))
    legacy = '<body><ul class="nav"><li class="signin"><a href="login">Sign In</a></li></ul></body>'
    generated = '<body><ul class="nav"><li class="profile"><a href="#">Profile</a></li></ul></body>'
    matches = []
    page = {"page": "p.php"} | compare.compare(legacy, generated, matches)
    out = classify.classify_page(page, compare.body(legacy), compare.body(generated, generated=True), [unit], {}, {},
                                 (), dict(matches))
    wrong = [d for d in out if d["class"] == "flag-but-wrong:unresolvedCondition"]
    assert wrong and all(d["flag"]["nodeId"] == "if1" for d in wrong)
    # the same arm on both sides is not wrong (the legacy page took it too)
    assert classify.defaulted_arm([unit], compare.Node("li", (("class", "profile"),), "", []),
                                  compare.Node("li", (("class", "profile"),), "", [])) is None


def test_a_flag_on_the_if_inside_a_start_tag_explains_its_attribute(tmp_path):
    """phpBlog: `<div class="<?php if (...) echo 'container-fluid'; else echo 'container'; ?> mt-3">`: the arms print
    next to each other in the source text (word match), and the flag is on the if."""
    sequence = [raw("n0", '<div class="'),
                dict(raw("n1", "container-fluid"), enclosedBy=[branch("if1", "then")]),
                dict(raw("n2", "container"), enclosedBy=[branch("if1", "else")]),
                raw("n3", ' mt-3">x</div>')]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [flag("if1", "attribute_omitted", "class on <div>",
                                                                        "condition_not_translatable")]))
    element = compare.Node("div", (("class", "container mt-3"),), "x", [])
    assert classify.explain_attributes([unit], element, ["class"])["nodeId"] == "if1"


def test_text_is_tied_through_entities_and_the_nearest_located_ancestor(tmp_path):
    echo = {"id": "n1", "kind": "Stmt_Echo", "enclosedBy": [],  # echo "&copy; " . date('Y')
            "parts": [{"nodeId": "n1a", "kind": "literal", "value": "&copy; "}, {"nodeId": "n1b", "kind": "expr"}]}
    sequence = [raw("n0", '<p class="copy">'), echo,
                raw("n2", '</p><div class="mail"><a><span>'), {"id": "n3", "kind": "Stmt_Echo", "enclosedBy": []},
                raw("n4", "</span></a></div>")]
    todos = [{"kind": "C2Todo", "reason": "unresolved_read", "nodeId": n, "detail": "", "file": "f", "line": 1}
             for n in ("n1b", "n3")]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [], todos))
    assert classify.explain_text([unit], "© 2026", "©")["nodeId"] == "n1b"  # the literal &copy; is ©
    tree = compare.body('<body><div class="mail"><a><span>admin@mail.com</span></a></div></body>')
    found = classify.explain_text([unit], "admin@mail.com", "", tree, "body>div[0]>a[0]>span[0]")
    assert found["nodeId"] == "n3" and found["via"].startswith("inside <span>")  # (the span occurs once: found)


def test_react_hoisting_needs_a_copy_beyond_next_js_own(tmp_path):
    head = compare.body("<body><meta charset='utf-8'><meta name='author' content='x'></body>").children
    charset = compare.Node("meta", (("charset", "utf-8"),), "", [])
    author = compare.Node("meta", (("content", "x"), ("name", "author")), "", [])
    assert classify.react_hoisted(author, head)  # not one of Next.js's own: one copy proves it
    assert not classify.react_hoisted(charset, head)  # Next.js writes this one itself
    assert classify.react_hoisted(charset, head + head[:1])  # its own and the hoisted one


def test_a_page_wide_finding_classifies_every_difference_of_that_page(tmp_path):
    unit = classify.Unit("u.php", unit_folder(tmp_path, [raw("n0", "<p>Config updated</p>")], []))
    legacy, generated = "<body><p>Config updated</p></body>", "<body><p>Config updated</p><br><br></body>"
    page = {"page": "u.php"} | compare.compare(legacy, generated)
    notes = {("u.php", "*"): {"class": "env:legacy_runtime_error", "rootCause": "fatal error at line 46"}}
    out = classify.classify_page(page, compare.body(legacy), compare.body(generated, generated=True), [unit], {}, notes)
    assert out and {d["class"] for d in out} == {"env:legacy_runtime_error"}


# ---------------------------------------------------------------- phpBlog's configuration, as its installer makes it


def test_the_config_files_are_made_exactly_as_install_done_php_makes_them(tmp_path):
    from ast_producer import find_php

    if find_php() is None or not (PHPBLOG / ".git").exists() or shutil.which("git") is None:
        pytest.skip("PHP or the phpBlog clone is not present")
    tree, out = tmp_path / "tree", tmp_path / "out"
    (tree / "install").mkdir(parents=True)
    out.mkdir()
    for name in ("config_settings.php", "install/config.tpl"):
        (tree / name).write_bytes(subprocess.run(["git", "-C", str(PHPBLOG), "show", f"4ffe3c9:{name}"],
                                                 capture_output=True, check=True).stdout)
    sys.path.insert(0, str(ROOT / "tools" / "runtime" / "phpblog"))
    import prepare  # noqa: E402

    (tmp_path / "in.json").write_text(json.dumps(prepare.INPUTS), encoding="utf-8")
    subprocess.run([find_php(), str(ROOT / "tools" / "runtime" / "phpblog" / "make_config.php"), str(tree), str(out),
                    str(tmp_path / "in.json")], check=True)
    config = (out / "config.php").read_text(encoding="utf-8")
    assert '$host     = "db";' in config and '$database = "phpblog";' in config and "<DB_" not in config
    template = (tree / "install" / "config.tpl").read_text(encoding="utf-8")
    assert config == template.replace("<DB_HOST>", "db").replace("<DB_NAME>", "phpblog").replace(
        "<DB_USER>", "root").replace("<DB_PASSWORD>", "phpblog")  # done.php 50-53, nothing else
    settings = (out / "config_settings.php").read_text(encoding="utf-8")
    assert settings.startswith("<?php $settings = array (") and settings.endswith("); ?>")
    assert "'site_url' => 'http://127.0.0.1:18081'," in settings and "'email' => 'admin@mail.com'," in settings
    assert "'sitename' => 'phpBlog'," in settings  # the repository's own settings otherwise
