import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from c3.validation.behavior_validator import compare_database_state


PROJECT_ROOT = Path(__file__).resolve().parents[3]

DATA_ACCESS_FILE = (
    PROJECT_ROOT
    / "output"
    / "simple_crud"
    / "data_access.py"
)


def load_generated_data_access():
    """
    Load the generated data_access.py file as a Python module.

    This allows the tests to execute the actual code produced
    by the Data Layer Migration component.
    """
    spec = importlib.util.spec_from_file_location(
        "generated_data_access",
        DATA_ACCESS_FILE,
    )

    module = importlib.util.module_from_spec(spec)

    assert spec.loader is not None
    spec.loader.exec_module(module)

    return module


@pytest.fixture
def engine():
    """
    Create an isolated in-memory SQLite database for each test.
    """

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(150) NOT NULL,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
        )

    yield test_engine

    test_engine.dispose()


@pytest.fixture
def data_access():
    """
    Load the generated data-access module.
    """
    return load_generated_data_access()


def test_generated_data_access_file_exists():
    assert DATA_ACCESS_FILE.exists()


def test_generated_module_loads():
    module = load_generated_data_access()

    assert hasattr(module, "insert_user_q001")
    assert hasattr(module, "select_users_q004")
    assert hasattr(module, "update_user_q003")
    assert hasattr(module, "delete_user_q002")


def test_insert_behavior(engine, data_access):
    with Session(engine) as session:
        data_access.insert_user_q001(
            session,
            name="Alice",
            email="alice@example.com",
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id, name, email, status
                FROM users
                WHERE email = :email
                """
            ),
            {
                "email": "alice@example.com",
            },
        ).mappings().one()

    assert row["name"] == "Alice"
    assert row["email"] == "alice@example.com"
    assert row["status"] == "active"


def test_select_behavior(engine, data_access):
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    name,
                    email,
                    status
                )
                VALUES (
                    :name,
                    :email,
                    :status
                )
                """
            ),
            {
                "name": "Bob",
                "email": "bob@example.com",
                "status": "active",
            },
        )

    with Session(engine) as session:
        rows = data_access.select_users_q004(session)

    assert len(rows) == 1
    assert rows[0]["name"] == "Bob"
    assert rows[0]["email"] == "bob@example.com"
    assert rows[0]["status"] == "active"


def test_update_behavior(engine, data_access):
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                INSERT INTO users (
                    name,
                    email,
                    status
                )
                VALUES (
                    :name,
                    :email,
                    :status
                )
                """
            ),
            {
                "name": "Charlie",
                "email": "charlie@example.com",
                "status": "active",
            },
        )

        user_id = result.lastrowid

    with Session(engine) as session:
        data_access.update_user_q003(
            session,
            name="Charlie Updated",
            id=user_id,
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT name, email, status
                FROM users
                WHERE id = :id
                """
            ),
            {
                "id": user_id,
            },
        ).mappings().one()

    assert row["name"] == "Charlie Updated"
    assert row["email"] == "charlie@example.com"
    assert row["status"] == "active"


def test_delete_behavior(engine, data_access):
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                INSERT INTO users (
                    name,
                    email,
                    status
                )
                VALUES (
                    :name,
                    :email,
                    :status
                )
                """
            ),
            {
                "name": "David",
                "email": "david@example.com",
                "status": "active",
            },
        )

        user_id = result.lastrowid

    with Session(engine) as session:
        data_access.delete_user_q002(
            session,
            id=user_id,
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id
                FROM users
                WHERE id = :id
                """
            ),
            {
                "id": user_id,
            },
        ).first()

    assert row is None


def test_complete_crud_sequence(engine, data_access):
    """
    Execute a complete migrated CRUD lifecycle:

    INSERT -> SELECT -> UPDATE -> DELETE
    """

    # INSERT
    with Session(engine) as session:
        data_access.insert_user_q001(
            session,
            name="Eve",
            email="eve@example.com",
        )

    # SELECT
    with Session(engine) as session:
        rows = data_access.select_users_q004(session)

    assert len(rows) == 1

    user_id = rows[0]["id"]

    assert rows[0]["name"] == "Eve"
    assert rows[0]["email"] == "eve@example.com"
    assert rows[0]["status"] == "active"

    # UPDATE
    with Session(engine) as session:
        data_access.update_user_q003(
            session,
            name="Eve Updated",
            id=user_id,
        )

    with engine.connect() as connection:
        updated_row = connection.execute(
            text(
                """
                SELECT name
                FROM users
                WHERE id = :id
                """
            ),
            {
                "id": user_id,
            },
        ).mappings().one()

    assert updated_row["name"] == "Eve Updated"

    # DELETE
    with Session(engine) as session:
        data_access.delete_user_q002(
            session,
            id=user_id,
        )

    with engine.connect() as connection:
        remaining = connection.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM users
                """
            )
        ).mappings().one()

    assert remaining["count"] == 0


def test_parameterized_insert_treats_sql_payload_as_data(
    engine,
    data_access,
):
    """
    Verify that a SQL-injection-like value is handled as data
    by the generated parameterized data-access function.
    """

    malicious_name = "Robert'); DROP TABLE users;--"

    with Session(engine) as session:
        data_access.insert_user_q001(
            session,
            name=malicious_name,
            email="robert@example.com",
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT name, email, status
                FROM users
                WHERE email = :email
                """
            ),
            {
                "email": "robert@example.com",
            },
        ).mappings().one()

    assert row["name"] == malicious_name
    assert row["email"] == "robert@example.com"
    assert row["status"] == "active"

    with engine.connect() as connection:
        count = connection.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM users
                """
            )
        ).mappings().one()

    assert count["count"] == 1


def test_insert_constraint_failure_is_not_silently_ignored(
    engine,
    data_access,
):
    """
    Verify that database constraint failures are propagated.
    """

    with pytest.raises(Exception):
        with Session(engine) as session:
            data_access.insert_user_q001(
                session,
                name=None,
                email="invalid@example.com",
            )

    with engine.connect() as connection:
        count = connection.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM users
                """
            )
        ).mappings().one()

    assert count["count"] == 0


def test_generated_crud_matches_expected_final_state(
    engine,
    data_access,
):
    """
    Execute generated CRUD operations and compare the resulting
    database state with the expected state using the behavioral
    validator.
    """

    # INSERT Alice and Bob using generated code.
    with Session(engine) as session:
        data_access.insert_user_q001(
            session,
            name="Alice",
            email="alice@example.com",
        )

        data_access.insert_user_q001(
            session,
            name="Bob",
            email="bob@example.com",
        )

    # Obtain generated IDs.
    with Session(engine) as session:
        rows = data_access.select_users_q004(session)

    alice = next(
        row
        for row in rows
        if row["email"] == "alice@example.com"
    )

    bob = next(
        row
        for row in rows
        if row["email"] == "bob@example.com"
    )

    # UPDATE Alice using generated code.
    with Session(engine) as session:
        data_access.update_user_q003(
            session,
            name="Alice Updated",
            id=alice["id"],
        )

    # DELETE Bob using generated code.
    with Session(engine) as session:
        data_access.delete_user_q002(
            session,
            id=bob["id"],
        )

    # Obtain the actual final state using generated SELECT.
    with Session(engine) as session:
        actual_rows = data_access.select_users_q004(session)

    # Define the expected final state.
    expected_rows = [
        {
            "id": alice["id"],
            "name": "Alice Updated",
            "email": "alice@example.com",
            "status": "active",
        }
    ]

    # Compare expected and actual states.
    result = compare_database_state(
        expected_rows,
        actual_rows,
        key="id",
    )

    assert result.equivalent is True
    assert result.expected_row_count == 1
    assert result.actual_row_count == 1
    assert result.differences == []