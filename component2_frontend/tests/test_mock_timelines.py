"""Checks on the mock timelines, labels and contract that Component 2 develops against.

Three layers:
  * JSON-only checks: always run.
  * Source checks (byte-exact raw, raw SQL): need the HMS clone at legacy-apps/hms.
  * Regeneration checks: also need PHP; they rebuild every mock with
    tools/gen_timelines.py and compare it with the committed file.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest
from openapi_spec_validator import validate

from src.main import load_input, load_labels, load_legacy_ast, load_timeline

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
HMS = ROOT / "legacy-apps" / "hms"
sys.path.insert(0, str(ROOT / "tools"))
import gen_timelines as gen  # noqa: E402

NAMES = ("list_while", "list_foreach", "admin", "detail", "edge_cases")
HMS_SOURCES = {"list_while": "admin-panel1.php", "detail": "admin-panel.php"}
HAND_WRITTEN = ("admin", "edge_cases")
ENDPOINT_SCHEMA = {"list_while": "Appointment", "list_foreach": "Appointment",
                   "admin": "AppointmentStats", "detail": "PatientAppointment"}
EXPECTED_MISSING = {"list_while": {"contact"}, "list_foreach": {"contact"}, "admin": set(), "detail": set()}

needs_hms = pytest.mark.skipif(not (HMS / ".git").exists(), reason="HMS clone not at legacy-apps/hms")
needs_php = pytest.mark.skipif(gen.find_php() is None, reason="PHP not found (set PHP_BIN)")


@pytest.fixture(scope="module")
def timelines():
    return {n: load_timeline(MOCKS / f"timeline_{n}.json") for n in NAMES}


@pytest.fixture(scope="module")
def labels():
    return {n: load_labels(MOCKS / f"labels_{n}.json")["labels"] for n in NAMES}


@pytest.fixture(scope="module")
def contract():
    return json.loads((MOCKS / "sample_contract.json").read_text(encoding="utf-8"))


def flat_reads(reads):
    for r in reads:
        yield r
        yield from flat_reads(r.get("derivedFrom", []))


def all_reads(timeline):
    for entry in timeline["sequence"]:
        yield from flat_reads(entry.get("reads", []))


def raws(timeline):
    return [e["raw"].encode("utf-8") for e in timeline["sequence"] if e["kind"] == "Stmt_InlineHTML"]


def found_in_order(parts, src, lo=1, hi=10**9):
    """Every part occurs in src, in order, each starting within lines lo..hi."""
    pos = 0
    for part in parts:
        at = src.find(part, pos)
        if at < 0 or not lo <= src.count(b"\n", 0, at) + 1 <= hi:
            return False
        pos = at + len(part)
    return True


def needs(timeline):
    """Fields a page needs from the API: db-sourced reads only (session etc. excluded)."""
    return {r["path"][-1] for r in all_reads(timeline) if r["sourceKind"] == "db_row_field"}


# ---------------------------------------------------------------- format


@pytest.mark.parametrize("name", NAMES)
def test_timeline_and_labels_load_under_schema_1_0(name, timelines, labels):
    assert timelines[name]["schemaVersion"] == "1.0"
    assert labels[name]


@pytest.mark.parametrize("name", NAMES)
def test_every_node_the_timeline_mentions_is_labelled(name, timelines, labels):
    t = timelines[name]
    mentioned = {e["id"] for e in t["sequence"]} | set(t["queries"])
    mentioned |= {o["nodeId"] for e in t["sequence"] for o in e["enclosedBy"]}
    mentioned |= {r["source"]["fetchNodeId"] for r in all_reads(t) if r["source"]}
    assert mentioned == set(labels[name])


@pytest.mark.parametrize("name", NAMES)
def test_label_format_matches_backend_spelling(name, labels):
    for node_id, lab in labels[name].items():
        assert re.fullmatch(r"R\d{2}", lab["ruleId"]), node_id
        assert lab["basis"] in ("rule", "abstained"), node_id
        assert (lab["basis"] == "abstained") == (lab["concern"] == "undecided"), node_id


def test_legacy_fixture_still_loads():
    assert load_legacy_ast(MOCKS / "sample_ast.json")["nodes"][0]["id"] == "n1"
    assert load_input(MOCKS / "sample_ast.json")[0] == "legacy_ast"


def test_schema_0_1_timeline_is_rejected(tmp_path):
    old = {"entrypoint": "x.php", "schemaVersion": "0.1.0", "sequence": [], "loops": {}, "guards": {}, "queries": {}}
    (tmp_path / "old.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="schema 0.1"):
        load_timeline(tmp_path / "old.json")


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r.update(sourceKind="db"), "sourceKind"),
    (lambda r: r["source"].update(table=None), "resolved db reads"),
    (lambda r: r.update(var=None, sourceKind="literal", source=None), "null var and path"),
])
def test_validator_rejects_malformed_reads(tmp_path, mutate, message):
    doc = json.loads((MOCKS / "timeline_list_while.json").read_text(encoding="utf-8"))
    mutate(next(r for e in doc["sequence"] for r in e.get("reads", [])))
    (tmp_path / "bad.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=message):
        load_timeline(tmp_path / "bad.json")


# ---------------------------------------------------------------- scenarios


@pytest.mark.parametrize("name, kind", [("list_while", "Stmt_While"), ("list_foreach", "Stmt_Foreach")])
def test_list_page_repeats_markup_inside_one_loop(name, kind, timelines):
    loops = [o["nodeId"] for e in timelines[name]["sequence"] for o in e["enclosedBy"] if o["kind"] == kind]
    assert len(set(loops)) == 1 and len(loops) > 1


def test_admin_page_has_role_guards_and_session_reads(timelines, labels):
    t, lab = timelines["admin"], labels["admin"]
    guards = {o["nodeId"] for e in t["sequence"] for o in e["enclosedBy"] if o["role"] == "branch"}
    # Component 1's rule: query in body -> authorization; output-only body -> display conditional.
    assert {(lab[g]["concern"], lab[g]["reason"]) for g in guards} == {
        ("business_logic", "gates_data_access"), ("presentation", "display_conditional")}
    assert not any(v["concern"] == "undecided" for v in lab.values())
    assert any(r["sourceKind"] == "session" for r in all_reads(t))


def test_edge_cases_have_one_undecided_role_guard_around_output(timelines, labels):
    t, lab = timelines["edge_cases"], labels["edge_cases"]
    undecided = [k for k, v in lab.items() if v["concern"] == "undecided"]
    assert len(undecided) == 1
    assert lab[undecided[0]] | {"ruleId": None} == {
        "concern": "undecided", "basis": "abstained", "ruleId": None, "reason": "auth_or_display"}
    enclosed = [e for e in t["sequence"] if any(o["nodeId"] == undecided[0] for o in e["enclosedBy"])]
    assert any(e["kind"] != "Stmt_InlineHTML" for e in enclosed), "the guard must wrap output"


def test_detail_page_has_static_and_nested_output(timelines):
    seq = timelines["detail"]["sequence"]
    assert any(not e["enclosedBy"] for e in seq)
    assert any({o["role"] for o in e["enclosedBy"]} == {"iteration", "branch"} for e in seq)
    assert any(o.get("branch") == "else" for e in seq for o in e["enclosedBy"])


def test_edge_cases_are_present(timelines):
    t = timelines["edge_cases"]
    ambiguous = [r for r in all_reads(t) if r["confidence"] == "ambiguous" and r["sourceKind"] == "db_row_field"]
    assert ambiguous and all(r["source"]["table"] is None and r["source"]["column"] is None for r in ambiguous)
    assert all("select *" in t["queries"][r["source"]["queryNodeId"]]["sql"] for r in ambiguous)
    assert any(r["sourceKind"] == "unresolved" and r["confidence"] == "unresolved" for r in all_reads(t))
    assert any(r["sourceKind"] == "computed" and r["derivedFrom"] for r in all_reads(t))
    roles = [[o["role"] for o in e["enclosedBy"]] for e in t["sequence"]]
    kinds = [[o["kind"] for o in e["enclosedBy"]] for e in t["sequence"]]
    assert ["branch", "iteration"] in roles and ["Stmt_If", "Stmt_Foreach"] in kinds


def test_mocks_cover_every_output_kind_and_concern(timelines, labels):
    assert {e["kind"] for t in timelines.values() for e in t["sequence"]} == {
        "Stmt_InlineHTML", "Stmt_Echo", "Expr_Print"}
    assert {v["concern"] for lab in labels.values() for v in lab.values()} == {
        "presentation", "business_logic", "data_access", "mixed", "undecided"}


# ---------------------------------------------------------------- while == foreach


def canonical(timeline, labels):
    """Timeline and labels with node ids renumbered by first use and the loop keyword erased."""
    ids = {}

    def c(node_id):
        return ids.setdefault(node_id, f"#{len(ids)}")

    def enclosure(o):
        if o["role"] == "iteration":
            return {**o, "nodeId": c(o["nodeId"]), "kind": "<loop>", "iterExpr": "<loop header>"}
        return {**o, "nodeId": c(o["nodeId"]), "condNodeId": c(o["condNodeId"])}

    def read(r):
        r = copy.deepcopy(r)
        if r["source"]:
            r["source"] |= {"fetchNodeId": c(r["source"]["fetchNodeId"]), "queryNodeId": c(r["source"]["queryNodeId"])}
        if "derivedFrom" in r:
            r["derivedFrom"] = [read(x) for x in r["derivedFrom"]]
        return r

    seq = []
    for e in timeline["sequence"]:
        entry = {**e, "id": c(e["id"]), "enclosedBy": [enclosure(o) for o in e["enclosedBy"]]}
        if "reads" in e:
            entry["reads"] = [read(r) for r in e["reads"]]
        seq.append(entry)
    queries = {c(k): v for k, v in timeline["queries"].items()}
    return seq, queries, {c(k): v for k, v in labels.items()}


def test_while_and_foreach_give_the_same_component_tree(timelines, labels):
    assert canonical(timelines["list_while"], labels["list_while"]) == canonical(
        timelines["list_foreach"], labels["list_foreach"])


# ---------------------------------------------------------------- contract


def test_contract_is_valid_openapi_3_1_and_unextended(contract):
    validate(contract)
    assert contract["openapi"].startswith("3.1")
    assert "/api/products/{id}" not in contract["paths"]
    assert all("sku" not in s.get("properties", {}) for s in contract["components"]["schemas"].values())


@pytest.mark.parametrize("name", ENDPOINT_SCHEMA)
def test_reconciliation_flags_exactly_the_deliberate_gap(name, timelines, contract):
    provides = set(contract["components"]["schemas"][ENDPOINT_SCHEMA[name]]["properties"])
    assert needs(timelines[name]) - provides == EXPECTED_MISSING[name]


@pytest.mark.parametrize("name", ["list_while", "list_foreach"])
def test_contact_gap_is_unambiguous(name, timelines):
    t = timelines[name]
    contact = [r for r in all_reads(t) if r["path"] == ["contact"]]
    assert contact and all(r["confidence"] == "resolved" and r["source"]["column"] == "contact" for r in contact)
    assert "contact" in t["queries"][contact[0]["source"]["queryNodeId"]]["columns"]


def test_session_reads_are_excluded_not_flagged(timelines, contract):
    t = timelines["admin"]
    session = {(r["path"] or [r["var"].lstrip("$")])[-1] for r in all_reads(t) if r["sourceKind"] == "session"}
    assert session == {"username", "role"}
    provides = set(contract["components"]["schemas"]["AppointmentStats"]["properties"])
    assert not session & provides
    # Without the exclusion, reconciliation would wrongly flag both.
    assert (needs(t) | session) - provides == session


# ---------------------------------------------------------------- byte-exact against source


@needs_hms
@pytest.mark.parametrize("name", HMS_SOURCES)
def test_raw_is_byte_exact_against_hms_source(name, timelines):
    t = timelines[name]
    lo, hi = t["_provenance"]["linesCovered"]
    assert found_in_order(raws(t), gen.upstream_blob(HMS_SOURCES[name]), lo, hi)
    assert found_in_order(raws(t), (HMS / HMS_SOURCES[name]).read_bytes(), lo, hi)


def test_foreach_raws_are_byte_identical_to_while(timelines):
    assert raws(timelines["list_foreach"]) == raws(timelines["list_while"])


@pytest.mark.parametrize("name", HAND_WRITTEN)
def test_hand_written_raws_match_embedded_source(name, timelines):
    t = timelines[name]
    assert found_in_order(raws(t), t["_provenance"]["handWrittenSource"].encode("utf-8"))


def test_whitespace_and_entities_are_not_normalised(timelines):
    detail = b"".join(raws(timelines["detail"]))
    assert b"\r\n" in detail and b"\t" in detail
    assert any("&#8377;" in e.get("raw", "") for e in timelines["admin"]["sequence"])


@pytest.mark.parametrize("name", NAMES)
def test_raw_sql_is_present_in_source(name, timelines):
    t = timelines[name]
    if name in HAND_WRITTEN:
        src = t["_provenance"]["handWrittenSource"].encode("utf-8")
    elif (HMS / ".git").exists():
        src = gen.upstream_blob(HMS_SOURCES.get(name, "admin-panel1.php"))
    else:
        pytest.skip("HMS clone not at legacy-apps/hms")
    for query in t["queries"].values():
        assert query["sql"].encode("utf-8") in src


# ---------------------------------------------------------------- regeneration


@pytest.fixture(scope="module")
def generated():
    return gen.generate_all()


@needs_hms
@needs_php
def test_generator_reproduces_the_committed_mocks(generated):
    for filename, doc in generated.items():
        committed = json.loads((MOCKS / filename).read_text(encoding="utf-8"))
        if filename.startswith("timeline_"):
            # The lexer note records whichever PHP ran; T_INLINE_HTML is the same across versions.
            committed["_provenance"].pop("lexer")
            doc = {**doc, "_provenance": {k: v for k, v in doc["_provenance"].items() if k != "lexer"}}
        assert json.loads(json.dumps(doc)) == committed, filename


@needs_hms
@needs_php
def test_undecided_guard_condition_is_a_session_check(generated, labels):
    undecided = [k for k, v in labels["edge_cases"].items() if v["concern"] == "undecided"]
    assert undecided and all("$_SESSION" in gen.CONDITIONS[k] for k in undecided)


@needs_hms
@needs_php
def test_admin_guards_are_session_role_checks(generated, timelines):
    guards = {o["nodeId"] for e in timelines["admin"]["sequence"] for o in e["enclosedBy"] if o["role"] == "branch"}
    assert guards and all("$_SESSION['role']" in gen.CONDITIONS[g] for g in guards)
