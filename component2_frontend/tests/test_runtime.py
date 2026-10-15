"""Roadmap Phase 4a: the runtime rendering check's own tools, on synthetic input only (no app, no Docker, no
browser): the raw DOM comparison, the substitute replay generator, and the whole-app assembly of lib files.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
sys.path.insert(0, str(ROOT / "tools" / "runtime" / "replay"))
import compare  # noqa: E402
import generate  # noqa: E402
import run_apps  # noqa: E402

LEGACY = """<!DOCTYPE html><html><head><title>t</title></head>
<body class="main  page" id="b"><!-- a comment -->
  <h1>Doctor   List</h1>
  <table><tr><th>Name</th></tr><tr><td>ashok</td></tr><tr><td>arun</td></tr></table>
  <script>alert('x')</script>
  <p>end</p>
</body></html>"""


def kinds(result: dict) -> list:
    return [(d["kind"], d["path"]) for d in result["differences"]]


# ---------------------------------------------------------------- compare.py


def test_identical_pages_match_entirely_after_normalisation():
    generated = ("<html><body id=\"b\" class=\"main page\"><div hidden=\"\"><!--$--><!--/$--></div><h1>Doctor List</h1>"
                 "<table><tbody><tr><th>Name</th></tr><tr><td>ashok</td></tr><tr><td>arun</td></tr></tbody></table>"
                 "<p>end</p><script src=\"/_next/x.js\"></script></body></html>")
    result = compare.compare(LEGACY, generated)
    # html5lib builds the implicit <tbody> on both sides; comments, scripts and Next's boundary are dropped
    assert (result["legacyElements"], result["generatedElements"], result["matched"]) == (10, 10, 10)
    assert result["differences"] == []


def test_missing_rows_extra_elements_attributes_and_text_are_reported():
    generated = ("<html><body class=\"main\" id=\"b\"><h1>Doctor Lists</h1><table><tbody><tr><th>Name</th></tr>"
                 "</tbody></table><div>new</div><p>end</p></body></html>")
    result = compare.compare(LEGACY, generated)
    assert (result["legacyElements"], result["generatedElements"], result["matched"]) == (10, 7, 6)
    assert kinds(result) == [  # sorted by path
        ("attributes", "body"), ("extra", "body>div[0]"), ("text", "body>h1[0]"),
        ("missing", "body>table[0]>tbody[0]>tr[1]"), ("missing", "body>table[0]>tbody[0]>tr[2]")]
    assert result["differences"][0]["legacy"] == {"class": "main page", "id": "b"}
    assert [d.get("elements") for d in result["differences"] if d["kind"] == "missing"] == [2, 2]


def test_an_empty_page_against_a_page_with_content():
    result = compare.compare("", "<html><body><p>x</p></body></html>")
    assert (result["legacyElements"], result["generatedElements"], result["matched"]) == (0, 1, 0)
    assert kinds(result) == [("extra", "body>p[0]")]


def test_the_comparison_is_deterministic():
    generated = "<html><body><div><span>a</span><span>b</span></div><p>end</p></body></html>"
    assert compare.compare(LEGACY, generated) == compare.compare(LEGACY, generated)


def test_only_an_empty_hidden_div_first_in_the_generated_body_is_dropped():
    generated = "<html><body><div hidden=\"\">real</div></body></html>"
    assert compare.compare("<html><body><div hidden>real</div></body></html>", generated)["differences"] == []
    assert compare.body("<html><body><div hidden=\"\"></div><p>x</p></body></html>", generated=True).children[0].tag == "p"
    assert compare.body("<html><body><div hidden=\"\"></div><p>x</p></body></html>").children[0].tag == "div"


# ---------------------------------------------------------------- the substitute replay generator


SCHEMA = """CREATE TABLE `doctb` (
  `username` varchar(50) NOT NULL,
  `spec` varchar(50) NOT NULL,
  `docFees` int(10) NOT NULL,
  `note` text
) ENGINE=InnoDB;
CREATE TABLE `appointmenttb` (
  `ID` int(11) NOT NULL,
  `fee` decimal(10,2) NOT NULL,
  `username` varchar(50) NOT NULL
) ENGINE=InnoDB;"""


def query(sql, columns, tables, interpolated=False):
    return {"sql": sql, "columns": columns, "tables": tables, "valuesInterpolated": interpolated}


def test_column_types_come_from_the_schema():
    assert generate.column_types(SCHEMA) == {
        "doctb": {"username": ("string", False), "spec": ("string", False), "docFees": ("integer", False),
                  "note": ("string", True)},
        "appointmenttb": {"ID": ("integer", False), "fee": ("number", False), "username": ("string", False)}}


@pytest.mark.parametrize("q, reason", [
    (query('"select * from doctb"', ["username"], ["doctb"]), None),
    (query("'SELECT username FROM doctb;'", ["username"], ["doctb"]), None),
    (query('"select * from doctb where email=\'$e\'"', ["username"], ["doctb"], interpolated=True), "request_input"),
    (query('"delete from doctb"', ["username"], ["doctb"]), "not_a_select"),
    (query('"select 1; drop table doctb"', ["x"], ["doctb"]), "not_plain_sql"),
    (query('"select \\"x\\" from doctb"', ["x"], ["doctb"]), "not_plain_sql"),
    (query('"select distinct(spec) from doctb"', [], ["doctb"]), "columns_unknown"),
])
def test_only_a_plain_literal_select_is_replayable(q, reason):
    assert generate.plain_select(q) == reason


def test_the_generator_writes_endpoints_a_contract_and_a_map():
    types = generate.column_types(SCHEMA)
    pages = [
        {"entrypoint": "legacy-apps/x/list.php", "route": "list", "queries": {
            "f001#00010": query('"select * from doctb"', ["username", "spec", "docFees", "note"], ["doctb"]),
            "f001#00020": query('"select * from doctb where spec=\'$s\'"', ["username"], ["doctb"], True)}},
        {"entrypoint": "legacy-apps/x/join.php", "route": "join", "queries": {
            "f002#00010": query('"select username from doctb, appointmenttb"', ["username"],
                                ["doctb", "appointmenttb"])}},
        {"entrypoint": "legacy-apps/x/form.php", "route": "form", "queries": {
            "f003#00010": query('"select * from doctb where x=\'$x\'"', ["username"], ["doctb"], True)}}]
    result = generate.build(pages, types)
    assert result["endpoint_map"]["endpoints"] == {"legacy-apps/x/list.php": {
        "method": "get", "path": "/api/c2-replay/page/list", "status": "200", "mediaType": "application/json"}}
    assert result["replay"]["queries"] == {"/api/c2-replay/f001-00010": {
        "queryNodeId": "f001#00010", "sql": "select * from doctb", "columns": ["username", "spec", "docFees", "note"]}}
    assert result["replay"]["pages"] == {"/api/c2-replay/page/list": {"q_f001_00010": "/api/c2-replay/f001-00010"}}
    item = result["contract"]["paths"]["/api/c2-replay/f001-00010"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]["properties"]["rows"]["items"]
    assert item == {"type": "object", "required": ["username", "spec", "docFees"], "properties": {
        "username": {"type": "string"}, "spec": {"type": "string"}, "docFees": {"type": "integer"},
        "note": {"type": ["string", "null"]}}}
    assert [(r["queryNodeId"], r["replayable"], r.get("reason")) for r in result["report"]] == [
        ("f003#00010", False, "request_input"), ("f002#00010", False, "column_type_unknown"),
        ("f001#00010", True, None), ("f001#00020", False, "request_input")]
    assert result["contract"]["info"]["x-c2-substitute"] == "c2-replay"
    assert result["endpoint_map"]["_provenance"]["substitute"] == "c2-replay"


def test_the_substitute_contract_and_map_load_and_reconcile(tmp_path):
    import json
    from src.mapping.loader import load_contract, load_endpoint_map
    result = generate.build([{"entrypoint": "legacy-apps/x/list.php", "route": "list", "queries": {
        "f001#00010": query('"select * from doctb"', ["username", "spec"], ["doctb"])}}],
        generate.column_types(SCHEMA))
    (tmp_path / "c.json").write_text(json.dumps(result["contract"]), encoding="utf-8")
    (tmp_path / "m.json").write_text(json.dumps(result["endpoint_map"]), encoding="utf-8")
    contract, endpoints = load_contract(tmp_path / "c.json"), load_endpoint_map(tmp_path / "m.json")
    ref = endpoints["legacy-apps/x/list.php"]
    assert (ref.method, ref.path) == ("get", "/api/c2-replay/page/list")
    assert (ref.method, ref.path, ref.status, ref.media_type) in contract.endpoints


# ---------------------------------------------------------------- whole-app assembly of lib/ (post-freeze log)


def api_files(page: str):
    types = (f"// Response types for this page, from Component 1's contract (docs/generation-spec.md section 8).\n"
             f"// Fields not in the contract do not appear: a missing field is a C2Todo, never row.<field>.\n\n"
             f"export type {page}Response = {{\n  rows: {{\n    a: string;\n  }}[];\n}};\n")
    api = (f'import type {{ {page}Response }} from "./api-types";\n\n'
           "// TODO(C2): token forwarding is Component 1's design (docs/generation-spec.md section 8).\n"
           "export function authHeaders(): Record<string, string> {\n  return {};\n}\n\n"
           f"export async function get{page}(): Promise<{page}Response> {{\n  return fetch(\"/{page}\");\n}}\n")
    return types, api


STUB = "// No mapped endpoint: this page uses no response types.\nexport {};\n"


def test_two_units_api_files_are_merged():
    types_a, api_a = api_files("A")
    types_b, api_b = api_files("B")
    types, clash = run_apps.merge_lib("lib/api-types.ts", types_a, types_b)
    assert clash == [] and types.count("// Response types") == 1
    assert "export type AResponse" in types and "export type BResponse" in types
    api, clash = run_apps.merge_lib("lib/api.ts", api_a, api_b)
    assert clash == [] and api.startswith('import type { AResponse, BResponse } from "./api-types";\n\n')
    assert api.count("export function authHeaders") == 1
    assert "export async function getA()" in api and "export async function getB()" in api


def test_a_stub_merges_into_nothing_and_two_stubs_stay_a_stub():
    types_a, _ = api_files("A")
    merged, _ = run_apps.merge_lib("lib/api-types.ts", STUB, types_a)
    assert "No mapped endpoint" not in merged and "export {};" not in merged and "export type AResponse" in merged
    assert run_apps.merge_lib("lib/api-types.ts", STUB, STUB) == (STUB, [])


def test_a_name_declared_differently_is_a_collision_and_other_files_are_not_merged():
    types_a, _ = api_files("A")
    _, clash = run_apps.merge_lib("lib/api-types.ts", types_a, types_a.replace("a: string", "a: number"))
    assert clash == ["AResponse"]
    assert run_apps.merge_lib("components/X.tsx", "a", "b") == (None, [])
