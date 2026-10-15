"""Rollback-safe MySQL execution test for Q020."""

import os
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


LEGACY_DIR = (
    Path(__file__).resolve().parents[5]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.mark.skipif(
    not os.environ.get("C3_DB_PASSWORD"),
    reason="MySQL test credentials not configured",
)
def test_q020_update_and_rollback():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 22  # 22 Clinic sites (S22 mysqli_prepare detected since S2)
    # Every site except the prepared statement (S22, detected since S2) converts.
    assert sum(q["status"] == "CONVERTED" for q in converted) == 22  # +Q022 (S14)

    namespace = {}
    source = generate_data_access_source(converted)
    exec(compile(source, "<q020_generated>", "exec"), namespace)

    update_q020 = namespace["update_appointment_q020"]

    engine = create_engine(
        URL.create(
            "mysql+pymysql",
            username="c3_tester",
            password=os.environ["C3_DB_PASSWORD"],
            host="127.0.0.1",
            database="c3_clinic_migrated_test",
        )
    )

    appointment_sql = text(
        "SELECT a.id, a.fee, d.hourly_rate "
        "FROM appointments a "
        "JOIN doctors d ON d.id = a.doctor_id "
        "ORDER BY a.id LIMIT 1"
    )

    fee_sql = text(
        "SELECT fee FROM appointments WHERE id = :id"
    )

    try:
        with engine.connect() as connection:
            appointment = connection.execute(
                appointment_sql
            ).mappings().first()

        assert appointment is not None, (
            "No appointment available for Q020 verification."
        )

        appointment_id = int(appointment["id"])
        original_fee = Decimal(str(appointment["fee"]))
        doctor_rate = Decimal(str(appointment["hourly_rate"]))

        # Make the fee different to guarantee an observable UPDATE.
        temporary_fee = doctor_rate + Decimal("1.00")

        with Session(engine) as session:
            try:
                session.execute(
                    text(
                        "UPDATE appointments SET fee = :fee "
                        "WHERE id = :id"
                    ),
                    {"fee": temporary_fee, "id": appointment_id},
                )

                before_update = session.execute(
                    fee_sql, {"id": appointment_id}
                ).scalar_one()

                assert Decimal(str(before_update)) == temporary_fee

                affected = update_q020(
                    session, {"id": str(appointment_id)}
                )

                after_update = session.execute(
                    fee_sql, {"id": appointment_id}
                ).scalar_one()

                assert affected == 1
                assert Decimal(str(after_update)) == doctor_rate

            finally:
                # Always roll back, including on failed assertions.
                session.rollback()

        # Check from a new connection after the rollback.
        with engine.connect() as connection:
            restored_fee = connection.execute(
                fee_sql, {"id": appointment_id}
            ).scalar_one()

        assert Decimal(str(restored_fee)) == original_fee

        print(
            f"\nQ020 appointment {appointment_id}: "
            "UPDATE verified; rollback verified."
        )

    finally:
        engine.dispose()
