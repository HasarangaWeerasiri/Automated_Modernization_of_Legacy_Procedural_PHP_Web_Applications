
"""
C3 Data Layer Migration - Backend Integration Verification

Demonstrates that a FastAPI endpoint can execute
C3-generated SQLAlchemy data-access functions.

This is an isolated research demonstration, not the
final migrated backend.
"""

import importlib.util
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[4]

GENERATED_FILE = (
    PROJECT_ROOT / "output" / "c3_viva_demo" / "data_access.py"
)


def load_generated_data_access():
    """Load the actual C3-generated Python module."""

    if not GENERATED_FILE.is_file():
        raise FileNotFoundError(
            f"Generated C3 data access file not found: {GENERATED_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "c3_generated_data_access",
        GENERATED_FILE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load generated C3 module.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


password = os.environ.get("C3_DB_PASSWORD")

if not password:
    raise RuntimeError(
        "C3_DB_PASSWORD is not set. "
        "Set the c3_tester MySQL password before starting FastAPI."
    )


database_url = URL.create(
    drivername="mysql+pymysql",
    username="c3_tester",
    password=password,
    host="localhost",
    database="c3_python_validation",
)

engine = create_engine(database_url, pool_pre_ping=True)

generated = load_generated_data_access()

app = FastAPI(
    title="C3 Data Layer Migration Verification",
    description="Standalone FastAPI integration demo for C3-generated data access.",
    version="1.0.0",
)


@app.get("/health")
def health():
    """Verify database connectivity."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "ok",
            "component": "C3",
            "database": "c3_python_validation",
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable.",
        )


@app.get("/users")
def get_users():
    """Retrieve users using the C3-generated SELECT function."""

    try:
        with Session(engine) as session:
            rows = generated.select_users_q004(session)

            return {
                "component": "C3",
                "source": "generated_data_access",
                "count": len(rows),
                "users": [dict(row) for row in rows],
            }

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Generated data-access query failed.",
        )
