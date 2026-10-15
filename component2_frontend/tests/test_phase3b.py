"""Roadmap Phase 3, step 2: the rules phpBlog added (boundary-rules v0.13, generation-spec v0.8).

R-I3b (attribute-presence conditional) and the start-tag fallback, R-I2b (multi-loop list guard), R-I8
(exit-terminated arm, both cases), `non_html_response`, R-L4's `outer` prop, R-F2 pairing after HTML5
implicit closes, implied end tags, and the phpBlog analysis substitution.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))

from src.mapping import app_units  # noqa: E402
from src.mapping import tags as tagtrack  # noqa: E402
from src.mapping.loader import load_timeline  # noqa: E402
from tests.test_harness import harness_app, page, phpblog_clone, pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402
from tests.test_phase3 import abstains, flat, run  # noqa: E402


def rules(result: dict) -> list:
    return [(n["type"], n["rule"], n["status"]) for n in result["nodes"] if n["rule"] in (
        "R-I3", "R-I3b", "R-I2b", "R-I8", "R-I4", "R-I5")]


def flags(result: dict, *names: str) -> list:
    return [(f["flag"], f["reason"], f["severity"]) for f in result["stage5"]["flags"] if f["flag"] in names]


def page_text(result: dict) -> str:
    return flat(result["files"]["app/page/page.tsx"])


def conditions(result: dict) -> list:
    return [t["reason"] for t in result["stage5"]["c2todos"] if t["kind"] == "unresolvedCondition"]


# ---------------------------------------------------------------- R-I3b: an if between the attributes of a start tag


def test_an_if_that_prints_a_boolean_attribute_is_a_conditional_prop(tmp_path):
    result = stages(tmp_path, '<input type="text" name="u" <?php if ($error == 1) { echo \'autofocus\'; } ?> required>')
    assert rules(result) == [("AttributeExpression", "R-I3b", "review")]  # review: the if has no label
    (todo,) = [t for t in result["stage5"]["c2todos"] if t["kind"] == "unresolvedCondition"]
    assert (f'<input type="text" name="u" autoFocus={{unresolvedCondition("{todo["nodeId"]}", '
            '"condition_not_translatable")} required />') in page_text(result)
    assert "markup_not_generatable" not in json.dumps(result["stage5"])


def test_an_if_that_prints_an_attribute_with_a_value_is_a_conditional_prop(tmp_path):
    result = stages(tmp_path, "<ul><li <?php if ($here) { echo 'class=\"nav-item\"'; } ?>>Home</li></ul>")
    assert rules(result) == [("AttributeExpression", "R-I3b", "review")]
    assert '<li className={unresolvedCondition(' in page_text(result) and ') ? "nav-item" : undefined}>Home</li>' in \
        page_text(result)


def test_the_condition_of_a_conditional_prop_is_translated_by_the_whitelist(tmp_path):
    """The whitelist (spec section 6) knows a typed prop of a function component."""
    files = {"lib.php": "<?php function item($page) { ?><li <?php if ($page == 'home') { echo 'class=\"current\"'; }"
                        " ?>>Home</li><?php }",
             "page.php": "<?php include 'lib.php'; ?><ul><?php item('home'); ?></ul>"}
    result = run(tmp_path, files)
    assert '<li className={page == "home" ? "current" : undefined}>Home</li>' in flat(
        result["file"]("components/Item.tsx"))


def test_checked_printed_by_an_if_is_default_checked(tmp_path):
    result = stages(tmp_path, '<input type="checkbox" name="a" value="1" <?php if ($on) { echo "checked"; } ?>>')
    assert "defaultChecked={unresolvedCondition(" in page_text(result) and 'value="1"' in page_text(result)


def test_selected_on_an_option_is_not_generated_and_needs_no_condition(tmp_path):
    """Spec v0.8 section 6a: as section 5 says for a written `selected`."""
    php = '<select name="s"><option value="Yes" <?php if ($v == "Yes") { echo \'selected\'; } ?>>Yes</option></select>'
    result = stages(tmp_path, php)
    assert '<select name="s"><option value="Yes">Yes</option></select>' in page_text(result)
    assert flags(result, "option_selected_not_converted") == [
        ("option_selected_not_converted", "option_selected_not_converted", "review")]
    assert conditions(result) == []  # nothing is written, so no condition is asked for


def test_both_arms_of_an_if_give_one_expression_per_attribute(tmp_path):
    php = "<a href=\"/x\" <?php if ($on) { echo 'class=\"on\"'; } else { echo 'class=\"off\" title=\"t\"'; } ?>>x</a>"
    text = page_text(stages(tmp_path, php))
    assert ') ? "on" : "off"}' in text and ') ? undefined : "t"}' in text


def test_a_conditional_attribute_that_stands_earlier_in_the_tag_has_no_effect(tmp_path):
    """R-I3b: a browser keeps the first of two equal attributes."""
    result = stages(tmp_path, "<li class=\"a\" <?php if ($x) { echo 'class=\"b\"'; } ?>>t</li>")
    assert '<li className="a">t</li>' in page_text(result)
    assert flags(result, "duplicate_attribute") == [("duplicate_attribute", "duplicate_attribute", "info")]
    assert conditions(result) == []


def test_a_conditional_attribute_that_also_stands_later_in_the_tag_falls_back_to_it(tmp_path):
    """R-I3b (v0.14): the conditional one wins when its condition holds; the later one otherwise."""
    result = stages(tmp_path, "<li <?php if ($x) { echo 'class=\"b\"'; } ?> class=\"a\" id=\"i\">t</li>")
    (todo,) = [t for t in result["stage5"]["c2todos"] if t["kind"] == "unresolvedCondition"]
    assert (f'<li className={{unresolvedCondition("{todo["nodeId"]}", "condition_not_translatable") ? "b" : "a"}} '
            'id="i">t</li>') in page_text(result)
    assert not flags(result, "attribute_omitted", "duplicate_attribute")


@pytest.mark.parametrize("arm", ["echo $cls;", "echo 'class=\"a';", "echo 'class=\"a\"' . $more;"])
def test_an_arm_that_prints_anything_else_costs_only_that_tags_attribute(tmp_path, arm):
    """R-I3b + spec v0.8 section 5b (start-tag fallback): the element and its children are kept."""
    result = stages(tmp_path, f'<div><p>kept</p><li id="i" <?php if ($x) {{ {arm} }} ?>>item</li></div>')
    assert [(n["reason"], n["rule"]) for n in result["nodes"] if n["status"] == "abstain"] == [
        ("partial_attribute_output", "R-I3b")]
    assert '<div><p>kept</p><li id="i">item</li></div>' in page_text(result)
    assert flags(result, "attribute_omitted") == [("attribute_omitted", "partial_attribute_output", "todo")]
    assert "markup_not_generatable" not in json.dumps(result["stage5"])


def test_an_expression_where_an_attribute_is_written_costs_only_that_attribute(tmp_path):
    result = stages(tmp_path, '<form><input type="text" <?php echo $attrs; ?> name="q"><b>kept</b></form>')
    assert '<form><input type="text" name="q" /><b>kept</b></form>' in page_text(result)
    assert flags(result, "attribute_omitted") == [("attribute_omitted", "partial_attribute_output", "todo")]


def test_an_abstained_region_inside_an_attribute_value_costs_only_that_attribute(tmp_path):
    """phpBlog core.php 633: an if with a nested if inside a class value. The public shell was lost to it."""
    php = "<a href=\"/p\" class=\"nav-link<?php if ($a) { echo ' active'; } else { if ($b) { echo ' active'; } } ?>\">P</a>"
    result = stages(tmp_path, php)
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["partial_attribute_output"]
    assert '<a href="/p">P</a>' in page_text(result)
    assert flags(result, "attribute_omitted") == [("attribute_omitted", "partial_attribute_output", "todo")]


def test_an_if_inside_a_style_element_is_dynamic_raw_text_not_a_lost_page(tmp_path):
    php = "<div><style><?php if ($bg != '') { echo 'body { background: red; }'; } ?></style><p>kept</p></div>"
    result = stages(tmp_path, php)
    assert "<p>kept</p>" in page_text(result)
    assert [t["reason"] for t in result["stage5"]["c2todos"] if t["severity"] == "todo"] == [
        "no_endpoint", "dynamic_raw_text"]


def test_literal_attributes():
    assert tagtrack.literal_attributes("selected") == [("selected", None)]
    assert tagtrack.literal_attributes(' class="nav-item" data-x=\'1\' checked') == [
        ("class", "nav-item"), ("data-x", "1"), ("checked", None)]
    assert tagtrack.literal_attributes("id=a") == [("id", "a")]
    for text in ("", "  ", 'class="a', "selected>", 'class="a"id="b"', "<b>", None):
        assert tagtrack.literal_attributes(text) is None, text


# ---------------------------------------------------------------- R-I2b: one guard around several Lists

TWO_LOOPS = ("<div><?php if ($count > 0) { ?><ul><?php foreach ($a as $x) { ?><li><b>a</b></li><?php } ?></ul>"
             "<ol><?php foreach ($b as $y) { ?><li><i>b</i></li><?php } ?></ol><?php } %s ?></div>")


def test_a_guard_around_several_loops_is_one_conditional_with_their_empty(tmp_path):
    result = stages(tmp_path, TWO_LOOPS % "else { ?><p>Nothing yet</p><?php }")
    assert [(n["type"], n["rule"]) for n in result["nodes"] if n["type"] in ("InlineConditional", "List", "Empty")] == [
        ("InlineConditional", "R-I2b"), ("List", "R-L1"), ("List", "R-L1"), ("Empty", "R-I2b")]
    assert not [n for n in result["nodes"] if n["status"] == "abstain"]
    text = page_text(result)
    assert ') ? (<><PageList1 items={[]} /><PageList2 items={[]} /></>) : (<PageList1Empty />)}' in text
    assert "<p>Nothing yet</p>" in result["files"]["components/PageList1Empty.tsx"]


def test_a_guard_around_several_loops_without_an_else(tmp_path):
    result = stages(tmp_path, TWO_LOOPS % "")
    assert ') && (<><PageList1 items={[]} /><PageList2 items={[]} /></>)}' in page_text(result)
    assert not [f for f in result["files"] if f.endswith("Empty.tsx")]


def test_loops_in_different_arms_of_one_if_still_abstain(tmp_path):
    php = ("<div><?php if ($k) { ?><ul><?php foreach ($a as $x) { ?><li><b>a</b></li><?php } ?></ul><?php } else { ?>"
           "<ol><?php foreach ($b as $y) { ?><li><i>b</i></li><?php } ?></ol><?php } ?></div>")
    result = stages(tmp_path, php)
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["list_guard_multiple_loops"]


# ---------------------------------------------------------------- R-I8: an arm that ends in exit / die

GUARD = "<?php if (empty($id)) { echo '<meta http-equiv=\"refresh\" content=\"0; url=list.php\">'; exit; } ?>"


def test_the_harness_marks_an_arm_that_exits_and_gives_a_die_message_as_output(tmp_path):
    php = "<?php if ($a) { echo 'x'; exit; } if ($b) { echo 'y'; } if ($c) { die('<b>no</b>'); }\n" \
          "mysqli_query($db, $q) or die('query failed');\nfunction f() { die('in a function'); }\necho 'end';"
    timeline = page(tmp_path, php)
    arms = {e["id"]: enc.get("exits", False) for e in timeline["sequence"] for enc in e["enclosedBy"]}
    # rules v0.15 section 1: `mysqli_query(...) or die('query failed')` is `if (!X) { die(...); }`
    assert list(arms.values()) == [True, False, True, True]
    message = next(e for e in timeline["sequence"] if e["kind"] == "Expr_Exit")
    assert [p["value"] for p in message["parts"]] == ["<b>no</b>"] and message["enclosedBy"][0]["exits"] is True
    # the `or die` is noted; a die inside a function is not in the page's timeline (R-I8c, at its calls)
    assert [(n["kind"], n["line"]) for n in timeline["harnessNotes"]
            if n["kind"] in ("expression_exit", "exit_with_output")] == [("expression_exit", 2)]
    normalised = [e for e in timeline["sequence"] if e["kind"] == "Expr_Exit"][1]
    (enclosure,) = normalised["enclosedBy"]
    assert enclosure == {
        "nodeId": enclosure["nodeId"], "kind": "Stmt_If", "role": "branch", "branch": "then",
        "condNodeId": enclosure["condNodeId"], "condExpr": "!(mysqli_query($db, $q))", "loc": enclosure["loc"],
        "exits": True}
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    loaded = load_timeline(path)
    assert [n.enclosed_by[0].exits for n in loaded.sequence if n.enclosed_by] == [True, False, True, True]
    assert "exitExtension" in timeline["_provenance"]  # marked: pending Member 01 Q20


def test_a_redirect_guard_is_not_a_component_and_the_rest_renders(tmp_path):
    result = stages(tmp_path, GUARD + "<h1>Record</h1><p>shown</p>")
    assert [(n["type"], n["rule"], n["reason"], n["flags"]) for n in result["nodes"] if n["rule"]] == [
        ("Static", "R-I8", "unlabelled", ["redirect_guard"])]
    text = page_text(result)
    assert "<h1>Record</h1><p>shown</p>" in text and "meta" not in text and "unresolvedCondition" not in text
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "redirect_guard"]
    assert (flag["severity"], flag["sourceLine"]) == ("review", 1) and flag["detail"].startswith(
        "redirect to list.php, then exit")


def test_a_script_that_sets_location_is_a_redirect_too(tmp_path):
    php = "<?php if (!$ok) { echo \"<script>alert('no'); window.location.href = 'index.php';</script>\"; exit(); } ?><p>in</p>"
    result = stages(tmp_path, php)
    assert [n["rule"] for n in result["nodes"] if n["rule"]] == ["R-I8"] and "<p>in</p>" in page_text(result)
    assert sorted(f["flag"] for f in result["stage5"]["flags"] if f["severity"] == "review" and f["flag"] in (
        "redirect_guard", "inline_js_dropped")) == ["inline_js_dropped", "redirect_guard"]


def test_nested_redirect_guards_are_one_guard(tmp_path):
    """phpBlog admin/header.php 6-18: a guard inside the `then` arm and another as the `else` arm. R-I8 case 1
    applies at any depth (v0.15, which corrects v0.14's scope)."""
    php = ("<?php if (isset($_SESSION['u'])) { if ($count <= 0) { echo '<meta http-equiv=\"refresh\" content=\"0; "
           "url=/\" />'; exit; } } else { echo '<meta http-equiv=\"refresh\" content=\"0; url=../login\" />'; exit; }"
           " ?><h1>Admin</h1>")
    result = stages(tmp_path, php)
    assert [(n["type"], n["rule"]) for n in result["nodes"] if n["rule"]] == [("Static", "R-I8")]
    assert [f["detail"].split(",")[0] for f in result["stage5"]["flags"] if f["flag"] == "redirect_guard"] == [
        "redirect to /", "redirect to ../login"]
    assert "<h1>Admin</h1>" in page_text(result) and conditions(result) == []


def test_a_redirect_arm_beside_an_arm_with_content_renders_nothing_itself(tmp_path):
    """R-I8 case 1 in an `else` arm (v0.15: at any depth)."""
    php = "<div><?php if ($ok) { echo '<p>fine</p>'; } else { echo '<meta http-equiv=\"refresh\" content=\"0; url=x\">'; exit; } ?></div>"
    result = stages(tmp_path, php)
    text = page_text(result)
    assert "<p>fine</p>" in text and "meta" not in text
    assert [f["flag"] for f in result["stage5"]["flags"] if f["flag"] == "redirect_guard"] == ["redirect_guard"]
    assert not [n for n in result["nodes"] if n["status"] == "abstain"]


@pytest.mark.parametrize("php", [
    "<?php if (!$id) { echo '<meta http-equiv=\"refresh\" content=\"0; url=x\">'; } ?><p>p</p>",  # no exit
    "<?php if (!$id) { echo '<meta name=\"robots\" content=\"noindex\">'; exit; } ?><p>p</p>",  # not a redirect
])
def test_only_a_redirect_that_exits_is_a_redirect_guard(tmp_path, php):
    result = stages(tmp_path, php)
    assert not [f for f in result["stage5"]["flags"] if f["flag"] == "redirect_guard"]
    assert "<meta" in page_text(result)


@pytest.mark.parametrize("arm", ["echo '<b>Stop</b>'; exit;", "die('<b>Stop</b>');"])
def test_an_arm_that_exits_after_other_output_makes_the_rest_its_else(tmp_path, arm):
    """R-I8, case 2: `cond ? <arm output> : <rest>`; a die message is the arm's output."""
    result = stages(tmp_path, f"<?php if ($error) {{ {arm} }} ?><h1>Page</h1><p>rest</p>")
    assert [(n["type"], n["rule"], n["reason"]) for n in result["nodes"] if n["rule"]] == [
        ("InlineConditional", "R-I8", "unlabelled")]
    (todo,) = [t for t in result["stage5"]["c2todos"] if t["kind"] == "unresolvedCondition"]
    assert (f'{{unresolvedCondition("{todo["nodeId"]}", "condition_not_translatable") ? (<b>Stop</b>) : '
            "(<><h1>Page</h1><p>rest</p></>)}") in page_text(result)


def test_an_exit_arm_before_the_shell_wraps_the_shell(tmp_path):
    files = {"lib.php": "<?php function head() { ?><html><body><div><?php }\n"
                        "function foot() { ?></div></body></html><?php }",
             "page.php": "<?php include 'lib.php'; if ($banned) { echo '<b>Banned</b>'; exit; } head(); ?><p>a</p>"
                         "<?php foot(); ?>"}
    result = run(tmp_path, files)
    assert abstains(result, "page.php") == [] and result["pages"]["page.php"]["group"] == "(head)"
    text = flat(result["file"]("app/(head)/page/page.tsx"))
    assert ') ? (<b>Banned</b>) : (<HeadShell><p>a</p></HeadShell>)}' in text


@pytest.mark.parametrize("php", [
    "<?php if ($a) { if ($b) { echo '<b>x</b>'; exit; } } ?><p>rest</p>",  # nested: the exit also ends the outer if
    "<div><?php if ($e) { echo '<b>x</b>'; exit; } ?><p>rest</p></div>",  # the rest closes a tag opened before
    "<?php if ($ok) { echo '<i>fine</i>'; } else { echo '<b>x</b>'; exit; } ?><p>rest</p>",  # not the then arm
])
def test_an_exit_arm_the_written_rule_does_not_fit_abstains(tmp_path, php):
    result = stages(tmp_path, php)
    assert [(n["reason"], n["rule"]) for n in result["nodes"] if n["status"] == "abstain"] == [
        ("exit_arm_not_covered", "R-I8")]
    assert 'reason="exit_arm_not_covered"' in result["files"]["app/page/page.tsx"]


def test_an_exit_arm_in_an_included_file_is_r_i8c(tmp_path):
    """R-I8c (v0.14): not generated in the file; its include is flagged (tests/test_phase3c.py has the rest)."""
    files = {"guard.php": "<?php if ($banned) { echo '<b>Banned</b>'; exit; } ?><i>ok</i>",
             "page.php": "<?php include 'guard.php'; ?><p>page</p>"}
    result = run(tmp_path, files)
    assert [(n["type"], n["rule"]) for n in result["nodes"]("guard.php") if n["rule"] == "R-I8c"] == [
        ("Static", "R-I8c")]
    assert abstains(result, "guard.php") == []


def test_redirect_text():
    assert tagtrack.only_redirect('<meta http-equiv="refresh" content="0; url=a.php" />')
    assert tagtrack.only_redirect("<meta content='0;url=x' HTTP-EQUIV=refresh>\n")
    assert tagtrack.only_redirect("<script>location = '/';</script>")
    assert tagtrack.only_redirect("<script type='text/javascript'>alert(1); document.location.href='x';</script>")
    for text in ("", "<meta charset='utf-8'>", "<script>alert(1)</script>", "<p>x</p>",
                 "<meta http-equiv='refresh' content='0'>text", "<script>if (location == 'x') {}</script>"):
        assert not tagtrack.only_redirect(text), text
    assert tagtrack.redirect_target('<meta http-equiv="refresh" content="0; url=a.php">') == "a.php"


# ---------------------------------------------------------------- non_html_response


def test_a_unit_that_sends_a_non_html_content_type_is_not_generated(tmp_path):
    files = {"feed.php": "<?php header(\"Content-type: text/xml\"); echo '<rss><channel>' . $t . '</channel></rss>';",
             "html.php": "<?php header('Content-Type: text/html; charset=utf-8'); ?><p>page</p>",
             "page.php": "<p>page</p>"}
    result = run(tmp_path, files)
    assert result["timelines"]["feed.php"]["responseContentType"] == "text/xml"
    (node,) = result["nodes"]("feed.php")
    assert (node["type"], node["status"], node["reason"]) == ("Page", "abstain", "non_html_response")
    stage5 = result["stage"]("feed.php", 5)
    assert stage5["files"] == [] and stage5["notGenerated"] == {
        "reason": "non_html_response", "contentType": "text/xml", "owner": "Component 1 (Route Handler)"}
    assert stage5["counts"]["severity"] == {"todo": 1, "review": 0, "info": 0}  # one gap for the unit
    assert result["index"]["notGenerated"] == [{"key": "feed.php", "path": "feed.php", **stage5["notGenerated"]}]
    files_written = set(result["index"]["appFiles"])
    assert not [f for f in files_written if "feed" in f] and "app/(plain)/html/page.tsx" in files_written


def test_the_loader_rejects_a_malformed_content_type(tmp_path):
    document = page(tmp_path, "<?php header('Content-Type: text/xml'); echo 'x';")
    document["responseContentType"] = 3
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="responseContentType"):
        load_timeline(path)


# ---------------------------------------------------------------- R-L4: the outer row

NESTED = ("<ul><?php foreach ($posts as $post) { ?><li><b>p</b><ol><?php foreach (%s as $tag) { ?>"
          "<li><i>t</i>%s</li><?php } ?></ol></li><?php } ?></ul>")


def test_the_inner_item_receives_the_outer_row_when_a_read_uses_it(tmp_path):
    result = stages(tmp_path, NESTED % ("$tags", " of <u><?php echo $post['title']; ?></u>"))
    files = result["files"]
    assert "<PageList2 items={[]} outer={row} />" in files["components/PageList1Item.tsx"]
    assert ("export function PageList2({ items, outer }: { items: Record<string, unknown>[]; "
            "outer: Record<string, unknown> })") in files["components/PageList2.tsx"]
    assert "<PageList2Item key={i} row={row} outer={outer} />" in files["components/PageList2.tsx"]
    assert "export function PageList2Item({ row, outer }: " in files["components/PageList2Item.tsx"]


def test_the_inner_item_receives_the_outer_row_when_the_iterable_uses_it(tmp_path):
    result = stages(tmp_path, NESTED % ("$post['tags']", ""))
    assert "<PageList2 items={[]} outer={row} />" in result["files"]["components/PageList1Item.tsx"]
    assert "outer" in result["files"]["components/PageList2Item.tsx"]


def test_an_inner_loop_that_does_not_use_the_outer_row_gets_no_outer_prop(tmp_path):
    result = stages(tmp_path, NESTED % ("$tags", " of <u><?php echo $site; ?></u>"))
    assert "outer" not in "".join(result["files"].values())  # $site is some other variable: unresolved, as before
    assert "<PageList2 items={[]} />" in result["files"]["components/PageList1Item.tsx"]


# ---------------------------------------------------------------- R-F2: pairing after HTML5 implicit closes


def test_closes_counts_the_implicit_closes_a_browser_applies():
    exact = (("html", "body", "div"), ("div", "body", "html"))
    assert app_units.closes(*exact)  # the pair as written before v0.13
    # phpBlog admin: <head> is closed by <body>; </div> also ends the unclosed <main>
    assert app_units.closes(("html", "head", "body", "div", "div", "main"), ("div", "div", "body", "html"))
    assert app_units.closes(("html", "body", "div", "main"), ("body", "html"))  # </body> ends what is open in it
    assert app_units.closes(("html", "body", "div"), ("div",))  # </body> and </html> are optional
    assert not app_units.closes(("html", "body", "div"), ("section", "body", "html"))  # closes what H never opened
    assert not app_units.closes(("html", "body", "div", "div"), ("div",))  # a <div> is left open
    assert not app_units.closes(("html", "head", "div"), ("div",))  # no <body>: <head> is not closed


def test_header_and_footer_files_pair_after_implicit_closes(tmp_path):
    """boundary-rules v0.13, R-F2 condition 2 refined (phpBlog admin/header.php and admin/footer.php)."""
    files = {"header.php": "<html><head><title>T</title><body><div class='c'><main>",
             "footer.php": "</div></body></html>",
             "page.php": "<?php include 'header.php'; ?><p>x</p><?php include 'footer.php'; ?>"}
    result = run(tmp_path, files)
    assert [(s["name"], s["group"]) for s in result["index"]["shells"]] == [("HeaderShell", "(header)")]
    assert abstains(result, "page.php") == [] and result["pages"]["page.php"]["group"] == "(header)"
    layout = flat(result["file"]("app/(header)/layout.tsx"))
    assert "<html><head><title>T</title></head><body>{children}</body></html>" in layout  # <head> ends at <body>
    assert '<div className="c"><main>{children}</main></div>' in flat(result["file"]("components/HeaderShell.tsx"))


@pytest.mark.parametrize("header, footer", [
    ("<html><body><div><div>", "</div>"),  # a <div> is left open: not one of the implicit closes
    ("<html><body><div>", "</section></body></html>"),  # the footer closes something the header never opened
])
def test_a_near_miss_is_still_no_pair(tmp_path, header, footer):
    files = {"header.php": header, "footer.php": footer,
             "page.php": "<?php include 'header.php'; ?><p>x</p><?php include 'footer.php'; ?>"}
    result = run(tmp_path, files)
    assert result["index"]["shells"] == [] and result["pages"]["page.php"]["group"] == "(plain)"
    assert "tag_crosses_function" in [reason for reason, _, _ in abstains(result, "page.php")]


# ---------------------------------------------------------------- spec 5b: implied end tags


def nesting(result: dict) -> list:
    return sorted(f["flag"] for f in result["stage5"]["flags"] if f["flag"] in (
        "implied_end_tag", "html_nesting_changed"))


def test_a_list_item_ends_the_one_before_it(tmp_path):
    result = stages(tmp_path, "<ul><li><p>one<li><p>two</p></li></ul>")
    assert "<ul><li><p>one</p></li><li><p>two</p></li></ul>" in page_text(result)
    assert nesting(result) == ["implied_end_tag"]
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "implied_end_tag"]
    assert flag["severity"] == "info"


def test_a_nested_list_is_not_touched(tmp_path):
    result = stages(tmp_path, "<ul><li>a<ul><li>b</li></ul></li></ul>")
    assert "<ul><li>a<ul><li>b</li></ul></li></ul>" in page_text(result) and nesting(result) == []


def test_definition_terms_and_descriptions_end_each_other(tmp_path):
    result = stages(tmp_path, "<dl><dt>t1<dd>d1<dt>t2<dd>d2</dl>")
    assert "<dl><dt>t1</dt><dd>d1</dd><dt>t2</dt><dd>d2</dd></dl>" in page_text(result)
    assert nesting(result) == ["implied_end_tag"] * 3


def test_an_option_ends_the_one_before_it(tmp_path):
    result = stages(tmp_path, "<select name='s'><option value='a'>A<option value='b'>B</select>")
    assert '<option value="a">A</option><option value="b">B</option>' in page_text(result)
    assert nesting(result) == ["implied_end_tag"]


@pytest.mark.parametrize("php", [
    "<ul><li><b>bold<li>next</ul>",  # a browser reopens <b> in the next item: not simply closed
    "<ul><li>first<?php foreach ($l as $v) { ?><li>x</li><?php } ?></ul>",  # across a loop
    "<ul><li><?php echo $lead; ?><li>next</li></ul>",  # across an expression
])
def test_an_end_tag_is_not_implied_across_a_boundary_or_a_formatting_element(tmp_path, php):
    result = stages(tmp_path, php)
    assert "implied_end_tag" not in nesting(result) and "html_nesting_changed" in nesting(result)


# ---------------------------------------------------------------- phpBlog: the analysis substitution


@pytest.fixture(scope="module")
def phpblog_timelines():
    if not phpblog_clone:
        pytest.skip("phpBlog clone not present (legacy-apps/PINS.md)")
    return harness_app.app_timelines("phpblog")


def test_config_php_is_analysed_from_the_installers_template(phpblog_timelines):
    """Owner decision (CONTEXT v18), recorded with its evidence in legacy-apps/PINS.md."""
    config = harness_app.APPS["phpblog"]
    assert config["substitutions"] == {"config.php": "install/config.tpl"}
    assert not (config["root"] / "config.php").exists()  # the clone itself is not touched
    assert phpblog_timelines.files == 51 and "config.php" in phpblog_timelines.silent  # no echo of its own
    core = next(p.timeline for p in phpblog_timelines.pages if p.key == "core.php")
    include = next(e for e in core["sequence"] if e["kind"] == "include")
    # the file is known now. Its only output is the printf of an arm that exits (config.tpl 10-13): R-I8c
    # (v0.14) counts it as non-printing and lists the arm on the include
    assert (include["path"], include["outputsInside"]) == ("legacy-apps/phpblog/config.php", False)
    assert include["exitOutput"] == [{"file": "legacy-apps/phpblog/config.php", "line": 12}]
    assert "config_settings.php" in phpblog_timelines.silent  # in the repository: a no-output include
    pins = (ROOT / "legacy-apps" / "PINS.md").read_text(encoding="utf-8")
    assert "Analysis substitutions" in pins and "`install/done.php` 49–53" in pins and "`install/done.php` 22" in pins


def test_no_other_app_has_a_substitution():
    assert {app: config.get("substitutions", {}) for app, config in harness_app.APPS.items()} == {
        "hms": {}, "wackopicko": {}, "phpblog": {"config.php": "install/config.tpl"}, "attendance": {},
        "warehouse": {}}
