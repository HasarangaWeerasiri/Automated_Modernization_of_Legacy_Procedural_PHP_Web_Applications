"""Roadmap Phase 2b: functions and shell (boundary-rules v0.11 section 4, generation-spec v0.6 section 8a),
and the three Phase 2a follow-ups: per-part ids, R-I7, browser-equivalent markup (spec section 5b).

A small app is built from PHP sources, run as a whole (tools/harness/run_apps.py) and its stage
outputs and generated Next.js files are checked: every row of the call-entry table, R-F1, R-F1b,
R-F2 with route groups, and both abstains.
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
import run_apps  # noqa: E402
from ast_producer import SourceFile, parse_sources  # noqa: E402
from harness import FileHarness  # noqa: E402

from src.mapping import app_units  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.loader import load_timeline  # noqa: E402
from tests.test_harness import harness_app, pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402

LIB = """<?php
function site_header($selected = "", $title = "Tiny") { ?><html lang="en"><head><title>Tiny</title>
<link rel="stylesheet" href="/site.css"><script>var a = 1;</script></head><body class="site"><div class="wrap">
<ul><li class="<?php if ($selected == "home") { echo 'current'; } ?>">Home</li></ul><?php }
function site_footer() { ?><p>bye</p></div></body></html><?php }
function badge($kind, $n, $extra = null) { ?><span class="badge"><?php echo $kind; ?></span><?php }
function options() {
  $r = mysqli_query($c, "select name from users");
  while ($row = mysqli_fetch_array($r)) { ?><option><?php echo $row['name']; ?></option><?php }
}
function loud($text) { $text = strtoupper($text); ?><b><?php echo $text; ?></b><?php }
function again() { ?><i>x</i><?php again(); }
function quiet() { return 1; }
"""
APP = {
    "lib.php": LIB,
    "home.php": "<?php require 'lib.php'; site_header(\"home\"); ?><h1>Home</h1><select><?php options(); ?></select>"
                "<?php badge(\"new\", 3); loud(\"hi\"); quiet(); $obj->draw(); site_footer(); ?>",
    "about.php": "<?php require 'lib.php'; site_header(); ?><p>About</p><?php site_footer(); ?>",
    "sub/guard.php": "<?php require '../lib.php'; if ($ok) { site_header(\"home\"); ?><p>guarded</p>"
                     "<?php site_footer(); } ?>",
    "plain.php": "<html><body><p>Plain</p><?php badge(\"old\", 1); again(); ?></body></html>",
    "broken.php": "<?php site_header(\"x\"); ?><p>no footer</p>",
    "outside.php": "<p>before</p><?php site_header(\"y\"); ?><p>in</p><?php site_footer(); ?>",
    "nested.php": "<?php if ($a) { ?><p>a</p><?php } if ($b) { site_header(\"z\"); ?><p>b</p><?php site_footer(); } ?>",
}


def run(tmp_path, files: dict, tsc: bool = False) -> dict:
    """Run PHP files as one app: its index, and loaders for each unit's stage outputs and the generated app."""
    sources = []
    for index, (name, php) in enumerate(sorted(files.items()), start=1):
        (tmp_path / "src" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / name).write_bytes(php.encode("utf-8"))
        sources.append((name, SourceFile(tmp_path / "src" / name, f"t{index:03d}", f"legacy-apps/tiny/{name}")))
    asts, _ = parse_sources([s for _, s in sources])
    harnesses = {name: FileHarness(s.source.read_bytes(), ast, s.path, {"users": ["id", "name"]})
                 for (name, s), ast in zip(sources, asts)}
    pages, silent = harness_app.build_timelines(harnesses, lambda path: {"generator": "c2-harness"})
    timelines = harness_app.AppTimelines("tiny", tuple(pages), tuple(silent), {}, len(files))
    index = run_apps.run_app(timelines, tmp_path / "out", tsc=tsc)
    folder = tmp_path / "out" / "tiny"

    def stage(key: str, number: int) -> dict:
        return json.loads((folder / key / f"stage{number}.json").read_text(encoding="utf-8"))

    def nodes(key: str) -> list:
        return list(apps_results_table._walk(stage(key, 2)["tree"]))

    def file(path: str) -> str:
        return (folder / run_apps.NEXT_APP / path).read_text(encoding="utf-8")

    return {"index": index, "folder": folder, "stage": stage, "nodes": nodes, "file": file,
            "pages": {p["key"]: p for p in index["pages"]}, "timelines": {p.key: p.timeline for p in pages}}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return run(tmp_path_factory.mktemp("tiny"), APP, tsc=compile_check.unavailable() is None)


# ---------------------------------------------------------------- harness: call resolution, arguments, parameters

THREE = {
    "a.php": "<?php function options() { echo '<option>a</option>'; }",
    "b.php": "<?php function options() { echo '<option>b</option>'; }",
    "inc/c.php": "<?php function options() { echo '<option>c</option>'; } include 'd.php';",
    "inc/d.php": "<?php function helper() { echo '<i>h</i>'; }",
}


def callee_of(result: dict, key: str, name: str):
    timeline = result["timelines"][key]
    (call,) = [e for e in timeline["sequence"]
               if e["kind"] == "call" and timeline["callNames"].get(e["id"]) == name]  # a method call has no name
    return call


def test_a_name_defined_in_several_files_resolves_through_the_pages_literal_includes(tmp_path):
    """boundary-rules v0.11 section 4 (HMS display_docs(): three definitions, the page includes one)."""
    result = run(tmp_path, THREE | {"page.php": "<select><?php include('b.php'); options(); ?></select>"})
    function = result["timelines"]["b.php#options"]["function"]
    assert callee_of(result, "page.php", "options")["callee"] == function["nodeId"]
    assert result["pages"]["b.php#options"]["role"] == "component"
    assert {result["pages"][k]["role"] for k in ("a.php#options", "inc__c.php#options")} == {"not_called"}


def test_includes_are_followed_transitively_and_relative_to_the_including_file(tmp_path):
    result = run(tmp_path, THREE | {"page.php": "<div><?php require 'inc/c.php'; options(); helper(); ?></div>"})
    assert callee_of(result, "page.php", "options")["callee"] == \
        result["timelines"]["inc__c.php#options"]["function"]["nodeId"]
    assert callee_of(result, "page.php", "helper")["outputsInside"] is True  # c.php includes d.php


@pytest.mark.parametrize("page", [
    "<select><?php options(); ?></select>",  # no include: nothing narrows the three definitions
    "<select><?php include 'a.php'; include 'b.php'; options(); ?></select>",  # two reachable definitions
    "<select><?php include $file; options(); ?></select>",  # not a literal include
])
def test_a_call_that_the_includes_do_not_narrow_to_one_definition_has_no_callee(tmp_path, page):
    result = run(tmp_path, THREE | {"page.php": page})
    call = callee_of(result, "page.php", "options")
    assert (call["callee"], call["outputsInside"]) == (None, True)  # every definition prints
    assert [n["reason"] for n in result["nodes"]("page.php") if n["status"] == "abstain"] == ["unresolved_call"]


def test_call_entries_carry_their_arguments_and_function_timelines_their_parameters(tiny):
    call = callee_of(tiny, "home.php", "badge")
    assert call["args"] == [{"kind": "literal", "type": "string", "value": "new"},
                            {"kind": "literal", "type": "number", "value": 3}]
    assert callee_of(tiny, "home.php", "site_header")["args"] == [
        {"kind": "literal", "type": "string", "value": "home"}]
    function = tiny["timelines"]["lib.php#site_header"]["function"]
    assert function["name"] == "site_header" and function["params"] == [
        {"name": "$selected", "default": {"kind": "literal", "type": "string", "value": ""}, "readOnly": True},
        {"name": "$title", "default": {"kind": "literal", "type": "string", "value": "Tiny"}, "readOnly": True}]
    loud = tiny["timelines"]["lib.php#loud"]["function"]["params"]
    assert loud == [{"name": "$text", "default": None, "readOnly": False}]  # it assigns $text
    loaded = load_timeline(tiny["folder"] / "home.php" / "timeline.json")
    assert [a.value for c in loaded.calls for a in c.args or () if a.kind == "literal"][:1] == ["home"]
    assert load_timeline(tiny["folder"] / "lib.php#badge" / "timeline.json").function.params[0].read_only is True


@pytest.mark.parametrize("change, message", [
    (lambda d: d["function"].pop("params"), "function needs exactly"),
    (lambda d: d["function"]["params"][0].update(name=3), "parameter"),
    (lambda d: d["function"]["params"][0].update(default={"kind": "literal", "value": 1}), "is not"),
])
def test_the_loader_rejects_a_malformed_function_block(tiny, tmp_path, change, message):
    doc = json.loads(json.dumps(tiny["timelines"]["lib.php#site_header"]))
    change(doc)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)


def test_the_loader_rejects_a_malformed_argument(tiny, tmp_path):
    doc = json.loads(json.dumps(tiny["timelines"]["home.php"]))
    next(e for e in doc["sequence"] if e["kind"] == "call")["args"] = [{"kind": "literal", "type": "bool", "value": 1}]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="is not"):
        load_timeline(path)


# ---------------------------------------------------------------- app units: components, props, shell pairs


@pytest.fixture(scope="module")
def units(tiny):
    loaded = [load_timeline(tiny["folder"] / key / "timeline.json") for key in tiny["timelines"]]
    found = app_units.analyse(loaded, load_boundary_config())
    return {unit.name: unit for unit in found.functions.values()}, found


def test_a_called_tag_balanced_function_is_a_component(units):
    by_name, _ = units
    assert {name: unit.component for name, unit in by_name.items()} == {
        "badge": "Badge", "options": "Options", "loud": "Loud", "again": None, "site_header": None,
        "site_footer": None}
    assert by_name["again"].recursive and by_name["again"].balanced


def test_a_parameter_is_a_typed_prop_only_when_it_is_read_only_and_literal_at_every_call_site(units):
    by_name, _ = units
    assert by_name["badge"].props == (("kind", "string"), ("n", "number"))  # $extra: default null, not a literal
    assert by_name["loud"].props == ()  # literal at its call site, but the function assigns it
    assert by_name["site_header"].props == (("selected", "string"), ("title", "string"))  # title: its default


def test_a_non_literal_argument_at_one_call_site_means_no_prop(tmp_path):
    files = {"lib.php": "<?php function tag($k) { ?><b><?php echo $k; ?></b><?php }",
             "a.php": "<p><?php tag('x'); ?></p>", "b.php": "<p><?php tag($_GET['k']); ?></p>"}
    result = run(tmp_path, files)
    loaded = [load_timeline(result["folder"] / key / "timeline.json") for key in result["timelines"]]
    (unit,) = app_units.analyse(loaded, load_boundary_config()).functions.values()
    assert (unit.component, unit.props) == ("Tag", ())
    assert 'reason="unresolved_read"' in result["file"]("components/Tag.tsx")  # reads of it stay unresolved


def test_a_header_and_footer_whose_tags_match_form_a_shell_pair(units):
    by_name, found = units
    header, footer = by_name["site_header"], by_name["site_footer"]
    assert (header.open_tags, header.unmatched) == (("html", "body", "div"), ())
    assert (footer.open_tags, footer.unmatched) == ((), ("div", "body", "html"))  # closes exactly S, in reverse
    (shell,) = found.shells.values()
    assert (shell.name, shell.group, shell.header, shell.footer) == (
        "SiteHeaderShell", "(site_header)", header.node_id, footer.node_id)
    assert [(n, t) for n, t, _ in shell.props] == [("selected", "string"), ("title", "string")]


def test_a_footer_that_closes_other_tags_is_no_pair(tmp_path):
    files = {"lib.php": "<?php function top() { ?><div><section><?php } function bottom() { ?></div></div><?php }",
             "page.php": "<?php top(); ?><p>x</p><?php bottom(); ?>"}
    result = run(tmp_path, files)
    assert result["index"]["shells"] == []
    reasons = [n["reason"] for n in result["nodes"]("page.php") if n["status"] == "abstain"]
    assert reasons == ["tag_crosses_function", "tag_crosses_function"]  # both calls: no pair to stand in


# ---------------------------------------------------------------- Stage 2: the call-entry table, R-F1, R-F1b, R-F2


def test_call_entry_table(tiny):
    nodes = tiny["nodes"]("home.php")
    calls = {n["sourceIds"][0]: n for n in nodes if n["type"] == "FunctionCall" or n["reason"] in (
        "unresolved_call", "recursive_function", "tag_crosses_function")}
    names = tiny["timelines"]["home.php"]["callNames"]
    by_name = {names.get(call_id, "method call"): (n["type"], n["rule"], n["reason"]) for call_id, n in calls.items()}
    assert by_name == {
        "options": ("FunctionCall", "R-F1", "function_component"),  # resolved, prints -> the R-F1 component
        "badge": ("FunctionCall", "R-F1", "function_component"),
        "loud": ("FunctionCall", "R-F1", "function_component"),
        "method call": ("Abstain", None, "unresolved_call"),  # $obj->draw(): no callee, output unknown
    }
    assert "quiet" not in by_name  # outputsInside false: nothing at the call site
    assert [n["callee"] for n in nodes if n["type"] == "FunctionCall"][0] == \
        tiny["timelines"]["lib.php#options"]["function"]["nodeId"]


def test_a_recursive_function_abstains_at_its_call_site(tiny):
    reasons = [(n["reason"], n["rule"]) for n in tiny["nodes"]("plain.php") if n["status"] == "abstain"]
    assert reasons == [("recursive_function", "R-F1")]
    assert 'reason="recursive_function"' in tiny["file"]("app/(plain)/plain/page.tsx")


def test_a_page_between_header_and_footer_is_wrapped_in_the_shell(tiny):
    for key in ("home.php", "about.php", "sub__guard.php", "outside.php"):
        page = tiny["nodes"](key)[0]
        assert page["type"] == "Page" and "R-F2" in page["rules"] and page["shell"]["header"], key
        assert tiny["pages"][key]["group"] == "(site_header)"
    assert tiny["nodes"]("sub__guard.php")[0]["rules"] == ["R-I1", "R-F2"]  # inside the page guard counts as top level


def test_a_header_without_its_footer_abstains(tiny):
    nodes = tiny["nodes"]("broken.php")
    assert "shell" not in nodes[0] and tiny["pages"]["broken.php"]["group"] == "(plain)"
    assert [(n["reason"], n["rule"]) for n in nodes if n["status"] == "abstain"] == [("tag_crosses_function", "R-F2")]


def test_a_header_called_inside_a_branch_that_is_not_the_page_guard_abstains(tiny):
    nodes = tiny["nodes"]("nested.php")
    assert "shell" not in nodes[0]
    assert [n["reason"] for n in nodes if n["status"] == "abstain"] == ["tag_crosses_function"] * 2


def test_output_before_the_header_abstains(tiny):
    nodes = tiny["nodes"]("outside.php")
    assert [(n["reason"], n["rule"]) for n in nodes if n["status"] == "abstain"] == [("output_outside_shell", "R-F2")]
    page = tiny["file"]("app/(site_header)/outside/page.tsx")
    assert page.index('reason="output_outside_shell"') < page.index("<SiteHeaderShell")  # outside the shell


def test_a_function_loop_whose_container_the_caller_opens_is_a_list_without_wrapper(tiny):
    """R-F1b: replaces container_undetermined for these loops."""
    nodes = tiny["nodes"]("lib.php#options")
    (lst,) = [n for n in nodes if n["type"] == "List"]
    assert lst["rules"] == ["R-L1", "R-F1b", "R-L3"] and lst["container"]["hasWrapper"] is False
    assert not [n for n in nodes if n["status"] == "abstain"]
    component = tiny["file"]("components/OptionsList1.tsx")
    assert "{items.map((row, i) => (" in component and "<option key={i}>" in component
    assert "<select>" not in component and "<Options />" in tiny["file"]("app/(site_header)/home/page.tsx")


def test_the_same_loop_in_a_page_still_abstains(tmp_path):
    result = stages(tmp_path, "<?php $r = mysqli_query($c, 'select name from users');\n"
                              "while ($row = mysqli_fetch_array($r)) { ?><option>x</option><?php }")
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["container_undetermined"]


def test_a_timeline_on_its_own_ignores_calls(tmp_path):
    """Without the app's units (a mock, src/main.py) call entries are not looked at."""
    result = stages(tmp_path, "<?php function f() { echo '<b>x</b>'; } ?><p><?php f(); ?></p>")
    assert not [n for n in result["nodes"] if n["type"] == "FunctionCall" or n["status"] == "abstain"]
    assert "app/page/page.tsx" in result["files"]


# ---------------------------------------------------------------- Stage 5: components, shell, route groups


def test_a_function_component_is_written_once_and_called_with_its_literal_arguments(tiny):
    badge = tiny["file"]("components/Badge.tsx")
    assert "export function Badge({ kind, n }: { kind: string; n: number }) {" in badge
    assert '<span className="badge">{kind}</span>' in badge  # a read of a typed prop is its value
    assert '<Badge kind="new" n={3} />' in tiny["file"]("app/(site_header)/home/page.tsx")
    assert '<Badge kind="old" n={1} />' in tiny["file"]("app/(plain)/plain/page.tsx")
    loud = tiny["file"]("components/Loud.tsx")
    assert "export function Loud() {" in loud and 'reason="unresolved_read"' in loud  # $text is not a prop


def test_an_unresolved_call_is_a_c2todo_at_the_call_site(tiny):
    page = tiny["file"]("app/(site_header)/home/page.tsx")
    assert page.count('reason="unresolved_call"') == 1 and "Abstained_" not in page
    (todo,) = [t for t in tiny["stage"]("home.php", 5)["c2todos"] if t["reason"] == "unresolved_call"]
    assert todo["severity"] == "todo"


def test_the_shell_component_holds_children_and_translates_the_parameter_condition(tiny):
    shell = tiny["file"]("components/SiteHeaderShell.tsx")
    assert ("export function SiteHeaderShell({ children, selected, title }: "
            "{ children?: ReactNode; selected: string; title: string }) {") in shell
    assert shell.index('<div className="wrap">') < shell.index("{children}") < shell.index("<p>bye</p>")
    assert 'selected == "home" ? `current` : ""' in shell  # spec 8a: the parameter is a typed prop
    assert "<html" not in shell and "<body" not in shell and "<title>" not in shell
    assert tiny["pages"]["lib.php#site_header+site_footer"]["role"] == "shell"
    assert {tiny["pages"][k]["role"] for k in ("lib.php#site_header", "lib.php#site_footer")} == {"shell_part"}


def test_the_shells_route_group_has_a_root_layout_from_the_headers_literal_document_parts(tiny):
    layout = tiny["file"]("app/(site_header)/layout.tsx")
    assert "export default function RootLayout({ children }: { children: ReactNode }) {" in layout
    assert '<html lang="en">' in layout and '<body className="site">{children}</body>' in layout
    assert "<title>Tiny</title>" in layout and '<link rel="stylesheet" href="/site.css" />' in layout
    assert "<script" not in layout  # inline JS is dropped and flagged, there as everywhere
    flags = tiny["stage"]("lib.php#site_header+site_footer", 5)["flags"]
    assert [f["file"] for f in flags if f["flag"] == "inline_js_dropped"] == ["app/(site_header)/layout.tsx"]
    assert not [f for f in flags if f["flag"] == "document_shell"]


def test_pages_are_wrapped_with_their_own_arguments(tiny):
    home = tiny["file"]("app/(site_header)/home/page.tsx")
    assert '<SiteHeaderShell selected="home" title="Tiny">' in home and "<h1>Home</h1>" in home
    assert '<SiteHeaderShell selected="" title="Tiny">' in tiny["file"]("app/(site_header)/about/page.tsx")
    assert '<SiteHeaderShell selected="home" title="Tiny">' in tiny["file"]("app/(site_header)/sub/guard/page.tsx")
    assert 'from "../../../../components/SiteHeaderShell"' in tiny["file"]("app/(site_header)/sub/guard/page.tsx")
    assert not [f for f in tiny["stage"]("home.php", 5)["flags"] if f["flag"] == "document_shell"]


def test_every_other_page_is_in_the_plain_group_with_a_minimal_layout(tiny):
    files = set(tiny["index"]["appFiles"])
    assert {"app/(plain)/layout.tsx", "app/(plain)/plain/page.tsx", "app/(plain)/broken/page.tsx",
            "app/(plain)/nested/page.tsx"} <= files
    assert "<body>{children}</body>" in tiny["file"]("app/(plain)/layout.tsx")
    flags = tiny["stage"]("plain.php", 5)["flags"]
    assert {f["flag"] for f in flags} >= {"document_shell"}  # its own <html> / <body>: still flagged
    assert not [f for f in files if "lib.php" in f]  # a function is a component, never a route
    assert tiny["index"]["collisions"] == []


def test_the_whole_app_compiles_as_one_program(tiny):
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    assert tiny["index"]["tscRan"] and tiny["index"]["tscUnattributed"] == 0, tiny["index"]["tscOutput"]
    assert {p["key"]: p["tsc"] for p in tiny["index"]["pages"] if p["role"] != "shell_part" and p["tsc"]} == {}


def test_a_type_error_in_the_assembled_app_is_reported_by_file(tiny, tmp_path):
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    import shutil

    shutil.copytree(tiny["folder"] / run_apps.NEXT_APP, tmp_path / "app")
    page = tmp_path / "app" / "app" / "(plain)" / "plain" / "page.tsx"
    page.write_text(page.read_text(encoding="utf-8").replace('kind="old"', "kind={7}"), encoding="utf-8")
    errors, _ = compile_check.check_app("phase2b_planted", tmp_path / "app")
    assert errors == {"app/(plain)/plain/page.tsx": 1}


# ---------------------------------------------------------------- R-I7: only inline JS


def test_an_if_whose_only_output_is_inline_js_is_not_a_boundary(tmp_path):
    php = "<div><?php if (isset($_POST['go'])) { if ($ok) { echo \"<script>alert('a');</script>\"; } else {\n" \
          "echo \"<script>alert('b');</script><script>x()</script><script>y()</script><script>z()</script>\"; } }\n" \
          "?><p>page</p></div>"
    result = stages(tmp_path, php)
    # 4 elements in one branch, but none is rendered. The `if` is not labelled by Stage 1, and that review
    # carries to the node built from it (CONTEXT.md rules 3 and 4), as for every other rule.
    assert [(n["type"], n["rule"], n["status"], n["reason"]) for n in result["nodes"]
            if n["rule"] in ("R-I7", "R-I5", "R-I4")] == [("Static", "R-I7", "review", "unlabelled")]
    assert not [n for n in result["nodes"] if n["type"] in ("ConditionalComponent", "InlineConditional")]
    dropped = [f for f in result["stage5"]["flags"] if f["flag"] == "inline_js_dropped"]
    assert len(dropped) == 5 and "unresolvedCondition" not in result["files"]["app/page/page.tsx"]


def test_a_loop_whose_only_output_is_inline_js_is_not_a_boundary(tmp_path):
    result = stages(tmp_path, "<ul><?php foreach ($l as $v) { ?><script>track();</script><?php } ?></ul>")
    assert [(n["type"], n["rule"]) for n in result["nodes"] if n["rule"]] == [("Static", "R-I7")]
    assert not [n for n in result["nodes"] if n["type"] == "List"]


@pytest.mark.parametrize("body", ["echo \"<script>a()</script>done\";", "echo \"<script>a()</script><b>x</b>\";",
                                  "echo $message;"])
def test_an_if_that_also_prints_something_visible_is_still_judged_by_the_other_rules(tmp_path, body):
    result = stages(tmp_path, f"<div><?php if ($ok) {{ {body} }} ?></div>")
    assert [n["rule"] for n in result["nodes"] if n["rule"]] == ["R-I4"]


# ---------------------------------------------------------------- spec 5b: browser-equivalent markup


def flag_names(result: dict) -> list:
    return sorted(f["flag"] for f in result["stage5"]["flags"] if f["flag"] in (
        "implicit_tbody", "implicit_p_close", "duplicate_attribute", "html_nesting_changed"))


def test_rows_directly_in_a_table_get_the_tbody_a_browser_builds(tmp_path):
    php = "<table><tr><th>N</th></tr><?php foreach ($rows as $r) { ?><tr><td>x</td></tr><?php } ?></table>"
    result = stages(tmp_path, php)
    component = result["files"]["components/PageList1.tsx"]
    assert component.index("<table>") < component.index("<tbody>") < component.index("<tr>")
    assert component.index("{items.map(") < component.index("</tbody>")  # the loop's rows are in the same tbody
    assert flag_names(result) == ["implicit_tbody"]
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "implicit_tbody"]
    assert flag["severity"] == "info"


def test_a_table_with_only_loop_rows_is_repaired_too(tmp_path):
    """Spec v0.7 section 5b: a List whose Item root is statically <tr>; the .map() goes inside the <tbody>."""
    result = stages(tmp_path, "<table><?php foreach ($rows as $r) { ?><tr><td>x</td></tr><?php } ?></table>")
    component = result["files"]["components/PageList1.tsx"]
    assert component.index("<tbody>") < component.index("{items.map(") < component.index("</tbody>")
    assert flag_names(result) == ["implicit_tbody"]


def test_a_loop_whose_row_is_not_statically_a_tr_is_not_repaired(tmp_path):
    php = "<table><?php foreach ($rows as $r) { if ($r) { ?><tr><td>x</td></tr><?php } } ?></table>"
    result = stages(tmp_path, php)
    assert "<tbody>" not in "".join(result["files"].values())
    assert flag_names(result) == ["html_nesting_changed"]


def test_foster_parenting_is_never_repaired(tmp_path):
    result = stages(tmp_path, "<table><form><tr><td><input name='a'></td></tr></form></table>")
    assert "<tbody>" not in result["files"]["app/page/page.tsx"] and flag_names(result) == ["html_nesting_changed"]
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "html_nesting_changed"]
    assert flag["severity"] == "review"


def test_a_paragraph_is_closed_before_a_block_element_and_its_end_tag_makes_an_empty_one(tmp_path):
    result = stages(tmp_path, "<div><p>intro <b>x</b><h4>Head</h4> tail</p><p>next</p></div>")
    page = "".join(line.strip() for line in result["files"]["app/page/page.tsx"].splitlines())
    # the <h4> closes the <p>; " tail" follows it in the <div>; the written </p> finds no open <p> and makes one
    assert '<div><p>{"intro "}<b>x</b></p><h4>Head</h4>{" tail"}<p /><p>next</p></div>' in page
    assert flag_names(result) == ["implicit_p_close"]
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "implicit_p_close"]
    assert flag["severity"] == "info"


def test_a_paragraph_that_is_never_closed_gets_no_empty_one(tmp_path):
    result = stages(tmp_path, "<div><p>intro<ul><li>a</li></ul></div>")
    page = result["files"]["app/page/page.tsx"]
    assert "<p />" not in page and flag_names(result) == ["implicit_p_close"]
    assert page.index("<p>intro</p>") < page.index("<ul>")


def test_a_repair_is_not_made_across_an_if(tmp_path):
    php = "<div><p><?php if ($strong) { ?><h3>Strong</h3><?php } ?></p></div>"
    result = stages(tmp_path, php)
    assert flag_names(result) == ["html_nesting_changed"]  # what closes the <p> is inside a branch


def test_a_repair_is_not_made_across_an_expression(tmp_path):
    result = stages(tmp_path, "<div><p><?php echo $lead; ?><h4>Head</h4></p></div>")
    assert flag_names(result) == ["html_nesting_changed"]


def test_valid_markup_is_left_alone(tmp_path):
    result = stages(tmp_path, "<div><p>a <b>b</b></p><table><tbody><tr><td>x</td></tr></tbody></table></div>")
    assert flag_names(result) == []


def test_the_first_of_two_equal_attributes_is_kept(tmp_path):
    result = stages(tmp_path, '<div class="a" id="x" class="b">t</div>')
    page = result["files"]["app/page/page.tsx"]
    assert 'className="a"' in page and 'className="b"' not in page
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "duplicate_attribute"]
    assert flag["severity"] == "info"


# ---------------------------------------------------------------- per-part ids (spec v0.6)


def test_each_expression_part_has_its_own_id_and_position(tmp_path):
    php = "<table><tbody><?php foreach ($rows as $r) {\n" \
          "  echo \"<tr><td>$a</td>\n        <td>$b</td></tr>\";\n" \
          "} ?></tbody></table>"
    result = stages(tmp_path, php)
    (echo,) = [e for e in result["timeline"]["sequence"] if e["kind"] == "Stmt_Echo"]
    parts = [p for p in echo["parts"] if p["kind"] == "expr"]
    todos = [t for t in result["stage5"]["c2todos"] if t["reason"] == "unresolved_read"]
    assert [t["nodeId"] for t in todos] == [p["nodeId"] for p in parts]  # the part's AST node, not the echo's id
    assert echo["id"] not in [t["nodeId"] for t in todos] and len({t["nodeId"] for t in todos}) == 2
    assert [(t["sourceLine"], t["sourceCol"]) for t in todos] == [(2, 17), (3, 13)]  # where $a and $b are written
    item = result["files"]["components/PageList1Item.tsx"]
    assert all(f'id="{p["nodeId"]}"' in item for p in parts)
    escaping = [f["nodeId"] for f in result["stage5"]["flags"] if f["flag"] == "escaping_changed"]
    assert escaping == [p["nodeId"] for p in parts]
