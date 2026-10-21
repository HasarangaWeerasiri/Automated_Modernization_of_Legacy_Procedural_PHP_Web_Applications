"""Capture against the running legacy system, for every scenario of the active benchmark."""

import json

import pytest

from c4 import dbstate
from c4.capture import capture
from c4.scenarios import load_all

pytestmark = pytest.mark.environment


@pytest.fixture
def scenarios(at_baseline):
    found = load_all(at_baseline)
    if not found:
        pytest.skip("the active benchmark has no scenarios yet")
    return found


@pytest.fixture
def sample(scenarios):
    """The first typical scenario of each legacy page: enough for properties of capture itself."""
    first = {}
    for scenario in scenarios:
        source = scenario["scenario_id"].split("_typical_")[0]
        if "_typical_" in scenario["scenario_id"] and source not in first:
            first[source] = scenario
    return list(first.values()) or scenarios[:1]


CHANGES = "(changes on every capture)"


def _without_timestamp(record):
    """A record without what it says itself differs on every capture.

    That is the time it was captured, and the values of the fields and columns it
    marks as changing: a row's creation time is stored as observed, so two captures
    can never hold the same one.
    """
    changing = {
        (entry["scope"], entry["field"]) for entry in record["excluded_fields"]
        if entry["reason"] in ("observed_nondeterministic", "current_date")
    } | {(entry["scope"], entry["field"]) for entry in record.get("clock_fields", ())}

    def row(table, values):
        return {c: CHANGES if ("database", f"{table}.{c}") in changing else v for c, v in values.items()}

    delta = {
        table: {
            "inserted": [row(table, item) for item in changes["inserted"]],
            "deleted": changes["deleted"],
            "updated": [
                {**item, "changes": {
                    c: {**change, "after": CHANGES} if ("database", f"{table}.{c}") in changing else change
                    for c, change in item["changes"].items()
                }}
                for item in changes["updated"]
            ],
        }
        for table, changes in record["db_state"]["delta"].items()
    }
    records = [
        {label: CHANGES if ("response", label) in changing else value for label, value in item.items()}
        for item in record["legacy_response"]["records"]
    ]
    kept = {key: value for key, value in record.items() if key != "captured_at"}
    return {
        **kept,
        "legacy_response": {**record["legacy_response"], "records": records},
        "db_state": {**record["db_state"], "delta": delta},
    }


def test_capture_is_repeatable(at_baseline, baselines, sample):
    for scenario in sample:
        first = capture(at_baseline, scenario, baselines)
        second = capture(at_baseline, scenario, baselines)

        assert _without_timestamp(first) == _without_timestamp(second), scenario["scenario_id"]


def test_record_is_tied_to_the_baseline_both_systems_share(at_baseline, baselines, sample):
    for scenario in sample:
        record = capture(at_baseline, scenario, baselines)

        assert record["db_state"]["baseline_fingerprint"] == baselines["migrated"].data_fingerprint


def test_capture_leaves_the_migrated_system_untouched(at_baseline, baselines, sample):
    for scenario in sample:
        capture(at_baseline, scenario, baselines)

    migrated = dbstate.snapshot(at_baseline.migrated)
    assert migrated.state_fingerprint == baselines["migrated"].state_fingerprint


def test_stored_oracle_records_are_reproduced_by_a_fresh_capture(at_baseline, baselines, scenarios):
    # Auditability: a committed record is evidence only if capturing again gives it back.
    checked = 0
    for scenario in scenarios:
        stored = at_baseline.oracle_dir / f"{scenario['scenario_id']}.json"
        if not stored.is_file():
            continue
        fresh = capture(at_baseline, scenario, baselines)
        recorded = json.loads(stored.read_text(encoding="utf-8"))

        assert _without_timestamp(fresh) == _without_timestamp(recorded), scenario["scenario_id"]
        checked += 1
    if not checked:
        pytest.skip("no oracle record has been stored yet")
