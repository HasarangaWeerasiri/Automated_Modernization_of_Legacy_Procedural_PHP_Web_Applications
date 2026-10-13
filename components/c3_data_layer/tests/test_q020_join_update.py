"""Regression tests for Q020 UPDATE JOIN migration."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from c3.conversion.join_update_binding import resolve_q020_id
from c3.conversion.join_update_converter import (
    PARAMETERIZED_SQL,
    convert_q020_join_update,
)
from c3.conversion.join_update_source_gate import validate_q020_source_shape
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source


LEGACY_DIR = Path(
    "../../benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module")
def recovered_q020():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in recovered if q["query_id"] == "Q020")


@pytest.fixture(scope="module")
def generated_q020():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert all(q["status"] == "CONVERTED" for q in converted)

    source = generate_data_access_source(converted)
    namespace = {}

    exec(compile(source, "<q020_generated>", "exec"), namespace)

    return namespace["update_appointment_q020"]


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({}, 1),
        ({"id": None}, 1),
        ({"id": "1"}, 1),
        ({"id": "2"}, 2),
        ({"id": 3}, 3),
        ({"id": 2147483647}, 2147483647),
    ],
)
def test_valid_ids(inputs, expected):
    assert resolve_q020_id(inputs) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "-1",
        "1 OR 1=1",
        "1; DROP TABLE appointments",
        "1 UNION SELECT 1",
        "1.5",
        " 1 ",
        True,
        ["1"],
        2147483648,
    ],
)
def test_invalid_ids(value):
    with pytest.raises(ValueError):
        resolve_q020_id({"id": value})


def test_original_source_and_conversion(recovered_q020):
    assert validate_q020_source_shape(recovered_q020["source_file"])

    converted = convert_q020_join_update(recovered_q020)

    assert converted is not None
    assert converted["status"] == "CONVERTED"
    assert converted["query_shape"] == "MYSQL_JOIN_UPDATE"
    assert converted["sql"] == PARAMETERIZED_SQL


@pytest.mark.parametrize(
    "field,value",
    [
        ("query_id", "Q019"),
        ("status", "UNRESOLVED"),
        ("operation", "DELETE"),
        ("executor", "mysqli_multi_query"),
        ("sql_argument", "$other"),
        ("dynamic_variables", ["doctor_id"]),
        ("reason", "unknown"),
        ("reason_code", "UNKNOWN"),
    ],
)
def test_modified_metadata_rejected(recovered_q020, field, value):
    modified = deepcopy(recovered_q020)
    modified[field] = value

    assert convert_q020_join_update(modified) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("UPDATE appointments", "UPDATE patients"),
        ("JOIN doctors", "JOIN departments"),
        ("a.fee", "a.status"),
        ("d.hourly_rate", "d.id"),
        ("$_POST['id']", "$_GET['id']"),
        ("?? '1'", "?? '2'"),
    ],
)
def test_modified_php_rejected(recovered_q020, old, new):
    original = Path(recovered_q020["source_file"]).read_text(
        encoding="utf-8-sig"
    )

    assert old in original

    with TemporaryDirectory() as directory:
        path = Path(directory) / "20_cross_table_update.php"
        path.write_text(original.replace(old, new), encoding="utf-8")

        assert not validate_q020_source_shape(path)


class FakeResult:
    rowcount = 1


class FakeSession:
    def __init__(self):
        self.calls = []
        self.committed = False

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        return FakeResult()

    def commit(self):
        self.committed = True


@pytest.mark.parametrize(
    "inputs,expected_id",
    [
        ({}, 1),
        ({"id": None}, 1),
        ({"id": "1"}, 1),
        ({"id": "2"}, 2),
        ({"id": 3}, 3),
    ],
)
def test_generated_update(generated_q020, inputs, expected_id):
    session = FakeSession()

    result = generated_q020(session, inputs)

    assert result == 1
    assert session.calls == [
        (PARAMETERIZED_SQL, {"id": expected_id})
    ]
    assert session.committed is False


@pytest.mark.parametrize(
    "invalid",
    [
        "0",
        "-1",
        "1 OR 1=1",
        "1; DROP TABLE appointments",
    ],
)
def test_generated_rejects_injection(generated_q020, invalid):
    session = FakeSession()

    with pytest.raises(ValueError):
        generated_q020(session, {"id": invalid})

    assert session.calls == []
    assert session.committed is False
