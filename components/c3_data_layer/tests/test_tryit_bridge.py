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
import time
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


@needs_php
def test_before_after_of_a_live_run_is_upload_relative_and_has_no_percentage(bridge):
    summary = bridge.analyze("simple_crud.zip", simple_crud_zip(), "schema.sql", (SIMPLE / "schema.sql").read_bytes())
    pipeline = json.loads((bridge.run_root / summary["run_id"] / "out" / "summary.json").read_text(encoding="utf-8"))
    ba = bridge.before_after(summary["run_id"])
    assert ba["detail"] is True and ba["label"] == server.UPLOAD_LABEL
    assert ba["note"] == "These are measured properties of the generated code, not performance claims."
    assert len(ba["queries"]) == pipeline["total_queries"]
    assert sum(q["status"] == "converted" for q in ba["queries"]) == pipeline["converted_queries"]
    for q in ba["queries"]:
        assert not re.match(r"^[A-Za-z]:|/", q["site"]) and q["legacy_sql"]
        if q["status"] == "converted":
            assert all(f":{key}" in q["generated_sql"] for key in q["function"]["bind_keys"])
    e4 = next(b for b in ba["benefits"] if b["key"] == "e4")
    assert e4["value"] is None and e4["note"].startswith("Not measured")
    f = ba["facts"]
    assert (f["detected"], f["converted"], f["unresolved"]) == (pipeline["total_queries"], pipeline["converted_queries"], pipeline["unresolved_total"])
    assert f["converted"] + f["unresolved_recovery"] + f["unresolved_conversion"] == f["detected"]
    assert "verification" not in ba  # uploads have no E1/E4: the view shows "Not measured"
    assert all(q["php_line"] and "mysqli_query" in q["php_line"] for q in ba["queries"])
    assert all(q["function"]["code"].startswith("def ") for q in ba["queries"] if q["status"] == "converted")
    text = json.dumps(ba)
    assert str(bridge.run_root) not in text and str(server.REPO) not in text and "%" not in text
    bridge.delete(summary["run_id"])


def test_try_it_shows_migration_impact_through_the_shared_renderer():
    block = _try_it_block()
    assert "MigrationImpact.render" in block and "runs/${state.run}/before_after" in block
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    renderer = re.search(r"/\* ===== MIGRATION IMPACT .*?===== \*/(.*?)/\* ===== END MIGRATION IMPACT ===== \*/", code, re.S).group(1)
    assert "%" not in renderer and not PERCENT_WORDS.search(re.sub(r"//.*", "", renderer))


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
    runs = tmp_path / "runs"
    # Windows: a just-killed child of the CLI can still hold the run folder as its working directory for a moment, so
    # rmtree may delete every file but leave the empty folder. No uploaded file may remain; the empty folder must go
    # at the next cleanup (made to treat every run as expired here).
    deadline = time.monotonic() + 5
    while any(p.is_file() for p in runs.rglob("*")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not [p for p in runs.rglob("*") if p.is_file()]
    slow.limits["run_ttl_seconds"] = -1
    while list(runs.iterdir()) and time.monotonic() < deadline:
        slow.cleanup()
        time.sleep(0.05)
    assert not list(runs.iterdir())
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
    status, _, payload = request(running, "GET", f"/api/runs/{run_id}/before_after")
    assert status == 200 and json.loads(payload)["detail"] is True
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


# ---------- failure reporting ----------

SCHOOL_LIKE_STDOUT = "".join(f"[{k}/7] {t}...\n" for k, t in enumerate([
    "Recovering legacy SQL", "Converting recovered queries", "Parsing schema and generating SQLAlchemy models",
    "Generating data-access functions", "Generating column-use report",
    "Generating unresolved-query, traceability and security reports"], 1))
SCHOOL_LIKE_STDERR = """Traceback (most recent call last):
  File "<c3>/c3/pipeline.py", line 172, in run_pipeline
    generate_security_report(
  File "<c3>/c3/analysis/security_report.py", line 297, in scan_generated_code
    tree = ast.parse(data_access_source)
  File "<python>/Lib/ast.py", line 50, in parse
    return compile(source, filename, mode, flags,
  File "<unknown>", line 237
    def insert_student_q013(session, roll_no, class, address, *, commit: bool = True):
                                              ^^^^^
SyntaxError: invalid syntax
"""


def test_describe_failure_names_stage_frame_generated_line_and_c3_defect():
    failure = server.describe_failure(SCHOOL_LIKE_STDOUT, SCHOOL_LIKE_STDERR)
    assert failure["stage"] == "stage 6/7 (Generating unresolved-query, traceability and security reports)"
    assert failure["exception"] == "SyntaxError" and failure["message"] == "invalid syntax"
    assert failure["frame"] == "c3/analysis/security_report.py:297 in scan_generated_code"
    assert failure["generated_code"] == {"line": 237, "code": "def insert_student_q013(session, roll_no, class, address, *, commit: bool = True):"}
    assert failure["classification"] == "c3_defect"
    assert "a C3 code-generation defect, not a problem with your upload" in failure["summary"]


@pytest.mark.parametrize("stdout,stderr,kind", [
    ("[1/7] Recovering legacy SQL...\n[2/7] Converting...\n[3/7] Parsing schema...\n[4/7] Generating data-access functions...\n",
     'Traceback (most recent call last):\n  File "<c3>/c3/pipeline.py", line 160, in run_pipeline\n    check_generated_python(\n'
     "c3.pipeline.GeneratedCodeError: stage 4/7 (data-access functions): generated data_access.py is not valid Python at line 3 "
     "(invalid syntax): def f(session, class):\n", "c3_defect"),
    ("[1/7] Recovering legacy SQL...\n", 'Traceback (most recent call last):\n  File "<c3>/c3/mapping/sql_locator.py", line 66, in _run_ast_extractor\n'
     "c3.mapping.sql_locator.PhpAstExtractorError: PHP executable was not found: php\n", "environment"),
    ("[1/7] Recovering legacy SQL...\n[2/7] Converting recovered queries...\n",
     'Traceback (most recent call last):\n  File "<c3>/c3/mapping/schema_reader.py", line 600, in read_schema\nValueError: bad schema\n', "input_schema"),
    ("", "Killed\n", "unknown"),
])
def test_describe_failure_classifies(stdout, stderr, kind):
    failure = server.describe_failure(stdout, stderr)
    assert failure["classification"] == kind
    assert failure["classification_text"] in failure["summary"]


def test_sanitize_leaves_only_upload_relative_paths(tmp_path):
    run_dir = tmp_path / "runs" / "0123456789abcdef"
    app_dir = run_dir / "app"
    text = (f'File "{app_dir}\\school\\students.php", line 26\n'
            f'File "{server.COMPONENT / "src" / "c3" / "pipeline.py"}"\n{run_dir}\\out\\data_access.py\n{server.REPO}\\x\n')
    clean = server.sanitize(text, run_dir, app_dir)
    assert 'File "school\\students.php", line 26' in clean
    assert "<c3>" in clean and "<run>" in clean and "<repo>" in clean
    assert str(tmp_path) not in clean and str(server.REPO) not in clean


FAKE_CLI = '''import os, sys
php = sys.argv[sys.argv.index("--php") + 1]
src = os.environ["FAKE_C3_SRC"]
for k, text in enumerate(["Recovering legacy SQL", "Converting recovered queries", "Parsing schema and generating SQLAlchemy models",
                          "Generating data-access functions", "Generating column-use report",
                          "Generating unresolved-query, traceability and security reports"], 1):
    print("[%d/7] %s..." % (k, text))
sys.stderr.write("Traceback (most recent call last):\\n")
sys.stderr.write('  File "%s", line 297, in scan_generated_code\\n' % os.path.join(src, "c3", "analysis", "security_report.py"))
sys.stderr.write("    tree = ast.parse(data_access_source)\\n")
sys.stderr.write('  File "<unknown>", line 237\\n')
sys.stderr.write("    def insert_student_q013(session, roll_no, class, *, commit: bool = True):\\n")
sys.stderr.write("                                              ^^^^^\\n")
sys.stderr.write("note: built from %s\\n" % os.path.join(php, "school", "students.php"))
sys.stderr.write("SyntaxError: invalid syntax\\n")
sys.exit(1)
'''


@pytest.fixture
def failing_cli(tmp_path, monkeypatch):
    fake = tmp_path / "fake_c3"
    (fake / "c3").mkdir(parents=True)
    (fake / "c3" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "c3" / "cli.py").write_text(FAKE_CLI, encoding="utf-8")
    real = server.child_environment

    def environment(run_dir):
        env = real(run_dir)
        env["PYTHONPATH"] = str(fake)
        env["FAKE_C3_SRC"] = str(server.COMPONENT / "src")
        return env
    monkeypatch.setattr(server, "child_environment", environment)


def test_failed_run_keeps_only_a_sanitized_log(bridge, failing_cli):
    error = error_of(bridge.analyze, "school.zip", make_zip({"school/students.php": b"<?php"}), "school.sql", SCHEMA)
    assert error.status == 422
    failure = error.detail["failure"]
    assert failure["classification"] == "c3_defect" and failure["stage"].startswith("stage 6/7")
    assert failure["frame"] == "c3/analysis/security_report.py:297 in scan_generated_code"
    run_dir = bridge.run_root / error.detail["run_id"]
    assert sorted(p.name for p in run_dir.iterdir()) == ["failure.json", "pipeline_log.txt"]   # upload removed
    log = bridge.log(error.detail["run_id"]).decode("utf-8")
    assert "school/students.php" in log.replace("\\", "/") and str(bridge.run_root) not in log and str(server.REPO) not in log
    assert error.detail["log_url"] == f"/api/runs/{error.detail['run_id']}/log"
    assert "%" not in json.dumps(server.error_payload(error))


def test_failed_run_log_is_removed_by_the_hourly_cleanup(tmp_path, failing_cli):
    short = server.Bridge(run_root=tmp_path / "runs", limits={"run_ttl_seconds": 0})
    error = error_of(short.analyze, "a.zip", make_zip({"a.php": b"<?php"}), "s.sql", SCHEMA)
    import os
    import time
    run_dir = short.run_root / error.detail["run_id"]
    old = time.time() - 10
    os.utime(run_dir, (old, old))
    short.cleanup()
    assert not run_dir.exists()
    with pytest.raises(server.BridgeError):
        short.log(error.detail["run_id"])


def test_http_failure_returns_stage_classification_and_log(tmp_path, failing_cli):
    httpd = server.make_server(0, server.Bridge(run_root=tmp_path / "runs"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body, content_type = multipart({"php_zip": ("a.zip", make_zip({"a.php": b"<?php"})), "schema_sql": ("s.sql", SCHEMA)})
        status, _, payload = request(httpd, "POST", "/api/analyze", body, {"Content-Type": content_type})
        data = json.loads(payload)
        assert status == 422 and data["failure"]["classification"] == "c3_defect"
        assert data["error"].startswith("C3 stopped in stage 6/7") and "log_available" not in data
        status, headers, log = request(httpd, "GET", data["log_url"])
        assert status == 200 and headers["Content-Type"].startswith("text/plain") and b"SyntaxError" in log
        assert request(httpd, "GET", "/api/runs/0123456789abcdef/log")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_try_it_shows_the_failure_details():
    block = _try_it_block()
    assert "function showFailure(error)" in block and "showFailure(error);" in block
    for field in ("failure.stage", "failure.exception", "failure.frame", "failure.generated_code", "failure.classification_text",
                  "error.detail.log_url"):
        assert field in block, field


def test_sample_check_flags_paths_credentials_and_foreign_tables():
    schema = b"CREATE TABLE posts (id INT PRIMARY KEY);"
    clean = {"previews": {"data_access.py": {"text": "text('SELECT id FROM posts WHERE id = :id')"},
                          "models.py": {"text": "__tablename__ = \"posts\""}}}
    assert server.sample_problems(clean, schema) == []
    leaky = json.loads(json.dumps(clean))
    leaky["upload"] = {"zip_name": "C:\\Users\\someone\\app.zip"}
    leaky["previews"]["data_access.py"]["text"] += "\npassword = \"hunter2\"\ntext('SELECT * FROM appointmenttb')"
    problems = " ".join(server.sample_problems(leaky, schema))
    assert "absolute Windows path" in problems and "credential literal" in problems and "appointmenttb" in problems


def test_saved_news_buzz_sample_is_clean():
    sample_file = DASHBOARD / "data" / "sample_run.json"
    if not sample_file.is_file():
        pytest.skip("no recorded sample")
    sample = json.loads(sample_file.read_text(encoding="utf-8"))
    assert sample["label"] == "Recorded run of the NEWS-BUZZ benchmark, not live"
    assert server.sample_problems(sample, (ROOT / "benchmarks/apps/news_buzz/db/NEWSBUZZ.sql").read_bytes()
                                  if (ROOT / "benchmarks/apps/news_buzz/db/NEWSBUZZ.sql").is_file()
                                  else b"CREATE TABLE posts (id INT); CREATE TABLE users (id INT);") == []
    assert "%" not in json.dumps(sample["summary"]) + json.dumps(sample["unresolved"])


@needs_php
def test_recorded_sample_writes_only_its_target(tmp_path):
    target = tmp_path / "sample_run.json"
    sample = server.record_sample("simple_crud", SIMPLE / "legacy", SIMPLE / "schema.sql", target=target,
                                  bridge=server.Bridge(run_root=tmp_path / "runs"))
    assert sample["label"] == "Recorded run, not live" and target.is_file()
    assert not [p for p in (tmp_path / "runs").iterdir()]
