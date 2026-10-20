"""Generate docs/C3_results_summary.md.

Two sources, nothing else:
  1. Historical stages: the S15 evaluation JSON files in evidence/evaluation/<app>/.
  2. Current code: a fresh C3 run on all 9 apps, written to a NEW temp folder
     outside the repo (never output/), read from each app's summary.json,
     unresolved_queries.json and converted_queries.json.

Every number in the output is copied from, or counted from, a JSON field; the
sourcing table names the file and field of each number. A value no file
records is written as "not measured" and listed as missing. Every stage row is
checked to sum to its live sites; the script fails loudly otherwise.

Usage (repo root):
    python scripts/summarize_evidence.py
    python scripts/summarize_evidence.py --dashboard-json components/c3_data_layer/dashboard/data/dashboard_data.json [--no-markdown]

--dashboard-json also writes the data file of the C3 Migration Studio dashboard
from the same sources (opt-in; without it the behaviour is unchanged).
--no-markdown skips docs/C3_results_summary.md.
"""

import argparse
import ast
import datetime
import re
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evidence" / "evaluation"
OUT = ROOT / "docs" / "C3_results_summary.md"
COMPONENT = ROOT / "components" / "c3_data_layer"
PYTHON = COMPONENT / ".venv" / "Scripts" / "python.exe"
APPS = ROOT / "benchmarks" / "apps"
NOT_MEASURED = "not measured"

# (app label, file, stage label). Order = stage order. Only files that exist are used.
STAGES = [
    ("HMS (kishan0725)", "hms_kishan0725/evaluation_preadaptation.json", "pre-adaptation"),
    ("HMS (kishan0725)", "hms_kishan0725/evaluation_pre_H1.json", "post-adaptation (general fixes, before H1)"),
    ("HMS (kishan0725)", "hms_kishan0725/evaluation.json", "post-adaptation (after H1)"),
    ("Dignity-Dev", "dignity_dev_hms/evaluation_first_holdout_run.json", "fresh holdout (first run)"),
    ("Dignity-Dev", "dignity_dev_hms/evaluation.json", "fresh holdout (current rules)"),
    ("Helpdesk", "helpdesk_legacy_holdout/evaluation_baseline_6of9.json", "pre-adaptation (frozen baseline)"),
    ("Helpdesk", "helpdesk_legacy_holdout/evaluation.json", "post-adaptation"),
    ("NEWS-BUZZ", "news_buzz/evaluation_preadaptation.json", "pre-adaptation"),
    ("NEWS-BUZZ", "news_buzz/evaluation.json", "post-adaptation"),
    ("NEWS-BUZZ", "news_buzz/evaluation_post_escape_fix.json", "post-fix (escape decoder)"),
    ("Fee-Rainbow", "fee_rainbow/evaluation_preadaptation.json", "pre-adaptation"),
    ("Fee-Rainbow", "fee_rainbow/evaluation.json", "post-adaptation"),
    ("Fee-Rainbow", "fee_rainbow/evaluation_wrapper_support.json", "post-fix (wrapper support + fragment rule)"),
    # Clinic and the three older fixtures: included automatically if an evaluation file appears.
    ("Clinic", "clinic/evaluation.json", "evaluation"),
    ("simple_crud", "simple_crud/evaluation.json", "evaluation"),
    ("mixed_crud", "mixed_crud/evaluation.json", "evaluation"),
    ("hard_crud", "hard_crud/evaluation.json", "evaluation"),
]

COLUMNS = ["live sites", "recovered correct", "missed", "wrong", "correct abstentions",
           "not detected (TN, not counted)", "ambiguous (excluded)", "converted", "precision", "recall"]
SUMMED = ["recovered correct", "missed", "wrong", "correct abstentions",
          "not detected (TN, not counted)", "ambiguous (excluded)"]

# Same inputs as scripts/regenerate_outputs.ps1 and the earlier gate runs:
# app label, run folder, PHP root, schema, author-verified ground truth exists.
CURRENT_RUNS = [
    ("HMS (kishan0725)", "hms_kishan0725", "_app_only/hms_kishan0725", "hms_kishan0725/myhmsdb.sql", True),
    ("Dignity-Dev", "dignity_dev_hms", "_app_only/dignity_dev_hms", "dignity_dev_hms/127_0_0_1.sql", True),
    ("Helpdesk", "helpdesk_legacy_holdout", "helpdesk_legacy_holdout/legacy",
     "helpdesk_legacy_holdout/database/schema.sql", True),
    ("NEWS-BUZZ", "news_buzz", "_app_only/news_buzz", "news_buzz/db/NEWSBUZZ.sql", True),
    ("Fee-Rainbow", "fee_rainbow", "_app_only/fee_rainbow", "fee_rainbow/database/db.sql", True),
    ("Clinic", "clinic_inventory_legacy_hard", "clinic_inventory_legacy_hard/legacy",
     "clinic_inventory_legacy_hard/database/schema.sql", False),
    ("simple_crud", "simple_crud", "simple_crud/legacy", "simple_crud/schema.sql", False),
    ("mixed_crud", "mixed_crud", "mixed_crud/legacy", "mixed_crud/schema.sql", False),
    ("hard_crud", "hard_crud", "hard_crud/legacy", "hard_crud/schema.sql", False),
]
NO_GT = "no author-verified ground truth: these are pipeline counts, not accuracy"
TEMP = "<temp>"  # the run folder's absolute path is never printed
PIPELINE_PATHS = ["components/c3_data_layer/src", "components/c3_data_layer/tools"]

# Text-only disclosures checked to exist in the summary they point to.
FEE_RAINBOW_TEXT = ROOT / "evidence" / "evaluation" / "fee_rainbow" / "summary.md"
FEE_RAINBOW_PHRASES = ["10 wrong recoveries, precision 80.0%", "the first correction reached 6 of 10"]


def load(relative):
    return json.loads((EVAL / relative).read_text(encoding="utf-8"))


def pct(value):
    return NOT_MEASURED if value is None else f"{value}%"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def run_current_code():
    """Run C3 on all 9 apps into a new temp folder outside the repo; fail on any error."""
    base = Path(tempfile.mkdtemp(prefix="c3_current_code_"))
    if ROOT in base.resolve().parents:
        raise SystemExit(f"temp folder {base} is inside the repo; refusing")
    env = dict(os.environ, PYTHONPATH=str(COMPONENT / "src"), PYTHONDONTWRITEBYTECODE="1")
    for app, folder, php, schema, _ in CURRENT_RUNS:
        target = base / folder
        completed = subprocess.run(
            [str(PYTHON), "-m", "c3.cli", "run", "--php", str(APPS / php), "--schema", str(APPS / schema),
             "--output", str(target)],
            cwd=COMPONENT, env=env, capture_output=True, text=True,
        )
        (base / f"{folder}_log.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise SystemExit(f"C3 failed for {app} (exit {completed.returncode}); log in {TEMP}/{folder}_log.txt "
                             "(<temp> = the newest c3_current_code_* folder in the system temp directory)")
        print(f"ran {app} -> {TEMP}/{folder}")
    return base


def historical_rows(sources, missing_converted):
    rows = []
    used = []
    for app, relative, stage in STAGES:
        if not (EVAL / relative).exists():
            continue
        data = load(relative)
        used.append(relative)
        recovery = data["recovery"]
        recovery_only = "recovery" in str(data.get("scope", "")).lower()
        stage_label = stage + (" - recovery only" if recovery_only else "")
        path = f"evidence/evaluation/{relative}"

        detected_tn = sum(1 for d in data["details"] if d.get("outcome") == "TN" and d.get("c3_status") != "NOT_DETECTED")
        undetected_tn = sum(1 for d in data["details"] if d.get("outcome") == "TN" and d.get("c3_status") == "NOT_DETECTED")
        if detected_tn + undetected_tn != recovery["TN"]:
            raise SystemExit(f"{path}: TN split {detected_tn}+{undetected_tn} != recovery.TN {recovery['TN']}")

        values = {
            "live sites": (data["gt_live_sites"], "gt_live_sites"),
            "recovered correct": (recovery["TP"], "recovery.TP"),
            "missed": (recovery["FN"], "recovery.FN"),
            "wrong": (recovery["FP"], "recovery.FP"),
            "correct abstentions": (detected_tn, 'count of details[] with outcome == "TN" and c3_status != "NOT_DETECTED"'),
            "not detected (TN, not counted)": (undetected_tn, 'count of details[] with outcome == "TN" and c3_status == "NOT_DETECTED"'),
            "ambiguous (excluded)": (recovery["excluded_ambiguous"], "recovery.excluded_ambiguous"),
            "converted": (None, None),
            "precision": (recovery["precision"], "recovery.precision"),
            "recall": (recovery["recall"], "recovery.recall"),
        }
        total = sum(values[c][0] for c in SUMMED)
        if total != values["live sites"][0]:
            raise SystemExit(f"{path}: row does not sum to live sites: "
                             + " + ".join(f"{c} {values[c][0]}" for c in SUMMED) + f" = {total} != {values['live sites'][0]}")

        cells = []
        for column in COLUMNS:
            value, field = values[column]
            if field is None:
                cells.append(NOT_MEASURED)
                missing_converted.append(f"{app} / {stage}")
                continue
            text = pct(value) if column in ("precision", "recall") else str(value)
            cells.append(text)
            sources.append((app, stage, column, text, path, field))
        rows.append((app, stage_label, cells))
    return rows, used


def pipeline_unchanged_since(head):
    """Fail unless the pipeline source and tools have no commits after `head` and no uncommitted changes."""
    if git("rev-parse", "HEAD") != head:
        raise SystemExit(f"HEAD moved during the run (recorded {head}); re-run")
    later = git("rev-list", f"{head}..HEAD", "--", *PIPELINE_PATHS)
    if later:
        raise SystemExit(f"commits to {PIPELINE_PATHS} after {head}:\n{later}")
    dirty = git("status", "--porcelain", "--", *PIPELINE_PATHS)
    if dirty:
        raise SystemExit("pipeline source or tools have uncommitted changes; the run would not match HEAD:\n" + dirty)


def site_label(entry, php_root):
    return f"{Path(entry['source_file']).resolve().relative_to(php_root).as_posix()}:{entry['source_line']}"


def footnotes(runs, sources):
    """Footnotes under the current-code table, computed from the run and the evaluation files."""
    notes = []

    def sites(folder):
        _, converted, php_root = runs[folder]
        return {site_label(c, php_root): c for c in converted}

    def conv_path(folder):
        return f"{TEMP}/{folder}/converted_queries.json"

    # Dignity-Dev: detected sites that are not ground-truth live sites.
    relative = "dignity_dev_hms/evaluation.json"
    evaluation = load(relative)
    gt_sites = {d["site"] for d in evaluation["details"]} | {a["site"] for a in evaluation["ambiguous_sites"]}
    detected = sites("dignity_dev_hms")
    non_live_field = "non_live_sites_reported" if "non_live_sites_reported" in evaluation["detection"] else "false_detections_commented_out"
    non_live = {n["site"]: n for n in evaluation["detection"][non_live_field]}
    extra = sorted(set(detected) - gt_sites)
    if len(detected) - len(extra) != evaluation["gt_live_sites"] or any(site not in non_live for site in extra):
        raise SystemExit(f"Dignity-Dev: detected sites not explained by the evaluation's non-live list: {extra}")
    described = "; ".join(f"{detected[site]['query_id']} `{site}` (`{non_live[site]['site_status']}`)" for site in extra)
    notes.append(f"- **Dignity-Dev:** {len(detected)} detected vs {evaluation['gt_live_sites']} ground-truth live sites. "
                 f"The extra site is {described}. The evaluation reports it as a non-live site, so it is not counted.")
    sources.append(("Dignity-Dev", "current code", "extra detected site", ", ".join(extra), conv_path("dignity_dev_hms"),
                    "source_file, source_line of every entry, minus the ground-truth sites"))
    sources.append(("Dignity-Dev", "current code", "reason not counted", ", ".join(non_live[s]["site_status"] for s in extra),
                    f"evidence/evaluation/{relative}", f"detection.{non_live_field}[].site_status"))

    # Fee-Rainbow: converted sites split by ground-truth label.
    relative = "fee_rainbow/evaluation_wrapper_support.json"
    evaluation = load(relative)
    label = {d["site"]: d for d in evaluation["details"]}
    label.update({a["site"]: a for a in evaluation["ambiguous_sites"]})
    converted = {site for site, c in sites("fee_rainbow").items() if c["status"] == "CONVERTED"}
    summary = runs["fee_rainbow"][0]
    recoverable = [s for s, d in label.items() if d["expected_recoverable"] is True]
    ambiguous = [s for s, d in label.items() if d["expected_recoverable"] == "ambiguous"]
    rec_conv = [s for s in recoverable if s in converted]
    amb_conv = sorted(label[s]["gt_id"] for s in ambiguous if s in converted)
    amb_not = sorted(label[s]["gt_id"] for s in ambiguous if s not in converted)
    other = sorted(converted - set(recoverable) - set(ambiguous))
    rec_not = sorted(f"{label[s]['gt_id']} `{s}`" for s in recoverable if s not in converted)
    if len(converted) != summary["converted_queries"] or other:
        raise SystemExit(f"Fee-Rainbow: converted split does not add up (converted {len(converted)}, "
                         f"summary {summary['converted_queries']}, unlabelled or non-recoverable {other})")
    notes.append(f"- **Fee-Rainbow:** converted {len(converted)} = {len(rec_conv)} of the {len(recoverable)} recoverable sites"
                 + (f" + ambiguous {', '.join(amb_conv)}" if amb_conv else "")
                 + (f"; {', '.join(amb_not)} (ambiguous) is not converted" if amb_not else "")
                 + (f". Recoverable but not converted: {', '.join(rec_not)}" if rec_not else "") + ".")
    sources.append(("Fee-Rainbow", "current code", "converted split", f"{len(rec_conv)} + {len(amb_conv)}",
                    conv_path("fee_rainbow") + f"; evidence/evaluation/{relative}",
                    'status == "CONVERTED" per site, joined on site with details[].expected_recoverable and ambiguous_sites[]'))

    # Clinic: general rules + query-specific templates, IDs taken from the run.
    summary, converted, _ = runs["clinic_inventory_legacy_hard"]
    general, template = summary["converted_by_general_rules"], summary["converted_by_query_specific_template"]
    total = summary["converted_queries"]
    template_ids = sorted(c["query_id"] for c in converted
                          if c["status"] == "CONVERTED" and c.get("conversion_rule") == "query_specific_template")
    if general + template != total or len(template_ids) != template:
        raise SystemExit("Clinic: general + template counts do not reconcile with summary.json and converted_queries.json")
    test_dir = COMPONENT / "tests" / "benchmarks" / "clinic_hard"
    untested = [q for q in template_ids if not (test_dir / f"test_{q.lower()}_equivalence.py").is_file()]
    if untested:
        raise SystemExit(f"Clinic: no MySQL equivalence test file for template(s) {untested} in {test_dir}")
    notes.append(
        f"- **Clinic:** converted {total} = general-rule {general} + query-specific templates {template} "
        f"({', '.join(template_ids)}). General rules alone: {general}/{total} ({100 * general / total:.1f}%). "
        f"This figure is code-derived: {general} is `converted_by_general_rules` in this run, and a separate run "
        "with the templates disabled has not been measured. Each template is selected by query ID and matches only "
        "the exact Clinic source file, so the templates are not general conversion rules and are not evidence of "
        "generalization. Each has a MySQL equivalence test in "
        "`components/c3_data_layer/tests/benchmarks/clinic_hard/`; this script does not read their results.")
    sources.append(("Clinic", "current code", "converted by general rules", str(general),
                    f"{TEMP}/clinic_inventory_legacy_hard/summary.json", "converted_by_general_rules"))
    sources.append(("Clinic", "current code", "template query IDs", ", ".join(template_ids),
                    f"{TEMP}/clinic_inventory_legacy_hard/converted_queries.json",
                    'query_id where status == "CONVERTED" and conversion_rule == "query_specific_template"'))
    return notes


def current_code_section(base, head, sources):
    pipeline_unchanged_since(head)
    date = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    lines = [
        "", "## Current code, all 9 apps", "",
        f"- **Code:** git HEAD `{head}`. The pipeline source is unchanged since that hash. The script checks that HEAD did not move "
        "during the run, and that `git rev-list <HEAD>..HEAD` and `git status --porcelain` are both empty for "
        + " and ".join(f"`{p}`" for p in PIPELINE_PATHS) + ". It fails otherwise.",
        f"- **Run:** {date}, by this script, into a new temp folder outside the repo, written `{TEMP}` below. "
        "No `output/` folder is read.",
        "- **Command per app:** `python -m c3.cli run --php <PHP root> --schema <schema>` with `PYTHONPATH=src`, cwd "
        "`components/c3_data_layer`. The inputs are the same as `scripts/regenerate_outputs.ps1` and the earlier gate runs.",
        "- **Definition of converted:** `summary.json` field `converted_queries` = `converted_by_general_rules` + "
        "`converted_by_query_specific_template`. It counts the sites for which C3 generated a parameterized data-access function. "
        "Conversion by a query-specific template can cover a site that the general recovery rules left unresolved, so converted can exceed recovered.",
        "- **Unresolved sites:** read from `unresolved_queries.json` (field `stage`). The script checks the per-stage counts against `summary.json`.",
        "",
        "| App | PHP root | Schema | sites detected | recovered | converted | of which query-specific template | "
        "unresolved at recovery stage | unresolved at conversion stage | Note |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    conversion_lists = []
    runs = {}
    for app, folder, php, schema, has_gt in CURRENT_RUNS:
        out = base / folder
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        unresolved = json.loads((out / "unresolved_queries.json").read_text(encoding="utf-8"))
        by_stage = {stage: [u for u in unresolved if u.get("stage") == stage] for stage in ("recovery", "conversion")}
        for stage, field in (("recovery", "unresolved_recovery_stage"), ("conversion", "unresolved_conversion_stage")):
            if len(by_stage[stage]) != summary[field]:
                raise SystemExit(f"{app}: unresolved_queries.json has {len(by_stage[stage])} {stage}-stage entries "
                                 f"but summary.json {field} = {summary[field]}")
        if len(unresolved) != summary["unresolved_total"]:
            raise SystemExit(f"{app}: unresolved_queries.json has {len(unresolved)} entries, summary unresolved_total = {summary['unresolved_total']}")

        summary_path = f"{TEMP}/{folder}/summary.json"
        unresolved_path = f"{TEMP}/{folder}/unresolved_queries.json"
        fields = [("sites detected", "total_queries"), ("recovered", "recovered_queries"), ("converted", "converted_queries"),
                  ("of which query-specific template", "converted_by_query_specific_template"),
                  ("unresolved at recovery stage", "unresolved_recovery_stage"),
                  ("unresolved at conversion stage", "unresolved_conversion_stage")]
        for label, field in fields:
            sources.append((app, "current code", label, str(summary[field]), summary_path, field))
        note = "" if has_gt else NO_GT
        lines.append(f"| {app} | `benchmarks/apps/{php}` | `benchmarks/apps/{schema}` | "
                     + " | ".join(str(summary[f]) for _, f in fields) + f" | {note} |")

        php_root = (APPS / php).resolve()
        runs[folder] = (summary, json.loads((out / "converted_queries.json").read_text(encoding="utf-8")), php_root)
        for entry in by_stage["conversion"]:
            site = site_label(entry, php_root)
            conversion_lists.append((app, entry["query_id"], site, entry["reason_code"], entry["reason"]))
            sources.append((app, "current code", f"conversion-stage unresolved {entry['query_id']}", site, unresolved_path,
                            '[stage == "conversion"]: query_id, source_file, source_line, reason_code, reason'))

    lines += [""] + footnotes(runs, sources)
    lines += ["", "### Unresolved at conversion stage (current code)", "",
              "| App | Site ID | file:line | Reason code | Rejection reason |", "|---|---|---|---|---|"]
    lines += [f"| {app} | {qid} | `{site}` | {code} | {reason} |" for app, qid, site, code, reason in conversion_lists]
    if not conversion_lists:
        lines.append("| (none) | | | | |")
    lines += ["", f"Clinic, simple_crud, mixed_crud, hard_crud: {NO_GT}."]
    return lines, head, date


# ===================================================================== dashboard
# Data file of the C3 Migration Studio dashboard (components/c3_data_layer/dashboard/).
# Every figure the dashboard shows is a "measurement" below, with app, stage,
# numerator/denominator, value, status (measured / derived / not_measured) and
# the evidence file and field it came from. No SQL text, PHP input values,
# database rows, credentials or absolute paths are exported.

DASHBOARD_SCHEMA_VERSION = 1
SENSITIVE_APPS = {"hms_kishan0725"}  # no licence: reason text is withheld too (codes only)
EXPERIMENT_KEY = {"clinic_inventory_legacy_hard": "clinic", "helpdesk_legacy_holdout": "helpdesk"}
E1_RUNS = [("final", "evidence/mysql_runs/2026-10-15_195730/e1/php_vs_sqlalchemy_equivalence.json",
            "Re-run after the E1 harness fix (code d96987c; raw log records the pre-rewrite hash 01f427e)"),
           ("superseded", "evidence/mysql_runs/2026-10-15_181758/e1/php_vs_sqlalchemy_equivalence.json",
            "First run; its 5 FAILs were a harness classification defect (evidence/hms/E1_triage/triage.md)")]
E2_OFFLINE = "evidence/e2/2026-10-15/schema_comparison.json"
E2_LIVE = "evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json"
# E3 runs, newest first. The first one is shown per application (docs/c3_fr_status.json FR9 cites its measurement);
# the 15 Oct run keeps its measurement as dated history.
E3_RUNS = [("current-code", "current code (E3, 2026-10-19)", "evidence/mysql_runs/2026-10-19_000500/e3"),
           ("post-adaptation", "post-adaptation (E3, 2026-10-15)", "evidence/mysql_runs/2026-10-15_173218/e3")]
E3_DIR = E3_RUNS[0][2]
# Newest E4 run (all nine apps, 163 functions after the FR6 join rule); the method is described in the 17 Oct README.
E4_REPORT = "evidence/final/2026-10-20/e4/injection_report.json"
E4_LABEL = "current code (E4, 2026-10-20)"
E4_SCRIPT = "scripts/e4_injection_check.py"
# Saved (committed) pytest logs only, newest first: (key, title, metric prefix, file, fixed stage label or None).
# A run without a saved log is never shown. docs/c3_fr_status.json FR10 cites the two newest logs (fixed label None);
# superseded logs keep their original stage label (and measurement id) as dated history, not the latest run.
# The 2026-10-20 13:56 MySQL logs ran at 84e97c0, which only adds evidence to 4d62a90 (the non-MySQL run); likewise
# the 12:36 logs ran at 0939bc3, which only adds docs and evidence to d263cbb.
TEST_LOGS = [
    ("non_mysql", "Non-MySQL suite (saved log)", "tests", "evidence/final/2026-10-20/test_run_non_mysql.txt", None),
    ("with_mysql", "Full suite with MySQL (saved log)", "tests_with_mysql",
     "evidence/mysql_runs/2026-10-20_135617/full_suite_with_mysql_log.txt", None),
    ("non_mysql_pre_joins", "Non-MySQL suite, earlier saved log", "tests", "evidence/final/2026-10-21/test_run_non_mysql.txt",
     "saved log (2026-10-20, HEAD d263cbb)"),
    ("with_mysql_pre_joins", "Full suite with MySQL, earlier saved log", "tests_with_mysql",
     "evidence/mysql_runs/2026-10-20_123611/full_suite_with_mysql_log.txt", "saved log (2026-10-20, HEAD 0939bc3)"),
    ("non_mysql_2026_10_18", "Non-MySQL suite, earlier saved log", "tests", "evidence/final/2026-10-19/test_run_non_mysql.txt",
     "saved log (2026-10-18, HEAD 1d93e93)"),
    ("with_mysql_2026_10_18", "Full suite with MySQL, earlier saved log", "tests_with_mysql",
     "evidence/final/2026-10-19/mysql/full_suite_with_mysql_log.txt", "saved log (2026-10-18, HEAD 1d93e93)"),
    ("non_mysql_2026_10_17", "Non-MySQL suite, earlier saved log", "tests", "evidence/tests/test_run_non_mysql_2026-10-17.txt",
     "current code (tests, 2026-10-17)"),
]
TEST_LOG = TEST_LOGS[0][3]
CLINIC_MYSQL_LOG = "evidence/mysql_runs/2026-10-20_135617/clinic_mysql_equivalence_log.txt"
# FR6 join rule: the MySQL tests in this file compare direct SQL with the generated function (not PHP differential).
JOIN_RULE_TESTS = "tests/benchmarks/clinic_hard/test_join_rule_mysql.py"
# Conversion coverage before (tag c3-pre-joins-20261020, adeb1c8) and after the FR6 join rule, same definition as
# conversion_coverage_gt; the current-code value must equal its "t2_final" side.
JOIN_COVERAGE = "evidence/final/2026-10-20/conversion_coverage_post_t2.json"
BASELINE_SUMMARY = "evidence/baseline/2026-10-19/summary.json"
BASELINE_README = "evidence/baseline/2026-10-19/README.md"
BASELINE_DIMENSIONS = ("tables", "columns", "primary_keys", "nullability", "types", "foreign_keys")

STAGE_KEYS = {  # evaluation file -> canonical stage key (labels in STAGES are kept verbatim)
    "hms_kishan0725/evaluation_preadaptation.json": "pre-adaptation",
    "hms_kishan0725/evaluation_pre_H1.json": "post-adaptation",
    "hms_kishan0725/evaluation.json": "post-adaptation",
    "dignity_dev_hms/evaluation_first_holdout_run.json": "fresh-holdout",
    "dignity_dev_hms/evaluation.json": "fresh-holdout",
    "helpdesk_legacy_holdout/evaluation_baseline_6of9.json": "pre-adaptation",
    "helpdesk_legacy_holdout/evaluation.json": "post-adaptation",
    "news_buzz/evaluation_preadaptation.json": "pre-adaptation",
    "news_buzz/evaluation.json": "post-adaptation",
    "news_buzz/evaluation_post_escape_fix.json": "post-fix",
    "fee_rainbow/evaluation_preadaptation.json": "pre-adaptation",
    "fee_rainbow/evaluation.json": "post-adaptation",
    "fee_rainbow/evaluation_wrapper_support.json": "post-fix",
}
STAGE_DEFINITIONS = {
    "pre-adaptation": "Pipeline before any change made after seeing this app.",
    "post-adaptation": "Pipeline after general rules that were developed or reviewed using this app.",
    "post-fix": "Post-adaptation run after a specific disclosed fix (escape decoder or wrapper support).",
    "fresh-holdout": "App run untouched; no rule was changed for it (Dignity-Dev only).",
    "current-code": "Current pipeline code. Conversion counts, review rates and site lists come from this script's fresh run "
                    "(recorded HEAD); tests, E3, E4, timing and the sqlacodegen baseline cite their own saved evidence file, "
                    "date and HEAD (see the stage label and evidence file).",
}
APP_INFO = {  # run folder -> label, role, latest S15 evaluation, licence / provenance note
    "hms_kishan0725": ("HMS (kishan0725)", "post-adaptation benchmark (third-party)", "hms_kishan0725/evaluation.json",
                       "No licence: source not committed; SQL text and values are never exported"),
    "dignity_dev_hms": ("Dignity-Dev", "fresh independent holdout", "dignity_dev_hms/evaluation.json",
                        "MIT (evidence register row 35)"),
    "helpdesk_legacy_holdout": ("Helpdesk", "holdout baseline, post-adaptation after S13", "helpdesk_legacy_holdout/evaluation.json",
                                "Project-authored fixture"),
    "news_buzz": ("NEWS-BUZZ", "post-adaptation benchmark (third-party)", "news_buzz/evaluation_post_escape_fix.json",
                  "MIT (its README)"),
    "fee_rainbow": ("Fee-Rainbow", "post-adaptation benchmark (third-party)", "fee_rainbow/evaluation_wrapper_support.json",
                    "LICENSE file present; licence type not verified"),
    "clinic_inventory_legacy_hard": ("Clinic", "development fixture", None, "Project-authored fixture"),
    "simple_crud": ("simple_crud", "development fixture", None, "Project-authored fixture"),
    "mixed_crud": ("mixed_crud", "development fixture", None, "Project-authored fixture"),
    "hard_crud": ("hard_crud", "development fixture", None, "Project-authored fixture"),
}
SQL_TEXT = [re.compile(r"\bSELECT\b[^.]{0,200}\bFROM\b"), re.compile(r"\bINSERT\s+INTO\b"),
            re.compile(r"\bUPDATE\s+\w+\s+SET\b"), re.compile(r"\bDELETE\s+FROM\b")]
LOCAL_TEXT = [re.compile(r"mysql\+\w+://"), re.compile(r"[A-Za-z]:[\\/]"), re.compile(r"AppData|/Users/|\\Users\\")]
FORBIDDEN_TEXT = SQL_TEXT + LOCAL_TEXT + [re.compile(r"password\s*[=:]", re.I)]  # privacy guard over every exported string
# Before / After: SQL and table detail only for apps whose source may be published (MIT or project-authored).
# HMS (no licence) and the other apps get counts only.
BEFORE_AFTER_DETAIL = ("news_buzz", "helpdesk_legacy_holdout", "clinic_inventory_legacy_hard")
BEFORE_AFTER_SQL_FIELD = re.compile(r"^\$\.before_after\.apps\.(%s)\.queries\[\d+\]\.(legacy_sql|generated_sql|php_argument|php_line|function\.code)$"
                                    % "|".join(BEFORE_AFTER_DETAIL))
# Verified by reading the source on 2026-10-19 (no source text exported): why bound parameters and user-controlled
# values differ for Dignity-Dev.
BEFORE_AFTER_APP_NOTES = {
    "dignity_dev_hms": "Verified in the source: the bound values come from parameters of PHP functions in library.php "
                       "(passed through a sanitising helper before the SQL is built). The security report does not follow "
                       "function parameters to their call sites, so it classifies none of them as user-controlled.",
}
# A quoted literal password; '{{name}}' is C3's placeholder for a PHP variable, not a value.
LITERAL_PASSWORD = re.compile(r"password\s*=\s*'(?!\{\{)[^'$]+'", re.I)


def _pct(numerator, denominator):
    return None if not denominator else round(100 * numerator / denominator, 1)


def _measurement(app, stage, stage_label, category, metric, label, *, value, unit, status, file,
                 numerator=None, denominator=None, fields=None, derivation=None, note=None):
    if status not in ("measured", "derived", "not_measured"):
        raise ValueError(status)
    return {
        "id": f"{app}|{stage_label}|{metric}", "app": app, "stage": stage, "stage_label": stage_label,
        "category": category, "metric": metric, "label": label,
        "numerator": numerator, "denominator": denominator, "value": value, "unit": unit, "status": status,
        "source": {"file": file, **({"fields": fields} if fields else {}), **({"derivation": derivation} if derivation else {})},
        **({"note": note} if note else {}),
    }


def _not_measured(app, category, metric, label, note):
    return _measurement(app, "not-measured", "not measured", category, metric, label, value=None, unit="none",
                        status="not_measured", file=None, note=note)


def _gt_labels(relative):
    """site -> (gt_id, label) from an S15 evaluation, plus the sites reported as non-live."""
    data = load(relative)
    labels = {}
    for d in data["details"] + data.get("ambiguous_sites", []):
        expected = d["expected_recoverable"]
        labels[d["site"]] = (d["gt_id"], "ambiguous" if expected == "ambiguous" else
                             "recoverable" if expected is True else "non-recoverable")
    detection = data["detection"]
    for n in detection.get("non_live_sites_reported", detection.get("false_detections_commented_out", [])):
        labels.setdefault(n["site"], (None, f"non-live ({n.get('site_status', 'not live')})"))
    return data, labels


def _join_kind(sql):
    if not sql:
        return None
    if re.search(r"\bLEFT\s+(OUTER\s+)?JOIN\b", sql, re.I):
        return "LEFT JOIN"
    if re.search(r"\bRIGHT\s+(OUTER\s+)?JOIN\b", sql, re.I):
        return "RIGHT JOIN"
    match = re.search(r"\bFROM\b(.*?)(\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|$)", sql, re.I | re.S)
    if match and "," in match.group(1) and not re.search(r"\bJOIN\b", match.group(1), re.I):
        return "implicit comma join"
    return "other multi-table form"


def _historical(measurements, stage_sites):
    for app_label, relative, stage_label in STAGES:
        if not (EVAL / relative).exists():
            continue
        data = load(relative)
        app = relative.split("/")[0]
        stage = STAGE_KEYS[relative]
        recovery_only = "recovery" in str(data.get("scope", "")).lower()
        label = stage_label + (" - recovery only" if recovery_only else "")
        path = f"evidence/evaluation/{relative}"
        r, detection = data["recovery"], data["detection"]
        detected_tn = sum(1 for d in data["details"] if d.get("outcome") == "TN" and d.get("c3_status") != "NOT_DETECTED")
        undetected_tn = r["TN"] - detected_tn
        args = dict(file=path)
        measurements += [
            _measurement(app, stage, label, "detection", "sql_detection", "SQL sites detected (of ground-truth live sites)",
                         numerator=detection["live_detected"], denominator=data["gt_live_sites"], value=detection["recall"],
                         unit="percent", status="measured",
                         fields={"numerator": "detection.live_detected", "denominator": "gt_live_sites", "value": "detection.recall"}, **args),
            _measurement(app, stage, label, "recovery", "recovery_precision", "Recovery precision",
                         numerator=r["TP"], denominator=r["TP"] + r["FP"], value=r["precision"], unit="percent", status="measured",
                         fields={"numerator": "recovery.TP", "denominator": "recovery.TP + recovery.FP", "value": "recovery.precision"}, **args),
            _measurement(app, stage, label, "recovery", "recovery_recall", "Recovery recall",
                         numerator=r["TP"], denominator=r["TP"] + r["FN"], value=r["recall"], unit="percent", status="measured",
                         fields={"numerator": "recovery.TP", "denominator": "recovery.TP + recovery.FN", "value": "recovery.recall"}, **args),
            _measurement(app, stage, label, "abstention", "correct_abstention", "Correct abstentions (detected, unresolved, non-recoverable)",
                         numerator=detected_tn, denominator=r["TN"] + r["FP"], value=_pct(detected_tn, r["TN"] + r["FP"]),
                         unit="percent", status="derived",
                         derivation='count details[] with outcome == "TN" and c3_status != "NOT_DETECTED"; denominator recovery.TN + recovery.FP',
                         note=("Not applicable: the ground truth has no non-recoverable live sites." if not r["TN"] + r["FP"] else
                               f"{undetected_tn} non-recoverable site(s) were not detected at all; the evaluator scores them TN, "
                               "but they are not counted as abstentions" if undetected_tn else None), **args),
            _measurement(app, stage, label, "recovery", "ambiguous_excluded", "Ambiguous sites excluded from precision/recall",
                         value=r["excluded_ambiguous"], unit="count", status="measured",
                         fields={"value": "recovery.excluded_ambiguous"}, **args),
        ]
        columns = data.get("column_use_accuracy")
        if columns:
            mismatch = {s["site"] for s in (data.get("schema_mismatch") or {}).get("sites", [])}
            scored = [d for d in data["details"] if d.get("column_checks") and d["site"] not in mismatch]
            for field in ("operation", "tables", "read", "write", "filter"):
                numerator = sum(1 for d in scored if d["column_checks"].get(field))
                consistent = len(scored) == columns["sites"] and _pct(numerator, columns["sites"]) == columns[field]
                measurements.append(_measurement(
                    app, stage, label, "column_use", f"column_use_{field}", f"Column use: {field}",
                    numerator=numerator if consistent else None, denominator=columns["sites"], value=columns[field],
                    unit="percent", status="measured",
                    fields={"value": f"column_use_accuracy.{field}", "denominator": "column_use_accuracy.sites",
                            **({"numerator": f"count details[].column_checks.{field} (schema-consistent sites)"} if consistent else {})},
                    note=None if not mismatch else f"{len(mismatch)} schema-mismatch site(s) reported separately", **args))
        stage_sites[f"{app}|{label}"] = [
            {"gt_id": d["gt_id"], "query_id": d.get("c3_query_id"), "site": d["site"], "outcome": d["outcome"],
             "c3_status": d.get("c3_status"), "reason_code": d.get("c3_reason_code")}
            for d in data["details"] + data.get("ambiguous_sites", []) if d["outcome"] in ("FN", "FP", "TN", "EXCLUDED_AMBIGUOUS")]


MANUAL_REVIEW_FORMULA = ("manual-review rate = (live sites unresolved at the recovery stage + live sites unresolved at the "
                         "conversion stage) / live sites. Each site is counted once; non-live sites (commented-out or dead code) "
                         "are excluded; ambiguous ground-truth sites are included in both counts and listed separately.")
MANUAL_REVIEW_LIMITS = ("A proxy for how much of the data layer still needs a person: it counts sites, not developer hours. It says "
                        "nothing about how long each site takes to migrate by hand, and nothing about whether the converted code is "
                        "correct. It is not a measurement of developer effort.")
NOT_VERIFIED = "pipeline counts, not verified"
PERFORMANCE_FILE = "evidence/performance/2026-10-18/execution_time.json"


def compute_manual_review(base) -> dict:
    """Per app: live sites not converted automatically, from the current-code run (one function for both outputs)."""

    result = {}
    for app_label, folder, php, schema, has_gt in CURRENT_RUNS:
        out = base / folder
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        unresolved = json.loads((out / "unresolved_queries.json").read_text(encoding="utf-8"))
        queries = json.loads((out / "queries.json").read_text(encoding="utf-8"))
        php_root = (APPS / php).resolve()
        latest = APP_INFO[folder][2]
        labels = _gt_labels(latest)[1] if latest else {}

        def is_live(site):
            if not latest:
                return True  # no ground truth: every detected site is treated as live (pipeline counts, not verified)
            gt_label = labels.get(site, (None, None))[1]
            if gt_label is None:
                raise SystemExit(f"{folder}: detected site {site} is not in the ground truth; cannot classify it as live or not")
            return not gt_label.startswith("non-live")

        detected_live = sum(1 for q in queries if is_live(site_label(q, php_root)))
        live_sites = load(latest)["gt_live_sites"] if latest else summary["total_queries"]
        if detected_live != live_sites:
            raise SystemExit(f"{folder}: {detected_live} live sites detected but {live_sites} live sites in the ground truth; "
                             "the manual-review rate would miss undetected sites")

        counted, excluded = [], []
        for u in unresolved:
            site = site_label(u, php_root)
            gt_id, gt_label = labels.get(site, (None, None))
            entry = {"query_id": u["query_id"], "gt_id": gt_id, "site": site, "stage": u["stage"],
                     "reason_code": u["reason_code"], "gt_label": gt_label}
            (counted if is_live(site) else excluded).append(entry)
        if len({e["site"] for e in counted}) != len(counted):
            raise SystemExit(f"{folder}: a live site appears twice in unresolved_queries.json")
        if len(counted) + len(excluded) != summary["unresolved_total"]:
            raise SystemExit(f"{folder}: manual-review sites do not reconcile with summary.json unresolved_total")
        for stage, field in (("recovery", "unresolved_recovery_stage"), ("conversion", "unresolved_conversion_stage")):
            if sum(e["stage"] == stage for e in counted + excluded) != summary[field]:
                raise SystemExit(f"{folder}: {stage}-stage sites do not reconcile with summary.json {field}")

        unresolved_sites = {e["site"] for e in counted}
        ambiguous = [{"gt_id": gt_id, "site": site, "converted": site not in unresolved_sites, "included": True}
                     for site, (gt_id, gt_label) in sorted(labels.items()) if gt_label == "ambiguous"]
        result[folder] = {
            "app": app_label, "verified_ground_truth": has_gt, "label": None if has_gt else NOT_VERIFIED,
            "live_sites": live_sites,
            "live_sites_source": f"evidence/evaluation/{latest} gt_live_sites" if latest else "summary.json total_queries (no ground truth)",
            "unresolved_recovery_live": sum(e["stage"] == "recovery" for e in counted),
            "unresolved_conversion_live": sum(e["stage"] == "conversion" for e in counted),
            "not_converted_live": len(counted), "value": _pct(len(counted), live_sites),
            "sites": counted, "ambiguous": ambiguous, "excluded_non_live": excluded,
            "summary_counts": {k: summary[k] for k in ("total_queries", "unresolved_total", "unresolved_recovery_stage",
                                                       "unresolved_conversion_stage")},
        }
    return result


def load_performance() -> dict:
    data = json.loads((ROOT / PERFORMANCE_FILE).read_text(encoding="utf-8"))
    return {"file": PERFORMANCE_FILE, "label": data["label"], "git_head": data["git_head"], "started_at": data["started_at"],
            "rounds": data["rounds"], "order": data["order"], "timer": data["timer"], "machine": data["machine"],
            "apps": {app: {k: v[k] for k in ("runs_seconds", "median_seconds", "sites_detected")} for app, v in data["apps"].items()},
            "readme": "evidence/performance/2026-10-18/README.md"}


def _current(base, measurements):
    apps, records, artifacts, joins = {}, {}, {}, []
    join_coverage = json.loads((ROOT / JOIN_COVERAGE).read_text(encoding="utf-8"))
    for app_label, folder, php, schema, has_gt in CURRENT_RUNS:
        out = base / folder
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        queries = json.loads((out / "queries.json").read_text(encoding="utf-8"))
        converted = {c["query_id"]: c for c in json.loads((out / "converted_queries.json").read_text(encoding="utf-8"))}
        unresolved = {u["query_id"]: u for u in json.loads((out / "unresolved_queries.json").read_text(encoding="utf-8"))}
        php_root = (APPS / php).resolve()
        label, role, latest, licence = APP_INFO[folder]
        labels = _gt_labels(latest)[1] if latest else {}
        sensitive = folder in SENSITIVE_APPS

        rows = []
        for q in queries:
            site = site_label(q, php_root)
            conv, unres = converted.get(q["query_id"], {}), unresolved.get(q["query_id"])
            gt_id, gt_label = labels.get(site, (None, None))
            row = {
                "query_id": q["query_id"], "gt_id": gt_id, "file": site.rsplit(":", 1)[0], "line": q["source_line"],
                "via_wrapper": bool(q.get("wrapper")), "operation": q.get("operation"),
                "recovery_status": q["status"], "recovery_reason_code": q.get("reason_code"),
                "conversion_status": "CONVERTED" if conv.get("status") == "CONVERTED" else "NOT_CONVERTED",
                "conversion_rule": conv.get("conversion_rule"),
                "unresolved_stage": unres["stage"] if unres else None,
                "unresolved_reason_code": unres["reason_code"] if unres else None,
                "unresolved_reason": (None if sensitive else unres["reason"]) if unres else None,
                "gt_label": gt_label,
            }
            if unres and unres["stage"] == "conversion":
                row["join_kind"] = _join_kind(q.get("recovered_sql"))
                joins.append({"app": folder, "query_id": q["query_id"], "gt_id": gt_id, "site": site,
                              "join_kind": row["join_kind"], "reason_code": unres["reason_code"], "gt_label": gt_label})
            rows.append(row)
        records[folder] = rows

        if len(rows) != summary["total_queries"] or sum(r["conversion_status"] == "CONVERTED" for r in rows) != summary["converted_queries"]:
            raise SystemExit(f"{folder}: per-query records do not reconcile with summary.json")
        if sum(r["unresolved_stage"] is not None for r in rows) != summary["unresolved_total"]:
            raise SystemExit(f"{folder}: unresolved records do not reconcile with summary.json")
        file = f"{TEMP}/{folder}/summary.json"
        for metric, field, text in [
            ("sql_detected", "total_queries", "SQL sites detected"),
            ("sql_recovered", "recovered_queries", "SQL recovered"),
            ("converted_functions", "converted_queries", "Converted data-access functions"),
            ("converted_general", "converted_by_general_rules", "Converted by general rules"),
            ("converted_template", "converted_by_query_specific_template", "Converted by query-specific templates"),
            ("unresolved_recovery", "unresolved_recovery_stage", "Unresolved at recovery stage"),
            ("unresolved_conversion", "unresolved_conversion_stage", "Unresolved at conversion stage"),
            ("unresolved_total", "unresolved_total", "Unresolved sites (flagged for review)"),
        ]:
            category = "detection" if metric == "sql_detected" else "recovery" if metric == "sql_recovered" else \
                "conversion" if metric.startswith("converted") else "unresolved"
            measurements.append(_measurement(folder, "current-code", "current code", category, metric, text,
                                             value=summary[field], unit="count", status="measured", file=file,
                                             fields={"value": field}))
        if latest:
            gt_data = load(latest)
            recoverable = gt_data["recovery"]["TP"] + gt_data["recovery"]["FN"]
            converted_recoverable = sum(1 for r in rows if r["gt_label"] == "recoverable" and r["conversion_status"] == "CONVERTED")
            measurements.append(_measurement(
                folder, "current-code", "current code", "conversion", "conversion_coverage_gt",
                "Ground-truth recoverable sites converted", numerator=converted_recoverable, denominator=recoverable,
                value=_pct(converted_recoverable, recoverable), unit="percent", status="derived",
                file=f"{TEMP}/{folder}/converted_queries.json; evidence/evaluation/{latest}",
                derivation="CONVERTED records joined on file:line with ground-truth label 'recoverable'; "
                           "denominator recovery.TP + recovery.FN of the latest evaluation",
                note="Conversion coverage, not recovery accuracy. Conversion is not behavioural equivalence."))
            before = join_coverage["apps"].get(folder)
            if before is not None:
                if (before["t2_final"]["converted_recoverable"], before["denominator"]) != (converted_recoverable, recoverable):
                    raise SystemExit(f"{folder}: current-code conversion coverage differs from {JOIN_COVERAGE} (t2_final)")
                measurements.append(_measurement(
                    folder, "current-code", "before the FR6 join rule (adeb1c8)", "conversion", "conversion_coverage_gt",
                    "Ground-truth recoverable sites converted", numerator=before["prejoins"]["converted_recoverable"],
                    denominator=before["denominator"], value=_pct(before["prejoins"]["converted_recoverable"], before["denominator"]),
                    unit="percent", status="derived", file=JOIN_COVERAGE,
                    fields={"numerator": f"apps.{folder}.prejoins.converted_recoverable", "denominator": f"apps.{folder}.denominator"},
                    derivation="numerator / denominator from the committed post-T2 record (gate run at adeb1c8, same definition "
                               "as conversion_coverage_gt)",
                    note="Code at tag c3-pre-joins-20261020, before LEFT JOIN and comma-join SELECTs could convert. Conversion "
                         "coverage, not recovery accuracy; the historical evaluation files are unchanged."))

        files = {name: (out / name).is_file() for name in
                 ("models.py", "data_access.py", "schema.json", "migration_notes.md", "queries.json", "converted_queries.json",
                  "unresolved_queries.json", "column_usage.json", "traceability_report.json", "security_report.json", "summary.json",
                  "alembic/alembic.ini", "alembic/env.py")}
        models = (out / "models.py").read_text(encoding="utf-8")
        data_access = (out / "data_access.py").read_text(encoding="utf-8")
        stubs = len(re.findall(r"^def unresolved_query_\w+\(", data_access, re.M))
        functions = len(re.findall(r"^def (?!unresolved_query_)\w+_q\d+\(", data_access, re.M))
        if functions != summary["converted_queries"] or stubs != summary["unresolved_total"]:
            raise SystemExit(f"{folder}: generated functions/stubs ({functions}/{stubs}) do not match summary.json")
        artifacts[folder] = {
            "files": files,
            "counts": {
                "orm_classes": len(re.findall(r"^class \w+\(Base\):", models, re.M)),
                "core_tables": len(re.findall(r"^\w+ = Table\(", models, re.M)),
                "data_access_functions": len(re.findall(r"^def (?!unresolved_query_)\w+_q\d+\(", data_access, re.M)),
                "unresolved_stubs": len(re.findall(r"^def unresolved_query_\w+\(", data_access, re.M)),
                "alembic_revisions": len(list((out / "alembic" / "versions").glob("*.py"))),
            },
            "derivation": "counted from the generated files of the current-code run (regex over models.py / data_access.py; "
                          "files in alembic/versions); file contents are not exported",
        }
        apps[folder] = {"id": folder, "label": label, "role": role, "has_author_verified_ground_truth": has_gt,
                        "latest_evaluation": f"evidence/evaluation/{latest}" if latest else None, "provenance": licence,
                        "php_root": f"benchmarks/apps/{php}", "schema": f"benchmarks/apps/{schema}",
                        "note": None if has_gt else NO_GT}
    return apps, records, artifacts, joins


def _disclosures(records, measurements):
    clinic = records["clinic_inventory_legacy_hard"]
    general = [r["query_id"] for r in clinic if r["conversion_rule"] == "general"]
    templates = [r["query_id"] for r in clinic if r["conversion_rule"] == "query_specific_template"]
    total = len(general) + len(templates)
    measurements.append(_measurement(
        "clinic_inventory_legacy_hard", "current-code", "current code", "conversion", "general_rule_share",
        "General-rule conversions (code-derived)", numerator=len(general), denominator=total, value=_pct(len(general), total),
        unit="percent", status="derived", file=f"{TEMP}/clinic_inventory_legacy_hard/converted_queries.json",
        derivation='count conversion_rule == "general" / all CONVERTED',
        note="Code-derived. A separate run with the templates disabled has not been measured."))
    fee = records["fee_rainbow"]
    fee_converted = [r for r in fee if r["conversion_status"] == "CONVERTED"]
    fee_ambiguous = [r for r in fee_converted if r["gt_label"] == "ambiguous"]
    fee_not = [r for r in fee if r["gt_label"] == "ambiguous" and r["conversion_status"] != "CONVERTED"]
    return {
        "clinic": {
            "converted": total, "general_rule": len(general), "query_specific_template": len(templates),
            "template_query_ids": templates,
            "general_rule_share": {"numerator": len(general), "denominator": total, "value": _pct(len(general), total),
                                   "status": "derived"},
            "text": (f"Clinic: {total} conversions = {len(general)} general-rule + {len(templates)} query-specific templates "
                     f"({', '.join(templates)}). {len(general)}/{total} is code-derived; a separate templates-disabled run "
                     "has not been measured. Each template is selected by query ID and matches only the exact Clinic source "
                     "file, so it is not a general conversion rule. Clinic has no author-verified ground truth: pipeline counts, "
                     "not accuracy."),
        },
        "fee_rainbow": {
            "converted": len(fee_converted),
            "recoverable_converted": sum(1 for r in fee_converted if r["gt_label"] == "recoverable"),
            "ambiguous_converted": [r["gt_id"] for r in fee_ambiguous],
            "ambiguous_not_converted": [r["gt_id"] for r in fee_not],
            "text": (f"Fee-Rainbow: {len(fee_converted)} conversions = "
                     f"{sum(1 for r in fee_converted if r['gt_label'] == 'recoverable')} ground-truth recoverable sites + "
                     f"ambiguous {', '.join(r['gt_id'] for r in fee_ambiguous)}; "
                     f"{', '.join(r['gt_id'] for r in fee_not)} (ambiguous) is not converted. Ground-truth IDs and query IDs "
                     "differ from G034/Q033 onward (the ground truth also numbers commented-out lines)."),
        },
        "dignity_dev": {"text": "Dignity-Dev is the only fresh independent holdout: run untouched, no rule changed for it. "
                                "Its first holdout run and the current rules give identical S15 results."},
        "ground_truth": {"text": "Ground truth was drafted blind by an LLM and verified by one human (the author). The drafting "
                                 "model is from the same model family as the development assistant, so shared blind spots are "
                                 "possible. Matching is by file and line, never by query ID."},
        "no_overall_score": {"text": "There is deliberately no combined overall accuracy score. Detection, recovery, conversion, "
                                     "column use and behavioural equivalence measure different things."},
    }


def _experiments(measurements):
    e1 = {"runs": []}
    for kind, relative, note in E1_RUNS:
        data = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        for app, result in data["apps"].items():
            verdicts = result["verdicts"]
            e1["runs"].append({"app": app, "kind": kind, "sites": result["sites"], "file": relative, "note": note,
                               "verdicts": {v: verdicts.get(v, 0) for v in ("PASS", "FAIL", "INCONCLUSIVE")}})
            if sum(verdicts.values()) != result["sites"]:
                raise SystemExit(f"{relative}: verdicts do not sum to sites")
            if kind != "final":
                continue
            for verdict in ("PASS", "FAIL", "INCONCLUSIVE"):
                present = verdict in verdicts
                measurements.append(_measurement(
                    app, "post-adaptation", "post-adaptation (E1 re-run)", "e1", f"e1_{verdict.lower()}", f"E1 {verdict}",
                    numerator=verdicts.get(verdict, 0), denominator=result["sites"], value=verdicts.get(verdict, 0),
                    unit="count", status="measured", file=relative,
                    fields={**({"value": f"apps.{app}.verdicts.{verdict}"} if present else {}), "denominator": f"apps.{app}.sites"},
                    derivation=None if present else f"no {verdict} key in apps.{app}.verdicts, so 0 (sites = sum of the other verdicts)"))
    clinic_suite = _pytest_log(CLINIC_MYSQL_LOG)
    collected = int(re.search(r"collected (\d+) items", clinic_suite["text"]).group(1))
    if clinic_suite["passed"] != collected or clinic_suite["failed"]:
        raise SystemExit(f"{CLINIC_MYSQL_LOG}: not every collected test passed; update the dashboard text before showing it")
    join_lines = re.findall(rf"^{re.escape(JOIN_RULE_TESTS)}::\S+ (\w+)", clinic_suite["text"], re.M)
    join_passed = sum(r == "PASSED" for r in join_lines)
    if not join_lines or join_passed != len(join_lines):
        raise SystemExit(f"{CLINIC_MYSQL_LOG}: the FR6 join-rule tests are missing or did not all pass")
    stage_label = f"saved log ({clinic_suite['date']}, HEAD {clinic_suite['head'][:7]})"
    e1["separate_suites"] = [{
        "app": "clinic_inventory_legacy_hard", "file": CLINIC_MYSQL_LOG, "passed": clinic_suite["passed"], "collected": collected,
        "failed": clinic_suite["failed"], "head": clinic_suite["head"], "date": clinic_suite["date"],
        "mysql_server": clinic_suite["mysql_server"], "join_rule_tests": len(join_lines), "join_rule_passed": join_passed,
        "note": f"Clinic MySQL verification: Clinic's pytest MySQL suite (tests/benchmarks/clinic_hard), not the E1 harness: each test passes or fails, "
                f"there is no INCONCLUSIVE category. {collected - len(join_lines)} tests compare the Clinic application's own "
                f"PHP with the generated functions (they include test_q002_sql_injection_is_blocked). The other "
                f"{len(join_lines)} are the FR6 join-rule tests ({JOIN_RULE_TESTS}): they compare the same SQL run directly "
                "(PHP mysqli, read with mysqli_fetch_assoc) with the generated function on the Clinic schema, for a LEFT JOIN "
                "with NULL-extended rows, a comma join and an INNER JOIN; that is direct SQL vs generated function, not "
                "differential testing of application PHP."}]
    measurements.append(_measurement(
        "clinic_inventory_legacy_hard", "current-code", stage_label,
        "mysql_tests", "mysql_equivalence_tests_passed", "Clinic MySQL verification tests passed (not E1)",
        numerator=clinic_suite["passed"], denominator=collected, value=clinic_suite["passed"], unit="count", status="measured",
        file=CLINIC_MYSQL_LOG, fields={"value": f"summary line '{clinic_suite['summary_line']}'",
                                       "denominator": f"line 'collected {collected} items'"},
        note=f"MySQL {clinic_suite['mysql_server']}. " + e1["separate_suites"][0]["note"]))
    measurements.append(_measurement(
        "clinic_inventory_legacy_hard", "current-code", stage_label, "mysql_tests", "join_rule_mysql_tests_passed",
        "FR6 join-rule MySQL tests passed (direct SQL vs generated function, not PHP differential)",
        numerator=join_passed, denominator=len(join_lines), value=join_passed, unit="count", status="measured",
        file=CLINIC_MYSQL_LOG, fields={"value": f"lines '{JOIN_RULE_TESTS}::... PASSED'", "denominator": f"lines '{JOIN_RULE_TESTS}::...'"},
        note=f"MySQL {clinic_suite['mysql_server']}. Clinic schema and seed data; LEFT JOIN with NULL-extended rows, comma join, "
             "INNER JOIN regression and one bound hostile value. Counted inside the Clinic suite total. Not PHP application "
             "differential testing and not E1."))
    for folder in ("dignity_dev_hms", "helpdesk_legacy_holdout", "news_buzz", "fee_rainbow", "clinic_inventory_legacy_hard",
                   "simple_crud", "mixed_crud", "hard_crud"):
        note = "No E1 run. Successful conversion is not behavioural equivalence."
        if folder == "clinic_inventory_legacy_hard":
            note = "No E1-harness run. Clinic has the Clinic MySQL verification suite instead (see the E1 card)."
        measurements.append(_not_measured(folder, "e1", "e1_equivalence", "E1 behavioural equivalence on MySQL", note))

    e2_offline = json.loads((ROOT / E2_OFFLINE).read_text(encoding="utf-8"))
    e2_live = json.loads((ROOT / E2_LIVE).read_text(encoding="utf-8"))
    e2, e3, e3_dates, e3_runs = {}, {}, {}, []
    e2_dates = {"offline": e2_offline["date_utc"][:len("yyyy-mm-dd")], "live": e2_live["date_utc"][:len("yyyy-mm-dd")]}
    for folder in APP_INFO:
        key = EXPERIMENT_KEY.get(folder, folder)
        offline, live = e2_offline["apps"][key]["verdicts"], e2_live["apps"][key]["verdicts"]
        e2[folder] = {"A_vs_B": offline["A_vs_B"], "A_vs_C": offline["A_vs_C"], "D_vs_C": live["D_vs_C"],
                      "query_schema_drift": live["query_schema_drift"],
                      "tables": e2_live["apps"][key]["A"]["tables"], "columns": e2_live["apps"][key]["A"]["columns"]}
    e2_match = sum(1 for v in e2.values() if v["A_vs_B"] == v["A_vs_C"] == v["D_vs_C"] == "MATCH")
    measurements.append(
        _measurement("all", "post-adaptation", "post-adaptation (E2, 2026-10-15)", "e2", "e2_schema_match",
                     "E2 schema fidelity: apps where every view matches", numerator=e2_match, denominator=len(e2),
                     value=e2_match, unit="count", status="derived", file=f"{E2_OFFLINE}; {E2_LIVE}",
                     derivation="apps with verdicts A_vs_B, A_vs_C (offline) and D_vs_C (MySQL) all MATCH"))
    for stage, stage_label, directory in E3_RUNS:
        header = (ROOT / directory / "summary.txt").read_text(encoding="utf-8-sig")
        date = re.search(r"^# date: (\d{4}-\d{2}-\d{2})", header, re.M).group(1)  # local date of the run
        head = re.search(r"^# git HEAD: (\w+)", header, re.M).group(1)
        verdicts = {}
        for folder in APP_INFO:
            key = EXPERIMENT_KEY.get(folder, folder)
            e3_data = json.loads((ROOT / directory / f"{key}.json").read_text(encoding="utf-8-sig"))
            if re.search(rf"^{re.escape(key)} : (\w+)", header, re.M).group(1) != e3_data["verdict"]:
                raise SystemExit(f"{directory}: summary.txt and {key}.json disagree")
            verdicts[folder] = e3_data["verdict"]
            if directory == E3_DIR:
                e3[folder] = {"verdict": e3_data["verdict"], "steps": e3_data["steps"], "schema_diffs": len(e3_data["schema_diffs"])}
                e3_dates[folder] = date
        e3_pass = sum(v == "PASS" for v in verdicts.values())
        e3_runs.append({"stage_label": stage_label, "dir": directory, "date": date, "git_head": head, "git_head_short": head[:7], "pass": e3_pass,
                        "apps": len(verdicts), "current": directory == E3_DIR})
        measurements.append(_measurement(
            "all", stage, stage_label, "e3", "e3_roundtrip_pass", "E3 Alembic round trip on MySQL: apps PASS",
            numerator=e3_pass, denominator=len(verdicts), value=e3_pass, unit="count", status="derived",
            file=f"{directory}/<app>.json", derivation="count verdict == PASS (each checked against summary.txt)",
            note=f"Run {date}, git HEAD {head[:7]} (from {directory}/summary.txt)."))

    report = json.loads((ROOT / E4_REPORT).read_text(encoding="utf-8"))
    benign = ast.literal_eval(re.search(r"^BENIGN = (\[.*?\])$", (ROOT / E4_SCRIPT).read_text(encoding="utf-8"), re.M).group(1))
    e4 = {"git_head": report["git_head"], "date_utc": report["date_utc"], "payload_count": len(report["payloads"]),
          "benign_value_count": len(benign), "apps": {}}
    for key, app in report["apps"].items():
        folder = next((f for f, k in EXPERIMENT_KEY.items() if k == key), key)
        cases = [c for f in app["detail"].values() for c in f["cases"]]
        e4["apps"][folder] = {"functions": app["functions"], "safe": app["safe"], "unsafe": app["unsafe"],
                              "hostile_cases": len(cases), "rejected": sum(c["outcome"] == "REJECTED" for c in cases),
                              "bound": sum(c["bound_as_parameter"] for c in cases),
                              "templates": sum(f["conversion_rule"] == "query_specific_template" for f in app["detail"].values())}
        measurements.append(_measurement(folder, "current-code", E4_LABEL, "e4", "e4_safe_functions",
                                         "E4 functions SAFE (12-payload recording-session check)", numerator=app["safe"],
                                         denominator=app["functions"], value=app["safe"], unit="count", status="measured",
                                         file=E4_REPORT, fields={"value": f"apps.{key}.safe", "denominator": f"apps.{key}.functions"}))
    functions = sum(a["functions"] for a in e4["apps"].values())
    e4["totals"] = {"functions": functions, "safe": sum(a["safe"] for a in e4["apps"].values()),
                    "unsafe": sum(a["unsafe"] for a in e4["apps"].values()),
                    "hostile_cases": sum(a["hostile_cases"] for a in e4["apps"].values()),
                    "benign_calls": functions * len(benign), "rejected": sum(a["rejected"] for a in e4["apps"].values())}
    e4["limitations"] = [
        "A bounded check with a fixed payload set; it is not a proof of general injection safety.",
        "Recording session: SQL is compiled for the MySQL dialect, never executed on a database.",
        "Only the 12 listed payloads and 4 benign values; not exhaustive.",
        "Functions whose SQL takes no user value pass trivially; Clinic Q013 rejects every benign value too, so its "
        "same-SQL comparison is vacuous and it passes by rejection only.",
        "Unresolved sites have no generated code and are not tested.",
        "Runtime hostile testing on MySQL: HMS through the E1 harness; Clinic has one MySQL test of an injection attempt "
        "(test_q002_sql_injection_is_blocked) in its separate equivalence suite, and the FR6 join-rule tests bind one "
        "hostile value in a comma-join function (direct SQL vs generated function, Clinic schema).",
    ]
    measurements.append(_measurement("all", "current-code", E4_LABEL, "e4", "e4_benign_calls",
                                     "E4 benign calls", value=e4["totals"]["benign_calls"], unit="count", status="derived",
                                     file=f"{E4_REPORT}; {E4_SCRIPT}", derivation="functions x len(BENIGN) in the script"))

    tests = {"runs": [], "note": "Only saved (committed) logs are shown; a run without a saved log is not evidence. Counts are "
                                 "copied from each log's pytest summary line; pytest omits zero counts, so an absent count is 0. "
                                 "Skipped tests in the non-MySQL runs are the tests that need MySQL. The full suite with MySQL is the same "
                                 "test tree with MySQL available, so it already contains the Clinic MySQL verification tests."}
    for key, title, prefix, file, fixed_label in TEST_LOGS:
        log = _pytest_log(file)
        stage_label = fixed_label or f"saved log ({log['date']}, HEAD {log['head'][:7]})"
        tests["runs"].append({"key": key, "title": title, "file": file, "stage_label": stage_label, "metric_prefix": prefix,
                              "summary_line": log["summary_line"], "date": log["date"], "head": log["head"], "head_short": log["head"][:7],
                              "mysql_server": log["mysql_server"], "latest": fixed_label is None,
                              **{k: log[k] for k in ("passed", "failed", "skipped", "warnings")}})
        suite = "Full suite with MySQL" if prefix == "tests_with_mysql" else "Non-MySQL tests"
        for metric in ("passed", "failed", "skipped"):
            measurements.append(_measurement("all", "current-code", stage_label, "tests", f"{prefix}_{metric}",
                                             f"{suite} {metric}", value=log[metric], unit="count", status="measured",
                                             file=file, fields={"value": f"summary line '{log['summary_line']}'"},
                                             note="pytest omits zero counts; absent means 0." if metric not in log["present"] else None))
    return e1, e2, e3, e4, tests, e2_dates, e3_dates, e3_runs


def _pytest_log(file):
    """Summary counts, date, HEAD and MySQL version from a saved pytest log (its own header lines)."""
    text = (ROOT / file).read_text(encoding="utf-8-sig")
    line = [l.strip() for l in text.splitlines() if re.match(r"^=*\s*\d+ passed", l)][-1].strip("= ")
    counts = {("warnings" if k.startswith("warning") else k): int(v)
              for v, k in re.findall(r"(\d+) (passed|failed|skipped|warnings?)", line)}
    date = re.search(r"^# date(?: \(start\))?: (\d{4}-\d{2}-\d{2})", text, re.M)
    server = re.search(r"^# MySQL server: (\S+)", text, re.M)
    return {"text": text, "summary_line": line, "present": set(counts), "date": date.group(1) if date else None,
            "head": re.search(r"^# git HEAD: (\w+)", text, re.M).group(1), "mysql_server": server.group(1) if server else None,
            **{k: counts.get(k, 0) for k in ("passed", "failed", "skipped", "warnings")}}


def _before_after(base, e4, e1):
    """Before / After per app from the current-code run: counts for every app, SQL and table detail only for
    BEFORE_AFTER_DETAIL (never for SENSITIVE_APPS). Built by components/c3_data_layer/dashboard/before_after.py."""
    if set(BEFORE_AFTER_DETAIL) & SENSITIVE_APPS:
        raise SystemExit("BEFORE_AFTER_DETAIL must not include an app without a licence")
    sys.dont_write_bytecode = True  # no dashboard/__pycache__ in the repository
    sys.path.insert(0, str(COMPONENT / "dashboard"))
    import before_after
    apps = {}
    for _, folder, php, _, _ in CURRENT_RUNS:
        key = EXPERIMENT_KEY.get(folder, folder)
        e4_app = e4["apps"][folder]
        e4_item = before_after._item(
            "e4", "E4 injection check (functions SAFE)", e4_app["safe"], e4_app["functions"], E4_REPORT,
            field=f"apps.{key}.safe (of apps.{key}.functions)",
            note=f"Bounded check ({e4['payload_count']} fixed payloads, SQL compiled for MySQL, no database); "
                 "not a proof of general injection safety.")
        detail = folder in BEFORE_AFTER_DETAIL
        payload = before_after.build(base / folder, APPS / php, detail=detail, source_prefix=f"{TEMP}/{folder}/", e4=e4_item,
                                     include_reason_text=folder not in SENSITIVE_APPS)
        payload["licence"] = APP_INFO[folder][3]
        payload["role"] = APP_INFO[folder][1]
        payload["verified_ground_truth"] = APP_INFO[folder][2] is not None
        if folder in BEFORE_AFTER_APP_NOTES:
            payload["explanations"].append(BEFORE_AFTER_APP_NOTES[folder])
        # Verification evidence for THIS app only (never borrowed from another app).
        final_e1 = next((r for r in e1["runs"] if r["kind"] == "final" and r["app"] == folder), None)
        suite = next((s for s in e1.get("separate_suites", []) if s["app"] == folder), None)
        payload["verification"] = {
            "e4": {"safe": e4_app["safe"], "functions": e4_app["functions"], "payload_count": e4["payload_count"],
                   "source": E4_REPORT, "note": e4_item["note"]},
            "e1": {"verdicts": final_e1["verdicts"], "sites": final_e1["sites"], "source": final_e1["file"],
                   "note": "E1 harness on MySQL, per SQL site; INCONCLUSIVE is not PASS."} if final_e1 else None,
            "mysql_suite": {"passed": suite["passed"], "collected": suite["collected"], "source": suite["file"],
                            "note": suite["note"]} if suite else None,
        }
        apps[folder] = payload
    labels = ", ".join(APP_INFO[f][0] for f in BEFORE_AFTER_DETAIL)
    coverage = [{"app": f, "label": APP_INFO[f][0], "detail": apps[f]["detail"],
                 **{k: apps[f]["facts"][k] for k in ("detected", "converted", "unresolved_recovery", "unresolved_conversion",
                                                      "templates")}} for f in apps]
    return {"note": before_after.NOTE, "coverage_note": before_after.COVERAGE_NOTE, "coverage": coverage,
            "coverage_source": f"{TEMP}/<app>/summary.json (total_queries, converted_queries, unresolved_recovery_stage, "
                               "unresolved_conversion_stage, converted_by_query_specific_template)",
            "detail_apps": list(BEFORE_AFTER_DETAIL),
            "detail_policy": f"SQL, PHP site and table detail is shown for {labels} only (MIT or project-authored source). "
                             "Other applications show counts only; HMS (kishan0725) has no licence, so none of its SQL or "
                             "source text is exported.",
            "apps": apps}


def _baseline(measurements):
    """The sqlacodegen schema-only baseline: model-file comparison counts, summed over the apps in its summary.json."""
    data = json.loads((ROOT / BASELINE_SUMMARY).read_text(encoding="utf-8"))
    compared = {app: entry for app, entry in data["apps"].items() if entry.get("status") == "compared"}
    date = Path(BASELINE_SUMMARY).parent.name
    stage_label = f"current code (sqlacodegen baseline, {date})"
    scope = ("Schema-only comparison of generated model files: sqlacodegen reflects the database and writes models; it never "
             "reads PHP and does no query recovery or data-access migration. Neither side is ground truth, and matching "
             "properties are not a general correctness guarantee for the C3 models.")
    totals = {}
    for dimension in BASELINE_DIMENSIONS:
        expression = lambda role: " + ".join(f"apps.{app}.comparison.counts.{dimension}.{role}" for app in compared)
        counts = {role: sum(e["comparison"]["counts"][dimension][role] for e in compared.values())
                  for role in ("compared", "matches", "differences")}
        totals[dimension] = counts
        measurements.append(_measurement(
            "all", "current-code", stage_label, "baseline", f"sqlacodegen_{dimension}",
            f"sqlacodegen baseline: {dimension.replace('_', ' ')} matching the C3 models", numerator=counts["matches"],
            denominator=counts["compared"], value=counts["matches"], unit="count", status="measured", file=BASELINE_SUMMARY,
            fields={"numerator": expression("matches"), "denominator": expression("compared"), "value": expression("matches")},
            note=scope))
    fk_apps = [app for app, e in compared.items() if e["comparison"]["counts"]["foreign_keys"]["compared"]]
    measurements.append(_measurement(
        "all", "current-code", stage_label, "baseline", "sqlacodegen_apps_compared",
        "sqlacodegen baseline: applications compared", numerator=len(compared), denominator=len(data["apps"]),
        value=len(compared), unit="count", status="derived", file=BASELINE_SUMMARY,
        derivation="apps with status == \"compared\"; sqlacodegen failures are listed in failures", note=scope))
    return {
        "file": BASELINE_SUMMARY, "readme": BASELINE_README, "date": date, "stage_label": stage_label,
        "git_head": data["generated"]["git_head"], "tool": data["tools"]["sqlacodegen"],
        "apps_total": len(data["apps"]), "apps_compared": len(compared), "failures": sorted(data["failures"]),
        "totals": totals, "foreign_key_apps": fk_apps,
        "apps": {app: {"verdict": e["comparison"]["verdict"], "notes": e.get("notes", []),
                       **{d: e["comparison"]["counts"][d]["compared"] for d in ("tables", "columns", "foreign_keys")}}
                 for app, e in compared.items()},
        "notes": [scope,
                  "Primary-key matches include tables where neither tool finds a primary key.",
                  "Foreign keys can only be compared where a schema declares them; the other applications declare none.",
                  "The C3 query-level counts (live sites, recovered sites, functions, manual review, abstentions) are "
                  "not applicable to sqlacodegen; no comparison of accuracy or speed is made."],
    }


def _reports():
    entries = [
        ("C3 results summary (generated)", "docs/C3_results_summary.md"),
        ("HMS viva evidence", "docs/HMS_viva_evidence.md"),
        ("C3 audit, 17 Oct 2026", "docs/C3_audit_2026-10-17.md"),
        ("Evidence register", "evidence/evidence_register.md"),
        ("HMS S15 evaluation summary", "evidence/evaluation/hms_kishan0725/summary.md"),
        ("Dignity-Dev S15 evaluation summary (fresh holdout)", "evidence/evaluation/dignity_dev_hms/summary.md"),
        ("NEWS-BUZZ S15 evaluation summary", "evidence/evaluation/news_buzz/summary.md"),
        ("Fee-Rainbow S15 evaluation summary", "evidence/evaluation/fee_rainbow/summary.md"),
        ("HMS E1 triage", "evidence/hms/E1_triage/triage.md"),
        ("E4 injection check, 17 Oct 2026", "evidence/e4/2026-10-17/README.md"),
        ("FR6 join sprint evidence, 20 Oct 2026", "evidence/final/2026-10-20/README.md"),
        *[(f"{title} ({file.split('/')[-1]})", file) for _, title, _, file, _ in TEST_LOGS],
        ("Clinic MySQL verification suite (saved log)", CLINIC_MYSQL_LOG),
        *[(f"E3 Alembic round trip on MySQL, {label.split(', ')[-1].rstrip(')')}", f"{directory}/summary.txt")
          for _, label, directory in E3_RUNS],
        ("sqlacodegen schema-only baseline (method, version, commands)", BASELINE_README),
        ("Commit hash map (history rewrite)", "evidence/commit_hash_map_2026-10-15.md"),
        ("FR status (reviewed, drives the Functions view)", "docs/c3_fr_status.json"),
        ("Execution time, 18 Oct 2026 (method and limits)", "evidence/performance/2026-10-18/README.md"),
    ]
    return [{"title": t, "path": p, "exists": (ROOT / p).is_file(), "downloadable": False,
             "note": "Repository file; open it in the repository (not served by the dashboard)."} for t, p in entries] + [
        {"title": "Dashboard data (this file)", "path": "components/c3_data_layer/dashboard/data/dashboard_data.json",
         "exists": True, "downloadable": True, "href": "data/dashboard_data.json", "note": "Served with the dashboard."}]


FR_STATUS_FILE = ROOT / "docs" / "c3_fr_status.json"
ID_TOKEN = re.compile(r"\b(?:FR|Q|G|S|E|H)\d+\b")  # identifiers such as FR6, Q014, G007, E1, H1 are not results


def _functions(measurements, experiments, limitations):
    """docs/c3_fr_status.json, checked against the audit and the generated measurements."""
    spec = json.loads(FR_STATUS_FILE.read_text(encoding="utf-8"))
    audit = (ROOT / spec["requirements_source"]).read_text(encoding="utf-8")
    ids = {m["id"] for m in measurements}
    limitation_frs = {l["fr"] for l in limitations}
    for entry in spec["functions"]:
        where = f"{FR_STATUS_FILE.relative_to(ROOT)} {entry['fr']}"
        if entry["status"] not in spec["statuses"]:
            raise SystemExit(f"{where}: unknown status {entry['status']!r}")
        if f'"{entry["requirement"]}"' not in audit:
            raise SystemExit(f"{where}: requirement text is not verbatim in {spec['requirements_source']}")
        for field in ("title", "requirement", "limitation"):
            if re.search(r"\d", ID_TOKEN.sub("", entry[field])):
                raise SystemExit(f"{where}: {field} contains a number; result numbers must come from measurements")
        unknown = [m for m in entry["measurements"] if m not in ids]
        if unknown:
            raise SystemExit(f"{where}: unknown measurement id(s): {unknown}")
        missing = [k for k in entry["experiments"] if k not in experiments]
        if missing:
            raise SystemExit(f"{where}: unknown experiment key(s): {missing}")
        if entry.get("site_list") and entry["site_list"] not in limitation_frs:
            raise SystemExit(f"{where}: site_list {entry['site_list']!r} is not a limitations entry")
    return {"source": FR_STATUS_FILE.relative_to(ROOT).as_posix(), "requirements_source": spec["requirements_source"],
            "statuses": spec["statuses"], "items": spec["functions"]}


def _pp1(base, data):
    """PP1 views (FR detail, SQL support matrix, failure explorer, register, definitions, inventory).
    Built by components/c3_data_layer/dashboard/pp1_content.py from docs/c3_pp1_dashboard_content.json."""
    sys.dont_write_bytecode = True  # no dashboard/__pycache__ in the repository
    sys.path.insert(0, str(COMPONENT / "dashboard"))
    import pp1_content
    return pp1_content.build(sys.modules[__name__], base, data)


def _privacy_check(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            _privacy_check(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _privacy_check(value, f"{path}[{i}]")
    elif isinstance(node, str):
        # SQL text is allowed only in the Before / After SQL fields of the publishable apps; local paths, database
        # URLs and literal passwords are never allowed.
        patterns = LOCAL_TEXT + [LITERAL_PASSWORD] if BEFORE_AFTER_SQL_FIELD.match(path) else FORBIDDEN_TEXT
        for pattern in patterns:
            if pattern.search(node):
                raise SystemExit(f"privacy check failed at {path}: matches {pattern.pattern!r}")


def write_dashboard_json(target: Path, base: Path, head: str) -> None:
    target = target if target.is_absolute() else (Path.cwd() / target)
    target = target.resolve()
    dashboard_dir = (COMPONENT / "dashboard").resolve()
    if dashboard_dir not in target.parents:
        raise SystemExit(f"--dashboard-json must be inside {dashboard_dir.relative_to(ROOT)}")
    pipeline_unchanged_since(head)
    measurements, stage_sites = [], {}
    _historical(measurements, stage_sites)
    apps, records, artifacts, joins = _current(base, measurements)
    manual_review = compute_manual_review(base)
    for folder, review in manual_review.items():
        measurements.append(_measurement(
            folder, "current-code", "current code", "review", "manual_review_rate",
            "Manual-review rate (live sites not converted / live sites)", numerator=review["not_converted_live"],
            denominator=review["live_sites"], value=review["value"], unit="percent", status="derived",
            file=f"{TEMP}/{folder}/unresolved_queries.json; {TEMP}/{folder}/summary.json"
                 + (f"; {review['live_sites_source'].split(' ')[0]}" if review["verified_ground_truth"] else ""),
            derivation=MANUAL_REVIEW_FORMULA + f" Live sites: {review['live_sites_source']}.",
            note=("Proxy: counts sites, not developer hours. It is not a measurement of developer effort."
                  + ("" if review["verified_ground_truth"] else f" {NOT_VERIFIED.capitalize()}."))))
    performance = load_performance()
    for folder, timing in performance["apps"].items():
        runs = ", ".join(f"{s:.3f}" for s in timing["runs_seconds"])
        measurements.append(_measurement(
            folder, "current-code", "current code (timing, 2026-10-18)", "performance", "execution_time",
            "End-to-end CLI analysis time (median of 3 runs)", value=timing["median_seconds"], unit="seconds",
            status="measured", file=PERFORMANCE_FILE, fields={"value": f"apps.{folder}.median_seconds"},
            note=f"{performance['label']}. Runs: {runs} s. One machine ({performance['machine']['cpu']}); not a benchmark of other hardware."))
    disclosures = _disclosures(records, measurements)
    e1, e2, e3, e4, tests, e2_dates, e3_dates, e3_runs = _experiments(measurements)
    baseline = _baseline(measurements)
    before_after = _before_after(base, e4, e1)
    for metric, label, note in [
        ("migration_effort", "Measured migration effort (developer time)",
         "Not measured. The manual-review rate is a derived proxy (sites), not developer effort."),
    ]:
        measurements.append(_not_measured("all", "not_measured", metric, label, note))
    data = {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "generated": {"by": "scripts/summarize_evidence.py --dashboard-json", "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                      "git_head": head, "pipeline_unchanged_since_head": True,
                      "current_code_run": "fresh C3 run of all 9 apps into a temp folder outside the repo (written <temp>)"},
        "stage_definitions": STAGE_DEFINITIONS,
        "apps": apps,
        "measurements": measurements,
        "stage_sites": stage_sites,
        "records": records,
        "artifacts": artifacts,
        "experiments": {"e1": e1, "e2": e2, "e2_dates": e2_dates, "e3": e3, "e3_dates": e3_dates, "e3_runs": e3_runs, "e4": e4,
                        "tests": tests, "performance": performance, "baseline": baseline},
        "manual_review": {"formula": MANUAL_REVIEW_FORMULA, "limits": MANUAL_REVIEW_LIMITS, "apps": manual_review},
        "before_after": before_after,
        "disclosures": disclosures,
        "limitations": [
            {"fr": "FR2", "title": "Branch-dependent SQL",
             "text": "SQL whose text differs between if/else branches is not rebuilt by a general rule. SQL built and executed in "
                     "the same conditional block is recovered (S13); otherwise C3 abstains with BRANCHING. Only the Clinic "
                     "templates Q014 and Q016 rebuild branch-dependent SQL, for those exact files.",
             "evidence": "components/c3_data_layer/src/c3/conversion/query_converter.py:220-232, :274-286"},
            {"fr": "FR4", "title": "Column use stops at the SQL statement",
             "text": "Tables and columns read, written and filtered are identified per SQL operation and measured against the "
                     "ground truth. Use of result columns in later PHP application logic is not traced; that part of FR4 is "
                     "not implemented.",
             "evidence": "components/c3_data_layer/src/c3/analysis/column_analyzer.py:256-414"},
            {"fr": "FR6", "title": "Unsupported join shapes",
             "text": "The converter accepts static INNER JOIN SELECTs, and LEFT JOIN or implicit comma-join SELECTs only under a "
                     "result-shape rule: every table is in the parsed schema, every output column is explicit and distinctly "
                     "named, and every column resolves to exactly one joined table. RIGHT, FULL, CROSS and NATURAL joins, "
                     "USING, subqueries, derived tables, unions and UPDATE/DELETE joins are not converted. Sites that fail "
                     "stay unconverted and are flagged with CONVERSION_UNSUPPORTED and a reason naming the failed condition.",
             "evidence": "unresolved_queries.json of the current-code run", "sites": joins},
        ],
        "reports": _reports(),
    }
    data["functions"] = _functions(measurements, data["experiments"], data["limitations"])
    data["pp1"] = _pp1(base, data)
    _privacy_check(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(measurements)} measurements, "
          f"{sum(len(r) for r in records.values())} query records)")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Generate the C3 results summary (and, opt-in, the dashboard data).")
    parser.add_argument("--dashboard-json", type=Path, help="also write the dashboard data file to this path")
    parser.add_argument("--no-markdown", action="store_true", help=f"do not write {OUT.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    if args.no_markdown and not args.dashboard_json:
        parser.error("--no-markdown needs --dashboard-json (otherwise nothing is written)")

    head = git("rev-parse", "HEAD")
    pipeline_unchanged_since(head)
    base = run_current_code()
    if not args.no_markdown:
        write_markdown(base, head)
    if args.dashboard_json:
        write_dashboard_json(args.dashboard_json, base, head)


def manual_review_section(review: dict, sources: list) -> list[str]:
    lines = ["", "## Manual-review rate (current code)", "",
             f"- **Formula:** {MANUAL_REVIEW_FORMULA}",
             f"- **What it does not measure:** {MANUAL_REVIEW_LIMITS}",
             f"- **Apps without author-verified ground truth** (Clinic, simple_crud, mixed_crud, hard_crud): every detected site is "
             f"treated as live; {NOT_VERIFIED}.", "",
             "| App | Live sites | Not converted at recovery | Not converted at conversion | Not converted (total) | Rate | "
             "Ambiguous sites (included) | Non-live sites excluded | Note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for folder, r in review.items():
        ambiguous = ", ".join(f"{a['gt_id']} ({'converted' if a['converted'] else 'not converted'})" for a in r["ambiguous"]) or "none"
        excluded = ", ".join(f"{e['query_id']} `{e['site']}`" for e in r["excluded_non_live"]) or "none"
        lines.append(f"| {r['app']} | {r['live_sites']} | {r['unresolved_recovery_live']} | {r['unresolved_conversion_live']} | "
                     f"{r['not_converted_live']} | {r['value']}% | {ambiguous} | {excluded} | {r['label'] or ''} |")
        summary_path = f"{TEMP}/{folder}/summary.json"
        sources.append((r["app"], "current code", "manual-review numerator", str(r["not_converted_live"]),
                        f"{TEMP}/{folder}/unresolved_queries.json", "live entries (stage recovery or conversion), each site once"))
        sources.append((r["app"], "current code", "manual-review denominator (live sites)", str(r["live_sites"]),
                        r["live_sites_source"].split(" ")[0] if r["verified_ground_truth"] else summary_path,
                        "gt_live_sites" if r["verified_ground_truth"] else "total_queries"))
    lines += ["", "Sites counted (query ID / ground-truth ID, file:line, stage, reason code):", ""]
    for folder, r in review.items():
        listed = "; ".join(f"{s['query_id']}/{s['gt_id'] or '-'} `{s['site']}` {s['stage']} {s['reason_code']}" for s in r["sites"])
        lines.append(f"- **{r['app']}** ({r['not_converted_live']}): {listed or 'none'}")
    return lines


def execution_time_section(sources: list) -> list[str]:
    perf = load_performance()
    m = perf["machine"]
    lines = ["", "## Execution time (current code)", "",
             f"- **What:** {perf['label']}.",
             f"- **Machine:** {m['os']}; {m['cpu']}, {m['physical_cores']} cores / {m['logical_processors']} logical processors; "
             f"{m['ram_gb']} GB RAM; Python {m['python']}; PHP {m['php']}.",
             f"- **Method:** {perf['rounds']} runs per app, {perf['order']}; code `{perf['git_head']}`. Limits and the full "
             f"method: `{perf['readme']}`. One machine; not a benchmark of other hardware.", "",
             "| App | Sites detected | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) |", "|---|---|---|---|---|---|"]
    for folder, t in perf["apps"].items():
        r = t["runs_seconds"]
        label = APP_INFO[folder][0]
        lines.append(f"| {label} | {t['sites_detected']} | {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {t['median_seconds']:.3f} |")
        sources.append((label, "current code (timing)", "median execution time (s)", f"{t['median_seconds']:.3f}",
                        PERFORMANCE_FILE, f"apps.{folder}.median_seconds"))
    return lines


def write_markdown(base, head) -> None:
    sources, missing, missing_converted = [], [], []
    rows, used_files = historical_rows(sources, missing_converted)

    lines = [
        "# C3 results summary (generated)",
        "",
        "Generated by `scripts/summarize_evidence.py` from the S15 evaluation files in `evidence/evaluation/` and a fresh "
        "current-code run (section below). Do not edit by hand; re-run the script. Every number is sourced in the table at the end. "
        f'A value no file records is shown as "{NOT_MEASURED}", never as 0.',
        "",
        "## Results per app and stage",
        "",
        "| App | Stage | " + " | ".join(COLUMNS) + " |",
        "|---|---|" + "---|" * len(COLUMNS),
    ]
    for app, stage, cells in rows:
        lines.append(f"| {app} | {stage} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "- **Row check:** in every row, recovered correct + missed + wrong + correct abstentions + not detected + ambiguous = live sites. "
        "The script asserts this.",
        "- **Precision and recall** are over sites labelled recoverable or non-recoverable; ambiguous sites are excluded.",
        "- **Correct abstentions** are non-recoverable sites that C3 detected and left unresolved. **Not detected (TN, not counted)** "
        "are non-recoverable sites C3 never detected. The evaluator scores them as TN, but they are not counted here as abstentions.",
        "- **Converted** is not recorded in the evaluation files, so historical stages show it as not measured. "
        "For the current code, see the next section.",
        "- **Labels:** Dignity-Dev is the only fresh holdout. All other post-* stages are post-adaptation.",
    ]

    current_lines, head, date = current_code_section(base, head, sources)
    lines += current_lines
    lines += manual_review_section(compute_manual_review(base), sources)
    lines += execution_time_section(sources)

    # ------------------------------------------------------------- limits
    lines += ["", "## Limits (measured)", ""]

    fr = load("fee_rainbow/evaluation_wrapper_support.json") if (EVAL / "fee_rainbow/evaluation_wrapper_support.json").exists() else None
    if fr:
        tables = sorted({p.split()[1] for s in fr["schema_mismatch"]["sites"] for p in s["problems"] if p.startswith("table ")})
        n_sites = len(fr["schema_mismatch"]["sites"])
        lines.append(f"- **Fee-Rainbow, tables missing from the supplied schema ({len(tables)}):** {', '.join('`' + t + '`' for t in tables)}. "
                     f"They are used by {n_sites} schema-mismatch sites; column use on those sites is reported separately.")
        sources.append(("Fee-Rainbow", "post-fix (wrapper support + fragment rule)", "missing-schema tables", str(len(tables)),
                        "evidence/evaluation/fee_rainbow/evaluation_wrapper_support.json", 'schema_mismatch.sites[*].problems ("table X not in schema")'))
        sources.append(("Fee-Rainbow", "post-fix (wrapper support + fragment rule)", "schema-mismatch sites", str(n_sites),
                        "evidence/evaluation/fee_rainbow/evaluation_wrapper_support.json", "len(schema_mismatch.sites)"))
        amb = [f"{a['gt_id']} `{a['site']}`" for a in fr["ambiguous_sites"]]
        lines.append(f"- **Fee-Rainbow, ambiguous sites excluded from precision/recall ({len(amb)}):** {', '.join(amb)}.")
        sources.append(("Fee-Rainbow", "post-fix (wrapper support + fragment rule)", "ambiguous sites excluded", str(len(amb)),
                        "evidence/evaluation/fee_rainbow/evaluation_wrapper_support.json", "ambiguous_sites[*]"))
        helpers = [d for d in fr["details"] if d["site"].endswith("helpers.php:4")]
        lines.append("- **Fee-Rainbow helper executor lines:** "
                     + "; ".join(f"{d['gt_id']} `{d['site']}`: outcome {d['outcome']}, C3 status {d['c3_status']}" for d in helpers)
                     + ". Both are counted as correct abstentions in the table above.")
        sources.append(("Fee-Rainbow", "post-fix (wrapper support + fragment rule)", "helper lines G021/G022 outcome",
                        ", ".join(d["outcome"] for d in helpers), "evidence/evaluation/fee_rainbow/evaluation_wrapper_support.json",
                        'details[] where site ends with "helpers.php:4": outcome, c3_status'))

    hms = load("hms_kishan0725/evaluation.json")
    if hms["ambiguous_sites"]:
        amb = [f"{a['gt_id']} `{a['site']}`" for a in hms["ambiguous_sites"]]
        lines.append(f"- **HMS, ambiguous sites excluded ({len(amb)}):** {', '.join(amb)}.")
        sources.append(("HMS (kishan0725)", "post-adaptation (after H1)", "ambiguous sites excluded", str(len(amb)),
                        "evidence/evaluation/hms_kishan0725/evaluation.json", "ambiguous_sites[*]"))

    lines.append("- **Join forms not converted:** LEFT JOIN and comma-join SELECTs are converted only under the result-shape "
                 "rule (FR6 join rule, 2026-10-20); the remaining conversion-stage rejections for the current code are listed "
                 "with IDs, file:line and reason in \"Unresolved at conversion stage (current code)\" above. The historical "
                 "evaluation files do not record them.")
    lines.append("- **Ground truth:** drafted blind by an LLM and verified by one human (the author). The drafting model "
                 "is from the same model family as the assistant used during development, so shared blind spots are possible.")

    # ---------------------------------------------------- disclosed findings
    lines += ["", "## Fix history (disclosed findings)", ""]
    nb_before, nb_after = load("news_buzz/evaluation.json"), load("news_buzz/evaluation_post_escape_fix.json")
    g3_before = next(d for d in nb_before["details"] if d["gt_id"] == "G003")["column_checks"]
    g3_after = next(d for d in nb_after["details"] if d["gt_id"] == "G003")["column_checks"]

    def passed(checks):
        return f"{sum(1 for v in checks.values() if v)}/{len(checks)}"
    failed = ", ".join(k for k, v in g3_before.items() if not v)
    lines.append(f"- **Escape decoder (NEWS-BUZZ G003):** column checks passed {passed(g3_before)} before the fix "
                 f"(failed: {failed}) and {passed(g3_after)} after.")
    sources.append(("NEWS-BUZZ", "post-adaptation / post-fix", "G003 column checks passed",
                    f"{passed(g3_before)} -> {passed(g3_after)}",
                    "evidence/evaluation/news_buzz/evaluation.json; evaluation_post_escape_fix.json", 'details[gt_id == "G003"].column_checks'))
    if fr:
        text = FEE_RAINBOW_TEXT.read_text(encoding="utf-8")
        absent = [p for p in FEE_RAINBOW_PHRASES if p not in text]
        if absent:
            raise SystemExit(f"{FEE_RAINBOW_TEXT} no longer contains: {absent}")
        lines.append(f"- **Wrapper support and the SQL-fragment rule (Fee-Rainbow):** the final measurement is in the table above "
                     f"(wrong recoveries = {fr['recovery']['FP']}). The first wrapper measurement (10 wrong, 80.0%) and the "
                     "first correction (6 of 10) are in `evidence/evaluation/fee_rainbow/summary.md` "
                     "(Disclosed findings 1 and 3). *Documented in text only, not in JSON.*")
        sources.append(("Fee-Rainbow", "first measurement / first correction", "10 wrong, 80.0%; 6 of 10",
                        "text only", "evidence/evaluation/fee_rainbow/summary.md",
                        "not JSON: the phrases \"" + "\" and \"".join(FEE_RAINBOW_PHRASES) + "\" are checked to exist"))
    lines.append("- **E1 harness classification bug (HMS):** disclosed in `evidence/hms/E1_triage/triage.md`. E1 results are not "
                 "S15 evaluation files.")
    missing.append("HMS E1 harness bug numbers (FAILs reclassified): in evidence/mysql_runs and E1 triage, not in evidence/evaluation")
    missing.append("Fee-Rainbow first wrapper measurement and first correction: documented in text only (summary.md), not in JSON")

    # ---------------------------------------------------------- sourcing
    lines += ["", "## Sourcing (file and JSON field of every number)", "",
              "| App | Stage | Value | Number | File | Field |", "|---|---|---|---|---|---|"]
    for app, stage, column, text, path, field in sources:
        lines.append(f"| {app} | {stage} | {column} | {text} | `{path}` | `{field}` |")

    lines += ["", "## Missing (could not be sourced from a JSON file)", ""]
    if missing_converted:
        lines.append("- Converted count for every historical stage (" + "; ".join(missing_converted)
                     + "): no evaluation file records conversion")
    lines += [f"- {m}" for m in dict.fromkeys(missing)]
    lines += ["", "Evaluation files read: " + ", ".join(f"`evidence/evaluation/{f}`" for f in used_files),
              "", f"Current-code run: HEAD `{head}`, {date}, folder `{TEMP}`.", ""]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(rows)} stage rows, {len(CURRENT_RUNS)} current runs, {len(sources)} sourced numbers)")


if __name__ == "__main__":
    sys.exit(main())
