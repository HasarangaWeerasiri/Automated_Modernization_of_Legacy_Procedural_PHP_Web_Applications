"""C3 Migration Studio: the dashboard shows only figures from dashboard_data.json,
and those figures reconcile with the committed evidence they cite.

Reads the generated data file and the dashboard sources; runs no pipeline.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "components" / "c3_data_layer" / "dashboard"
DATA_FILE = DASHBOARD / "data" / "dashboard_data.json"

spec = importlib.util.spec_from_file_location("summarize_evidence", ROOT / "scripts" / "summarize_evidence.py")
summarize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize)

pytestmark = pytest.mark.skipif(not DATA_FILE.is_file(), reason="dashboard_data.json not generated")

NUMBER = re.compile(r"(?<![\w.$\-#])\d+(?:\.\d+)?(?![\w])")
FIELD_EXPRESSION = re.compile(r"^[\w.]+( \+ [\w.]+)*$")


@pytest.fixture(scope="module")
def data():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _code_without_layout(source: str) -> str:
    source = re.sub(r"/\* ===== LAYOUT.*?===== END LAYOUT ===== \*/", "", source, flags=re.S)
    source = re.sub(r"^const SVG_NS = .*$", "", source, flags=re.M)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _resolve(document, expression):
    total = 0
    for path in expression.split(" + "):
        node = document
        for part in path.split("."):
            node = node[part]
        total += node
    return total


# 1. Every displayed research statistic comes from dashboard_data.json ------------------------

def test_app_js_has_no_research_numbers_outside_layout():
    code = _code_without_layout((DASHBOARD / "app.js").read_text(encoding="utf-8"))
    literals = {float(n) for n in NUMBER.findall(code)}
    assert literals <= {0.0, 1.0}, f"numeric literals outside LAYOUT: {sorted(literals)}"


def test_index_html_has_no_research_numbers():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    literals = {float(n) for n in NUMBER.findall(html)}
    assert literals <= {0.0, 1.0}, f"numeric literals in index.html: {sorted(literals)}"


def test_app_js_loads_the_generated_data_file():
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert 'fetch("data/dashboard_data.json"' in code


# 2. Measurements reconcile with their evidence ------------------------------------------------

def test_measurements_with_json_fields_match_their_source_files(data):
    checked = 0
    for m in data["measurements"]:
        source = m["source"]
        file = source.get("file")
        if not file or file.startswith("<temp>") or ";" in file or not file.endswith(".json") or "fields" not in source:
            continue
        document = json.loads((ROOT / file).read_text(encoding="utf-8-sig"))
        for role, expression in source["fields"].items():
            if not FIELD_EXPRESSION.match(expression):
                continue
            assert _resolve(document, expression) == m[role], (m["id"], role, expression)
            checked += 1
    assert checked > 100


def test_every_measurement_names_app_stage_metric_status_and_source(data):
    for m in data["measurements"]:
        assert m["app"] and m["stage"] and m["stage_label"] and m["metric"] and m["label"], m["id"]
        assert m["status"] in {"measured", "derived", "not_measured"}, m["id"]
        if m["status"] != "not_measured":
            assert m["source"]["file"], m["id"]
            assert m["source"].get("fields") or m["source"].get("derivation"), m["id"]
        if m["status"] == "derived":
            assert m["source"].get("derivation"), m["id"]


def test_current_code_counts_match_the_generated_results_summary(data):
    summary = (ROOT / "docs" / "C3_results_summary.md").read_text(encoding="utf-8")
    for app_id, app in data["apps"].items():
        row = next(line for line in summary.splitlines() if line.startswith(f"| {app['label']} | `benchmarks/apps/"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        expected = dict(zip(["sql_detected", "sql_recovered", "converted_functions", "converted_template",
                             "unresolved_recovery", "unresolved_conversion"], map(int, cells[3:9])))
        for metric, value in expected.items():
            m = next(x for x in data["measurements"] if x["app"] == app_id and x["metric"] == metric and x["stage"] == "current-code")
            assert m["value"] == value, (app_id, metric)


def test_records_reconcile_with_current_code_counts(data):
    for app_id, records in data["records"].items():
        get = lambda metric: next(m["value"] for m in data["measurements"]
                                  if m["app"] == app_id and m["metric"] == metric and m["stage"] == "current-code")
        assert len(records) == get("sql_detected")
        assert sum(r["conversion_status"] == "CONVERTED" for r in records) == get("converted_functions")
        assert sum(r["unresolved_stage"] is not None for r in records) == get("unresolved_total")
        assert get("converted_general") + get("converted_template") == get("converted_functions")


def test_e1_e4_and_tests_match_their_evidence(data):
    final = next(r for r in data["experiments"]["e1"]["runs"] if r["kind"] == "final")
    source = json.loads((ROOT / final["file"]).read_text(encoding="utf-8"))["apps"][final["app"]]
    assert final["sites"] == source["sites"]
    assert final["verdicts"] == {v: source["verdicts"].get(v, 0) for v in ("PASS", "FAIL", "INCONCLUSIVE")}
    assert final["verdicts"] == {"PASS": 27, "FAIL": 0, "INCONCLUSIVE": 17}

    e4 = data["experiments"]["e4"]
    report = json.loads((ROOT / summarize.E4_REPORT).read_text(encoding="utf-8"))
    assert e4["totals"]["functions"] == sum(a["functions"] for a in report["apps"].values())
    assert e4["totals"]["hostile_cases"] == sum(len(f["cases"]) for a in report["apps"].values() for f in a["detail"].values())
    assert e4["totals"]["unsafe"] == 0 and e4["totals"]["safe"] == e4["totals"]["functions"]
    assert (e4["totals"]["functions"], e4["totals"]["hostile_cases"], e4["totals"]["benign_calls"]) == (159, 1908, 636)

    tests = data["experiments"]["tests"]
    log = (ROOT / summarize.TEST_LOG).read_text(encoding="utf-8")
    assert tests["summary_line"] in log
    assert (tests["passed"], tests["failed"], tests["skipped"]) == (783, 0, 61)


# 3. Clinic and Fee-Rainbow disclosures ----------------------------------------------------------

def test_clinic_disclosure(data):
    clinic = data["disclosures"]["clinic"]
    records = data["records"]["clinic_inventory_legacy_hard"]
    templates = [r["query_id"] for r in records if r["conversion_rule"] == "query_specific_template"]
    assert clinic["template_query_ids"] == templates == ["Q010", "Q011", "Q012", "Q013", "Q014", "Q016", "Q020"]
    assert (clinic["converted"], clinic["general_rule"], clinic["query_specific_template"]) == (22, 15, 7)
    share = clinic["general_rule_share"]
    assert (share["numerator"], share["denominator"], share["value"], share["status"]) == (15, 22, 68.2, "derived")
    assert "code-derived" in clinic["text"] and "has not been measured" in clinic["text"]
    m = next(x for x in data["measurements"] if x["metric"] == "general_rule_share")
    assert m["status"] == "derived" and "has not been measured" in m["note"]


def test_fee_rainbow_disclosure(data):
    fee = data["disclosures"]["fee_rainbow"]
    assert fee["converted"] == fee["recoverable_converted"] + len(fee["ambiguous_converted"])
    assert (fee["converted"], fee["recoverable_converted"], fee["ambiguous_converted"], fee["ambiguous_not_converted"]) == \
        (40, 39, ["G011"], ["G007"])


def test_dignity_dev_is_the_only_fresh_holdout_and_keeps_its_write_accuracy(data):
    holdout = {m["app"] for m in data["measurements"] if m["stage"] == "fresh-holdout"}
    assert holdout == {"dignity_dev_hms"}
    writes = [m for m in data["measurements"] if m["app"] == "dignity_dev_hms" and m["metric"] == "column_use_write"]
    assert writes and all((m["numerator"], m["denominator"], m["value"]) == (5, 7, 71.4) for m in writes)


# 4. Stage labels are preserved -----------------------------------------------------------------

def test_stage_labels_are_preserved(data):
    labels = {(relative.split("/")[0], label): summarize.STAGE_KEYS[relative] for _, relative, label in summarize.STAGES
              if relative in summarize.STAGE_KEYS}
    for m in data["measurements"]:
        if m["stage"] in {"pre-adaptation", "post-adaptation", "post-fix", "fresh-holdout"} and m["category"] not in {"e1", "e2", "e3"}:
            base = m["stage_label"].replace(" — recovery only", "")
            assert labels[(m["app"], base)] == m["stage"], m["id"]
    assert set(data["stage_definitions"]) == {"pre-adaptation", "post-adaptation", "post-fix", "fresh-holdout", "current-code"}


# 5. Missing measurements display "Not yet measured" -------------------------------------------

def test_missing_measurements_are_not_measured_never_zero(data):
    missing = {m["metric"]: m for m in data["measurements"] if m["status"] == "not_measured"}
    for metric in ("execution_time", "migration_effort", "sqlacodegen_baseline", "e1_equivalence"):
        assert metric in missing
    assert all(m["value"] is None and m["numerator"] is None for m in missing.values())
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert 'const NOT_MEASURED = "Not yet measured"' in code
    assert 'm.status === "not_measured"' in code


def test_zero_denominator_is_not_applicable_not_missing(data):
    empty = [m for m in data["measurements"] if m["denominator"] == 0]
    assert empty, "expected HMS/Helpdesk correct-abstention rows with no non-recoverable sites"
    for m in empty:
        assert m["status"] != "not_measured" and m["value"] is None and m["note"].startswith("Not applicable"), m["id"]
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert 'const NOT_APPLICABLE = "Not applicable"' in code


def test_manual_review_rate_is_derived_not_effort(data):
    rates = [m for m in data["measurements"] if m["metric"] == "manual_review_rate"]
    assert rates and all(m["status"] == "derived" and "not a measurement of developer effort" in m["note"] for m in rates)


def test_no_overall_accuracy_score(data):
    assert not [m for m in data["measurements"] if re.search(r"overall|combined", m["metric"] + m["label"], re.I)]


# 6. Historical accuracy is never assigned to uploaded applications -----------------------------

def test_try_it_never_reads_evidence_data():
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    block = re.search(r"/\* ===== TRY IT \(no evidence data\) ===== \*/(.*?)/\* ===== END TRY IT ===== \*/", code, re.S).group(1)
    for forbidden in ("DATA", "measurement", "Evidence", "fetch(", "dashboard_data", "records", "experiments"):
        assert forbidden not in block, forbidden


def test_try_it_panel_is_ui_only():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    panel = re.search(r'<section id="panel-tryit".*?</section>', html, re.S).group(0)
    assert re.search(r'<button[^>]*id="run-migration"[^>]*\bdisabled\b[^>]*>Local migration connection pending</button>', panel)
    assert not NUMBER.findall(re.sub(r"<[^>]+>", " ", panel))
    assert panel.count('class="kpi-value">—<') == 4


# Privacy ----------------------------------------------------------------------------------------

def test_export_contains_no_sql_values_credentials_or_paths(data):
    summarize._privacy_check(data)
    text = DATA_FILE.read_text(encoding="utf-8")
    for key in ('"recovered_sql"', '"parameterized_sql"', '"sql_argument"', '"payloads"', '"inputs"', '"database"', '"cases"'):
        assert key not in text, key
    assert all(r["unresolved_reason"] is None for r in data["records"]["hms_kishan0725"])


def test_dashboard_option_is_opt_in():
    with pytest.raises(SystemExit):
        summarize.main(["--no-markdown"])
