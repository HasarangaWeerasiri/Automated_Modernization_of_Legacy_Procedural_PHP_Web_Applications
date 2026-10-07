"""The WackoPicko mocks: valid schema 1.0, fully labelled, and byte-exact against the pinned source."""

import sys
from pathlib import Path

import pytest

from src.mapping.loader import load_labels, load_labels_json, load_timeline, load_timeline_json
from src.mapping.stage1_isolation import isolate_presentation

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
sys.path.insert(0, str(ROOT / "tools"))
import gen_timelines as gen  # noqa: E402

NAMES = ("wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
PREFIX = "legacy-apps/wackopicko/"
needs_clone = pytest.mark.skipif(not (gen.WACKOPICKO / ".git").exists(), reason="WackoPicko clone not present")


def flat_reads(reads):
    for read in reads:
        yield read
        yield from flat_reads(read.get("derivedFrom", []))


@pytest.mark.parametrize("name", NAMES)
def test_mock_loads_and_every_mentioned_node_is_labelled(name):
    timeline = load_timeline_json(MOCKS / f"timeline_{name}.json")
    labels = load_labels_json(MOCKS / f"labels_{name}.json")["labels"]
    mentioned = {e["id"] for e in timeline["sequence"]}
    mentioned |= {o["nodeId"] for e in timeline["sequence"] for o in e["enclosedBy"]}
    assert mentioned == set(labels)
    assert timeline["entrypoint"].startswith(PREFIX) and timeline["queries"] == {}


@pytest.mark.parametrize("name", NAMES)
def test_reads_claim_no_database_source(name):
    """Data comes through include/*.php class methods, so nothing here is resolved to a table or column."""
    timeline = load_timeline_json(MOCKS / f"timeline_{name}.json")
    kinds = {r["sourceKind"] for e in timeline["sequence"] for r in flat_reads(e.get("reads", []))}
    assert kinds <= {"unresolved", "computed", "literal", "server"}


@pytest.mark.parametrize("name", NAMES)
def test_stage1_runs(name):
    result = isolate_presentation(load_timeline(MOCKS / f"timeline_{name}.json"),
                                  load_labels(MOCKS / f"labels_{name}.json"))
    assert result.counts.total == result.counts.kept + result.counts.review + result.counts.excluded
    assert result.counts.excluded == 0, "no query, fetch or logic guard in these pages' own code"


@needs_clone
@pytest.mark.parametrize("name", NAMES)
def test_raw_is_byte_exact_against_the_pinned_source(name):
    timeline = load_timeline_json(MOCKS / f"timeline_{name}.json")
    source = gen.upstream_blob(timeline["entrypoint"].removeprefix(PREFIX), gen.WACKOPICKO, gen.WP_COMMIT)
    first, last = timeline["_provenance"]["linesCovered"]
    position = sum(len(line) for line in source.splitlines(keepends=True)[:first - 1])  # start of line `first`
    for entry in timeline["sequence"]:
        if entry["kind"] != "Stmt_InlineHTML":
            continue
        raw = entry["raw"].encode("utf-8")
        at = source.find(raw, position)
        assert at >= 0, f"{entry['id']} raw not found in order"
        assert first <= source.count(b"\n", 0, at) + 1 <= last
        position = at + len(raw)


@needs_clone
def test_working_copy_matches_the_pinned_commit():
    for name in NAMES:
        path = load_timeline_json(MOCKS / f"timeline_{name}.json")["entrypoint"].removeprefix(PREFIX)
        assert (gen.WACKOPICKO / path).read_bytes() == gen.upstream_blob(path, gen.WACKOPICKO, gen.WP_COMMIT)
