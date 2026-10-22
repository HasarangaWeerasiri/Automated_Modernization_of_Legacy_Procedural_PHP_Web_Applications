"""PP1 views of the C3 Migration Studio: FR detail, SQL support matrix, failure explorer, applications register and
measurement definitions.

Text comes from the reviewed file docs/c3_pp1_dashboard_content.json (no research numbers). Every number added here is
computed from the fresh current-code run or from committed evidence and is stored as {"value", "source", "derivation"}
(tests/test_dashboard_data.py checks that no number in the "pp1" block lacks a source). Restricted applications are
counts and query IDs only. Called by scripts/summarize_evidence.py write_dashboard_json.
"""

import json
import re
import subprocess
from collections import Counter, defaultdict

from sqlglot import exp, parse_one

CONTENT = "docs/c3_pp1_dashboard_content.json"
MANIFEST = "benchmarks/benchmark_manifest.json"
STAR_SITES = "evidence/final/2026-10-20/result_shape_star_join_sites.json"
AMS = {"baseline": "evidence/baseline_unseen/ams_native/2026-10-20/baseline.json",
       "correction": "evidence/baseline_unseen/ams_native/2026-10-20/CORRECTION_2026-10-20.md",
       "post_t1": "evidence/final/2026-10-21/ams_native_post_t1.json",
       "post_t2": "evidence/final/2026-10-20/ams_native_post_t2.json"}
RESTRICTED = {"hms_kishan0725", "dignity_dev_hms", "fee_rainbow", "ams_native"}
# Licence provenance records (each must conclude MIT, match the recorded commit and both reference texts).
PROVENANCE = {"fee_rainbow": "evidence/provenance/fee_rainbow_licence.json"}
# Reason codes the source can emit, by stage (each is checked to occur in the source).
RECOVERY_CODES = ["STRUCTURAL", "BRANCHING", "LOOP_OR_SWITCH_CONTROL_FLOW", "UNKNOWN_EXPR", "UNSUPPORTED_SHAPE",
                  "UNSUPPORTED_EXECUTOR", "MISSING_SQL_ARGUMENT", "MULTI_STATEMENT_EXECUTOR"]
CONVERSION_CODES = ["CONVERSION_UNSUPPORTED"]
CODE_FILES = ["components/c3_data_layer/src/c3/mapping/sql_recovery.py", "components/c3_data_layer/src/c3/analysis/unresolved_report.py"]
PH = re.compile(r"\{\{(\w+)\}\}")


def _v(value, source, derivation=None):
    return {"value": value, "source": source, **({"derivation": derivation} if derivation else {})}


def reason_key(code, reason):
    """Classify one unresolved entry by C3's own reason message (never by application text)."""
    r = reason or ""
    if code == "STRUCTURAL":
        if r.startswith("Dynamic table identifier"): return "STRUCTURAL:dynamic_table"
        if "ORDER BY" in r: return "STRUCTURAL:dynamic_order_by"
        if "IN-list fragment" in r: return "STRUCTURAL:in_list_fragment"
        if "supplies SQL text" in r: return "STRUCTURAL:sql_fragment"
        raise SystemExit(f"unclassified STRUCTURAL reason: {r[:60]!r}")
    if code == "UNKNOWN_EXPR":
        return "UNKNOWN_EXPR:no_assignment_chain" if r.startswith("No recoverable assignment chain") else "UNKNOWN_EXPR:unsupported_expression"
    if code == "CONVERSION_UNSUPPORTED":
        if r.startswith("Unsupported multi-table SQL structure. A LEFT JOIN or comma join is converted only when every table"):
            return "CONVERSION_UNSUPPORTED:join_schema"
        if r.startswith("Unsupported multi-table SQL structure. A LEFT JOIN"): return "CONVERSION_UNSUPPORTED:join_other"
        if r.startswith("Unsupported multi-table SQL structure"): return "CONVERSION_UNSUPPORTED:multi_table"
        if r.startswith("Dynamic placeholder occurs"): return "CONVERSION_UNSUPPORTED:placeholder_position"
        return "CONVERSION_UNSUPPORTED:other"
    return code


def constructs(sql):
    """Constructs in one recovered statement (SQLGlot); used for occurrence counts only."""
    c = set()
    try:
        t = parse_one(PH.sub(lambda m: "__p_" + m.group(1), sql).rstrip().rstrip(";"), dialect="mysql")
    except Exception:
        return {"parse_failed"}
    c.add("stmt_" + type(t).__name__.lower())
    joins = list(t.find_all(exp.Join))
    kinds = set()
    for j in joins:
        a = j.args
        if "pivots" not in a and not any(a.get(k) for k in ("kind", "side", "method", "on", "using")): k = "join_comma"
        elif a.get("method"): k = "join_" + str(a["method"]).lower()
        elif a.get("using"): k = "join_using"
        elif (a.get("side") or "").upper() in ("LEFT", "RIGHT", "FULL"): k = "join_" + a["side"].lower()
        elif (a.get("kind") or "").upper() == "CROSS": k = "join_cross"
        elif (a.get("kind") or "").upper() == "STRAIGHT_JOIN": k = "join_straight"
        else: k = "join_inner" if a.get("on") is not None else "join_bare_no_on"
        kinds.add(k)
    c |= kinds
    if len(kinds) > 1: c.add("mixed_join_kinds")
    if joins:
        names = [x.name for x in t.find_all(exp.Table)]
        if len(names) != len(set(names)): c.add("self_join")
        if any(isinstance(j.this, exp.Subquery) for j in joins): c.add("derived_table_join")
    if isinstance(t, (exp.Update, exp.Delete)) and (joins or len({x.name for x in t.find_all(exp.Table)}) > 1):
        c.add("multi_table_" + type(t).__name__.lower())
    for cls, key in [(exp.Subquery, "subquery"), (exp.Union, "union"), (exp.With, "cte"), (exp.Window, "window"),
                     (exp.Group, "group_by"), (exp.Having, "having"), (exp.Distinct, "distinct"), (exp.Order, "order_by"),
                     (exp.Limit, "limit"), (exp.Offset, "offset"), (exp.Where, "where"), (exp.And, "and"), (exp.Or, "or"),
                     (exp.In, "in"), (exp.Between, "between"), (exp.Like, "like")]:
        if any(True for _ in t.find_all(cls)): c.add(key)
    for f in t.find_all(exp.AggFunc, exp.Anonymous, exp.MD5, exp.Cast, exp.CurrentTimestamp, exp.Rand):
        c.add("fn:" + (f.name.upper() if isinstance(f, exp.Anonymous) else type(f).__name__.upper()))
    return c


def _check_code_refs(se, where, code_refs, tests):
    for ref in code_refs:
        path = se.ROOT / ref["file"]
        if not path.is_file():
            raise SystemExit(f"{where}: file not found {ref['file']}")
        text = path.read_text(encoding="utf-8")
        for name in ref["functions"]:
            if not re.search(rf"\b(def|const|class)\s+{re.escape(name)}\b|^{re.escape(name)}\s*=|'{re.escape(name)}'|\b{re.escape(name)}\s*=\s*\[", text, re.M):
                raise SystemExit(f"{where}: {name} not defined in {ref['file']}")
    for t in tests:
        if not (se.ROOT / t).is_file():
            raise SystemExit(f"{where}: test file not found {t}")


def _module_function(se, dotted):
    """'query_converter.convert_query' -> the source file that defines it (checked)."""
    module, name = dotted.rsplit(".", 1) if "." in dotted and not dotted.endswith(".php") else (dotted, None)
    if dotted.endswith(".php"):
        path = se.COMPONENT / "tools" / "php_ast" / dotted
        if not path.is_file(): raise SystemExit(f"sql_matrix: {dotted} not found")
        return path.relative_to(se.ROOT).as_posix()
    hits = [p for p in (se.COMPONENT / "src" / "c3").rglob(f"{module}.py")]
    if len(hits) != 1: raise SystemExit(f"sql_matrix: module {module} not found exactly once")
    if not re.search(rf"^(def|class)\s+{re.escape(name)}\b|^{re.escape(name)}\s*=", hits[0].read_text(encoding="utf-8"), re.M):
        raise SystemExit(f"sql_matrix: {name} not defined in {module}")
    return f"{hits[0].relative_to(se.ROOT).as_posix()}::{name}"


def build(se, base, data):
    content = json.loads((se.ROOT / CONTENT).read_text(encoding="utf-8"))
    manifest = {a["name"]: a for a in json.loads((se.ROOT / MANIFEST).read_text(encoding="utf-8"))["applications"]}
    ids = {m["id"] for m in data["measurements"]}
    runs = [(folder, php) for _, folder, php, _, _ in se.CURRENT_RUNS]
    temp = se.TEMP

    def run_json(folder, name):
        return json.loads((base / folder / name).read_text(encoding="utf-8"))

    # ---------- functions ----------
    functions = {}
    for item in data["functions"]["items"]:
        fr = item["fr"]
        c = content["functions"][fr]
        _check_code_refs(se, f"{CONTENT} {fr}", c["code"], c["tests"])
        for imp in c["improvements"]:
            if subprocess.run(["git", "cat-file", "-e", imp["commit"] + "^{commit}"], cwd=se.ROOT, capture_output=True).returncode:
                raise SystemExit(f"{CONTENT} {fr}: unknown commit {imp['commit']}")
        headline = c.get("headline", [])
        if not 1 <= len(headline) <= 3 or not set(headline) <= set(item["measurements"]):
            raise SystemExit(f"{CONTENT} {fr}: headline must be one to three of the FR's own measurement ids")
        if not c.get("main_limit") or len(c["main_limit"].split()) > 25:
            raise SystemExit(f"{CONTENT} {fr}: main_limit is required and at most twenty-five words")
        if item["status"] == "Partial" and not c.get("partial_reason"):
            raise SystemExit(f"{CONTENT} {fr}: Partial status needs partial_reason")
        functions[fr] = c

    # ---------- SQL support matrix (occurrence counts over the nine apps) ----------
    occ = defaultdict(Counter)
    for folder, _ in runs:
        for q in run_json(folder, "queries.json"):
            if q.get("recovered_sql"):
                for key in constructs(q["recovered_sql"]):
                    occ[key][folder] += 1
    matrix = []
    for row in content["sql_matrix"]:
        if row["status"] not in content["statuses"]:
            raise SystemExit(f"sql_matrix {row['id']}: unknown status")
        sources = [_module_function(se, s) for s in row["source"]]
        for t in row["tests"]:
            if not list((se.COMPONENT / "tests").rglob(t)):
                raise SystemExit(f"sql_matrix {row['id']}: test {t} not found")
        per_app = Counter()
        for key in row["constructs"]:
            per_app.update(occ.get(key, Counter()))
        matrix.append({**row, "source_files": sources,
                       "occurrences": _v(sum(per_app.values()), f"{temp}/<app>/queries.json recovered_sql (nine applications)",
                                         "statements whose SQLGlot parse contains the construct; ams-native is not included "
                                         "(its inputs are not in the repository)") if row["constructs"] else None,
                       "occurrences_by_app": {a: _v(n, f"{temp}/{a}/queries.json") for a, n in sorted(per_app.items())}})

    # ---------- result-shape warning for converted star INNER JOIN sites ----------
    star = json.loads((se.ROOT / STAR_SITES).read_text(encoding="utf-8"))
    found = set()
    for folder, _ in runs:
        for cq in run_json(folder, "converted_queries.json"):
            if cq["status"] != "CONVERTED" or not cq.get("parameterized_sql"):
                continue
            try:
                t = parse_one(cq["parameterized_sql"].rstrip().rstrip(";"), dialect="mysql")
            except Exception:
                continue
            if isinstance(t, exp.Select) and t.args.get("joins") and any(
                    isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)) for p in t.expressions):
                found.add((folder, cq["query_id"]))
    recorded = {(s["app"], s["query_id"]) for s in star["sites"] if s["app"] != "ams_native"}
    if found != recorded:
        raise SystemExit(f"{STAR_SITES}: star join sites in the current run {sorted(found)} differ from the record {sorted(recorded)}")
    star_sites = [{"app": s["app"], "query_id": s["query_id"], "projection": s["projection"],
                   "join_tables": _v(s["join_tables"], STAR_SITES), "tables_missing_from_parsed_schema": _v(s["tables_missing_from_parsed_schema"], STAR_SITES),
                   "expanded_output_columns": _v(s["expanded_output_columns"], STAR_SITES),
                   "duplicate_output_names": _v(s["duplicate_output_names"], STAR_SITES),
                   "finding": ("Duplicate projected column names exist; result.mappings() keys for them may need additional checking."
                               if s["duplicate_output_names"] else
                               "Column list cannot be determined: the joined tables are not in the parsed schema (the supplied schema file "
                               "defines one table). The projection is a single table.*, so duplicate names cannot arise within it."
                               if s["duplicate_output_names"] is None else "No duplicate projected column names.")}
                  for s in star["sites"]]

    # ---------- failure and limitation explorer ----------
    defined = {}
    for code in RECOVERY_CODES + CONVERSION_CODES:
        if not any(f'"{code}"' in (se.ROOT / f).read_text(encoding="utf-8") for f in CODE_FILES):
            raise SystemExit(f"reason code {code} not found in the source")
        defined[code] = "recovery" if code in RECOVERY_CODES else "conversion"
    groups = defaultdict(lambda: {"ids": defaultdict(list)})
    template_overrides = defaultdict(list)
    for folder, _ in runs:
        conv = {c["query_id"]: c for c in run_json(folder, "converted_queries.json")}
        for u in run_json(folder, "unresolved_queries.json"):
            key = reason_key(u["reason_code"], u.get("reason"))
            g = groups[(u["stage"], u["reason_code"], key)]
            g["ids"][folder].append(u["query_id"])
        for q in run_json(folder, "queries.json"):
            if q["status"] != "RECOVERED" and conv.get(q["query_id"], {}).get("status") == "CONVERTED":
                template_overrides[(q.get("reason_code"), reason_key(q.get("reason_code"), q.get("reason")))].append((folder, q["query_id"]))
    ams_post = json.loads((se.ROOT / AMS["post_t1"]).read_text(encoding="utf-8"))
    ams_t2 = json.loads((se.ROOT / AMS["post_t2"]).read_text(encoding="utf-8"))
    if not ams_t2["output_files_identical_after_path_normalisation"]:
        raise SystemExit("ams-native post-T2 outputs differ from post-T1; the post-T1 unresolved list is not current")
    for u in ams_post["post"]["unresolved"]:
        groups[(u["stage"], u["reason_code"], reason_key(u["reason_code"], u.get("reason")))]["ids"]["ams_native"].append(u["query_id"])
    reasons = []
    for (stage, code, key), g in sorted(groups.items()):
        text = content["reasons"].get(key)
        if text is None:
            raise SystemExit(f"{CONTENT}: no explanation for reason {key}")
        nine = {a: v for a, v in g["ids"].items() if a != "ams_native"}
        reasons.append({"stage": stage, "code": code, "key": key, **text, "code_generated": "No: an unresolved-site stub that raises is emitted instead of SQL.",
                        "count": _v(sum(len(v) for v in nine.values()), f"{temp}/<app>/unresolved_queries.json (nine applications)"),
                        "by_app": {a: {"count": _v(len(v), f"{temp}/{a}/unresolved_queries.json"), "query_ids": sorted(v)} for a, v in sorted(nine.items())},
                        "ams_native": ({"count": _v(len(g["ids"]["ams_native"]), AMS["post_t1"], "post.unresolved; equal to the current outputs "
                                                    f"({AMS['post_t2']} output_files_identical_after_path_normalisation)"),
                                        "query_ids": sorted(g["ids"]["ams_native"])} if g["ids"].get("ams_native") else None)})
    used = sorted({r["code"] for r in reasons})
    catalogue = {"defined": [{"code": c, "stage": s, "in_use": c in used} for c, s in defined.items()],
                 "defined_recovery": _v(len(RECOVERY_CODES), ", ".join(CODE_FILES), "distinct reason_code strings the recovery stage can emit"),
                 "defined_conversion": _v(len(CONVERSION_CODES), CODE_FILES[1]),
                 "in_use_recovery": _v(sum(1 for c in used if c in RECOVERY_CODES), "unresolved entries of the nine apps and ams-native"),
                 "in_use_conversion": _v(sum(1 for c in used if c in CONVERSION_CODES), "unresolved entries of the nine apps and ams-native")}
    overrides = [{"code": code, "key": key, "query_ids": {a: sorted(q for f, q in v if f == a) for a in sorted({f for f, _ in v})},
                  "count": _v(len(v), f"{temp}/<app>/queries.json status != RECOVERED and converted_queries.json CONVERTED")}
                 for (code, key), v in sorted(template_overrides.items())]

    outcomes = defaultdict(dict)
    for folder, rows in data["records"].items():
        cats = {"recoverable_unsupported": [r["query_id"] for r in rows if r["gt_label"] == "recoverable" and r["conversion_status"] != "CONVERTED"],
                "non_recoverable": [r["query_id"] for r in rows if r["gt_label"] == "non-recoverable"],
                "ambiguous": [r["query_id"] for r in rows if r["gt_label"] == "ambiguous"],
                "non_live": [r["query_id"] for r in rows if (r["gt_label"] or "").startswith("non-live")]}
        for k, v in cats.items():
            outcomes[k][folder] = {"count": _v(len(v), f"records.{folder} (gt_label, conversion_status)"), "query_ids": v}
    for s in star_sites:
        outcomes["unverified"].setdefault(s["app"], {"query_ids": []})["query_ids"].append(s["query_id"])
    for a, o in outcomes["unverified"].items():
        o["count"] = _v(len(o["query_ids"]), STAR_SITES)
    final_e1 = next(r for r in data["experiments"]["e1"]["runs"] if r["kind"] == "final")
    outcomes["verification_inconclusive"][final_e1["app"]] = {"count": _v(final_e1["verdicts"]["INCONCLUSIVE"], final_e1["file"]), "query_ids": []}
    outcomes["failed_verification"][final_e1["app"]] = {"count": _v(final_e1["verdicts"]["FAIL"], final_e1["file"]), "query_ids": []}
    for folder, _ in runs:
        latest = se.APP_INFO[folder][2]
        if latest:
            ev = se.load(latest)
            n = len(ev.get("schema_mismatch", {}).get("sites", []))
            if n:
                outcomes["schema_mismatch"][folder] = {"count": _v(n, f"evidence/evaluation/{latest}", "schema_mismatch.sites"), "query_ids": []}
    outcome_list = [{"key": k, **content["outcome_categories"][k], "apps": dict(outcomes.get(k, {}))} for k in content["outcome_categories"]]

    # ---------- applications register ----------
    def evidence_applied(folder):
        e = []
        if se.APP_INFO[folder][2]: e.append("S15")
        if any(r["app"] == folder for r in data["experiments"]["e1"]["runs"]): e.append("E1")
        if folder in data["experiments"]["e2"]: e.append("E2")
        if folder in data["experiments"]["e3"]: e.append("E3")
        if folder in data["experiments"]["e4"]["apps"]: e.append("E4")
        e.append("byte gate")
        if any(s["app"] == folder for s in data["experiments"]["e1"]["separate_suites"]): e.append("Clinic MySQL verification (incl. join-rule tests)")
        return e
    register = []
    for folder, php in runs:
        m = manifest.get(folder, {})
        url = m.get("url")
        label, role, latest, licence = se.APP_INFO[folder]
        gt_file = se.ROOT / "benchmarks" / "ground truth" / folder / "queries.json"
        fix_file = se.ROOT / "benchmarks" / "ground_truth" / folder / "queries.json"
        manifest_licence = m.get("licence")
        inconsistent = bool(manifest_licence and "not verified" in licence and manifest_licence.upper().startswith("MIT"))
        evidence = None
        if folder in PROVENANCE:
            rec = json.loads((se.ROOT / PROVENANCE[folder]).read_text(encoding="utf-8"))
            ok = (rec["conclusion"] == "MIT" and rec["clone"]["head_matches_recorded_commit"] and rec["recorded_commit"] == m.get("commit")
                  and rec["comparison"]["body_identical_to_reference_1"] and rec["comparison"]["body_identical_to_reference_2"]
                  and rec["licence_file"]["git_blob_at_recorded_commit"] == rec["licence_file"]["git_blob_of_working_file"])
            if not ok or PROVENANCE[folder] not in licence:
                raise SystemExit(f"{PROVENANCE[folder]}: licence not verified, or APP_INFO does not cite it")
            evidence = {"file": PROVENANCE[folder], "conclusion": rec["conclusion"], "date": rec["date"],
                        "licence_sha256": rec["licence_file"]["sha256"], "method": rec["comparison"]["method"]}
        register.append({
            "app": folder, "label": label, "role": role, **content["apps"][folder],
            "repository": url.split("://", 1)[1] if url else None, "commit": m.get("commit"), "commit_date": m.get("commit_date"),
            "origin": m.get("origin"), "licence_manifest": manifest_licence, "licence_generator": licence,
            "licence_status": content["fee_rainbow_licence"]["message"] if inconsistent else None,
            "licence_evidence": evidence,
            "provenance_files": [MANIFEST, "scripts/summarize_evidence.py APP_INFO"] + ([PROVENANCE[folder]] if folder in PROVENANCE else []),
            "ground_truth": ("author-verified ground truth" if gt_file.is_file() else "fixture expectations" if fix_file.is_file() else "none"),
            "ground_truth_entries": _v(len(json.loads(gt_file.read_text(encoding="utf-8"))), gt_file.relative_to(se.ROOT).as_posix()) if gt_file.is_file() else None,
            "measurements": {k: f"{folder}|current code|{k}" for k in ("sql_detected", "sql_recovered", "converted_functions", "unresolved_total")},
            "evidence_applied": evidence_applied(folder),
            "display": "Code-level Before/After allowed" if folder in se.BEFORE_AFTER_DETAIL else "Counts and query IDs only"})
    for r in register:
        missing = [i for i in r["measurements"].values() if i not in ids]
        if missing: raise SystemExit(f"register {r['app']}: unknown measurement ids {missing}")
    base_line = json.loads((se.ROOT / AMS["baseline"]).read_text(encoding="utf-8"))
    ams = {"app": "ams_native", "label": "ams-native-3c4d475", **content["apps"]["ams_native"], "display": "Counts and query IDs only",
           "licence_history": [{"when": "first run (baseline, commit 351be5b)", "text": "Original ZIP hash not recorded; licence unknown.", "source": AMS["baseline"]},
                               {"when": "later on 2026-10-20", "text": "Original ZIP found; PHP and schema hashes match the baseline; LICENSE.txt is MIT. Source is still not exported.", "source": AMS["correction"]}],
           "runs": [{"label": "pre-improvement baseline (351be5b)", **{k: _v(base_line["counts"][k], AMS["baseline"], f"counts.{k}") for k in ("total_queries", "recovered_queries", "converted_queries", "unresolved_total")}},
                    {"label": "after the value-position extension (d263cbb)", **{k: _v(ams_post["post"]["counts"][k], AMS["post_t1"], f"post.counts.{k}") for k in ("total_queries", "recovered_queries", "converted_queries", "unresolved_total")}},
                    {"label": "after the FR6 join rule (4d62a90)", **{k: _v(ams_t2["runs"]["4d62a90"][k], AMS["post_t2"], f"runs.4d62a90.{k}") for k in ("total_queries", "converted_queries", "unresolved_total")}}],
           "evidence_applied": ["E4-style check", "SQLite Alembic round trip", "output comparison"], "ground_truth": "none"}

    # ---------- measurement definitions and E4 split ----------
    latest_ids = {}
    for m in data["measurements"]:
        if m["status"] != "not_measured":
            latest_ids.setdefault(m["metric"], [])
    defs = []
    for d in content["measurement_definitions"]:
        bad = [r for r in d["refs"] if r not in ids]
        if bad: raise SystemExit(f"{CONTENT} measurement_definitions: unknown ids {bad}")
        metric_ids = [m["id"] for m in data["measurements"] if m["metric"] in d.get("metrics", [])
                      and (m["category"] != "tests" or any(r["latest"] and r["stage_label"] == m["stage_label"] for r in data["experiments"]["tests"]["runs"]))]
        defs.append({**d, "refs": d["refs"] + metric_ids})
    report = json.loads((se.ROOT / se.E4_REPORT).read_text(encoding="utf-8"))
    e4 = {}
    for key, app in report["apps"].items():
        folder = next((f for f, k in se.EXPERIMENT_KEY.items() if k == key), key)
        with_values = sum(1 for f in app["detail"].values() if f["bound"] + f["rejected"])
        e4[folder] = {"with_runtime_values": _v(with_values, se.E4_REPORT, f"apps.{key}.detail[*]: bound + rejected > 0"),
                      "without_runtime_values": _v(app["functions"] - with_values, se.E4_REPORT, f"apps.{key}.detail[*]: no payload case (no bound value)"),
                      "functions": _v(app["functions"], se.E4_REPORT, f"apps.{key}.functions")}

    return {"content_source": CONTENT, "methodology": content["methodology"], "statuses": content["statuses"],
            "functions": functions, "sql_matrix": matrix, "star_join_sites": {"source": STAR_SITES, "warning": star["warning_text"], "sites": star_sites},
            "reasons": reasons, "reason_catalogue": catalogue, "template_overrides": overrides, "outcomes": outcome_list,
            "register": register, "ams_native": ams, "fee_rainbow_licence": content["fee_rainbow_licence"],
            "measurement_definitions": defs, "e4_split": e4,
            "restricted_apps": sorted(RESTRICTED)}
