"""C4 validation dashboard: a temporary, local window onto the validation engine.

It holds no validation logic. It starts the engine's own command as a separate
process (`python -m c4 run --progress json`), streams that command's progress to the
browser, and shows the files the engine wrote. Every number on a screen is read from
one of those files.

Each run writes into its own folder, dashboard/runs/<id>/, through the engine's
C4_OUTPUT_DIR setting, so a demonstration never touches the evidence kept in git
(oracles/, reports/, generated/). The benchmark's oracle records are copied into the
run folder first, so a run only captures what has no record yet.

Planting a fault is a demonstration of fault injection: the stub API standing in for
the migrated system has a fault switch, which the dashboard turns on once the run's
environment is up and off again when the run ends.

    dashboard\\start.ps1      then open http://127.0.0.1:8800

Local only: it listens on 127.0.0.1 and has no login. Deleting the dashboard folder
leaves the engine exactly as it was; the engine never imports anything from here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("C4_ROOT", HERE.parent)).resolve()  # the c4_validation folder
RUNS = Path(os.environ.get("C4_DASHBOARD_RUNS", HERE / "runs")).resolve()
WEB = HERE / "web"
# The names of the containers the engine starts (see docker-compose.yml).
CONTAINERS = ("c4-gateway", "c4-legacy-db", "c4-legacy-web", "c4-migrated-db", "c4-migrated-api")
# The id of the results kept in git, shown when no run is chosen.
COMMITTED = "committed"

app = FastAPI(title="C4 validation dashboard")


def engine_command() -> list[str]:
    """How to start the engine: its own virtual environment, never this one."""
    override = os.environ.get("C4_ENGINE_PYTHON")
    if override:
        return [override]
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return [str(candidate)]
    return [sys.executable]


def _settings() -> dict[str, str]:
    """The engine's machine-local settings, read the way it reads them (KEY=value lines)."""
    found: dict[str, str] = {}
    local = ROOT / ".env"
    if local.is_file():
        for line in local.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def migrated_url() -> str:
    port = os.environ.get("C4_MIGRATED_API_PORT") or _settings().get("C4_MIGRATED_API_PORT") or "8000"
    return f"http://127.0.0.1:{port}"


# ------------------------------------------------------------------ benchmarks


def benchmarks() -> list[dict[str, Any]]:
    """Every benchmark that has a descriptor and an endpoint map."""
    found = []
    for descriptor in sorted((ROOT / "benchmarks").glob("*.env")):
        name = descriptor.stem
        inputs = ROOT / "inputs" / name
        if not (inputs / "endpoint_map.json").is_file():
            continue
        title = next(
            (line.split(":", 1)[1].strip().split(" (")[0] for line in descriptor.read_text(encoding="utf-8").splitlines()
             if line.startswith("# Benchmark descriptor:")),
            name,
        )
        found.append({
            "id": name,
            "title": title,
            "scenarios": len(list((inputs / "scenarios").glob("*.json"))),
            "oracle_records": len(list((ROOT / "oracles" / name).glob("*.json"))),
            "has_results": (ROOT / "reports" / name / "verdicts.json").is_file(),
        })
    return found


def _known(benchmark: str) -> None:
    if benchmark not in {item["id"] for item in benchmarks()}:
        raise HTTPException(404, f"No benchmark '{benchmark}'.")


@app.get("/api/benchmarks")
def list_benchmarks() -> list[dict[str, Any]]:
    return benchmarks()


@app.get("/api/benchmarks/{benchmark}/endpoint-map")
def endpoint_map(benchmark: str) -> Any:
    _known(benchmark)
    return json.loads((ROOT / "inputs" / benchmark / "endpoint_map.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ environment


def _docker(*args: str) -> str | None:
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


@app.get("/api/status")
def status() -> dict[str, Any]:
    """Which benchmark the containers run, and the state of each, as Docker reports it."""
    if _docker("info", "--format", "{{.ServerVersion}}") is None:
        return {"docker": False, "running": None, "containers": []}
    containers = []
    running = None
    for name in CONTAINERS:
        raw = _docker("inspect", name)
        if not raw:
            containers.append({"name": name, "state": "absent", "health": None})
            continue
        info = json.loads(raw)[0]
        state = info["State"]
        containers.append({"name": name, "state": state.get("Status"), "health": (state.get("Health") or {}).get("Status")})
        if name == "c4-legacy-db":
            running = (info["Config"].get("Labels") or {}).get("c4.benchmark")
    return {"docker": True, "running": running, "containers": containers}


def _fault_switch(method: str, path: str = "") -> dict[str, Any] | None:
    request = urllib.request.Request(migrated_url() + "/_fault" + path, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


@app.get("/api/faults")
def faults() -> dict[str, Any]:
    """The faults the running stub API can plant; empty when no stub answers."""
    found = _fault_switch("GET")
    return {"running": status()["running"], "active": (found or {}).get("active"), "available": (found or {}).get("available", [])}


# ------------------------------------------------------------------------ runs


class RunRequest(BaseModel):
    benchmark: str
    fault: str | None = None
    recapture: bool = False


class Run:
    """One run of the engine, its progress kept as it arrives."""

    def __init__(self, run_id: str, request: RunRequest):
        self.id = run_id
        self.folder = RUNS / run_id
        self.request = request
        self.events: list[dict[str, Any]] = []
        self.finished = False
        self.exit_code: int | None = None
        self.started = datetime.now().isoformat(timespec="seconds")
        self.lock = threading.Lock()

    def add(self, event: dict[str, Any]) -> None:
        event = {"at": datetime.now().strftime("%H:%M:%S"), **event}
        with self.lock:
            self.events.append(event)
        with (self.folder / "events.jsonl").open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "benchmark": self.request.benchmark, "fault": self.request.fault,
            "started": self.started, "finished": self.finished, "exit_code": self.exit_code,
        }


current: Run | None = None
current_lock = threading.Lock()


def _write_summary(run: Run) -> None:
    target = run.folder / "run.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(run.summary(), indent=2), encoding="utf-8")
    temporary.replace(target)  # the screens never read half a file


def _drive(run: Run) -> None:
    """Run the engine and record what it says; plant and clear the fault around it."""
    benchmark = run.request.benchmark
    command = [*engine_command(), "-m", "c4", "run", "--benchmark", benchmark, "--progress", "json"]
    if run.request.recapture:
        command.append("--recapture")
    environment = {**os.environ, "C4_OUTPUT_DIR": str(run.folder), "PYTHONUNBUFFERED": "1"}
    planted = False
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = {"stage": "log", "event": "line", "message": line}
            run.add(event)
            # The fault goes on once the environment is up, so a restart cannot lose it,
            # and before the verdict stage replays anything.
            if run.request.fault and not planted and (event.get("stage"), event.get("event")) == ("environment", "done"):
                answer = _fault_switch("PUT", f"/{run.request.fault}")
                planted = bool(answer and answer.get("active") == run.request.fault)
                run.add({"stage": "fault", "event": "planted" if planted else "not planted", "fault": run.request.fault})
        run.exit_code = process.wait()
    except OSError as error:
        run.add({"stage": "run", "event": "error", "message": f"The engine could not be started: {error}"})
        run.exit_code = 2
    finally:
        if planted:
            _fault_switch("DELETE")
            run.add({"stage": "fault", "event": "cleared", "fault": run.request.fault})
        run.finished = True
        _write_summary(run)


@app.post("/api/runs", status_code=201)
def start_run(request: RunRequest) -> dict[str, Any]:
    global current
    _known(request.benchmark)
    with current_lock:
        if current is not None and not current.finished:
            raise HTTPException(409, "A run is in progress; one run at a time.")
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{request.benchmark}"
        run = Run(run_id, request)
        run.folder.mkdir(parents=True, exist_ok=True)
        committed = ROOT / "oracles" / request.benchmark
        if committed.is_dir() and not request.recapture:
            shutil.copytree(committed, run.folder / "oracles" / request.benchmark)
        _write_summary(run)
        current = run
    threading.Thread(target=_drive, args=(run,), daemon=True).start()
    return run.summary()


def _stored(run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[A-Za-z0-9_-]+", run_id):
        raise HTTPException(404, "No such run.")
    if current is not None and current.id == run_id:
        return {**current.summary(), "events": list(current.events)}
    folder = RUNS / run_id
    if not (folder / "run.json").is_file():
        raise HTTPException(404, "No such run.")
    summary = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    events_file = folder / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line] if events_file.is_file() else []
    return {**summary, "events": events}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    found = []
    for folder in sorted(RUNS.glob("*/run.json"), reverse=True):
        try:
            found.append(json.loads(folder.read_text(encoding="utf-8")))
        except ValueError:
            continue
    if current is not None and not current.finished and all(item["id"] != current.id for item in found):
        found.insert(0, current.summary())
    return found


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _stored(run_id)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """The run's progress as Server-Sent Events: what has happened so far, then live."""
    _stored(run_id)

    async def stream():
        sent = 0
        while True:
            run = current if current is not None and current.id == run_id else None
            events = run.events if run else _stored(run_id)["events"]
            while sent < len(events):
                yield f"data: {json.dumps(events[sent], ensure_ascii=False)}\n\n"
                sent += 1
            if run is None or run.finished:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ----------------------------------------------------------------------- files


def _folder(source: str) -> Path:
    """Where a set of results lives: the evidence kept in git, or one run's folder."""
    if source == COMMITTED:
        return ROOT
    _stored(source)
    return RUNS / source


@app.get("/api/results/{source}/file")
def result_file(source: str, path: str) -> FileResponse:
    """One file the engine wrote, from the results' own folders only."""
    base = _folder(source)
    target = (base / path).resolve()
    allowed = [(base / name).resolve() for name in ("reports", "oracles", "generated")]
    if not any(target == folder or folder in target.parents for folder in allowed) or not target.is_file():
        raise HTTPException(404, "No such file.")
    media = "application/json" if target.suffix == ".json" else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media)


@app.get("/api/results/{source}/{benchmark}")
def results(source: str, benchmark: str) -> dict[str, Any]:
    """The three reports and the list of oracle records and suite files, for one benchmark."""
    _known(benchmark)
    base = _folder(source)

    def read(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    suite = base / "generated" / benchmark
    tests = suite / "test_equivalence.py"
    return {
        "source": source,
        "benchmark": benchmark,
        "verdicts": read(base / "reports" / benchmark / "verdicts.json"),
        "injection": read(base / "reports" / benchmark / "injection_safety.json"),
        "has_readable_report": (base / "reports" / benchmark / "differential_report.md").is_file(),
        "oracle_records": sorted(p.stem for p in (base / "oracles" / benchmark).glob("*.json")),
        "scenarios": sorted(p.stem for p in (ROOT / "inputs" / benchmark / "scenarios").glob("*.json")),
        "suite_files": sorted(p.name for p in suite.glob("*") if p.is_file()) if suite.is_dir() else [],
        "suite_tests": tests.read_text(encoding="utf-8").count("\ndef test_") if tests.is_file() else 0,
    }


# ---------------------------------------------------------------------- the page


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
