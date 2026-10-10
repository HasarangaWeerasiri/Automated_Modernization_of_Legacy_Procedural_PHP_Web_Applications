"""loc on timeline sequence entries (reconciliation-spec v0.2 section 1.2b; Member 01 Q13).

The generated mocks carry loc {startLine, endLine, startCol, endCol} as an agent extension:
1-based, columns count bytes, the end is the statement's last byte. These tests check every
loc against the real source bytes, and that the loader treats loc as optional.
"""

import json
import sys
from pathlib import Path

import pytest

from src.mapping.loader import load_timeline, load_timeline_json

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))
import gen_timelines as gen  # noqa: E402

ALL_MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases",
             "wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
HAND_WRITTEN = ("admin", "edge_cases")
needs_clones = pytest.mark.skipif(not ((gen.HMS / ".git").exists() and (gen.WACKOPICKO / ".git").exists()),
                                  reason="HMS or WackoPicko clone not present")


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


def offset(source: bytes, line: int, col: int) -> int:
    lines = source.split(b"\n")
    return sum(len(text) + 1 for text in lines[:line - 1]) + col - 1


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_generated_entry_has_loc_and_the_extension_is_marked(name):
    doc = mock(name)
    assert all("loc" in entry for entry in doc["sequence"])
    assert "Member 01 Q13" in doc["_provenance"]["locExtension"]


@needs_clones
@pytest.mark.parametrize("name", ALL_MOCKS)
def test_loc_points_at_the_statement_in_the_source(name):
    doc = mock(name)
    source = source_of(name, doc)
    for entry in doc["sequence"]:
        loc = entry["loc"]
        start = offset(source, loc["startLine"], loc["startCol"])
        end = offset(source, loc["endLine"], loc["endCol"]) + 1  # loc ends on the statement's last byte
        text = source[start:end]
        if entry["kind"] == "Stmt_InlineHTML":
            assert text == entry["raw"].encode("utf-8"), entry["id"]
        else:
            keyword = {"Stmt_Echo": (b"echo", b"<?="), "Expr_Print": (b"print",)}[entry["kind"]]
            assert text.startswith(keyword), (entry["id"], text[:20])
            first_read = entry["reads"][0]["expr"].encode("utf-8")
            assert first_read in text, (entry["id"], text, first_read)


def test_contact_is_at_line_468_of_admin_panel1():
    (entry,) = [e for e in mock("list_while")["sequence"]
                if e["kind"] == "Stmt_Echo" and e["reads"][0]["expr"] == "$row['contact']"]
    assert entry["loc"] == {"startLine": 468, "endLine": 468, "startCol": 35, "endCol": 55}


def test_loc_is_optional():
    timeline = load_timeline(FIXTURES / "timeline_unlabelled.json")
    assert all(node.loc is None for node in timeline.sequence)


def test_loc_is_loaded_into_the_model():
    node = load_timeline(MOCKS / "timeline_list_while.json").sequence[0]
    assert (node.loc.start_line, node.loc.start_col) == (414, 1)


@pytest.mark.parametrize("loc, message", [
    ({"startLine": 3, "endLine": 3, "startCol": 1}, "loc needs exactly"),
    ({"startLine": 0, "endLine": 3, "startCol": 1, "endCol": 2}, "integers >= 1"),
    ({"startLine": "3", "endLine": 3, "startCol": 1, "endCol": 2}, "integers >= 1"),
    ({"startLine": 5, "endLine": 3, "startCol": 1, "endCol": 2}, "ends before it starts"),
])
def test_malformed_loc_is_rejected(tmp_path, loc, message):
    doc = json.loads((MOCKS / "timeline_list_while.json").read_text(encoding="utf-8"))
    doc["sequence"][0]["loc"] = loc
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)
