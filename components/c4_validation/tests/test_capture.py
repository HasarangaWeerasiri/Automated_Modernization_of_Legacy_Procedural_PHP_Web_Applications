"""Capture logic, checked without a running system."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from c4 import capture as capture_module
from c4.capture import CaptureError, Observation, capture, load_scenario, write_record
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
    monkeypatch.setattr(capture_module, "_observe", lambda config, scenario, baselines: next(runs))


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
    assert record["legacy_response"] == {"status_code": 200, "outcome": "success", "records": [row]}
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


def test_a_different_database_delta_is_not_a_stable_observation(config, monkeypatch):
    first = {"contact": {"inserted": [{"name": "Anu"}], "updated": [], "deleted": []}}
    second = {"contact": {"inserted": [{"name": "Ben"}], "updated": [], "deleted": []}}
    replay_observations(monkeypatch, observed(delta=first), observed(delta=second))

    with pytest.raises(CaptureError, match="did not behave the same way"):
        capture(config, SCENARIO, BASELINES)


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


def test_a_page_with_no_table_is_refused_not_recorded_as_empty(config, page):
    page("<script>alert('No entries found!')</script>")

    with pytest.raises(CaptureError, match="0 tables"):
        capture(config, SCENARIO, BASELINES)


def test_several_tables_without_a_pointer_are_refused(config, page):
    page(TWO_TABLES)

    with pytest.raises(CaptureError, match="2 tables"):
        capture(config, SCENARIO, BASELINES)


def test_a_scenario_can_point_at_one_table_of_several(config, page):
    page(TWO_TABLES)

    record = capture(config, {**SCENARIO, "legacy_table": 1}, BASELINES)

    assert record["legacy_response"]["records"] == [{"Doctor": "Ganesh"}]


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


def test_a_scenario_file_missing_a_required_part_is_refused(tmp_path):
    file = tmp_path / "broken.json"
    file.write_text(json.dumps({"scenario_id": "x", "scenario_type": "typical"}))

    with pytest.raises(CaptureError, match="endpoint_id, legacy_request"):
        load_scenario(file)
