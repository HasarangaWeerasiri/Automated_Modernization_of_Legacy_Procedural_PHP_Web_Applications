
"""
C3 Data Layer Migration
Migrated Python Behavioral Validation

Executes generated SQLAlchemy CRUD functions
against an isolated MySQL validation database.
"""

import importlib.util
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


# Locate the generated C3 output.
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATA_ACCESS_FILE = (
    PROJECT_ROOT / "output" / "simple_crud" / "data_access.py"
)


def load_generated_module():
    if not DATA_ACCESS_FILE.is_file():
        raise FileNotFoundError(
            f"Generated data access file not found: {DATA_ACCESS_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "c3_generated_data_access",
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
        database="c3_python_validation",
    )

    engine = create_engine(url)

    try:
        generated = load_generated_module()

        with Session(engine) as session:
            print("Connected to MySQL successfully.")

            initial_count = session.execute(
                text("SELECT COUNT(*) FROM users")
            ).scalar_one()

            if initial_count != 0:
                raise RuntimeError(
                    "Validation database is not empty. "
                    "Aborting to protect existing data."
                )


            print("\n1. CREATE USER")

            insert_result = generated.insert_user_q001(
                session,
                name="Alice",
                email="alice@example.com",
            )

            user_id = insert_result.lastrowid
            print(f"Created user ID: {user_id}")

            created_user = session.execute(
                text(
                    "SELECT id, name, email, status "
                    "FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).mappings().one()

            assert created_user["name"] == "Alice"
            assert created_user["email"] == "alice@example.com"
            assert created_user["status"] == "active"

            print("CREATE verified against MySQL.")



            print("\n2. READ USERS")

            users = generated.select_users_q004(session)

            for user in users:
                print(json.dumps(dict(user)))

            print("\n3. UPDATE USER")

            generated.update_user_q003(
                session,
                name="Alice Updated",
                id=user_id,
            )

            updated_user = session.execute(
                text(
                    "SELECT id, name, email, status "
                    "FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).mappings().one()

            assert updated_user["name"] == "Alice Updated"
            print(f"Updated user ID: {user_id}")
            print(json.dumps(dict(updated_user)))

            print("\n4. DELETE USER")

            generated.delete_user_q002(
                session,
                id=user_id,
            )

            print(f"Deleted user ID: {user_id}")

            print("\n5. FINAL DATABASE STATE")

            final_rows = session.execute(
                text(
                    "SELECT id, name, email, status "
                    "FROM users ORDER BY id"
                )
            ).mappings().all()

            print(json.dumps(
                [dict(row) for row in final_rows],
                indent=2,
            ))


            assert len(final_rows) == 0

            # Save results for automated behavioral comparison.
            validation_results = {
                "implementation": "migrated_python",
                "create": {
                    "name": "Alice",
                    "email": "alice@example.com",
                    "status": "active",
                },
                "create": {
    "name": created_user["name"],
    "email": created_user["email"],
    "status": created_user["status"],
},
                "read": {
                    "name": users[0]["name"],
                    "email": users[0]["email"],
                    "status": users[0]["status"],
                },
                "update": {
                    "name": updated_user["name"],
                    "email": updated_user["email"],
                    "status": updated_user["status"],
                },
                "delete": len(final_rows) == 0,
                "final_state": [dict(row) for row in final_rows],
            }

            output_file = (
                Path(__file__).resolve().parent
                / "migrated_results.json"
            )

            output_file.write_text(
                json.dumps(validation_results, indent=4),
                encoding="utf-8",
            )

            print(f"\nValidation results saved to: {output_file}")
            print("Migrated Python CRUD execution completed.")


    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
