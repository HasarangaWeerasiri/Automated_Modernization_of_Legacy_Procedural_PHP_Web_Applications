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


@pytest.fixture(scope="module")
def main_pipeline_q014_function():
    """Generate executable Q014 through the real C3 pipeline."""

    from c3.mapping.sql_locator import analyze_php_ast
    from c3.mapping.sql_recovery import recover_queries_from_ast
    from c3.conversion.query_converter import convert_queries
    from c3.generation.emitter import generate_data_access_source

    legacy_dir = (
        COMPONENT_DIR
        / "../../benchmarks/apps/clinic_inventory_legacy_hard/legacy"
    ).resolve()

    assert legacy_dir.is_dir()

    ast_data = analyze_php_ast(legacy_dir)
    recovered = recover_queries_from_ast(ast_data)
    converted = convert_queries(recovered)

    q014 = next(
        q for q in converted
        if q["query_id"] == "Q014"
    )

    assert len(converted) == 21
    assert q014["status"] == "CONVERTED"
    assert q014["query_shape"] == "CONDITIONAL_SELECT"

    generated_source = generate_data_access_source(converted)

    namespace = {}
    exec(
        compile(
            generated_source,
            "<main_pipeline_data_access>",
            "exec",
        ),
        namespace,
    )

    return namespace["select_doctors_q014"]


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
def test_q014_full_pipeline_equivalence(
    mode,
    value,
    inputs,
    main_pipeline_q014_function,
):
    """Compare PHP results with full-pipeline-generated SQLAlchemy code."""

    php_rows = run_php(mode, value)

    engine = make_engine("c3_clinic_migrated_test")

    try:
        with Session(engine) as session:
            python_rows = [
                dict(row)
                for row in main_pipeline_q014_function(session, inputs)
            ]
    finally:
        engine.dispose()

    assert normalize(php_rows) == normalize(python_rows)
