"""FR6 join rule on MySQL (Clinic schema and seed): direct-SQL vs generated function, not PHP differential.

The Clinic application has no LEFT JOIN or comma-join site, so the existing PHP-vs-SQLAlchemy comparator (which runs
the application's own PHP files) cannot cover these shapes. Each case here is converted by the C3 converter and
emitter, then compared with the same SQL executed directly: built by raw interpolation as legacy code does, run with
mysqli_query and read with mysqli_fetch_assoc (php_join_rule_sql_test.php). This checks the result shape (row keys,
key order, NULL-extended LEFT JOIN rows, values) of the generated function against a direct run of the SQL. It is NOT
differential testing of original application PHP against the generated code.

Requires MySQL (C3_DB_PASSWORD) and PHP; skipped otherwise. Read-only: SELECTs on the two Clinic test databases.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from c3.conversion.query_converter import convert_query
from c3.generation.emitter import generate_data_access_source
from c3.mapping.schema_reader import read_schema

COMPONENT_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = COMPONENT_DIR.parents[1]
HARNESS = Path(__file__).parent / "php_join_rule_sql_test.php"
CLINIC_SCHEMA = REPO_ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard" / "database" / "schema.sql"
LABEL = "direct-SQL vs generated function, not PHP differential"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("C3_DB_PASSWORD") or shutil.which("php") is None,
    reason="C3_DB_PASSWORD not configured or PHP not available",
)

# (case id, SQL with {{placeholders}}, join form, ORDER BY makes row order deterministic)
CASES = {
    # LEFT JOIN with unmatched rows: doctor 3 has no appointments (COUNT 0, NULL-extended row).
    "left_count": ("SELECT d.full_name, COUNT(a.id) AS appointment_count FROM doctors d "
                   "LEFT JOIN appointments a ON a.doctor_id = d.id WHERE d.department_id = {{department}} "
                   "GROUP BY d.id, d.full_name ORDER BY d.id", "LEFT JOIN"),
    # LEFT JOIN returning NULL-extended columns: appointment 3 has no payment.
    "left_nulls": ("SELECT a.id AS appointment_id, a.status, p.amount, p.method, p.paid_at FROM appointments a "
                   "LEFT JOIN payments p ON p.appointment_id = a.id WHERE a.status <> '{{status}}' ORDER BY a.id",
                   "LEFT JOIN"),
    # implicit comma join; unqualified columns resolve to one table each
    "comma": ("SELECT a.id AS appointment_id, full_name, status, fee FROM patients, appointments a "
              "WHERE a.patient_id = patients.id AND a.status = '{{status}}' ORDER BY a.id", "comma join"),
    # static INNER JOIN regression (earlier rule)
    "inner": ("SELECT p.full_name, d.name AS department FROM patients p INNER JOIN departments d "
              "ON d.id = p.department_id WHERE p.id = {{id}} ORDER BY p.id", "INNER JOIN"),
}

PARAMETERS = [
    ("left_count", {"department": 1}), ("left_count", {"department": 2}), ("left_count", {"department": 999}),
    ("left_nulls", {"status": "pending"}), ("left_nulls", {"status": "none"}),
    ("comma", {"status": "confirmed"}), ("comma", {"status": "cancelled"}), ("comma", {"status": "missing"}),
    ("inner", {"id": 1}), ("inner", {"id": 3}), ("inner", {"id": 999}),
]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    schema = asdict(read_schema(CLINIC_SCHEMA))
    folder = tmp_path_factory.mktemp("join_rule")
    converted = {}
    for index, (case, (sql, _)) in enumerate(CASES.items(), start=1):
        query = {"query_id": f"Q{index:03d}", "status": "RECOVERED", "recovered_sql": sql, "operation": "SELECT",
                 "executor": "mysqli_query", "source_file": str(folder / f"{case}.php"), "source_line": 1}
        result = convert_query(query, schema)
        assert result["status"] == "CONVERTED", (case, result["reason"])
        converted[case] = result
    source = generate_data_access_source(list(converted.values()), folder)
    path = folder / "data_access.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("join_rule_generated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import e4_injection_check as e4

    by_id = {c["query_id"]: c for c in converted.values()}
    functions = {qid: function for qid, _, function, _ in e4.generated_functions(module, by_id)}
    return {case: functions[c["query_id"]] for case, c in converted.items()}


def run_sql_directly(case, values):
    env = {**os.environ, "C3_BENCH_DB_HOST": "127.0.0.1", "C3_BENCH_DB_USER": "c3_tester",
           "C3_BENCH_DB_PASSWORD": os.environ["C3_DB_PASSWORD"], "C3_BENCH_DB_NAME": "c3_clinic_legacy_test"}
    done = subprocess.run(["php", str(HARNESS), CASES[case][0], json.dumps(values)], capture_output=True, text=True,
                          encoding="utf-8", check=True, env=env)
    return json.loads(done.stdout.lstrip("﻿"))


def run_generated(function, values):
    engine = create_engine(URL.create("mysql+pymysql", username="c3_tester", password=os.environ["C3_DB_PASSWORD"],
                                      host="127.0.0.1", database="c3_clinic_migrated_test"))
    try:
        with Session(engine) as session:
            return [dict(row) for row in function(session, **values)]
    finally:
        engine.dispose()


def as_text(rows):
    """mysqli returns every value as a string (NULL as null); render the SQLAlchemy values the same way."""
    def text(value):
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ")
        if isinstance(value, Decimal):
            return format(value, "f")
        return str(value)
    return [[(key, text(value)) for key, value in row.items()] for row in rows]


@pytest.mark.parametrize("case,values", PARAMETERS)
def test_generated_join_function_matches_direct_sql(generated, case, values):
    direct = run_sql_directly(case, values)
    migrated = run_generated(generated[case], values)
    # Same rows, same keys in the same order, same values: the result.mappings() shape equals mysqli_fetch_assoc.
    assert as_text(migrated) == as_text(direct), f"{LABEL}: {case} {values}"


def test_left_join_returns_null_extended_rows(generated):
    rows = run_generated(generated["left_nulls"], {"status": "none"})
    assert [row["appointment_id"] for row in rows] == [1, 2, 3]
    unmatched = rows[2]
    assert (unmatched["amount"], unmatched["method"], unmatched["paid_at"]) == (None, None, None)
    counts = run_generated(generated["left_count"], {"department": 1})
    assert [(row["full_name"], row["appointment_count"]) for row in counts] == [("Dr A", 2), ("Dr C", 0)]


def test_hostile_value_is_bound_not_interpolated(generated):
    payload = "' OR '1'='1"
    direct = run_sql_directly("comma", {"status": payload})
    migrated = run_generated(generated["comma"], {"status": payload})
    assert len(direct) > 1  # the interpolated SQL is injectable
    assert migrated == []   # the generated function binds the value
