"""Hand-written stand-in for the migrated system.

It mimics the API that C1-C3 are expected to produce, so the full differential loop
can be proven before their outputs exist. Endpoints are added one per legacy
endpoint under validation, and return JSON keyed by database column names, as a
generated backend would.

It is also the instrument for fault injection. A named fault can be switched on,
which makes the stub misbehave in one known way, so that the validation engine can
be checked for reporting it:

    PUT    /_fault/{name}    switch a fault on
    DELETE /_fault           behave correctly again
    GET    /_fault           the active fault and the available ones

The fault switch exists only in this stub. A real migrated system has no such thing.
"""

import os
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

app = FastAPI(title="C4 stub API")
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

Patients = list[dict[str, Any]]


def _alter(column: str) -> Callable[[Patients], Patients]:
    return lambda patients: [{**patient, column: f"{patient[column]}x"} for patient in patients]


# Each fault changes the response of the patients endpoint in one known way.
FAULTS: dict[str, Callable[[Patients], Patients]] = {
    "alters_last_name": _alter("lname"),
    "alters_password": _alter("password"),
    "reverses_order": lambda patients: patients[::-1],
    "drops_last_record": lambda patients: patients[:-1],
    "adds_a_record": lambda patients: patients + [
        {"pid": 9999, "fname": "Ghost", "lname": "Row", "gender": "Male",
         "email": "ghost@example.test", "contact": "0000000000", "password": "ghost"}
    ],
    "omits_email": lambda patients: [
        {column: value for column, value in patient.items() if column != "email"}
        for patient in patients
    ],
    "swaps_first_and_last_name": lambda patients: [
        {**patient, "fname": patient["lname"], "lname": patient["fname"]} for patient in patients
    ],
}
# Faults that are not a change to the returned records.
OTHER_FAULTS = (
    "ignores_contact_filter",
    "fails_with_server_error",
    # The contact endpoint reports success in every one of these.
    "suppresses_contact_insert",
    "stores_wrong_message",
    "inserts_contact_twice",
    # Builds its SQL by pasting the input into the statement, as the legacy pages do.
    "concatenates_input",
    # The prescriptions endpoint ignores whose token it was given.
    "shows_everyones_prescriptions",
    "alters_appointment_date",
)

active_fault: str | None = None


@app.get("/_fault")
def read_fault() -> dict[str, Any]:
    return {"active": active_fault, "available": sorted([*FAULTS, *OTHER_FAULTS])}


@app.put("/_fault/{name}")
def set_fault(name: str) -> dict[str, Any]:
    global active_fault
    if name not in FAULTS and name not in OTHER_FAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown fault '{name}'.")
    active_fault = name
    return read_fault()


@app.delete("/_fault")
def clear_fault() -> dict[str, Any]:
    global active_fault
    active_fault = None
    return read_fault()


@app.get("/api/patients")
def patients(contact: str | None = None) -> dict[str, Patients]:
    """All patients, or the patient registered with one contact number.

    Stands in for two legacy pages: the patient list on the admin panel, and the
    patient search, which shows the first patient found for a contact number.
    """
    if active_fault == "fails_with_server_error":
        raise HTTPException(status_code=500, detail="Injected fault.")

    columns = "pid, fname, lname, gender, email, contact, password"
    with engine.connect() as connection:
        if contact is None or active_fault == "ignores_contact_filter":
            rows = connection.execute(text(f"SELECT {columns} FROM patreg")).mappings().all()
        elif active_fault == "concatenates_input":
            unsafe = f"SELECT {columns} FROM patreg WHERE contact = '{contact}' LIMIT 1"
            rows = connection.exec_driver_sql(unsafe).mappings().all()
        else:
            rows = connection.execute(
                text(f"SELECT {columns} FROM patreg WHERE contact = :contact LIMIT 1"),
                {"contact": contact},
            ).mappings().all()

    found = [dict(row) for row in rows]
    if active_fault in FAULTS:
        found = FAULTS[active_fault](found)
    return {"patients": found}


class ContactMessage(BaseModel):
    # As on the legacy page: a field that is not sent is stored as an empty value,
    # and a value longer than its column is refused, so nothing is stored. The legacy
    # page refuses it silently; this endpoint says so with a 422.
    name: str = Field("", max_length=30)
    email: str = ""
    contact: str = Field("", max_length=10)
    message: str = Field("", max_length=200)


@app.post("/api/contact", status_code=201)
def create_contact(body: ContactMessage) -> dict[str, str]:
    """Store a message sent through the contact form.

    Stands in for the legacy contact page, which inserts one row and tells the user
    the message was sent.
    """
    row = body.model_dump()
    if active_fault == "stores_wrong_message":
        row["message"] = f"{row['message']}x"
    copies = {"suppresses_contact_insert": 0, "inserts_contact_twice": 2}.get(active_fault, 1)

    insert = text(
        "INSERT INTO contact (name, email, contact, message) "
        "VALUES (:name, :email, :contact, :message)"
    )
    with engine.begin() as connection:
        if active_fault == "concatenates_input":
            unsafe = (
                "INSERT INTO contact (name, email, contact, message) VALUES "
                f"('{row['name']}', '{row['email']}', '{row['contact']}', '{row['message']}')"
            )
            connection.exec_driver_sql(unsafe)
            return {"message": "Contact saved"}
        for _ in range(copies):
            connection.execute(insert, row)
    return {"message": "Contact saved"}


class Credentials(BaseModel):
    email: str
    password: str


def _patient(authorization: str | None) -> int:
    """The patient a bearer token stands for. Tokens here are not secrets: this is a stub."""
    scheme, _, token = (authorization or "").partition(" ")
    role, _, pid = token.partition(":")
    if scheme != "Bearer" or role != "patient" or not pid.isdigit():
        raise HTTPException(status_code=401, detail="A patient token is required.")
    return int(pid)


@app.post("/api/auth/login")
def log_in(body: Credentials) -> dict[str, str]:
    """Log a patient in, as the legacy patient login form does, and give a token."""
    with engine.connect() as connection:
        pid = connection.execute(
            text("SELECT pid FROM patreg WHERE email = :email AND password = :password"),
            body.model_dump(),
        ).scalar()
    if pid is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return {"access_token": f"patient:{pid}", "token_type": "bearer"}


@app.get("/api/me/prescriptions")
def my_prescriptions(authorization: str | None = Header(None)) -> dict[str, Any]:
    """The logged-in patient's prescriptions, as the patient dashboard lists them."""
    pid = _patient(authorization)
    # TIME columns are returned as text, as a model with a Time column would render them.
    query = (
        "SELECT doctor, ID, appdate, CAST(apptime AS CHAR) AS apptime, disease, allergy, prescription "
        "FROM prestb"
    )
    with engine.connect() as connection:
        if active_fault == "shows_everyones_prescriptions":
            rows = connection.execute(text(query)).mappings().all()
        else:
            rows = connection.execute(text(query + " WHERE pid = :pid"), {"pid": pid}).mappings().all()
    return {"prescriptions": [dict(row) for row in rows]}


@app.get("/api/me/appointments")
def my_appointments(authorization: str | None = Header(None)) -> dict[str, Any]:
    """The logged-in patient's appointment history, as the patient dashboard lists it.

    Like a generated backend, it returns the two status flags, not the wording the
    legacy page builds from them ("Cancelled by Doctor" and so on).
    """
    pid = _patient(authorization)
    query = (
        "SELECT ID, doctor, docFees, appdate, CAST(apptime AS CHAR) AS apptime, userStatus, doctorStatus "
        "FROM appointmenttb WHERE pid = :pid"
    )
    with engine.connect() as connection:
        rows = connection.execute(text(query), {"pid": pid}).mappings().all()
    appointments = [dict(row) for row in rows]
    if active_fault == "alters_appointment_date":
        appointments = [{**row, "appdate": "1999-01-01"} for row in appointments]
    return {"appointments": appointments}
