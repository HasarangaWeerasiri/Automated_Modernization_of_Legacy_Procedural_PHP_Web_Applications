"""The database clock, and a time written from it, against the running systems.

The clock tests name no table of any particular benchmark. The guestbook tests are
specific to the stub written for the wackopicko benchmark, and are skipped otherwise.
"""

from datetime import datetime

import pytest
import requests

from c4 import dbstate
from c4.capture import capture
from c4.compare import FAIL, PASS, compare
from c4.replay import replay
from c4.scenarios import load_all

pytestmark = pytest.mark.environment

GUESTBOOK = "guestbook_sign_typical_01"


@pytest.mark.parametrize("name", ["legacy", "migrated"])
def test_the_database_clock_reads_its_own_time_and_utc(at_baseline, name):
    system = getattr(at_baseline, name)

    window = dbstate.window(dbstate.clock(system), dbstate.clock(system))

    assert set(window) == {"local", "utc"}
    for start, end in window.values():
        assert datetime.fromisoformat(start) <= datetime.fromisoformat(end)


@pytest.fixture
def fault(at_baseline):
    """The wackopicko stub's fault switch; every test leaves it switched off."""
    url = at_baseline.migrated.base_url + "/_fault"
    http = requests.Session()
    http.trust_env = False
    try:
        available = http.get(url, timeout=10).json().get("available") or []
    except (requests.RequestException, ValueError, AttributeError):
        available = []
    if at_baseline.benchmark != "wackopicko" or "stores_a_fixed_time" not in available:
        pytest.skip("the migrated system is not the stub API written for the wackopicko benchmark")

    def plant(name):
        assert http.put(f"{url}/{name}", timeout=10).status_code == 200

    http.delete(url, timeout=10)
    yield plant
    http.delete(url, timeout=10)
    http.close()


@pytest.fixture
def entry(at_baseline, baselines):
    """A guestbook entry captured from the legacy system just now."""
    scenario = next(s for s in load_all(at_baseline) if s["scenario_id"] == GUESTBOOK)
    return scenario, capture(at_baseline, scenario, baselines)


def test_the_time_an_entry_is_written_is_recorded_as_the_time_of_the_request(fault, entry):
    _, record = entry

    assert {e["field"]: e["written"] for e in record["clock_fields"]} == {"guestbook.created_on": "time_of_request"}
    assert "guestbook.created_on" not in {e["field"] for e in record["excluded_fields"]}


def test_an_entry_written_at_the_time_of_its_request_passes(fault, entry, at_baseline, baselines):
    scenario, record = entry

    assert compare(record, replay(at_baseline, scenario, record, baselines)).verdict == PASS


def test_an_entry_written_with_a_fixed_time_fails(fault, entry, at_baseline, baselines):
    scenario, record = entry
    fault("stores_a_fixed_time")

    result = compare(record, replay(at_baseline, scenario, record, baselines))

    assert result.verdict == FAIL
    assert "2000-01-01 00:00:00" in str(result.differences)
    assert "the time of the request" in str(result.differences)
