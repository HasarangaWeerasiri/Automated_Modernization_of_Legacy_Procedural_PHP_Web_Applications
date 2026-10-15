"""Permanent regression tests for guarded Q016 migration."""

from copy import deepcopy
from pathlib import Path

import pytest

from c3.conversion.conditional_concat_binding import (
    resolve_q016_department,
)
from c3.conversion.conditional_concat_converter import (
    BASE_SQL,
    FILTERED_SQL,
    convert_q016_conditional_concat,
)
from c3.conversion.conditional_concat_source_gate import (
    validate_q016_source_shape,
)
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module")
def recovered_q016():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in recovered if q["query_id"] == "Q016")


@pytest.fixture(scope="module")
def generated_q016():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)

    assert len(converted) == 22  # 22 Clinic sites (S22 mysqli_prepare detected since S2)
    assert sum(q["status"] == "CONVERTED" for q in converted) == 22  # +Q022 (S14)

    q016 = next(q for q in converted if q["query_id"] == "Q016")
    assert q016["status"] == "CONVERTED"
    assert q016["query_shape"] == "CONDITIONAL_CONCAT_SELECT"

    source = generate_data_access_source(converted)
    namespace = {}
    exec(compile(source, "<q016_generated>", "exec"), namespace)

    for name in (
        "select_patients_q010",
        "select_patients_q011",
        "select_patients_q012",
        "select_q013",
        "select_doctors_q014",
        "select_patients_q016",
    ):
        assert callable(namespace[name])

    return namespace["select_patients_q016"]


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({}, None),
        ({"department_id": None}, None),
        ({"department_id": "1"}, 1),
        ({"department_id": "2"}, 2),
        ({"department_id": 3}, 3),
    ],
)
def test_valid_department_inputs(inputs, expected):
    assert resolve_q016_department(inputs) == expected


UNSAFE_VALUES = [
    "",
    "abc",
    "1 OR 1=1",
    "1; DROP TABLE patients",
    "1 AND SLEEP(5)",
    "1 UNION SELECT 1,2",
    "-1",
    "0",
    "1.5",
    " 1 ",
    True,
    ["1"],
    2**63,
]


@pytest.mark.parametrize("value", UNSAFE_VALUES)
def test_invalid_department_inputs(value):
    with pytest.raises(ValueError):
        resolve_q016_department({"department_id": value})


def test_original_source_and_conversion(recovered_q016):
    assert validate_q016_source_shape(
        LEGACY_DIR / "16_conditional_concat.php"
    )

    result = convert_q016_conditional_concat(recovered_q016)
    assert result is not None
    assert result["status"] == "CONVERTED"
    assert result["query_shape"] == "CONDITIONAL_CONCAT_SELECT"


@pytest.mark.parametrize(
    "change",
    [
        {"query_id": "Q999"},
        {"status": "RECOVERED"},
        {"reason_code": "STRUCTURAL"},
        {"recovered_sql": "SELECT 1"},
        {"executor": "unknown"},
        {"sql_argument": '"SELECT 1"'},
        {"dynamic_variables": ["department_id"]},
        {"source_file": "missing.php"},
        {"sql_variants": []},
        {"sql_variants": [None, None]},
    ],
)
def test_modified_recovery_rejected(recovered_q016, change):
    modified = deepcopy(recovered_q016)
    modified.update(change)
    assert convert_q016_conditional_concat(modified) is None


@pytest.mark.parametrize("variant_index", [0, 1])
def test_changed_variant_sql_rejected(recovered_q016, variant_index):
    modified = deepcopy(recovered_q016)
    modified["sql_variants"][variant_index]["sql"] = "SELECT 1"

    assert convert_q016_conditional_concat(modified) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("FROM patients", "FROM doctors"),
        ("department_id = ", "id = "),
        ("$_GET['department_id']", "$_GET['id']"),
        (
            "isset($_GET['department_id'])",
            "!isset($_GET['department_id'])",
        ),
        ("$sql .=", "$sql ="),
        (
            "$result = mysqli_query",
            "$sql .= ' OR 1=1';\n$result = mysqli_query",
        ),
    ],
)
def test_modified_php_rejected(tmp_path, old, new):
    original = (LEGACY_DIR / "16_conditional_concat.php").read_text(
        encoding="utf-8"
    )

    changed = original.replace(old, new, 1)
    assert changed != original

    source_path = tmp_path / "16_conditional_concat.php"
    source_path.write_text(changed, encoding="utf-8")

    assert not validate_q016_source_shape(source_path)


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
    "inputs,expected_sql,expected_params",
    [
        ({}, BASE_SQL, None),
        ({"department_id": None}, BASE_SQL, None),
        (
            {"department_id": "1"},
            FILTERED_SQL,
            {"department_id": 1},
        ),
        (
            {"department_id": "2"},
            FILTERED_SQL,
            {"department_id": 2},
        ),
        (
            {"department_id": 3},
            FILTERED_SQL,
            {"department_id": 3},
        ),
    ],
)
def test_generated_sql(
    generated_q016, inputs, expected_sql, expected_params
):
    session = RecordingSession()

    result = generated_q016(session, inputs)

    assert result == []
    assert len(session.calls) == 1

    statement, params = session.calls[0]
    assert statement.text == expected_sql
    assert params == expected_params


@pytest.mark.parametrize("value", UNSAFE_VALUES)
def test_generated_rejects_unsafe_input(generated_q016, value):
    session = RecordingSession()

    with pytest.raises(ValueError):
        generated_q016(session, {"department_id": value})

    assert session.calls == []


def test_mutated_emitter_representation_rejected():
    recovered = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(recovered)
    modified = deepcopy(converted)

    q016 = next(q for q in modified if q["query_id"] == "Q016")
    q016["filtered_sql"] = "SELECT * FROM patients"

    with pytest.raises(ValueError):
        generate_data_access_source(modified)
