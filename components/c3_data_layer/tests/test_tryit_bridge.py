"""Try It bridge (dashboard/server.py): upload safety, limits, local-only serving,
and no accuracy figures for uploads. Every run goes to pytest temp folders."""

import http.client
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "components" / "c3_data_layer" / "dashboard"
SIMPLE = ROOT / "benchmarks" / "apps" / "simple_crud"

sys.dont_write_bytecode = True  # importing server.py must not create dashboard/__pycache__
spec = importlib.util.spec_from_file_location("c3_studio_server", DASHBOARD / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="PHP executable not on PATH")
SCHEMA = b"CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(20));"
PERCENT_WORDS = re.compile(r"(?<![a-z])(percent|percentage|precision|recall|accuracy|rate|success)(?![a-z])", re.I)


def make_zip(entries: dict, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "../../outside.php")
    return buffer.getvalue()


def simple_crud_zip() -> bytes:
    return make_zip({p.relative_to(SIMPLE / "legacy").as_posix(): p.read_bytes() for p in (SIMPLE / "legacy").rglob("*.php")})


@pytest.fixture
def bridge(tmp_path):
    return server.Bridge(run_root=tmp_path / "runs")


def error_of(callable_, *args):
    with pytest.raises(server.BridgeError) as caught:
        callable_(*args)
    return caught.value


# ---------- zip-slip and unsafe entries ----------

@pytest.mark.parametrize("name", ["../evil.php", "/abs.php", "C:/win.php", "a/../../b.php", "a\\..\\..\\c.php", "x/./../../d.php"])
def test_zip_slip_is_rejected(bridge, tmp_path, name):
    target = tmp_path / "extract"
    target.mkdir()
    error = error_of(bridge.extract_zip, make_zip({"ok.php": b"<?php", name: b"<?php"}), target)
    assert error.status == 400
    assert not (tmp_path / "evil.php").exists() and not list(tmp_path.glob("*.php"))


def test_symlink_entry_is_rejected(bridge, tmp_path):
    error = error_of(bridge.extract_zip, make_zip({"ok.php": b"<?php"}, symlink="link.php"), tmp_path)
    assert error.status == 400 and "symbolic link" in error.message


def test_only_php_files_are_extracted(bridge, tmp_path):
    result = bridge.extract_zip(make_zip({"a.php": b"<?php", "b.exe": b"MZ", "c.js": b"x", "d/e.php": b"<?php"}), tmp_path)
    assert result == {"php_files": 2, "files_skipped": 2}
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == ["a.php", "d/e.php"]


# ---------- limits ----------

def test_oversize_zip_is_rejected(tmp_path):
    small = server.Bridge(run_root=tmp_path / "runs", limits={"zip_bytes": 100})
    error = error_of(small.analyze, "a.zip", make_zip({"a.php": b"<?php " + b"x" * 500}), "s.sql", SCHEMA)
    assert error.status == 413


def test_oversize_schema_is_rejected(tmp_path):
    small = server.Bridge(run_root=tmp_path / "runs", limits={"sql_bytes": 10})
    error = error_of(small.analyze, "a.zip", make_zip({"a.php": b"<?php"}), "s.sql", SCHEMA)
    assert error.status == 413


def test_too_many_files_and_zip_bomb_are_rejected(tmp_path):
    few = server.Bridge(run_root=tmp_path / "runs", limits={"zip_files": 2})
    assert error_of(few.extract_zip, make_zip({f"{i}.php": b"<?php" for i in range(3)}), tmp_path).status == 413
    tiny = server.Bridge(run_root=tmp_path / "runs2", limits={"extracted_bytes": 1000})
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.php", b"<?php " + b"0" * 100_000)
    assert error_of(tiny.extract_zip, bomb.getvalue(), tmp_path / "x").status == 413


# ---------- bad input ----------

def test_bad_zip_returns_an_error(bridge):
    error = error_of(bridge.analyze, "a.zip", b"this is not a zip", "s.sql", SCHEMA)
    assert error.status == 400 and "not a valid ZIP" in error.message


def test_zip_without_php_returns_an_error(bridge):
    assert "no .php files" in error_of(bridge.analyze, "a.zip", make_zip({"readme.txt": b"x"}), "s.sql", SCHEMA).message


@pytest.mark.parametrize("name,data,text", [("", b"", "missing"), ("s.txt", SCHEMA, ".sql"), ("s.sql", b"SELECT 1;", "CREATE TABLE")])
def test_missing_or_invalid_schema_returns_an_error(bridge, name, data, text):
    error = error_of(bridge.analyze, "a.zip", make_zip({"a.php": b"<?php"}), name, data)
    assert error.status == 400 and text in error.message


def test_second_concurrent_run_is_refused(bridge):
    assert bridge.lock.acquire(blocking=False)
    try:
        error = error_of(bridge.analyze, "a.zip", make_zip({"a.php": b"<?php"}), "s.sql", SCHEMA)
        assert error.status == 409
    finally:
        bridge.lock.release()


def test_run_root_inside_the_repository_is_refused():
    with pytest.raises(SystemExit):
        server.Bridge(run_root=DASHBOARD / "runs")


def test_child_environment_carries_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("C3_DB_PASSWORD", "never-pass-this")
    monkeypatch.setenv("C3_BENCH_DB_PASSWORD", "never-pass-this")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-pass-this")
    env = server.child_environment(tmp_path)
    assert "never-pass-this" not in json.dumps(env)
    assert set(env) <= set(server.ENV_KEEP) | {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "TEMP", "TMP"}
    assert env["TEMP"].startswith(str(tmp_path))


# ---------- real runs (PHP needed) ----------

def _repo_status():
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout


@needs_php
def test_real_run_counts_previews_download_and_nothing_in_repo(bridge):
    before = _repo_status()
    summary = bridge.analyze("simple_crud.zip", simple_crud_zip(), "schema.sql", (SIMPLE / "schema.sql").read_bytes())
    run_dir = bridge.run_root / summary["run_id"]
    pipeline = json.loads((run_dir / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["sql_detected"] == pipeline["total_queries"] > 0
    assert summary["counts"]["converted_functions"] == pipeline["converted_queries"]
    assert summary["counts"]["unresolved_sites"] == pipeline["unresolved_total"]
    assert summary["label"] == server.UPLOAD_LABEL

    unresolved = bridge.unresolved(summary["run_id"])
    for payload in (summary, unresolved):
        text = json.dumps(payload)
        assert "%" not in text
        assert not [k for k in _keys(payload) if PERCENT_WORDS.search(k)]
    preview = bridge.preview(summary["run_id"], "data_access.py")
    assert "def " in preview["text"] and preview["truncated"] is False
    with pytest.raises(server.BridgeError):
        bridge.preview(summary["run_id"], "../summary.json")

    archive = zipfile.ZipFile(io.BytesIO(bridge.download(summary["run_id"])))
    assert {"c3_output/models.py", "c3_output/data_access.py", "c3_output/unresolved_queries.json"} <= set(archive.namelist())
    bridge.delete(summary["run_id"])
    assert not run_dir.exists()
    assert _repo_status() == before
    assert not (DASHBOARD / "__pycache__").exists()


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


@needs_php
def test_timeout_stops_the_run_and_cleans_up(tmp_path):
    slow = server.Bridge(run_root=tmp_path / "runs", limits={"timeout_seconds": 0.01})
    error = error_of(slow.analyze, "simple_crud.zip", simple_crud_zip(), "schema.sql", (SIMPLE / "schema.sql").read_bytes())
    assert error.status == 504 and "did not finish" in error.message
    assert not [p for p in (tmp_path / "runs").iterdir()]
    assert slow.lock.acquire(blocking=False)


# ---------- HTTP layer ----------

@pytest.fixture
def running(tmp_path):
    httpd = server.make_server(0, server.Bridge(run_root=tmp_path / "runs"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def request(httpd, method, path, body=None, headers=None, host=None):
    port = httpd.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    connection.putrequest(method, path, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    for key, value in (headers or {}).items():
        connection.putheader(key, value)
    connection.putheader("Content-Length", str(len(body or b"")))
    connection.endheaders(body or b"")
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def multipart(fields: dict) -> tuple[bytes, str]:
    boundary = "c3testboundary"
    out = b""
    for name, (filename, data) in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def test_server_binds_loopback_only(running):
    assert running.server_address[0] == "127.0.0.1"
    assert server.HOST == "127.0.0.1"
    assert "--host" not in (DASHBOARD / "server.py").read_text(encoding="utf-8")


def test_port_already_in_use_fails_loudly(running):
    with pytest.raises(SystemExit) as caught:
        server.make_server(running.server_address[1], running.bridge)
    assert "already in use" in str(caught.value)


def test_static_allowlist_and_host_guard(running):
    assert request(running, "GET", "/")[0] == 200
    assert request(running, "GET", "/data/dashboard_data.json")[0] == 200
    for path in ("/server.py", "/../server.py", "/data/../server.py", "/%2e%2e/README.md"):
        assert request(running, "GET", path)[0] == 404, path
    assert request(running, "GET", "/api/limits", host="evil.example:80")[0] == 403
    status, _, body = request(running, "GET", "/api/limits", headers={"Origin": "http://evil.example"})
    assert status == 403
    assert request(running, "GET", "/api/runs/zzzz/summary")[0] == 404


def test_http_upload_errors(running):
    body, content_type = multipart({"php_zip": ("a.zip", b"not a zip"), "schema_sql": ("s.sql", SCHEMA)})
    status, _, payload = request(running, "POST", "/api/analyze", body, {"Content-Type": content_type})
    assert status == 400 and "not a valid ZIP" in json.loads(payload)["error"]
    body, content_type = multipart({"php_zip": ("a.zip", make_zip({"a.php": b"<?php"}))})
    status, _, payload = request(running, "POST", "/api/analyze", body, {"Content-Type": content_type})
    assert status == 400 and "schema" in json.loads(payload)["error"]


@needs_php
def test_http_end_to_end_and_download_deletes_the_run(running):
    body, content_type = multipart({"php_zip": ("simple_crud.zip", simple_crud_zip()),
                                    "schema_sql": ("schema.sql", (SIMPLE / "schema.sql").read_bytes())})
    status, _, payload = request(running, "POST", "/api/analyze", body, {"Content-Type": content_type})
    assert status == 200, payload
    run_id = json.loads(payload)["run_id"]
    assert request(running, "GET", f"/api/runs/{run_id}/unresolved")[0] == 200
    assert request(running, "GET", f"/api/runs/{run_id}/file/models.py")[0] == 200
    status, headers, data = request(running, "GET", f"/api/runs/{run_id}/download")
    assert status == 200 and headers["Content-Type"] == "application/zip" and zipfile.is_zipfile(io.BytesIO(data))
    assert request(running, "GET", f"/api/runs/{run_id}/summary")[0] == 404


# ---------- frontend ----------

def _try_it_block():
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    return re.search(r"/\* ===== TRY IT \(no evidence data\) ===== \*/(.*?)/\* ===== END TRY IT ===== \*/", code, re.S).group(1)


def test_try_it_code_never_reads_the_evidence_data():
    block = _try_it_block()
    for forbidden in ("dashboard_data", "DATA", "measurement", "Evidence", "records", "experiments"):
        assert forbidden not in block, forbidden
    urls = set(re.findall(r'"((?:/|data/)[^"]*)"', block))
    assert urls == {"/api/", "data/sample_run.json"}, urls


def test_uploaded_results_never_show_a_percentage():
    block = _try_it_block()
    assert "%" not in block
    assert not PERCENT_WORDS.search(re.sub(r"//.*", "", block).replace("Counts from this upload", ""))


def test_recorded_sample_refuses_hms(tmp_path):
    with pytest.raises(SystemExit):
        server.record_sample("HMS", ROOT / "benchmarks" / "apps" / "_app_only" / "hms_kishan0725",
                             ROOT / "benchmarks" / "apps" / "hms_kishan0725" / "myhmsdb.sql", target=tmp_path / "s.json")


@needs_php
def test_recorded_sample_writes_only_its_target(tmp_path):
    target = tmp_path / "sample_run.json"
    sample = server.record_sample("simple_crud", SIMPLE / "legacy", SIMPLE / "schema.sql", target=target,
                                  bridge=server.Bridge(run_root=tmp_path / "runs"))
    assert sample["label"] == "Recorded run, not live" and target.is_file()
    assert not [p for p in (tmp_path / "runs").iterdir()]
