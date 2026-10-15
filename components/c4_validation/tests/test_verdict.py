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
    # The scenarios written here hold no oracle content to check for conflicts.
    monkeypatch.setattr(verdict_module, "conflicts", lambda cases: {})
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


# -------------------------------------------------------- conflicting expectations

from c4.verdict import conflicts  # noqa: E402


def _case(scenario_id, request, *, outcome="success", records=(), delta=None):
    scenario = {"scenario_id": scenario_id, "endpoint_id": "GET /api/things", "migrated_request": request}
    record = {
        "scenario_id": scenario_id,
        "legacy_response": {"status_code": 200, "outcome": outcome, "records": list(records), "messages": []},
        "db_state": {"baseline_fingerprint": "sha256:x", "delta": delta or {}},
        "excluded_fields": [],
    }
    return scenario, record


ALL = {"method": "GET", "path": "/api/things", "params": {}, "body": None, "records_at": "things"}
ONE = {**ALL, "params": {"q": "ram"}}
EVERYONE = [{"Name": "Ram"}, {"Name": "Alia"}]


def test_the_same_request_expected_to_show_everything_and_nothing_is_a_conflict():
    found = conflicts([
        _case("list_01", ALL, records=EVERYONE),
        _case("search_missing", ALL, outcome="no_effect"),
    ])

    assert found == {"list_01": ["search_missing"], "search_missing": ["list_01"]}


def test_different_requests_never_conflict():
    assert conflicts([
        _case("list_01", ALL, records=EVERYONE),
        _case("search_01", ONE, outcome="no_effect"),
    ]) == {}


def test_two_pages_that_agree_on_the_data_do_not_conflict():
    # One page shows more columns than the other: an answer with all of them satisfies both.
    assert conflicts([
        _case("panel_01", ALL, records=[{"Name": "Ram", "Age": "30"}, {"Name": "Alia", "Age": "25"}]),
        _case("list_01", ALL, records=EVERYONE),
    ]) == {}


def test_two_pages_that_write_differently_for_the_same_request_conflict():
    written = {"things": {"inserted": [{"name": "Ram"}], "updated": [], "deleted": []}}

    found = conflicts([
        _case("form_a", ALL, delta=written),
        _case("form_b", ALL, outcome="no_effect"),
    ])

    assert set(found) == {"form_a", "form_b"}


def test_a_conflict_makes_both_scenarios_inconclusive_and_keeps_what_was_seen(config, outcomes, monkeypatch):
    _add(config, "list_01", "GET /api/things")
    _add(config, "search_01", "GET /api/things")
    difference = {"where": "response", "kind": "unexpected_record", "migrated_record": {"id": 9}}
    outcomes["search_01"] = Comparison(FAIL, (difference,), ())
    monkeypatch.setattr(verdict_module, "conflicts", lambda cases: {"list_01": ["search_01"], "search_01": ["list_01"]})

    endpoint = evaluate(config, {})["endpoints"]["GET /api/things"]

    assert endpoint["verdict"] == INCONCLUSIVE
    search = endpoint["scenarios"][1]
    assert (search["verdict"], search["differences"]) == (INCONCLUSIVE, [])
    (abstention,) = search["abstentions"]
    assert abstention["kind"] == "conflicting_expectations"
    assert abstention["with"] == ["list_01"]
    assert abstention["differences_seen"] == [difference]
    assert _describe(abstention) == (
        "two legacy pages expect different results for the same request to this endpoint (also: list_01)"
    )
