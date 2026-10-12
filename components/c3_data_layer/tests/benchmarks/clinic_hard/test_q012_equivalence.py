"""Q012 ordered PHP versus generated SQLAlchemy MySQL equivalence."""

import json
import os
import subprocess
from itertools import groupby
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
    return [
        {
            "id": int(row["id"]),
            "full_name": str(row["full_name"]),
        }
        for row in rows
    ]


def ordered_groups(rows, column):
    """Compare ordering by key, ignoring row order within tied groups."""
    return [
        (
            key,
            tuple(sorted(
                (row["id"], row["full_name"])
                for row in group
            )),
        )
        for key, group in groupby(
            rows, key=lambda row: row[column]
        )
    ]


@pytest.fixture(scope="module", autouse=True)
def require_database():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")


@pytest.fixture(scope="module")
def generated_q012():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 20

    q012 = next(q for q in converted if q["query_id"] == "Q012")

    assert q012["status"] == "CONVERTED"
    assert q012["query_shape"] == "ORDER_BY_SELECT"

    source = generate_data_access_source(converted)

    namespace = {}
    exec(compile(source, "<q012_pipeline>", "exec"), namespace)

    return namespace["select_patients_q012"]


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
            str(TEST_DIR / "php_q012_test.php"),
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
    "mode,value,inputs,column",
    [
        ("missing", "", {}, "full_name"),
        ("null", "", {"sort": None}, "full_name"),
        ("value", "id", {"sort": "id"}, "id"),
        ("value", "full_name", {"sort": "full_name"}, "full_name"),
    ],
)
def test_q012_full_pipeline_equivalence(
    mode, value, inputs, column, generated_q012
):
    php_rows = normalize(run_php(mode, value))

    engine = make_engine("c3_clinic_migrated_test")
    try:
        with Session(engine) as session:
            python_rows = normalize(generated_q012(session, inputs))
    finally:
        engine.dispose()

    assert php_rows, "Legacy patients dataset is empty."
    assert python_rows, "Migrated patients dataset is empty."

    # Check that both databases contain equivalent patient records.
    assert sorted(
        (row["id"], row["full_name"]) for row in php_rows
    ) == sorted(
        (row["id"], row["full_name"]) for row in python_rows
    )

    # Compare sort-key order while allowing nondeterministic ties.
    assert ordered_groups(php_rows, column) == ordered_groups(
        python_rows, column
    )
