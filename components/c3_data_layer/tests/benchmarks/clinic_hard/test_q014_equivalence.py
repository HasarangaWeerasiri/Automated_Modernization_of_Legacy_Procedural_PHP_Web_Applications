"""Q014 PHP versus SQLAlchemy database equivalence tests."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from c3.generation.branch_emitter import generate_conditional_select_source


TEST_DIR = Path(__file__).resolve().parent
COMPONENT_DIR = Path(__file__).resolve().parents[3]
IR_FILE = COMPONENT_DIR / "tests/fixtures/q014_branch_diagnostic.json"


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


def run_php(mode, value=""):
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
            str(TEST_DIR / "php_q014_test.php"),
            mode,
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    return json.loads(result.stdout.lstrip("\ufeff"))


def run_python(inputs):
    ir = json.loads(IR_FILE.read_text(encoding="utf-8"))
    source = generate_conditional_select_source(ir)

    namespace = {}
    exec(compile(source, "<q014_generated>", "exec"), namespace)
    function = namespace["select_doctors_q014"]

    engine = make_engine("c3_clinic_migrated_test")

    try:
        with Session(engine) as session:
            return [dict(row) for row in function(session, inputs)]
    finally:
        engine.dispose()


def normalize(rows):
    return sorted(
        [(int(row["id"]), str(row["full_name"])) for row in rows]
    )


@pytest.fixture(scope="module", autouse=True)
def verify_database_setup():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")

    for name in ("c3_clinic_legacy_test", "c3_clinic_migrated_test"):
        engine = make_engine(name)

        try:
            with engine.connect() as connection:
                counts = connection.execute(
                    text(
                        "SELECT active, COUNT(*) "
                        "FROM doctors GROUP BY active"
                    )
                ).all()

            status_counts = {
                int(active): int(count)
                for active, count in counts
            }

            assert status_counts.get(0, 0) > 0, (
                f"{name}: no inactive doctors"
            )
            assert status_counts.get(1, 0) > 0, (
                f"{name}: no active doctors"
            )
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    "mode,value,inputs",
    [
        ("missing", "", {}),
        ("null", "", {"active": None}),
        ("value", "1", {"active": "1"}),
        ("value", "0", {"active": "0"}),
        ("value", "", {"active": ""}),
        ("value", "false", {"active": "false"}),
    ],
)
def test_q014_equivalence(mode, value, inputs):
    php_rows = run_php(mode, value)
    python_rows = run_python(inputs)

    assert normalize(php_rows) == normalize(python_rows)
