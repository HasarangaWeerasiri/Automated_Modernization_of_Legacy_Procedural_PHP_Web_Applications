"""The whole pipeline in one command.

    python -m c4 run [--benchmark <app>] [--regenerate] [--recapture] [--down] [--progress json]

In: the benchmark's descriptor (benchmarks/<app>.env), its endpoint map and logins
(inputs/<app>/), and the migrated system the descriptor names. Out: the verdicts, the
injection-safety report, the readable report (reports/<app>/) and the generated test
suite (generated/<app>/).

Each stage does only what is needed, and adds nothing to what the stages decide:

    environment  start both systems, or switch them to this benchmark, and confirm
                 they are ready and have no route out
    scenarios    generate them only if the benchmark has none; they are fixed inputs
    capture      capture only scenarios that have no oracle record, or whose record
                 was observed for another request; the oracle is captured once and the
                 migrated system is then held to it again and again
    suite        generate the standalone test suite
    verdict      replay, compare and judge every scenario and probe; write the reports

--regenerate replaces the scenarios, and so captures everything again. --recapture
captures everything again, as is needed after the engine's capture rules change.
--until capture stops once the oracle is captured, for an application that has no
migrated system yet.
--down stops the systems at the end. --progress json prints one JSON object per line
for a program to read, instead of text.

Exit status: 0 when no endpoint failed, 1 when one did, 2 when the pipeline could not
run. INCONCLUSIVE is not a failure; it is reported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import requests
from docker.errors import NotFound

from c4 import capture, containers, dbstate, harness, scenarios, verdict
from c4.compare import FAIL, INCONCLUSIVE, PASS
from c4.config import LEGACY_DB_CONTAINER, Config, ConfigError, load_config
from c4.replay import ReplayError

Emit = Callable[..., None]


class IsolationError(RuntimeError):
    """A system under validation has a route out of its private network."""


def _relative(config: Config, path: Path) -> str:
    return path.relative_to(config.root).as_posix()


def _environment(config: Config, emit: Emit) -> None:
    client = containers.docker_client()
    try:
        try:
            running = client.containers.get(LEGACY_DB_CONTAINER).labels.get("c4.benchmark", "unknown")
        except NotFound:
            running = None
    finally:
        client.close()

    if running == config.benchmark and all(containers.probe(config).values()):
        action = "already running"
    else:
        if running is not None:
            # Another benchmark, or one started without a label: start afresh.
            containers.down(config, remove_volumes=True)
        containers.up(config)
        action = {None: "started", config.benchmark: "restarted", "unknown": "restarted"}.get(
            running, f"switched from {running}"
        )
    exposed = sorted(name for name, sealed in containers.isolated(config).items() if not sealed)
    if exposed:
        raise IsolationError(f"Outbound network is open for {', '.join(exposed)}; nothing is run.")
    emit("environment", "done", action=action, network="blocked")


def _scenarios(config: Config, regenerate: bool, emit: Emit) -> bool:
    """Generate the scenarios if needed; whether they were generated."""
    existing = sorted(config.scenario_dir.glob("*.json")) if config.scenario_dir.is_dir() else []
    if existing and not regenerate:
        emit("scenarios", "done", action="kept", count=len(existing))
        return False
    endpoint_map = scenarios.load_endpoint_map(config.endpoint_map_file)
    dbstate.reset(config.legacy)
    generated = scenarios.generate(endpoint_map, scenarios.seed_facts(config.legacy))
    scenarios.write_scenarios(config, generated)
    emit("scenarios", "done", action="generated", count=len(generated))
    return True


def _observed_for(record: dict[str, Any], scenario: dict[str, Any]) -> bool:
    """Whether a stored record was observed for exactly this scenario's request."""
    request = scenario["legacy_request"]
    return (
        record.get("endpoint_id") == scenario["endpoint_id"]
        and record.get("legacy_url") == request["path"]
        and record.get("input") == {
            "method": request["method"], "params": request.get("params") or {}, "body": request.get("body"),
        }
    )


def _capture(config: Config, recapture: bool, emit: Emit) -> None:
    resolved = scenarios.load_all(config)
    baselines = {system.name: dbstate.reset(system) for system in config.systems}
    kept = captured = 0
    refused: list[dict[str, str]] = []
    for number, scenario in enumerate(resolved, start=1):
        scenario_id = scenario["scenario_id"]
        stored = config.oracle_dir / f"{scenario_id}.json"
        if not recapture and stored.is_file() and _observed_for(json.loads(stored.read_text(encoding="utf-8")), scenario):
            kept += 1
            emit("capture", "scenario", scenario_id=scenario_id, result="kept", number=number, of=len(resolved))
            continue
        try:
            capture.write_record(config, capture.capture(config, scenario, baselines))
        except capture.CaptureError as error:
            reason = str(error).split(": ", 1)[-1]
            # An earlier record of a scenario capture now refuses is not kept.
            stale = capture.forget(config, scenario_id)
            refused.append({"scenario_id": scenario_id, "reason": reason, **({"stale_record_removed": True} if stale else {})})
            emit("capture", "scenario", scenario_id=scenario_id, result="refused", reason=reason, number=number, of=len(resolved))
            continue
        captured += 1
        emit("capture", "scenario", scenario_id=scenario_id, result="captured", number=number, of=len(resolved))
    for system in config.systems:
        dbstate.ensure_baseline(system, baselines[system.name])
    emit("capture", "done", kept=kept, captured=captured, refused=refused)


def run(
    config: Config, *, regenerate: bool = False, recapture: bool = False, stop: bool = False,
    until: str | None = None, emit: Emit = lambda *args, **kwargs: None,
) -> dict[str, Any]:
    """Run every stage for one benchmark; returns the verdict report and the outputs.

    With until="capture" it stops once the oracle is captured, for an application
    that has no migrated system yet: there is nothing to replay against.
    """
    emit("run", "start", benchmark=config.benchmark)
    _environment(config, emit)
    generated = _scenarios(config, regenerate, emit)
    _capture(config, recapture or generated, emit)
    if until == "capture":
        if stop:
            containers.down(config, remove_volumes=True)
            emit("environment", "stopped")
        outputs = {"oracle records": _relative(config, config.oracle_dir)}
        emit("run", "done", outputs=outputs, until="capture")
        return {"report": None, "injection": None, "outputs": outputs}

    folder = harness.build(config, dbstate.reset(config.migrated))
    tests = (folder / "test_equivalence.py").read_text(encoding="utf-8").count("\ndef test_")
    emit("suite", "done", tests=tests, folder=_relative(config, folder))

    baselines = {system.name: dbstate.reset(system) for system in config.systems}
    report, injection, paths = verdict.produce(config, baselines)
    endpoints = {endpoint: entry["verdict"] for endpoint, entry in report["endpoints"].items()}
    emit("verdict", "done", endpoints=endpoints, probes=injection["summary"])

    if stop:
        containers.down(config, remove_volumes=True)
        emit("environment", "stopped")
    outputs = {name: _relative(config, path) for name, path in paths.items()}
    outputs["test suite"] = _relative(config, folder)
    emit("run", "done", outputs=outputs)
    return {"report": report, "injection": injection, "outputs": outputs}


# ----------------------------------------------------------------------- printing


def _text(stage: str, event: str, **detail: Any) -> None:
    if event == "start":
        print(f"benchmark: {detail['benchmark']}")
    elif stage == "environment" and event == "done":
        print(f"  environment  {detail['action']}, both systems ready, outbound network {detail['network']}")
    elif stage == "environment" and event == "stopped":
        print("  environment  stopped")
    elif stage == "scenarios":
        print(f"  scenarios    {detail['count']} {detail['action']}")
    elif stage == "capture" and event == "scenario" and detail["result"] != "kept":
        print(f"               {detail['result']:<9}{detail['scenario_id']}")
    elif stage == "capture" and event == "done":
        print(f"  capture      {detail['captured']} captured, {detail['kept']} kept, {len(detail['refused'])} refused")
    elif stage == "suite":
        print(f"  suite        {detail['tests']} tests")
    elif stage == "verdict":
        counts = [list(detail["endpoints"].values()).count(v) for v in (PASS, FAIL, INCONCLUSIVE)]
        print(f"  verdict      {len(detail['endpoints'])} endpoints: {counts[0]} PASS, {counts[1]} FAIL, {counts[2]} INCONCLUSIVE")
        for endpoint, result in detail["endpoints"].items():
            print(f"                 {result:<13}{endpoint}")
        if detail["probes"]:
            print("               injection probes: " + ", ".join(f"{n} {label}" for label, n in detail["probes"].items()))
    elif stage == "run" and event == "done":
        if detail.get("until") == "capture":
            print("  stopped after capture: no migrated system to replay against, so no verdict")
        print("outputs:")
        for name, path in detail["outputs"].items():
            print(f"  {name:<18}{path}")


def _json(stage: str, event: str, **detail: Any) -> None:
    print(json.dumps({"stage": stage, "event": event, **detail}, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4", description="Run the C4 validation pipeline.")
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    parser.add_argument("--regenerate", action="store_true", help="replace the scenarios, and capture everything again")
    parser.add_argument("--recapture", action="store_true", help="capture every scenario again")
    parser.add_argument("--down", action="store_true", help="stop both systems at the end")
    parser.add_argument("--until", choices=("capture",), help="stop after capture: for an app with no migrated system yet")
    parser.add_argument("--progress", choices=("text", "json"), default="text", help="how progress is printed")
    args = parser.parse_args(argv)
    emit = _json if args.progress == "json" else _text

    try:
        config = load_config(args.benchmark)
        result = run(
            config, regenerate=args.regenerate, recapture=args.recapture, stop=args.down, until=args.until, emit=emit,
        )
    except (
        ConfigError, IsolationError, ReplayError, harness.HarnessError,
        containers.ComposeError, containers.DockerUnavailableError, containers.NotReadyError,
        scenarios.EndpointMapError, scenarios.ScenarioError,
        dbstate.BaselineError, dbstate.ResetError, dbstate.StructureChangedError,
        requests.RequestException,
    ) as error:
        if args.progress == "json":
            _json("run", "error", message=str(error))
        else:
            print(error, file=sys.stderr)
        return 2

    report = result["report"] or {"endpoints": {}}  # no report when the run stopped after capture
    failed = any(entry["verdict"] == FAIL for entry in report["endpoints"].values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
