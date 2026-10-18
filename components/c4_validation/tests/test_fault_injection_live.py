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

from c4.compare import FAIL, INCONCLUSIVE, PASS, compare
from c4.replay import replay
from c4.scenarios import load_all
from c4.verdict import evaluate

pytestmark = pytest.mark.environment

LIST, SEARCH = "patient_list_typical_01", "patient_search_typical_01"
CONTACT = "contact_form_typical_01"


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
    resolved = {scenario["scenario_id"]: scenario for scenario in load_all(at_baseline)}

    def run(scenario_id, exclude=None):
        scenario = resolved[scenario_id]
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
    scenarios = [s for entry in report["endpoints"].values() for s in entry["scenarios"]]
    assert all(s["verdict"] != FAIL for s in scenarios)
    # The only abstentions allowed are those the endpoint map forces: two legacy pages
    # that expect different results for the same request.
    # The other is a column the legacy page computes, which a correct API need not repeat.
    for s in scenarios:
        if s["verdict"] == INCONCLUSIVE:
            for a in s["abstentions"]:
                allowed = a["kind"] == "conflicting_expectations" or a.get("reason") == "computed_on_page"
                assert allowed, (s["scenario_id"], a)


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
    verdicts = {s["scenario_id"]: s["verdict"] for s in endpoint["scenarios"]}
    assert verdicts[SEARCH] == FAIL


# ---------------------------------------------------- planted faults in a write


def test_a_write_passes_although_status_and_message_wording_differ(stub, judge):
    # The legacy page answers 200 with a pop-up; the stub answers 201 with other words.
    assert judge(CONTACT).verdict == PASS


def test_a_suppressed_database_row_fails_although_the_response_reports_success(stub, judge):
    stub("suppresses_contact_insert")

    result = judge(CONTACT)

    assert result.verdict == FAIL
    assert [d["where"] for d in result.differences] == ["database"]
    assert "root['contact']" in str(result.differences[0]["detail"])


def test_a_wrong_stored_value_fails_and_the_column_is_named(stub, judge):
    stub("stores_wrong_message")

    (difference,) = judge(CONTACT).differences

    assert difference["where"] == "database"
    assert "['contact']['inserted'][0]['message']" in str(difference["detail"])


def test_a_row_written_twice_fails(stub, judge):
    stub("inserts_contact_twice")

    result = judge(CONTACT)

    assert result.verdict == FAIL
    assert "repetition_change" in result.differences[0]["detail"]


def test_a_faulty_write_does_not_spoil_the_scenario_after_it(stub, judge):
    stub("inserts_contact_twice")
    assert judge(CONTACT).verdict == FAIL

    # The read scenario starts from the baseline again, not from the two extra rows.
    assert judge(LIST).verdict == PASS


# ------------------------------------------------------------ labelled comparison


def test_swapped_fields_are_detected_under_their_columns(stub, judge):
    # Until values were compared under their columns, two fields that exchange values
    # still matched and this gave PASS: the gap labelled comparison closes.
    stub("swaps_first_and_last_name")

    result = judge(LIST)

    assert result.verdict == FAIL
    misplaced = [d["values_under_another_column"] for d in result.differences if "values_under_another_column" in d]
    assert misplaced and {"patreg.fname", "patreg.lname"} <= {e["column"] for m in misplaced for e in m.values()}


# ---------------------------------------------------------------- injection rubric

from c4.compare import IMPROVEMENT, SAFE_AS_BEFORE, UNHANDLED, VULNERABILITY_KEPT  # noqa: E402
from c4.verdict import evaluate_injection  # noqa: E402


def test_a_parameterised_migrated_system_handles_every_probe_safely(stub, at_baseline, baselines):
    report = evaluate_injection(at_baseline, baselines)

    assert report["probes"]
    assert set(report["summary"]) <= {IMPROVEMENT, SAFE_AS_BEFORE}


def test_the_legacy_search_leak_is_recorded_as_closed(stub, at_baseline, baselines):
    probes = {p["scenario_id"]: p for p in evaluate_injection(at_baseline, baselines)["probes"]}

    tautology = probes["patient_search_injection_contact_02"]
    assert tautology["legacy"]["behaviour"] == "vulnerable"
    assert tautology["legacy"]["effects"][0]["kind"] == "leak"
    assert tautology["label"] == IMPROVEMENT


def test_a_migrated_system_that_pastes_input_into_sql_is_caught(stub, at_baseline, baselines):
    stub("concatenates_input")

    probes = {p["scenario_id"]: p for p in evaluate_injection(at_baseline, baselines)["probes"]}

    assert probes["patient_search_injection_contact_02"]["label"] == VULNERABILITY_KEPT
    assert probes["contact_form_injection_name_02"]["label"] == VULNERABILITY_KEPT
    assert probes["contact_form_injection_name_01"]["label"] == UNHANDLED  # a lone quote crashes it


def test_injection_probes_never_change_an_endpoints_verdict(stub, at_baseline, baselines):
    stub("concatenates_input")

    report = evaluate(at_baseline, baselines)

    judged = [s["scenario_id"] for entry in report["endpoints"].values() for s in entry["scenarios"]]
    assert not [scenario_id for scenario_id in judged if "_injection_" in scenario_id]
    assert len(report["injection_probes_not_judged_here"]) == 15


# ------------------------------------------------------------- pages behind a login


def test_a_page_behind_a_login_passes_when_the_same_user_sees_the_same_data(stub, judge):
    assert judge("patient_prescriptions_typical_01").verdict == PASS


def test_showing_other_users_data_fails(stub, judge):
    stub("shows_everyones_prescriptions")

    result = judge("patient_prescriptions_typical_01")

    assert result.verdict == FAIL
    assert {d["kind"] for d in result.differences} == {"unexpected_record"}


# ----------------------------------------------------- columns computed by the page


def test_status_wording_built_by_the_page_is_inconclusive_not_failed(stub, judge):
    result = judge("patient_appointments_typical_01")

    assert result.verdict == INCONCLUSIVE
    assert {(a["field"], a["reason"]) for a in result.abstentions} == {
        ("Current Status", "computed_on_page"), ("Action", "computed_on_page"),
    }


def test_a_wrong_date_beside_computed_columns_still_fails(stub, judge):
    stub("alters_appointment_date")

    result = judge("patient_appointments_typical_01")

    assert result.verdict == FAIL
    assert all(list(d["values_not_found"]) == ["Appointment Date"] for d in result.differences)
