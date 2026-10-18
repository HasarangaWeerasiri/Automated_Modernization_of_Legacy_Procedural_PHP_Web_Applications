"""Generation of the standalone equivalence suite."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from c4 import dbstate
from c4.config import load_config
from c4.dbstate import Snapshot, TableState
from c4.harness import _python, build, checks_source


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checks(tmp_path):
    """The generated c4_checks.py, imported on its own."""
    file = tmp_path / "c4_checks.py"
    file.write_text(checks_source(), encoding="utf-8")
    return load_module(file, "generated_c4_checks")


# ------------------------------------------------------------------ the helper file


def test_the_helper_file_holds_the_engines_own_rules_word_for_word(checks):
    source = checks_source()

    for name in ("def normalise(", "def _match(", "def _plain_delta(", "def injection_effects(", "def outcome_of("):
        assert source.count(name) == 1, name
    assert "from c4" not in source and "import c4" not in source


def test_the_helper_file_normalises_values_exactly_as_the_engine_does(checks):
    from c4.compare import normalise

    for value in ("550.00", 550, None, True, " Viki", "0771234567", "2020-02-14", -0.0):
        assert checks.normalise(value) == normalise(value)


def _snapshots(rows_before, rows_after, keyed):
    def snapshot(rows):
        columns = ("id", "name") if keyed else ("name", "message")
        table = TableState(
            columns=columns, primary_key=("id",) if keyed else (),
            definition=tuple(f"{c} X" for c in columns), auto_increment=None,
            rows=tuple(dict(zip(columns, row)) for row in rows),
        )
        return Snapshot({"things": table})
    return snapshot(rows_before), snapshot(rows_after)


def _as_suite_snapshot(snapshot: Snapshot):
    return {
        name: {"columns": list(t.columns), "primary_key": list(t.primary_key), "rows": list(t.rows)}
        for name, t in snapshot.tables.items()
    }


@pytest.mark.parametrize(
    ("before", "after", "keyed"),
    [
        ([(1, "Ram")], [(1, "Ram"), (2, "Alia")], True),
        ([(1, "Ram"), (2, "Alia")], [(2, "Alia")], True),
        ([(1, "Ram")], [(1, "Rama")], True),
        ([(1, "Ram"), (2, "Alia")], [(2, "Ali"), (3, "Kim")], True),
        ([("Anu", "hi")], [("Anu", "hi"), ("Anu", "hi")], False),
        ([("Anu", "hi"), ("Ben", None)], [("Ben", None), ("Anu", "bye")], False),
        ([("Anu", "hi")], [("Anu", "hi")], False),
    ],
)
def test_the_suites_delta_agrees_with_the_engines(checks, before, after, keyed):
    engine_before, engine_after = _snapshots(before, after, keyed)

    expected = dbstate.delta(engine_before, engine_after)
    actual = checks.delta(_as_suite_snapshot(engine_before), _as_suite_snapshot(engine_after))

    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_values_are_written_as_python_that_reads_back_the_same():
    value = {"name": "' OR '1'='1", "fee": 550, "none": None, "flag": True, "rows": [{"a": "x" * 120}]}

    assert eval(_python(value, 8)) == value


# ----------------------------------------------------------------------- the suite


@pytest.fixture
def benchmark(tmp_path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    config = load_config("demo", environ={}, root=tmp_path)
    config.scenario_dir.mkdir(parents=True)
    config.oracle_dir.mkdir(parents=True)
    config.endpoint_map_file.write_text(json.dumps({"format": "c4-stand-in/1", "endpoints": [{
        "endpoint_id": "GET /api/things", "method": "GET", "path": "/api/things", "records_at": "things",
        "sources": [
            {"source_id": "list", "legacy": {"method": "GET", "path": "/list.php", "records": "only"}, "parameters": []},
            {"source_id": "search", "legacy": {"method": "POST", "path": "/search.php", "records": "only"},
             "parameters": [{"name": "q", "in": "query", "legacy_name": "q", "legacy_in": "body", "column": "things.name"}]},
        ],
    }]}))
    return config


def _add(config, scenario_id, source, values, kind="typical", parameter=None, *, outcome="success", records=()):
    config.scenario_dir.joinpath(f"{scenario_id}.json").write_text(json.dumps({
        "scenario_id": scenario_id, "scenario_type": kind, "case": kind, "parameter": parameter,
        "endpoint_id": "GET /api/things", "source_id": source, "values": values,
    }))
    config.oracle_dir.joinpath(f"{scenario_id}.json").write_text(json.dumps({
        "endpoint_id": "GET /api/things", "legacy_url": f"/{source}.php", "scenario_id": scenario_id,
        "scenario_type": kind, "input": {"method": "GET", "params": {}, "body": None},
        "legacy_response": {"status_code": 200, "outcome": outcome, "records": list(records), "messages": []},
        "db_state": {"baseline_fingerprint": "sha256:x", "delta": {}},
        "excluded_fields": [], "capture_runs": 2, "captured_at": "2026-10-15T00:00:00+00:00",
    }))


BASELINE = Snapshot({"things": TableState(
    columns=("id", "name"), primary_key=("id",), definition=("id INTEGER", "name TEXT"),
    auto_increment=3, rows=({"id": 1, "name": "Ram"}, {"id": 2, "name": "Alia"}),
)})


def test_one_test_per_scenario_with_the_expected_values_written_out(benchmark):
    _add(benchmark, "list_typical_01", "list", {}, records=[{"Name": "Ram"}, {"Name": "Alia"}])
    _add(benchmark, "search_injection_q_01", "search", {"q": "' OR '1'='1"}, "injection", "q", records=[{"Name": "Ram"}])

    folder = build(benchmark, BASELINE)

    assert sorted(path.name for path in folder.iterdir()) == [
        "baseline.json", "c4_checks.py", "conftest.py", "requirements.txt", "test_equivalence.py",
    ]
    tests = (folder / "test_equivalence.py").read_text(encoding="utf-8")
    compile(tests, "test_equivalence.py", "exec")
    assert tests.count("\ndef test_") == 2
    assert "{'Name': 'Ram'}" in tests and "{'Name': 'Alia'}" in tests
    assert "expect_safe_handling(response, db, before, probe=\"' OR '1'='1\", at='things')" in tests
    assert "Oracle record: oracles/demo/list_typical_01.json" in tests
    assert tests.count("    headers = {}\n    before = start(db)") == 2  # no login needed here
    assert json.loads((folder / "baseline.json").read_text()) == {
        "tables": {"things": [{"id": 1, "name": "Ram"}, {"id": 2, "name": "Alia"}]},
        "auto_increment": {"things": 3},
    }


def test_conflicting_scenarios_are_generated_as_skipped_tests_with_the_reason(benchmark):
    _add(benchmark, "list_typical_01", "list", {}, records=[{"Name": "Ram"}, {"Name": "Alia"}])
    _add(benchmark, "search_boundary_q_missing", "search", {}, "boundary", "q", outcome="no_effect")

    tests = (build(benchmark, BASELINE) / "test_equivalence.py").read_text(encoding="utf-8")

    assert tests.count("@pytest.mark.skip(") == 2
    assert "(also: search_boundary_q_missing)" in tests


def test_the_generated_suite_is_the_same_every_time(benchmark):
    _add(benchmark, "list_typical_01", "list", {}, records=[{"Name": "Ram"}])

    first = (build(benchmark, BASELINE) / "test_equivalence.py").read_bytes()

    assert (build(benchmark, BASELINE) / "test_equivalence.py").read_bytes() == first


# ------------------------------------------------- running it outside the engine


def _run_suite(folder: Path, migrated, *selection):
    environment = {
        **os.environ,
        "C4_API_URL": migrated.base_url,
        "C4_DB_URL": migrated.db_url,
        "PYTHONPATH": "",  # the engine must not be importable
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(folder), str(folder), *selection],
        cwd=folder, env=environment, capture_output=True, text=True,
    )


@pytest.mark.environment
def test_the_suite_runs_on_its_own_and_passes_on_a_correct_migration(at_baseline, baselines, tmp_path_factory):
    config = at_baseline
    folder = tmp_path_factory.mktemp("standalone")
    for file in build(config, baselines["migrated"]).iterdir():
        if file.is_file():
            (folder / file.name).write_bytes(file.read_bytes())

    result = _run_suite(folder, config.migrated)

    assert result.returncode == 0, result.stdout[-3000:]
    assert " passed" in result.stdout and " failed" not in result.stdout


@pytest.mark.environment
def test_the_suite_on_its_own_catches_a_suppressed_database_row(at_baseline, baselines, tmp_path_factory):
    config = at_baseline
    session = requests.Session()
    session.trust_env = False
    fault = config.migrated.base_url + "/_fault"
    try:
        available = session.get(fault, timeout=10).json().get("available", [])
    except (requests.RequestException, ValueError):
        available = []
    if "suppresses_contact_insert" not in available:
        pytest.skip("the migrated system is not the stub API written for the hms benchmark")
    folder = tmp_path_factory.mktemp("standalone_fault")
    for file in build(config, baselines["migrated"]).iterdir():
        if file.is_file():
            (folder / file.name).write_bytes(file.read_bytes())

    session.put(fault + "/suppresses_contact_insert", timeout=10)
    try:
        result = _run_suite(folder, config.migrated, "-k", "contact_form_typical")
    finally:
        session.delete(fault, timeout=10)
        session.close()

    assert result.returncode != 0
    assert "3 failed" in result.stdout
    assert "The database change differs from the legacy page's." in result.stdout
