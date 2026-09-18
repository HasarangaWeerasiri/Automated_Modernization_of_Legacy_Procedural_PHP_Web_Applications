"""Stage 1 (presentation isolation) against the five schema-1.0 mocks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.mapping.loader import load_labels, load_timeline
from src.mapping.stage1_isolation import dumps, isolate_presentation, result_to_dict, summary, write_result
from src.model import Concern, Status

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
FIXTURES = Path(__file__).parent / "fixtures"
NAMES = ("list_while", "list_foreach", "admin", "detail", "edge_cases")


def run(name, folder=MOCKS):
    return isolate_presentation(load_timeline(folder / f"timeline_{name}.json"),
                                load_labels(folder / f"labels_{name}.json"))


@pytest.fixture(scope="module")
def results():
    return {n: run(n) for n in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_runs_on_every_mock_and_accounts_for_every_node(name, results):
    r = results[name]
    c = r.counts
    assert c.total == c.kept + c.review + c.excluded == len(r.kept) + len(r.excluded)
    assert c.review == sum(k.status is Status.REVIEW for k in r.kept)
    ids = [k.node_id for k in r.kept] + [x.node_id for x in r.excluded]
    assert len(ids) == len(set(ids)), "each node decided exactly once"
    sequence = [n.id for n in load_timeline(MOCKS / f"timeline_{name}.json").sequence]
    assert set(sequence) <= set(ids), "every output node is kept or excluded"


@pytest.mark.parametrize("name", NAMES)
def test_kept_output_nodes_keep_sequence_order(name, results):
    sequence = [n.id for n in load_timeline(MOCKS / f"timeline_{name}.json").sequence]
    kept_outputs = [k.node_id for k in results[name].kept if k.node_type == "output"]
    assert kept_outputs == [i for i in sequence if i in set(kept_outputs)]


def test_edge_cases_have_exactly_one_auth_or_display_review(results):
    review = [k for k in results["edge_cases"].kept if k.status is Status.REVIEW]
    assert [(k.kind, k.reason) for k in review] == [("Stmt_If", "auth_or_display")]


def test_admin_has_no_review_and_keeps_the_authorization_guard_in_chains(results):
    r = results["admin"]
    assert r.counts.review == 0
    auth_guards = {x.node_id for x in r.excluded if x.kind == "Stmt_If" and x.label.concern is Concern.BUSINESS_LOGIC}
    assert len(auth_guards) == 1
    guard = auth_guards.pop()
    chains = [e for k in r.kept if k.node_type == "output" for e in k.enclosed_by if e.node_id == guard]
    assert chains, "outputs inside the authorization guard are kept, with the guard in their chain"
    assert all(e.label.concern is Concern.BUSINESS_LOGIC and e.label.reason == "gates_data_access" for e in chains)
    assert {e.branch for e in chains} == {"then", "else"}


@pytest.mark.parametrize("name", NAMES)
def test_every_enclosure_in_a_chain_carries_its_own_label(name, results):
    labels = load_labels(MOCKS / f"labels_{name}.json")
    for k in results[name].kept:
        assert all(e.label == labels[e.node_id] for e in k.enclosed_by)
        if k.output:
            assert k.output.enclosed_by == k.enclosed_by


@pytest.mark.parametrize("name", NAMES)
def test_no_business_logic_or_data_access_node_is_kept(name, results):
    blocked = {Concern.BUSINESS_LOGIC, Concern.DATA_ACCESS}
    assert not [k.node_id for k in results[name].kept if k.label and k.label.concern in blocked]
    assert all(x.label.concern in blocked for x in results[name].excluded)


def test_mixed_nodes_are_kept_for_review(results):
    mixed = [k for k in results["list_while"].kept if k.label and k.label.concern is Concern.MIXED]
    assert mixed and all(k.status is Status.REVIEW and k.reason == "mixed_concern" for k in mixed)


def canonical(result):
    """Stage 1 JSON with node ids renumbered by first use and the loop keyword/header erased."""
    ids = {}
    id_keys = {"nodeId", "condNodeId", "fetchNodeId", "queryNodeId"}

    def walk(value, key=None):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items() if k != "entrypoint"}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if key in id_keys and value is not None:
            return ids.setdefault(value, f"#{len(ids)}")
        if key == "kind" and value in ("Stmt_While", "Stmt_Foreach"):
            return "<loop>"
        if key == "iterExpr":
            return "<loop header>"
        return value

    return walk(result_to_dict(result))


def test_while_and_foreach_give_identical_stage1_results(results):
    assert canonical(results["list_while"]) == canonical(results["list_foreach"])


@pytest.mark.parametrize("name", NAMES)
def test_running_twice_gives_byte_identical_json(name, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_result(run(name), first)
    write_result(run(name), second)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().decode("utf-8") == dumps(run(name))


def test_unlabelled_node_is_kept_for_review_without_guessing():
    r = run("unlabelled", FIXTURES)
    node = next(k for k in r.kept if k.node_id == "t001#00005")
    assert (node.status, node.reason, node.label) == (Status.REVIEW, "unlabelled", None)
    assert node.enclosed_by[0].label.concern is Concern.PRESENTATION, "its labelled enclosure keeps its label"
    assert r.counts.review == 1 and r.counts.excluded == 0


def test_decisions_ignore_rule_id_and_basis():
    timeline = load_timeline(MOCKS / "timeline_edge_cases.json")
    labels = load_labels(MOCKS / "labels_edge_cases.json")
    scrambled = {k: type(v)(v.concern, "other-basis", "R99", v.reason) for k, v in labels.items()}
    decide = [(k.node_id, k.status, k.reason) for k in isolate_presentation(timeline, labels).kept]
    assert decide == [(k.node_id, k.status, k.reason) for k in isolate_presentation(timeline, scrambled).kept]


def test_cli_prints_summary_and_writes_json():
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--timeline", "mocks/timeline_admin.json",
         "--labels", "mocks/labels_admin.json", "--stage", "1"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert summary(run("admin")) in proc.stdout
    written = json.loads((ROOT / "output" / "stage1_admin.json").read_text(encoding="utf-8"))
    assert written == result_to_dict(run("admin"))
