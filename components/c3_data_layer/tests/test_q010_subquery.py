"""Regression and safety tests for Q010 static IN-subquery migration."""

from copy import deepcopy
from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_query, convert_queries
from c3.generation.emitter import generate_data_access_source


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "clinic_inventory_legacy_hard"
    / "legacy"
)

EXPECTED_SQL = (
    "SELECT id, full_name FROM patients "
    "WHERE id IN ("
    "SELECT patient_id FROM appointments "
    "WHERE status = 'confirmed'"
    ")"
)


@pytest.fixture(scope="module")
def recovered_q010():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in recovered if q["query_id"] == "Q010")


def test_q010_recovered_as_static_select(recovered_q010):
    assert recovered_q010["status"] == "RECOVERED"
    assert recovered_q010["operation"] == "SELECT"
    assert recovered_q010["dynamic_variables"] == []
    assert recovered_q010["recovered_sql"] == EXPECTED_SQL


def test_q010_converts_successfully(recovered_q010):
    result = convert_query(recovered_q010)

    assert result["status"] == "CONVERTED"
    assert result["operation"] == "SELECT"
    assert result["table"] == "patients"
    assert result["parameters"] == []
    assert result["parameterized_sql"] == EXPECTED_SQL


def test_q010_generated_through_main_pipeline():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 19

    source = generate_data_access_source(converted)

    assert source.count("def select_patients_q010(") == 1
    assert EXPECTED_SQL in source

    compile(source, "<generated_data_access>", "exec")


@pytest.mark.parametrize(
    "modified_sql",
    [
        EXPECTED_SQL.replace("FROM appointments", "FROM users"),
        EXPECTED_SQL.replace("FROM patients", "FROM doctors"),
        EXPECTED_SQL.replace("SELECT patient_id", "SELECT doctor_id"),
        EXPECTED_SQL.replace("status = 'confirmed'", "status = 'pending'"),
        EXPECTED_SQL.replace(
            "WHERE status = 'confirmed'",
            "WHERE status = 'confirmed' AND fee > 100",
        ),
        (
            "SELECT id, full_name FROM patients "
            "WHERE id IN (SELECT patient_id FROM appointments "
            "WHERE status = :status)"
        ),
        (
            "SELECT id, full_name FROM patients "
            "WHERE id IN (SELECT patient_id FROM appointments "
            "WHERE id IN (SELECT id FROM doctors))"
        ),
    ],
)
def test_modified_q010_sql_rejected(recovered_q010, modified_sql):
    query = deepcopy(recovered_q010)
    query["recovered_sql"] = modified_sql

    result = convert_query(query)

    assert result["status"] == "UNRESOLVED"


def test_q010_dynamic_variables_rejected(recovered_q010):
    query = deepcopy(recovered_q010)
    query["dynamic_variables"] = ["status"]

    assert convert_query(query)["status"] == "UNRESOLVED"


def test_q010_wrong_query_id_rejected(recovered_q010):
    query = deepcopy(recovered_q010)
    query["query_id"] = "Q999"

    assert convert_query(query)["status"] == "UNRESOLVED"


def test_q010_unrecovered_status_rejected(recovered_q010):
    query = deepcopy(recovered_q010)
    query["status"] = "UNRESOLVED"

    assert convert_query(query)["status"] == "UNRESOLVED"


def test_existing_static_inner_join_still_supported(recovered_q010):
    """Existing JOIN support must remain functional."""

    query = deepcopy(recovered_q010)

    query["recovered_sql"] = (
        "SELECT p.id, p.full_name FROM patients p "
        "JOIN appointments a ON a.patient_id = p.id "
        "WHERE a.status = 'confirmed'"
    )

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert result["operation"] == "SELECT"
    assert result["parameters"] == []
