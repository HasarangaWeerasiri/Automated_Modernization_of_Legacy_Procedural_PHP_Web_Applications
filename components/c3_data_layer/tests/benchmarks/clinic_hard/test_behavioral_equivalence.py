import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from datetime import date, datetime
from decimal import Decimal

# components/c3_data_layer
COMPONENT_DIR = Path(__file__).resolve().parents[3]

# Repository root
REPO_ROOT = COMPONENT_DIR.parents[1]

HARNESS_DIR = Path(__file__).parent

PHP_HARNESSES = {
    "Q001": "php_q001_q002_test.php",
    "Q002": "php_q001_q002_test.php",
    "Q006": "php_q006_q009_test.php",
    "Q007": "php_q006_q009_test.php",
    "Q008": "php_q008_test.php",
    "Q009": "php_q006_q009_test.php",
    "Q015": "php_q015_test.php",
    "Q018": "php_q017_q021_test.php",
    "Q019": "php_q017_q021_test.php",
    "Q021": "php_q017_q021_test.php",
}

GENERATED_FILE = (
    REPO_ROOT
    / "output"
    / "clinic_inventory_legacy_hard"
    / "data_access.py"
)


def load_generated_module():
    spec = importlib.util.spec_from_file_location(
        "clinic_generated_data_access",
        GENERATED_FILE,
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def normalize_rows(rows):
    """Normalize MySQL result types for behavioral comparison."""

    numeric_fields = {
        "id",
        "doctor_id",
        "total",
    }

    decimal_fields = {
        "fees",
        "fee",
        "hourly_rate",
    }

    normalized = []

    for row in rows:
        item = dict(row)

        for key, value in item.items():
            if value is None:
                continue

            if key in numeric_fields:
                item[key] = int(value)

            elif key in decimal_fields:
                item[key] = Decimal(str(value))

            elif isinstance(value, (datetime, date)):
                item[key] = value.isoformat(sep=" ")

        normalized.append(item)

    return sorted(
        normalized,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )


def run_php(query_id, *args):
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
            str(HARNESS_DIR / PHP_HARNESSES[query_id]),
            *(
                list(map(str, args))
                if query_id in ("Q008", "Q015")
                else [query_id, *map(str, args)]
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=env,
    )

    return json.loads(result.stdout.lstrip("\ufeff"))


def run_python(query_id, *args):
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
        with Session(engine) as session:

            if query_id == "Q001":
                rows = module.select_patients_q001(session)

            elif query_id == "Q002":
                rows = module.select_patients_q002(
                    session,
                    id=args[0],
                )

            elif query_id == "Q006":
                rows = module.select_patients_q006(
                    session,
                    q=args[0],
                )

            elif query_id == "Q007":
                rows = module.select_appointments_q007(
                    session
                )

            elif query_id == "Q008":
                rows = module.select_appointments_q008(
                    session,
                    patient=args[0],
                )
            elif query_id == "Q009":
                rows = module.select_appointments_q009(
                    session
                )

            elif query_id == "Q015":
                from c3.conversion.input_binding import (
                    validate_numeric_identifier,
                )

                department_id = validate_numeric_identifier(
                    str(args[0]) if args else "1"
                )

                rows = module.select_doctors_q015(
                    session,
                    departmentId=department_id,
                )

            elif query_id == "Q018":
                rows = module.select_doctors_q018(
                    session,
                    minimum=args[0],
                )

            elif query_id == "Q019":
                rows = module.select_patients_q019(
                    session,
                    limit=args[0],
                    offset=args[1],
                )

            elif query_id == "Q021":
                rows = module.select_patients_q021(
                    session
                )

            else:
                raise ValueError(
                    f"Unsupported query: {query_id}"
                )

            return [dict(row) for row in rows]

    finally:
        engine.dispose()

@pytest.mark.parametrize(
    "query_id,args",
    [
        ("Q001", ()),
        ("Q002", (1,)),
        ("Q002", (2,)),
        ("Q002", (3,)),
        ("Q002", (999,)),
        ("Q006", ("Ali",)),
        ("Q006", ("Bob",)),
        ("Q006", ("Dina",)),
        ("Q006", ("XYZ",)),
        ("Q007", ()),
        ("Q008", (1,)),
        ("Q008", (2,)),
        ("Q008", (999,)),
        ("Q009", ()),
        ("Q015", ()),
        ("Q015", ("1",)),
        ("Q015", ("2",)),
        ("Q015", ("0",)),
        ("Q018", (100,)),
        ("Q018", (150,)),
        ("Q019", (2, 0)),
        ("Q019", (2, 1)),
        ("Q021", ()),
    ],
)

def test_php_python_equivalence(query_id, args):
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")

    php_rows = run_php(query_id, *args)
    python_rows = run_python(query_id, *args)

    assert normalize_rows(php_rows) == normalize_rows(python_rows)

def test_q002_sql_injection_is_blocked():
    if not os.environ.get("C3_DB_PASSWORD"):
        pytest.skip("C3_DB_PASSWORD not configured")

    payload = "' OR '1'='1"

    legacy_rows = run_php("Q002", payload)
    migrated_rows = run_python("Q002", payload)

    assert len(legacy_rows) > 1
    assert migrated_rows == []
