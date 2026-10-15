import importlib.util
import json
import os
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from clinic_generated_output import generated_data_access_file
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


COMPONENT_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = COMPONENT_DIR.parents[1]
TEST_DIR = Path(__file__).resolve().parent

# Generated fresh per test session (not read from output/).

PHP_HARNESS = TEST_DIR / "php_write_transaction_test.php"


CHECK_QUERIES = {
    "Q003": """
        SELECT department_id, full_name, email, created_at
        FROM patients
        WHERE email = 'c3-transaction@example.test'
    """,
    "Q004": """
        SELECT id, phone
        FROM patients
        WHERE id = 1
    """,
    "Q005": """
        SELECT id, appointment_id, amount
        FROM payments
        ORDER BY id
    """,
    "Q017": """
        SELECT id, fee
        FROM appointments
        ORDER BY id
    """,
}


def load_generated_module():
    spec = importlib.util.spec_from_file_location(
        "clinic_generated_writes",
        generated_data_access_file(),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_rows(rows):
    numeric_fields = {"id", "department_id", "appointment_id"}
    decimal_fields = {"amount", "fee", "fees"}

    normalized = []

    for row in rows:
        item = dict(row)

        for key, value in item.items():
            if value is None:
                continue

            if key in numeric_fields:
                item[key] = int(value)

            elif key in decimal_fields:
                item[key] = str(Decimal(str(value)))

            elif isinstance(value, (datetime, date)):
                item[key] = str(value)

        normalized.append(item)

    return sorted(
        normalized,
        key=lambda row: json.dumps(row, sort_keys=True),
    )


def run_php_write(query_id):
    env = os.environ.copy()
    env.update({
        "C3_BENCH_DB_HOST": "127.0.0.1",
        "C3_BENCH_DB_USER": "c3_tester",
        "C3_BENCH_DB_PASSWORD": os.environ["C3_DB_PASSWORD"],
        "C3_BENCH_DB_NAME": "c3_clinic_legacy_test",
    })

    result = subprocess.run(
        ["php", str(PHP_HARNESS), query_id],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        check=True,
        env=env,
    )

    return json.loads(result.stdout)


def run_python_write(query_id):
    module = load_generated_module()

    engine = create_engine(
        URL.create(
            "mysql+pymysql",
            username="c3_tester",
            password=os.environ["C3_DB_PASSWORD"],
            host="127.0.0.1",
            database="c3_clinic_migrated_test",
        )
    )

    try:
        with engine.connect() as connection:
            outer_transaction = connection.begin()

            try:
                # The generated functions commit their sessions.
                # Keep those commits inside SAVEPOINTs so the
                # outer transaction can still roll back.
                with Session(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                ) as session:

                    if query_id == "Q003":
                        result = module.insert_patient_q003(
                            session,
                            department=1,
                            name="C3 Transaction Patient",
                            email="c3-transaction@example.test",
                        )

                    elif query_id == "Q004":
                        result = module.update_patient_q004(
                            session,
                            phone="0799999999",
                            id=1,
                        )

                    elif query_id == "Q005":
                        result = module.delete_payment_q005(
                            session,
                            id=1,
                        )

                    elif query_id == "Q017":
                        result = module.update_appointment_q017(
                            session,
                            delta="7.25",
                            id=1,
                        )

                    else:
                        raise ValueError(query_id)

                    affected_rows = result.rowcount

                # Read inside the outer transaction before rollback.
                rows = connection.execute(
                    text(CHECK_QUERIES[query_id])
                ).mappings().all()

                return {
                    "query_id": query_id,
                    "affected_rows": affected_rows,
                    "rows": [dict(row) for row in rows],
                }

            finally:
                if outer_transaction.is_active:
                    outer_transaction.rollback()

    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "query_id",
    ["Q003", "Q004", "Q005", "Q017"],
)
def test_php_python_write_equivalence(query_id):
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")

    php_result = run_php_write(query_id)
    python_result = run_python_write(query_id)

    assert php_result["affected_rows"] == python_result["affected_rows"]

    assert normalize_rows(php_result["rows"]) == normalize_rows(
        python_result["rows"]
    )