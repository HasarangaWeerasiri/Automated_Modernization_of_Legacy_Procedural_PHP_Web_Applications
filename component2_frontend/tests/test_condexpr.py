"""condExpr on branch enclosures (agent extension, pending Member 01 Q2; generation-spec.md section 6),
and the typed contract schema Stage 5 reads (loader.SchemaType)."""

import json
import re

import pytest

from src.mapping.loader import load_contract, load_timeline, load_timeline_json
from tests.test_loc import ALL_MOCKS, FIXTURES, MOCKS, needs_clones, source_of


def branches(doc):
    return [enc for entry in doc["sequence"] for enc in entry["enclosedBy"] if enc["role"] == "branch"]


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_then_and_elseif_carry_condexpr_and_else_does_not(name):
    doc = load_timeline_json(MOCKS / f"timeline_{name}.json")
    for enc in branches(doc):
        assert ("condExpr" in enc) == (enc["branch"] != "else"), enc
    assert "Member 01 Q2" in doc["_provenance"]["condExprExtension"]


@needs_clones
@pytest.mark.parametrize("name", ALL_MOCKS)
def test_condexpr_is_the_condition_source_between_the_parentheses(name):
    doc = load_timeline_json(MOCKS / f"timeline_{name}.json")
    source = source_of(name, doc).decode("utf-8")
    for enc in branches(doc):
        if "condExpr" in enc:
            keyword = "elseif" if enc["branch"] == "elseif" else "if"
            pattern = rf"\b{keyword}\s*\(\s*{re.escape(enc['condExpr'])}\s*\)"
            assert re.search(pattern, source), (enc["nodeId"], enc["condExpr"])


def test_list_while_status_conditions():
    timeline = load_timeline(MOCKS / "timeline_list_while.json")
    conditions = list(dict.fromkeys(e.cond_expr for n in timeline.sequence for e in n.enclosed_by
                                    if e.role == "branch"))
    assert conditions == ["($row['userStatus']==1) && ($row['doctorStatus']==1)",
                          "($row['userStatus']==0) && ($row['doctorStatus']==1)",
                          "($row['userStatus']==1) && ($row['doctorStatus']==0)"]


def test_condexpr_is_optional():
    timeline = load_timeline(FIXTURES / "timeline_unlabelled.json")
    assert all(e.cond_expr is None for n in timeline.sequence for e in n.enclosed_by)


@pytest.mark.parametrize("branch, value", [("else", "$x"), ("then", ""), ("then", 3)])
def test_malformed_condexpr_is_rejected(tmp_path, branch, value):
    doc = json.loads((MOCKS / "timeline_admin.json").read_text(encoding="utf-8"))
    enc = next(e for entry in doc["sequence"] for e in entry["enclosedBy"] if e["role"] == "branch")
    enc["branch"], enc["condExpr"] = branch, value
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        load_timeline(path)


def test_contract_schema_is_typed_for_stage5():
    contract = load_contract(MOCKS / "sample_contract.json")
    schema = contract.endpoints[("get", "/api/appointments", "200", "application/json")].schema
    assert schema.kind == "object" and schema.ref is None and schema.required == ("appointments",)
    array = dict(schema.properties)["appointments"]
    assert array.kind == "array" and array.items.ref == "Appointment"
    fields = dict(array.items.properties)
    assert "contact" not in fields and fields["userStatus"].kind == "integer" and fields["fname"].kind == "string"
    stats = contract.endpoints[("get", "/api/admin/appointment-stats", "200", "application/json")].schema
    assert stats.ref == "AppointmentStats"


def test_nullable_and_untyped_schemas(tmp_path):
    doc = {"openapi": "3.1.0", "info": {"title": "t"}, "paths": {"/x": {"get": {"responses": {"200": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {
            "a": {"type": ["string", "null"]}, "b": {"type": "string", "nullable": True},
            "c": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}}}}}}}}}}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    props = dict(load_contract(path).endpoints[("get", "/x", "200", "application/json")].schema.properties)
    assert (props["a"].kind, props["a"].nullable) == ("string", True)
    assert props["b"].nullable and props["c"].kind == "unknown"
