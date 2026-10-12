"""Q013 PHP versus generated SQLAlchemy MySQL equivalence."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source


TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[5]

LEGACY_DIR = (
    PROJECT_ROOT
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module", autouse=True)
def require_database():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")


@pytest.fixture(scope="module")
def generated_q013():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 20

    q013 = next(q for q in converted if q["query_id"] == "Q013")

    assert q013["status"] == "CONVERTED"
    assert q013["query_shape"] == "TABLE_IDENTIFIER_SELECT"
    assert q013["allowed_tables"] == ["patients"]

    source = generate_data_access_source(converted)

    namespace = {}
    exec(compile(source, "<q013_pipeline>", "exec"), namespace)

    return namespace["select_q013"]


def make_engine():
    return create_engine(
        URL.create(
            "mysql+pymysql",
            username="c3_tester",
            password=os.environ["C3_DB_PASSWORD"],
            host="127.0.0.1",
            database="c3_clinic_migrated_test",
        )
    )


def run_php(mode, value):
    env = os.environ.copy()

    env.update({
        "C3_BENCH_DB_HOST": "127.0.0.1",
        "C3_BENCH_DB_USER": "c3_tester",
        "C3_BENCH_DB_PASSWORD": os.environ["C3_DB_PASSWORD"],
        "C3_BENCH_DB_NAME": "c3_clinic_legacy_test",
    })

    result = subprocess.run(
        [
            "php",
            str(TEST_DIR / "php_q013_test.php"),
            mode,
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    return json.loads(result.stdout.lstrip("\ufeff"))


def normalize(rows):
    return sorted(int(row["id"]) for row in rows)


@pytest.mark.parametrize(
    "mode,value,inputs",
    [
        ("missing", "", {}),
        ("null", "", {"table": None}),
        ("value", "patients", {"table": "patients"}),
    ],
)
def test_q013_full_pipeline_equivalence(
    mode, value, inputs, generated_q013
):
    php_rows = normalize(run_php(mode, value))

    engine = make_engine()

    try:
        with Session(engine) as session:
            python_rows = normalize(
                generated_q013(session, inputs)
            )
    finally:
        engine.dispose()

    # The benchmark must exercise an existing patient record.
    assert php_rows, "Legacy patient id=1 was not found."
    assert python_rows, "Migrated patient id=1 was not found."

    assert php_rows == python_rows
    assert php_rows == [1]
