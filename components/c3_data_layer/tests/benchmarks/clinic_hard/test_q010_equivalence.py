"""Q010 full-pipeline PHP versus SQLAlchemy MySQL equivalence."""

import os
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
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
def check_databases():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")

    # Prevent an empty result set from passing equivalence trivially.
    for name in ("c3_clinic_legacy_test", "c3_clinic_migrated_test"):
        engine = make_engine(name)

        try:
            with engine.connect() as connection:
                count = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM patients "
                        "WHERE id IN ("
                        "SELECT patient_id FROM appointments "
                        "WHERE status = 'confirmed')"
                    )
                ).scalar_one()

            assert count > 0, f"{name}: Q010 returned no test rows"
        finally:
            engine.dispose()


@pytest.fixture(scope="module")
def generated_q010():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    q010 = next(q for q in converted if q["query_id"] == "Q010")

    assert q010["status"] == "CONVERTED"
    assert q010["table"] == "patients"
    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 21

    source = generate_data_access_source(converted)

    namespace = {}
    exec(compile(source, "<generated_q010_pipeline>", "exec"), namespace)

    return namespace["select_patients_q010"]


def test_q010_php_sqlalchemy_equivalence(generated_q010):
    env = os.environ.copy()
    env.update({
        "C3_BENCH_DB_HOST": "127.0.0.1",
        "C3_BENCH_DB_USER": "c3_tester",
        "C3_BENCH_DB_PASSWORD": os.environ["C3_DB_PASSWORD"],
        "C3_BENCH_DB_NAME": "c3_clinic_legacy_test",
    })

    php_result = subprocess.run(
        ["php", str(TEST_DIR / "php_q010_test.php")],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    php_rows = json.loads(php_result.stdout.lstrip("\ufeff"))

    engine = make_engine("c3_clinic_migrated_test")

    try:
        with Session(engine) as session:
            python_rows = [
                dict(row)
                for row in generated_q010(session)
            ]
    finally:
        engine.dispose()

    assert normalize(php_rows) == normalize(python_rows)
    assert len(php_rows) > 0
