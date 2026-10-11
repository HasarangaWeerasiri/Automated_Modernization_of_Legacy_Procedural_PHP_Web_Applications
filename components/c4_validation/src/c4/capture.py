"""Stage 2 — capture (LOOP 1).

Reset → snapshot → drive the LEGACY system over HTTP → snapshot → extract values →
assemble one oracle record.

The record is the expectation the migrated system will be held to. Nothing in it is
authored: the response values and the database delta are what the legacy system was
observed to do. That is what makes the oracle derived.

Every scenario is captured more than once from the same baseline. A value that
differs between captures cannot be expected of any system, so it is listed as
excluded instead of being compared.

    python -m c4.capture [scenario_id ...]

This is the walking-skeleton version. A scenario is read from a hand-written file
that stands in for the endpoint map and the scenario generator, and only pages whose
data is in one HTML table are supported.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from c4 import containers, dbstate
from c4.config import Config, ConfigError, load_config
from c4.extract import extract_tables

CAPTURE_RUNS = 2

_REQUIRED = ("scenario_id", "scenario_type", "endpoint_id", "legacy_request")


class CaptureError(RuntimeError):
    """A scenario could not be turned into a trustworthy oracle record."""


@dataclass(frozen=True)
class Observation:
    """What one run of a scenario against the legacy system showed."""

    status_code: int
    records: tuple[dict[str, str], ...]
    delta: dict[str, dict[str, list]]


def load_scenario(path: Path) -> dict[str, Any]:
    scenario = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in _REQUIRED if key not in scenario]
    if missing:
        raise CaptureError(f"{path.name} does not set: {', '.join(missing)}.")
    return scenario


def _observe(config: Config, scenario: dict[str, Any], baseline: dbstate.Snapshot) -> Observation:
    before = dbstate.ensure_baseline(config.legacy, baseline)

    request = scenario["legacy_request"]
    session = requests.Session()  # a fresh session: no cookies carried between runs
    session.trust_env = False
    try:
        response = session.request(
            request["method"],
            config.legacy.base_url + request["path"],
            params=request.get("params") or None,
            data=request.get("body") or None,
            timeout=config.request_timeout,
            allow_redirects=False,
        )
    finally:
        session.close()

    after = dbstate.snapshot(config.legacy)

    tables = extract_tables(response.content)
    chosen = scenario.get("legacy_table")
    if chosen is None and len(tables) != 1:
        # Sound-or-abstain: with no table, or several and no pointer to the right
        # one, any choice made here would be a guess.
        raise CaptureError(
            f"{scenario['scenario_id']}: the page shows {len(tables)} tables and the "
            "scenario does not say which one holds this endpoint's data."
        )
    table = tables[chosen or 0]

    return Observation(response.status_code, table.records, dbstate.delta(before, after))


def _volatile_labels(scenario_id: str, observations: list[Observation]) -> list[str]:
    """Labels whose value changed between captures of the same scenario."""
    first, *others = observations
    volatile: set[str] = set()
    for other in others:
        same_shape = len(other.records) == len(first.records) and all(
            a.keys() == b.keys() for a, b in zip(first.records, other.records)
        )
        if other.status_code != first.status_code or not same_shape or other.delta != first.delta:
            # More than single values differ, so there is no stable behaviour to record.
            raise CaptureError(
                f"{scenario_id}: the legacy system did not behave the same way in "
                f"{len(observations)} captures from the same baseline."
            )
        for a, b in zip(first.records, other.records):
            volatile.update(label for label in a if a[label] != b[label])
    labels = first.records[0].keys() if first.records else ()
    return [label for label in labels if label in volatile]


def _outcome(status_code: int) -> str:
    if status_code < 400:
        return "success"
    return "rejection" if status_code < 500 else "error"


def capture(config: Config, scenario: dict[str, Any], baseline: dbstate.Snapshot) -> dict[str, Any]:
    """Observe one scenario on the legacy system and return its oracle record."""
    observations = [_observe(config, scenario, baseline) for _ in range(CAPTURE_RUNS)]
    volatile = _volatile_labels(scenario["scenario_id"], observations)
    observed = observations[0]
    request = scenario["legacy_request"]
    return {
        "endpoint_id": scenario["endpoint_id"],
        "legacy_url": request["path"],
        "scenario_id": scenario["scenario_id"],
        "scenario_type": scenario["scenario_type"],
        "input": {
            "method": request["method"],
            "params": request.get("params") or {},
            "body": request.get("body"),
        },
        "legacy_response": {
            "status_code": observed.status_code,
            "outcome": _outcome(observed.status_code),
            "records": list(observed.records),
        },
        "db_state": {
            # The content fingerprint, so replay can confirm that the migrated
            # database starts from the same logical baseline.
            "baseline_fingerprint": baseline.data_fingerprint,
            "delta": observed.delta,
        },
        "excluded_fields": [
            {"scope": "response", "field": label, "reason": "observed_nondeterministic"}
            for label in volatile
        ],
        "capture_runs": CAPTURE_RUNS,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_record(config: Config, record: dict[str, Any]) -> Path:
    config.oracle_dir.mkdir(parents=True, exist_ok=True)
    path = config.oracle_dir / f"{record['scenario_id']}.json"
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4.capture", description="Capture oracle records from the legacy system.")
    parser.add_argument("scenario_id", nargs="*", help="scenarios to capture; default: all")
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.benchmark)
        files = sorted(config.scenario_dir.glob("*.json"))
        if args.scenario_id:
            files = [path for path in files if path.stem in args.scenario_id]
        if not files:
            raise CaptureError(f"No matching scenario in {config.scenario_dir}.")
        containers.wait_until_ready(config, timeout=0)
        baseline = dbstate.reset(config.legacy)

        print(f"benchmark: {config.benchmark}")
        for file in files:
            record = capture(config, load_scenario(file), baseline)
            path = write_record(config, record)
            changed = ", ".join(record["db_state"]["delta"]) or "none"
            print(
                f"  {record['scenario_id']}: {len(record['legacy_response']['records'])} record(s), "
                f"database change: {changed} -> {path.relative_to(config.root).as_posix()}"
            )
    except (
        CaptureError, ConfigError, containers.DockerUnavailableError, containers.NotReadyError,
        dbstate.BaselineError, dbstate.ResetError, dbstate.StructureChangedError,
        requests.RequestException,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
