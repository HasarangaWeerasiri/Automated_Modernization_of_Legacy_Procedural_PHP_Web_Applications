"""E4 injection check (scripts/e4_injection_check.py), with negative controls."""

import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import e4_injection_check as e4  # noqa: E402


def bound_select(session, name):
    return session.execute(text("SELECT id FROM users WHERE name = :name"), {"name": name}).mappings().all()


def interpolated_select(session, name):  # the unsafe pattern C3 must never emit
    return session.execute(text(f"SELECT id FROM users WHERE name = '{name}'")).mappings().all()


def allowlisted_order(session, inputs):
    column = inputs["sort"]
    if column not in ("id", "name"):
        raise ValueError("sort column not allowed")
    return session.execute(text(f"SELECT id FROM users ORDER BY {column}")).mappings().all()


def unchecked_order(session, inputs):
    return session.execute(text(f"SELECT id FROM users ORDER BY {inputs['sort']}")).mappings().all()


def test_bound_parameters_are_safe():
    result = e4.check_function(bound_select)

    assert result["verdict"] == "SAFE"
    assert result["bound"] == len(e4.PAYLOADS)


def test_interpolation_is_detected_as_unsafe():
    result = e4.check_function(interpolated_select)

    assert result["verdict"] == "UNSAFE"
    assert any("payload text in SQL" in c["problems"] for c in result["cases"])


def test_allowlisted_identifier_rejects_every_payload():
    result = e4.check_function(allowlisted_order)

    assert result["verdict"] == "SAFE"
    assert result["rejected"] == len(e4.PAYLOADS)


def test_unchecked_identifier_is_unsafe():
    assert e4.check_function(unchecked_order)["verdict"] == "UNSAFE"


def test_generated_clinic_functions_are_all_safe(tmp_path):
    from c3.pipeline import run_pipeline
    import importlib.util
    import json

    apps = ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard"
    run_pipeline(apps / "legacy", apps / "database" / "schema.sql", tmp_path)
    spec = importlib.util.spec_from_file_location("e4_clinic_test", tmp_path / "data_access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    converted = {q["query_id"]: q for q in json.loads((tmp_path / "converted_queries.json").read_text(encoding="utf-8"))}

    verdicts = {name: e4.check_function(f)["verdict"] for _, name, f, _ in e4.generated_functions(module, converted)}

    assert len(verdicts) == 21
    assert set(verdicts.values()) == {"SAFE"}
