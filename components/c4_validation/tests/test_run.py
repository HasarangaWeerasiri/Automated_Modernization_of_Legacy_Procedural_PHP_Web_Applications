"""The run command: which stages do what, checked without a running system."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

import c4.__main__ as run_module
from c4.compare import FAIL, INCONCLUSIVE, PASS
from c4.config import load_config


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    return load_config("demo", environ={}, root=tmp_path)


def scenario(number, path="/a.php"):
    return {
        "scenario_id": f"s_{number:02d}", "endpoint_id": "GET /api/a",
        "legacy_request": {"method": "GET", "path": path, "params": {"n": number}, "body": None},
    }


def record_for(item):
    request = item["legacy_request"]
    return {"scenario_id": item["scenario_id"], "endpoint_id": item["endpoint_id"], "legacy_url": request["path"],
            "input": {"method": "GET", "params": request["params"], "body": None}}


@pytest.fixture
def stages(config, monkeypatch):
    """Every stage replaced by a recorder; returns what each was asked to do."""
    seen = SimpleNamespace(captured=[], generated=False, verdicts={"GET /api/a": PASS}, written=[], stopped=False)
    planned = [scenario(1), scenario(2)]

    monkeypatch.setattr(run_module, "_environment", lambda config, emit: emit("environment", "done", action="started", network="blocked"))
    monkeypatch.setattr(run_module.dbstate, "reset", lambda system: SimpleNamespace(name=system.name))
    monkeypatch.setattr(run_module.dbstate, "ensure_baseline", lambda system, baseline: baseline)
    monkeypatch.setattr(run_module.scenarios, "load_endpoint_map", lambda path: {})
    monkeypatch.setattr(run_module.scenarios, "seed_facts", lambda system: None)
    monkeypatch.setattr(run_module.scenarios, "generate", lambda endpoint_map, seed: [s for s in planned])

    def write_scenarios(config, generated):
        seen.generated = True
        config.scenario_dir.mkdir(parents=True, exist_ok=True)
        for item in generated:
            (config.scenario_dir / f"{item['scenario_id']}.json").write_text("{}")

    monkeypatch.setattr(run_module.scenarios, "write_scenarios", write_scenarios)
    monkeypatch.setattr(run_module.scenarios, "load_all", lambda config: planned)

    def capture(config, item, baselines):
        seen.captured.append(item["scenario_id"])
        if item.get("refuse"):
            raise run_module.capture.CaptureError(f"{item['scenario_id']}: the page shows neither a table nor a message.")
        return record_for(item)

    def write_record(config, record):
        config.oracle_dir.mkdir(parents=True, exist_ok=True)
        (config.oracle_dir / f"{record['scenario_id']}.json").write_text(json.dumps(record))
        seen.written.append(record["scenario_id"])

    monkeypatch.setattr(run_module.capture, "capture", capture)
    monkeypatch.setattr(run_module.capture, "write_record", write_record)

    def build(config, baseline):
        config.generated_dir.mkdir(parents=True, exist_ok=True)
        (config.generated_dir / "test_equivalence.py").write_text("x\ndef test_a():\n    pass\n")
        return config.generated_dir

    monkeypatch.setattr(run_module.harness, "build", build)

    def produce(config, baselines):
        report = {"endpoints": {e: {"verdict": v} for e, v in seen.verdicts.items()}}
        paths = {name: config.report_dir / file for name, file in (
            ("verdicts", "verdicts.json"), ("injection safety", "injection_safety.json"), ("readable report", "differential_report.md"))}
        return report, {"summary": {}}, paths

    monkeypatch.setattr(run_module.verdict, "produce", produce)
    monkeypatch.setattr(run_module.containers, "down", lambda config, remove_volumes=False: setattr(seen, "stopped", True))
    seen.planned = planned
    return seen


# ------------------------------------------------------------------- the stages


def test_a_first_run_generates_the_scenarios_and_captures_them_all(config, stages):
    result = run_module.run(config)

    assert stages.generated
    assert stages.captured == ["s_01", "s_02"]
    assert result["outputs"] == {
        "verdicts": "reports/demo/verdicts.json",
        "injection safety": "reports/demo/injection_safety.json",
        "readable report": "reports/demo/differential_report.md",
        "test suite": "generated/demo",
    }


def test_a_later_run_keeps_the_scenarios_and_the_oracle_and_only_judges_again(config, stages):
    run_module.run(config)
    stages.captured.clear()
    stages.generated = False

    run_module.run(config)

    assert not stages.generated
    assert stages.captured == []


def test_a_scenario_without_a_record_or_whose_request_changed_is_captured(config, stages):
    run_module.run(config)
    stages.captured.clear()
    (config.oracle_dir / "s_01.json").unlink()
    stages.planned[1]["legacy_request"]["params"] = {"n": 99}

    run_module.run(config)

    assert stages.captured == ["s_01", "s_02"]


def test_recapture_and_regenerate_capture_everything_again(config, stages):
    run_module.run(config)
    for options in ({"recapture": True}, {"regenerate": True}):
        stages.captured.clear()
        run_module.run(config, **options)
        assert stages.captured == ["s_01", "s_02"], options


def test_a_refused_scenario_is_reported_and_the_run_goes_on(config, stages):
    stages.planned[0]["refuse"] = True
    events = []

    run_module.run(config, emit=lambda stage, event, **detail: events.append((stage, event, detail)))

    (done,) = [detail for stage, event, detail in events if (stage, event) == ("capture", "done")]
    assert (done["captured"], done["refused"][0]["scenario_id"]) == (1, "s_01")
    assert "neither a table nor a message" in done["refused"][0]["reason"]
    assert ("verdict", "done") in [(stage, event) for stage, event, _ in events]


def test_down_stops_the_systems_at_the_end(config, stages):
    run_module.run(config, stop=True)

    assert stages.stopped


# ------------------------------------------------------------- what it returns


@pytest.mark.parametrize(("verdicts", "status"), [
    ({"GET /api/a": PASS}, 0),
    ({"GET /api/a": INCONCLUSIVE}, 0),
    ({"GET /api/a": PASS, "GET /api/b": FAIL}, 1),
])
def test_the_exit_status_says_whether_an_endpoint_failed(config, stages, monkeypatch, verdicts, status):
    stages.verdicts = verdicts
    monkeypatch.setattr(run_module, "load_config", lambda benchmark: config)

    assert run_module.main(["run"]) == status


def test_a_pipeline_that_cannot_run_exits_with_2(monkeypatch, capsys):
    def broken(benchmark):
        raise run_module.ConfigError("No benchmark selected.")

    monkeypatch.setattr(run_module, "load_config", broken)

    assert run_module.main(["run"]) == 2
    assert "No benchmark selected." in capsys.readouterr().err


def test_json_progress_is_one_object_per_line(config, stages, monkeypatch, capsys):
    monkeypatch.setattr(run_module, "load_config", lambda benchmark: config)

    run_module.main(["run", "--progress", "json"])

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0] == {"stage": "run", "event": "start", "benchmark": "demo"}
    assert events[-1]["stage"] == "run" and events[-1]["event"] == "done"
    assert [e["stage"] for e in events if e["event"] == "done"] == ["environment", "scenarios", "capture", "suite", "verdict", "run"]


# --------------------------------------------------------------- the environment


class FakeClient:
    def __init__(self, label):
        self.label = label
        self.containers = self

    def get(self, name):
        if self.label is None:
            raise NotFound("no such container")
        return SimpleNamespace(labels={"c4.benchmark": self.label})

    def close(self):
        pass


@pytest.fixture
def environment(config, monkeypatch):
    calls = []
    monkeypatch.setattr(run_module.containers, "up", lambda config: calls.append("up"))
    monkeypatch.setattr(run_module.containers, "down", lambda config, remove_volumes=False: calls.append("down"))
    monkeypatch.setattr(run_module.containers, "probe", lambda config: {"c4-legacy-db": True})
    monkeypatch.setattr(run_module.containers, "isolated", lambda config: {"c4-legacy-db": True})

    def start(label):
        monkeypatch.setattr(run_module.containers, "docker_client", lambda: FakeClient(label))
        events = []
        run_module._environment(config, lambda stage, event, **detail: events.append(detail))
        return calls, events[-1]["action"]

    return start


def test_the_systems_already_running_for_this_benchmark_are_used_as_they_are(environment):
    assert environment("demo") == ([], "already running")


def test_no_systems_running_are_started(environment):
    assert environment(None) == (["up"], "started")


def test_systems_running_for_another_benchmark_are_switched(environment):
    assert environment("hms") == (["down", "up"], "switched from hms")


def test_a_system_with_a_route_out_stops_the_run(environment, monkeypatch):
    monkeypatch.setattr(run_module.containers, "isolated", lambda config: {"c4-legacy-web": False})

    with pytest.raises(run_module.IsolationError, match="c4-legacy-web"):
        environment("demo")
