"""FR8 consistency of the pipeline reports (S5).

Every query that is not converted appears exactly once in
unresolved_queries.json with file, line, reason and action; converted
queries never appear there; summary.json counts reconcile.
"""

import json
from pathlib import Path

import pytest

from c3.pipeline import build_summary, run_pipeline

ROOT = Path(__file__).resolve().parents[3]
APPS = ROOT / "benchmarks" / "apps"

BENCHMARKS = {
    "simple_crud": (APPS / "simple_crud" / "legacy", APPS / "simple_crud" / "schema.sql"),
    "mixed_crud": (APPS / "mixed_crud" / "legacy", APPS / "mixed_crud" / "schema.sql"),
    "hard_crud": (APPS / "hard_crud" / "legacy", APPS / "hard_crud" / "schema.sql"),
    "clinic": (
        APPS / "clinic_inventory_legacy_hard" / "legacy",
        APPS / "clinic_inventory_legacy_hard" / "database" / "schema.sql",
    ),
    "helpdesk": (
        APPS / "helpdesk_legacy_holdout" / "legacy",
        APPS / "helpdesk_legacy_holdout" / "database" / "schema.sql",
    ),
}


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    result = {}

    for name, (php, schema) in BENCHMARKS.items():
        out = tmp_path_factory.mktemp(name)
        run_pipeline(php, schema, out)
        result[name] = {
            file: json.loads((out / f"{file}.json").read_text(encoding="utf-8"))
            for file in ("queries", "converted_queries", "unresolved_queries", "summary")
        }

    return result


@pytest.mark.parametrize("name", sorted(BENCHMARKS))
def test_every_non_converted_query_reported_exactly_once(outputs, name):
    data = outputs[name]
    converted_ids = {
        q["query_id"] for q in data["converted_queries"] if q["status"] == "CONVERTED"
    }
    all_ids = [q["query_id"] for q in data["queries"]]
    reported = [item["query_id"] for item in data["unresolved_queries"]]

    assert sorted(reported) == sorted(set(all_ids) - converted_ids)
    assert len(reported) == len(set(reported))

    for item in data["unresolved_queries"]:
        assert item["source_file"] and isinstance(item["source_line"], int)
        assert item["reason"] and item["reason_code"]
        assert item["action"] == "manual_review_required"
        assert item["stage"] in ("recovery", "conversion")


@pytest.mark.parametrize("name", sorted(BENCHMARKS))
def test_summary_counts_reconcile(outputs, name):
    s = outputs[name]["summary"]

    assert s["total_queries"] == s["recovered_queries"] + s["recovery_unresolved"]
    assert s["total_queries"] == s["converted_queries"] + s["not_converted"]
    assert s["converted_queries"] == (
        s["converted_by_general_rules"] + s["converted_by_query_specific_template"]
    )
    assert s["not_converted"] == len(outputs[name]["unresolved_queries"])
    # Backward-compatible keys keep their original meaning.
    assert s["unresolved_queries"] == s["recovery_unresolved"]
    assert s["conversion_unresolved"] == s["not_converted"]


def test_clinic_template_conversions_are_counted_separately(outputs):
    s = outputs["clinic"]["summary"]

    assert s["total_queries"] == 22
    assert s["converted_by_general_rules"] == 15  # +Q022 prepared statement (S14)
    assert s["converted_by_query_specific_template"] == 7
    assert s["recovered_not_converted"] == 0


def test_no_template_conversions_outside_clinic(outputs):
    for name in ("simple_crud", "mixed_crud", "hard_crud", "helpdesk"):
        assert outputs[name]["summary"]["converted_by_query_specific_template"] == 0


def test_build_summary_on_hand_written_records():
    queries = [
        {"query_id": "Q001", "status": "RECOVERED"},
        {"query_id": "Q002", "status": "RECOVERED"},
        {"query_id": "Q003", "status": "UNRESOLVED"},
        {"query_id": "Q004", "status": "UNRESOLVED"},
    ]
    converted = [
        {"query_id": "Q001", "status": "CONVERTED", "conversion_rule": "general"},
        {"query_id": "Q002", "status": "UNRESOLVED"},
        {"query_id": "Q003", "status": "CONVERTED", "conversion_rule": "query_specific_template"},
        {"query_id": "Q004", "status": "UNRESOLVED"},
    ]

    s = build_summary(queries, converted)

    assert s == {
        "total_queries": 4,
        "recovered_queries": 2,
        "recovery_unresolved": 2,
        "converted_queries": 2,
        "converted_by_general_rules": 1,
        "converted_by_query_specific_template": 1,
        "recovered_not_converted": 1,
        "not_converted": 2,
        "unresolved_queries": 2,
        "conversion_unresolved": 2,
    }


def test_summary_separates_primary_outputs_from_supporting_artifacts(tmp_path):
    php, schema = BENCHMARKS["simple_crud"]

    summary = run_pipeline(php, schema, tmp_path)

    assert summary["primary_outputs"] == [
        "models.py",
        "data_access.py",
        "unresolved_queries.json",
    ]
    for name in summary["primary_outputs"]:
        assert (tmp_path / name).is_file()
    assert not set(summary["primary_outputs"]) & set(summary["supporting_artifacts"])
    assert {"queries.json", "converted_queries.json", "schema.json", "summary.json"} <= set(
        summary["supporting_artifacts"]
    )
