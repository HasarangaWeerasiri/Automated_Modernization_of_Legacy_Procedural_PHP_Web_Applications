"""FR6 join rule (2026-10-20): LEFT JOIN and implicit comma-join SELECTs.

A SELECT with a LEFT JOIN or a comma join is converted only when its result row keys are known: every table is in the
parsed schema, every output column is explicit and distinctly named, and every column resolves to exactly one joined
table. The SQL text is passed unchanged to text() with named binds. Static INNER JOIN SELECTs keep the earlier rule.
Every other multi-table form keeps abstaining with an accurate reason.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from c3.conversion.query_converter import MULTI_TABLE_REASON, OUTER_JOIN_REASONS, convert_query
from c3.generation.emitter import generate_data_access_source

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import e4_injection_check as e4  # noqa: E402

UNSAFE = "Dynamic placeholder occurs in an unsupported structural SQL position."

SCHEMA = {"tables": [
    {"name": "patients", "columns": [{"name": n} for n in ("id", "full_name", "dob", "speciality_note")]},
    {"name": "appointments", "columns": [{"name": n} for n in ("appointment_no", "patient_id", "speciality", "status")]},
    {"name": "teams", "columns": [{"name": n} for n in ("id", "team_name")]},
    {"name": "agents", "columns": [{"name": n} for n in ("id", "team_id", "agent_name")]},
    {"name": "tickets", "columns": [{"name": n} for n in ("id", "agent_id", "subject", "status")]},
]}


def convert(sql, schema=SCHEMA, query_id="Q001"):
    query = {"query_id": query_id, "status": "RECOVERED", "recovered_sql": sql, "operation": sql.split()[0].upper(),
             "executor": "mysqli_query", "source_file": "fixture.php", "source_line": 1}
    return convert_query(query, schema)


ACCEPTED = [
    # implicit comma join, join condition and a bound value in WHERE
    ("SELECT appointment_no, full_name FROM patients, appointments "
     "WHERE speciality = '{{doctor}}' AND patients.id = appointments.patient_id", {"doctor"}),
    ("SELECT appointment_no, full_name, dob FROM patients, appointments "
     "WHERE appointment_no = {{appointment_no}} AND patients.id = appointments.patient_id;", {"appointment_no"}),
    ("SELECT appointment_no, full_name, speciality FROM patients, appointments WHERE patients.id = appointments.patient_id",
     set()),
    # LEFT JOIN with COUNT, GROUP BY and ORDER BY; aliases on every table
    ("SELECT m.team_name, COUNT(t.id) AS ticket_count FROM teams m LEFT JOIN agents a ON a.team_id = m.id "
     "LEFT JOIN tickets t ON t.agent_id = a.id GROUP BY m.id, m.team_name ORDER BY m.id", set()),
    # ORDER BY an output alias
    ("SELECT m.team_name, COUNT(a.id) AS agents FROM teams m LEFT JOIN agents a ON a.team_id = m.id "
     "GROUP BY m.team_name ORDER BY agents DESC", set()),
    # LEFT OUTER JOIN, a bound value in ON and in WHERE, LIMIT
    ("SELECT p.full_name, a.status FROM patients p LEFT OUTER JOIN appointments a "
     "ON a.patient_id = p.id AND a.status = '{{status}}' WHERE p.id = '{{id}}' LIMIT 10", {"status", "id"}),
    # self-join with aliases and distinct output names
    ("SELECT a.team_name, b.team_name AS other_team FROM teams a LEFT JOIN teams b ON b.id = a.id + 1", set()),
    # INNER JOIN mixed with LEFT JOIN; COUNT(*) under an alias
    ("SELECT m.team_name, COUNT(*) AS n FROM teams m INNER JOIN agents a ON a.team_id = m.id "
     "LEFT JOIN tickets t ON t.agent_id = a.id GROUP BY m.team_name", set()),
    # BETWEEN bounds and an IN list keep their accepted value positions
    ("SELECT p.full_name, a.appointment_no FROM patients p LEFT JOIN appointments a ON a.patient_id = p.id "
     "WHERE p.dob BETWEEN '{{date_from}}' AND '{{date_to}}' AND a.status IN ('{{s1}}', '{{s2}}')",
     {"date_from", "date_to", "s1", "s2"}),
]


@pytest.mark.parametrize("sql,parameters", ACCEPTED)
def test_accepted_join_shapes_convert_with_unchanged_sql_and_named_binds(sql, parameters):
    result = convert(sql)
    assert result["status"] == "CONVERTED", result["reason"]
    assert result["conversion_rule"] == "general"
    assert set(result["parameters"]) == parameters
    generated = result["parameterized_sql"]
    assert "{{" not in generated and "'" + ":" not in generated
    assert {name for name in re.findall(r"(?<![:\w]):(\w+)", generated)} == parameters
    # SQL text unchanged apart from the placeholders becoming named binds.
    expected = re.sub(r"'\{\{(\w+)\}\}'", r":\1", sql)
    assert generated == re.sub(r"\{\{(\w+)\}\}", r":\1", expected)


REJECTED = [
    # join forms outside the rule
    ("SELECT p.full_name FROM patients p RIGHT JOIN appointments a ON a.patient_id = p.id", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p FULL OUTER JOIN appointments a ON a.patient_id = p.id", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p CROSS JOIN appointments a", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p NATURAL JOIN appointments a", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p JOIN appointments a USING (id)", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p JOIN appointments a WHERE a.patient_id = p.id", MULTI_TABLE_REASON),
    ("SELECT p.full_name FROM patients p LEFT JOIN appointments a USING (id)", OUTER_JOIN_REASONS["shape"]),
    ("SELECT p.full_name FROM patients p NATURAL LEFT JOIN appointments a", OUTER_JOIN_REASONS["shape"]),
    ("SELECT p.full_name FROM patients p, appointments a STRAIGHT_JOIN teams m ON m.id = a.patient_id",
     OUTER_JOIN_REASONS["shape"]),
    ("SELECT p.full_name FROM patients p, appointments a LEFT JOIN teams m ON m.id = a.patient_id",
     OUTER_JOIN_REASONS["shape"]),
    # nesting
    ("SELECT p.full_name FROM patients p LEFT JOIN (SELECT patient_id FROM appointments) d ON d.patient_id = p.id",
     OUTER_JOIN_REASONS["shape"]),
    ("SELECT p.full_name FROM patients p LEFT JOIN appointments a ON a.patient_id = p.id "
     "WHERE p.id IN (SELECT patient_id FROM appointments)", OUTER_JOIN_REASONS["shape"]),
    ("SELECT full_name FROM patients, appointments WHERE patients.id = appointments.patient_id "
     "UNION SELECT team_name FROM teams", MULTI_TABLE_REASON),
    # tables outside the parsed schema
    ("SELECT p.full_name FROM patients p LEFT JOIN invoices i ON i.patient_id = p.id", OUTER_JOIN_REASONS["schema"]),
    ("SELECT p.full_name FROM clinic.patients p LEFT JOIN appointments a ON a.patient_id = p.id",
     OUTER_JOIN_REASONS["schema"]),
    # result shape
    ("SELECT * FROM patients p LEFT JOIN appointments a ON a.patient_id = p.id", OUTER_JOIN_REASONS["star"]),
    ("SELECT p.*, a.status FROM patients p LEFT JOIN appointments a ON a.patient_id = p.id", OUTER_JOIN_REASONS["star"]),
    ("SELECT * FROM patients, appointments WHERE patients.id = appointments.patient_id", OUTER_JOIN_REASONS["star"]),
    ("SELECT p.id, a.patient_id AS id FROM patients p LEFT JOIN appointments a ON a.patient_id = p.id",
     OUTER_JOIN_REASONS["names"]),
    ("SELECT m.id, a.id FROM teams m LEFT JOIN agents a ON a.team_id = m.id", OUTER_JOIN_REASONS["names"]),
    ("SELECT m.team_name, COUNT(a.id) FROM teams m LEFT JOIN agents a ON a.team_id = m.id GROUP BY m.team_name",
     OUTER_JOIN_REASONS["names"]),
    # column resolution
    ("SELECT id, team_name FROM teams m LEFT JOIN agents a ON a.team_id = m.id", OUTER_JOIN_REASONS["columns"]),
    ("SELECT m.team_name, a.nickname FROM teams m LEFT JOIN agents a ON a.team_id = m.id", OUTER_JOIN_REASONS["columns"]),
    ("SELECT m.team_name, bonus FROM teams m LEFT JOIN agents a ON a.team_id = m.id", OUTER_JOIN_REASONS["columns"]),
    ("SELECT x.team_name FROM teams m LEFT JOIN agents a ON a.team_id = m.id", OUTER_JOIN_REASONS["columns"]),
    # self-joins name one table but still join
    ("SELECT teams.team_name FROM teams LEFT JOIN teams ON teams.id = teams.id", OUTER_JOIN_REASONS["columns"]),
    ("SELECT * FROM teams a LEFT JOIN teams b ON b.id = a.id", OUTER_JOIN_REASONS["star"]),
    ("SELECT a.id, b.id FROM teams a, teams b WHERE b.id = a.id", OUTER_JOIN_REASONS["names"]),
    ("SELECT m.team_name AS label FROM teams m LEFT JOIN agents a ON a.team_id = m.id WHERE label = 'x'",
     OUTER_JOIN_REASONS["columns"]),
    # placeholders outside the accepted value positions
    ("SELECT m.team_name FROM teams m LEFT JOIN agents a ON '{{x}}' = a.team_id", UNSAFE),
    ("SELECT m.team_name FROM teams m LEFT JOIN agents a ON a.team_id = m.id ORDER BY '{{column}}'", UNSAFE),
    ("SELECT m.team_name FROM teams m LEFT JOIN agents a ON a.team_id = m.id WHERE a.agent_name = 'x{{v}}y'", UNSAFE),
    # UPDATE / DELETE joins are not this rule
    ("UPDATE tickets t LEFT JOIN agents a ON a.id = t.agent_id SET t.status = 'open' WHERE a.id IS NULL",
     MULTI_TABLE_REASON),
    ("DELETE t FROM tickets t LEFT JOIN agents a ON a.id = t.agent_id WHERE a.id IS NULL", MULTI_TABLE_REASON),
]


@pytest.mark.parametrize("sql,reason", REJECTED)
def test_other_join_forms_keep_abstaining_with_an_accurate_reason(sql, reason):
    result = convert(sql)
    assert result["status"] == "UNRESOLVED", sql
    assert result["reason"] == reason


def test_outer_and_comma_joins_need_a_parsed_schema():
    for sql, _ in ACCEPTED:
        result = convert(sql, schema=None)
        assert (result["status"], result["reason"]) == ("UNRESOLVED", OUTER_JOIN_REASONS["schema"])


def test_inner_join_rule_is_unchanged():
    # Static INNER JOINs convert as before: no schema needed, SELECT * allowed (pinned by the 9-app byte gate).
    for schema in (None, SCHEMA):
        result = convert("SELECT * FROM patients p JOIN appointments a ON a.patient_id = p.id WHERE p.id = '{{id}}'",
                         schema=schema)
        assert result["status"] == "CONVERTED" and result["parameters"] == ["id"]


def test_comma_join_is_told_apart_from_join_without_on():
    comma = convert("SELECT appointment_no, full_name FROM patients, appointments WHERE patients.id = appointments.patient_id")
    bare = convert("SELECT appointment_no, full_name FROM patients JOIN appointments WHERE patients.id = appointments.patient_id")
    assert comma["status"] == "CONVERTED"
    assert (bare["status"], bare["reason"]) == ("UNRESOLVED", MULTI_TABLE_REASON)


def test_join_reasons_say_what_the_rule_accepts():
    # The old text ("Only static SELECT INNER JOINs are supported") is wrong once LEFT and comma joins can convert.
    for reason in OUTER_JOIN_REASONS.values():
        assert reason.startswith("Unsupported multi-table SQL structure. ")
        assert "INNER JOINs are supported" not in reason and "only INNER" not in reason.lower()
        assert "LEFT JOIN or comma join" in reason
    # The reason for forms outside the rule names what is converted and what is not.
    assert MULTI_TABLE_REASON.startswith("Unsupported multi-table SQL structure. ")
    assert "INNER JOINs are supported" not in MULTI_TABLE_REASON
    assert all(word in MULTI_TABLE_REASON for word in ("LEFT JOIN", "comma join", "RIGHT", "UPDATE/DELETE"))


FEE = ROOT / "benchmarks" / "apps" / "_app_only" / "fee_rainbow"
FEE_SCHEMA = ROOT / "benchmarks" / "apps" / "fee_rainbow" / "database" / "db.sql"


@pytest.mark.skipif(not FEE.is_dir() or not FEE_SCHEMA.is_file(), reason="Fee-Rainbow source not available locally")
def test_fee_rainbow_left_join_sites_abstain_with_the_schema_reason(tmp_path):
    import json

    from c3.pipeline import run_pipeline

    run_pipeline(FEE, FEE_SCHEMA, tmp_path / "out")
    unresolved = {u["query_id"]: u for u in json.loads((tmp_path / "out" / "unresolved_queries.json").read_text(encoding="utf-8"))}
    for query_id in ("Q007", "Q054"):
        site = unresolved[query_id]
        assert (site["stage"], site["reason_code"]) == ("conversion", "CONVERSION_UNSUPPORTED")
        assert site["reason"] == OUTER_JOIN_REASONS["schema"]
        assert "INNER JOINs are supported" not in site["reason"]


def _generated_module(tmp_path, sqls):
    converted = [{**convert(sql, query_id=f"Q{i + 1:03d}"), "source_file": str(tmp_path / "fixture.php")}
                 for i, sql in enumerate(sqls)]
    assert all(c["status"] == "CONVERTED" for c in converted)
    source = generate_data_access_source(converted, tmp_path)
    path = tmp_path / "data_access.py"
    path.write_text(source, encoding="utf-8")
    compile(source, str(path), "exec")
    spec = importlib.util.spec_from_file_location("outer_join_generated", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, source, {c["query_id"]: c for c in converted}


def test_generated_join_functions_compile_and_bind_every_hostile_payload(tmp_path):
    module, source, converted = _generated_module(tmp_path, [sql for sql, _ in ACCEPTED])
    assert "text(f" not in source and ".format(" not in source and "% (" not in source
    assert source.count(".mappings().all()") >= len(ACCEPTED)
    verdicts = {}
    for query_id, name, function, _ in e4.generated_functions(module, converted):
        result = e4.check_function(function)
        verdicts[name] = result["verdict"]
        # A function that takes no value has no payload cases (it passes trivially, as in E4).
        expected = len(e4.PAYLOADS) if converted[query_id]["parameters"] else 0
        assert result["bound"] + result["rejected"] == expected, name
        for case in result["cases"]:
            assert not case["problems"], (name, case)
    assert len(verdicts) == len(ACCEPTED) and set(verdicts.values()) == {"SAFE"}
