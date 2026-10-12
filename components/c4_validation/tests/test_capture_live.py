"""Capture against the running legacy system, for every scenario of the active benchmark."""

import json

import pytest

from c4 import dbstate
from c4.capture import capture, load_scenario

pytestmark = pytest.mark.environment


@pytest.fixture
def scenarios(at_baseline):
    files = sorted(at_baseline.scenario_dir.glob("*.json"))
    if not files:
        pytest.skip("the active benchmark has no scenarios yet")
    return [load_scenario(file) for file in files]


def _without_timestamp(record):
    return {key: value for key, value in record.items() if key != "captured_at"}


def test_capture_is_repeatable(at_baseline, baselines, scenarios):
    for scenario in scenarios:
        first = capture(at_baseline, scenario, baselines)
        second = capture(at_baseline, scenario, baselines)

        assert _without_timestamp(first) == _without_timestamp(second), scenario["scenario_id"]


def test_record_is_tied_to_the_baseline_both_systems_share(at_baseline, baselines, scenarios):
    for scenario in scenarios:
        record = capture(at_baseline, scenario, baselines)

        assert record["db_state"]["baseline_fingerprint"] == baselines["migrated"].data_fingerprint


def test_capture_leaves_the_migrated_system_untouched(at_baseline, baselines, scenarios):
    for scenario in scenarios:
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
