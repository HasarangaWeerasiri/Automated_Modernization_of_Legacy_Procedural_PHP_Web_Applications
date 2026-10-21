"""The dashboard server, against a stand-in engine in a temporary folder.

    dashboard\\.venv\\Scripts\\python -m pytest dashboard\\tests
"""

import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parents[1]

# A stand-in for `python -m c4 run --progress json`: it reports the stages the way the
# engine does, says where it was told to write, and writes one report there.
FAKE_ENGINE = '''
import json, os, sys, time
args = sys.argv[1:]
benchmark = args[args.index("--benchmark") + 1]
out = os.environ["C4_OUTPUT_DIR"]
def emit(**event):
    print(json.dumps(event), flush=True)
emit(stage="run", event="start", benchmark=benchmark, args=args)
emit(stage="environment", event="done", action="already running", network="blocked")
time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
emit(stage="capture", event="done", kept=1, captured=0, refused=[], output_dir=out)
reports = os.path.join(out, "reports", benchmark)
os.makedirs(reports, exist_ok=True)
with open(os.path.join(reports, "verdicts.json"), "w") as f:
    json.dump({"benchmark": benchmark, "endpoints": {}}, f)
emit(stage="run", event="done", outputs={})
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text("# Benchmark descriptor: Demo Shop (see PINS.md).\nC4_LEGACY_DB_NAME=demo\n")
    (tmp_path / "benchmarks" / "nomap.env").write_text("C4_LEGACY_DB_NAME=x\n")
    inputs = tmp_path / "inputs" / "demo"
    (inputs / "scenarios").mkdir(parents=True)
    (inputs / "endpoint_map.json").write_text('{"format": "c4-stand-in/1", "endpoints": []}')
    (inputs / "scenarios" / "s_01.json").write_text("{}")
    (tmp_path / "oracles" / "demo").mkdir(parents=True)
    (tmp_path / "oracles" / "demo" / "s_01.json").write_text('{"scenario_id": "s_01"}')
    (tmp_path / "reports" / "demo").mkdir(parents=True)
    (tmp_path / "reports" / "demo" / "verdicts.json").write_text('{"endpoints": {"GET /a": {"verdict": "PASS", "scenarios": []}}}')
    package = tmp_path / "c4"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(FAKE_ENGINE)
    monkeypatch.setenv("C4_ROOT", str(tmp_path))
    monkeypatch.setenv("C4_DASHBOARD_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("C4_ENGINE_PYTHON", sys.executable)
    return tmp_path


@pytest.fixture
def server(root, monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    import server as module
    module = importlib.reload(module)  # read C4_ROOT and the other settings again
    calls = []
    monkeypatch.setattr(module, "_fault_switch", lambda method, path="": calls.append((method, path)) or {"active": path.strip("/") or None})
    module.fault_calls = calls
    return module


@pytest.fixture
def client(server):
    return TestClient(server.app)


def wait_for(client, run_id):
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["finished"]:
            return run
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_only_benchmarks_with_an_endpoint_map_are_offered(client):
    (benchmark,) = client.get("/api/benchmarks").json()

    assert benchmark == {"id": "demo", "title": "Demo Shop", "scenarios": 1, "oracle_records": 1, "has_results": True,
                         "migrated": False, "default": False}


def test_the_evidence_kept_in_git_is_shown_without_a_run(client):
    results = client.get("/api/results/committed/demo").json()

    assert results["verdicts"]["endpoints"]["GET /a"]["verdict"] == "PASS"
    assert (results["scenarios"], results["oracle_records"]) == (["s_01"], ["s_01"])


@pytest.mark.parametrize("path", ["benchmarks/demo.env", "inputs/demo/endpoint_map.json", "../../etc/passwd", "reports/../c4/__main__.py"])
def test_only_files_the_engine_wrote_can_be_read(client, path):
    assert client.get("/api/results/committed/file", params={"path": path}).status_code == 404


def test_a_run_writes_into_its_own_folder_and_leaves_the_evidence_alone(client, root):
    run = client.post("/api/runs", json={"benchmark": "demo"}).json()
    finished = wait_for(client, run["id"])

    folder = root / "runs" / run["id"]
    (capture,) = [e for e in finished["events"] if e["stage"] == "capture"]
    assert Path(capture["output_dir"]) == folder
    assert (folder / "oracles" / "demo" / "s_01.json").is_file()  # the kept records, copied
    assert (folder / "reports" / "demo" / "verdicts.json").is_file()
    assert json.loads((root / "reports" / "demo" / "verdicts.json").read_text())["endpoints"]["GET /a"]["verdict"] == "PASS"
    assert finished["exit_code"] == 0
    assert client.get(f"/api/results/{run['id']}/demo").json()["verdicts"]["endpoints"] == {}


def test_one_run_at_a_time(client, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "1")
    first = client.post("/api/runs", json={"benchmark": "demo"}).json()

    assert client.post("/api/runs", json={"benchmark": "demo"}).status_code == 409
    wait_for(client, first["id"])


def test_a_planted_fault_goes_on_after_the_environment_is_up_and_off_at_the_end(client, server, root):
    with (root / "benchmarks" / "demo.env").open("a") as descriptor:
        descriptor.write("C4_MIGRATED_APP=./stubs/demo\n")  # a fault needs a migrated system to plant it in
    run = client.post("/api/runs", json={"benchmark": "demo", "fault": "drops_last_record"}).json()
    finished = wait_for(client, run["id"])

    assert server.fault_calls == [("PUT", "/drops_last_record"), ("DELETE", "")]
    stages = [(e["stage"], e["event"]) for e in finished["events"]]
    assert stages.index(("environment", "done")) < stages.index(("fault", "planted")) < stages.index(("fault", "cleared"))


def test_the_run_history_and_its_events_survive_the_run(client):
    run = client.post("/api/runs", json={"benchmark": "demo"}).json()
    wait_for(client, run["id"])

    assert client.get("/api/runs").json()[0]["id"] == run["id"]
    with client.stream("GET", f"/api/runs/{run['id']}/events") as stream:
        text = "".join(stream.iter_text())
    assert '"stage": "run", "event": "start"' in text and "event: end" in text


def test_an_unknown_benchmark_or_run_is_refused(client):
    assert client.post("/api/runs", json={"benchmark": "nomap"}).status_code == 404
    assert client.get("/api/runs/../../etc").status_code == 404
    assert client.get("/api/results/20990101-000000-demo/demo").status_code == 404


def test_the_page_is_served(client):
    assert "<title>Validation</title>" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def started_with(client, **request):
    run = client.post("/api/runs", json={"benchmark": "demo", **request}).json()
    finished = wait_for(client, run["id"])
    return finished, next(e for e in finished["events"] if e["stage"] == "run" and e["event"] == "start")["args"]


def test_a_benchmark_without_a_migrated_system_runs_capture_only(client):
    finished, args = started_with(client, fault="drops_last_record")

    assert finished["capture_only"] is True and finished["fault"] is None
    assert args[-2:] == ["--until", "capture"]


def test_a_benchmark_with_a_migrated_system_runs_the_whole_pipeline_unless_asked(client, root):
    with (root / "benchmarks" / "demo.env").open("a") as descriptor:
        descriptor.write("C4_MIGRATED_APP=./stubs/demo\n")

    whole, whole_args = started_with(client)
    only, only_args = started_with(client, capture_only=True)

    assert whole["capture_only"] is False and "--until" not in whole_args
    assert only["capture_only"] is True and only_args[-2:] == ["--until", "capture"]


def test_generating_again_is_passed_to_the_engine(client):
    _, args = started_with(client, regenerate=True)

    assert "--regenerate" in args


def test_the_scenarios_are_counted_page_by_page(client, root):
    folder = root / "inputs" / "demo" / "scenarios"
    for name, kind, case in (("a_typical_01", "typical", "typical"), ("a_not_found_01", "typical", "not_found"),
                             ("a_injection_q_01", "injection", "probe")):
        (folder / f"{name}.json").write_text(json.dumps({"source_id": "a", "scenario_type": kind, "case": case}))

    counts = client.get("/api/benchmarks/demo/scenarios").json()

    page = next(p for p in counts["pages"] if p["source_id"] == "a")
    assert (page["typical"], page["not_found"], page["injection"], page["total"]) == (1, 1, 1, 3)


def test_two_runs_started_in_the_same_second_get_folders_of_their_own(client, server, monkeypatch):
    class Frozen:
        @staticmethod
        def now():
            from datetime import datetime as real
            return real(2026, 10, 20, 12, 0, 0)

    monkeypatch.setattr(server, "datetime", Frozen)
    first = client.post("/api/runs", json={"benchmark": "demo"}).json()
    wait_for(client, first["id"])
    second = client.post("/api/runs", json={"benchmark": "demo"}).json()
    wait_for(client, second["id"])

    assert (first["id"], second["id"]) == ("20261020-120000-demo", "20261020-120000-demo-2")


def test_every_oracle_record_can_be_downloaded_in_one_zip(client):
    import io
    import zipfile

    response = client.get("/api/results/committed/demo/oracles.zip")

    assert response.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == ["oracles/demo/s_01.json"]


def test_the_benchmark_this_machine_selects_is_marked_as_the_default(client, root):
    (root / ".env").write_text("C4_BENCHMARK=demo\n")

    (benchmark,) = client.get("/api/benchmarks").json()

    assert benchmark["default"] is True
