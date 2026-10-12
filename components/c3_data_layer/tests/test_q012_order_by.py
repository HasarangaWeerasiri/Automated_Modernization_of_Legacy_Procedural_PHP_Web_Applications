"""Regression tests for guarded Q012 ORDER BY migration."""

from copy import deepcopy
from pathlib import Path

import pytest

from c3.conversion.order_by_binding import resolve_q012_sort
from c3.conversion.order_by_converter import convert_q012_order_by
from c3.conversion.order_by_source_gate import validate_q012_source_shape
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module")
def recovered_q012():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in recovered if q["query_id"] == "Q012")


@pytest.fixture(scope="module")
def generated_q012():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 20

    q012 = next(q for q in converted if q["query_id"] == "Q012")
    assert q012["status"] == "CONVERTED"
    assert q012["query_shape"] == "ORDER_BY_SELECT"

    source = generate_data_access_source(converted)
    namespace = {}
    exec(compile(source, "<generated_q012>", "exec"), namespace)

    return namespace["select_patients_q012"]


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({}, "full_name"),
        ({"sort": None}, "full_name"),
        ({"sort": "id"}, "id"),
        ({"sort": "full_name"}, "full_name"),
    ],
)
def test_allowed_sort_inputs(inputs, expected):
    assert resolve_q012_sort(inputs) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "id DESC",
        "full_name ASC",
        "RAND()",
        "id; DROP TABLE patients",
        "unknown_column",
        123,
        ["id"],
    ],
)
def test_disallowed_sort_inputs(value):
    with pytest.raises(ValueError):
        resolve_q012_sort({"sort": value})


def test_original_source_and_conversion(recovered_q012):
    assert validate_q012_source_shape(
        LEGACY_DIR / "12_order_by.php"
    )

    result = convert_q012_order_by(recovered_q012)

    assert result is not None
    assert result["status"] == "CONVERTED"
    assert result["query_shape"] == "ORDER_BY_SELECT"


@pytest.mark.parametrize(
    "change",
    [
        {"query_id": "Q999"},
        {"status": "RECOVERED"},
        {"recovered_sql": "SELECT 1"},
        {"dynamic_variables": ["other"]},
        {"source_file": "missing.php"},
        {"executor": "unknown"},
    ],
)
def test_modified_query_rejected(recovered_q012, change):
    query = deepcopy(recovered_q012)
    query.update(change)
    assert convert_q012_order_by(query) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("'full_name'", "'id'"),
        ("FROM patients", "FROM doctors"),
        ("$_GET['sort']", "$_GET['order']"),
        (
            "$result = mysqli_query",
            "$sort = 'id DESC';\n$result = mysqli_query",
        ),
    ],
)
def test_modified_source_rejected(tmp_path, old, new):
    original = (LEGACY_DIR / "12_order_by.php").read_text(
        encoding="utf-8"
    )

    changed = original.replace(old, new, 1)
    assert changed != original

    path = tmp_path / "12_order_by.php"
    path.write_text(changed, encoding="utf-8")

    assert not validate_q012_source_shape(path)


class RecordingResult:
    def mappings(self):
        return self

    def all(self):
        return []


class RecordingSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return RecordingResult()


@pytest.mark.parametrize(
    "inputs,column",
    [
        ({}, "full_name"),
        ({"sort": None}, "full_name"),
        ({"sort": "id"}, "id"),
        ({"sort": "full_name"}, "full_name"),
    ],
)
def test_generated_order_by(generated_q012, inputs, column):
    session = RecordingSession()

    result = generated_q012(session, inputs)

    assert result == []
    assert len(session.calls) == 1

    statement, params = session.calls[0]

    assert statement.text == (
        f"SELECT id, full_name FROM patients ORDER BY {column}"
    )
    assert params is None


@pytest.mark.parametrize(
    "unsafe",
    [
        "id DESC",
        "RAND()",
        "id; DROP TABLE patients",
    ],
)
def test_generated_rejects_unsafe_sort(generated_q012, unsafe):
    session = RecordingSession()

    with pytest.raises(ValueError):
        generated_q012(session, {"sort": unsafe})

    assert session.calls == []


def test_unknown_query_shape_rejected():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    modified = deepcopy(converted)
    q012 = next(q for q in modified if q["query_id"] == "Q012")
    q012["query_shape"] = "UNKNOWN"

    with pytest.raises(ValueError):
        generate_data_access_source(modified)
