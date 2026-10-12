"""Fault injection against the stub API.

The stub is first shown to be judged equivalent as it stands. Then one known fault
at a time is switched on in it, and the engine must give the verdict that fault
deserves. A validator that misses a planted fault is worse than none.

These tests are specific to the stub and to the scenarios of the benchmark it was
written for. They are skipped when the migrated system is anything else.
"""

import copy
import json

import pytest
import requests

from c4.capture import load_scenario
from c4.compare import FAIL, INCONCLUSIVE, PASS, compare
from c4.replay import replay
from c4.verdict import evaluate

pytestmark = pytest.mark.environment

LIST, SEARCH = "patient_list_typical_01", "patient_search_typical_01"


def _http():
    session = requests.Session()
    session.trust_env = False
    return session


@pytest.fixture
def stub(at_baseline):
    """The stub's fault switch; every test leaves it switched off."""
    url = at_baseline.migrated.base_url + "/_fault"
    http = _http()
    try:
        available = http.get(url, timeout=10).json().get("available")
    except (requests.RequestException, ValueError, AttributeError):
        available = None
    if at_baseline.benchmark != "hms" or not available:
        pytest.skip("the migrated system is not the stub API written for the hms benchmark")

    def inject(name):
        assert http.put(f"{url}/{name}", timeout=10).status_code == 200

    http.delete(url, timeout=10)
    yield inject
    http.delete(url, timeout=10)
    http.close()


@pytest.fixture
def judge(at_baseline, baselines):
    """Replay one stored scenario and compare it, optionally excluding a response field."""
    def run(scenario_id, exclude=None):
        scenario = load_scenario(at_baseline.scenario_dir / f"{scenario_id}.json")
        stored = at_baseline.oracle_dir / f"{scenario_id}.json"
        record = copy.deepcopy(json.loads(stored.read_text(encoding="utf-8")))
        if exclude:
            record["excluded_fields"].append({"scope": "response", "field": exclude, "reason": "declared"})
        return compare(record, replay(at_baseline, scenario, record, baselines))
    return run


# ------------------------------------------------------------ a correct migration


def test_the_correct_stub_is_judged_equivalent(stub, at_baseline, baselines):
    # False-positive check: a FAIL here would be a defect in the engine.
    report = evaluate(at_baseline, baselines)

    assert report["endpoints"]
    assert {entry["verdict"] for entry in report["endpoints"].values()} == {PASS}


def test_three_consecutive_runs_give_identical_reports(stub, at_baseline, baselines):
    runs = [json.dumps(evaluate(at_baseline, baselines), sort_keys=True) for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]


# ----------------------------------------------------------------- planted faults


def test_an_altered_field_value_fails_and_the_field_is_named(stub, judge):
    stub("alters_last_name")

    result = judge(LIST)

    assert result.verdict == FAIL
    assert len(result.differences) == 11  # every patient's record is affected
    assert all(list(d["values_not_found"]) == ["Last Name"] for d in result.differences)


def test_records_in_a_different_order_pass(stub, judge):
    stub("reverses_order")

    assert judge(LIST).verdict == PASS


def test_a_change_only_to_an_excluded_field_is_inconclusive(stub, judge):
    stub("alters_password")

    result = judge(LIST, exclude="Password")

    assert result.verdict == INCONCLUSIVE
    assert result.differences == ()
    assert {a["field"] for a in result.abstentions} == {"Password"}


def test_the_same_change_fails_when_the_field_is_not_excluded(stub, judge):
    stub("alters_password")

    assert judge(LIST).verdict == FAIL


def test_a_dropped_record_fails_and_the_record_is_shown(stub, judge):
    stub("drops_last_record")

    result = judge(LIST)

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["kind"] == "record_not_reproduced"
    assert difference["closest_migrated_record"] is None


def test_an_added_record_fails_and_the_record_is_shown(stub, judge):
    stub("adds_a_record")

    result = judge(LIST)

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["kind"] == "unexpected_record"
    assert difference["migrated_record"]["fname"] == "Ghost"


def test_an_omitted_field_fails_and_the_field_is_named(stub, judge):
    stub("omits_email")

    result = judge(SEARCH)

    assert result.verdict == FAIL
    assert list(result.differences[0]["values_not_found"]) == ["Email"]


def test_an_ignored_filter_fails_the_search_and_not_the_list(stub, judge):
    stub("ignores_contact_filter")

    assert judge(LIST).verdict == PASS
    search = judge(SEARCH)
    assert search.verdict == FAIL
    assert {d["kind"] for d in search.differences} == {"unexpected_record"}
    assert len(search.differences) == 10  # every patient but the one searched for


def test_a_server_error_fails_on_outcome(stub, judge):
    stub("fails_with_server_error")

    result = judge(SEARCH)

    assert result.verdict == FAIL
    assert result.differences[0]["kind"] == "outcome_differs"


def test_one_failing_scenario_fails_its_endpoint(stub, at_baseline, baselines):
    stub("ignores_contact_filter")

    endpoint = evaluate(at_baseline, baselines)["endpoints"]["GET /api/patients"]

    assert endpoint["verdict"] == FAIL
    assert {s["scenario_id"]: s["verdict"] for s in endpoint["scenarios"]} == {LIST: PASS, SEARCH: FAIL}


# ---------------------------------------------------------------------- known gap


def test_known_gap_swapped_fields_are_not_detected_without_labels(stub, judge):
    # Records are matched by their values, not by field names, so two fields that
    # exchange values still match. Recorded here as a measured limit of the current
    # comparison; this test changes when values are compared under labels.
    stub("swaps_first_and_last_name")

    assert judge(LIST).verdict == PASS
