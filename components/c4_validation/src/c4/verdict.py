"""Stage 6 — verdict.

Replays every scenario that has an oracle record, compares it, and gives each
endpoint one verdict. Injection probes are kept apart: they are judged by a rubric of
their own (FR13), not by equivalence.

    FAIL          if any of its scenarios failed
    INCONCLUSIVE  otherwise, if any of its scenarios could not be judged
    PASS          otherwise

Every verdict is written with the path of the oracle record it rests on and the
differences that were found, so it can be traced back to its evidence (NFR3).

Injection probes are replayed too, and each gets a label of its own (see compare.py)
in a separate injection-safety report. They never change an endpoint's verdict.

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
from c4.compare import FAIL, INCONCLUSIVE, PASS, compare, judge_probe
from c4.config import Config, ConfigError, load_config
from c4.replay import Replay, ReplayError, replay


def endpoint_verdict(scenario_verdicts: Iterable[str]) -> str:
    verdicts = set(scenario_verdicts)
    if FAIL in verdicts:
        return FAIL
    if INCONCLUSIVE in verdicts:
        return INCONCLUSIVE
    return PASS


def _as_replay(record: dict[str, Any]) -> Replay:
    """What the legacy system did, presented as if a migrated system had done it."""
    response = record["legacy_response"]
    outcome = "success" if response["outcome"] == "no_effect" else response["outcome"]
    return Replay(response["status_code"], outcome, tuple(response["records"]), None, record["db_state"]["delta"])


def _compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether one answer can satisfy both oracle records.

    If what one legacy page did would pass against the other's record, a migrated
    system that does the same satisfies both.
    """
    return compare(a, _as_replay(b)).verdict != FAIL or compare(b, _as_replay(a)).verdict != FAIL


def conflicts(cases: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, list[str]]:
    """Scenarios whose identical request is expected to give incompatible results.

    One endpoint can replace several legacy pages. Two of its scenarios may then send
    exactly the same request to the migrated system while the pages they came from
    did different things. No migrated system can satisfy both, so neither scenario
    can be judged. Returns, per scenario, the scenarios it conflicts with.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for scenario, record in cases:
        request = json.dumps([scenario["endpoint_id"], scenario["migrated_request"]], sort_keys=True)
        groups.setdefault(request, []).append(record)
    found: dict[str, list[str]] = {}
    for records in groups.values():
        for a in records:
            for b in records:
                if a is not b and not _compatible(a, b):
                    found.setdefault(a["scenario_id"], []).append(b["scenario_id"])
    return found


def evaluate(config: Config, baselines: dict[str, dbstate.Snapshot]) -> dict[str, Any]:
    """Replay and compare every captured scenario of the benchmark."""
    endpoints: dict[str, dict[str, Any]] = {}
    injection_probes: list[str] = []
    cases: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for scenario in scenarios.load_all(config):
        if scenario["scenario_type"] == "injection":
            injection_probes.append(scenario["scenario_id"])
            continue
        stored = config.oracle_dir / f"{scenario['scenario_id']}.json"
        if not stored.is_file():
            continue  # not captured yet: there is no expectation to hold it to
        cases.append((scenario, json.loads(stored.read_text(encoding="utf-8"))))

    conflicting = conflicts(cases)
    for scenario, record in cases:
        result = compare(record, replay(config, scenario, record, baselines))
        verdict, differences, abstentions = result.verdict, list(result.differences), list(result.abstentions)
        if record["scenario_id"] in conflicting:
            # The comparison still ran; what it found is kept for the reader, but it
            # cannot count against the migrated system.
            verdict, differences = INCONCLUSIVE, []
            abstentions.append({
                "where": "scenario",
                "kind": "conflicting_expectations",
                "with": sorted(conflicting[record["scenario_id"]]),
                "detail": "two legacy pages expect different results for the same request to this endpoint",
                "differences_seen": list(result.differences),
            })
        endpoints.setdefault(record["endpoint_id"], {"scenarios": []})["scenarios"].append({
            "scenario_id": record["scenario_id"],
            "scenario_type": record["scenario_type"],
            "verdict": verdict,
            "oracle_record": Path(config.oracle_dir / f"{record['scenario_id']}.json").relative_to(config.root).as_posix(),
            "differences": differences,
            "abstentions": abstentions,
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
        "injection_probes_not_judged_here": sorted(injection_probes),
    }


def evaluate_injection(config: Config, baselines: dict[str, dbstate.Snapshot]) -> dict[str, Any]:
    """Replay every captured injection probe and label what it did (FR13)."""
    probes = []
    for scenario in scenarios.load_all(config):
        if scenario["scenario_type"] != "injection":
            continue
        stored = config.oracle_dir / f"{scenario['scenario_id']}.json"
        if not stored.is_file():
            continue
        record = json.loads(stored.read_text(encoding="utf-8"))
        judged = judge_probe(record, scenario, replay(config, scenario, record, baselines))
        judged["oracle_record"] = stored.relative_to(config.root).as_posix()
        probes.append(judged)
    labels = sorted({probe["label"] for probe in probes})
    return {
        "benchmark": config.benchmark,
        "summary": {label: sum(probe["label"] == label for probe in probes) for label in labels},
        "probes": probes,
    }


def write_report(config: Config, report: dict[str, Any], name: str = "verdicts.json") -> Path:
    config.report_dir.mkdir(parents=True, exist_ok=True)
    path = config.report_dir / name
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
        if finding.get("note"):
            prefix = f"{finding['note']}: "
        return prefix + _describe_delta(finding["detail"])
    if kind == "record_not_reproduced":
        return f"values not reproduced: {json.dumps(finding['values_not_found'], ensure_ascii=False)}"
    if kind == "unexpected_record":
        return f"record the legacy system did not show: {json.dumps(finding['migrated_record'], ensure_ascii=False, default=str)}"
    if kind == "outcome_differs":
        return f"legacy {finding['legacy']}, migrated {finding['migrated']} (status {finding['migrated_status_code']})"
    if kind == "conflicting_expectations":
        return f"{finding['detail']} (also: {', '.join(finding['with'])})"
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
        injection = evaluate_injection(config, baselines)
        path = write_report(config, report)
        injection_path = write_report(config, injection, "injection_safety.json")
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

    if injection["probes"]:
        print(f"injection probes: {len(injection['probes'])}")
        for label, count in injection["summary"].items():
            print(f"  {count:>3}  {label}")
        for probe in injection["probes"]:
            if probe["label"] not in ("security improvement", "safe, as before"):
                print(f"      {probe['label']}: {probe['scenario_id']} ({probe['probe']!r})")
        print(f"report: {injection_path.relative_to(config.root).as_posix()}")

    failed = any(entry["verdict"] == FAIL for entry in report["endpoints"].values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
