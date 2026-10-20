"""C3 Migration Studio: the dashboard shows only figures from dashboard_data.json,
and those figures reconcile with the committed evidence they cite.

Reads the generated data file and the dashboard sources; runs no pipeline.
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "components" / "c3_data_layer" / "dashboard"
DATA_FILE = DASHBOARD / "data" / "dashboard_data.json"

sys.dont_write_bytecode = True  # importing the script must not create scripts/__pycache__
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
    assert (e4["totals"]["functions"], e4["totals"]["hostile_cases"], e4["totals"]["benign_calls"]) == (163, 1956, 652)


def test_test_counts_come_only_from_saved_committed_logs(data):
    runs = data["experiments"]["tests"]["runs"]
    assert [r["file"] for r in runs] == [file for _, _, _, file, _ in summarize.TEST_LOGS]
    for run in runs:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", run["file"]], cwd=ROOT, capture_output=True)
        assert tracked.returncode == 0, f"{run['file']} is not a committed log"
        log = (ROOT / run["file"]).read_text(encoding="utf-8-sig")
        assert run["summary_line"] in log
        counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped)", run["summary_line"])}
        assert (run["passed"], run["failed"], run["skipped"]) == tuple(counts.get(k, 0) for k in ("passed", "failed", "skipped"))
        assert re.search(rf"^# git HEAD: {run['head']}$", log, re.M)
        for metric in ("passed", "failed", "skipped"):
            m = next(x for x in data["measurements"] if x["stage_label"] == run["stage_label"] and x["metric"] == f"{run['metric_prefix']}_{metric}")
            assert m["value"] == run[metric] and m["source"]["file"] == run["file"] and m["status"] == "measured"
    # Superseded logs keep their original measurement ids as dated history, not the latest run.
    assert [r["latest"] for r in runs] == [True, True, False, False, False, False, False]
    ids = {m["id"] for m in data["measurements"]}
    assert {"all|current code (tests, 2026-10-17)|tests_passed", "all|saved log (2026-10-18, HEAD 1d93e93)|tests_passed",
            "all|saved log (2026-10-18, HEAD 1d93e93)|tests_with_mysql_passed"} <= ids


def test_latest_saved_test_logs_are_the_post_sprint_runs(data):
    latest = {r["key"]: r for r in data["experiments"]["tests"]["runs"] if r["latest"]}
    assert (latest["non_mysql"]["passed"], latest["non_mysql"]["failed"], latest["non_mysql"]["skipped"]) == (990, 0, 74)
    assert (latest["with_mysql"]["passed"], latest["with_mysql"]["failed"], latest["with_mysql"]["skipped"]) == (1064, 0, 0)
    # The full suite with MySQL is the non-MySQL tree with MySQL available: same test count, no extra suites.
    assert latest["with_mysql"]["passed"] == latest["non_mysql"]["passed"] + latest["non_mysql"]["skipped"]
    suite = data["experiments"]["e1"]["separate_suites"][0]
    assert (suite["passed"], suite["collected"], suite["file"]) == (76, 76, summarize.CLINIC_MYSQL_LOG)
    assert suite["file"].startswith("evidence/mysql_runs/2026-10-20_135617/")
    assert (suite["join_rule_tests"], suite["join_rule_passed"]) == (13, 13)
    assert "already contains the Clinic MySQL verification tests" in data["experiments"]["tests"]["note"]



def test_join_rule_evidence_is_scoped_and_coverage_is_shown_before_and_after(data):
    # The 13 FR6 join-rule MySQL tests are direct SQL vs generated function, counted inside the Clinic suite total.
    suite = data["experiments"]["e1"]["separate_suites"][0]
    join = next(m for m in data["measurements"] if m["metric"] == "join_rule_mysql_tests_passed")
    assert (join["value"], join["denominator"], join["source"]["file"]) == (13, 13, summarize.CLINIC_MYSQL_LOG)
    for text in (join["label"], suite["note"]):
        assert "direct SQL vs generated function" in text and "not" in text and "PHP" in text
    log = (ROOT / suite["file"]).read_text(encoding="utf-8-sig")
    assert len(re.findall(rf"^{re.escape(summarize.JOIN_RULE_TESTS)}::\S+ PASSED", log, re.M)) == 13
    # Conversion coverage before (adeb1c8) and after the join rule, from the committed post-T2 record.
    coverage = json.loads((ROOT / summarize.JOIN_COVERAGE).read_text(encoding="utf-8"))["apps"]
    expected = {"dignity_dev_hms": ((4, 7), (7, 7)), "helpdesk_legacy_holdout": ((8, 9), (9, 9)), "fee_rainbow": ((39, 40), (39, 40))}
    for app, (before, after) in expected.items():
        cur = next(m for m in data["measurements"] if m["id"] == f"{app}|current code|conversion_coverage_gt")
        pre = next(m for m in data["measurements"] if m["id"] == f"{app}|before the FR6 join rule (adeb1c8)|conversion_coverage_gt")
        assert (pre["numerator"], pre["denominator"]) == before == (coverage[app]["prejoins"]["converted_recoverable"], coverage[app]["denominator"])
        assert (cur["numerator"], cur["denominator"]) == after
    # The historical Dignity-Dev fresh-holdout evaluation is not replaced.
    assert data["apps"]["dignity_dev_hms"]["latest_evaluation"] == "evidence/evaluation/dignity_dev_hms/evaluation.json"
    fr6 = next(l for l in data["limitations"] if l["fr"] == "FR6")
    assert "INNER JOIN SELECTs only" not in fr6["text"] and "result-shape rule" in fr6["text"]
    assert {(s["app"], s["query_id"]) for s in fr6["sites"]} == {("fee_rainbow", "Q007"), ("fee_rainbow", "Q054")}
    items = {item["fr"]: item for item in data["functions"]["items"]}
    assert items["FR6"]["status"] == "Partial"
    assert "not differential testing of application PHP" in items["FR6"]["limitation"]

def test_e3_runs_match_their_evidence_and_the_newest_is_shown_per_app(data):
    runs = data["experiments"]["e3_runs"]
    assert [r["dir"] for r in runs] == [directory for _, _, directory in summarize.E3_RUNS]
    for run in runs:
        summary = (ROOT / run["dir"] / "summary.txt").read_text(encoding="utf-8-sig")
        assert f"# git HEAD: {run['git_head']}" in summary and f"# date: {run['date']}" in summary
        verdicts = [json.loads(p.read_text(encoding="utf-8-sig"))["verdict"] for p in (ROOT / run["dir"]).glob("*.json")]
        assert (run["pass"], run["apps"]) == (verdicts.count("PASS"), len(verdicts))
        m = next(x for x in data["measurements"] if x["metric"] == "e3_roundtrip_pass" and x["stage_label"] == run["stage_label"])
        assert (m["numerator"], m["denominator"]) == (run["pass"], run["apps"]) and m["source"]["file"].startswith(run["dir"])
    current = next(r for r in runs if r["current"])
    assert set(data["experiments"]["e3_dates"].values()) == {current["date"]}
    for app_id in data["apps"]:
        key = summarize.EXPERIMENT_KEY.get(app_id, app_id)
        source = json.loads((ROOT / current["dir"] / f"{key}.json").read_text(encoding="utf-8-sig"))
        assert data["experiments"]["e3"][app_id]["verdict"] == source["verdict"]
        assert data["experiments"]["e3"][app_id]["steps"] == source["steps"]
    # The 15 Oct run keeps its original measurement id as dated history.
    assert any(m["id"] == "all|post-adaptation (E3, 2026-10-15)|e3_roundtrip_pass" for m in data["measurements"])


def _strings(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def test_migration_impact_detail_only_for_publishable_apps(data):
    ba = data["before_after"]
    assert ba["detail_apps"] == list(summarize.BEFORE_AFTER_DETAIL)
    assert not set(ba["detail_apps"]) & summarize.SENSITIVE_APPS
    assert set(ba["apps"]) == set(data["apps"])
    for app_id, payload in ba["apps"].items():
        assert payload["detail"] is (app_id in ba["detail_apps"])
        if not payload["detail"]:
            # Counts only: no SQL, PHP line, generated code, file, table or column names.
            assert "queries" not in payload and "tables" not in payload, app_id
            assert all(isinstance(v, (int, dict)) for k, v in payload["facts"].items()), app_id
    # SQL text appears only in the SQL / code fields of the publishable apps.
    for path, text in _strings(data):
        if any(p.search(text) for p in summarize.SQL_TEXT):
            assert summarize.BEFORE_AFTER_SQL_FIELD.match(path), path
    # HMS: no reason text either (codes and counts only).
    assert all(not item["reasons"] for item in ba["apps"]["hms_kishan0725"]["limitations"])


HMS_PHP = ROOT / "benchmarks" / "apps" / "_app_only" / "hms_kishan0725"
HMS_DUMP = ROOT / "benchmarks" / "apps" / "hms_kishan0725" / "myhmsdb.sql"


@pytest.mark.skipif(not HMS_PHP.is_dir() or not HMS_DUMP.is_file(), reason="HMS source is not in this checkout (no licence)")
def test_no_hms_source_text_in_dashboard_data(data):
    text = DATA_FILE.read_text(encoding="utf-8")
    hms = data["before_after"]["apps"]["hms_kishan0725"]
    assert hms["detail"] is False and "queries" not in hms and "tables" not in hms
    sql_literals, lines = set(), set()
    for php in HMS_PHP.rglob("*.php"):
        source = php.read_text(encoding="utf-8", errors="replace")
        for literal in re.findall(r"\"([^\"]{20,})\"|'([^']{20,})'", source):
            value = " ".join((literal[0] or literal[1]).split())
            if re.search(r"\b(select|insert|update|delete)\b", value, re.I):
                sql_literals.add(value)
        lines |= {line.strip() for line in source.splitlines() if "mysqli_query" in line and len(line.strip()) > 30}
    assert sql_literals and lines, "expected SQL string literals and query calls in the HMS source"
    flat = " ".join(text.split())
    assert not [s for s in sql_literals if s in flat]
    assert not [line for line in lines if json.dumps(line)[1:-1] in text]
    emails = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", HMS_DUMP.read_text(encoding="utf-8", errors="replace")))
    assert not [e for e in emails if e in text]


def test_migration_impact_counts_reconcile_with_the_run_measurements(data):
    current = lambda app, metric: next(m["value"] for m in data["measurements"]
                                       if m["app"] == app and m["metric"] == metric and m["stage"] == "current-code")
    for app_id, payload in data["before_after"]["apps"].items():
        f, b = payload["facts"], {x["key"]: x for x in payload["benefits"]}
        assert (f["detected"], f["recovered"], f["converted"], f["templates"], f["unresolved"],
                f["unresolved_recovery"], f["unresolved_conversion"]) == (
            current(app_id, "sql_detected"), current(app_id, "sql_recovered"), current(app_id, "converted_functions"),
            current(app_id, "converted_template"), current(app_id, "unresolved_total"), current(app_id, "unresolved_recovery"),
            current(app_id, "unresolved_conversion"))
        assert f["converted"] + f["unresolved_recovery"] + f["unresolved_conversion"] == f["detected"]
        assert f["general_rules"] + f["templates"] == f["converted"]
        assert (b["functions_generated"]["value"], b["functions_generated"]["of"]) == (f["converted"], f["detected"])
        assert b["flagged_for_review"]["value"] == f["unresolved"]
        e4 = data["experiments"]["e4"]["apps"][app_id]
        assert (b["e4"]["value"], b["e4"]["of"], b["e4"]["source"]["file"]) == (e4["safe"], e4["functions"], summarize.E4_REPORT)
        assert f["orm_classes"] + f["core_tables"] == f["tables"] == data["experiments"]["e2"][app_id]["tables"]
        assert f["columns"] == data["experiments"]["e2"][app_id]["columns"]
        assert f["core_tables"] == data["artifacts"][app_id]["counts"]["core_tables"]
        assert f["trace_entries"] == f["detected"] and f["data_access_functions"] == f["converted"]
        assert sum(item["count"] for item in payload["limitations"]) == f["unresolved"]
        assert all(item["future_work_status"] == "Proposed future work, not implemented" for item in payload["limitations"])
        if payload["detail"]:
            queries = payload["queries"]
            assert len(queries) == f["detected"] and sum(q["status"] == "converted" for q in queries) == f["converted"]
            assert sum(q["flag"] is not None for q in queries) == f["unresolved"]
            assert sum(1 for q in queries if q["dynamic_values"]) == f["dynamic_value_sites"]
            assert len({q["site"].rsplit(":", 1)[0] for q in queries}) == f["php_files"]
            assert len(payload["tables"]) == f["tables"]
            assert sum(t["no_primary_key"] for t in payload["tables"]) == f["core_tables"]
            assert sum(len(t["model"]["relationships"]) for t in payload["tables"] if t["model"]) == f["relationships"]


def test_explorer_mappings_come_from_traceability_and_flags_from_the_unresolved_report(data):
    for app_id in data["before_after"]["detail_apps"]:
        payload = data["before_after"]["apps"][app_id]
        records = {r["query_id"]: r for r in data["records"][app_id]}
        for q in payload["queries"]:
            record = records[q["query_id"]]
            assert q["site"] == f"{record['file']}:{record['line']}"
            assert not re.match(r"^[A-Za-z]:|/", q["site"])
            if q["status"] == "converted":
                fn = q["function"]
                assert fn and q["function_kind"] == "data_access" and not fn["raises_not_implemented"]
                assert fn["cited_query_id"] in (None, q["query_id"]) and fn["cited_site"] in (None, q["site"])
                assert fn["code"].startswith(f"def {fn['name']}(")
                assert all(f":{key}" in fn["code"] for key in fn["bind_keys"])
                assert [m["parameter"] for m in fn["parameter_mapping"]] == fn["parameters"] or not fn["parameter_mapping"]
                assert q["flag"] is None
            else:
                assert q["flag"]["reason_code"] == record["unresolved_reason_code"]
                assert q["flag"]["stage"] == record["unresolved_stage"]
                assert q["function"] is None or q["function"]["raises_not_implemented"]
            if q["security"]["remediated"]:
                assert q["security"]["legacy_injection_site"] and q["status"] == "converted"


def test_conversion_limitations_are_grouped_by_reason_family(data):
    for app_id, payload in data["before_after"]["apps"].items():
        families = [l for l in payload["limitations"] if l["code"] == "CONVERSION_UNSUPPORTED"]
        assert all(l["family"] in ("multi_table", "placeholder_position", "other") for l in families), app_id
        assert all(l["family"] is None for l in payload["limitations"] if l["code"] != "CONVERSION_UNSUPPORTED"), app_id
        assert len({l["family"] for l in families}) == len(families), app_id  # one entry per family
        for l in families:
            text = (l["category"] + " " + l["why"] + " " + l["future_work"]).lower()
            assert ("join" in text) is (l["family"] == "multi_table"), (app_id, l["family"])
        if app_id in data["records"]:
            reasons = [r["unresolved_reason"] or "" for r in data["records"][app_id] if r["unresolved_reason_code"] == "CONVERSION_UNSUPPORTED"]
            expected = {}
            for reason in reasons:
                key = ("multi_table" if reason.startswith("Unsupported multi-table SQL structure")
                       else "placeholder_position" if reason.startswith("Dynamic placeholder occurs in an unsupported structural SQL position")
                       else "other")
                expected[key] = expected.get(key, 0) + 1
            assert {l["family"]: l["count"] for l in families} == expected, app_id
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert "traced sources (may pass through a query result)" in code.lower()


def test_conversion_family_classifier_and_texts():
    ba_spec = importlib.util.spec_from_file_location("before_after_family", DASHBOARD / "before_after.py")
    ba = importlib.util.module_from_spec(ba_spec)
    ba_spec.loader.exec_module(ba)
    assert ba.conversion_family("Unsupported multi-table SQL structure. Only static SELECT INNER JOINs are supported.") == "multi_table"
    assert ba.conversion_family("Dynamic placeholder occurs in an unsupported structural SQL position.") == "placeholder_position"
    assert ba.conversion_family("Unsupported SQL operation: REPLACE") == "other"
    info = {key: texts for key, _, texts in ba.CONVERSION_FAMILIES}
    assert "join" not in " ".join(info["placeholder_position"]).lower()
    assert "join" in " ".join(info["multi_table"]).lower()


def test_verification_is_never_borrowed_from_another_app(data):
    e1_apps = {r["app"] for r in data["experiments"]["e1"]["runs"] if r["kind"] == "final"}
    suite_apps = {s["app"] for s in data["experiments"]["e1"]["separate_suites"]}
    for app_id, payload in data["before_after"]["apps"].items():
        v = payload["verification"]
        assert (v["e1"] is not None) is (app_id in e1_apps)
        assert (v["mysql_suite"] is not None) is (app_id in suite_apps)
        e4 = data["experiments"]["e4"]["apps"][app_id]
        assert (v["e4"]["safe"], v["e4"]["functions"]) == (e4["safe"], e4["functions"])
        assert "not a proof of general injection safety" in v["e4"]["note"]
    assert data["before_after"]["apps"]["hms_kishan0725"]["verification"]["e1"]["verdicts"] == \
        next(r["verdicts"] for r in data["experiments"]["e1"]["runs"] if r["kind"] == "final")


def test_coverage_rows_cover_every_app_with_comparable_units(data):
    ba = data["before_after"]
    assert [r["app"] for r in ba["coverage"]] == list(ba["apps"])
    for row in ba["coverage"]:
        f = ba["apps"][row["app"]]["facts"]
        assert (row["detected"], row["converted"], row["unresolved_recovery"], row["unresolved_conversion"]) == \
            (f["detected"], f["converted"], f["unresolved_recovery"], f["unresolved_conversion"])
    assert ba["coverage_note"] == ("Conversion coverage is measured over detected sites and does not establish "
                                   "complete application migration accuracy.")


def test_dignity_dev_parameter_and_user_input_counts_are_explained(data):
    payload = data["before_after"]["apps"]["dignity_dev_hms"]
    f = payload["facts"]
    assert f["functions_with_bind_parameters"] > 0 and f["user_controlled_values"] == 0
    text = " ".join(payload["explanations"])
    assert "different definitions" in text and "does not show that the application has no user input" in text
    assert "function parameters" in text


def test_migration_impact_view_makes_no_unsupported_claims(data):
    note = "These are measured properties of the generated code, not performance claims."
    assert data["before_after"]["note"] == note
    claim = re.compile(r"\b(faster|speed|speedup|seconds?|minutes?|hours?|effort|performance|productivity|saves?)\b", re.I)
    for payload in data["before_after"]["apps"].values():
        assert payload["note"] == note
        for b in payload["benefits"]:
            assert b["source"]["file"] and (b["source"].get("field") or b["source"].get("derivation")), b["key"]
            assert not claim.search(b["label"] + " " + (b.get("note") or "")), b["label"]
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    block = re.search(r"/\* ===== MIGRATION IMPACT .*?===== \*/(.*?)/\* ===== END MIGRATION IMPACT ===== \*/", code, re.S).group(1)
    # The renderer reads only the payload it is given: no global data, no fetch, no Evidence measurement helpers.
    for forbidden in ("DATA", "dashboard_data", "fetch(", "measurement(", "measurements(", "byId("):
        assert forbidden not in block, forbidden
    # No complexity, maintainability-index, LOC-reduction or speed claim; only the explicit disclaimer may name them.
    stripped = block.replace("No cyclomatic complexity, maintainability index, cognitive complexity or lines-of-code comparison is shown", "")
    assert not re.search(r"cyclomatic|maintainability index|lines of code|lines-of-code|faster|speedup|\bhours\b", stripped, re.I)
    # 0 of 0 is shown as not applicable, never as a ratio.
    assert 'const NA = "N/A - No applicable cases identified"' in block and "of === 0 && v === 0 ? NA" in block
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    assert 'data-section="impact">Migration Impact<' in html and '"impact"' in code
    assert 'section === "before-after" ? "impact"' in code  # old links still open the view


def test_fr9_and_fr10_cite_the_newest_committed_evidence(data):
    items = {item["fr"]: item for item in data["functions"]["items"]}
    current_e3 = next(r for r in data["experiments"]["e3_runs"] if r["current"])
    assert items["FR9"]["measurements"] == [f"all|{current_e3['stage_label']}|e3_roundtrip_pass"]
    latest = [r for r in data["experiments"]["tests"]["runs"] if r["latest"]]
    expected = [f"all|{r['stage_label']}|{r['metric_prefix']}_passed" for r in latest]
    assert [m for m in items["FR10"]["measurements"] if "|tests" in m] == expected
    by_id = {m["id"]: m for m in data["measurements"]}
    assert all(by_id[m]["source"]["file"] == r["file"] for m, r in zip(expected, latest))


def test_clinic_mysql_suite_is_separate_from_the_e1_harness(data):
    suite = data["experiments"]["e1"]["separate_suites"][0]
    log = (ROOT / suite["file"]).read_text(encoding="utf-8-sig")
    assert re.search(rf"collected {suite['collected']} items", log) and re.search(rf"\b{suite['passed']} passed\b", log)
    assert suite["failed"] == 0 and "not the E1 harness" in suite["note"]
    m = next(x for x in data["measurements"] if x["metric"] == "mysql_equivalence_tests_passed")
    assert (m["app"], m["value"], m["denominator"], m["category"]) == (suite["app"], suite["passed"], suite["collected"], "mysql_tests")
    assert not any(x["category"] == "e1" and x["app"] == suite["app"] and x["status"] != "not_measured" for x in data["measurements"])
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert "Clinic MySQL verification (not E1)" in code


def test_sqlacodegen_baseline_is_measured_and_matches_its_summary(data):
    source = json.loads((ROOT / summarize.BASELINE_SUMMARY).read_text(encoding="utf-8"))
    base = data["experiments"]["baseline"]
    compared = [e for e in source["apps"].values() if e["status"] == "compared"]
    assert (base["apps_total"], base["apps_compared"], base["failures"]) == (len(source["apps"]), len(compared), sorted(source["failures"]))
    for dimension in summarize.BASELINE_DIMENSIONS:
        expected = {role: sum(e["comparison"]["counts"][dimension][role] for e in compared) for role in ("compared", "matches", "differences")}
        assert base["totals"][dimension] == expected
        m = next(x for x in data["measurements"] if x["metric"] == f"sqlacodegen_{dimension}")
        assert (m["status"], m["numerator"], m["denominator"]) == ("measured", expected["matches"], expected["compared"])
        assert m["source"]["file"] == summarize.BASELINE_SUMMARY
    assert base["foreign_key_apps"] == [a for a, e in source["apps"].items() if e["comparison"]["counts"]["foreign_keys"]["compared"]]
    notes = " ".join(base["notes"])
    assert "never reads PHP" in notes and "not a general correctness guarantee" in notes and "no comparison of accuracy or speed" in notes
    assert not any(m["metric"] == "sqlacodegen_baseline" for m in data["measurements"])


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
            base = m["stage_label"].replace(" - recovery only", "")
            assert labels[(m["app"], base)] == m["stage"], m["id"]
    assert set(data["stage_definitions"]) == {"pre-adaptation", "post-adaptation", "post-fix", "fresh-holdout", "current-code"}


# 5. Missing measurements display "Not yet measured" -------------------------------------------

def test_missing_measurements_are_not_measured_never_zero(data):
    missing = {m["metric"]: m for m in data["measurements"] if m["status"] == "not_measured"}
    for metric in ("migration_effort", "e1_equivalence"):
        assert metric in missing
    assert "execution_time" not in missing  # measured since 2026-10-18 (evidence/performance/2026-10-18)
    assert "sqlacodegen_baseline" not in missing  # completed: evidence/baseline/2026-10-19
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


# Manual-review rate ---------------------------------------------------------------------------------

def _measurement(data, app, metric):
    return next(m for m in data["measurements"] if m["app"] == app and m["metric"] == metric and m["stage"] == "current-code")


def test_manual_review_reconciles_with_each_apps_summary_counts(data):
    review = data["manual_review"]["apps"]
    assert set(review) == set(data["apps"])
    for app, r in review.items():
        counts = r["summary_counts"]
        # summary_counts are the run's summary.json values; they must match the current-code measurements too
        assert counts["total_queries"] == _measurement(data, app, "sql_detected")["value"]
        assert counts["unresolved_total"] == _measurement(data, app, "unresolved_total")["value"]
        assert counts["unresolved_recovery_stage"] == _measurement(data, app, "unresolved_recovery")["value"]
        assert counts["unresolved_conversion_stage"] == _measurement(data, app, "unresolved_conversion")["value"]
        # every unresolved site is either counted (live) or excluded (non-live), once
        assert r["not_converted_live"] + len(r["excluded_non_live"]) == counts["unresolved_total"]
        recovery = sum(s["stage"] == "recovery" for s in r["sites"] + r["excluded_non_live"])
        conversion = sum(s["stage"] == "conversion" for s in r["sites"] + r["excluded_non_live"])
        assert (recovery, conversion) == (counts["unresolved_recovery_stage"], counts["unresolved_conversion_stage"])
        assert r["not_converted_live"] == len(r["sites"]) == len({s["site"] for s in r["sites"]})
        assert r["not_converted_live"] == r["unresolved_recovery_live"] + r["unresolved_conversion_live"]
        assert not {s["site"] for s in r["sites"]} & {e["site"] for e in r["excluded_non_live"]}
        assert r["value"] == round(100 * r["not_converted_live"] / r["live_sites"], 1)
        m = _measurement(data, app, "manual_review_rate")
        assert (m["numerator"], m["denominator"], m["value"], m["status"]) == (r["not_converted_live"], r["live_sites"], r["value"], "derived")


def test_manual_review_denominator_is_live_sites(data):
    for app, r in data["manual_review"]["apps"].items():
        latest = data["apps"][app]["latest_evaluation"]
        if latest:
            evaluation = json.loads((ROOT / latest).read_text(encoding="utf-8"))
            assert r["live_sites"] == evaluation["gt_live_sites"] and r["verified_ground_truth"] and r["label"] is None
            ambiguous = {a["site"] for a in evaluation.get("ambiguous_sites", [])}
            assert {a["site"] for a in r["ambiguous"]} == ambiguous and all(a["included"] for a in r["ambiguous"])
            assert all(not e["gt_label"] or e["gt_label"].startswith("non-live") for e in r["excluded_non_live"])
        else:
            assert r["live_sites"] == r["summary_counts"]["total_queries"] and r["label"] == "pipeline counts, not verified"
            assert not r["excluded_non_live"]


def test_manual_review_values_and_wording(data):
    review = data["manual_review"]
    values = {app: (r["not_converted_live"], r["live_sites"]) for app, r in review["apps"].items()}
    assert values["hms_kishan0725"][0] == 0 and values["dignity_dev_hms"] == (6, 13)  # 9 of 13 before the FR6 join rule
    assert {e["query_id"] for e in review["apps"]["dignity_dev_hms"]["excluded_non_live"]} == {"Q010"}
    assert "not a measurement of developer effort" in review["limits"] and "counted once" in review["formula"]


def test_markdown_and_dashboard_share_the_manual_review_numbers(data):
    summary = (ROOT / "docs" / "C3_results_summary.md").read_text(encoding="utf-8")
    section = summary.split("## Manual-review rate (current code)", 1)[1].split("\n## ", 1)[0]
    for app, r in data["manual_review"]["apps"].items():
        row = next(line for line in section.splitlines() if line.startswith(f"| {r['app']} | "))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[1:6] == [str(r["live_sites"]), str(r["unresolved_recovery_live"]), str(r["unresolved_conversion_live"]),
                              str(r["not_converted_live"]), f"{r['value']}%"], app


# Execution time --------------------------------------------------------------------------------------

def test_execution_time_comes_from_the_evidence_file(data):
    import statistics
    path = summarize.PERFORMANCE_FILE
    evidence = json.loads((ROOT / path).read_text(encoding="utf-8"))
    timings = [m for m in data["measurements"] if m["metric"] == "execution_time"]
    assert {m["app"] for m in timings} == set(data["apps"]) == set(evidence["apps"])
    for m in timings:
        runs = evidence["apps"][m["app"]]["runs_seconds"]
        assert m["status"] == "measured" and m["unit"] == "seconds" and m["source"]["file"] == path
        assert m["source"]["fields"] == {"value": f"apps.{m['app']}.median_seconds"}
        assert m["value"] == evidence["apps"][m["app"]]["median_seconds"] == round(statistics.median(runs), 3)
        assert len(runs) == evidence["rounds"] == 3
    performance = data["experiments"]["performance"]
    assert performance["machine"] == evidence["machine"] and performance["git_head"] == evidence["git_head"]
    assert "includes Python start-up; no database" in performance["label"]


def test_no_overall_accuracy_score(data):
    assert not [m for m in data["measurements"] if re.search(r"overall|combined", m["metric"] + m["label"], re.I)]


# 6. Historical accuracy is never assigned to uploaded applications -----------------------------

def test_try_it_never_reads_evidence_data():
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    block = re.search(r"/\* ===== TRY IT \(no evidence data\) ===== \*/(.*?)/\* ===== END TRY IT ===== \*/", code, re.S).group(1)
    for forbidden in ("DATA", "measurement", "Evidence", "dashboard_data", "records", "experiments"):
        assert forbidden not in block, forbidden
    # it may only talk to the local bridge and the recorded sample file
    assert set(re.findall(r'"((?:/|data/)[^"]*)"', block)) == {"/api/", "data/sample_run.json"}


def test_try_it_panel_starts_empty_and_disabled():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    panel = re.search(r'<section id="panel-tryit".*?</section>', html, re.S).group(0)
    assert re.search(r'<button[^>]*id="run-migration"[^>]*\bdisabled\b[^>]*>Local migration connection pending</button>', panel)
    assert not NUMBER.findall(re.sub(r"<[^>]+>", " ", panel))
    assert panel.count('class="kpi-value">-<') == 4
    assert "Counts from this upload, no ground truth, not an accuracy figure" in panel


# Functions view (FR1-FR10) ------------------------------------------------------------------------

FR_STATUS = ROOT / "docs" / "c3_fr_status.json"
AUDIT = ROOT / "docs" / "C3_audit_2026-10-17.md"


def _fr_spec():
    return json.loads(FR_STATUS.read_text(encoding="utf-8"))


def test_fr_status_file_holds_no_result_numbers():
    for item in _fr_spec()["functions"]:
        for field in ("title", "requirement", "limitation"):
            assert not re.search(r"\d", summarize.ID_TOKEN.sub("", item[field])), (item["fr"], field)
        assert set(item) <= {"fr", "title", "requirement", "status", "limitation", "measurements", "experiments", "site_list"}


def test_fr_requirements_are_verbatim_and_statuses_match_the_audit():
    audit = AUDIT.read_text(encoding="utf-8")
    rows = {cells[1].strip(): cells for cells in (line.split("|") for line in audit.splitlines() if re.match(r"^\| FR\d+ \|", line))}
    spec = _fr_spec()
    assert [i["fr"] for i in spec["functions"]] == [f"FR{n}" for n in range(1, 11)]
    for item in spec["functions"]:
        cells = rows[item["fr"]]
        assert cells[2].strip() == f'"{item["requirement"]}"', item["fr"]
        assert cells[7].strip().startswith(f"**{item['status']}**"), (item["fr"], cells[7].strip())


def test_functions_are_copied_and_every_reference_exists(data):
    spec = _fr_spec()
    assert data["functions"]["items"] == spec["functions"]
    assert data["functions"]["source"] == "docs/c3_fr_status.json"
    ids = {m["id"] for m in data["measurements"]}
    for item in spec["functions"]:
        assert item["measurements"], item["fr"]
        assert set(item["measurements"]) <= ids, item["fr"]
        assert set(item["experiments"]) <= set(data["experiments"]), item["fr"]
        if item.get("site_list"):
            assert any(l["fr"] == item["site_list"] for l in data["limitations"])


def test_status_summary_counts_come_from_the_file(data):
    from collections import Counter
    counts = Counter(i["status"] for i in data["functions"]["items"])
    assert counts == Counter({"Implemented and measured": 7, "Partial": 3})
    assert {i["fr"] for i in data["functions"]["items"] if i["status"] == "Partial"} == {"FR2", "FR4", "FR6"}
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert "fn.statuses.filter((s) => counts[s])" in code  # the summary line is counted, not typed


def test_function_cards_take_numbers_only_from_referenced_measurements():
    code = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    card = re.search(r"function frCard\(item\) \{(.*?)\n  \}\n", code, re.S).group(1)
    assert "item.measurements.map(byId)" in card
    # every value on a card is formatted from a referenced measurement (or counted from the limitation's site list)
    assert re.findall(r"formatValue\((\w+)\)", card) and set(re.findall(r"formatValue\((\w+)\)", card)) == {"m"}
    assert "DATA.measurements" not in card and "DATA.experiments" not in card


# Privacy ----------------------------------------------------------------------------------------

def test_export_contains_no_sql_values_credentials_or_paths(data):
    summarize._privacy_check(data)
    # Raw run and E1/E4 fields must not be exported as keys. (A generated function may have a parameter named
    # "inputs"; that is a value, not an exported field. Before / After SQL is checked in the before_after tests.)
    keys = set()
    def walk(node):
        if isinstance(node, dict):
            keys.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    for key in ("recovered_sql", "parameterized_sql", "sql_argument", "payloads", "inputs", "database", "cases"):
        assert key not in keys, key
    assert all(r["unresolved_reason"] is None for r in data["records"]["hms_kishan0725"])


def test_dashboard_option_is_opt_in():
    with pytest.raises(SystemExit):
        summarize.main(["--no-markdown"])
