"""The inputs kept for each benchmark agree with each other.

Every benchmark folder under inputs/ holds an endpoint map, optionally its logins, and
the scenarios generated from them. A scenario that no longer fits its map would only
fail once the containers are up; this finds it without them.
"""

import json
from pathlib import Path

import pytest

from c4.scenarios import load_endpoint_map, load_logins, resolve

INPUTS = Path(__file__).resolve().parents[1] / "inputs"
BENCHMARKS = sorted(folder.name for folder in INPUTS.iterdir() if (folder / "endpoint_map.json").is_file())


def test_there_are_benchmark_inputs():
    assert BENCHMARKS


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_every_scenario_resolves_against_its_endpoint_map(benchmark):
    folder = INPUTS / benchmark
    endpoint_map = load_endpoint_map(folder / "endpoint_map.json")
    logins = load_logins(folder / "logins.json")
    files = sorted((folder / "scenarios").glob("*.json"))

    resolved = [resolve(endpoint_map, json.loads(file.read_text(encoding="utf-8")), logins) for file in files]

    assert resolved
    assert {scenario["endpoint_id"] for scenario in resolved} == set(endpoint_map)
    assert all(file.stem == scenario["scenario_id"] for file, scenario in zip(files, resolved))
