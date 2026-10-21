"""Logging in to reach a page, on both systems, checked without a running system.

Logging in is a precondition for reaching pages, never something that is tested.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from c4 import capture as capture_module
from c4 import replay as replay_module
from c4.capture import CaptureError, capture
from c4.config import load_config
from c4.replay import ReplayError, replay
from c4.scenarios import EndpointMapError, ScenarioError, load_endpoint_map, load_logins, resolve

LEGACY_LOGIN = {"method": "POST", "path": "/login.php", "body": {"user": "ann", "pass": "x"}, "expect_redirect": "home.php"}
MIGRATED_LOGIN = {"method": "POST", "path": "/api/auth/login", "body": {"user": "ann", "password": "x"}, "token_at": "access_token"}
LOGINS = {"format": "c4-stand-in/1", "logins": {"patient": {"legacy": LEGACY_LOGIN, "migrated": MIGRATED_LOGIN}}}

MAP = {"format": "c4-stand-in/1", "endpoints": [{
    "endpoint_id": "GET /api/me/things", "method": "GET", "path": "/api/me/things", "records_at": "things",
    "sources": [
        {"source_id": "dashboard", "login": "patient", "parameters": [],
         "legacy": {"method": "GET", "path": "/dashboard.php", "records": "only"}},
        {"source_id": "public_list", "parameters": [],
         "legacy": {"method": "GET", "path": "/list.php", "records": "only"}},
    ],
}]}


def scenario(source):
    return {"scenario_id": "s_01", "scenario_type": "typical", "endpoint_id": "GET /api/me/things",
            "source_id": source, "values": {}}


@pytest.fixture
def endpoint_map(tmp_path):
    file = tmp_path / "endpoint_map.json"
    file.write_text(json.dumps(MAP))
    return load_endpoint_map(file)


@pytest.fixture
def logins_file(tmp_path):
    file = tmp_path / "logins.json"
    file.write_text(json.dumps(LOGINS))
    return file


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


# ------------------------------------------------------------- the logins file


def test_a_benchmark_without_a_logins_file_has_no_logins(tmp_path):
    assert load_logins(tmp_path / "nothing_here.json") == {}


def test_a_login_must_say_how_to_log_in_on_both_systems(tmp_path):
    file = tmp_path / "logins.json"
    file.write_text(json.dumps({"format": "c4-stand-in/1", "logins": {"patient": {"legacy": LEGACY_LOGIN}}}))

    with pytest.raises(EndpointMapError, match="'patient' does not say how to log in on the migrated system"):
        load_logins(file)


def test_a_migrated_login_must_say_where_its_token_is(tmp_path):
    document = copy.deepcopy(LOGINS)
    del document["logins"]["patient"]["migrated"]["token_at"]
    file = tmp_path / "logins.json"
    file.write_text(json.dumps(document))

    with pytest.raises(EndpointMapError, match="does not set: token_at"):
        load_logins(file)


def test_a_logins_file_in_an_unknown_format_is_refused(tmp_path):
    file = tmp_path / "logins.json"
    file.write_text(json.dumps({"logins": {}}))

    with pytest.raises(EndpointMapError, match="no known format"):
        load_logins(file)


# --------------------------------------------------------------------- resolving


def test_a_page_behind_a_login_carries_both_logins_into_its_scenario(endpoint_map, logins_file):
    resolved = resolve(endpoint_map, scenario("dashboard"), load_logins(logins_file))

    assert resolved["login"] == "patient"
    assert resolved["legacy_login"] == LEGACY_LOGIN
    assert resolved["migrated_login"] == MIGRATED_LOGIN


def test_a_page_without_a_login_carries_none(endpoint_map, logins_file):
    resolved = resolve(endpoint_map, scenario("public_list"), load_logins(logins_file))

    assert "legacy_login" not in resolved and "migrated_login" not in resolved


def test_a_page_needing_a_login_that_is_not_described_is_refused(endpoint_map):
    with pytest.raises(ScenarioError, match="needs the login 'patient', which is not described"):
        resolve(endpoint_map, scenario("dashboard"), {})


# ----------------------------------------------------------- the legacy system

PAGE = "<table><tr><th>Name</th></tr><tr><td>Ram</td></tr></table>"
CAPTURED = {
    "scenario_id": "s_01", "scenario_type": "typical", "endpoint_id": "GET /api/me/things",
    "legacy_request": {"method": "GET", "path": "/dashboard.php", "params": {}, "body": None},
    "login": "patient", "legacy_login": LEGACY_LOGIN,
}
BASELINE = SimpleNamespace(data_fingerprint="sha256:baseline")
BASELINES = {"legacy": BASELINE, "migrated": BASELINE}


class LegacySession:
    """Answers the login with a redirect, and the page with a table."""

    def __init__(self, location):
        self.location, self.requests, self.trust_env = location, [], True

    def request(self, method, url, **options):
        self.requests.append((method, url, options))
        if url.endswith("/login.php"):
            return SimpleNamespace(status_code=302, headers={"Location": self.location}, content=b"")
        return SimpleNamespace(status_code=200, headers={}, content=PAGE.encode())

    def close(self):
        pass


@pytest.fixture
def legacy(monkeypatch):
    def serve(location="home.php"):
        session = LegacySession(location)
        session.snapshots = []
        monkeypatch.setattr(capture_module.requests, "Session", lambda: session)
        monkeypatch.setattr(capture_module.dbstate, "ensure_baseline", lambda system, baseline: baseline)
        monkeypatch.setattr(capture_module.dbstate, "clock", lambda system: None)  # the clock is not read
        monkeypatch.setattr(
            capture_module.dbstate, "snapshot",
            lambda system: session.snapshots.append(len(session.requests)) or BASELINE,
        )
        monkeypatch.setattr(capture_module.dbstate, "delta", lambda before, after: {})
        return session
    return serve


def test_a_page_behind_a_login_is_reached_after_logging_in_on_the_same_session(config, legacy):
    session = legacy()

    record = capture(config, CAPTURED, BASELINES)

    urls = [url for _, url, _ in session.requests]
    assert urls == ["http://127.0.0.1:8080/login.php", "http://127.0.0.1:8080/dashboard.php"] * 2
    assert session.requests[0][2]["data"] == {"user": "ann", "pass": "x"}
    assert record["legacy_response"]["records"] == [{"Name": "Ram"}]


def test_the_change_is_measured_from_after_the_login(config, legacy):
    session = legacy()

    capture(config, CAPTURED, BASELINES)

    # In each capture, one snapshot after the login (1 request so far), one after the page (2).
    assert session.snapshots[:2] == [1, 2]


def test_a_login_that_does_not_lead_where_expected_is_refused(config, legacy):
    legacy(location="index1.php")  # the legacy app sends a failed login back to its login page

    with pytest.raises(CaptureError, match="logging in as 'patient' on the legacy system did not succeed"):
        capture(config, CAPTURED, BASELINES)


# --------------------------------------------------------- the migrated system

REPLAYED = {
    **{key: CAPTURED[key] for key in ("scenario_id", "scenario_type", "endpoint_id", "login")},
    "migrated_request": {"method": "GET", "path": "/api/me/things", "params": {}, "body": None, "records_at": "things"},
    "migrated_login": MIGRATED_LOGIN,
}
RECORD = {"scenario_id": "s_01", "db_state": {"baseline_fingerprint": "sha256:baseline"}}


class Answer:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


class MigratedSession:
    def __init__(self, token_answer):
        self.token_answer, self.requests, self.headers, self.trust_env = token_answer, [], {}, True

    def request(self, method, url, **options):
        self.requests.append((url, options, dict(self.headers)))
        return self.token_answer if url.endswith("/api/auth/login") else Answer(200, {"things": []})

    def close(self):
        pass


@pytest.fixture
def migrated(monkeypatch):
    def serve(token_body, token_status=200):
        session = MigratedSession(Answer(token_status, token_body))
        monkeypatch.setattr(replay_module.requests, "Session", lambda: session)
        monkeypatch.setattr(replay_module.dbstate, "ensure_baseline", lambda system, baseline: baseline)
        monkeypatch.setattr(replay_module.dbstate, "clock", lambda system: None)  # the clock is not read
        monkeypatch.setattr(replay_module.dbstate, "snapshot", lambda system: BASELINE)
        monkeypatch.setattr(replay_module.dbstate, "delta", lambda before, after: {})
        return session
    return serve


def test_the_token_from_the_login_is_sent_with_the_request(config, migrated):
    session = migrated({"access_token": "abc"})

    replay(config, REPLAYED, RECORD, BASELINES)

    (login_url, login_options, _), (page_url, _, page_headers) = session.requests
    assert (login_url, login_options["json"]) == ("http://127.0.0.1:8000/api/auth/login", {"user": "ann", "password": "x"})
    assert page_url == "http://127.0.0.1:8000/api/me/things"
    assert page_headers["Authorization"] == "Bearer abc"


@pytest.mark.parametrize(("body", "status"), [({"detail": "no"}, 401), ({"token": "abc"}, 200), ("not json", 200), ({"access_token": ""}, 200)])
def test_a_login_that_gives_no_token_stops_the_replay(config, migrated, body, status):
    migrated(body, status)

    with pytest.raises(ReplayError, match="did not give a token"):
        replay(config, REPLAYED, RECORD, BASELINES)
