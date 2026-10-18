"""Hand-written stand-in for the migrated WackoPicko system.

It mimics the API that C1-C3 are expected to produce for the wackopicko benchmark, so
the full differential loop can run on a second application before their outputs
exist. Each endpoint does what the legacy page behind it does, through the same
database, and returns JSON keyed by database column names, as a generated backend
would.

It is also an instrument for fault injection, with the same switch as the stub API
of the hms benchmark:

    PUT    /_fault/{name}    switch a fault on
    DELETE /_fault           behave correctly again
    GET    /_fault           the active fault and the available ones

The fault switch exists only in this stub. A real migrated system has no such thing.
"""

import base64
import hashlib
import os
import random
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

app = FastAPI(title="C4 stub API (wackopicko)")
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

FAULTS = (
    # The guestbook.
    "drops_last_entry",
    "stores_wrong_comment",
    "stores_a_fixed_time",
    # The pictures.
    "alters_picture_title",
    "ignores_search_filter",
    # Builds its SQL by pasting the input into the statement.
    "concatenates_input",
)

active_fault: str | None = None


@app.get("/_fault")
def read_fault() -> dict[str, Any]:
    return {"active": active_fault, "available": sorted(FAULTS)}


@app.put("/_fault/{name}")
def set_fault(name: str) -> dict[str, Any]:
    global active_fault
    if name not in FAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown fault '{name}'.")
    active_fault = name
    return read_fault()


@app.delete("/_fault")
def clear_fault() -> dict[str, Any]:
    global active_fault
    active_fault = None
    return read_fault()


def _rows(query: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(query), values or {}).mappings()]


# ------------------------------------------------------------------- guestbook


@app.get("/api/guestbook")
def guestbook() -> dict[str, Any]:
    """Every guestbook entry, newest first, as the guestbook page lists them."""
    entries = _rows("SELECT id, name, comment, created_on FROM guestbook ORDER BY created_on DESC")
    if active_fault == "drops_last_entry":
        entries = entries[:-1]
    return {"entries": entries}


class GuestbookEntry(BaseModel):
    # As on the legacy page, both fields are required; a value longer than its
    # column is refused, so nothing is stored.
    name: str = Field("", max_length=100)
    comment: str = Field("", max_length=500)


@app.post("/api/guestbook", status_code=201)
def sign_guestbook(body: GuestbookEntry) -> dict[str, str]:
    """Store a guestbook entry with the current time, as the guestbook page does."""
    if not body.name or not body.comment:
        raise HTTPException(status_code=422, detail="Must include both the name and comment field!")
    row = body.model_dump()
    if active_fault == "stores_wrong_comment":
        row["comment"] = f"{row['comment']}x"
    created_on = "'2000-01-01 00:00:00'" if active_fault == "stores_a_fixed_time" else "NOW()"
    with engine.begin() as connection:
        if active_fault == "concatenates_input":
            connection.exec_driver_sql(
                "INSERT INTO guestbook (name, comment, created_on) "
                f"VALUES ('{row['name']}', '{row['comment']}', NOW())"
            )
        else:
            connection.execute(
                text(f"INSERT INTO guestbook (name, comment, created_on) VALUES (:name, :comment, {created_on})"),
                row,
            )
    return {"message": "Entry saved"}


# -------------------------------------------------------------------- pictures

_PICTURE_COLUMNS = "id, title, width, height, tag, filename, price, created_on, user_id"


def _pictures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if active_fault == "alters_picture_title":
        rows = [{**row, "title": f"{row['title']}x"} for row in rows]
    return rows


@app.get("/api/pictures/recent")
def recent_pictures() -> dict[str, Any]:
    """The ten most recently uploaded pictures, as the recent pictures page shows them."""
    return {"pictures": _pictures(_rows(f"SELECT {_PICTURE_COLUMNS} FROM pictures ORDER BY created_on DESC LIMIT 10"))}


@app.get("/api/pictures/search")
def search_pictures(query: str) -> dict[str, Any]:
    """The pictures whose tag matches a pattern, as the search page finds them (SQL LIKE)."""
    if active_fault == "ignores_search_filter":
        rows = _rows(f"SELECT {_PICTURE_COLUMNS} FROM pictures")
    elif active_fault == "concatenates_input":
        with engine.connect() as connection:
            unsafe = f"SELECT {_PICTURE_COLUMNS} FROM pictures WHERE tag LIKE '{query}'"
            rows = [dict(row) for row in connection.exec_driver_sql(unsafe).mappings()]
    else:
        rows = _rows(f"SELECT {_PICTURE_COLUMNS} FROM pictures WHERE tag LIKE :query", {"query": query})
    return {"pictures": _pictures(rows)}


# ----------------------------------------------------------------------- users


def _user(authorization: str | None) -> int:
    """The user a bearer token stands for. Tokens here are not secrets: this is a stub."""
    scheme, _, token = (authorization or "").partition(" ")
    role, _, userid = token.partition(":")
    if scheme != "Bearer" or role != "user" or not userid.isdigit():
        raise HTTPException(status_code=401, detail="A user token is required.")
    return int(userid)


@app.get("/api/users/{userid}")
def user_pictures(userid: int, authorization: str | None = Header(None)) -> dict[str, Any]:
    """A user and the pictures they uploaded, as a user's page shows them."""
    _user(authorization)
    found = _rows("SELECT id, login, firstname, lastname FROM users WHERE id = :id", {"id": userid})
    if not found:
        raise HTTPException(status_code=404, detail="No such user.")
    pictures = _rows(f"SELECT {_PICTURE_COLUMNS} FROM pictures WHERE user_id = :id", {"id": userid})
    return {"user": found[0], "pictures": _pictures(pictures)}


class Credentials(BaseModel):
    username: str
    password: str


def _log_in(connection, userid: int) -> dict[str, str]:
    connection.execute(text("UPDATE users SET last_login_on = NOW() WHERE id = :id"), {"id": userid})
    return {"access_token": f"user:{userid}", "token_type": "bearer"}


@app.post("/api/auth/login")
def log_in(body: Credentials) -> dict[str, str]:
    """Log a user in, as the legacy login page does, and give a token."""
    with engine.begin() as connection:
        userid = connection.execute(
            text("SELECT id FROM users WHERE login LIKE :username AND password = SHA1(CONCAT(:password, salt)) LIMIT 1"),
            body.model_dump(),
        ).scalar()
        if userid is None:
            raise HTTPException(status_code=401, detail="The username/password combination you have entered is invalid")
        return _log_in(connection, userid)


class Registration(BaseModel):
    username: str = Field("", max_length=50)
    firstname: str = Field("", max_length=50)
    lastname: str = Field("", max_length=200)
    password: str = ""
    againpass: str = ""


@app.post("/api/users", status_code=201)
def register(body: Registration) -> dict[str, Any]:
    """Create an account and log it in, as the registration page does."""
    if not all(body.model_dump().values()):
        raise HTTPException(status_code=422, detail="All fields are required")
    if body.password != body.againpass:
        raise HTTPException(status_code=422, detail="The passwords do not match")
    # As the legacy page: a random salt of up to three digits, base64-encoded.
    salt = base64.b64encode(str(random.randint(0, 900)).encode()).decode()
    with engine.begin() as connection:
        if connection.execute(text("SELECT 1 FROM users WHERE login = :login"), {"login": body.username}).first():
            raise HTTPException(status_code=409, detail=f"Username '{body.username}' is already in use.")
        result = connection.execute(
            text(
                "INSERT INTO users (login, password, firstname, lastname, salt, tradebux, created_on, last_login_on) "
                "VALUES (:login, :password, :firstname, :lastname, :salt, 100, NOW(), NOW())"
            ),
            {
                "login": body.username,
                "password": hashlib.sha1((body.password + salt).encode()).hexdigest(),
                "firstname": body.firstname,
                "lastname": body.lastname,
                "salt": salt,
            },
        )
        userid = result.lastrowid
        return {"id": userid, **_log_in(connection, userid)}
