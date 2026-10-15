"""Q020 transactional PHP versus SQLAlchemy equivalence."""

import json
import os
import subprocess
from decimal import Decimal
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
LEGACY_DIR = (
    Path(__file__).resolve().parents[5]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)

APPOINTMENT_QUERY = text(
    "SELECT a.id, a.fee, d.hourly_rate "
    "FROM appointments a "
    "JOIN doctors d ON d.id = a.doctor_id "
    "ORDER BY a.id LIMIT 1"
)

FEE_QUERY = text(
    "SELECT fee FROM appointments WHERE id = :id"
)


def engine_for(database):
    return create_engine(
        URL.create(
            "mysql+pymysql",
            username="c3_tester",
            password=os.environ["C3_DB_PASSWORD"],
            host="127.0.0.1",
            database=database,
        )
    )


def fee(value):
    return Decimal(str(value))


@pytest.mark.skipif(
    not os.environ.get("C3_DB_PASSWORD"),
    reason="MySQL credentials are not configured",
)
def test_q020_php_sqlalchemy_equivalence():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 22  # 22 Clinic sites (S22 mysqli_prepare detected since S2)
    # Every site except the prepared statement (S22, detected since S2) converts.
    assert sum(q["status"] == "CONVERTED" for q in converted) == 22  # +Q022 (S14)

    namespace = {}
    source = generate_data_access_source(converted)
    exec(compile(source, "<q020>", "exec"), namespace)

    generated_update = namespace["update_appointment_q020"]

    legacy_engine = engine_for("c3_clinic_legacy_test")
    migrated_engine = engine_for("c3_clinic_migrated_test")

    try:
        with legacy_engine.connect() as connection:
            legacy_before = connection.execute(
                APPOINTMENT_QUERY
            ).mappings().first()

        assert legacy_before is not None

        appointment_id = int(legacy_before["id"])

        with migrated_engine.connect() as connection:
            migrated_before = connection.execute(
                text(
                    "SELECT a.id, a.fee, d.hourly_rate "
                    "FROM appointments a "
                    "JOIN doctors d ON d.id = a.doctor_id "
                    "WHERE a.id = :id"
                ),
                {"id": appointment_id},
            ).mappings().first()

        assert migrated_before is not None
        assert fee(legacy_before["fee"]) == fee(migrated_before["fee"])
        assert fee(legacy_before["hourly_rate"]) == fee(
            migrated_before["hourly_rate"]
        )

        env = os.environ.copy()
        env["C3_BENCH_DB_PASSWORD"] = os.environ["C3_DB_PASSWORD"]

        php_process = subprocess.run(
            [
                "php",
                str(TEST_DIR / "php_q020_test.php"),
                str(appointment_id),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

        php_result = json.loads(php_process.stdout.strip())

        with Session(migrated_engine) as session:
            try:
                doctor_rate = fee(migrated_before["hourly_rate"])
                temporary_fee = doctor_rate + Decimal("1.00")

                session.execute(
                    text(
                        "UPDATE appointments SET fee = :fee "
                        "WHERE id = :id"
                    ),
                    {
                        "fee": temporary_fee,
                        "id": appointment_id,
                    },
                )

                affected = generated_update(
                    session, {"id": str(appointment_id)}
                )

                updated_fee = fee(
                    session.execute(
                        FEE_QUERY, {"id": appointment_id}
                    ).scalar_one()
                )
            finally:
                session.rollback()

        assert php_result["appointment_id"] == appointment_id
        assert fee(php_result["original_fee"]) == fee(
            migrated_before["fee"]
        )
        assert fee(php_result["doctor_rate"]) == fee(
            migrated_before["hourly_rate"]
        )
        assert fee(php_result["updated_fee"]) == updated_fee
        assert affected == php_result["affected_rows"] == 1
        assert updated_fee == fee(migrated_before["hourly_rate"])

        # Both databases must retain their original fees.
        with legacy_engine.connect() as connection:
            legacy_after = fee(
                connection.execute(
                    FEE_QUERY, {"id": appointment_id}
                ).scalar_one()
            )

        with migrated_engine.connect() as connection:
            migrated_after = fee(
                connection.execute(
                    FEE_QUERY, {"id": appointment_id}
                ).scalar_one()
            )

        assert legacy_after == fee(legacy_before["fee"])
        assert migrated_after == fee(migrated_before["fee"])

        print(
            f"\nQ020 appointment {appointment_id}: "
            "PHP and SQLAlchemy UPDATE equivalent; "
            "both transactions rolled back."
        )

    finally:
        legacy_engine.dispose()
        migrated_engine.dispose()
