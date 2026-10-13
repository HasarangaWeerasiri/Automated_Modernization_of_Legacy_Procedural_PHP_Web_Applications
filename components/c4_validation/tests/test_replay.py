"""Replay logic, checked without a running system."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from c4 import replay as replay_module
from c4.config import load_config
from c4.replay import ReplayError, replay

BASELINE = SimpleNamespace(data_fingerprint="sha256:baseline")
BASELINES = {"legacy": BASELINE, "migrated": BASELINE}
RECORD = {"scenario_id": "search_typical_01", "db_state": {"baseline_fingerprint": "sha256:baseline"}}


def scenario(records_at="things", **request):
    return {"migrated_request": {
        "method": "GET", "path": "/api/things", "params": {"q": "abc"}, "body": None,
        "records_at": records_at, **request,
    }}


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response, self.requests, self.trust_env = response, [], True

    def request(self, method, url, **options):
        self.requests.append((method, url, options))
        return self.response

    def close(self):
        pass


@pytest.fixture
def api(monkeypatch):
    """Answer in place of the migrated system, with an unchanged database."""
    ensured = []

    def serve(body, status=200, delta=None):
        session = FakeSession(FakeResponse(status, body))
        monkeypatch.setattr(replay_module.requests, "Session", lambda: session)
        monkeypatch.setattr(
            replay_module.dbstate, "ensure_baseline",
            lambda system, baseline: ensured.append(system.name) or baseline,
        )
        monkeypatch.setattr(replay_module.dbstate, "snapshot", lambda system: BASELINE)
        monkeypatch.setattr(replay_module.dbstate, "delta", lambda before, after: delta or {})
        session.ensured = ensured
        return session

    return serve


def test_the_migrated_request_is_sent_as_the_scenario_describes(config, api):
    session = api({"things": []})

    replay(config, scenario(), RECORD, BASELINES)

    ((method, url, options),) = session.requests
    assert (method, url) == ("GET", "http://127.0.0.1:8000/api/things")
    assert options["params"] == {"q": "abc"}
    assert options["allow_redirects"] is False
    assert session.trust_env is False


def test_both_databases_are_brought_to_the_baseline_first(config, api):
    session = api({"things": []})

    replay(config, scenario(), RECORD, BASELINES)

    assert sorted(session.ensured) == ["legacy", "migrated"]


def test_replay_refuses_a_baseline_other_than_the_one_the_oracle_was_captured_from(config, api):
    session = api({"things": []})
    other = {"scenario_id": "s", "db_state": {"baseline_fingerprint": "sha256:another"}}

    with pytest.raises(ReplayError, match="does not start from the baseline"):
        replay(config, scenario(), other, BASELINES)
    assert session.requests == []  # nothing is sent to a system in the wrong state


def test_records_are_read_from_where_the_scenario_points(config, api):
    api({"things": [{"name": "Ram"}, {"name": "Alia"}]})

    result = replay(config, scenario(), RECORD, BASELINES)

    assert result.records == ({"name": "Ram"}, {"name": "Alia"})
    assert (result.status_code, result.outcome, result.problem) == (200, "success", None)


def test_records_can_sit_deeper_or_at_the_top_level(config, api):
    api({"data": {"things": [{"name": "Ram"}]}})
    assert replay(config, scenario("data.things"), RECORD, BASELINES).records == ({"name": "Ram"},)

    api([{"name": "Ram"}])
    assert replay(config, scenario(""), RECORD, BASELINES).records == ({"name": "Ram"},)


def test_a_single_object_is_one_record(config, api):
    api({"thing": {"name": "Ram"}})

    assert replay(config, scenario("thing"), RECORD, BASELINES).records == ({"name": "Ram"},)


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("<html>Internal Server Error</html>", "not JSON"),
        ({"detail": "Injected fault."}, "nothing at 'things'"),
        ({"things": "eleven"}, "not a list of records"),
        ({"things": ["Ram", "Alia"]}, "not a list of records"),
    ],
)
def test_a_response_without_readable_records_is_reported_not_guessed(config, api, body, problem):
    api(body, status=500)

    result = replay(config, scenario(), RECORD, BASELINES)

    assert result.records is None
    assert problem in result.problem
    assert result.outcome == "error"


def test_an_endpoint_said_to_return_no_records_has_none_whatever_its_body(config, api):
    api({"message": "Contact saved"}, status=201)

    result = replay(config, scenario(None, method="POST"), RECORD, BASELINES)

    assert result.records == () and result.problem is None


def test_the_database_change_made_by_the_request_is_returned(config, api):
    delta = {"contact": {"inserted": [{"name": "Anu"}], "updated": [], "deleted": []}}
    api({"things": []}, status=201, delta=delta)

    result = replay(config, scenario(method="POST", body={"name": "Anu"}), RECORD, BASELINES)

    assert result.delta == delta and result.outcome == "success"
