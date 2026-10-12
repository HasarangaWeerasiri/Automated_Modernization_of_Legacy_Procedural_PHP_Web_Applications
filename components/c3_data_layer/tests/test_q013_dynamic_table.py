"""Q013 dynamic table allowlist and generated SQL regression tests."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from c3.conversion.table_binding import resolve_q013_table
from c3.conversion.table_converter import convert_q013_table
from c3.conversion.table_source_gate import validate_q013_source_shape
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


LEGACY_DIR = (
    Path(__file__).resolve().parents[3]
    / "benchmarks/apps/clinic_inventory_legacy_hard/legacy"
)


@pytest.fixture(scope="module")
def recovered_q013():
    queries = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    return next(q for q in queries if q["query_id"] == "Q013")


@pytest.fixture(scope="module")
def generated_q013():
    queries = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(queries)

    assert len(converted) == 21
    assert sum(q["status"] == "CONVERTED" for q in converted) == 20

    q013 = next(q for q in converted if q["query_id"] == "Q013")
    assert q013["query_shape"] == "TABLE_IDENTIFIER_SELECT"

    source = generate_data_access_source(converted)
    namespace = {}
    exec(compile(source, "<q013_generated>", "exec"), namespace)

    for name in (
        "select_patients_q010",
        "select_patients_q011",
        "select_patients_q012",
        "select_q013",
        "select_doctors_q014",
    ):
        assert callable(namespace[name])

    return namespace["select_q013"]


@pytest.mark.parametrize(
    "inputs",
    [{}, {"table": None}, {"table": "patients"}],
)
def test_allowed_table_inputs(inputs):
    assert resolve_q013_table(inputs) == "patients"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "doctors",
        "patients; DROP TABLE doctors",
        "patients WHERE 1=1",
        "patients JOIN appointments",
        "(SELECT id FROM patients)",
        "PATIENTS",
        123,
        ["patients"],
    ],
)
def test_disallowed_table_inputs(value):
    with pytest.raises(ValueError):
        resolve_q013_table({"table": value})


def test_original_source_and_conversion(recovered_q013):
    assert validate_q013_source_shape(
        LEGACY_DIR / "13_dynamic_table.php"
    )

    result = convert_q013_table(recovered_q013)
    assert result is not None
    assert result["status"] == "CONVERTED"
    assert result["query_shape"] == "TABLE_IDENTIFIER_SELECT"
    assert result["allowed_tables"] == ["patients"]


@pytest.mark.parametrize(
    "change",
    [
        {"query_id": "Q999"},
        {"status": "RECOVERED"},
        {"reason_code": "OTHER"},
        {"recovered_sql": "SELECT 1"},
        {"dynamic_variables": ["other"]},
        {"executor": "unknown"},
        {"sql_argument": '"SELECT 1"'},
        {"source_file": "missing.php"},
    ],
)
def test_modified_query_rejected(recovered_q013, change):
    query = deepcopy(recovered_q013)
    query.update(change)
    assert convert_q013_table(query) is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("'patients'", "'doctors'"),
        ("SELECT id FROM $table", "SELECT id FROM patients"),
        ("$_GET['table']", "$_GET['name']"),
        ("WHERE id = 1", "WHERE id = 2"),
        (
            "$result = mysqli_query",
            "$table = 'doctors';\n$result = mysqli_query",
        ),
    ],
)
def test_modified_source_rejected(old, new):
    original = (LEGACY_DIR / "13_dynamic_table.php").read_text(
        encoding="utf-8"
    )

    changed = original.replace(old, new, 1)
    assert changed != original

    with TemporaryDirectory() as directory:
        file = Path(directory) / "13_dynamic_table.php"
        file.write_text(changed, encoding="utf-8")
        assert not validate_q013_source_shape(file)


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
    "inputs",
    [{}, {"table": None}, {"table": "patients"}],
)
def test_generated_sql(generated_q013, inputs):
    session = RecordingSession()
    result = generated_q013(session, inputs)

    assert result == []
    assert len(session.calls) == 1

    statement, params = session.calls[0]
    assert statement.text == "SELECT id FROM patients WHERE id = 1"
    assert params is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "doctors",
        "patients; DROP TABLE doctors",
        "patients WHERE 1=1",
        "patients JOIN appointments",
        "(SELECT id FROM patients)",
        "PATIENTS",
        123,
        ["patients"],
    ],
)
def test_generated_rejects_unsafe_tables(generated_q013, value):
    session = RecordingSession()

    with pytest.raises(ValueError):
        generated_q013(session, {"table": value})

    assert session.calls == []


def test_mutated_generated_representation_rejected():
    queries = recover_queries_from_ast(analyze_php_ast(LEGACY_DIR))
    converted = convert_queries(queries)

    modified = deepcopy(converted)
    q013 = next(q for q in modified if q["query_id"] == "Q013")
    q013["allowed_tables"] = ["patients", "doctors"]

    with pytest.raises(ValueError):
        generate_data_access_source(modified)
