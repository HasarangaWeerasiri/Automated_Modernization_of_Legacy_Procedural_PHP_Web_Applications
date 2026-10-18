"""Values that exist only on the legacy page: controls, and columns the page computes."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from c4 import capture as capture_module
from c4.capture import Observation, capture
from c4.compare import INCONCLUSIVE, PASS, compare
from c4.config import load_config
from c4.dbstate import Snapshot, TableState
from c4.extract import extract_tables
from c4.replay import Replay
from c4.verdict import _describe, differential_report


def records(html):
    (table,) = extract_tables(html)
    return list(table.records)


# --------------------------------------------------------- controls are not data


def test_the_label_of_a_button_is_not_data():
    html = """<table><tr><th>Doctor</th><th>Action</th></tr>
              <tr><td>Dinesh</td><td><a href="?cancel=1"><button class="btn">Cancel</button></a></td></tr></table>"""

    assert records(html) == [{"Doctor": "Dinesh", "Action": ""}]


def test_text_next_to_a_button_is_still_data():
    html = "<table><tr><th>Status</th></tr><tr><td>Paid <button>Refund</button></td></tr></table>"

    assert records(html) == [{"Status": "Paid"}]


def test_a_list_of_choices_and_a_text_box_are_controls_not_data():
    html = """<table><tr><th>Doctor</th><th>Change to</th><th>Note</th></tr>
              <tr><td>Dinesh</td><td><select><option>Ganesh</option><option>Amit</option></select></td>
                  <td><textarea>type here</textarea></td></tr></table>"""

    assert records(html) == [{"Doctor": "Dinesh", "Change to": "", "Note": ""}]


def test_a_link_is_data():
    html = "<table><tr><th>Doctor</th></tr><tr><td><a href='doctor.php?id=3'>Dinesh</a></td></tr></table>"

    assert records(html) == [{"Doctor": "Dinesh"}]


# ------------------------------------------------- columns computed by the page

APPOINTMENTS = TableState(
    columns=("id", "doctor", "userStatus", "doctorStatus"), primary_key=("id",),
    definition=("id INT", "doctor TEXT", "userStatus INT", "doctorStatus INT"), auto_increment=3,
    rows=({"id": 1, "doctor": "Ganesh", "userStatus": 1, "doctorStatus": 0},
          {"id": 2, "doctor": "Dinesh", "userStatus": 1, "doctorStatus": 1}),
)
BASELINE = Snapshot({"appointments": APPOINTMENTS})
BASELINES = {"legacy": BASELINE, "migrated": BASELINE}
SCENARIO = {
    "scenario_id": "appointments_01", "scenario_type": "typical", "endpoint_id": "GET /api/me/appointments",
    "legacy_request": {"method": "GET", "path": "/dashboard.php", "params": {}, "body": None},
}


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


def observe(monkeypatch, *rows, delta=None):
    seen = Observation(200, tuple(rows), delta or {})
    monkeypatch.setattr(capture_module, "_observe", lambda config, scenario, baselines, not_before=0.0: seen)


def excluded(record):
    return [(entry["field"], entry["reason"]) for entry in record["excluded_fields"]]


def test_a_column_whose_values_occur_nowhere_in_the_database_is_computed(config, monkeypatch):
    observe(
        monkeypatch,
        {"Doctor": "Ganesh", "Status": "Cancelled by Doctor"},
        {"Doctor": "Dinesh", "Status": "Active"},
    )

    record = capture(config, SCENARIO, BASELINES)

    assert excluded(record) == [("Status", "computed_on_page")]


def test_a_column_backed_by_the_database_is_compared(config, monkeypatch):
    observe(monkeypatch, {"Doctor": "Ganesh", "Doctor Status": "0"}, {"Doctor": "Dinesh", "Doctor Status": "1"})

    assert excluded(capture(config, SCENARIO, BASELINES)) == []


def test_a_value_the_request_itself_wrote_counts_as_stored(config, monkeypatch):
    written = {"appointments": {"inserted": [{"id": 3, "doctor": "Kumar", "userStatus": 1, "doctorStatus": 1}],
                                "updated": [], "deleted": []}}
    observe(monkeypatch, {"Doctor": "Kumar"}, delta=written)

    assert excluded(capture(config, SCENARIO, BASELINES)) == []


def test_an_empty_column_is_not_called_computed(config, monkeypatch):
    observe(monkeypatch, {"Doctor": "Ganesh", "Action": ""}, {"Doctor": "Dinesh", "Action": ""})

    assert excluded(capture(config, SCENARIO, BASELINES)) == []


def test_without_database_facts_nothing_is_called_computed(config, monkeypatch):
    observe(monkeypatch, {"Status": "Cancelled by Doctor"})
    empty = SimpleNamespace(data_fingerprint="sha256:x", tables={})

    assert excluded(capture(config, SCENARIO, {"legacy": empty, "migrated": empty})) == []


# ------------------------------------------------------- judging a computed column

RECORD = {
    "scenario_id": "appointments_01",
    "legacy_response": {"status_code": 200, "outcome": "success", "messages": [], "records": [
        {"Doctor": "Ganesh", "Status": "Cancelled by Doctor"},
    ]},
    "db_state": {"baseline_fingerprint": "sha256:x", "delta": {}},
    "excluded_fields": [{"scope": "response", "field": "Status", "reason": "computed_on_page"}],
}


def test_an_api_returning_the_inputs_instead_of_the_wording_is_inconclusive_with_the_reason():
    flags = {"doctor": "Ganesh", "userStatus": 1, "doctorStatus": 0}

    result = compare(RECORD, Replay(200, "success", (flags,), None, {}))

    assert result.verdict == INCONCLUSIVE
    (abstention,) = result.abstentions
    assert (abstention["field"], abstention["reason"]) == ("Status", "computed_on_page")
    assert _describe(abstention) == (
        "'Status' is not reproduced, and it is computed by the legacy page from other data, so it cannot be judged"
    )


def test_an_api_returning_the_same_wording_passes():
    worded = {"doctor": "Ganesh", "status": "Cancelled by Doctor"}

    assert compare(RECORD, Replay(200, "success", (worded,), None, {})).verdict == PASS


def test_a_wrong_value_in_a_stored_column_still_fails_beside_a_computed_one():
    wrong = {"doctor": "Gaensh", "status": "Cancelled by Doctor"}

    assert compare(RECORD, Replay(200, "success", (wrong,), None, {})).verdict == "FAIL"


def test_the_readable_report_states_each_reason_once():
    abstention = {"where": "response", "kind": "excluded_field_differs", "field": "Status", "reason": "computed_on_page"}
    scenario = {
        "scenario_id": "appointments_01", "verdict": INCONCLUSIVE, "differences": [],
        "abstentions": [abstention, abstention, abstention],
        "request": {"method": "GET", "path": "/x", "params": {}, "body": None},
        "legacy_request": "GET /x.php", "oracle_record": "oracles/demo/appointments_01.json",
    }
    report = {"benchmark": "demo", "endpoints": {"GET /x": {"verdict": INCONCLUSIVE, "scenarios": [scenario]}}}

    text = differential_report(report, {"summary": {}, "probes": []})

    assert text.count("'Status' is not reproduced") == 1
