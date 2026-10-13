"""Schema 1.0 guarantees that C2 and the rest of C1 rely on."""

from __future__ import annotations

import base64
import json
import re

from conftest import load_ast, requires_php

ID_RE = re.compile(r"^f\d{3,}#\d{5}$")


@requires_php
def test_ids_are_well_formed_unique_and_in_preorder(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    doc = load_ast(out, "list_page.php")
    ids = [n["id"] for n in doc["nodes"]]
    assert all(ID_RE.match(i) for i in ids)
    assert len(set(ids)) == len(ids)
    assert doc["rootId"] == f"{doc['fileId']}#00000"
    # ordinal == position in the list, so ids ascend with pre-order
    assert [int(i.split("#")[1]) for i in ids] == list(range(len(ids)))


@requires_php
def test_parent_and_children_are_consistent(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "list_page.php")
    by_id = {n["id"]: n for n in doc["nodes"]}
    assert by_id[doc["rootId"]]["parent"] is None
    for n in doc["nodes"]:
        for c in n["children"]:
            assert by_id[c]["parent"] == n["id"]
        if n["parent"] is not None:
            assert n["id"] in by_id[n["parent"]]["children"]
        # every id mentioned in `fields` is also in `children`
        flat = []
        for v in n["fields"].values():
            flat.extend(v if isinstance(v, list) else [v])
        assert {x for x in flat if isinstance(x, str)} == set(n["children"])


@requires_php
def test_children_ids_ascend_so_preorder_holds(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "guard_page.php")
    for n in doc["nodes"]:
        ords = [int(c.split("#")[1]) for c in n["children"]]
        assert ords == sorted(ords)
        if ords:
            assert min(ords) > int(n["id"].split("#")[1])


@requires_php
def test_loc_slices_back_to_the_source(fixture_tree, run_extract):
    """loc.startByte/endByte must slice the exact node text out of the file."""
    out = run_extract(fixture_tree)
    doc = load_ast(out, "guard_page.php")
    src = (fixture_tree / "guard_page.php").read_bytes()
    lines = src.split(b"\n")
    for n in doc["nodes"]:
        loc = n["loc"]
        assert loc is not None, n["kind"]
        assert 1 <= loc["startLine"] <= loc["endLine"]
        assert 0 <= loc["startByte"] <= loc["endByte"] <= len(src)
        # columns agree with bytes: col = byte offset from the start of that line
        line_start = sum(len(x) + 1 for x in lines[: loc["startLine"] - 1])
        assert loc["startByte"] - line_start == loc["startCol"]
    ifs = [n for n in doc["nodes"] if n["kind"] == "Stmt_If"]
    assert len(ifs) == 1
    text = src[ifs[0]["loc"]["startByte"] : ifs[0]["loc"]["endByte"]].decode()
    assert text.startswith("if (!isset($_SESSION['user']))")
    assert text.rstrip().endswith("}")


@requires_php
def test_if_exposes_cond_then_else_as_named_fields(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "guard_page.php")
    by_id = {n["id"]: n for n in doc["nodes"]}
    if_node = next(n for n in doc["nodes"] if n["kind"] == "Stmt_If")
    assert by_id[if_node["fields"]["cond"]]["kind"] == "Expr_BooleanNot"
    kinds = [by_id[i]["kind"] for i in if_node["fields"]["stmts"]]
    assert kinds == ["Stmt_Expression", "Stmt_Expression"] or "Stmt_Expression" in kinds
    # the terminator is visible to later stages: `exit;` inside the guarded branch
    assert any(n["kind"] == "Expr_Exit" for n in doc["nodes"])


@requires_php
def test_list_page_has_inline_html_around_the_loop(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "list_page.php")
    kinds = [n["kind"] for n in doc["nodes"]]
    assert "Stmt_Foreach" in kinds
    assert kinds.count("Stmt_InlineHTML") >= 3
    assert "Stmt_Echo" in kinds


@requires_php
def test_inline_html_is_byte_exact_with_crlf(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "crlf_page.php")
    html = [n for n in doc["nodes"] if n["kind"] == "Stmt_InlineHTML"]
    assert len(html) == 1
    value = html[0]["attrs"]["value"]
    # PHP swallows ONE newline directly after "?>", so the "\r\n" after it is not output.
    # What remains must keep its own CRLF untouched.
    assert value == "<p>line one\r\nline two</p>\r\n"


@requires_php
def test_non_utf8_bytes_are_preserved_not_lost(fixture_tree, run_extract):
    doc = load_ast(run_extract(fixture_tree), "latin1_page.php")
    html = [n for n in doc["nodes"] if n["kind"] == "Stmt_InlineHTML"]
    assert len(html) == 1
    value = html[0]["attrs"]["value"]
    assert isinstance(value, dict) and "__b64" in value
    assert base64.b64decode(value["__b64"]) == b"caf\xe9 <b>latin1</b>\n"


@requires_php
def test_every_value_in_the_ast_files_is_valid_json_with_sorted_keys(fixture_tree, run_extract):
    out = run_extract(fixture_tree)
    for f in (out / "ast").glob("*.json"):
        text = f.read_text(encoding="utf-8")
        obj = json.loads(text)
        assert text == json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
