"""Roadmap Phase 4b: the post-freeze data wiring (reconciliation-spec v0.3, generation-spec v0.13) and the
difference classifier of the runtime check (tools/runtime/classify.py). Synthetic input only.

a) Stage 4 maps a List exactly to an endpoint-map entry keyed by its loop's queryNodeId; b) Stage 5 fetches it
in the function rendering the List (a page or a function component, then async); c) the guard: without such
entries nothing changes.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
sys.path.insert(0, str(ROOT / "tools" / "runtime" / "replay"))
import classify  # noqa: E402
import compare  # noqa: E402
import compile_check  # noqa: E402
import generate  # noqa: E402
import run_apps  # noqa: E402
from ast_producer import SourceFile, parse_sources  # noqa: E402
from harness import FileHarness  # noqa: E402

from src.mapping import stage4_reconcile  # noqa: E402
from tests.test_harness import harness_app, pytestmark  # noqa: E402, F401
from tests.test_phase3 import flat  # noqa: E402

SCHEMA = {"users": ["id", "name"], "doctors": ["name", "fee"]}
TYPES = {"users": {"id": ("integer", False), "name": ("string", False)},
         "doctors": {"name": ("string", False), "fee": ("integer", False)}}

PAGE = ("<?php $c = mysqli_connect('h', 'u', '', 'd');\n"
        "$a = mysqli_query($c, 'select id, name from users');\n"
        "$b = mysqli_query($c, 'select name, fee from doctors'); ?>\n"
        "<table><?php while ($u = mysqli_fetch_assoc($a)) { ?><tr><td><?php echo $u['name']; ?></td></tr><?php } ?>"
        "</table>\n"
        "<ul><?php while ($d = mysqli_fetch_assoc($b)) { ?><li><?php echo $d['name']; ?> <?php echo $d['fee']; ?>"
        "</li><?php } ?></ul>")


def run_with(tmp_path, files: dict, mapping: str, tsc: bool = False) -> dict:
    """The app `tiny` through Stages 1-5 with a substitute contract / map built from its own queries:
    mapping = "query" (per-query entries), "page" (one page entry with every query's array), "empty" (the contract
    and a map with no entry) or "none" (neither)."""
    sources = []
    for index, (name, php) in enumerate(sorted(files.items()), start=1):
        (tmp_path / "src" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / name).write_bytes(php.encode("utf-8"))
        sources.append((name, SourceFile(tmp_path / "src" / name, f"t{index:03d}", f"legacy-apps/tiny/{name}")))
    asts, _ = parse_sources([s for _, s in sources])
    harnesses = {name: FileHarness(s.source.read_bytes(), ast, s.path, SCHEMA) for (name, s), ast in zip(sources, asts)}
    pages, silent = harness_app.build_timelines(harnesses, lambda path: {"generator": "c2-harness"})
    timelines = harness_app.AppTimelines("tiny", tuple(pages), tuple(silent), {}, len(files), (), {}, "",
                                         "legacy-apps/tiny")
    units = [{"entrypoint": p.timeline["entrypoint"], "queries": p.timeline["queries"]}
             for p in pages if p.timeline.get("queries")]
    built = generate.build(units, TYPES)
    contract, endpoint_map = built["contract"], built["endpoint_map"]
    if mapping == "page":  # the v0.2 rule: one page entry whose response holds every query's array
        page = next(p.timeline["entrypoint"] for p in pages if p.function is None and p.timeline.get("queries"))
        arrays = {f"q{n}": path["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["rows"]
                  for n, path in enumerate(contract["paths"].values())}
        contract["paths"]["/api/page"] = {"get": {"responses": {"200": {"description": "x", "content": {
            "application/json": {"schema": {"type": "object", "required": sorted(arrays), "properties": arrays}}}}}}}
        endpoint_map["endpoints"] = {page: {"method": "get", "path": "/api/page", "status": "200",
                                            "mediaType": "application/json"}}
    if mapping == "empty":
        endpoint_map["endpoints"] = {}
    (tmp_path / "c.json").write_text(json.dumps(contract), encoding="utf-8")
    (tmp_path / "m.json").write_text(json.dumps(endpoint_map), encoding="utf-8")
    index = run_apps.run_app(timelines, tmp_path / "out", tsc=tsc,
                             **({} if mapping == "none" else {"contract_path": tmp_path / "c.json",
                                                              "endpoint_map_path": tmp_path / "m.json"}))
    folder = tmp_path / "out" / "tiny"
    return {"index": index, "stage": lambda key, n: json.loads((folder / key / f"stage{n}.json").read_text(
        encoding="utf-8")), "file": lambda path: (folder / run_apps.NEXT_APP / path).read_text(encoding="utf-8"),
        "folder": folder, "built": built}


# ---------------------------------------------------------------- a) Stage 4: per-query entries


def test_each_list_maps_to_its_own_query_endpoint(tmp_path):
    result = run_with(tmp_path, {"page.php": PAGE}, "query")
    stage4 = result["stage"]("page.php", 4)
    assert [(c["result"], c["schemaProperty"]) for c in stage4["collections"]] == [("mapped", "rows")] * 2
    assert [e["path"] for e in stage4["listEndpoints"]] == ["/api/c2-replay/t001-00017", "/api/c2-replay/t001-00026"]
    matched = [(r["need"]["column"], r["result"], r["schemaProperty"]) for r in stage4["results"]]
    assert matched == [("name", "matched", "rows[].name"), ("name", "matched", "rows[].name"),
                       ("fee", "matched", "rows[].fee")]
    assert stage4["endpoint"] is None  # the page itself has no entry


def test_a_page_entry_keeps_the_one_list_rule(tmp_path):
    result = run_with(tmp_path, {"page.php": PAGE}, "page")
    stage4 = result["stage"]("page.php", 4)
    assert [(c["result"], c["reason"]) for c in stage4["collections"]] == [
        ("cannot_reconcile", "list_not_mapped_to_array")] * 2
    assert "listEndpoints" not in stage4


def test_an_entry_whose_endpoint_has_several_arrays_is_not_mapped():
    """A per-query endpoint is exact only when its response has one array (the rows)."""
    two_arrays = type("Shape", (), {"arrays": (("a", ()), ("b", ()))})()
    assert stage4_reconcile._map_collection("0.1", object(), object(), two_arrays, {}, set()) == (
        "cannot_reconcile", "list_not_mapped_to_array", None)


# ---------------------------------------------------------------- b) Stage 5: who fetches


def test_the_page_fetches_each_lists_own_endpoint(tmp_path):
    result = run_with(tmp_path, {"page.php": PAGE}, "query")
    page = flat(result["file"]("app/(plain)/page/page.tsx"))
    assert "export default async function Page() {" in page
    assert ("const apiC2ReplayT00100017 = await getApiC2ReplayT00100017();"
            "const apiC2ReplayT00100026 = await getApiC2ReplayT00100026();") in page
    assert "<PageList1 items={apiC2ReplayT00100017.rows} />" in page and \
        "<PageList2 items={apiC2ReplayT00100026.rows} />" in page
    item = flat(result["file"]("components/PageList2.tsx"))  # one <li> with no child element: R-L3, an inline map
    assert 'items: ApiC2ReplayT00100026Response["rows"][number][]' in item
    assert "{row.name}" in item and "{row.fee}" in item
    api = result["file"]("lib/api.ts")
    assert api.count("export async function") == 2 and api.count("export function authHeaders") == 1


def test_a_function_component_with_its_own_endpoint_is_async(tmp_path):
    """HMS newfunc.php display_docs(): `select * from doctb`, called inside a page's <select>."""
    files = {"lib.php": "<?php function docs() { global $c;\n"
                        "  $q = mysqli_query($c, 'select name, fee from doctors');\n"
                        "  while ($d = mysqli_fetch_assoc($q)) { echo '<option>' . $d['name'] . '</option>'; } }",
             "page.php": "<?php include 'lib.php'; ?><select><?php docs(); ?></select>"}
    result = run_with(tmp_path, files, "query")
    component = flat(result["file"]("components/Docs.tsx"))
    assert component.startswith('import { getApiC2ReplayT00100008 } from "../lib/api";')
    assert "export async function Docs() {const apiC2ReplayT00100008 = await getApiC2ReplayT00100008();" in component
    assert "<select><Docs /></select>" in flat(result["file"]("app/(plain)/page/page.tsx"))


def test_the_wired_app_compiles(tmp_path):
    if compile_check.unavailable() is not None:
        pytest.skip(compile_check.unavailable())
    files = {"lib.php": "<?php function docs() { global $c;\n"
                        "  $q = mysqli_query($c, 'select name, fee from doctors');\n"
                        "  while ($d = mysqli_fetch_assoc($q)) { echo '<li>' . $d['name'] . '</li>'; } }",
             "page.php": PAGE + "<?php include 'lib.php'; ?><ol><?php docs(); ?></ol>"}
    index = run_with(tmp_path, files, "query", tsc=True)["index"]
    assert index["tscRan"] and index["tscUnattributed"] == 0, index["tscOutput"]
    assert [(p["key"], p["tsc"]) for p in index["pages"] if p["tsc"] not in (0, None)] == []


# ---------------------------------------------------------------- c) the guard


def test_without_entries_the_outputs_are_byte_identical(tmp_path):
    """No contract / endpoint map at all, or a contract with a map that has no entry for these units: every stage
    file and every generated file is the same (the freeze guard on a small app; the five apps are proven by
    hashes in the Phase 4b report)."""
    def tree(folder: Path) -> dict:
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
                and p.name != "index.json"}  # (the index names the substitute files it was given)
    none = tree(run_with(tmp_path, {"page.php": PAGE}, "none")["folder"])
    empty = tree(run_with(tmp_path, {"page.php": PAGE}, "empty")["folder"])
    assert none == empty and "next-app/app/(plain)/page/page.tsx" in none
    assert b"listEndpoints" not in none["page.php/stage4.json"]


# ---------------------------------------------------------------- the classifier (synthetic captures)


def unit_folder(tmp_path, sequence, flags, todos=()) -> Path:
    folder = tmp_path / "unit"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "timeline.json").write_text(json.dumps({"entrypoint": "legacy-apps/x/p.php", "sequence": sequence}),
                                          encoding="utf-8")
    (folder / "stage5.json").write_text(json.dumps({"flags": list(flags), "c2todos": list(todos)}), encoding="utf-8")
    return folder


def flag(node, kind, detail, reason=None):
    return {"flag": kind, "kind": "flag", "reason": reason or kind, "nodeId": node, "detail": detail,
            "file": "app/p/page.tsx", "line": 1, "sourceLine": 1}


def raw(node_id, text, loops=()):
    return {"id": node_id, "kind": "Stmt_InlineHTML", "raw": text,
            "enclosedBy": [{"nodeId": loop, "role": "iteration"} for loop in loops]}


def test_an_attribute_difference_needs_the_flag_at_the_node_that_printed_the_tag(tmp_path):
    sequence = [raw("n1", '<p><a href="#a" onclick="go(1)">a</a>'), raw("n2", '<a href="#b" onclick="go(2)">b</a></p>')]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [flag("n2", "inline_js_dropped", "onclick on <a>")]))
    element = compare.Node("a", (("href", "#b"), ("onclick", "go(2)")), "b", [])
    assert classify.explain_attributes([unit], element, ["onclick"])["nodeId"] == "n2"
    other = compare.Node("a", (("href", "#a"), ("onclick", "go(1)")), "a", [])
    assert classify.explain_attributes([unit], other, ["onclick"]) is None  # a flag elsewhere does not count


def test_a_style_is_matched_in_its_canonical_form_and_ties_must_all_carry_the_flag(tmp_path):
    sequence = [raw("n1", '<body style="padding-top: 50px;">'), raw("n2", "<option value='' selected>x</option>"),
                raw("n3", "<option value='' selected>y</option>")]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [
        flag("n1", "document_shell", "<body style> dropped, children kept: it belongs in app/layout.tsx"),
        flag("n2", "option_selected_not_converted", "selected on <option> omitted")]))
    body = compare.Node("body", (("style", "padding-top:50px"),), "", [])
    assert classify.explain_attributes([unit], body, ["style"])["kind"] == "document_shell"
    option = compare.Node("option", (("selected", ""), ("value", "")), "x", [])
    assert classify.explain_attributes([unit], option, ["selected"]) is None  # n3, the tie, has no flag


def test_missing_rows_are_tied_to_their_lists_gap_by_the_tables_position(tmp_path):
    sequence = [raw("n1", "<table><tr><th>h</th></tr>"), raw("n2", "<tr><td>", loops=["L1"]),
                {"id": "n3", "kind": "Stmt_Echo", "enclosedBy": [{"nodeId": "L1", "role": "iteration"}]},
                raw("n4", "</td></tr>", loops=["L1"]), raw("n5", "</table><table><tr><td>s</td></tr></table>")]
    todo = {"kind": "C2Todo", "reason": "no_endpoint", "nodeId": "L1", "detail": "collection", "file": "f", "line": 1}
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [], [todo]))
    legacy = compare.body("<table><tr><th>h</th></tr><tr><td>1</td></tr></table><table><tr><td>s</td></tr></table>")
    found = classify.explain_by_order([unit], legacy, "body>table[0]>tbody[0]>tr[1]")
    assert (found["reason"], found["via"]) == ("no_endpoint", "<table> 1 of 2")
    assert classify.explain_by_order([unit], legacy, "body>table[1]>tbody[0]>tr[0]") is None  # no loop there


def test_dropping_the_head_explains_only_what_stood_in_it(tmp_path):
    sequence = [raw("n1", '<html><head><nav class="top">n</nav></head><body><nav class="low">m</nav>')]
    unit = classify.Unit("p.php", unit_folder(tmp_path, sequence, [
        flag("n1", "document_shell", "<head> and its content dropped")]))
    top, low = compare.Node("nav", (("class", "top"),), "n", []), compare.Node("nav", (("class", "low"),), "m", [])
    assert classify.explain_structure([unit], [top], missing=True)["kind"] == "document_shell"
    assert classify.explain_structure([unit], [low], missing=True) is None
    assert classify.explain_structure([unit], [top], missing=False) is None  # it cannot add an element


def test_compare_normalises_style_and_boolean_attributes():
    legacy = '<body><select required="required"><option selected="selected">a</option></select>' \
             '<p style="margin-top: 5%; color : red;">x</p></body>'
    generated = '<body><select required=""><option selected="">a</option></select>' \
                '<p style="margin-top:5%;color:red">x</p></body>'
    assert compare.compare(legacy, generated)["differences"] == []
    result = compare.compare('<body><p style="color: red">x</p></body>', '<body><p style="color:blue">x</p></body>')
    assert [d["names"] for d in result["differences"]] == [["style"]]
