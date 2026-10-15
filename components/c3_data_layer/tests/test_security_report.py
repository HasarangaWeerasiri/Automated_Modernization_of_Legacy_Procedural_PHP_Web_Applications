"""Security report (S6, FR7). Fixtures are written for this test only."""

import json
from pathlib import Path

import pytest

from c3.analysis.security_report import (
    _inside_single_quotes,
    build_security_report,
    scan_generated_code,
)
from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[3]

PHP = {
    "a_unsanitized.php": """<?php
$id = $_GET['id'];
$sql = "SELECT id FROM items WHERE id = $id";
mysqli_query($conn, $sql);
""",
    "b_int_cast_transitive.php": """<?php
$raw = $_POST['qty'] ?? '0';
$qty = (int)$raw;
mysqli_query($conn, "UPDATE items SET qty = $qty WHERE id = 1");
""",
    "c_escaped_quoted.php": """<?php
$name = mysqli_real_escape_string($conn, $_POST['name']);
mysqli_query($conn, "INSERT INTO items (name) VALUES ('$name')");
""",
    "d_escaped_unquoted.php": """<?php
$id = mysqli_real_escape_string($conn, $_GET['id']);
mysqli_query($conn, "DELETE FROM items WHERE id = $id");
""",
    "e_session_only.php": """<?php
$user = $_SESSION['user'];
mysqli_query($conn, "SELECT id FROM items WHERE owner = '$user'");
""",
    "f_dynamic_order_unresolved.php": """<?php
$sort = $_GET['sort'];
mysqli_query($conn, "SELECT id FROM items ORDER BY $sort");
""",
    "g_static.php": """<?php
mysqli_query($conn, "SELECT id FROM items");
""",
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    directory = tmp_path_factory.mktemp("security")
    for name, body in PHP.items():
        (directory / name).write_text(body, encoding="utf-8")

    ast_data = analyze_php_ast(directory)
    queries = recover_queries_from_ast(ast_data)
    converted = convert_queries(queries)
    result = build_security_report(queries, converted, ast_data["assignments"])
    return {Path(r["source_file"]).name: r for r in result["queries"]}, result


def test_unsanitized_get_value_is_legacy_injection_and_bound(report):
    by_file, _ = report
    record = by_file["a_unsanitized.php"]
    [item] = record["user_inputs"]

    assert item["sources"] == ["_GET.id"]
    assert item["legacy_risk"] == "injectable_unsanitized"
    assert item["generated_handling"] == "bound_parameter"
    assert record["legacy_injection_site"] and record["remediated"]


def test_transitive_int_cast_is_mitigated(report):
    by_file, _ = report
    [item] = by_file["b_int_cast_transitive.php"]["user_inputs"]

    assert item["sources"] == ["_POST.qty"]
    assert "numeric_cast" in item["sanitizers"]
    assert item["legacy_risk"] == "mitigated_numeric_cast"


def test_escape_inside_quotes_vs_unquoted(report):
    by_file, _ = report

    assert by_file["c_escaped_quoted.php"]["user_inputs"][0]["legacy_risk"] == "mitigated_escaped_and_quoted"
    assert by_file["d_escaped_unquoted.php"]["user_inputs"][0]["legacy_risk"] == "injectable_escaped_but_unquoted"


def test_session_is_indirect_not_user_input(report):
    by_file, _ = report
    [item] = by_file["e_session_only.php"]["user_inputs"]

    assert item["sources"] == ["_SESSION.user"]
    assert item["user_controlled"] is False
    assert by_file["e_session_only.php"]["legacy_injection_site"] is False


def test_unresolved_structural_input_is_not_converted(report):
    by_file, _ = report
    record = by_file["f_dynamic_order_unresolved.php"]

    assert record["conversion_status"] == "UNRESOLVED"
    assert record["placeholders"]["structural_positions"] == ["sort"]
    assert record["user_inputs"][0]["generated_handling"] == "not_converted"
    assert record["legacy_injection_site"] and not record["remediated"]


def test_summary_and_coverage(report):
    _, result = report
    summary = result["summary"]

    assert summary["total_queries"] == 7
    assert summary["legacy_injection_sites"] == 3  # a, d, f
    assert summary["legacy_injection_sites_remediated"] == 2  # a, d
    assert summary["legacy_injection_sites_not_converted"] == 1  # f
    assert summary["injection_safety_coverage_percent"] == 100.0


def test_scan_flags_string_built_sql_and_allows_literals():
    source = '''
from sqlalchemy import text

def select_items_q001(session, name):
    return session.execute(text("SELECT id FROM items WHERE name = :name"), {"name": name})

def select_items_q002(session, name):
    return session.execute(text(f"SELECT id FROM items WHERE name = '{name}'"))

def select_items_q003(session, name):
    return session.execute(text("SELECT id FROM items WHERE name = '" + name + "'"))
'''
    converted = [
        {"query_id": f"Q00{n}", "status": "CONVERTED"} for n in (1, 2, 3)
    ]

    findings = scan_generated_code(source, converted)

    assert [(f["function"], f["verdict"]) for f in findings] == [
        ("select_items_q002", "VIOLATION_sql_built_from_variables"),
        ("select_items_q003", "VIOLATION_sql_built_from_variables"),
    ]


def test_inside_single_quotes():
    assert _inside_single_quotes("WHERE a LIKE '%{{t}}%'", "t")
    assert not _inside_single_quotes("WHERE id = {{id}}", "id")
    assert not _inside_single_quotes("WHERE a = '{{the_user_id}}' AND b = {{id}}", "id")
    assert not _inside_single_quotes("WHERE x = 'it''s' AND y = {{v}}", "v")


def test_pipeline_writes_report_and_clinic_identifiers_are_allowlisted(tmp_path):
    clinic = ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard"

    run_pipeline(clinic / "legacy", clinic / "database" / "schema.sql", tmp_path)

    report = json.loads((tmp_path / "security_report.json").read_text(encoding="utf-8"))
    by_id = {r["query_id"]: r for r in report["queries"]}

    assert by_id["Q012"]["placeholders"]["structural_positions"] == ["sort"]
    assert by_id["Q013"]["placeholders"]["structural_positions"] == ["table"]
    assert report["summary"]["generated_code_violations"] == 0
    assert report["summary"]["injection_safety_coverage_percent"] == 100.0
    assert {f["verdict"] for f in report["generated_code_scan"]} <= {
        "allowed_schema_allowlisted_identifier",
        "allowed_literal_sql_selection",
    }
