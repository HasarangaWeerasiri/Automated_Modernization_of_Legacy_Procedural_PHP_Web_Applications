"""Stage 6 — verdict.

Replays every scenario that has an oracle record, compares it, and gives each
endpoint one verdict:

    FAIL          if any of its scenarios failed
    INCONCLUSIVE  otherwise, if any of its scenarios could not be judged
    PASS          otherwise

Every verdict is written with the path of the oracle record it rests on and the
differences that were found, so it can be traced back to its evidence (NFR3).

    python -m c4.verdict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import requests

from c4 import containers, dbstate, scenarios
from c4.capture import CaptureError
from c4.compare import FAIL, INCONCLUSIVE, PASS, compare
from c4.config import Config, ConfigError, load_config
from c4.replay import ReplayError, replay


def endpoint_verdict(scenario_verdicts: Iterable[str]) -> str:
    verdicts = set(scenario_verdicts)
    if FAIL in verdicts:
        return FAIL
    if INCONCLUSIVE in verdicts:
        return INCONCLUSIVE
    return PASS


def evaluate(config: Config, baselines: dict[str, dbstate.Snapshot]) -> dict[str, Any]:
    """Replay and compare every captured scenario of the benchmark."""
    endpoints: dict[str, dict[str, Any]] = {}
    for scenario in scenarios.load_all(config):
        stored = config.oracle_dir / f"{scenario['scenario_id']}.json"
        if not stored.is_file():
            continue  # not captured yet: there is no expectation to hold it to
        record = json.loads(stored.read_text(encoding="utf-8"))
        result = compare(record, replay(config, scenario, record, baselines))
        endpoints.setdefault(record["endpoint_id"], {"scenarios": []})["scenarios"].append({
            "scenario_id": record["scenario_id"],
            "scenario_type": record["scenario_type"],
            "verdict": result.verdict,
            "oracle_record": stored.relative_to(config.root).as_posix(),
            "differences": list(result.differences),
            "abstentions": list(result.abstentions),
        })
    return {
        "benchmark": config.benchmark,
        "endpoints": {
            endpoint: {
                "verdict": endpoint_verdict(s["verdict"] for s in entry["scenarios"]),
                "scenarios": entry["scenarios"],
            }
            for endpoint, entry in sorted(endpoints.items())
        },
    }


def write_report(config: Config, report: dict[str, Any]) -> Path:
    config.report_dir.mkdir(parents=True, exist_ok=True)
    path = config.report_dir / "verdicts.json"
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


_DELTA_CHANGES = {
    "values_changed": "value differs",
    "repetition_change": "written a different number of times",
    "iterable_item_removed": "missing on the migrated side",
    "dictionary_item_removed": "missing on the migrated side",
    "iterable_item_added": "only on the migrated side",
    "dictionary_item_added": "only on the migrated side",
}


def _describe_delta(detail: dict[str, Any]) -> str:
    """Say in words where two database deltas differ, e.g. contact.inserted[0].message."""
    lines = []
    for change, places in detail.items():
        for path in places:
            place = re.sub(r"\['([^']*)'\]", r".\1", path.removeprefix("root")).lstrip(".")
            line = f"{place}: {_DELTA_CHANGES.get(change, change.replace('_', ' '))}"
            info = places[path] if isinstance(places, dict) else None
            if change == "values_changed":
                line += f" (legacy {info['old_value']!r}, migrated {info['new_value']!r})"
            elif change == "repetition_change":
                line += f" (legacy {info['old_repeat']}, migrated {info['new_repeat']})"
            lines.append(line)
    return "; ".join(lines)


def _describe(finding: dict[str, Any]) -> str:
    kind = finding["kind"]
    if kind in ("delta_differs", "excluded_field_differs") and "detail" in finding:
        prefix = "excluded field differs: " if kind == "excluded_field_differs" else ""
        return prefix + _describe_delta(finding["detail"])
    if kind == "record_not_reproduced":
        return f"values not reproduced: {json.dumps(finding['values_not_found'], ensure_ascii=False)}"
    if kind == "unexpected_record":
        return f"record the legacy system did not show: {json.dumps(finding['migrated_record'], ensure_ascii=False, default=str)}"
    if kind == "outcome_differs":
        return f"legacy {finding['legacy']}, migrated {finding['migrated']} (status {finding['migrated_status_code']})"
    if kind == "excluded_field_differs" and "field" in finding:
        return f"excluded field '{finding['field']}' differs"
    if kind == "unreadable":
        return str(finding["detail"])
    return kind.replace("_", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4.verdict", description="Replay, compare and give a verdict per endpoint.")
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.benchmark)
        containers.wait_until_ready(config, timeout=0)
        baselines = {system.name: dbstate.reset(system) for system in config.systems}
        report = evaluate(config, baselines)
        path = write_report(config, report)
    except (
        CaptureError, ConfigError, ReplayError,
        scenarios.EndpointMapError, scenarios.ScenarioError,
        containers.DockerUnavailableError, containers.NotReadyError,
        dbstate.BaselineError, dbstate.ResetError, dbstate.StructureChangedError,
        requests.RequestException,
    ) as error:
        print(error, file=sys.stderr)
        return 2

    print(f"benchmark: {config.benchmark}")
    if not report["endpoints"]:
        print("  no oracle records yet: run python -m c4.capture first")
    for endpoint, entry in report["endpoints"].items():
        print(f"  {entry['verdict']:<13}{endpoint}")
        for scenario in entry["scenarios"]:
            print(f"      {scenario['verdict']:<13}{scenario['scenario_id']}")
            for finding in (*scenario["differences"], *scenario["abstentions"]):
                print(f"          {finding['where']}: {_describe(finding)}")
    print(f"report: {path.relative_to(config.root).as_posix()}")

    failed = any(entry["verdict"] == FAIL for entry in report["endpoints"].values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
