"""OpenAPI 3.1 contract and generated FastAPI backend (src/c1/contract.py)."""

from __future__ import annotations

import importlib
import json
import sys

import pytest

from c1.contract import build_openapi, write_backend
from test_endpoints import SCHEMA, endpoints

APP = {
    "contact.php": """<?php
if (isset($_POST['btnSubmit'])) {
  $name = $_POST['txtName']; $message = $_POST['txtMsg'];
  mysqli_query($con, "insert into contact(name,message) values('$name','$message')");
}
""",
    "me.php": """<?php
session_start();
if (!isset($_SESSION['pid'])) { header('Location: /'); exit; }
$pid = $_SESSION['pid'];
$r = mysqli_query($con, "select ID, appdate from appointmenttb where pid='$pid'");
while ($row = mysqli_fetch_array($r)) { echo $row['ID'] . $row['appdate']; }
""",
}


def contract():
    rec = endpoints(APP)
    rec["schema"] = SCHEMA
    return rec, build_openapi(rec, "Test", SCHEMA)


def test_openapi_document_shape():
    rec, oa = contract()
    assert oa["openapi"] == "3.1.0"
    assert set(oa["paths"]) == {"/api/contact", "/api/me/appointmenttb"}
    post = oa["paths"]["/api/contact"]["post"]
    body = oa["components"]["schemas"][post["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]]
    assert body["properties"] == {"message": {"type": "string", "maxLength": 200}, "name": {"type": "string", "maxLength": 30}}
    # no check in the legacy page: locked by default (login required) and still flagged for a decision
    assert post["security"] == [{"bearerAuth": []}] and "401" in post["responses"]
    assert post["x-c1"]["flags"][0]["flag"] == "no-guard-in-legacy" and post["x-c1"]["auth"]["securedByDefault"]
    assert post["description"].startswith("Locked by default")


def test_guarded_endpoint_requires_bearer_and_lists_fields():
    _, oa = contract()
    get = oa["paths"]["/api/me/appointmenttb"]["get"]
    assert get["security"] == [{"bearerAuth": []}] and "401" in get["responses"]
    resp = oa["components"]["schemas"]["GetApiMeAppointmenttbResponse"]
    item = oa["components"]["schemas"]["GetApiMeAppointmenttbItem"]
    assert resp["properties"]["appointmenttb"]["type"] == "array"
    assert item["properties"] == {"ID": {"type": "integer"}, "appdate": {"type": "string", "format": "date"}}
    assert get["x-c1"]["sessionBindings"][0]["sessionKey"] == "pid"


def test_contract_is_deterministic():
    assert contract()[1] == contract()[1]


def test_generated_fastapi_app_serves_the_same_contract(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("jose")
    from fastapi.testclient import TestClient

    rec, oa = contract()
    write_backend(tmp_path / "backend", rec, oa, "Test")
    sys.path.insert(0, str(tmp_path / "backend"))
    try:
        for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
        main = importlib.import_module("app.main")
        auth = importlib.import_module("app.auth")
        client = TestClient(main.app)
        served = client.get("/openapi.json").json()
        assert {(p, m) for p, v in served["paths"].items() for m in v} == \
               {(p, m) for p, v in oa["paths"].items() for m in v}
        # Swagger shows the served text: it must equal the contract's, including the locked-by-default note
        for p, v in oa["paths"].items():
            for m, op in v.items():
                assert served["paths"][p][m].get("description") == op["description"], (p, m)
        assert served["paths"]["/api/contact"]["post"]["description"].startswith("Locked by default")
        # session check -> 401 without a token, and the token's claim reaches the data layer
        assert client.get("/api/me/appointmenttb").status_code == 401
        # secured by default: the unguarded legacy form also needs a token now
        assert client.post("/api/contact", json={"name": "a", "message": "b"}).status_code == 401
        token = auth.create_access_token({"pid": 7})
        da = importlib.import_module("app.data_access")
        seen = {}
        da.get_api_me_appointmenttb = lambda **kw: (seen.update(kw), [{"ID": 1, "appdate": "2026-10-01"}])[1]
        importlib.import_module("app.routes").data_access = da
        r = client.get("/api/me/appointmenttb", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and seen == {"pid": 7}
        assert r.json() == {"appointmenttb": [{"ID": 1, "appdate": "2026-10-01"}]}
    finally:
        sys.path.remove(str(tmp_path / "backend"))
        for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
