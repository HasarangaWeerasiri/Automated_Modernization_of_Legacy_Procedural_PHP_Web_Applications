"""loc and node ids in the mock timelines: Member 01's conventions (CONTEXT.md section 3, 12 Oct 2026).

loc is {startLine, endLine, startCol, endCol, startByte, endByte}: lines 1-based, columns and bytes
0-based, every end exclusive, so source[startByte:endByte] is the node's exact text. It is on every
sequence entry and on loop and branch enclosures. Node ids are the pre-order ids of the file's AST.
These tests check every loc against the real source bytes and every id against the AST, and that
the loader treats loc as optional.
"""

import json
import sys
from pathlib import Path

import pytest

from src.mapping.loader import load_labels_json, load_timeline, load_timeline_json

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))
import gen_timelines as gen  # noqa: E402
from ast_producer import unavailable as harness_unavailable  # noqa: E402  (tools/harness, on the path via gen)

ALL_MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases",
             "wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
HAND_WRITTEN = ("admin", "edge_cases")
LOC_KEYS = {"startLine", "endLine", "startCol", "endCol", "startByte", "endByte"}
needs_clones = pytest.mark.skipif(not ((gen.HMS / ".git").exists() and (gen.WACKOPICKO / ".git").exists()),
                                  reason="HMS or WackoPicko clone not present")
needs_harness = pytest.mark.skipif(harness_unavailable() is not None,
                                   reason=f"AST producer skipped: {harness_unavailable()}")


def mock(name):
    return load_timeline_json(MOCKS / f"timeline_{name}.json")


def source_of(name, doc):
    """The exact bytes the mock's loc values refer to."""
    if name in HAND_WRITTEN:
        return doc["_provenance"]["handWrittenSource"].encode("utf-8")
    if name == "list_foreach":  # admin-panel1.php with the documented same-line loop rewrite
        return gen.upstream_blob("admin-panel1.php").replace(gen.WHILE_HDR, gen.FOREACH_HDR)
    entrypoint = doc["entrypoint"]
    if entrypoint.startswith("legacy-apps/wackopicko/"):
        return gen.upstream_blob(entrypoint.removeprefix("legacy-apps/wackopicko/"), gen.WACKOPICKO, gen.WP_COMMIT)
    return gen.upstream_blob(entrypoint.removeprefix("legacy-apps/hms/"))


def position(source: bytes, offset: int) -> tuple[int, int]:
    """(1-based line, 0-based byte column) of a byte offset."""
    return source.count(b"\n", 0, offset) + 1, offset - (source.rfind(b"\n", 0, offset) + 1)


def enclosures(doc):
    return [enc for entry in doc["sequence"] for enc in entry["enclosedBy"]]


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_entry_loop_and_branch_has_loc_and_the_convention_is_recorded(name):
    doc = mock(name)
    assert all(set(entry["loc"]) == LOC_KEYS for entry in doc["sequence"])
    assert all(set(enc["loc"]) == LOC_KEYS for enc in enclosures(doc))  # Member 01 Q15
    assert "Member 01" in doc["_provenance"]["locConvention"]
    assert "pre-order" in doc["_provenance"]["idConvention"]


@needs_clones
@pytest.mark.parametrize("name", ALL_MOCKS)
def test_loc_slices_the_statement_out_of_the_source(name):
    doc = mock(name)
    source = source_of(name, doc)
    for entry in doc["sequence"]:
        loc = entry["loc"]
        text = source[loc["startByte"]:loc["endByte"]]  # the end is exclusive
        if entry["kind"] == "Stmt_InlineHTML":
            assert text == entry["raw"].encode("utf-8"), entry["id"]
        else:
            keyword = {"Stmt_Echo": (b"echo", b"<?="), "Expr_Print": (b"print",)}[entry["kind"]]
            assert text.startswith(keyword), (entry["id"], text[:20])
            first_read = entry["reads"][0]["expr"].encode("utf-8")
            assert first_read in text, (entry["id"], text, first_read)


@needs_clones
@pytest.mark.parametrize("name", ALL_MOCKS)
def test_lines_and_columns_are_computed_from_the_bytes(name):
    doc = mock(name)
    source = source_of(name, doc)
    for owner in doc["sequence"] + enclosures(doc):
        loc = owner["loc"]
        assert (loc["startLine"], loc["startCol"]) == position(source, loc["startByte"]), owner
        assert (loc["endLine"], loc["endCol"]) == position(source, loc["endByte"]), owner
    for enc in enclosures(doc):  # an enclosure's loc is its own node's: the loop, or the whole if statement
        keyword = {"Stmt_While": b"while", "Stmt_Foreach": b"foreach", "Stmt_For": b"for", "Stmt_If": b"if"}
        assert source[enc["loc"]["startByte"]:].startswith(keyword[enc["kind"]]), enc["nodeId"]


def test_contact_is_at_line_468_of_admin_panel1():
    (entry,) = [e for e in mock("list_while")["sequence"]
                if e["kind"] == "Stmt_Echo" and e["reads"][0]["expr"] == "$row['contact']"]
    # `echo $row['contact'];` starts at the 35th byte of line 468: column 34 counted from 0
    assert entry["loc"] == {"startLine": 468, "endLine": 468, "startCol": 34, "endCol": 55,
                            "startByte": 18631, "endByte": 18652}


def test_an_echo_closed_by_the_closing_tag_ends_after_it():
    """nikic's Stmt_Echo node includes the `?>` that ends the statement, so Member 01's loc does too."""
    doc = mock("admin")
    source = doc["_provenance"]["handWrittenSource"].encode("utf-8")
    (entry,) = [e for e in doc["sequence"] if e["kind"] == "Stmt_Echo" and e["reads"][0]["expr"] == "$username"]
    assert source[entry["loc"]["startByte"]:entry["loc"]["endByte"]] == b"echo $username ?>\n"
    assert (entry["loc"]["endLine"], entry["loc"]["endCol"]) == (entry["loc"]["startLine"] + 1, 0)


def test_loc_is_optional():
    timeline = load_timeline(FIXTURES / "timeline_unlabelled.json")
    assert all(node.loc is None for node in timeline.sequence)
    assert all(enc.loc is None for node in timeline.sequence for enc in node.enclosed_by)


def test_loc_is_loaded_into_the_model():
    timeline = load_timeline(MOCKS / "timeline_list_while.json")
    node = timeline.sequence[0]
    assert (node.loc.start_line, node.loc.start_col, node.loc.column) == (414, 0, 1)  # column: 1-based, for reports
    assert (node.loc.start_byte, node.loc.end_byte) == (16448, 17889)
    loop = next(enc for n in timeline.sequence for enc in n.enclosed_by if enc.role == "iteration")
    assert (loop.loc.start_line, loop.loc.end_line) == (459, 489)


@pytest.mark.parametrize("change, message", [
    ({"startByte": None}, "loc needs exactly"),
    ({"startLine": 0}, "lines are 1-based"),
    ({"startLine": "414"}, "must be integers"),
    ({"startCol": -1}, "0-based"),
    ({"startLine": 500}, "ends before it starts"),
    ({"endByte": 5}, "ends before it starts"),
])
def test_malformed_loc_is_rejected(tmp_path, change, message):
    doc = json.loads((MOCKS / "timeline_list_while.json").read_text(encoding="utf-8"))
    loc = doc["sequence"][0]["loc"] | change
    doc["sequence"][0]["loc"] = {k: v for k, v in loc.items() if v is not None}
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)


def test_malformed_enclosure_loc_is_rejected(tmp_path):
    doc = json.loads((MOCKS / "timeline_admin.json").read_text(encoding="utf-8"))
    enc = next(e for entry in doc["sequence"] for e in entry["enclosedBy"])
    enc["loc"] = {"startLine": 1, "endLine": 1, "startCol": 1, "endCol": 2}  # the old four-key shape
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="loc needs exactly"):
        load_timeline(path)


# ---------------------------------------------------------------- ids are the AST's pre-order ids


@pytest.fixture(scope="module")
def asts():
    specs = gen.specs() | gen.wackopicko_specs()
    found, _ = gen.spec_asts(specs)
    return {name: (found[spec["file_id"]], spec["src"]) for name, spec in specs.items()}


@needs_clones
@needs_harness
@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_id_is_the_ast_node_of_that_statement(name, asts):
    ast, source = asts[name]
    doc = mock(name)
    loc = gen.loc_dict
    for entry in doc["sequence"]:
        node = ast.nodes[entry["id"]]
        assert (node.kind, loc(node.loc)) == (entry["kind"], entry["loc"]), entry["id"]
        for enc in entry["enclosedBy"]:
            owner = ast.nodes[enc["nodeId"]]
            assert (owner.kind, loc(owner.loc)) == (enc["kind"], enc["loc"]), enc["nodeId"]
            if enc["role"] == "branch":
                cond = ast.nodes[enc["condNodeId"]]  # the if's `cond` node, or its elseif's
                arms = [owner] + [ast.nodes[e] for e in owner.fields["elseifs"]]
                assert enc["condNodeId"] in [arm.fields["cond"] for arm in arms]
                if "condExpr" in enc:  # Member 01 Q2: the exact source of the cond node
                    assert source[cond.loc.start_byte:cond.loc.end_byte].decode("utf-8") == enc["condExpr"]
        for read in entry.get("reads", []):
            if read["source"]:
                assert ast.nodes[read["source"]["fetchNodeId"]].kind == "Expr_FuncCall"
                assert ast.nodes[read["source"]["queryNodeId"]].kind == "Expr_FuncCall"
    assert set(doc["queries"]) <= set(ast.nodes)
    assert set(load_labels_json(MOCKS / f"labels_{name}.json")["labels"]) <= set(ast.nodes)


@needs_clones
@needs_harness
def test_ids_are_numbered_in_pre_order_so_source_order_is_id_order(asts):
    for name in ALL_MOCKS:
        ids = [entry["id"] for entry in mock(name)["sequence"]]
        assert ids == sorted(ids), name
