"""Placeholder value positions: BETWEEN bounds and allow-listed scalar-function arguments (T1, 2026-10-20).

The converter accepts a bound value only where it is provably a scalar value. These tests pin the extension
(BETWEEN bounds; MD5, SHA1, SHA2 first argument, LOWER, UPPER, TRIM, DATE, CAST value operand, each only when the call
itself sits in a value position) and every case that must keep abstaining. Generated functions are checked with the
E4 hostile payloads: a payload may only ever arrive as a bound parameter, never in the SQL text.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from c3.conversion.query_converter import convert_query
from c3.generation.emitter import generate_data_access_source

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import e4_injection_check as e4  # noqa: E402

UNSAFE = "Dynamic placeholder occurs in an unsupported structural SQL position."


def convert(sql, query_id="Q001"):
    query = {"query_id": query_id, "status": "RECOVERED", "recovered_sql": sql, "operation": sql.split()[0].upper(),
             "executor": "mysqli_query", "source_file": "fixture.php", "source_line": 1}
    return convert_query(query)


ACCEPTED = [
    # BETWEEN bounds, in WHERE, inside AND/OR/NOT, and in JOIN ... ON
    ("SELECT id FROM users WHERE created BETWEEN '{{date_from}}' AND '{{date_to}}'", {"date_from", "date_to"}),
    ("SELECT id FROM users WHERE active = 1 AND created BETWEEN '{{date_from}}' AND '{{date_to}}'", {"date_from", "date_to"}),
    ("SELECT id FROM users WHERE NOT (created BETWEEN '{{date_from}}' AND '{{date_to}}')", {"date_from", "date_to"}),
    ("SELECT id FROM users WHERE created BETWEEN '2020-01-01' AND '{{date_to}}'", {"date_to"}),
    ("UPDATE users SET flag = 1 WHERE created BETWEEN '{{date_from}}' AND '{{date_to}}'", {"date_from", "date_to"}),
    # allow-listed functions, comparison right-hand side
    ("SELECT id FROM users WHERE pwd = MD5('{{pwd}}')", {"pwd"}),
    ("SELECT id FROM users WHERE pwd = SHA1('{{pwd}}')", {"pwd"}),
    ("SELECT id FROM users WHERE pwd = SHA2('{{pwd}}', 256)", {"pwd"}),
    ("SELECT id FROM users WHERE email = LOWER('{{email}}')", {"email"}),
    ("SELECT id FROM users WHERE code = UPPER('{{code}}')", {"code"}),
    ("SELECT id FROM users WHERE name = TRIM('{{name}}')", {"name"}),
    ("SELECT id FROM users WHERE created = DATE('{{day}}')", {"day"}),
    ("SELECT id FROM users WHERE id = CAST('{{id}}' AS SIGNED)", {"id"}),
    # functions as BETWEEN bounds
    ("SELECT id FROM users WHERE created BETWEEN DATE('{{date_from}}') AND DATE('{{date_to}}')", {"date_from", "date_to"}),
    # SET and VALUES
    ("UPDATE users SET pwd = MD5('{{pwd}}') WHERE id = '{{id}}'", {"pwd", "id"}),
    ("INSERT INTO users (name, pwd) VALUES ('{{name}}', MD5('{{pwd}}'))", {"name", "pwd"}),
    ("INSERT INTO users (name, email) VALUES ('{{name}}', LOWER('{{email}}'))", {"name", "email"}),
]


@pytest.mark.parametrize("sql,parameters", ACCEPTED)
def test_accepted_value_positions_convert_to_named_binds(sql, parameters):
    result = convert(sql)
    assert result["status"] == "CONVERTED", result["reason"]
    assert set(result["parameters"]) == parameters
    generated = result["parameterized_sql"]
    assert "{{" not in generated and "'" + ":" not in generated
    assert {name for name in re.findall(r"(?<![:\w]):(\w+)", generated)} == parameters


ABSTAIN = [
    # functions outside the allow-list
    "SELECT id FROM users WHERE x = SLEEP('{{n}}')",
    "SELECT id FROM users WHERE x = BENCHMARK('{{n}}', MD5('a'))",
    "SELECT id FROM users WHERE x = LOAD_FILE('{{path}}')",
    "SELECT id FROM users WHERE x = CONCAT('{{a}}', 'b')",
    # non-value arguments of allow-listed functions
    "SELECT id FROM users WHERE pwd = SHA2('{{pwd}}', {{bits}})",
    "SELECT id FROM users WHERE name = TRIM(BOTH '{{chars}}' FROM name)",
    "SELECT id FROM users WHERE d = DATE_FORMAT(created, '{{fmt}}')",
    # nested dynamic fragments
    "SELECT id FROM users WHERE pwd = MD5(LOWER('{{pwd}}'))",
    "SELECT id FROM users WHERE pwd = MD5(CONCAT('{{pwd}}', 'salt'))",
    # allow-listed function outside a value position
    "SELECT id FROM users ORDER BY MD5('{{x}}')",
    "SELECT id FROM users GROUP BY LOWER('{{x}}')",
    "SELECT id FROM users WHERE CAST('{{x}}' AS SIGNED) = 1",
    "SELECT MD5('{{x}}') FROM users",
    # BETWEEN outside a condition, or on a non-column
    "SELECT created BETWEEN '{{date_from}}' AND '{{date_to}}' FROM users",
    "SELECT id FROM users WHERE '{{x}}' BETWEEN 1 AND 2",
    # placeholders embedded inside string literals
    "SELECT id FROM users WHERE name = 'x{{v}}y'",
    "SELECT id FROM users WHERE pwd = MD5('pre{{v}}')",
    # identifiers stay structural
    "SELECT id FROM users WHERE MD5({{column}}) = 'a'",
]


@pytest.mark.parametrize("sql", ABSTAIN)
def test_other_positions_keep_abstaining(sql):
    result = convert(sql)
    assert result["status"] == "UNRESOLVED", sql
    assert result["reason"] == UNSAFE


def _generated_module(tmp_path, sqls):
    converted = [{**convert(sql, f"Q{i + 1:03d}"), "source_file": str(tmp_path / "fixture.php")} for i, sql in enumerate(sqls)]
    assert all(c["status"] == "CONVERTED" for c in converted)
    source = generate_data_access_source(converted, tmp_path)
    path = tmp_path / "data_access.py"
    path.write_text(source, encoding="utf-8")
    compile(source, str(path), "exec")
    spec = importlib.util.spec_from_file_location("value_positions_generated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, source, {c["query_id"]: c for c in converted}


def test_generated_functions_bind_every_hostile_payload(tmp_path):
    module, source, converted = _generated_module(tmp_path, [sql for sql, _ in ACCEPTED])
    # text() with named binds only: no f-string or % / format interpolation into SQL.
    assert "text(f" not in source and ".format(" not in source and "% (" not in source
    verdicts = {}
    for query_id, name, function, _ in e4.generated_functions(module, converted):
        result = e4.check_function(function)
        verdicts[name] = result["verdict"]
        assert result["bound"] + result["rejected"] == len(e4.PAYLOADS), name
        for case in result["cases"]:
            assert not case["problems"], (name, case)
    assert len(verdicts) == len(ACCEPTED) and set(verdicts.values()) == {"SAFE"}
