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
            "legacy_url": "/page.php", "input": {"method": "GET", "params": {}, "body": None},
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


# ------------------------------------------------------------- differential report

from c4.verdict import differential_report  # noqa: E402

_REQUEST = {"method": "GET", "path": "/api/patients", "params": {"contact": "8683619153"}, "body": None}


def _scenario(scenario_id, verdict, differences=(), abstentions=(), request=_REQUEST):
    return {
        "scenario_id": scenario_id, "scenario_type": "typical", "verdict": verdict,
        "request": request, "legacy_request": "POST /patientsearch.php",
        "oracle_record": f"oracles/demo/{scenario_id}.json",
        "differences": list(differences), "abstentions": list(abstentions),
    }


ALTERED = {
    "where": "response", "kind": "record_not_reproduced",
    "legacy_record": {"First Name": "William", "Last Name": "Blake"},
    "values_not_found": {"Last Name": "Blake"},
    "closest_migrated_record": {"fname": "William", "lname": "Blakex"},
}
SUPPRESSED = {"where": "database", "kind": "delta_differs", "detail": {"dictionary_item_removed": ["root['contact']"]}}
CONFLICT = {
    "where": "scenario", "kind": "conflicting_expectations", "with": ["patient_list_typical_01"],
    "detail": "two legacy pages expect different results for the same request to this endpoint",
}
WRITE = {"method": "POST", "path": "/api/contact", "params": {}, "body": {"name": "Anu", "message": "hello"}}


def _report():
    return {"benchmark": "demo", "endpoints": {
        "GET /api/patients": {"verdict": FAIL, "scenarios": [
            _scenario("search_01", FAIL, [ALTERED]),
            _scenario("search_02", PASS),
            _scenario("search_missing", INCONCLUSIVE, abstentions=[CONFLICT]),
        ]},
        "POST /api/contact": {"verdict": FAIL, "scenarios": [
            _scenario("contact_01", FAIL, [SUPPRESSED], request=WRITE),
        ]},
    }}


INJECTION = {"summary": {"security improvement": 2, "vulnerability kept": 1}, "probes": [
    {"scenario_id": "search_injection_01", "label": "vulnerability kept", "probe": "' OR '1'='1"},
    {"scenario_id": "search_injection_02", "label": "security improvement", "probe": "'"},
    {"scenario_id": "contact_injection_01", "label": "security improvement", "probe": "'"},
]}


def test_the_summary_counts_every_scenario_of_every_endpoint():
    text = differential_report(_report(), INJECTION)

    assert "| `GET /api/patients` | **FAIL** | 1 | 1 | 1 |" in text
    assert "| `POST /api/contact` | **FAIL** | 0 | 1 | 0 |" in text


def test_a_failing_response_names_the_field_and_the_closest_migrated_record():
    text = differential_report(_report(), INJECTION)

    assert "### FAIL: search_01" in text
    assert "`GET /api/patients?contact=8683619153` · legacy page: `POST /patientsearch.php`" in text
    assert '| Response | "Last Name" = `"Blake"` not reproduced |' in text
    assert '| Response | closest migrated record: `{"fname": "William", "lname": "Blakex"}` |' in text


def test_a_failing_write_shows_its_request_body_and_the_database_row():
    text = differential_report(_report(), INJECTION)

    assert '```json\n{"name": "Anu", "message": "hello"}\n```' in text
    assert "| Database | `contact: missing on the migrated side` |" in text


def test_every_failing_scenario_links_to_the_oracle_record_it_rests_on():
    text = differential_report(_report(), INJECTION)

    assert "[search_01.json](../../oracles/demo/search_01.json)" in text
    assert "[contact_01.json](../../oracles/demo/contact_01.json)" in text


def test_passing_scenarios_appear_only_in_the_counts():
    assert "search_02" not in differential_report(_report(), INJECTION)


def test_an_inconclusive_scenario_is_listed_with_its_reason():
    text = differential_report(_report(), INJECTION)

    assert (
        "- **search_missing** (`GET /api/patients`): two legacy pages expect different results "
        "for the same request to this endpoint (also: patient_list_typical_01)"
    ) in text


def test_injection_probes_are_summarised_and_only_the_unsafe_ones_listed():
    text = differential_report(_report(), INJECTION)

    assert "| vulnerability kept | 1 |" in text
    assert "- **vulnerability kept**: search_injection_01, probe `' OR '1'='1`" in text
    assert "contact_injection_01" not in text


def test_a_clean_run_says_so_plainly():
    clean = {"benchmark": "demo", "endpoints": {"GET /api/x": {"verdict": PASS, "scenarios": [_scenario("x_01", PASS)]}}}

    text = differential_report(clean, {"summary": {}, "probes": []})

    assert "## Failing scenarios\n\nNone." in text
    assert "## Inconclusive scenarios\n\nNone." in text


def test_a_table_cell_cannot_be_broken_by_a_value_containing_a_bar():
    finding = {"where": "response", "kind": "unexpected_record", "migrated_record": {"name": "a|b"}}
    report = {"benchmark": "demo", "endpoints": {"GET /api/x": {"verdict": FAIL, "scenarios": [_scenario("x_01", FAIL, [finding])]}}}

    assert r'{"name": "a\|b"}' in differential_report(report, {"summary": {}, "probes": []})


def test_the_report_is_identical_for_identical_findings():
    assert differential_report(_report(), INJECTION) == differential_report(_report(), INJECTION)
