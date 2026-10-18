"""C3 Migration Studio local server: the dashboard plus the Try It bridge.

Binds 127.0.0.1 only and serves the dashboard files and /api from the same
origin. POST /api/analyze takes a PHP project ZIP and a MySQL schema .sql,
extracts the .php files into a new temp run folder outside the repository and
runs the existing CLI (python -m c3.cli run) on them. Uploaded PHP is only
parsed by the C3 AST extractor; it is never executed or included.

Responses carry the pipeline's own counts. No accuracy, precision, recall or
other percentage is computed for uploads: there is no ground truth for them.

Usage (repo root, component venv):
    components/c3_data_layer/.venv/Scripts/python.exe components/c3_data_layer/dashboard/server.py
    then open http://127.0.0.1:8765/
"""

import argparse
import email.parser
import email.policy
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

HOST = "127.0.0.1"  # never configurable: the bridge is local only
DEFAULT_PORT = 8765
DASHBOARD = Path(__file__).resolve().parent
COMPONENT = DASHBOARD.parent
REPO = COMPONENT.parents[1]
DEFAULT_RUN_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp" / "c3try"

MB = 1024 * 1024
LIMITS = {
    "zip_bytes": 50 * MB,
    "sql_bytes": 5 * MB,
    "zip_files": 5000,
    "extracted_bytes": 200 * MB,
    "timeout_seconds": 300,
    "run_ttl_seconds": 3600,
    "preview_bytes": 200_000,
}
PHP_EXTENSIONS = {".php"}  # the only files the C3 extractor reads (extract_sql_calls.php)
MAX_PATH_CHARS = 240  # Windows MAX_PATH margin for extracted files
UPLOAD_LABEL = "Counts from this upload, no ground truth, not an accuracy figure"
PREVIEW_FILES = ("models.py", "data_access.py")
RUN_ID = re.compile(r"^[0-9a-f]{16}$")
ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/data/dashboard_data.json": ("data/dashboard_data.json", "application/json"),
    "/data/sample_run.json": ("data/sample_run.json", "application/json"),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'",
}


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def child_environment(run_dir: Path) -> dict:
    """A minimal environment: no credentials, no database settings, temp files inside the run."""
    env = {key: os.environ[key] for key in ENV_KEEP if key in os.environ}
    tmp = run_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(PYTHONPATH=str(COMPONENT / "src"), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               TEMP=str(tmp), TMP=str(tmp))
    return env


def parse_multipart(content_type: str, body: bytes) -> dict:
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise BridgeError(HTTPStatus.BAD_REQUEST, "Expected a multipart form upload.")
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            parts[name] = (part.get_filename() or "", part.get_payload(decode=True) or b"")
    return parts


def safe_member_path(name: str) -> PurePosixPath | None:
    """The relative path of a ZIP member, or None for a directory entry. Raises on zip-slip."""
    normalised = name.replace("\\", "/")
    if normalised.endswith("/"):
        return None
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised) or "\x00" in normalised:
        raise BridgeError(HTTPStatus.BAD_REQUEST, f"Rejected ZIP entry with an absolute path: {name!r}")
    path = PurePosixPath(normalised)
    if any(part in ("..", "") for part in path.parts) or any(part.endswith(":") for part in path.parts):
        raise BridgeError(HTTPStatus.BAD_REQUEST, f"Rejected ZIP entry that escapes the project folder: {name!r}")
    return path


class Bridge:
    """Runs uploads one at a time in temp folders under run_root (outside the repository)."""

    def __init__(self, run_root: Path = DEFAULT_RUN_ROOT, limits: dict | None = None, python: str = sys.executable):
        self.run_root = Path(run_root).resolve()
        if self.run_root == REPO or REPO in self.run_root.parents:
            raise SystemExit(f"run root {self.run_root} is inside the repository; refusing")
        self.limits = {**LIMITS, **(limits or {})}
        self.python = python
        self.lock = threading.Lock()
        self.run_root.mkdir(parents=True, exist_ok=True)

    # ---------- uploads ----------
    def extract_zip(self, data: bytes, target: Path) -> dict:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
            members = archive.infolist()
        except (zipfile.BadZipFile, ValueError, OSError) as error:
            raise BridgeError(HTTPStatus.BAD_REQUEST, f"The PHP upload is not a valid ZIP file ({error}).")
        files = [m for m in members if not m.is_dir()]
        if len(files) > self.limits["zip_files"]:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                              f"The ZIP has {len(files)} files; the limit is {self.limits['zip_files']}.")
        if sum(m.file_size for m in files) > self.limits["extracted_bytes"]:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The ZIP would extract to more than the size limit.")
        php, skipped, written = 0, 0, 0
        for member in members:
            path = safe_member_path(member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise BridgeError(HTTPStatus.BAD_REQUEST, f"Rejected symbolic link in the ZIP: {member.filename!r}")
            if member.flag_bits & 0x1:
                raise BridgeError(HTTPStatus.BAD_REQUEST, "Encrypted ZIP entries are not supported.")
            if path is None:
                continue
            if path.suffix.lower() not in PHP_EXTENSIONS:
                skipped += 1
                continue
            destination = target.joinpath(*path.parts)
            if len(str(destination)) > MAX_PATH_CHARS:
                raise BridgeError(HTTPStatus.BAD_REQUEST, f"A path in the ZIP is too long to extract on Windows: {member.filename!r}")
            if target.resolve() not in destination.resolve().parents:
                raise BridgeError(HTTPStatus.BAD_REQUEST, f"Rejected ZIP entry outside the project folder: {member.filename!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(member) as source, open(destination, "wb") as sink:
                    while chunk := source.read(MB):
                        written += len(chunk)
                        if written > self.limits["extracted_bytes"]:
                            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The ZIP extracts to more than the size limit.")
                        sink.write(chunk)
            except (zipfile.BadZipFile, OSError, EOFError) as error:
                raise BridgeError(HTTPStatus.BAD_REQUEST, f"The ZIP could not be read ({error}).")
            php += 1
        if not php:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "The ZIP contains no .php files.")
        return {"php_files": php, "files_skipped": skipped}

    def check_schema(self, name: str, data: bytes) -> None:
        if not data:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "The MySQL schema (.sql) file is missing or empty.")
        if not name.lower().endswith(".sql"):
            raise BridgeError(HTTPStatus.BAD_REQUEST, "The schema must be a .sql file.")
        if len(data) > self.limits["sql_bytes"]:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The schema is larger than the size limit.")
        if not re.search(rb"create\s+table", data, re.I):
            raise BridgeError(HTTPStatus.BAD_REQUEST, "The schema file contains no CREATE TABLE statement.")

    # ---------- runs ----------
    def analyze(self, zip_name: str, zip_data: bytes, sql_name: str, sql_data: bytes) -> dict:
        if not zip_data:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "The PHP project ZIP is missing or empty.")
        if len(zip_data) > self.limits["zip_bytes"]:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The ZIP is larger than the size limit.")
        self.check_schema(sql_name, sql_data)
        if not self.lock.acquire(blocking=False):
            raise BridgeError(HTTPStatus.CONFLICT, "Another analysis is already running. Try again when it finishes.")
        try:
            self.cleanup()
            run_id = secrets.token_hex(8)
            run_dir = self.run_root / run_id
            app_dir, out_dir = run_dir / "app", run_dir / "out"
            app_dir.mkdir(parents=True)
            try:
                upload = self.extract_zip(zip_data, app_dir)
                schema = run_dir / "schema.sql"
                schema.write_bytes(sql_data)
                started = time.monotonic()
                self.run_cli(run_dir, app_dir, schema, out_dir)
                duration = round(time.monotonic() - started, 1)
            except BaseException:
                shutil.rmtree(run_dir, ignore_errors=True)
                raise
            meta = {"run_id": run_id, "created": time.time(), "zip_name": Path(zip_name).name, "schema_name": Path(sql_name).name,
                    "duration_seconds": duration, **upload}
            (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            return self.summary(run_id)
        finally:
            self.lock.release()

    def run_cli(self, run_dir: Path, app_dir: Path, schema: Path, out_dir: Path) -> None:
        command = [self.python, "-m", "c3.cli", "run", "--php", str(app_dir), "--schema", str(schema), "--output", str(out_dir)]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=run_dir, env=child_environment(run_dir), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        try:
            _, stderr = process.communicate(timeout=self.limits["timeout_seconds"])
        except subprocess.TimeoutExpired:
            kill_tree(process)
            raise BridgeError(HTTPStatus.GATEWAY_TIMEOUT,
                              f"The analysis did not finish within {self.limits['timeout_seconds']} seconds and was stopped.")
        (run_dir / "pipeline_log.txt").write_bytes(stderr or b"")
        if process.returncode != 0 or not (out_dir / "summary.json").is_file():
            tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            reason = tail[-1] if tail else f"exit code {process.returncode}"
            raise BridgeError(HTTPStatus.UNPROCESSABLE_ENTITY, f"C3 could not process this upload: {reason}")

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id or ""):
            raise BridgeError(HTTPStatus.NOT_FOUND, "Unknown run.")
        run_dir = self.run_root / run_id
        if not (run_dir / "meta.json").is_file():
            raise BridgeError(HTTPStatus.NOT_FOUND, "This run does not exist or has expired.")
        return run_dir

    def summary(self, run_id: str) -> dict:
        run_dir = self.run_dir(run_id)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        s = json.loads((run_dir / "out" / "summary.json").read_text(encoding="utf-8"))
        return {
            "run_id": run_id,
            "label": UPLOAD_LABEL,
            "counts": {
                "sql_detected": s["total_queries"],
                "sql_recovered": s["recovered_queries"],
                "converted_functions": s["converted_queries"],
                "unresolved_sites": s["unresolved_total"],
                "unresolved_recovery_stage": s["unresolved_recovery_stage"],
                "unresolved_conversion_stage": s["unresolved_conversion_stage"],
                "converted_by_general_rules": s["converted_by_general_rules"],
                "converted_by_query_specific_template": s["converted_by_query_specific_template"],
            },
            "upload": {k: meta[k] for k in ("zip_name", "schema_name", "php_files", "files_skipped")},
            "duration_seconds": meta["duration_seconds"],
            "expires_in_seconds": max(0, int(meta["created"] + self.limits["run_ttl_seconds"] - time.time())),
        }

    def unresolved(self, run_id: str) -> dict:
        run_dir = self.run_dir(run_id)
        app_dir = (run_dir / "app").resolve()
        rows = []
        for u in json.loads((run_dir / "out" / "unresolved_queries.json").read_text(encoding="utf-8")):
            try:
                file = Path(u["source_file"]).resolve().relative_to(app_dir).as_posix()
            except ValueError:
                file = Path(u["source_file"]).name
            rows.append({"query_id": u["query_id"], "file": file, "line": u["source_line"], "stage": u["stage"],
                         "reason_code": u.get("reason_code"), "reason": u.get("reason"), "operation": u.get("operation")})
        return {"run_id": run_id, "label": UPLOAD_LABEL, "sites": rows}

    def preview(self, run_id: str, name: str) -> dict:
        if name not in PREVIEW_FILES:
            raise BridgeError(HTTPStatus.NOT_FOUND, "Only models.py and data_access.py can be previewed.")
        data = (self.run_dir(run_id) / "out" / name).read_bytes()
        limit = self.limits["preview_bytes"]
        return {"run_id": run_id, "file": name, "size_bytes": len(data), "truncated": len(data) > limit,
                "text": data[:limit].decode("utf-8", "replace")}

    def download(self, run_id: str) -> bytes:
        out_dir = self.run_dir(run_id) / "out"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(out_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, PurePosixPath("c3_output", path.relative_to(out_dir).as_posix()))
        return buffer.getvalue()

    def delete(self, run_id: str) -> None:
        if RUN_ID.match(run_id or ""):
            shutil.rmtree(self.run_root / run_id, ignore_errors=True)

    def cleanup(self) -> None:
        cutoff = time.time() - self.limits["run_ttl_seconds"]
        for run_dir in self.run_root.iterdir():
            if run_dir.is_dir() and RUN_ID.match(run_dir.name) and run_dir.stat().st_mtime < cutoff:
                shutil.rmtree(run_dir, ignore_errors=True)


def kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        process.kill()
    process.wait()


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "C3MigrationStudio"
        sys_version = ""

        def log_message(self, format, *args):  # quiet console: method and path only
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}\n")

        def send_body(self, status, body: bytes, content_type: str, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, payload):
            self.send_body(status, json.dumps(payload).encode("utf-8"), "application/json")

        def guard(self) -> bool:
            # Only same-origin requests addressed to this local server (blocks DNS rebinding and cross-site posts).
            port = self.server.server_address[1]
            allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in allowed_hosts:
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Requests must use 127.0.0.1 or localhost."})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin requests are not accepted."})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            path = self.path.split("?")[0]
            try:
                if path == "/api/limits":
                    return self.send_json(HTTPStatus.OK, {"limits": bridge.limits, "label": UPLOAD_LABEL})
                match = re.match(r"^/api/runs/([^/]+)/(summary|unresolved|download|file/(models\.py|data_access\.py))$", path)
                if match:
                    run_id, what = match.group(1), match.group(2)
                    if what == "summary":
                        return self.send_json(HTTPStatus.OK, bridge.summary(run_id))
                    if what == "unresolved":
                        return self.send_json(HTTPStatus.OK, bridge.unresolved(run_id))
                    if what == "download":
                        body = bridge.download(run_id)
                        bridge.delete(run_id)  # temp runs are deleted once downloaded (the zip is already in memory)
                        return self.send_body(HTTPStatus.OK, body, "application/zip",
                                              {"Content-Disposition": f'attachment; filename="c3_output_{run_id}.zip"'})
                    return self.send_json(HTTPStatus.OK, bridge.preview(run_id, match.group(3)))
                if path.startswith("/api/"):
                    return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API path."})
                if path in STATIC and (DASHBOARD / STATIC[path][0]).is_file():
                    name, content_type = STATIC[path]
                    return self.send_body(HTTPStatus.OK, (DASHBOARD / name).read_bytes(), content_type)
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            except BridgeError as error:
                return self.send_json(error.status, {"error": error.message})

        def do_POST(self):
            if not self.guard():
                return
            if self.path.split("?")[0] != "/api/analyze":
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API path."})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > bridge.limits["zip_bytes"] + bridge.limits["sql_bytes"] + MB:
                    self.close_connection = True
                    raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The upload is larger than the size limits.")
                content_type = self.headers.get("Content-Type", "")
                if not content_type.startswith("multipart/form-data"):
                    raise BridgeError(HTTPStatus.BAD_REQUEST, "Expected a multipart form upload.")
                parts = parse_multipart(content_type, self.rfile.read(length))
                zip_name, zip_data = parts.get("php_zip", ("", b""))
                sql_name, sql_data = parts.get("schema_sql", ("", b""))
                return self.send_json(HTTPStatus.OK, bridge.analyze(zip_name, zip_data, sql_name, sql_data))
            except BridgeError as error:
                return self.send_json(error.status, {"error": error.message})

    return Handler


class StudioServer(ThreadingHTTPServer):
    """Binds its port exclusively. The stdlib default (SO_REUSEADDR) lets a second server on Windows
    silently share a port that is already in use, so requests could reach the wrong process."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(port: int = DEFAULT_PORT, bridge: Bridge | None = None) -> ThreadingHTTPServer:
    bridge = bridge or Bridge()
    try:
        server = StudioServer((HOST, port), make_handler(bridge))
    except OSError as error:
        raise SystemExit(f"Port {port} on {HOST} is already in use ({error}). Stop the other server or pass --port.")
    server.bridge = bridge
    return server


def start_cleanup(bridge: Bridge, interval: int = 60) -> None:
    def loop():
        while True:
            time.sleep(interval)
            try:
                bridge.cleanup()
            except OSError:
                pass
    threading.Thread(target=loop, daemon=True).start()


SAMPLE_FILE = DASHBOARD / "data" / "sample_run.json"


def record_sample(app_label: str, php_dir: Path, schema: Path, target: Path = SAMPLE_FILE, bridge: Bridge | None = None,
                  allow_hms: bool = False) -> dict:
    """Run one app through the bridge and save it as the Try It fallback ("Recorded run, not live")."""
    php_dir, schema = Path(php_dir).resolve(), Path(schema).resolve()
    if "hms_kishan0725" in php_dir.as_posix() and not allow_hms:
        raise SystemExit("HMS (kishan0725) has no licence; it is not recorded unless explicitly allowed (--allow-hms).")
    if target.exists():
        raise SystemExit(f"{target} already exists; remove it first")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(php_dir.rglob("*.php")):
            archive.write(path, path.relative_to(php_dir).as_posix())
    bridge = bridge or Bridge()
    summary = bridge.analyze(f"{php_dir.name}.zip", buffer.getvalue(), schema.name, schema.read_bytes())
    run_id = summary["run_id"]
    try:
        sample = {
            "recorded": True, "label": "Recorded run, not live", "app": app_label,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M"), "summary": summary, "unresolved": bridge.unresolved(run_id),
            "previews": {name: bridge.preview(run_id, name) for name in PREVIEW_FILES},
        }
    finally:
        bridge.delete(run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sample, indent=1) + "\n", encoding="utf-8")
    return sample


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="C3 Migration Studio (dashboard + local Try It bridge on 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--record-sample", metavar="APP_LABEL", help="record dashboard/data/sample_run.json from --php and --schema, then exit")
    parser.add_argument("--php", type=Path, help="with --record-sample: the app's PHP folder")
    parser.add_argument("--schema", type=Path, help="with --record-sample: the app's schema .sql")
    parser.add_argument("--allow-hms", action="store_true", help="with --record-sample: allow the unlicensed HMS app")
    args = parser.parse_args(argv)
    if args.record_sample:
        if not (args.php and args.schema):
            parser.error("--record-sample needs --php and --schema")
        sample = record_sample(args.record_sample, args.php, args.schema, allow_hms=args.allow_hms)
        print(f"wrote {SAMPLE_FILE} ({sample['app']}: {sample['summary']['counts']})")
        return
    server = make_server(args.port)
    server.bridge.cleanup()
    start_cleanup(server.bridge)
    print(f"C3 Migration Studio on http://{HOST}:{args.port}/  (temp runs in {server.bridge.run_root}; Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
