"""Q011 guarded SQL IN-list migration regression tests."""

from copy import deepcopy
from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries
from c3.conversion.in_list_converter import convert_q011_in_list
from c3.conversion.in_list_binding import (
    parse_integer_id_list,
    resolve_q011_ids,
)
from c3.conversion.in_list_source_gate import validate_q011_source_shape
from c3.generation.emitter import generate_data_access_source


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module")
def recovered_q011():
    queries = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in queries if q["query_id"] == "Q011")


@pytest.fixture(scope="module")
def generated_q011():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 17

    source = generate_data_access_source(converted)
    compile(source, "<generated_q011>", "exec")

    namespace = {}
    exec(compile(source, "<generated_q011>", "exec"), namespace)
    return namespace["select_patients_q011"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", [1]),
        ("1,2", [1, 2]),
        ("1,2,3", [1, 2, 3]),
        ("001,002", [1, 2]),
    ],
)
def test_valid_id_lists(raw, expected):
    assert parse_integer_id_list(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "-1",
        "1,",
        ",1",
        "1,,2",
        "1 OR 1=1",
        "1); DELETE FROM patients; --",
        "SELECT id FROM patients",
        "1, NULL",
        "9223372036854775808",
        123,
        [1, 2],
    ],
)
def test_invalid_id_lists_rejected(raw):
    with pytest.raises(ValueError):
        parse_integer_id_list(raw)


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({}, [1, 2]),
        ({"ids": None}, [1, 2]),
        ({"ids": "2,3"}, [2, 3]),
    ],
)
def test_q011_default_binding(inputs, expected):
    assert resolve_q011_ids(inputs) == expected


def test_q011_source_shape_and_conversion(recovered_q011):
    assert validate_q011_source_shape(
        LEGACY_DIR / "11_in_values.php"
    )

    result = convert_q011_in_list(recovered_q011)

    assert result is not None
    assert result["status"] == "CONVERTED"
    assert result["query_shape"] == "EXPANDING_IN_SELECT"
    assert result["parameters"] == ["ids"]


@pytest.mark.parametrize(
    "change",
    [
        lambda q: q.update(query_id="Q999"),
        lambda q: q.update(status="RECOVERED"),
        lambda q: q.update(dynamic_variables=["other"]),
        lambda q: q.update(source_file="missing.php"),
        lambda q: q.update(
            recovered_sql="SELECT id FROM patients WHERE id IN ({{ids}})"
        ),
    ],
)
def test_modified_q011_rejected(recovered_q011, change):
    query = deepcopy(recovered_q011)
    change(query)

    assert convert_q011_in_list(query) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("'1,2'", "'3,4'"),
        ("FROM patients", "FROM doctors"),
        ("$_GET['ids']", "$_GET['other']"),
        (
            "$result = mysqli_query",
            "modifyIds($ids);\n$result = mysqli_query",
        ),
    ],
)
def test_modified_php_source_rejected(tmp_path, old, new):
    original = (LEGACY_DIR / "11_in_values.php").read_text(
        encoding="utf-8"
    )
    modified = original.replace(old, new, 1)

    assert modified != original

    path = tmp_path / "11_in_values.php"
    path.write_text(modified, encoding="utf-8")

    assert not validate_q011_source_shape(path)


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
    "inputs,expected",
    [
        ({}, [1, 2]),
        ({"ids": None}, [1, 2]),
        ({"ids": "1,3"}, [1, 3]),
    ],
)
def test_generated_q011_expanding_bind(generated_q011, inputs, expected):
    session = RecordingSession()

    result = generated_q011(session, inputs)

    assert result == []
    assert len(session.calls) == 1

    statement, params = session.calls[0]

    assert params == {"ids": expected}
    assert statement._bindparams["ids"].expanding is True


def test_generated_q011_rejects_injection(generated_q011):
    session = RecordingSession()

    with pytest.raises(ValueError):
        generated_q011(
            session,
            {"ids": "1); DELETE FROM patients; --"},
        )

    assert session.calls == []
