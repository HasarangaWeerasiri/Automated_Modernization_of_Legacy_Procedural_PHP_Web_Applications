"""Stage 3 (data requirement recovery), against docs/reconciliation-spec.md section 1."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.mapping.boundary_config import load_boundary_config
from src.mapping.loader import load_labels, load_timeline
from src.mapping.stage1_isolation import isolate_presentation
from src.mapping.stage2_boundaries import infer_boundaries
from src.mapping.stage3_requirements import dumps, recover_requirements, requirements_to_dict, summary
from src.model import Concern, Enclosure, Label, OutputNode, Read, ReadSource, Timeline

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
ALL_MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases",
             "wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
CONFIG = load_boundary_config()
LIST_COLUMNS = ["ID", "pid", "fname", "lname", "gender", "email", "contact", "doctor", "docFees", "appdate",
                "apptime"]


def stages(timeline, labels):
    tree = infer_boundaries(isolate_presentation(timeline, labels), timeline, CONFIG)
    return tree, recover_requirements(tree, timeline)


def run(name):
    return stages(load_timeline(MOCKS / f"timeline_{name}.json"), load_labels(MOCKS / f"labels_{name}.json"))[1]


@pytest.fixture(scope="module")
def results():
    return {name: run(name) for name in ALL_MOCKS}


def needs(result, kind=None):
    return [(c, n) for c in result.components for n in c.needs if kind is None or n.kind == kind]


def component(result, type_):
    (found,) = [c for c in result.components if c.type == type_]
    return found


# ---------------------------------------------------------------- classification (section 1.1)


@pytest.mark.parametrize("name", ["list_while", "list_foreach"])
def test_item_needs_one_field_per_echoed_column(name, results):
    item = component(results[name], "Item")
    fields = [n for n in item.needs if n.kind == "field"]
    assert [n.column for n in fields] == LIST_COLUMNS
    assert {(n.table, n.query_node_id) for n in fields} == {("appointmenttb", fields[0].query_node_id)}
    assert [n.kind for n in item.needs] == ["field"] * len(LIST_COLUMNS), "status-cell literals need nothing"


def test_session_reads_are_context_needs(results):
    page = component(results["admin"], "Page")
    assert [(n.kind, n.name) for n in page.needs] == [
        ("context", "$username"), ("field", "total"), ("field", "revenue"), ("context", "$_SESSION['role']")]
    assert {n.source_kind for n in page.needs if n.kind == "context"} == {"session"}


def test_request_and_server_reads_are_context_needs(results):
    kinds = {n.source_kind for _, n in needs(results["edge_cases"], "context")}
    assert kinds == {"session", "request"}
    assert [n.source_kind for _, n in needs(results["wp_login"], "context")] == ["server"]


def test_ambiguous_and_unresolved_reads_are_flagged(results):
    result = results["edge_cases"]
    assert [(n.name, n.flag) for _, n in needs(result, "ambiguous")] == [
        ("doctor", "ambiguous_needs_schema"), ("prescription", "ambiguous_needs_schema"),
        ("docFees", "ambiguous_needs_schema")]
    assert [(n.name, n.flag) for _, n in needs(result, "unresolved")] == [
        ("$settings['hospital_name']", "unresolved_read")]
    assert all(n.table is None and n.column is None for _, n in needs(result, "ambiguous"))


def test_computed_reads_are_followed_to_their_base_read_and_keep_the_wrapper(results):
    (fees,) = [n for _, n in needs(results["edge_cases"], "ambiguous") if n.name == "docFees"]
    assert [(r.expr, r.wrappers) for r in fees.references] == [("$row['docFees']", ("number_format",))]
    (name,) = [n for _, n in needs(results["wp_guestbook"]) if n.name == '$guest["name"]']
    assert name.references[0].wrappers == ("h",)


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_no_literal_and_no_computed_read_becomes_a_need(name, results):
    assert {n.kind for _, n in needs(results[name])} <= {"field", "ambiguous", "unresolved", "context", "collection"}


@pytest.mark.parametrize("name", ["wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails"])
def test_wackopicko_has_no_field_need(name, results):
    assert not needs(results[name], "field") and not needs(results[name], "ambiguous")


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_sku_is_never_a_need(name, results):
    assert "sku" not in json.dumps(requirements_to_dict(results[name]))


# ---------------------------------------------------------------- where needs live (section 1.2)


@pytest.mark.parametrize("name", ["list_while", "detail", "edge_cases"])
def test_list_has_a_collection_need_for_its_loop(name, results):
    result = results[name]
    lst, item = component(result, "List"), component(result, "Item")
    (collection,) = [n for n in lst.needs if n.kind == "collection"]
    assert collection.item_component == item.component_id and collection.name == "$row"
    row_queries = {n.query_node_id for n in item.needs if n.query_node_id}
    assert row_queries == {collection.query_node_id} and collection.flag is None
    assert all(n.row_of == lst.component_id for n in item.needs)


def test_collection_without_a_resolved_query_is_flagged(results):
    lists = [c for c in results["wp_guestbook"].components if c.type == "List"]
    assert [(n.query_node_id, n.flag) for c in lists for n in c.needs if n.kind == "collection"] == [
        (None, "unresolved_read")]


def test_needs_outside_any_list_have_no_row(results):
    assert all(n.row_of is None for n in component(results["admin"], "Page").needs)


# ---------------------------------------------------------------- de-duplication and references


def test_needs_are_deduplicated_and_list_every_reference():
    """Two echoes of the same column in one Item: one need, both references."""
    loop = Enclosure(node_id="s#00003", kind="Stmt_While", role="iteration", iter_expr="$row = f($r)",
                     iter_source_kind="db_row_field", value_var="$row", key_var=None)
    source = ReadSource(fetch_node_id="s#00002", query_node_id="s#00001", table="t", column="c")
    read = Read(expr="$row['c']", var="$row", path=("c",), source_kind="db_row_field", confidence="resolved",
                source=source)
    nodes = (OutputNode("s#00010", "Stmt_InlineHTML", "<ul>", (), ()),
             OutputNode("s#00011", "Stmt_InlineHTML", "<li><b>", (), (loop,)),
             OutputNode("s#00012", "Stmt_Echo", None, (read,), (loop,)),
             OutputNode("s#00013", "Stmt_Echo", None, (read,), (loop,)),
             OutputNode("s#00014", "Stmt_InlineHTML", "</b></li>", (), (loop,)),
             OutputNode("s#00015", "Stmt_InlineHTML", "</ul>", (), ()))
    labels = {i: Label(Concern.PRESENTATION, "rule", "R00", "test") for i in {n.id for n in nodes} | {"s#00003"}}
    _, result = stages(Timeline("synthetic", "1.0", nodes, {}, {}), labels)
    (field,) = [n for _, n in needs(result, "field")]
    assert [r.node_id for r in field.references] == ["s#00012", "s#00013"]
    assert all(r.line is None for r in field.references), "schema 1.0 carries no line per output node"


# ---------------------------------------------------------------- determinism and equivalence


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_two_runs_give_byte_identical_json(name, results):
    assert dumps(run(name)) == dumps(results[name])


def canonical(result):
    ids = {}

    def walk(value, key=None):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items() if k != "entrypoint"}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        if key in ("sourceIds", "nodeId", "queryNodeId") and value:
            return ids.setdefault(value, f"#{len(ids)}")
        if key == "expr" and isinstance(value, str) and ("while" in value or "fetch" in value):
            return "<loop header>"
        return value

    return walk(requirements_to_dict(result))


def test_while_and_foreach_give_identical_requirements(results):
    assert canonical(results["list_while"]) == canonical(results["list_foreach"])


def test_summary_line(results):
    assert summary(results["list_while"]) == (
        "[Stage 3] 3 components, 11 needs: 11 field, 0 ambiguous, 0 unresolved, 0 context · 1 collections")


def test_cli_writes_stage3_json(results):
    subprocess.run([sys.executable, "src/main.py", "--timeline", "mocks/timeline_admin.json",
                    "--labels", "mocks/labels_admin.json", "--stage", "3"],
                   cwd=ROOT, capture_output=True, encoding="utf-8", check=True, env={**os.environ, "PYTHONUTF8": "1"})
    written = json.loads((ROOT / "output" / "stage3_admin.json").read_text(encoding="utf-8"))
    assert written == requirements_to_dict(results["admin"])
