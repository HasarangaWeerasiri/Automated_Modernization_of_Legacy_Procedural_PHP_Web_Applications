"""Roadmap Phase 3, step 1: the five fixes from the Phase 2b review (generation-spec v0.7), an include as
a timeline entry with R-F2 for included files (boundary-rules v0.12 section 4), and phpBlog, the first new
rule-discovery app, run as a whole.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import pattern_log  # noqa: E402
import run_apps  # noqa: E402
from ast_producer import SourceFile, parse_sources  # noqa: E402
from harness import FileHarness  # noqa: E402

from src.mapping import app_units  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.loader import load_timeline  # noqa: E402
from tests.test_harness import harness_app, page, phpblog_clone, pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402


def run(tmp_path, files: dict, docroot: str = "", tsc: bool = False, build: bool = False) -> dict:
    """Run PHP files as one app named `tiny`: its index and readers for the stage outputs and generated files."""
    sources = []
    for index, (name, php) in enumerate(sorted(files.items()), start=1):
        (tmp_path / "src" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / name).write_bytes(php.encode("utf-8"))
        sources.append((name, SourceFile(tmp_path / "src" / name, f"t{index:03d}", f"legacy-apps/tiny/{name}")))
    asts, _ = parse_sources([s for _, s in sources])
    harnesses = {name: FileHarness(s.source.read_bytes(), ast, s.path, {"users": ["id", "name"]})
                 for (name, s), ast in zip(sources, asts)}
    pages, silent = harness_app.build_timelines(harnesses, lambda path: {"generator": "c2-harness"})
    timelines = harness_app.AppTimelines("tiny", tuple(pages), tuple(silent), {}, len(files), (), {}, docroot,
                                         "legacy-apps/tiny")
    index = run_apps.run_app(timelines, tmp_path / "out", tsc=tsc, build=build)
    folder = tmp_path / "out" / "tiny"

    def stage(key: str, number: int) -> dict:
        return json.loads((folder / key / f"stage{number}.json").read_text(encoding="utf-8"))

    def nodes(key: str) -> list:
        return list(apps_results_table._walk(stage(key, 2)["tree"]))

    def file(path: str) -> str:
        return (folder / run_apps.NEXT_APP / path).read_text(encoding="utf-8")

    return {"index": index, "folder": folder, "out": tmp_path / "out", "stage": stage, "nodes": nodes, "file": file,
            "pages": {p["key"]: p for p in index["pages"]}, "timelines": {p.key: p.timeline for p in pages},
            "silent": silent}


def abstains(result: dict, key: str) -> list:
    return [(n["reason"], n["rule"], len(n["sourceIds"])) for n in result["nodes"](key) if n["status"] == "abstain"]


def flat(text: str) -> str:
    return "".join(line.strip() for line in text.splitlines())


# ---------------------------------------------------------------- a) value on non-editable inputs (spec v0.7 section 5)


@pytest.mark.parametrize("kind", ["submit", "button", "reset", "image", "hidden", "checkbox", "radio", "SUBMIT"])
def test_value_stays_value_on_an_input_whose_value_is_not_form_state(tmp_path, kind):
    result = stages(tmp_path, f'<form><input type="{kind}" name="n" value="Go"></form>')
    page_text = result["files"]["app/page/page.tsx"]
    assert 'value="Go"' in page_text and "defaultValue" not in page_text


@pytest.mark.parametrize("attributes", ['type="text"', 'type="password"', 'type="email"', 'type="date"', ""])
def test_value_on_an_editable_input_is_still_default_value(tmp_path, attributes):
    result = stages(tmp_path, f'<form><input {attributes} name="n" value="x"></form>')
    assert 'defaultValue="x"' in result["files"]["app/page/page.tsx"]


def test_an_input_whose_type_is_not_literal_keeps_default_value(tmp_path):
    result = stages(tmp_path, '<form><input type="<?php echo $kind; ?>" name="n" value="x"></form>')
    assert 'defaultValue="x"' in result["files"]["app/page/page.tsx"]  # not known to be a fixed-value type


def test_checked_is_form_state_whatever_the_type(tmp_path):
    result = stages(tmp_path, '<form><input type="radio" name="n" value="a" checked></form>')
    assert '<input type="radio" name="n" value="a" defaultChecked />' in result["files"]["app/page/page.tsx"]


# ---------------------------------------------------------------- c) stray </p> and </br> (spec v0.7 section 5b)


def stray_flags(result: dict) -> list:
    return [(f["flag"], f["severity"]) for f in result["stage5"]["flags"]
            if f["flag"] in ("stray_end_tag_repaired", "unmatched_close", "html_nesting_changed")]


def test_a_stray_end_p_is_an_empty_paragraph(tmp_path):
    result = stages(tmp_path, "<div><ol><li>a</li></p></ol><b>x</b></div>")
    assert "<div><ol><li>a</li><p /></ol><b>x</b></div>" in flat(result["files"]["app/page/page.tsx"])
    assert stray_flags(result) == [("stray_end_tag_repaired", "info")]  # and the nesting check agrees


def test_a_stray_end_br_is_a_line_break(tmp_path):
    result = stages(tmp_path, "<div>a</br>b</div>")
    assert flat(result["files"]["app/page/page.tsx"]).count("<br />") == 1
    assert stray_flags(result) == [("stray_end_tag_repaired", "info")]


def test_an_end_p_with_a_p_open_closes_it_as_before(tmp_path):
    result = stages(tmp_path, "<div><p>a <b>b</p></div>")
    assert "<p />" not in result["files"]["app/page/page.tsx"] and not [
        f for f in stray_flags(result) if f[0] == "stray_end_tag_repaired"]


def test_any_other_unmatched_end_tag_is_still_dropped(tmp_path):
    result = stages(tmp_path, "<div>a</span></table>b</div>")
    assert [f for f, _ in stray_flags(result)] == ["unmatched_close", "unmatched_close"]  # R-L6: browsers ignore them


def test_a_stray_end_p_after_an_abstained_region_is_not_repaired(tmp_path):
    """What the abstained region leaves open is not known: a <p> of its last pass may be what this </p> closes."""
    php = "<div><?php foreach ($l as $v) { ?><p>x<?php } ?></p></div>"
    result = stages(tmp_path, php)
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["container_undetermined"]
    assert "stray_end_tag_repaired" not in [f for f, _ in stray_flags(result)]


def test_a_functions_stray_end_p_is_not_repaired(tmp_path):
    """It may close a <p> its caller opened: the function is not tag-balanced, and nothing is inserted."""
    files = {"lib.php": "<?php function tail() { ?>end</p><b>x</b><?php }",
             "page.php": "<?php include 'lib.php'; ?><div><p>start <?php tail(); ?></div>"}
    result = run(tmp_path, files)
    assert abstains(result, "page.php") == [("tag_crosses_function", "R-F2", 1)]
    flags = result["stage"]("lib.php#tail", 5)["flags"]
    assert "stray_end_tag_repaired" not in [f["flag"] for f in flags] and "unmatched_close" in [
        f["flag"] for f in flags]


# ---------------------------------------------------------------- d) consecutive unresolved calls (spec v0.7 section 8a)


def test_consecutive_unresolved_calls_are_one_gap(tmp_path):
    php = "<div><?php $pdf->SetTitle('t'); $pdf->AddPage(); require_once('lib/' . $x); $pdf->Output(); ?></div>"
    result = run(tmp_path, {"page.php": php})
    assert abstains(result, "page.php") == [("unresolved_call", None, 4)]
    ids = [e["id"] for e in result["timelines"]["page.php"]["sequence"] if e["kind"] in ("call", "include")]
    assert len(ids) == 4
    page_text = result["file"]("app/(plain)/page/page.tsx")
    assert page_text.count("<C2Todo") == 2 and f'<C2Todo id="{",".join(ids)}" reason="unresolved_call" />' in page_text
    (todo,) = [t for t in result["stage"]("page.php", 5)["c2todos"] if t["reason"] == "unresolved_call"]
    assert (todo["severity"], todo["sourceLine"], todo["nodeId"]) == ("todo", 1, ",".join(ids))  # counted once
    assert todo["detail"].startswith("4 consecutive calls / includes")


def test_output_between_two_unresolved_calls_keeps_them_apart(tmp_path):
    php = "<div><?php $pdf->a(); ?><b>x</b><?php $pdf->b(); $pdf->c(); ?></div>"
    result = run(tmp_path, {"page.php": php})
    assert abstains(result, "page.php") == [("unresolved_call", None, 1), ("unresolved_call", None, 2)]
    assert result["file"]("app/(plain)/page/page.tsx").count('reason="unresolved_call"') == 2


def test_unresolved_calls_in_different_blocks_are_not_merged(tmp_path):
    php = "<div><?php $pdf->a(); if ($x) { $pdf->b(); } $pdf->c(); ?></div>"
    result = run(tmp_path, {"page.php": php})
    assert [a for a in abstains(result, "page.php") if a[0] == "unresolved_call"] == [("unresolved_call", None, 1)] * 3


# ---------------------------------------------------------------- e) routes from the document root; the build record

SITE = {
    "site/index.php": "<h1>Home</h1>",
    "site/about.php": "<p>About</p>",
    "site/users/index.php": "<p>Users</p>",
    "site/users/login.php": "<?php include '../inc/top.php'; ?><form><input type='submit' value='Go'></form>"
                            "<?php include '../inc/bottom.php'; ?>",
    "site/inc/top.php": "<html><head><title>T</title></head><body><div class='wrap'>",
    "site/inc/bottom.php": "</div></body></html>",
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    return run(tmp_path_factory.mktemp("site"), SITE, docroot="site", tsc=compile_check.unavailable() is None)


def test_routes_are_paths_from_the_document_root(site):
    files = set(site["index"]["appFiles"])
    assert {"app/(plain)/page.tsx", "app/(plain)/about/page.tsx", "app/(plain)/users/page.tsx",
            "app/(inc-top)/users/login/page.tsx", "app/(inc-top)/layout.tsx", "app/(plain)/layout.tsx"} <= files
    assert not [f for f in files if "site" in f.split("/")]  # the served folder is not part of any URL
    assert {p["key"]: p["route"] for p in site["index"]["pages"] if p["role"] == "page"} == {
        "site__index.php": "/", "site__about.php": "/about", "site__users__index.php": "/users",
        "site__users__login.php": "/users/login"}
    assert site["index"]["docroot"] == "site" and site["index"]["collisions"] == []
    assert "export default function Page()" in site["file"]("app/(plain)/page.tsx")
    assert 'from "../../lib/c2-todo"' in site["file"]("app/(plain)/page.tsx")


def test_route_of():
    assert run_apps.route_of("website/users/login.php", "website") == ("users/login", "UsersLogin")
    assert run_apps.route_of("website/index.php", "website") == ("", "Index")
    assert run_apps.route_of("admin/index.php") == ("admin", "AdminIndex")
    assert run_apps.route_of("admin-panel1.php") == ("admin-panel1", "AdminPanel1")
    assert run_apps.route_of("tools/x.php", "website") == ("tools/x", "ToolsX")  # outside the served folder: kept


def test_the_site_compiles_as_one_app(site):
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    assert site["index"]["tscRan"] and site["index"]["tscUnattributed"] == 0, site["index"]["tscOutput"]
    assert not [p["key"] for p in site["index"]["pages"] if p["tsc"]]


def test_the_build_result_is_kept_beside_the_app_not_in_it(site, tmp_path):
    assert "nextBuild" not in site["index"]
    app_dir = site["folder"] / run_apps.NEXT_APP
    assert compile_check.read_build(site["out"], "tiny", app_dir) is None  # nothing was built
    compile_check.write_build(tmp_path, "tiny", app_dir, True, "ok")
    assert compile_check.build_record(tmp_path, "tiny") == tmp_path / "tiny.next-build.json"
    assert compile_check.read_build(tmp_path, "tiny", app_dir)["ok"] is True
    changed = tmp_path / "changed"
    changed.mkdir()
    (changed / "page.tsx").write_text("x", encoding="utf-8")
    assert compile_check.read_build(tmp_path, "tiny", changed) is None  # a build of other files is not this one's


def test_two_runs_of_an_app_are_byte_identical(tmp_path):
    first = compile_check.tree_hash(run(tmp_path, SITE, docroot="site")["folder"])
    assert first == compile_check.tree_hash(run(tmp_path, SITE, docroot="site")["folder"])


# ---------------------------------------------------------------- includes are timeline entries (pending Q4)


def include_entries(result: dict, key: str) -> list:
    return [(e["path"], e["outputsInside"]) for e in result["timelines"][key]["sequence"] if e["kind"] == "include"]


def test_an_include_is_a_sequence_entry_at_its_position(tmp_path):
    files = {"part.php": "<b>part</b>", "quiet.php": "<?php $x = 1;",
             "page.php": "<p>a</p><?php include 'part.php'; require_once('quiet.php'); include $name; "
                         "include 'missing.php'; ?><p>b</p>"}
    result = run(tmp_path, files)
    timeline = result["timelines"]["page.php"]
    assert [e["kind"] for e in timeline["sequence"]] == [
        "Stmt_InlineHTML", "include", "include", "include", "include", "Stmt_InlineHTML"]
    assert include_entries(result, "page.php") == [
        ("legacy-apps/tiny/part.php", True),  # the file's top level prints
        ("legacy-apps/tiny/quiet.php", False),  # it prints nothing: no slot
        (None, "unknown"),  # not a literal path
        (None, "unknown")]  # no such file in the app (a library, a file the installer writes)
    entry = next(e for e in timeline["sequence"] if e["kind"] == "include")
    assert set(entry) == {"id", "kind", "path", "outputsInside", "enclosedBy", "loc"}
    assert "includesExtension" in timeline["_provenance"]  # marked as a harness extension, pending Member 01 Q4
    loaded = load_timeline(result["folder"] / "page.php" / "timeline.json")
    assert [(c.kind, c.callee, c.outputs_inside) for c in loaded.calls][:2] == [
        ("include", "legacy-apps/tiny/part.php", True), ("include", "legacy-apps/tiny/quiet.php", False)]
    assert [n.kind for n in loaded.sequence] == ["Stmt_InlineHTML", "Stmt_InlineHTML"]


@pytest.mark.parametrize("change, message", [
    (lambda e: e.pop("path"), "include keys"),
    (lambda e: e.update(path=3), "include path"),
    (lambda e: e.update(outputsInside="maybe"), "outputsInside"),
    (lambda e: e.update(callee=None), "include keys"),
])
def test_the_loader_rejects_a_malformed_include_entry(tmp_path, change, message):
    document = page(tmp_path, "<?php include 'x.php'; ?><p>a</p>")
    change(next(e for e in document["sequence"] if e["kind"] == "include"))
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)


def test_what_a_function_or_a_file_prints_is_followed_through_its_includes(tmp_path):
    files = {"a.php": "<?php include 'b.php';", "b.php": "<?php include 'c.php';", "c.php": "<i>c</i>",
             "lib.php": "<?php function quiet() { include 'config.php'; return 1; }\n"
                        "function loud() { include 'c.php'; }\nfunction dark() { include 'nowhere.php'; }",
             "config.php": "<?php $db = 1;",
             "page.php": "<?php include 'lib.php'; ?><div><?php include 'a.php'; quiet(); loud(); dark(); ?></div>"}
    result = run(tmp_path, files)
    assert include_entries(result, "page.php") == [("legacy-apps/tiny/lib.php", False),
                                                   ("legacy-apps/tiny/a.php", True)]  # through b.php to c.php
    names = result["timelines"]["page.php"]["callNames"]
    calls = {names[e["id"]]: e["outputsInside"] for e in result["timelines"]["page.php"]["sequence"]
             if e["kind"] == "call"}
    assert calls == {"quiet": False, "loud": True, "dark": "unknown"}  # its include prints nothing / prints / unknown


def test_a_page_that_prints_only_through_what_it_includes_has_a_timeline(tmp_path):
    files = {"view.php": "<h1>View</h1>", "sample.php": "<?php include('view.php');", "none.php": "<?php $x = 1;"}
    result = run(tmp_path, files)
    assert result["silent"] == ["none.php"]
    assert result["timelines"]["sample.php"]["sequence"][0]["kind"] == "include"
    assert abstains(result, "sample.php") == [("cross_file_output", None, 1)]
    assert result["pages"]["sample.php"]["nodes"] == 0 and result["pages"]["sample.php"]["includes"] == 1


def test_print_r_and_var_export_that_return_their_text_are_not_output(tmp_path):
    files = {"save.php": "<?php file_put_contents('c.php', var_export($s, true)); $t = print_r($s, TRUE);",
             "dump.php": "<?php var_export($s); print_r($s, false);",
             "page.php": "<p>x</p><?php include 'save.php'; include 'dump.php'; ?>"}
    result = run(tmp_path, files)
    assert include_entries(result, "page.php") == [("legacy-apps/tiny/save.php", False),
                                                   ("legacy-apps/tiny/dump.php", True)]


# ---------------------------------------------------------------- Stage 2: what stands at an include


def test_an_include_that_cannot_be_followed_is_an_unresolved_call(tmp_path):
    result = run(tmp_path, {"page.php": "<?php include 'config.php'; ?><p>x</p>"})
    assert abstains(result, "page.php") == [("unresolved_call", None, 1)]
    (todo,) = [t for t in result["stage"]("page.php", 5)["c2todos"] if t["reason"] == "unresolved_call"]
    assert todo["detail"] == "an include whose output is not generated here" and todo["sourceLine"] == 1


def test_an_include_of_a_file_that_prints_outside_a_shell_abstains(tmp_path):
    """Section 5, cross-file output: R-F1 is written for functions, so no rule places an included file's output."""
    files = {"menu.php": "<ul><li>Home</li></ul>", "page.php": "<div><?php include 'menu.php'; ?><p>x</p></div>"}
    result = run(tmp_path, files)
    assert abstains(result, "page.php") == [("cross_file_output", None, 1)]
    text = result["file"]("app/(plain)/page/page.tsx")
    assert 'reason="cross_file_output"' in text and "<ul>" not in text and "Abstained_" not in text
    assert result["pages"]["menu.php"]["role"] == "page"  # the file can still be requested by itself


def test_an_include_that_prints_nothing_leaves_no_trace(tmp_path):
    files = {"lib.php": "<?php function f() { return 1; }", "page.php": "<?php include 'lib.php'; ?><p>x</p>"}
    result = run(tmp_path, files)
    assert abstains(result, "page.php") == [] and "C2Todo id=\"t" not in result["file"]("app/(plain)/page/page.tsx")


# ---------------------------------------------------------------- R-F2 for included files


def test_included_header_and_footer_files_form_a_shell_pair(site):
    """boundary-rules section 4, R-F2: "a header unit H and a footer unit F (functions, or included files)"."""
    (shell,) = site["index"]["shells"]
    assert (shell["name"], shell["group"], shell["props"]) == ("IncTopShell", "(inc-top)", [])
    assert shell["header"] == "legacy-apps/tiny/site/inc/top.php"  # a file unit is named by its entrypoint
    assert {site["pages"][k]["role"] for k in ("site__inc__top.php", "site__inc__bottom.php")} == {"shell_part"}
    assert site["pages"]["site__inc__top.php#top.php+bottom.php"]["role"] == "shell"
    page_node = site["nodes"]("site__users__login.php")[0]
    assert page_node["rules"] == ["R-F2"] and page_node["shell"]["header"] == shell["header"]
    assert not [n for n in site["nodes"]("site__users__login.php") if n["status"] == "abstain"]


def test_the_included_shell_is_generated_like_a_function_shell(site):
    layout = site["file"]("app/(inc-top)/layout.tsx")
    assert "<title>T</title>" in layout and "<body>{children}</body>" in layout
    shell = site["file"]("components/IncTopShell.tsx")
    assert "export function IncTopShell({ children }: { children?: ReactNode }) {" in shell
    assert flat(shell).count('<div className="wrap">{children}</div>') == 1
    login = flat(site["file"]("app/(inc-top)/users/login/page.tsx"))
    assert '<IncTopShell><form><input type="submit" value="Go" /></form></IncTopShell>' in login
    assert not [f for f in site["stage"]("site__users__login.php", 5)["flags"] if f["flag"] == "document_shell"]


def test_header_and_footer_files_that_do_not_close_at_all_are_no_pair(tmp_path):
    """(What phpBlog's admin header leaves to the browser, <head> and <main>, pairs since rules v0.13: see
    tests/test_phase3b.py. A footer that leaves a <div> open still does not.)"""
    files = {"header.php": "<html><head><title>T</title></head><body><div><div>", "footer.php": "</div>",
             "page.php": "<?php include 'header.php'; ?><p>x</p><?php include 'footer.php'; ?>"}
    result = run(tmp_path, files)
    assert result["index"]["shells"] == []
    assert abstains(result, "page.php") == [("tag_crosses_function", "R-F2", 1)] * 2
    assert result["pages"]["page.php"]["group"] == "(plain)"


def test_a_footer_is_paired_only_with_a_header_it_is_called_with(tmp_path):
    """Two footers close the same tags (phpBlog: core.php's footer() and admin/footer.php)."""
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }",
             "admin/top.php": "<html><body><div>", "admin/end.php": "</div></body></html>",
             "page.php": "<?php include 'lib.php'; head(); ?><p>a</p><?php foot(); ?>",
             "admin/home.php": "<?php include 'top.php'; ?><p>b</p><?php include 'end.php'; ?>"}
    result = run(tmp_path, files)
    assert sorted((s["name"], s["group"]) for s in result["index"]["shells"]) == [
        ("AdminTopShell", "(admin-top)"), ("HeadShell", "(head)")]
    assert (result["pages"]["page.php"]["group"], result["pages"]["admin__home.php"]["group"]) == (
        "(head)", "(admin-top)")


def test_two_headers_of_one_name_get_two_shell_names(tmp_path):
    """phpBlog: head() and footer() in core.php and again in install/core.php."""
    pair = "<?php function head() { ?><html><body><div class='%s'><?php }\nfunction foot() { ?></div></body></html><?php }"
    files = {"core.php": pair % "site", "install/core.php": pair % "setup",
             "page.php": "<?php include 'core.php'; head(); ?><p>a</p><?php foot(); ?>",
             "install/index.php": "<?php include 'core.php'; head(); ?><p>b</p><?php foot(); ?>"}
    result = run(tmp_path, files)
    assert [(s["name"], s["group"]) for s in result["index"]["shells"]] == [
        ("HeadShell", "(head)"), ("HeadShell2", "(head-2)")]
    assert (result["pages"]["page.php"]["group"], result["pages"]["install__index.php"]["group"]) == (
        "(head)", "(head-2)")
    assert 'className="setup"' in result["file"]("components/HeadShell2.tsx") and result["index"]["collisions"] == []


def test_the_end_tag_of_a_void_element_is_not_an_unmatched_closing_tag(tmp_path):
    files = {"lib.php": "<?php function top() { ?><html><head><meta charset='x'></meta></head><body><div><?php }\n"
                        "function bottom() { ?></div></body></html><?php }\n"
                        "function line() { ?><b>x</b></br><?php }",
             "page.php": "<?php include 'lib.php'; top(); ?><p>a</p><?php line(); bottom(); ?>"}
    result = run(tmp_path, files)
    loaded = [load_timeline(result["folder"] / key / "timeline.json") for key in result["timelines"]]
    units = {u.name: u for u in app_units.analyse(loaded, load_boundary_config()).functions.values()}
    assert (units["top"].open_tags, units["top"].unmatched) == (("html", "body", "div"), ())
    assert units["line"].component == "Line"  # `</br>` closes nothing: the function is still tag-balanced
    assert [s["name"] for s in result["index"]["shells"]] == ["TopShell"]


def test_output_before_the_header_keeps_the_reason_of_a_gap_that_is_already_one(tmp_path):
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }\nfunction note() { ?><i>n</i><?php }",
             "guard.php": "<?php if (!$ok) { echo '<b>stop</b>'; }",
             "a.php": "<?php include 'lib.php'; include 'guard.php'; head(); ?><p>a</p><?php foot(); ?>",
             "b.php": "<?php include 'lib.php'; note(); head(); ?><p>b</p><?php foot(); ?>"}
    result = run(tmp_path, files)
    assert abstains(result, "a.php") == [("cross_file_output", None, 1)]  # an include that prints: its own reason
    assert abstains(result, "b.php") == [("output_outside_shell", "R-F2", 1)]  # a component would print there
    text = result["file"]("app/(head)/a/page.tsx")
    assert text.index('reason="cross_file_output"') < text.index("<HeadShell")


# ---------------------------------------------------------------- an if whose `then` arm prints nothing


def test_an_if_that_prints_only_in_its_else_arm_is_written_with_an_empty_then(tmp_path):
    result = stages(tmp_path, "<div><?php if ($ok) { $n = 1; } else { echo '<b>no</b>'; } ?></div>")
    text = flat(result["files"]["app/page/page.tsx"])
    (todo,) = [t for t in result["stage5"]["c2todos"] if t["kind"] == "unresolvedCondition"]
    assert (todo["reason"], todo["severity"]) == ("condition_unavailable", "todo")
    assert f'{{unresolvedCondition("{todo["nodeId"]}", "condition_unavailable") ? (null) : (<b>no</b>)}}' in text


def test_an_if_that_prints_only_from_its_elseif_on_keeps_its_structure(tmp_path):
    php = "<div><?php if ($a) { $n = 1; } elseif ($b == 2) { echo '<b>two</b>'; } else { echo '<i>other</i>'; } ?></div>"
    result = stages(tmp_path, php)
    text = flat(result["files"]["app/page/page.tsx"])
    assert text.count("unresolvedCondition(") == 2 and "? (null) :" in text  # then: unavailable; elseif: its own
    assert text.index("(null)") < text.index("<b>two</b>") < text.index("<i>other</i>")


def test_an_attribute_if_that_prints_only_in_its_else_arm_is_omitted(tmp_path):
    php = '<a href="/x" class="<?php if ($on) { $n = 1; } else { echo "off"; } ?>">x</a>'
    result = stages(tmp_path, php)
    assert "className" not in result["files"]["app/page/page.tsx"]
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "attribute_omitted"]
    assert flag["reason"] == "condition_unavailable"


# ---------------------------------------------------------------- evidence for the pattern log (no rule)


def notes(tmp_path, php: str) -> list:
    return [(n["kind"], n["line"]) for n in page(tmp_path, php)["harnessNotes"]
            if n["kind"] in ("exit_after_output", "silent_elseif_arm", "markup_in_assigned_string")]


def test_output_followed_by_exit_is_noted(tmp_path):
    php = "<?php\nif (!$user) {\n  echo '<meta http-equiv=\"refresh\" content=\"0; url=login\">';\n  exit;\n}\n?><p>page</p>"
    assert notes(tmp_path, php) == [("exit_after_output", 4)]
    assert notes(tmp_path, "<?php if (!$u) { header('Location: x'); exit; } ?><p>page</p>") == []  # no output before it


def test_an_elseif_arm_that_prints_nothing_before_one_that_prints_is_noted(tmp_path):
    php = "<?php\nif ($a) { echo 'A'; } elseif ($b) { $n = 1; } else { echo 'C'; }"
    assert notes(tmp_path, php) == [("silent_elseif_arm", 2)]
    assert notes(tmp_path, "<?php if ($a) { echo 'A'; } elseif ($b) { echo 'B'; } else { $n = 1; }") == []


def test_markup_kept_in_a_variable_is_noted(tmp_path):
    php = "<?php\n$image = '<img src=\"a.png\">';\n$active = '';\n$active .= ' class=\"active\"';\n$name = 'x';\n" \
          "echo '<div' . $active . '>' . $image . '</div>';"
    assert notes(tmp_path, php) == [("markup_in_assigned_string", 2), ("markup_in_assigned_string", 4)]


def test_an_expression_where_an_attribute_name_is_written_abstains_inside_an_inline_map_too(tmp_path):
    """Rules section 1 (markup_from_expression), for a row that R-L3 keeps as an inline map."""
    php = "<select><?php foreach ($rows as $r) { echo '<option value=\"' . $r['id'] . '\" ' . $selected . '>x</option>'; } ?></select>"
    result = stages(tmp_path, php)
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["markup_from_expression"]
    assert [t["reason"] for t in result["stage5"]["c2todos"] if t["severity"] == "todo"].count(
        "markup_from_expression") == 1


def test_the_pattern_log_counts_every_if_between_attributes(tmp_path):
    """Step 1 recorded it as a candidate; since rules v0.13 it is R-I3b (tests/test_phase3b.py)."""
    php = "<div><p>kept</p><select><option value=\"a\" <?php if ($v == 'a') { echo 'selected'; } ?>>A</option></select></div>"
    result = stages(tmp_path, php)
    assert not [t for t in result["stage5"]["c2todos"] if t["reason"] == "markup_not_generatable"]
    assert "<select>" in result["files"]["app/page/page.tsx"]
    assert pattern_log.attribute_ifs(result["path"], load_boundary_config()) == [((1, 49), "option", "selected")]


# ---------------------------------------------------------------- phpBlog, the first new discovery app


@pytest.fixture(scope="module")
def phpblog(tmp_path_factory):
    if not phpblog_clone:
        pytest.skip("phpBlog clone not present (legacy-apps/PINS.md)")
    output = tmp_path_factory.mktemp("phpblog")
    tsc = compile_check.unavailable() is None
    return output, run_apps.run_app(harness_app.app_timelines("phpblog"), output, tsc=tsc)


def test_phpblog_is_pinned_and_keeps_its_licence():
    if not phpblog_clone:
        pytest.skip("phpBlog clone not present (legacy-apps/PINS.md)")
    config = harness_app.APPS["phpblog"]
    assert harness_app.head_commit(config["root"]) == config["commit"] == "4ffe3c9641a42b99829cf4b8e4185093793a3abc"
    assert "GNU GENERAL PUBLIC LICENSE" in (config["root"] / "LICENSE").read_text(encoding="utf-8")
    pins = (ROOT / "legacy-apps" / "PINS.md").read_text(encoding="utf-8")
    assert config["commit"] in pins and "GPL-3.0" in pins and "codeplay-code/phpBlog-blogScript" in pins


def test_phpblog_runs_whole_without_a_crash_and_compiles(phpblog):
    _, index = phpblog
    assert index["files"] == 51 and index["parseErrors"] == {} and index["collisions"] == []  # with config.php
    assert [p["key"] for p in index["pages"] if p["crash"]] == []
    assert index["schemaFiles"] == ["install/database.sql"]  # SELECT * is expanded from its own schema
    assert [(s["name"], s["group"]) for s in index["shells"]] == [
        ("HeadShell", "(head)"), ("HeadShell2", "(head-2)"), ("AdminHeaderShell", "(admin-header)")]
    assert {"app/(head)/page.tsx", "app/(head)/layout.tsx", "app/(head)/post/page.tsx", "components/Sidebar.tsx",
            "app/(head-2)/install/page.tsx", "app/(plain)/admin/page.tsx", "components/AdminHeaderShell.tsx",
            "app/(admin-header)/layout.tsx", "app/(admin-header)/admin/dashboard/page.tsx"} <= set(index["appFiles"])
    assert [(n["path"], n["reason"]) for n in index["notGenerated"]] == [
        ("rss.php", "non_html_response"), ("sitemap.php", "non_html_response")]
    assert not [f for f in index["appFiles"] if "rss" in f or "sitemap" in f]
    if compile_check.unavailable() is None:
        assert index["tscRan"] and index["tscUnattributed"] == 0, index["tscOutput"]
        assert [(p["key"], p["tsc"]) for p in index["pages"]
                if p["role"] != "shell_part" and not p.get("notGenerated") and p["tsc"] != 0] == []


def test_phpblog_admin_header_and_footer_files_pair_after_implicit_closes(phpblog):
    """Rules v0.13, R-F2 refined. Step 1: no pair as the rule was written, 48 tag_crosses_function."""
    output, index = phpblog
    admin = [p for p in index["pages"] if p["path"].startswith("admin/") and p["role"] == "page"]
    assert len(admin) == 25 and sorted({p["group"] for p in admin}) == ["(admin-header)", "(plain)"]
    assert [p["path"] for p in admin if p["group"] == "(plain)"] == ["admin/index.php"]  # the login page: no include
    reasons = {}
    for entry in index["pages"]:
        if entry["role"] == "shell_part" or entry.get("notGenerated"):
            continue
        stage2 = json.loads((output / "phpblog" / entry["key"] / "stage2.json").read_text(encoding="utf-8"))
        for node in apps_results_table._walk(stage2["tree"]):
            if node["status"] == "abstain":
                reasons[node["reason"]] = reasons.get(node["reason"], 0) + 1
    # cross_file_output 24 (step 2) -> 0 (step 3): config.php and core.php print only in exit arms (R-I8c)
    assert "tag_crosses_function" not in reasons and "cross_file_output" not in reasons
    shell = (output / "phpblog" / "next-app" / "components" / "HeadShell.tsx").read_text(encoding="utf-8")
    assert shell.count("\n") > 100 and "{children}" in shell  # the public shell is generated, not one C2Todo


def test_the_pattern_log_has_a_phpblog_section_that_does_not_decide(phpblog, tmp_path):
    output, _ = phpblog
    apps = pattern_log.APPS
    pattern_log.APPS = ("phpblog",)
    try:
        log = pattern_log.render(output, tmp_path / "log.md")
        assert log == pattern_log.render(output, tmp_path / "log.md")  # deterministic
    finally:
        pattern_log.APPS = apps
    assert "phpblog: constructs against the §8 definition of a new pattern (Phase 3)" in log
    for heading in ("### (a) New patterns (the owner's verdict, rules v0.13) and what each is now",
                    "### (b) Existing patterns with new instances",
                    "### (c) Implementation gaps fixed for this app (not new patterns)",
                    "### (d) Unresolved reads, missing labels, no endpoint only"):
        assert heading in log
    assert "Attribute-presence conditional (R-I3b)" in log and "**This table does not decide**" in log
    assert "Redirect guard (R-I8, case 1) — **" in log and "Unit that is not an HTML response — **2**" in log
    assert "`phpblog/admin/comments.php:67:" in log and "prints `selected`" in log  # every such `if`
