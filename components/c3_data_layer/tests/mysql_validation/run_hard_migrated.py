
"""
C3 Data Layer Migration
Hard CRUD - Migrated Python LIKE Search Validation

Executes the generated SQLAlchemy function for Q001
against an isolated MySQL database.
"""

import importlib.util
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[4]

DATA_ACCESS_FILE = (
    PROJECT_ROOT / "output" / "hard_crud" / "data_access.py"
)

OUTPUT_FILE = (
    Path(__file__).resolve().parent / "hard_migrated_results.json"
)

SEARCH_TERMS = ["Ali", "Alex", "Bob", "Charlie", "xyz"]


def normalize_users(rows):
    normalized = [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "email": row["email"],
        }
        for row in rows
    ]

    return sorted(normalized, key=lambda user: user["id"])


def load_generated_module():
    if not DATA_ACCESS_FILE.is_file():
        raise FileNotFoundError(
            f"Generated data access file not found: {DATA_ACCESS_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "c3_hard_generated",
        DATA_ACCESS_FILE,
    )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    return module


def main():
    password = os.environ.get("C3_DB_PASSWORD")

    if not password:
        raise RuntimeError(
            "C3_DB_PASSWORD is not set."
        )

    url = URL.create(
        drivername="mysql+pymysql",
        username="c3_tester",
        password=password,
        host="localhost",
        database="c3_hard_python",
    )

    engine = create_engine(url)

    try:
        generated = load_generated_module()

        with Session(engine) as session:
            print(
                "Connected to c3_hard_python successfully."
            )

            count = session.execute(
                text("SELECT COUNT(*) FROM users")
            ).scalar_one()

            if count != 5:
                raise RuntimeError(
                    "Expected exactly 5 test users."
                )

            results = {}

            for term in SEARCH_TERMS:
                print(f"\nSearching for: {term}")

                rows = generated.select_users_q001(
                    session,
                    q=term,
                )

                normalized = normalize_users(rows)
                results[term] = normalized

                print(
                    json.dumps(normalized, indent=4)
                )

            report = {
                "implementation": "migrated_python",
                "benchmark": "hard_crud",
                "query_id": "Q001",
                "search_results": results,
            }

            OUTPUT_FILE.write_text(
                json.dumps(report, indent=4),
                encoding="utf-8",
            )

            print(
                f"\nResults saved to: {OUTPUT_FILE}"
            )
            print(
                "Hard CRUD migrated Python validation completed."
            )

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
