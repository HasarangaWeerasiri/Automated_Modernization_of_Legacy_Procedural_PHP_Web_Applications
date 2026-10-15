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

from fastapi import FastAPI, HTTPException
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
        for _ in range(copies):
            connection.execute(insert, row)
    return {"message": "Contact saved"}
