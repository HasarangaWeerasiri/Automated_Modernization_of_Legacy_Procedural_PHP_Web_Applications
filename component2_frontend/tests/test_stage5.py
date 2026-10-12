"""Stage 5 (generation), against docs/generation-spec.md v0.1.

Mock-level tests run the whole pipeline on the ten mocks (sections 10 and 11).
Unit tests build tiny one-page timelines to check one rule each: every row of
section 5 (markup -> JSX) and every whitelist row of section 6 (conditions).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.mapping import stage5_generate as stage5
from src.mapping.boundary_config import load_boundary_config
from src.mapping.conditions import Scope, translate
from src.mapping.generation_config import load_generation_config
from src.mapping.loader import load_contract, load_endpoint_map, load_labels, load_timeline
from src.mapping.stage1_isolation import isolate_presentation
from src.mapping.stage2_boundaries import find, infer_boundaries
from src.mapping.stage3_requirements import recover_requirements
from src.mapping.stage4_reconcile import reconcile
from src.model import SchemaType

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
sys.path.insert(0, str(ROOT / "tools"))
import compile_check  # noqa: E402

HMS = ("list_while", "list_foreach", "admin", "detail", "edge_cases")
WP = ("wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
ALL_MOCKS = HMS + WP
CONFIG = load_boundary_config()
GEN_CONFIG = load_generation_config()
CONTRACT = load_contract(MOCKS / "sample_contract.json")
ENDPOINT_MAP = load_endpoint_map(MOCKS / "endpoint_map.json")
CONTEXT = GEN_CONFIG.condition_context_reasons


def pipeline(timeline_path, labels_path, contract=CONTRACT, endpoint_map=ENDPOINT_MAP):
    timeline = load_timeline(timeline_path)
    tree = infer_boundaries(isolate_presentation(timeline, load_labels(labels_path)), timeline, CONFIG)
    requirements = recover_requirements(tree, timeline)
    report = reconcile(requirements, contract, endpoint_map)
    return tree, report, stage5.generate(tree, requirements, report, contract, timeline, CONFIG, GEN_CONFIG)


def mock(name):
    return pipeline(MOCKS / f"timeline_{name}.json", MOCKS / f"labels_{name}.json")


@pytest.fixture(scope="module")
def runs():
    return {name: mock(name) for name in ALL_MOCKS}


def text(result, path):
    return next(f.text for f in result.files if f.path == path)


def todos(result, reason=None):
    return [t for t in result.todos if reason is None or t.reason == reason]


def flags(result, key=None):
    return [f for f in result.flags if key is None or stage5.flag_key(f) == key]


# ---------------------------------------------------------------- section 10: checks


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_two_runs_are_byte_identical(name, runs, tmp_path):
    _, _, first = runs[name]
    _, _, second = mock(name)
    assert [(f.path, f.text) for f in first.files] == [(f.path, f.text) for f in second.files]
    assert stage5.dumps(first, "x") == stage5.dumps(second, "x")
    stage5.write_files(first, tmp_path / "a")
    stage5.write_files(second, tmp_path / "b")
    for f in first.files:
        assert (tmp_path / "a" / f.path).read_bytes() == (tmp_path / "b" / f.path).read_bytes()


def without_node_ids(code):
    ids = {}
    return re.sub(r"f\d{3}#\d{5}", lambda m: ids.setdefault(m.group(), f"#{len(ids)}"), code)


def test_list_while_and_list_foreach_components_are_identical(runs):
    _, _, while_ = runs["list_while"]
    _, _, foreach = runs["list_foreach"]
    components = sorted(f.path for f in while_.files if f.path.startswith("components/"))
    assert components == sorted(f.path for f in foreach.files if f.path.startswith("components/"))
    assert components == ["components/AppointmentsItem.tsx", "components/AppointmentsList.tsx"]
    for path in components:
        assert without_node_ids(text(while_, path)) == without_node_ids(text(foreach, path))
    # Byte-identical except the C2Todo's node id, which the spec says is the timeline's own node id.
    assert text(while_, "components/AppointmentsList.tsx") == text(foreach, "components/AppointmentsList.tsx")
    assert text(while_, "app/admin-panel1/page.tsx") == text(foreach, "app/list_foreach/page.tsx")
    assert [f.text for f in while_.files if f.path.startswith("lib/")] == \
        [f.text for f in foreach.files if f.path.startswith("lib/")]


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_file_is_a_server_component_without_handlers(name, runs):
    _, _, result = runs[name]
    for f in result.files:
        assert "use client" not in f.text, f.path
        assert not re.search(r"\son[A-Z]\w*=", f.text), f.path


def gap_is_visible(result, node_id, reason):
    """A gap is a C2Todo with this id and reason, or (in an attribute or a dropped element) a flag for the node."""
    if any(t.node_id == node_id and t.reason == reason for t in result.todos):
        return True
    return any(f.node_id == node_id and (f.reason == reason and f.flag == "attribute_omitted"
                                         or f.flag in ("output_dropped", "html_comment_dropped"))
               for f in result.flags)


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_flagged_need_and_every_abstain_appears(name, runs):
    tree, report, result = runs[name]
    for r in report.results:
        if r.result in ("missing", "cannot_reconcile"):
            for ref in r.need.references:
                assert gap_is_visible(result, ref.node_id, r.reason), (r.need.name, ref.node_id, r.reason)
        if r.result == "excluded":
            reason = GEN_CONFIG.context_reasons[r.reason]
            for ref in r.need.references:
                assert gap_is_visible(result, ref.node_id, reason), (r.need.name, ref.node_id, reason)
    for r in report.collection_results:
        if r.result == "cannot_reconcile":
            assert gap_is_visible(result, r.need.references[0].node_id, r.reason)
    for node in find(tree, type="Abstain"):
        assert gap_is_visible(result, node.source_ids[0], node.reason), node.source_ids


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_c2todo_is_listed_at_its_line_and_nothing_else_is_in_the_code(name, runs):
    _, _, result = runs[name]
    files = {f.path: f.text.split("\n") for f in result.files}
    for t in result.todos:
        line = files[t.file][t.line - 1]
        if t.kind == "C2Todo":
            assert f'<C2Todo id="{t.node_id}" reason="{t.reason}" />' in line, (t, line)
        else:
            assert f'unresolvedCondition("{t.node_id}", "{t.reason}")' in line, (t, line)
    in_code = sum(len(re.findall(r"<C2Todo |unresolvedCondition\(\"", "\n".join(lines)))
                  for path, lines in files.items() if not path.startswith("lib/"))
    assert in_code == len(result.todos)
    for f in result.flags:
        assert 1 <= f.line <= len(files[f.file]), f


@pytest.fixture(scope="module")
def compiled(runs, tmp_path_factory):
    reason = compile_check.unavailable()
    if reason:
        pytest.skip(f"compile check skipped: {reason}")
    out = {}
    for name in ALL_MOCKS:
        directory = tmp_path_factory.mktemp(name)
        stage5.write_files(runs[name][2], directory)
        out[name] = compile_check.check(name, directory)
    return out


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_generated_code_compiles_with_tsc(name, compiled):
    assert compiled[name].errors == 0, compiled[name].output


def test_compile_check_reports_a_planted_type_error(runs, tmp_path):
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    stage5.write_files(runs["list_while"][2], tmp_path)
    item = tmp_path / "components" / "AppointmentsItem.tsx"
    item.write_text(item.read_text(encoding="utf-8").replace(
        '<C2Todo id="f001#00475" reason="missing_in_contract" />', "{row.contact}"), encoding="utf-8")
    result = compile_check.check("planted_error", tmp_path)
    assert result.errors == 1 and "Property 'contact' does not exist on type 'Appointment'" in result.output


# ---------------------------------------------------------------- section 11: expected results on the mocks


def test_list_while_item_and_status_chain(runs):
    _, _, result = runs["list_while"]
    item = text(result, "components/AppointmentsItem.tsx")
    listing = text(result, "components/AppointmentsList.tsx")
    assert "export function AppointmentsList({ items }: { items: Appointment[] })" in listing
    assert '<table className="table table-hover">' in listing and "<thead>" in listing
    assert "<AppointmentsItem key={i} row={row} />" in listing
    assert item.count("<td>") == 12 and item.strip().endswith("}") and "<tr>" in item
    assert '<td><C2Todo id="f001#00475" reason="missing_in_contract" /></td>' in item
    assert "row.contact" not in item
    # R-I6: three separate conditionals in source order, translated from condExpr; no ternary chain.
    chain = [line.strip() for line in item.splitlines() if "&&" in line]
    assert chain == ['{(row.userStatus == 1) && (row.doctorStatus == 1) && "Active"}',
                     '{(row.userStatus == 0) && (row.doctorStatus == 1) && "Cancelled by Patient"}',
                     '{(row.userStatus == 1) && (row.doctorStatus == 0) && "Cancelled by Doctor"}']
    (review,) = flags(result, "stage2_review:exclusivity_not_verified")
    assert review.file == "components/AppointmentsItem.tsx" and "&&" in item.split("\n")[review.line - 1]
    page = text(result, "app/admin-panel1/page.tsx")
    assert "const data = await getApiAppointments();" in page and "<AppointmentsList items={data.appointments} />" in page


def test_admin_guard_is_a_c2todo_with_an_unused_abstained_function(runs):
    _, _, result = runs["admin"]
    page = text(result, "app/admin_dashboard/page.tsx")
    assert '<C2Todo id="f003#00028" reason="cuts_across_business_logic" />' in page
    assert page.count("Abstained_f003_00028") == 1  # defined, never called
    assert "function Abstained_f003_00028({ data }: { data: AppointmentStats })" in page
    assert "{data.total}" in page and "{data.revenue}" in page
    session = [t.node_id for t in todos(result, "needs_auth_context") if t.kind == "C2Todo"]
    assert session == ["f003#00025", "f003#00071"]
    assert "export default function Page()" in page  # nothing rendered needs data: no fetch


def test_detail_has_no_missing_field(runs):
    _, report, result = runs["detail"]
    assert not todos(result, "missing_in_contract") and not todos(result)
    item = text(result, "components/AppointmentsItem.tsx")
    assert "href={`admin-panel.php?ID=${row.ID}&cancel=update`}" in item  # text + matched field -> template
    assert ") : (" in item and '"Cancelled"' in item  # a real if/else -> ternary


def test_edge_cases_gaps(runs):
    _, _, result = runs["edge_cases"]
    assert [t.node_id for t in todos(result, "ambiguous_needs_schema")] == ["f005#00083", "f005#00088", "f005#00093"]
    assert [t.node_id for t in todos(result, "unresolved_read")] == ["f005#00105"]
    (abstain,) = todos(result, "cuts_across_business_logic")
    assert abstain.node_id == "f005#00035"  # the if holding the nested foreach
    assert all(t.in_abstained for t in todos(result, "ambiguous_needs_schema"))


@pytest.mark.parametrize("name", WP)
def test_wackopicko_data_is_never_rendered(name, runs):
    """Every WackoPicko read is unresolved, so no value expression is generated."""
    _, _, result = runs[name]
    for f in result.files:
        assert not re.search(r"\{(row|data)\.", f.text) and "${row" not in f.text and "${data" not in f.text
    assert todos(result, "no_endpoint")


def test_wp_thumbnails_chunked_loop_is_a_c2todo(runs):
    _, _, result = runs["wp_thumbnails"]
    (chunked,) = todos(result, "chunked_container")
    assert chunked.node_id == "f009#00106" and chunked.in_abstained  # inside the abstained if's function
    assert todos(result, "tag_crosses_branch")[0].in_abstained is False


@pytest.mark.parametrize("name, node_id", [("wp_view", "f007#00131"), ("wp_guestbook", "f006#00083")])
def test_unescaped_comments_are_flagged_escaping_changed(name, node_id, runs):
    _, _, result = runs[name]
    assert [f.status for f in flags(result, "escaping_changed") if f.node_id == node_id] == ["info"]


def test_escaped_output_is_not_flagged(runs):
    _, _, result = runs["wp_view"]
    assert not [f for f in flags(result, "escaping_changed") if f.node_id == "f007#00109"]  # h( $pic['title'] )


def test_wp_header(runs):
    _, _, result = runs["wp_header"]
    page = text(result, "app/html_functions/page.tsx")
    assert "<Span" not in page and "<span>Logout</span>" in page
    assert not re.search(r"<(html|head|body|title|link)\b", page) and "<!--" not in page
    assert len(flags(result, "document_shell")) == 3 and len(flags(result, "html_comment_dropped")) == 1
    omitted = flags(result, "attribute_omitted:condition_not_translatable")
    assert [(f.node_id, f.detail) for f in omitted] == [(i, "class on <li>") for i in (
        "f009#00019", "f009#00026", "f009#00033", "f009#00040", "f009#00052")]
    search = next(line for line in page.split("\n") if 'id="query2"' in line)
    assert "size={15}" in search and "value" not in search.lower().replace("verticalalign", "")
    (value,) = flags(result, "attribute_omitted:unresolved_read")
    assert (value.node_id, value.detail) == ("f009#00068", "value on <input>")


# ---------------------------------------------------------------- unit: tiny pages


def html(raw):
    return {"kind": "Stmt_InlineHTML", "raw": raw}


def read(expr, kind="unresolved", var="$x", path=(), column=None):
    source = None
    confidence = "unresolved" if kind == "unresolved" else "resolved"
    if kind == "db_row_field":
        source = {"fetchNodeId": "t001#09000", "queryNodeId": "t001#09001", "table": "t", "column": column}
    if kind == "literal":
        var, path = None, None
    return {"expr": expr, "var": var, "path": None if path is None else list(path), "sourceKind": kind,
            "confidence": confidence, "source": source}


def computed(name, inner, extra=""):
    return {"expr": f"{name}({inner['expr']}{extra})", "var": None, "path": None, "sourceKind": "computed",
            "confidence": inner["confidence"], "source": None, "derivedFrom": [inner]}


def echo(r):
    return {"kind": "Stmt_Echo", "reads": [r]}


TINY_CONTRACT = {
    "openapi": "3.1.0", "info": {"title": "tiny", "version": "1"},
    "paths": {"/api/tiny": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["total"],
        "properties": {"total": {"type": "integer"}, "name": {"type": ["string", "null"]}}}}}}}}}},
}
STATS_TOTAL = read("$s['total']", "db_row_field", "$s", ["total"], "total")


LIST_CONTRACT = {
    "openapi": "3.1.0", "info": {"title": "tiny", "version": "1"},
    "paths": {"/api/tiny": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["rows"], "properties": {"rows": {"type": "array", "items": {
            "type": "object", "required": ["name", "n"],
            "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}}}}}}}}}}}},
}


def tiny(tmp_path, items, mapped=False, contract=TINY_CONTRACT, if_concern="presentation"):
    """Run Stages 1-5 on a one-page timeline. Items: dicts (entries), ("if", [(branch, condExpr|None, items)...])
    or ("loop", items) (a foreach over $rows as $row). Every if is labelled `if_concern`."""
    return tiny_run(tmp_path, items, mapped, contract, if_concern)[2]


def tiny_run(tmp_path, items, mapped=False, contract=TINY_CONTRACT, if_concern="presentation"):
    """Like tiny(), returning (Stage 2 tree, Stage 4 report, Stage 5 result)."""
    sequence, labels, counter = [], {}, iter(range(1, 10_000))

    def add(entries, enclosed):
        for item in entries:
            if isinstance(item, tuple) and item[0] == "loop":
                loop_id = f"t001#{next(counter):05d}"
                labels[loop_id] = {"concern": "presentation", "basis": "rule", "ruleId": "R05", "reason": "x"}
                add(item[1], enclosed + [{"nodeId": loop_id, "kind": "Stmt_Foreach", "role": "iteration",
                                          "iterExpr": "$rows", "iterSourceKind": "db_row_field", "valueVar": "$row",
                                          "keyVar": None}])
                continue
            if isinstance(item, tuple):  # ("if", [(branch, condExpr, items), ...])
                if_id = f"t001#{next(counter):05d}"
                labels[if_id] = {"concern": if_concern, "basis": "rule", "ruleId": "R08", "reason": "x"}
                cond_id = f"t001#{next(counter):05d}"
                for branch, cond, inner in item[1]:
                    enc = {"nodeId": if_id, "kind": "Stmt_If", "role": "branch", "branch": branch,
                           "condNodeId": cond_id}
                    if cond is not None:
                        enc["condExpr"] = cond
                    add(inner, enclosed + [enc])
                continue
            node_id = f"t001#{next(counter):05d}"
            sequence.append({"id": node_id, **item, "enclosedBy": enclosed})
            labels[node_id] = {"concern": "presentation", "basis": "rule", "ruleId": "R01", "reason": "x"}

    add(items, [])
    entrypoint = "legacy-apps/tiny/page.php"
    (tmp_path / "t.json").write_text(json.dumps({"entrypoint": entrypoint, "schemaVersion": "1.0",
                                                 "sequence": sequence}), encoding="utf-8")
    (tmp_path / "l.json").write_text(json.dumps({"schemaVersion": "1.0", "labels": labels}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps(contract), encoding="utf-8")
    endpoints = {entrypoint: {"method": "get", "path": "/api/tiny", "status": "200",
                              "mediaType": "application/json"}} if mapped else {}
    (tmp_path / "m.json").write_text(json.dumps({"endpoints": endpoints}), encoding="utf-8")
    return pipeline(tmp_path / "t.json", tmp_path / "l.json", load_contract(tmp_path / "c.json"),
                    load_endpoint_map(tmp_path / "m.json"))


def body(result):
    """The Page's JSX lines, stripped, without the no_endpoint C2Todo and the fragment around the page."""
    page = text(result, "app/page/page.tsx").split("\n")
    start = page.index("  return (") + 1
    end = page.index("  );", start)
    lines = page[start:end]
    if lines[0] == "    <>":
        lines = lines[1:-1]
    return [line.strip() for line in lines if 'reason="no_endpoint"' not in line]


# section 5, row by row


def test_tag_and_attribute_names_are_lower_cased(tmp_path):
    lines = body(tiny(tmp_path, [html('<DIV ID="a"><Span TITLE="t">x</SPAN></DIV>')]))
    assert '<div id="a">' in lines and '<span title="t">x</span>' in lines


def test_class_and_for_become_classname_and_htmlfor(tmp_path):
    lines = body(tiny(tmp_path, [html('<label class="c" for="f">x</label>')]))
    assert '<label className="c" htmlFor="f">x</label>' in lines


def test_other_attributes_follow_react_names(tmp_path):
    lines = body(tiny(tmp_path, [html('<table cellspacing="0"><tr><td colspan="2" data-x="1" aria-label="l">x'
                                      '</td></tr></table><input readonly tabindex="3" maxlength="5">')]))
    assert '<table cellSpacing="0">' in lines
    assert '<td colSpan={2} data-x="1" aria-label="l">x</td>' in lines
    assert "<input readOnly tabIndex={3} maxLength={5} />" in lines


def test_void_elements_are_self_closed(tmp_path):
    lines = body(tiny(tmp_path, [html('<p>a<br>b<img src="i.png"></p>')]))
    assert lines[1:5] == ["a", "<br />", "b", '<img src="i.png" />']


def test_numeric_props_need_an_integer(tmp_path):
    result = tiny(tmp_path, [html('<input size="15"><input size="big">')])
    assert body(result) == ["<input size={15} />", "<input />"]
    (dropped,) = flags(result, "attribute_dropped")
    assert dropped.status == "review" and "size='big'" in dropped.detail


def test_form_state_becomes_uncontrolled_props(tmp_path):
    result = tiny(tmp_path, [html('<input value="v" type="checkbox" checked><textarea name="t">\nhello</textarea>'
                                  '<select><option value="a" selected>A</option></select>')])
    lines = body(result)
    assert '<input defaultValue="v" type="checkbox" defaultChecked />' in lines
    assert '<textarea name="t" defaultValue="hello" />' in lines  # HTML drops the newline after <textarea>
    assert '<option value="a">A</option>' in lines
    (selected,) = flags(result, "option_selected_not_converted")
    assert selected.status == "review"


def test_attribute_with_text_and_php_is_a_template_literal(tmp_path):
    result = tiny(tmp_path, [html('<a href="view.php?id='), echo(computed("h", STATS_TOTAL)), html('">x</a>')],
                  mapped=True)
    assert "<a href={`view.php?id=${data.total}`}>x</a>" in body(result)  # h() dropped: React escapes
    assert not flags(result, "escaping_changed") and flags(result, "legacy_link")
    assert "export default async function Page()" in text(result, "app/page/page.tsx")


def test_attribute_with_an_unresolvable_part_is_omitted_not_emptied(tmp_path):
    result = tiny(tmp_path, [html('<a class="k" href="v.php?id='), echo(read("$id")), html('">x</a>')])
    assert '<a className="k">x</a>' in body(result)
    (omitted,) = flags(result, "attribute_omitted:unresolved_read")
    assert omitted.detail == "href on <a>" and omitted.node_id == "t001#00002"


def test_document_shell_is_dropped_children_kept(tmp_path):
    result = tiny(tmp_path, [html('<!DOCTYPE html><html lang="en"><head><title>T</title><link rel="x"></head>'
                                  '<body><p>kept</p></body></html>')])
    assert body(result) == ["<p>kept</p>"]
    assert [f.status for f in flags(result, "document_shell")] == ["review"] * 4


def test_html_comments_are_dropped(tmp_path):
    result = tiny(tmp_path, [html("<p>a<!-- note --></p><!--[if IE]><link rel=x><![endif]-->")])
    assert body(result) == ["<p>a</p>"]
    assert sorted(f.detail for f in flags(result, "html_comment_dropped")) == ["comment", "conditional comment"]


def test_style_becomes_an_object_or_is_omitted(tmp_path):
    result = tiny(tmp_path, [html('<p style="margin-left: 40%; -webkit-transition: none; TEXT-ALIGN:CENTER">a</p>'
                                  '<p style="color: red !important">b</p><p style="float: sideways">c</p>')])
    lines = body(result)
    assert '<p style={{ marginLeft: "40%", WebkitTransition: "none", textAlign: "center" }}>a</p>' in lines
    assert "<p>b</p>" in lines and "<p>c</p>" in lines
    assert [f.status for f in flags(result, "unsupported_style")] == ["review", "review"]


def test_entities_are_decoded_with_html5_rules(tmp_path):
    lines = body(tiny(tmp_path, [html("<p>a&nbsp;b &nbspc &#8377;5 &amp; &lt;x&gt;</p>")]))
    assert lines == ['<p>{"a\\u00a0b \\u00a0c \\u20b95 & <x>"}</p>']


def test_text_with_braces_or_angle_brackets_is_a_string_literal(tmp_path):
    lines = body(tiny(tmp_path, [html("<p>{x}</p><p>plain</p>")]))
    assert '<p>{"{x}"}</p>' in lines and "<p>plain</p>" in lines


def test_whitespace_rules(tmp_path):
    lines = body(tiny(tmp_path, [html("<div>\n  <p>a   b\n c</p>\n  <p>x</p>\n</div><span>s</span>  <b>t</b>"
                                      "<pre>  keep\n   this </pre>")]))
    assert lines[:4] == ["<div>", "<p>a b c</p>", "<p>x</p>", "</div>"]  # whitespace between blocks dropped
    assert lines[4:7] == ["<span>s</span>", '{" "}', "<b>t</b>"]  # between inline nodes: one space
    assert '<pre>{"  keep\\n   this "}</pre>' in lines


def test_unmatched_closing_tag_is_dropped_and_flagged(tmp_path):
    result = tiny(tmp_path, [html("</div><p>a</p></span>")])
    assert body(result) == ["<p>a</p>"]
    assert [f.detail for f in flags(result, "unmatched_close")] == [
        "</div> closes nothing open; dropped (R-L6)", "</span> closes nothing open; dropped (R-L6)"]


def test_inline_js_is_dropped_and_flagged(tmp_path):
    result = tiny(tmp_path, [html('<button onclick="go()" class="b">x</button><script>alert(1)</script>')])
    assert body(result) == ['<button className="b">x</button>']
    assert sorted((f.status, f.detail) for f in flags(result, "inline_js_dropped")) == [
        ("review", "<script> and its content dropped"), ("review", "onclick on <button>")]


def test_links_to_php_pages_are_kept_and_flagged(tmp_path):
    result = tiny(tmp_path, [html('<a href="list.php?x=1">l</a><form action="save.php"></form><a href="/x.css">c</a>')])
    lines = body(result)
    assert '<a href="list.php?x=1">l</a>' in lines and '<form action="save.php" />' in lines
    assert [f.detail for f in flags(result, "legacy_link")] == ["href points at a .php page",
                                                                 "action points at a .php page"]


def test_attributes_react_types_do_not_have_are_dropped(tmp_path):
    result = tiny(tmp_path, [html('<option name="n" value="v">o</option><a tooltip="t" tooltip-placement="top">a</a>')])
    lines = body(result)
    assert '<option value="v">o</option>' in lines and '<a tooltip-placement="top">a</a>' in lines
    assert len(flags(result, "attribute_dropped")) == 2


# section 4: values and wrappers


def test_missing_field_is_a_c2todo_and_matched_field_a_value(tmp_path):
    missing = read("$s['nope']", "db_row_field", "$s", ["nope"], "nope")
    result = tiny(tmp_path, [html("<p>"), echo(STATS_TOTAL), html("</p><p>"), echo(missing), html("</p>")],
                  mapped=True)
    lines = body(result)
    assert "<p>{data.total}</p>" in lines
    assert '<p><C2Todo id="t001#00004" reason="missing_in_contract" /></p>' in lines


def test_context_reads_are_c2todos(tmp_path):
    result = tiny(tmp_path, [html("<p>"), echo(read("$_SESSION['u']", "session", "$_SESSION", ["u"])), html("</p><p>"),
                             echo(read("$_GET['q']", "request", "$_GET", ["q"])), html("</p>")])
    assert [t.reason for t in todos(result) if t.node_id != "page"] == ["needs_auth_context", "needs_request_context"]


def test_unsupported_wrapper_keeps_the_value_with_a_todo_comment(tmp_path):
    result = tiny(tmp_path, [html("<p>"), echo(computed("number_format", STATS_TOTAL, ", 2")), html("</p>")],
                  mapped=True)
    assert "<p>{data.total /* TODO(C2): number_format */}</p>" in body(result)
    (wrapper,) = flags(result, "unsupported_wrapper")
    assert wrapper.status == "review"
    assert [f.node_id for f in flags(result, "escaping_changed")] == ["t001#00002"]  # not escaped in PHP


def test_literal_echo_is_text(tmp_path):
    assert body(tiny(tmp_path, [html("<p>"), echo(read('"Active"', "literal")), html("</p>")])) == ["<p>Active</p>"]


def test_page_without_endpoint_renders_one_no_endpoint_c2todo(tmp_path):
    result = tiny(tmp_path, [html("<p>a</p>")])
    assert [(t.node_id, t.reason) for t in todos(result)] == [("page", "no_endpoint")]
    assert "export default function Page()" in text(result, "app/page/page.tsx")


# section 6: conditions in pages


def test_if_elseif_else_of_one_if_is_a_nested_ternary(tmp_path):
    result = tiny(tmp_path, [("if", [("then", "$_SESSION['a']", [html("<p>a</p>")]),
                                     ("elseif", "$_GET['b']", [html("<p>b</p>")]),
                                     ("else", None, [html("<p>c</p>")])])])
    lines = body(result)
    start = next(i for i, line in enumerate(lines) if "?" in line)
    assert lines[start:start + 7] == [
        '{unresolvedCondition("t001#00001", "needs_auth_context") ? (', "<p>a</p>",
        ') : unresolvedCondition("t001#00002", "needs_request_context") ? (', "<p>b</p>", ") : (", "<p>c</p>", ")}"]


def test_missing_condexpr_is_condition_unavailable(tmp_path):
    # Not the only output (that would be an R-I1 page guard, rendered without its condition).
    result = tiny(tmp_path, [html("<h1>t</h1>"), ("if", [("then", None, [html("<p>a</p>")])])])
    assert [t.reason for t in todos(result) if t.kind == "unresolvedCondition"] == ["condition_unavailable"]


# section 6: the whitelist, row by row

ROW = (("x", SchemaType("integer")), ("s", SchemaType("string")), ("n", SchemaType("number", nullable=True)),
       ("ok", SchemaType("boolean")))
ITEM = Scope(row_var="$row", row_fields=ROW)


@pytest.mark.parametrize("php, ts", [
    ("$row['x'] == 1", "row.x == 1"),
    ('$row["x"] == 1', "row.x == 1"),
])
def test_whitelist_row_field_in_the_item_type(php, ts):
    assert translate(php, ITEM, CONTEXT).code == ts


@pytest.mark.parametrize("php, scope", [
    ("$row['contact'] == 1", ITEM),  # not in the item type
    ("$row['x'] == 1", Scope(row_var="$row", row_fields=None)),  # the item type is unknown
    ("$other['x'] == 1", ITEM),  # not the Item's row
])
def test_whitelist_row_field_outside_the_item_type_is_not_translated(php, scope):
    assert translate(php, scope, CONTEXT).reason == "condition_not_translatable"


@pytest.mark.parametrize("op", ["==", "!=", "===", "!==", "<", ">", "<=", ">="])
def test_whitelist_comparison_operators_are_kept_loose_or_strict_as_written(op):
    assert translate(f"$row['x'] {op} 2", ITEM, CONTEXT).code == f"row.x {op} 2"


@pytest.mark.parametrize("php, ts", [
    ("$row['x'] == 1 && $row['x'] != 2", "row.x == 1 && row.x != 2"),
    ("$row['x'] == 1 || !$row['ok']", "row.x == 1 || !row.ok"),
    ("$row['x'] == 1 and $row['x'] == 2 || $row['x'] == 3", "row.x == 1 && (row.x == 2 || row.x == 3)"),
    ("$row['x'] == 1 && $row['x'] == 2 or $row['x'] == 3", "row.x == 1 && row.x == 2 || row.x == 3"),
    ("($row['x'] == 1)", "(row.x == 1)"),
])
def test_whitelist_logical_operators_keep_php_precedence(php, ts):
    assert translate(php, ITEM, CONTEXT).code == ts


@pytest.mark.parametrize("php, ts", [
    ("$row['x'] == 10", "row.x == 10"),
    ("$row['n'] >= 2.5", "row.n >= 2.5"),
    ("$row['s'] == 'it\\'s'", 'row.s == "it\'s"'),
    ('$row[\'s\'] == "a"', 'row.s == "a"'),
])
def test_whitelist_literals(php, ts):
    assert translate(php, ITEM, CONTEXT).code == ts


def test_whitelist_isset_of_a_row_field():
    assert translate("isset($row['n'])", ITEM, CONTEXT).code == "(row.n !== undefined && row.n !== null)"


def test_whitelist_list_guard_variable():
    guard = Scope(guard_var="$comments")
    assert translate("$comments", guard, CONTEXT).code == "items.length > 0"
    assert translate("$other", guard, CONTEXT).reason == "condition_not_translatable"
    assert translate("$comments", Scope(), CONTEXT).reason == "condition_not_translatable"


@pytest.mark.parametrize("php, reason", [
    ("$_SESSION['role'] == 'admin'", "needs_auth_context"),
    ("isset($_GET['id'])", "needs_request_context"),
    ("Users::is_logged_in()", "condition_not_translatable"),
    ("count($row['x']) > 0", "condition_not_translatable"),
    ('$selected == "home"', "condition_not_translatable"),
    ("$row['x'] == 010", "condition_not_translatable"),  # PHP octal
    ('$row[\'s\'] == "$v"', "condition_not_translatable"),  # interpolation
    ("$row['x'] == $row['x'] == 1", "condition_not_translatable"),  # not valid PHP
    ("$row['s']", "condition_not_translatable"),  # PHP "0" is false, JavaScript "0" is true
    (None, "condition_unavailable"),
])
def test_anything_else_is_not_translated(php, reason):
    translation = translate(php, ITEM, CONTEXT)
    assert (translation.code, translation.reason) == (None, reason)


def test_string_compared_with_number_is_loose_compare_semantics():
    assert translate("$row['s'] == 1", ITEM, CONTEXT).reason == "loose_compare_semantics"


# ---------------------------------------------------------------- the rest


def test_spec_numeric_props_are_number_only_in_react_types():
    react = GEN_CONFIG.react
    numeric = {p: k for props in react.props.values() for p, k in props.items()}
    for name in ("size", "colSpan", "rowSpan", "tabIndex", "maxLength", "minLength", "cols", "rows", "span", "start"):
        assert numeric[name] == "n", name


def test_generation_config_rejects_unknown_keys(tmp_path):
    doc = json.loads((ROOT / "config" / "generation.json").read_text(encoding="utf-8"))
    doc["extra"] = 1
    path = tmp_path / "g.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="expected keys"):
        load_generation_config(path)


def test_stage5_json_lists_files_and_counts(runs):
    _, _, result = runs["list_while"]
    doc = json.loads(stage5.dumps(result, "output/generated/list_while"))
    assert doc["files"] == ["app/admin-panel1/page.tsx", "components/AppointmentsItem.tsx",
                            "components/AppointmentsList.tsx", "lib/api-types.ts", "lib/api.ts", "lib/c2-todo.tsx"]
    assert doc["counts"]["c2todos"] == {"missing_in_contract": 1}
    assert doc["counts"]["components"] == 3 and doc["counts"]["componentsWithoutC2Todo"] == 2
    assert doc["counts"]["metrics"] == {"componentsWithoutC2Todo": "2/3", "flagRate": 12}
    assert doc["counts"]["severity"] == {"todo": 1, "review": 11, "info": 23}
    (todo,) = doc["c2todos"]
    assert (todo["file"], todo["line"], todo["sourceLine"]) == ("components/AppointmentsItem.tsx", 13, 468)


def test_cli_stage_5_writes_files_and_prints_the_summary(tmp_path):
    env = {**os.environ, "PYTHONUTF8": "1", "COLUMNS": "200"}
    out = subprocess.run([sys.executable, str(ROOT / "src" / "main.py"), "--timeline",
                          str(MOCKS / "timeline_list_while.json"), "--labels", str(MOCKS / "labels_list_while.json"),
                          "--stage", "5", "--output", str(tmp_path)], capture_output=True, encoding="utf-8",
                         env=env, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert ("[Stage 5] 2/3 components without C2Todo · 1 todo, 11 review, 23 info · flag rate 12 · 6 files"
            in out.stdout)
    assert "todo by reason: missing_in_contract 1" in out.stdout
    assert "escaping_changed: 11 unescaped outputs (info, not in the flag rate)" in out.stdout
    assert out.stdout.count("escaping_changed") == 1  # one line per page, not one per output
    assert (tmp_path / "list_while" / "components" / "AppointmentsItem.tsx").exists()


# ---------------------------------------------------------------- spec v0.2: severity, metrics, string compares, nesting


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_entry_has_a_severity_and_the_flag_rate_counts_todo_and_review(name, runs):
    _, _, result = runs[name]
    doc = json.loads(stage5.dumps(result, "x"))
    for t in doc["c2todos"]:
        assert t["severityIfRendered"] == "todo"
        assert t["severity"] == ("info" if t["inAbstained"] else "todo"), t
    for f in doc["flags"]:
        if_rendered = "todo" if f["flag"] in ("attribute_omitted", "output_dropped") else f["status"]
        assert f["severityIfRendered"] == if_rendered, f
        assert f["severity"] == ("info" if f["inAbstained"] else if_rendered), f  # v0.4: abstained counted once
    severity = doc["counts"]["severity"]
    entries = doc["c2todos"] + doc["flags"]
    assert severity == {s: sum(e["severity"] == s for e in entries) for s in ("todo", "review", "info")}
    assert doc["counts"]["metrics"]["flagRate"] == severity["todo"] + severity["review"]
    components = doc["components"]
    assert doc["counts"]["metrics"]["componentsWithoutC2Todo"] == \
        f"{sum(c['c2todos'] == 0 for c in components)}/{len(components)}"


@pytest.mark.parametrize("flag", ["escaping_changed", "legacy_link", "html_comment_dropped", "unclosed_at_end",
                                  "numeric_string_compare"])
def test_info_flags_are_outside_the_flag_rate(flag, runs):
    for _, _, result in runs.values():
        assert all(f.severity == "info" for f in flags(result, flag))


def test_escaping_changed_is_never_raised_on_literal_output(runs):
    _, _, result = runs["list_while"]
    literal = {"f001#00512", "f001#00526", "f001#00540"}  # echo "Active" etc.
    assert not literal & {f.node_id for f in flags(result, "escaping_changed")}


def test_numeric_string_compare_notes_from_the_translator():
    assert translate("$row['s'] == 'a'", ITEM, CONTEXT).notes == ('row.s == "a"',)
    assert translate("$row['s'] != 'a' && $row['s'] == 'b'", ITEM, CONTEXT).notes == (
        'row.s != "a"', 'row.s == "b"')
    assert translate("$row['s'] === 'a'", ITEM, CONTEXT).notes == ()  # strict: PHP compares as written
    assert translate("$row['x'] == 1", ITEM, CONTEXT).notes == ()


def test_numeric_string_compare_is_flagged_on_a_translated_condition(tmp_path):
    name = read("$row['name']", "db_row_field", "$row", ["name"], "name")
    result = tiny(tmp_path, [html("<ul>"), ("loop", [
        html("<li>"), echo(name),
        ("if", [("then", "$row['name'] == 'a'", [html("!")])]),
        ("if", [("then", "$row['n'] == 1", [html("?")])]),
        html("</li>")]), html("</ul>")], mapped=True, contract=LIST_CONTRACT)
    code = "\n".join(f.text for f in result.files if f.path.startswith(("app/", "components/")))
    assert '{row.name == "a" && "!"}' in code and '{row.n == 1 && "?"}' in code
    (note,) = flags(result, "numeric_string_compare")
    assert note.severity == "info" and note.detail.startswith('row.name == "a"')


def test_html_nesting_on_the_mocks(runs):
    found = {name: [(f.function, f.in_abstained, f.detail) for f in flags(result, "html_nesting_changed")]
             for name, (_, _, result) in runs.items()}
    assert found.pop("wp_login") == [("Page", False, "div > table: generated children [form]; an HTML5 parser "
                                                     "builds [form, tbody]")]
    assert found.pop("edge_cases") == [("EdgeCasesList1", True, "table: generated children [tr]; an HTML5 parser "
                                                                "builds [tbody]")]
    assert all(not v for v in found.values()), found


EXPECTED_FLAG_RATES = {"list_while": 12, "list_foreach": 12, "admin": 6, "detail": 5, "edge_cases": 10,
                       "wp_guestbook": 5, "wp_view": 29, "wp_login": 4, "wp_header": 12, "wp_thumbnails": 2}


def test_abstained_code_is_counted_once(runs):
    """Spec v0.4 section 12: inside abstained code every entry, review and todo alike, counts as info."""
    _, _, result = runs["edge_cases"]
    (nested,) = flags(result, "html_nesting_changed")
    assert (nested.in_abstained, nested.severity, nested.severity_if_rendered) == (True, "info", "review")
    inner = [t for t in result.todos if t.in_abstained]
    assert len(inner) == 6 and {(t.severity, t.severity_if_rendered) for t in inner} == {("info", "todo")}
    (region,) = todos(result, "cuts_across_business_logic")
    assert (region.in_abstained, region.severity) == (False, "todo")  # the one todo for the region
    _, _, thumbnails = runs["wp_thumbnails"]
    omitted = [f for f in flags(thumbnails, "attribute_omitted:unresolved_read")]
    assert omitted and all((f.in_abstained, f.severity, f.severity_if_rendered) == (True, "info", "todo")
                           for f in omitted)
    rates = {name: stage5.counts(r)["metrics"]["flagRate"] for name, (_, _, r) in runs.items()}
    assert rates == EXPECTED_FLAG_RATES and sum(rates.values()) == 97


def test_html_nesting_flag_points_at_the_element(runs):
    _, _, result = runs["wp_login"]
    (nested,) = flags(result, "html_nesting_changed")
    line = text(result, nested.file).split("\n")[nested.line - 1]
    assert nested.severity == "review" and line.strip().startswith("<table ")


def test_a_div_directly_inside_a_tr_is_caught(tmp_path):
    result = tiny(tmp_path, [html("<table><tbody><tr><div>x</div><td>y</td></tr></tbody></table>")])
    (nested,) = flags(result, "html_nesting_changed")
    # The browser moves the <div> out in front of the table; React would keep it in the row.
    assert nested.detail == ("(top level): generated children [table]; an HTML5 parser builds [div, table]")


def test_valid_nesting_is_not_flagged(tmp_path):
    result = tiny(tmp_path, [html("<table><thead><tr><th>a</th></tr></thead><tbody><tr><td>b<br>c</td></tr>"
                                  "</tbody></table><ul><li><a>x</a></li></ul><p>t <b>u</b></p>")])
    assert not flags(result, "html_nesting_changed")


@pytest.mark.parametrize("html_in, path", [
    ("<p><div>x</div></p>", "(top level)"),  # a <div> closes the open <p>
    ("<table><td>x</td></table>", "table"),  # the parser adds <tbody> and <tr>
    ("<select><option>a</option><div>b</div></select>", "select"),
])
def test_nesting_compare_finds_reparented_elements(html_in, path):
    from src.mapping import nesting

    def parse(markup):  # a tiny reader for these well-formed snippets
        stack, root = [], []
        for token in re.findall(r"</?\w+>|[^<]+", markup):
            if token.startswith("</"):
                stack.pop()
            elif token.startswith("<"):
                node = nesting.Node(token[1:-1], label=token[1:-1])
                (stack[-1].children if stack else root).append(node)
                stack.append(node)
            else:
                (stack[-1].children if stack else root).append(nesting.TEXT)
        return root

    differences = nesting.compare(parse(html_in), frozenset(CONFIG.void_tags))
    assert differences and differences[0].path == path


def test_test_app_tsconfig_is_strict(tmp_path):
    assert compile_check.strict_enabled()  # output/test-app/tsconfig.json
    loose = tmp_path / "tsconfig.json"
    loose.write_text(json.dumps({"compilerOptions": {"strict": False}}), encoding="utf-8")
    assert not compile_check.strict_enabled(loose)
    loose.write_text(json.dumps({"compilerOptions": {}}), encoding="utf-8")
    assert not compile_check.strict_enabled(loose)


def test_cli_prints_the_metrics_for_every_mock(runs):
    for _, _, result in runs.values():
        c = stage5.counts(result)
        line = stage5.summary(result)
        assert f"{c['metrics']['componentsWithoutC2Todo']} components without C2Todo" in line
        assert f"flag rate {c['metrics']['flagRate']}" in line


# ---------------------------------------------------------------- boundary-rules v0.7 R-L3 scope, spec v0.4 fallback

ROW_NAME = read("$row['name']", "db_row_field", "$row", ["name"], "name")
CASE_A = [html("<select>"), ("loop", [  # an if inside the row's one element
    html("<option>"), echo(ROW_NAME), ("if", [("then", "$row['name'] == 'a'", [html(" (default)")])]),
    html("</option>")]), html("</select>")]
CASE_B = [html("<h1>Rows</h1><select>"), ("loop", [  # an if wrapping the row's one element (filter-in-loop)
    ("if", [("then", "$row['n'] > 0", [html("<option>"), echo(ROW_NAME), html("</option>")])])]),
    html("</select>")]


def rows_list(result):
    return text(result, "components/RowsList.tsx")


def compiles(result, tmp_path, name):
    if compile_check.unavailable():
        return  # the compile check reports why it cannot run; the other assertions still hold
    stage5.write_files(result, tmp_path / "generated")
    checked = compile_check.check(name, tmp_path / "generated")
    assert checked.errors == 0, checked.output


def test_rl3_case_a_presentation_if_is_an_inline_conditional(tmp_path):
    tree, _, result = tiny_run(tmp_path, CASE_A, mapped=True, contract=LIST_CONTRACT)
    (inline_map,) = find(tree, rule="R-L3")
    assert [child.type for child in inline_map.children] == ["Static", "InlineConditional", "Static"]
    listing = rows_list(result)
    assert '<option key={i}>' in listing and '{row.name == "a" && " (default)"}' in listing
    assert not result.todos
    compiles(result, tmp_path, "rl3_case_a_presentation")


def test_rl3_case_a_business_logic_if_abstains_like_any_other_if(tmp_path):
    tree, _, result = tiny_run(tmp_path, CASE_A, mapped=True, contract=LIST_CONTRACT, if_concern="business_logic")
    (abstain,) = find(tree, type="Abstain")
    assert abstain.reason == "cuts_across_business_logic"
    (todo,) = result.todos
    assert (todo.reason, todo.severity) == ("cuts_across_business_logic", "todo")
    listing = rows_list(result)
    assert f'<C2Todo id="{abstain.source_ids[0]}" reason="cuts_across_business_logic" />' in listing
    assert "function Abstained_t001_00005({ row }: { row: ApiTinyResponse[\"rows\"][number] })" in listing
    compiles(result, tmp_path, "rl3_case_a_business_logic")


def test_rl3_case_b_fallback_replaces_only_the_list(tmp_path):
    _, _, result = tiny_run(tmp_path, CASE_B, mapped=True, contract=LIST_CONTRACT)
    (todo,) = result.todos
    assert (todo.reason, todo.node_id, todo.function) == ("markup_not_generatable", "t001#00002", "RowsList")
    assert todo.detail.startswith("List RowsList: ")
    assert "return (\n    <C2Todo id=\"t001#00002\" reason=\"markup_not_generatable\" />\n  );" in rows_list(result)
    page = text(result, "app/page/page.tsx")
    assert "<h1>Rows</h1>" in page and "<RowsList items={data.rows} />" in page  # the page itself survives
    compiles(result, tmp_path, "rl3_case_b_presentation")


def test_rl3_case_b_business_logic_if_abstains(tmp_path):
    tree, _, result = tiny_run(tmp_path, CASE_B, mapped=True, contract=LIST_CONTRACT, if_concern="business_logic")
    (abstain,) = find(tree, type="Abstain")
    assert abstain.reason == "cuts_across_business_logic"
    listing = rows_list(result)
    assert '<C2Todo id="t001#00003" reason="cuts_across_business_logic" key={i} />' in listing
    assert [t.reason for t in result.todos] == ["cuts_across_business_logic"]
    compiles(result, tmp_path, "rl3_case_b_business_logic")


def test_page_fallback_is_kept_when_the_page_is_the_smallest_component(tmp_path):
    # A loop inside an attribute value cannot be placed anywhere: Stage 2 abstains on it, and the markup of the
    # page itself cannot be built, so the page is the smallest enclosing component.
    result = tiny(tmp_path, [html('<a href="'), ("loop", [html("x")]), html('">t</a>')])
    assert [(t.node_id, t.reason) for t in result.todos if t.reason != "no_endpoint"] == [
        ("page", "markup_not_generatable")]


def test_list_whose_container_holds_page_output_falls_back_alone(tmp_path):
    # HMS: `if(!$result){ echo mysqli_error($con); }` sits in the <table>, before the row loop. The table is
    # the List's container (R-L2a) but also holds page output, so the List cannot own it: the List becomes one
    # C2Todo (spec v0.4 section 12: the smallest enclosing component, never the page).
    result = tiny(tmp_path, [
        html("<h1>Rows</h1><table><tbody>"),
        ("if", [("then", "!$result", [echo(read("mysqli_error($con)"))])]),
        ("loop", [html("<tr><td>"), echo(ROW_NAME), html("</td></tr>")]),
        html("</tbody></table>")], mapped=True, contract=LIST_CONTRACT)
    fallback = [t for t in result.todos if t.reason == "markup_not_generatable"]
    assert [(t.function, t.node_id) for t in fallback] == [("RowsList", "t001#00005")]  # the loop's id
    assert "its container also holds" in fallback[0].detail
    page = text(result, "app/page/page.tsx")
    assert "<h1>Rows</h1>" in page and "<table>" in page and "<RowsList items={data.rows} />" in page
    assert not any(f.path == "components/RowsItem.tsx" for f in result.files)
    compiles(result, tmp_path, "list_container_shared")
