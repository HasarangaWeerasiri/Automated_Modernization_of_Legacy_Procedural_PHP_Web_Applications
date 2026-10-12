"""Q011 supported-input PHP versus generated SQLAlchemy equivalence."""

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


def make_engine(database):
    return create_engine(
        URL.create(
            "mysql+pymysql",
            username="c3_tester",
            password=os.environ["C3_DB_PASSWORD"],
            host="127.0.0.1",
            database=database,
        )
    )


def normalize(rows):
    return sorted(
        (int(row["id"]), str(row["full_name"]))
        for row in rows
    )


@pytest.fixture(scope="module", autouse=True)
def require_database():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")


@pytest.fixture(scope="module")
def generated_q011():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 18

    q011 = next(q for q in converted if q["query_id"] == "Q011")

    assert q011["status"] == "CONVERTED"
    assert q011["query_shape"] == "EXPANDING_IN_SELECT"

    source = generate_data_access_source(converted)

    namespace = {}
    exec(
        compile(source, "<q011_pipeline>", "exec"),
        namespace,
    )

    return namespace["select_patients_q011"]


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
            str(TEST_DIR / "php_q011_test.php"),
            mode,
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    return json.loads(result.stdout.lstrip("\ufeff"))


@pytest.mark.parametrize(
    "mode,value,inputs",
    [
        ("missing", "", {}),
        ("null", "", {"ids": None}),
        ("value", "1", {"ids": "1"}),
        ("value", "1,2", {"ids": "1,2"}),
        ("value", "2,3", {"ids": "2,3"}),
        ("value", "001,002", {"ids": "001,002"}),
    ],
)
def test_q011_full_pipeline_equivalence(
    mode,
    value,
    inputs,
    generated_q011,
):
    php_rows = run_php(mode, value)

    engine = make_engine("c3_clinic_migrated_test")

    try:
        with Session(engine) as session:
            python_rows = [
                dict(row)
                for row in generated_q011(session, inputs)
            ]
    finally:
        engine.dispose()

    assert normalize(php_rows) == normalize(python_rows)
