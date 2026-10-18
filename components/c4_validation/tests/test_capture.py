"""Capture logic, checked without a running system."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from c4 import capture as capture_module
from c4.capture import CaptureError, Observation, capture, write_record
from c4.config import load_config

SCENARIO = {
    "scenario_id": "search_typical_01",
    "scenario_type": "typical",
    "endpoint_id": "GET /api/things",
    "legacy_request": {
        "method": "POST",
        "path": "/search.php",
        "params": {},
        "body": {"q": "abc"},
    },
}

BASELINE = SimpleNamespace(data_fingerprint="sha256:baseline")
BASELINES = {"legacy": BASELINE, "migrated": BASELINE}


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


def observed(*records, status=200, delta=None):
    return Observation(status, tuple(records), delta or {})


def replay_observations(monkeypatch, *observations):
    runs = iter(observations)
    monkeypatch.setattr(capture_module, "_observe", lambda config, scenario, baselines, not_before=0.0: next(runs))


# ------------------------------------------------------------------ oracle record


def test_record_holds_what_the_legacy_system_was_observed_to_do(config, monkeypatch):
    row = {"Name": "Ram", "Fee": "550"}
    replay_observations(monkeypatch, observed(row), observed(row))

    record = capture(config, SCENARIO, BASELINES)

    assert record["endpoint_id"] == "GET /api/things"
    assert record["legacy_url"] == "/search.php"
    assert record["scenario_id"] == "search_typical_01"
    assert record["scenario_type"] == "typical"
    assert record["input"] == {"method": "POST", "params": {}, "body": {"q": "abc"}}
    assert record["legacy_response"] == {
        "status_code": 200, "outcome": "success", "records": [row], "messages": [],
    }
    assert record["db_state"] == {"baseline_fingerprint": "sha256:baseline", "delta": {}}
    assert record["excluded_fields"] == []
    assert record["capture_runs"] == 2


def test_record_never_contains_markup(config, monkeypatch):
    row = {"Name": "Ram"}
    replay_observations(monkeypatch, observed(row), observed(row))

    text = json.dumps(capture(config, SCENARIO, BASELINES))

    assert "<" not in text and ">" not in text


def test_database_delta_is_recorded(config, monkeypatch):
    delta = {"contact": {"inserted": [{"name": "Anu"}], "updated": [], "deleted": []}}
    replay_observations(monkeypatch, observed(delta=delta), observed(delta=delta))

    assert capture(config, SCENARIO, BASELINES)["db_state"]["delta"] == delta


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(200, "success"), (302, "success"), (404, "rejection"), (422, "rejection"), (500, "error")],
)
def test_outcome_follows_the_status_class(config, monkeypatch, status, outcome):
    replay_observations(monkeypatch, observed(status=status), observed(status=status))

    assert capture(config, SCENARIO, BASELINES)["legacy_response"]["outcome"] == outcome


# -------------------------------------------------------------- repeated capture


def test_a_value_that_changes_between_captures_is_excluded_not_expected(config, monkeypatch):
    replay_observations(
        monkeypatch,
        observed({"Name": "Ram", "Seen At": "10:00:01", "Fee": "550"}),
        observed({"Name": "Ram", "Seen At": "10:00:02", "Fee": "550"}),
    )

    record = capture(config, SCENARIO, BASELINES)

    assert record["excluded_fields"] == [
        {"scope": "response", "field": "Seen At", "reason": "observed_nondeterministic"}
    ]


def test_excluded_fields_are_listed_in_page_order(config, monkeypatch):
    replay_observations(
        monkeypatch,
        observed({"Token": "a", "Name": "Ram", "Seen At": "1"}),
        observed({"Token": "b", "Name": "Ram", "Seen At": "2"}),
    )

    fields = [entry["field"] for entry in capture(config, SCENARIO, BASELINES)["excluded_fields"]]

    assert fields == ["Token", "Seen At"]


def test_a_different_number_of_records_is_not_a_stable_observation(config, monkeypatch):
    replay_observations(monkeypatch, observed({"Name": "Ram"}), observed({"Name": "Ram"}, {"Name": "Alia"}))

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


def test_a_different_status_is_not_a_stable_observation(config, monkeypatch):
    replay_observations(monkeypatch, observed(status=200), observed(status=500))

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


def test_a_different_pop_up_message_is_not_a_stable_observation(config, monkeypatch):
    replay_observations(
        monkeypatch,
        Observation(200, (), {}, ("Message sent successfully!",)),
        Observation(200, (), {}, ("Something went wrong",)),
    )

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


def guestbook(*rows, updated=()):
    return {"guestbook": {"inserted": list(rows), "updated": list(updated), "deleted": []}}


def test_a_database_column_that_changes_between_captures_is_excluded_not_expected(config, monkeypatch):
    # The current time stored with a new row: the rest of the row is still expected.
    replay_observations(
        monkeypatch,
        observed(delta=guestbook({"id": 20, "name": "Anu", "created_on": "2026-10-18 04:42:15"})),
        observed(delta=guestbook({"id": 20, "name": "Anu", "created_on": "2026-10-18 04:42:17"})),
    )

    record = capture(config, SCENARIO, BASELINES)

    assert record["excluded_fields"] == [
        {"scope": "database", "field": "guestbook.created_on", "reason": "observed_nondeterministic"}
    ]
    assert record["db_state"]["delta"]["guestbook"]["inserted"][0]["name"] == "Anu"


def test_an_updated_column_that_changes_between_captures_is_excluded(config, monkeypatch):
    def seen(at):
        return guestbook(updated=[{"key": {"id": 1}, "changes": {"last_login_on": {"before": "2009-02-18", "after": at}}}])

    replay_observations(monkeypatch, observed(delta=seen("04:42:15")), observed(delta=seen("04:42:17")))

    fields = [entry["field"] for entry in capture(config, SCENARIO, BASELINES)["excluded_fields"]]

    assert fields == ["guestbook.last_login_on"]


@pytest.mark.parametrize(
    "second",
    [
        guestbook({"id": 20, "name": "Anu"}, {"id": 21, "name": "Anu"}),  # one row more
        {"contact": {"inserted": [{"id": 20, "name": "Anu"}], "updated": [], "deleted": []}},  # another table
        guestbook(),  # nothing written
        guestbook(updated=[{"key": {"id": 1}, "changes": {}}]),  # a row updated as well
    ],
)
def test_a_database_change_of_a_different_shape_is_not_a_stable_observation(config, monkeypatch, second):
    replay_observations(monkeypatch, observed(delta=guestbook({"id": 20, "name": "Anu"})), observed(delta=second))

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


def test_the_captures_are_sent_at_least_the_gap_apart(config, monkeypatch):
    asked = []

    def observe(config, scenario, baselines, not_before=0.0):
        asked.append(not_before)
        return Observation(200, (), {}, sent_at=100.0 + len(asked) * 5)

    monkeypatch.setattr(capture_module, "_observe", observe)

    capture(config, SCENARIO, BASELINES)

    assert asked == [0.0, 105.0 + capture_module.CAPTURE_GAP]


def test_a_capture_waits_until_it_may_be_sent(config, page, monkeypatch):
    page(ONE_TABLE)
    waited = []
    monkeypatch.setattr(capture_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(capture_module.time, "sleep", waited.append)

    observation = capture_module._observe(config, SCENARIO, BASELINES, not_before=11.5)

    assert waited == [1.5]
    assert observation.sent_at == 10.0


# ---------------------------------------------------------------- driving a page


class FakeSession:
    def __init__(self, body):
        self.body, self.requests, self.trust_env = body, [], True

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, content=self.body.encode())

    def close(self):
        pass


@pytest.fixture
def page(monkeypatch):
    """Serve a fixed page in place of the legacy system, with an unchanged database."""
    def serve(body):
        session = FakeSession(body)
        monkeypatch.setattr(capture_module.requests, "Session", lambda: session)
        monkeypatch.setattr(capture_module.dbstate, "ensure_baseline", lambda system, baseline: baseline)
        monkeypatch.setattr(capture_module.dbstate, "snapshot", lambda system: BASELINE)
        monkeypatch.setattr(capture_module.dbstate, "delta", lambda before, after: {})
        monkeypatch.setattr(capture_module.time, "sleep", lambda seconds: None)  # no real wait between captures
        return session
    return serve


ONE_TABLE = "<table><tr><th>Name</th></tr><tr><td>Ram</td></tr></table>"
TWO_TABLES = ONE_TABLE + "<table><tr><th>Doctor</th></tr><tr><td>Ganesh</td></tr></table>"


def test_the_legacy_request_is_sent_as_the_scenario_describes(config, page):
    session = page(ONE_TABLE)

    capture(config, SCENARIO, BASELINES)

    method, url, options = session.requests[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8080/search.php")
    assert options["data"] == {"q": "abc"}
    assert options["allow_redirects"] is False
    assert session.trust_env is False
    assert len(session.requests) == 2  # captured twice


def test_the_only_table_of_a_page_is_used(config, page):
    page(ONE_TABLE)

    assert capture(config, SCENARIO, BASELINES)["legacy_response"]["records"] == [{"Name": "Ram"}]


def test_a_message_in_place_of_the_expected_records_means_no_data_was_shown(config, page):
    page("<script>alert('No entries found! Please enter valid details');</script>")

    response = capture(config, SCENARIO, BASELINES)["legacy_response"]

    assert response["records"] == []
    assert response["messages"] == ["No entries found! Please enter valid details"]
    assert response["outcome"] == "no_effect"


def test_a_page_with_neither_table_nor_message_is_refused_not_recorded_as_empty(config, page):
    # The engine cannot tell "no data" from data it is unable to read, so it abstains.
    page("<div class='result'><span>Ram</span><span>Kumar</span></div>")

    with pytest.raises(CaptureError, match="neither a table nor a message"):
        capture(config, SCENARIO, BASELINES)


def test_a_page_that_shows_its_table_is_a_success_even_when_the_table_is_empty(config, page):
    page("<table><tr><th>Name</th></tr></table>")

    response = capture(config, SCENARIO, BASELINES)["legacy_response"]

    assert (response["records"], response["outcome"]) == ([], "success")


def test_several_tables_without_a_pointer_are_refused(config, page):
    page(TWO_TABLES)

    with pytest.raises(CaptureError, match="2 tables"):
        capture(config, SCENARIO, BASELINES)


def test_a_scenario_can_point_at_one_table_of_several(config, page):
    page(TWO_TABLES)

    record = capture(config, {**SCENARIO, "legacy_table": 1}, BASELINES)

    assert record["legacy_response"]["records"] == [{"Doctor": "Ganesh"}]


def test_pointing_at_a_table_the_page_does_not_have_is_refused(config, page):
    page(TWO_TABLES)

    with pytest.raises(CaptureError, match="points at table 2"):
        capture(config, {**SCENARIO, "legacy_table": 2}, BASELINES)


POP_UP = """<script type="text/javascript">alert("Message sent successfully!");window.location.href = "contact.html";</script>"""


def test_an_endpoint_that_shows_no_records_is_captured_with_its_pop_up_message(config, page):
    page(POP_UP)

    response = capture(config, {**SCENARIO, "legacy_table": "none"}, BASELINES)["legacy_response"]

    assert response["records"] == []
    assert response["messages"] == ["Message sent successfully!"]


def test_a_write_that_fails_silently_is_a_request_with_no_effect(config, page):
    # What a legacy page does when its insert fails: status 200, no table, no message,
    # no change to the database.
    page("")

    response = capture(config, {**SCENARIO, "legacy_table": "none"}, BASELINES)["legacy_response"]

    assert response == {"status_code": 200, "outcome": "no_effect", "records": [], "messages": []}


def test_a_write_that_changes_the_database_is_a_success_whatever_it_shows(config, monkeypatch):
    delta = {"contact": {"inserted": [{"name": "Anu"}], "updated": [], "deleted": []}}
    written = Observation(200, (), delta, (), shown=False)
    replay_observations(monkeypatch, written, written)

    assert capture(config, SCENARIO, BASELINES)["legacy_response"]["outcome"] == "success"


@pytest.mark.parametrize(("status", "outcome"), [(404, "rejection"), (500, "error")])
def test_a_status_of_400_or_more_decides_the_outcome_on_its_own(config, monkeypatch, status, outcome):
    quiet = Observation(status, (), {}, (), shown=False)
    replay_observations(monkeypatch, quiet, quiet)

    assert capture(config, SCENARIO, BASELINES)["legacy_response"]["outcome"] == outcome


def test_whether_records_were_shown_must_be_the_same_in_every_capture(config, monkeypatch):
    replay_observations(monkeypatch, Observation(200, (), {}, (), shown=True), Observation(200, (), {}, (), shown=False))

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


# -------------------------------------------------------------------------- files


def test_record_is_written_under_the_benchmark_as_stable_json(config):
    record = {"scenario_id": "search_typical_01", "legacy_response": {"records": [{"Name": "Renée"}]}}

    path = write_record(config, record)

    assert path == config.oracle_dir / "search_typical_01.json"
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    assert json.loads(raw.decode("utf-8")) == record
    assert "Renée" in raw.decode("utf-8")


def test_capturing_the_same_behaviour_again_leaves_the_stored_record_untouched(config):
    first = {"scenario_id": "s", "legacy_response": {"records": [{"Name": "Ram"}]}, "captured_at": "2026-10-11T08:00:00+00:00"}
    again = {**first, "captured_at": "2026-10-12T09:30:00+00:00"}

    path = write_record(config, first)
    write_record(config, again)

    assert json.loads(path.read_text(encoding="utf-8"))["captured_at"] == "2026-10-11T08:00:00+00:00"


def test_capturing_different_behaviour_replaces_the_stored_record(config):
    first = {"scenario_id": "s", "legacy_response": {"records": [{"Name": "Ram"}]}, "captured_at": "2026-10-11T08:00:00+00:00"}
    changed = {"scenario_id": "s", "legacy_response": {"records": [{"Name": "Rama"}]}, "captured_at": "2026-10-12T09:30:00+00:00"}

    path = write_record(config, first)
    write_record(config, changed)

    assert json.loads(path.read_text(encoding="utf-8")) == changed


# ------------------------------------------------------ tables that line up a form

FORM_TABLE = "<table><tr><td>Username</td><td><input type='text' name='u'></td></tr></table>"


def test_a_page_whose_only_table_lines_up_a_form_is_refused(config, page):
    page(FORM_TABLE)

    with pytest.raises(CaptureError, match="neither a table nor a message"):
        capture(config, SCENARIO, BASELINES)


def test_a_form_table_next_to_the_table_of_data_is_not_counted(config, page):
    page(FORM_TABLE + ONE_TABLE)

    assert capture(config, SCENARIO, BASELINES)["legacy_response"]["records"] == [{"Name": "Ram"}]


def test_a_form_table_keeps_its_place_so_table_numbers_do_not_move(config, page):
    page(FORM_TABLE + TWO_TABLES)

    record = capture(config, {**SCENARIO, "legacy_table": 2}, BASELINES)

    assert record["legacy_response"]["records"] == [{"Doctor": "Ganesh"}]


def test_pointing_at_a_table_that_lines_up_a_form_is_refused(config, page):
    page(FORM_TABLE + ONE_TABLE)

    with pytest.raises(CaptureError, match="only lines up a form"):
        capture(config, {**SCENARIO, "legacy_table": 0}, BASELINES)
