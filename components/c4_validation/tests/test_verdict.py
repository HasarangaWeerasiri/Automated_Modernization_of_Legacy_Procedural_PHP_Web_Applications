"""Verdict aggregation and reporting, checked without a running system."""

import json
from pathlib import Path

import pytest

from c4 import verdict as verdict_module
from c4.compare import FAIL, INCONCLUSIVE, PASS, Comparison
from c4.config import load_config
from c4.verdict import _describe, endpoint_verdict, evaluate, write_report


@pytest.mark.parametrize(
    ("scenarios", "expected"),
    [
        ([PASS, PASS], PASS),
        ([PASS, INCONCLUSIVE], INCONCLUSIVE),
        ([PASS, FAIL], FAIL),
        ([INCONCLUSIVE, FAIL, PASS], FAIL),  # a failure is never hidden by an abstention
        ([INCONCLUSIVE], INCONCLUSIVE),
    ],
)
def test_an_endpoint_takes_the_worst_verdict_of_its_scenarios(scenarios, expected):
    assert endpoint_verdict(scenarios) == expected


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


def _add(config, scenario_id, endpoint, *, captured=True):
    config.scenario_dir.mkdir(parents=True, exist_ok=True)
    config.oracle_dir.mkdir(parents=True, exist_ok=True)
    (config.scenario_dir / f"{scenario_id}.json").write_text(json.dumps({
        "scenario_id": scenario_id, "scenario_type": "typical",
        "endpoint_id": endpoint, "source_id": "page", "values": {},
    }))
    # The endpoint map grows with each endpoint a test adds.
    method, path = endpoint.split(" ")
    known = json.loads(config.endpoint_map_file.read_text()) if config.endpoint_map_file.is_file() else {
        "format": "c4-stand-in/1", "endpoints": [],
    }
    if endpoint not in [entry["endpoint_id"] for entry in known["endpoints"]]:
        known["endpoints"].append({
            "endpoint_id": endpoint, "method": method, "path": path, "records_at": "things",
            "sources": [{
                "source_id": "page", "parameters": [],
                "legacy": {"method": "GET", "path": "/page.php", "records": "only"},
            }],
        })
    config.endpoint_map_file.write_text(json.dumps(known))
    if captured:
        (config.oracle_dir / f"{scenario_id}.json").write_text(json.dumps({
            "scenario_id": scenario_id, "scenario_type": "typical", "endpoint_id": endpoint,
        }))


@pytest.fixture
def outcomes(monkeypatch):
    """Decide the comparison result of each scenario in place of a real replay."""
    decided: dict[str, Comparison] = {}
    monkeypatch.setattr(verdict_module, "replay", lambda config, scenario, record, baselines: record)
    monkeypatch.setattr(
        verdict_module, "compare",
        lambda record, replayed: decided.get(record["scenario_id"], Comparison(PASS, (), ())),
    )
    return decided


def test_report_groups_scenarios_under_their_endpoint(config, outcomes):
    _add(config, "list_01", "GET /api/things")
    _add(config, "search_01", "GET /api/things")
    _add(config, "create_01", "POST /api/things")
    difference = {"where": "response", "kind": "unexpected_record", "migrated_record": {"id": 9}}
    outcomes["search_01"] = Comparison(FAIL, (difference,), ())

    report = evaluate(config, {})

    assert report["benchmark"] == "demo"
    assert list(report["endpoints"]) == ["GET /api/things", "POST /api/things"]
    things = report["endpoints"]["GET /api/things"]
    assert things["verdict"] == FAIL
    assert [(s["scenario_id"], s["verdict"]) for s in things["scenarios"]] == [
        ("list_01", PASS), ("search_01", FAIL),
    ]
    assert things["scenarios"][1]["differences"] == [difference]
    assert report["endpoints"]["POST /api/things"]["verdict"] == PASS


def test_every_verdict_points_at_the_oracle_record_it_rests_on(config, outcomes):
    _add(config, "list_01", "GET /api/things")

    (scenario,) = evaluate(config, {})["endpoints"]["GET /api/things"]["scenarios"]

    assert scenario["oracle_record"] == "oracles/demo/list_01.json"
    assert (config.root / scenario["oracle_record"]).is_file()


def test_a_scenario_that_was_never_captured_gets_no_verdict(config, outcomes):
    _add(config, "list_01", "GET /api/things")
    _add(config, "new_01", "GET /api/other", captured=False)

    assert list(evaluate(config, {})["endpoints"]) == ["GET /api/things"]


def test_report_file_is_stable_json_without_a_timestamp(config, outcomes):
    _add(config, "list_01", "GET /api/things")
    report = evaluate(config, {})

    path = write_report(config, report)
    first = path.read_bytes()
    write_report(config, evaluate(config, {}))

    assert path == config.report_dir / "verdicts.json"
    assert path.read_bytes() == first  # identical runs give an identical file
    assert first.endswith(b"\n") and b"\r" not in first
    assert json.loads(first) == report


# ------------------------------------------------------------------- descriptions


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            {"dictionary_item_removed": ["root['contact']"]},
            "contact: missing on the migrated side",
        ),
        (
            {"values_changed": {"root['contact']['inserted'][0]['message']": {"old_value": "hello", "new_value": "hellox"}}},
            "contact.inserted[0].message: value differs (legacy 'hello', migrated 'hellox')",
        ),
        (
            {"repetition_change": {"root['contact']['inserted'][0]": {"old_repeat": 1, "new_repeat": 2}}},
            "contact.inserted[0]: written a different number of times (legacy 1, migrated 2)",
        ),
        (
            {"dictionary_item_added": ["root['audit']"]},
            "audit: only on the migrated side",
        ),
    ],
)
def test_a_database_difference_is_described_by_table_row_and_column(detail, expected):
    finding = {"where": "database", "kind": "delta_differs", "detail": detail}

    assert _describe(finding) == expected
