
"""
C3 Data Layer Migration
Mixed CRUD - Migrated Python MySQL Validation
"""

import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[4]

DATA_ACCESS_FILE = (
    PROJECT_ROOT / "output" / "mixed_crud" / "data_access.py"
)

OUTPUT_FILE = (
    Path(__file__).resolve().parent / "mixed_migrated_results.json"
)


def normalize_products(rows):
    return [
        {
            "id": int(row["id"]),
            "title": row["title"],
            "price": f"{Decimal(str(row['price'])):.2f}",
        }
        for row in rows
    ]


def load_generated_module():
    if not DATA_ACCESS_FILE.is_file():
        raise FileNotFoundError(
            f"Generated data access file not found: {DATA_ACCESS_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "c3_mixed_generated",
        DATA_ACCESS_FILE,
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def main():
    password = os.environ.get("C3_DB_PASSWORD")

    if not password:
        raise RuntimeError("C3_DB_PASSWORD is not set.")

    url = URL.create(
        drivername="mysql+pymysql",
        username="c3_tester",
        password=password,
        host="localhost",
        database="c3_mixed_python",
    )

    engine = create_engine(url)

    expected_initial = [
        {"id": 1, "title": "Laptop", "price": "150000.00"},
        {"id": 2, "title": "Mouse", "price": "2500.00"},
        {"id": 3, "title": "Keyboard", "price": "7500.00"},
    ]

    try:
        generated = load_generated_module()

        with Session(engine) as session:
            print("Connected to c3_mixed_python successfully.")

            initial_rows = session.execute(
                text("SELECT id, title, price FROM products ORDER BY id")
            ).mappings().all()

            if normalize_products(initial_rows) != expected_initial:
                raise RuntimeError(
                    "Unexpected initial data. Database was not modified."
                )

            print("Initial dataset verified: 3 products.")

            # Q001 - SELECT all products
            print("\nQ001 - SELECT ALL")

            q001 = normalize_products(
                generated.select_products_q001(session)
            )
            q001.sort(key=lambda item: item["id"])

            assert q001 == expected_initial
            print(json.dumps(q001, indent=4))

            # Q002 - SELECT product by ID
            print("\nQ002 - SELECT BY ID")

            q002 = normalize_products(
                generated.select_products_q002(session, id=1)
            )

            assert len(q002) == 1
            assert q002[0]["title"] == "Laptop"

            print(json.dumps(q002, indent=4))

            # Q004 - Reassignment SELECT
            print("\nQ004 - REASSIGNMENT SELECT")

            generated.select_products_q004(session)

            q004 = normalize_products(
                session.execute(
                    text("SELECT id, title, price FROM products ORDER BY id")
                ).mappings().all()
            )

            assert q004 == expected_initial
            print("Q004 executed successfully.")

            # Q005 - UPDATE product
            print("\nQ005 - UPDATE PRODUCT")

            generated.update_product_q005(
                session,
                title="Laptop Updated",
                price="155000.00",
                id=1,
            )

            updated = session.execute(
                text(
                    "SELECT id, title, price "
                    "FROM products WHERE id = :id"
                ),
                {"id": 1},
            ).mappings().one()

            q005 = normalize_products([updated])[0]

            assert q005["title"] == "Laptop Updated"
            assert q005["price"] == "155000.00"

            print(json.dumps(q005, indent=4))

            # Q003 - DELETE product
            print("\nQ003 - DELETE PRODUCT")

            generated.delete_product_q003(session, id=2)

            deleted_count = session.execute(
                text("SELECT COUNT(*) FROM products WHERE id = :id"),
                {"id": 2},
            ).scalar_one()

            assert deleted_count == 0

            print("Mouse deleted successfully.")

            # Final database state
            print("\nFINAL DATABASE STATE")

            final_rows = normalize_products(
                session.execute(
                    text(
                        "SELECT id, title, price "
                        "FROM products ORDER BY id"
                    )
                ).mappings().all()
            )

            print(json.dumps(final_rows, indent=4))

            validation_results = {
                "implementation": "migrated_python",
                "benchmark": "mixed_crud",
                "q001": q001,
                "q002": q002,
                "q004_state": q004,
                "q005": q005,
                "q003_deleted": deleted_count == 0,
                "final_state": final_rows,
                "q006": "manual_review_required",
            }

            OUTPUT_FILE.write_text(
                json.dumps(validation_results, indent=4),
                encoding="utf-8",
            )

            print(f"\nResults saved to: {OUTPUT_FILE}")
            print("Mixed CRUD migrated Python validation completed.")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
