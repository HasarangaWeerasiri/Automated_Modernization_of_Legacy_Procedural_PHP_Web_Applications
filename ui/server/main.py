"""Group UI server (FastAPI): runs a component's command on an app, streams its log, serves its outputs.

The contract every component follows (ui/README.md):
    <command> --app <app> --out <folder>
      exit 0 = ok, 1 = failed; stdout = log lines ("::step <name>" starts a step); <folder>/summary.json.
Components and apps are listed in ui/config.json; only listed apps can be run (no evaluation app).

Run, from the repository root:
    ui/.venv/Scripts/python.exe -m uvicorn main:app --app-dir ui/server --host 127.0.0.1 --port 8000
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

UI = Path(__file__).resolve().parents[1]  # ui/
ROOT = UI.parent  # the repository
RUNS = UI / "runs"
DB = RUNS / "runs.db"
WEB = UI / "web" / "dist"
MAX_FILE = 2_000_000


def config() -> dict:
    return json.loads((UI / "config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- run records (SQLite)


def db() -> sqlite3.Connection:
    RUNS.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""create table if not exists runs (id text primary key, component text, app text, options text,
                   status text, started real, finished real, exit_code integer)""")
    return con


def record(run_id: str) -> dict:
    with db() as con:
        row = con.execute("select * from runs where id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such run")
    run = dict(row) | {"options": json.loads(row["options"])}
    summary = RUNS / run_id / "out" / "summary.json"
    try:
        run["summary"] = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None
    except (OSError, json.JSONDecodeError):  # being written
        run["summary"] = None
    return run


def status(run_id: str) -> str:
    with db() as con:
        row = con.execute("select status from runs where id = ?", (run_id,)).fetchone()
    return row["status"] if row else "failed"


def expand(items: list, values: dict) -> list[str]:
    """A command with {placeholders}; an item that is exactly a list-valued placeholder expands to its items."""
    out = []
    for item in items:
        key = item[1:-1] if item.startswith("{") and item.endswith("}") else None
        if key is not None and isinstance(values.get(key), list):
            out.extend(values[key])
        else:
            out.append(item.format(**{k: v for k, v in values.items() if not isinstance(v, list)}))
    return out


def executable(command: list[str]) -> list[str]:
    """A repository-relative program path made absolute (a bare name like `node` stays as it is)."""
    first = ROOT / command[0]
    return [str(first) if first.exists() else command[0]] + command[1:]


# ---------------------------------------------------------------- running a component

ACTIVE: dict[str, subprocess.Popen] = {}


def run_component(run_id: str, command: list[str], cwd: Path) -> None:
    folder = RUNS / run_id
    env = os.environ | {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    with (folder / "log.txt").open("w", encoding="utf-8", newline="\n") as log:
        try:
            proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", env=env)
        except OSError as error:
            log.write(f"could not start: {error}\n")
            code = -1
        else:
            ACTIVE[run_id] = proc
            for line in proc.stdout:
                log.write(line)
                log.flush()
            code = proc.wait()
            ACTIVE.pop(run_id, None)
    with db() as con:
        con.execute("update runs set status = ?, finished = ?, exit_code = ? where id = ?",
                    ("ok" if code == 0 else "failed", time.time(), code, run_id))


# ---------------------------------------------------------------- serving a run's result (e.g. the Next.js app)

SERVED: dict = {"run": None, "url": None, "procs": []}


def stop_served() -> None:
    for proc in SERVED["procs"]:
        if proc.poll() is None:
            if os.name == "nt":  # the whole tree: next start has workers
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                proc.terminate()
    SERVED.update(run=None, url=None, procs=[])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with db() as con:  # a run cut off by a restart is not running any more
        con.execute("update runs set status = 'failed' where status = 'running'")
    yield
    stop_served()


app = FastAPI(title="Legacy PHP Modernization — group UI", lifespan=lifespan)


class RunRequest(BaseModel):
    component: str
    app: str
    options: dict[str, bool] = {}


@app.get("/api/config")
def get_config() -> dict:
    return config()


@app.get("/api/runs")
def list_runs() -> list[dict]:
    with db() as con:
        rows = con.execute("select * from runs order by started desc limit 50").fetchall()
    return [dict(r) | {"options": json.loads(r["options"])} for r in rows]


@app.post("/api/runs")
def start_run(request: RunRequest) -> dict:
    settings = config()
    component = settings["components"].get(request.component)
    if component is None or not component.get("connected"):
        raise HTTPException(400, f"{request.component}: not a connected component")
    if request.app not in component["apps"] or request.app not in settings["apps"]:
        raise HTTPException(400, f"{request.app}: not an app this component may run")
    if ACTIVE:
        raise HTTPException(409, "another run is in progress")
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    folder = RUNS / run_id
    (folder / "out").mkdir(parents=True)
    command = expand(component["command"], {"app": request.app, "out": str(folder / "out")})
    for option in component.get("options", []):
        if request.options.get(option["id"], option.get("default", False)):
            command.append(option["flag"])
    options = {o["id"]: request.options.get(o["id"], o.get("default", False)) for o in component.get("options", [])}
    with db() as con:
        con.execute("insert into runs values (?, ?, ?, ?, 'running', ?, null, null)",
                    (run_id, request.component, request.app, json.dumps(options), time.time()))
    if SERVED["run"] is not None and record(SERVED["run"])["app"] == request.app:
        stop_served()  # the build it serves is about to be replaced
    threading.Thread(target=run_component, args=(run_id, executable(command), ROOT / component["cwd"]),
                     daemon=True).start()
    return {"id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return record(run_id)


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: every log line as it is written (its number as the event id, so a reconnecting browser
    resumes after the last line it has), then the run's end."""
    record(run_id)
    log = RUNS / run_id / "log.txt"
    last = request.headers.get("last-event-id", "")
    resume = int(last) + 1 if last.isdigit() else 0

    async def stream():
        position, buffer, number = 0, "", 0
        while True:
            if log.exists():
                with log.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(position)
                    chunk = f.read()
                    position = f.tell()
                buffer += chunk
                *lines, buffer = buffer.split("\n")
                for line in lines:
                    if number >= resume:
                        yield f"id: {number}\ndata: {json.dumps(line)}\n\n"
                    number += 1
            state = status(run_id)
            if state != "running" and run_id not in ACTIVE:
                if buffer and number >= resume:
                    yield f"id: {number}\ndata: {json.dumps(buffer)}\n\n"
                yield f"event: end\ndata: {json.dumps(state)}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def inside(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise HTTPException(404, "no such file")
    if target.stat().st_size > MAX_FILE:
        raise HTTPException(413, "file too large to show")
    return target


@app.get("/api/runs/{run_id}/file", response_class=PlainTextResponse)
def run_file(run_id: str, path: str = Query(...)) -> str:
    """A file the run wrote (its output folder only)."""
    record(run_id)
    return inside(RUNS / run_id / "out", path).read_text(encoding="utf-8", errors="replace")


@app.get("/api/runs/{run_id}/source", response_class=PlainTextResponse)
def run_source(run_id: str, path: str = Query(...)) -> str:
    """A legacy source file of the run's app (the component's sourceRoot only)."""
    run = record(run_id)
    root = config()["components"][run["component"]].get("sourceRoot")
    if not root:
        raise HTTPException(404, "this component shows no source")
    return inside(ROOT / root.format(app=run["app"]), path).read_text(encoding="utf-8", errors="replace")


@app.post("/api/runs/{run_id}/serve")
def serve(run_id: str) -> dict:
    """Start what the component's `serve` block names for this run (e.g. the generated Next.js app)."""
    run = record(run_id)
    if run["status"] != "ok":
        raise HTTPException(400, "only a finished, successful run can be served")
    if ACTIVE:
        raise HTTPException(409, "a run is in progress")
    block = config()["components"][run["component"]].get("serve")
    if not block:
        raise HTTPException(400, "this component serves nothing")
    stop_served()
    values = {"app": run["app"], "replayArgs": block.get("replayArgs", {}).get(run["app"], [])}
    logs = RUNS / run_id / "serve.log"
    with logs.open("w", encoding="utf-8") as log:
        for proc in block["processes"]:
            when = proc.get("when")  # what the run actually did (its summary), else what was asked
            if when and not (run["summary"] or {}).get(when, run["options"].get(when)):
                continue
            env = os.environ | {"PYTHONUTF8": "1"} | proc.get("env", {})
            SERVED["procs"].append(subprocess.Popen(executable(expand(proc["command"], values)),
                                                    cwd=ROOT / proc["cwd"], stdout=log, stderr=subprocess.STDOUT,
                                                    env=env))
    SERVED.update(run=run_id, url=block["url"])
    return {"run": run_id, "url": block["url"]}


@app.get("/api/serve")
def served() -> dict:
    alive = [p.poll() is None for p in SERVED["procs"]]
    return {"run": SERVED["run"], "url": SERVED["url"], "running": bool(alive) and all(alive)}


@app.post("/api/serve/stop")
def serve_stop() -> dict:
    stop_served()
    return {"stopped": True}


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(WEB / "index.html")
