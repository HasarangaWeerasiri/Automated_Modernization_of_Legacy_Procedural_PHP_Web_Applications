"""
Automated FastAPI integration tests for C3.

Uses the actual C3-generated data-access module
with an isolated SQLite database.
Does not modify existing MySQL benchmark databases.
"""

import importlib.util

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


@pytest.fixture
def test_environment(tmp_path, monkeypatch):
    # app.py requires this variable at import time.
    # This is a dummy value, not a real database password.
    monkeypatch.setenv("C3_DB_PASSWORD", "test_only_dummy")

    spec = importlib.util.spec_from_file_location(
        "c3_backend_test_app",
        __file__.replace("test_app.py", "app.py"),
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Create a fresh SQLite database for every test.
    database_file = tmp_path / "c3_test.db"

    test_engine = create_engine(
        f"sqlite+pysqlite:///{database_file}"
    )

    with test_engine.begin() as connection:
        connection.execute(
            text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100),
                    email VARCHAR(150),
                    status VARCHAR(30)
                )
            """)
        )

    # Replace only the test application's engine.
    # The MySQL benchmark database is never accessed.
    monkeypatch.setattr(module, "engine", test_engine)

    with TestClient(module.app) as client:
        yield client, module, test_engine

    test_engine.dispose()


def test_health_endpoint(test_environment):
    client, _, _ = test_environment

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["component"] == "C3"


def test_users_endpoint_with_records(test_environment):
    client, _, engine = test_environment

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO users
                    (id, name, email, status)
                VALUES
                    (1, 'Alice', 'alice@example.com', 'active'),
                    (2, 'Bob', 'bob@example.com', 'active')
            """)
        )

    response = client.get("/users")

    assert response.status_code == 200

    data = response.json()

    assert data["component"] == "C3"
    assert data["source"] == "generated_data_access"
    assert data["count"] == 2

    users = sorted(data["users"], key=lambda row: row["id"])

    assert users[0]["name"] == "Alice"
    assert users[1]["name"] == "Bob"


def test_users_endpoint_empty_database(test_environment):
    client, _, _ = test_environment

    response = client.get("/users")

    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert response.json()["users"] == []


def test_health_database_failure(test_environment, monkeypatch):
    client, module, _ = test_environment

    class BrokenEngine:
        def connect(self):
            raise RuntimeError("Simulated connection failure")

    monkeypatch.setattr(module, "engine", BrokenEngine())

    response = client.get("/health")

    assert response.status_code == 503


def test_generated_query_failure(test_environment, monkeypatch):
    client, module, _ = test_environment

    def failing_query(session):
        raise RuntimeError("Simulated generated query failure")

    monkeypatch.setattr(
        module.generated,
        "select_users_q004",
        failing_query,
    )

    response = client.get("/users")

    assert response.status_code == 500