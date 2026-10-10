"""Stage 4 (contract reconciliation), against docs/reconciliation-spec.md sections 2 and 3."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.mapping.boundary_config import load_boundary_config
from src.mapping.loader import load_contract, load_endpoint_map, load_labels, load_timeline
from src.mapping.stage1_isolation import isolate_presentation
from src.mapping.stage2_boundaries import infer_boundaries
from src.mapping.stage3_requirements import recover_requirements
from src.mapping.stage4_reconcile import dumps, reconcile, report_to_dict, summary

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
HMS = ("list_while", "list_foreach", "admin", "detail", "edge_cases")
WP = ("wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
ALL_MOCKS = HMS + WP
CONFIG = load_boundary_config()
CONTRACT = load_contract(MOCKS / "sample_contract.json")
ENDPOINT_MAP = load_endpoint_map(MOCKS / "endpoint_map.json")


def requirements(name):
    timeline = load_timeline(MOCKS / f"timeline_{name}.json")
    tree = infer_boundaries(isolate_presentation(timeline, load_labels(MOCKS / f"labels_{name}.json")), timeline, CONFIG)
    return recover_requirements(tree, timeline)


def run(name, contract=CONTRACT, endpoint_map=ENDPOINT_MAP):
    return reconcile(requirements(name), contract, endpoint_map)


@pytest.fixture(scope="module")
def reports():
    return {name: run(name) for name in ALL_MOCKS}


def results(report, result=None):
    return [r for r in report.results if result is None or r.result == result]


def contract_with_appointment_property(tmp_path, name):
    """A temporary copy of the contract with one extra property on the Appointment schema."""
    doc = json.loads((MOCKS / "sample_contract.json").read_text(encoding="utf-8"))
    doc["components"]["schemas"]["Appointment"]["properties"][name] = {"type": "string"}
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return load_contract(path)


# ---------------------------------------------------------------- section 3: expected results on the mocks


@pytest.mark.parametrize("name", ["list_while", "list_foreach"])
def test_list_pages_report_exactly_contact_as_missing(name, reports):
    (missing,) = results(reports[name], "missing")
    assert (missing.need.column, missing.reason, missing.component_type) == ("contact", "missing_in_contract", "Item")
    assert missing.hint is None


def canonical(report):
    ids = {}

    def walk(value, key=None):
        if isinstance(value, dict):  # the two pages are different files, so "file" differs too
            return {k: walk(v, k) for k, v in value.items() if k not in ("entrypoint", "file")}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        if key in ("nodeId", "queryNodeId") and value:
            return ids.setdefault(value, f"#{len(ids)}")
        if key == "expr" and isinstance(value, str) and ("while" in value or "fetch" in value):
            return "<loop header>"
        return value

    return walk(report_to_dict(report))


def test_while_and_foreach_give_identical_reports(reports):
    assert canonical(reports["list_while"]) == canonical(reports["list_foreach"])


def test_detail_has_nothing_missing(reports):
    assert not results(reports["detail"], "missing") and not results(reports["detail"], "cannot_reconcile")


def test_admin_session_reads_are_excluded_not_missing(reports):
    report = reports["admin"]
    assert not results(report, "missing")
    assert [(r.need.name, r.reason) for r in results(report, "excluded")] == [
        ("$username", "session"), ("$_SESSION['role']", "session")]
    assert [r.schema_property for r in results(report, "matched")] == ["total", "revenue"]


def test_edge_cases_ambiguous_and_unresolved_reads_cannot_be_reconciled(reports):
    report = reports["edge_cases"]
    assert not results(report, "missing")
    assert [(r.need.name, r.reason) for r in results(report, "cannot_reconcile")] == [
        ("$settings['hospital_name']", "unresolved_read"), ("doctor", "ambiguous_needs_schema"),
        ("prescription", "ambiguous_needs_schema"), ("docFees", "ambiguous_needs_schema")]


@pytest.mark.parametrize("name", WP)
def test_wackopicko_raises_no_false_alarm(name, reports):
    report = reports[name]
    assert not results(report, "missing") and not results(report, "matched")
    assert {(r.result, r.reason) for r in report.results if r.need.kind != "context"} <= {
        ("cannot_reconcile", "unresolved_read")}


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_sku_never_appears_as_a_need(name, reports):
    assert all(r.need.name != "sku" and r.need.column != "sku" for r in reports[name].results)


# ---------------------------------------------------------------- section 2 rules


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_context_needs_are_excluded_never_missing(name, reports):
    for r in reports[name].results:
        if r.need.kind == "context":
            assert (r.result, r.reason) == ("excluded", r.need.source_kind)


def test_unmapped_page_cannot_reconcile_its_field_needs(tmp_path):
    without = {k: v for k, v in ENDPOINT_MAP.items() if k != "legacy-apps/hms/admin-panel1.php"}
    report = run("list_while", endpoint_map=without)
    assert report.endpoint is None and not results(report, "missing") and not results(report, "matched")
    assert {(r.result, r.reason) for r in report.results} == {("cannot_reconcile", "no_endpoint")}
    assert report.unused == ()


@pytest.mark.parametrize("name", ["list_while", "list_foreach"])
def test_closing_the_contact_gap_leaves_nothing_missing(name, tmp_path):
    report = run(name, contract=contract_with_appointment_property(tmp_path, "contact"))
    assert not results(report, "missing")
    assert "appointments[].contact" in [r.schema_property for r in results(report, "matched")]


def test_no_fuzzy_match_but_a_case_hint(tmp_path):
    report = run("list_while", contract=contract_with_appointment_property(tmp_path, "Contact"))
    (missing,) = results(report, "missing")
    assert (missing.need.column, missing.hint) == ("contact", "Contact")
    assert "appointments[].Contact" in report.unused


@pytest.mark.parametrize("name", ["list_while", "detail", "admin"])
def test_matched_needs_carry_an_unverified_type(name, reports):
    matched = results(reports[name], "matched")
    assert matched and {r.type for r in matched} == {"unverified"}
    assert all(r.type is None for r in reports[name].results if r.result != "matched")


def test_list_rows_are_matched_against_the_array_item_schema(reports):
    report = reports["list_while"]
    (collection,) = report.collection_results
    assert (collection.component_id, collection.schema_property) == (report.results[0].need.row_of, "appointments")
    assert all(r.schema_property.startswith("appointments[].") for r in results(report, "matched"))


@pytest.mark.parametrize("name", ["list_while", "list_foreach", "detail"])
def test_unused_contract_properties_are_reported(name, reports):
    assert reports[name].unused == ("appointments[].userStatus", "appointments[].doctorStatus")


def test_endpoint_map_is_marked_as_a_substitute():
    provenance = json.loads((MOCKS / "endpoint_map.json").read_text(encoding="utf-8"))["_provenance"]
    assert provenance["substitute"] is True
    timelines = {json.loads((MOCKS / f"timeline_{n}.json").read_text(encoding="utf-8"))["entrypoint"]
                 for n in ALL_MOCKS}
    assert set(ENDPOINT_MAP) <= timelines, "every mapped entrypoint is a real timeline"
    assert all((r.method, r.path, r.status, r.media_type) in CONTRACT.endpoints for r in ENDPOINT_MAP.values())


# ---------------------------------------------------------------- output


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_two_runs_give_byte_identical_json(name, reports):
    assert dumps(run(name)) == dumps(reports[name])


def test_summary_line_has_the_spec_format(reports):
    """Spec v0.2 section 2.4, collections counted apart from field needs."""
    assert summary(reports["list_while"]) == (
        "[Stage 4] 11 needs: 10 matched, 1 missing, 0 excluded, 0 cannot reconcile · 1 collection mapped · 2 unused")
    assert "· 0 collections mapped ·" in summary(reports["admin"])
    assert "· 0 of 3 collections mapped ·" in summary(reports["wp_view"])
    pattern = (r"\[Stage 4\] \d+ needs: \d+ matched, \d+ missing, \d+ excluded, \d+ cannot reconcile"
               r" · (\d+ collections? mapped|\d+ of \d+ collections mapped) · \d+ unused")
    assert all(re.fullmatch(pattern, summary(r)) for r in reports.values())


# ---------------------------------------------------------------- v0.2 (b) collection results


@pytest.mark.parametrize("name", ["list_while", "list_foreach", "detail"])
def test_collection_of_a_mapped_list_page_is_mapped(name, reports):
    (collection,) = reports[name].collection_results
    assert (collection.result, collection.reason, collection.schema_property) == ("mapped", "mapped", "appointments")
    assert collection.need.kind == "collection"
    assert all(r.need.kind != "collection" for r in reports[name].results), "not mixed with field needs"
    assert (reports[name].counts.collections_mapped, reports[name].counts.collections_cannot_reconcile) == (1, 0)


@pytest.mark.parametrize("name, lists", [("edge_cases", 1), ("wp_guestbook", 1), ("wp_view", 3)])
def test_collection_of_an_unmapped_page_cannot_be_reconciled(name, lists, reports):
    collections = reports[name].collection_results
    assert len(collections) == lists
    assert {(c.result, c.reason) for c in collections} == {("cannot_reconcile", "no_endpoint")}


def test_several_lists_and_one_array_cannot_be_mapped(tmp_path):
    """wp_view has three Lists; pointed at a one-array response, which array holds which rows is undecidable."""
    view = json.loads((MOCKS / "timeline_wp_view.json").read_text(encoding="utf-8"))["entrypoint"]
    endpoint_map = {**ENDPOINT_MAP, view: ENDPOINT_MAP["legacy-apps/hms/admin-panel1.php"]}
    report = run("wp_view", endpoint_map=endpoint_map)
    assert {(c.result, c.reason) for c in report.collection_results} == {
        ("cannot_reconcile", "list_not_mapped_to_array")}


def test_endpoint_missing_from_the_contract_cannot_be_reconciled():
    ref = ENDPOINT_MAP["legacy-apps/hms/admin-panel1.php"]
    endpoint_map = {**ENDPOINT_MAP, "legacy-apps/hms/admin-panel1.php": type(ref)("get", "/api/nowhere", "200",
                                                                                  "application/json")}
    report = run("list_while", endpoint_map=endpoint_map)
    assert {(r.result, r.reason) for r in report.results} == {("cannot_reconcile", "endpoint_not_in_contract")}
    assert {(c.result, c.reason) for c in report.collection_results} == {
        ("cannot_reconcile", "endpoint_not_in_contract")}


# ---------------------------------------------------------------- v0.2 (c) needs from abstained content


def test_needs_inside_an_abstained_guard_are_marked(reports):
    admin = {r.need.name: r for r in reports["admin"].results}
    assert admin["total"].need.from_abstained and admin["revenue"].need.from_abstained
    assert admin["$_SESSION['role']"].need.from_abstained, "echoed inside the authorization guard"
    assert not admin["$username"].need.from_abstained, "echoed before the guard"
    assert (admin["total"].result, admin["total"].schema_property) == ("matched", "total"), "still reconciled"


def test_abstained_marking_reaches_the_json_and_the_cli(reports):
    written = report_to_dict(reports["edge_cases"])
    marked = {r["need"]["name"] for r in written["results"] if r["fromAbstained"]}
    assert marked == {"doctor", "prescription", "docFees", "$_GET['ID']"}
    assert written["collections"][0]["fromAbstained"] is True
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--timeline", "mocks/timeline_edge_cases.json",
         "--labels", "mocks/labels_edge_cases.json", "--stage", "4"],
        cwd=ROOT, capture_output=True, encoding="utf-8", check=True,
        env={**os.environ, "PYTHONUTF8": "1", "COLUMNS": "200"},
    )
    rows = {line.split()[2]: line for line in proc.stdout.splitlines() if line.lstrip().startswith(("Item", "Page"))}
    assert rows["doctor"].rstrip().endswith("yes") and not rows["$settings['hospital_name']"].rstrip().endswith("yes")


@pytest.mark.parametrize("name", ["list_while", "detail", "wp_guestbook"])
def test_nothing_is_marked_abstained_where_stage2_did_not_abstain(name, reports):
    report = reports[name]
    assert not any(r.need.from_abstained for r in report.results + report.collection_results)


# ---------------------------------------------------------------- v0.2 (d) unused is a lower bound


def test_unused_note_is_in_the_json_and_printed_under_the_table(reports):
    assert report_to_dict(reports["list_while"])["unusedNote"].startswith(
        "unused = not referenced by output reads; condition reads are not visible yet")
    assert report_to_dict(reports["list_while"])["unused"] == ["appointments[].userStatus",
                                                               "appointments[].doctorStatus"]
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--timeline", "mocks/timeline_list_while.json",
         "--labels", "mocks/labels_list_while.json", "--stage", "4"],
        cwd=ROOT, capture_output=True, encoding="utf-8", check=True, env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert "unused = not referenced by output reads; condition reads are not visible yet" in proc.stdout


# ---------------------------------------------------------------- v0.2 (e) source lines


def test_contact_is_reported_at_its_real_line(reports):
    (missing,) = results(reports["list_while"], "missing")
    (reference,) = missing.need.references
    assert (reference.file, reference.line) == ("legacy-apps/hms/admin-panel1.php", 468)


def test_a_node_without_loc_gets_no_line(tmp_path):
    doc = json.loads((MOCKS / "timeline_list_while.json").read_text(encoding="utf-8"))
    for entry in doc["sequence"]:
        entry.pop("loc")
    path = tmp_path / "timeline_list_while.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    timeline = load_timeline(path)
    tree = infer_boundaries(isolate_presentation(timeline, load_labels(MOCKS / "labels_list_while.json")),
                            timeline, CONFIG)
    report = reconcile(recover_requirements(tree, timeline), CONTRACT, ENDPOINT_MAP)
    assert all(ref.line is None for r in report.results for ref in r.need.references)
    assert summary(report) == summary(run("list_while")), "loc changes lines only, never a decision"


def test_cli_stage_all_prints_every_stage_and_the_missing_table(reports):
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--timeline", "mocks/timeline_list_while.json",
         "--labels", "mocks/labels_list_while.json", "--stage", "all"],
        cwd=ROOT, capture_output=True, encoding="utf-8", check=True,
        env={**os.environ, "PYTHONUTF8": "1", "COLUMNS": "160"},
    )
    for line in ("[Stage 1]", "[Stage 2]", "[Stage 3]", summary(reports["list_while"]), "contact",
                 "missing_in_contract"):
        assert line in proc.stdout
    for stage in (1, 2, 3, 4):
        assert (ROOT / "output" / f"stage{stage}_list_while.json").exists()
    written = json.loads((ROOT / "output" / "stage4_list_while.json").read_text(encoding="utf-8"))
    assert written == report_to_dict(reports["list_while"])
