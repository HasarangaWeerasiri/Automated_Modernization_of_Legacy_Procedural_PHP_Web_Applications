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
        stage_label = stage + (" — recovery only" if recovery_only else "")
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
E3_DIR = "evidence/mysql_runs/2026-10-15_173218/e3"
E4_REPORT = "evidence/e4/2026-10-17/injection_report.json"
E4_SCRIPT = "scripts/e4_injection_check.py"
TEST_LOG = "evidence/tests/test_run_non_mysql_2026-10-17.txt"

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
    "current-code": "Fresh run of the current pipeline (recorded HEAD) by this script; conversion counts come from here.",
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
FORBIDDEN_TEXT = [  # privacy guard over every exported string
    re.compile(r"\bSELECT\b[^.]{0,200}\bFROM\b"), re.compile(r"\bINSERT\s+INTO\b"), re.compile(r"\bUPDATE\s+\w+\s+SET\b"),
    re.compile(r"\bDELETE\s+FROM\b"), re.compile(r"mysql\+\w+://"), re.compile(r"password\s*[=:]", re.I),
    re.compile(r"[A-Za-z]:[\\/]"), re.compile(r"AppData|/Users/|\\Users\\"),
]


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
        label = stage_label + (" — recovery only" if recovery_only else "")
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


def _current(base, measurements):
    apps, records, artifacts, joins = {}, {}, {}, []
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
        measurements.append(_measurement(
            folder, "current-code", "current code", "review", "manual_review_rate",
            "Manual-review rate (unresolved / detected)", numerator=summary["unresolved_total"],
            denominator=summary["total_queries"], value=_pct(summary["unresolved_total"], summary["total_queries"]),
            unit="percent", status="derived", file=file, derivation="unresolved_total / total_queries",
            note="Share of detected sites flagged for human review. It is not a measurement of developer effort."))
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
    for folder in ("dignity_dev_hms", "helpdesk_legacy_holdout", "news_buzz", "fee_rainbow", "clinic_inventory_legacy_hard",
                   "simple_crud", "mixed_crud", "hard_crud"):
        measurements.append(_not_measured(folder, "e1", "e1_equivalence", "E1 behavioural equivalence on MySQL",
                                          "No E1 run. Successful conversion is not behavioural equivalence."))

    e2_offline = json.loads((ROOT / E2_OFFLINE).read_text(encoding="utf-8"))
    e2_live = json.loads((ROOT / E2_LIVE).read_text(encoding="utf-8"))
    e2, e3, e3_dates = {}, {}, {}
    e2_dates = {"offline": e2_offline["date_utc"][:len("yyyy-mm-dd")], "live": e2_live["date_utc"][:len("yyyy-mm-dd")]}
    for folder in APP_INFO:
        key = EXPERIMENT_KEY.get(folder, folder)
        offline, live = e2_offline["apps"][key]["verdicts"], e2_live["apps"][key]["verdicts"]
        e2[folder] = {"A_vs_B": offline["A_vs_B"], "A_vs_C": offline["A_vs_C"], "D_vs_C": live["D_vs_C"],
                      "query_schema_drift": live["query_schema_drift"],
                      "tables": e2_live["apps"][key]["A"]["tables"], "columns": e2_live["apps"][key]["A"]["columns"]}
        e3_data = json.loads((ROOT / E3_DIR / f"{key}.json").read_text(encoding="utf-8"))
        e3[folder] = {"verdict": e3_data["verdict"], "steps": e3_data["steps"], "schema_diffs": len(e3_data["schema_diffs"])}
        e3_dates[folder] = e3_data["date_utc"][:len("yyyy-mm-dd")]
    e2_match = sum(1 for v in e2.values() if v["A_vs_B"] == v["A_vs_C"] == v["D_vs_C"] == "MATCH")
    e3_pass = sum(1 for v in e3.values() if v["verdict"] == "PASS")
    measurements += [
        _measurement("all", "post-adaptation", "post-adaptation (E2, 2026-10-15)", "e2", "e2_schema_match",
                     "E2 schema fidelity: apps where every view matches", numerator=e2_match, denominator=len(e2),
                     value=e2_match, unit="count", status="derived", file=f"{E2_OFFLINE}; {E2_LIVE}",
                     derivation="apps with verdicts A_vs_B, A_vs_C (offline) and D_vs_C (MySQL) all MATCH"),
        _measurement("all", "post-adaptation", "post-adaptation (E3, 2026-10-15)", "e3", "e3_roundtrip_pass",
                     "E3 Alembic round trip on MySQL: apps PASS", numerator=e3_pass, denominator=len(e3), value=e3_pass,
                     unit="count", status="derived", file=f"{E3_DIR}/<app>.json", derivation="count verdict == PASS"),
    ]

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
        measurements.append(_measurement(folder, "current-code", "current code (E4, 2026-10-17)", "e4", "e4_safe_functions",
                                         "E4 functions SAFE (12-payload recording-session check)", numerator=app["safe"],
                                         denominator=app["functions"], value=app["safe"], unit="count", status="measured",
                                         file=E4_REPORT, fields={"value": f"apps.{key}.safe", "denominator": f"apps.{key}.functions"}))
    functions = sum(a["functions"] for a in e4["apps"].values())
    e4["totals"] = {"functions": functions, "safe": sum(a["safe"] for a in e4["apps"].values()),
                    "unsafe": sum(a["unsafe"] for a in e4["apps"].values()),
                    "hostile_cases": sum(a["hostile_cases"] for a in e4["apps"].values()),
                    "benign_calls": functions * len(benign), "rejected": sum(a["rejected"] for a in e4["apps"].values())}
    e4["limitations"] = [
        "Recording session: SQL is compiled for the MySQL dialect, never executed on a database.",
        "Only the 12 listed payloads and 4 benign values; not exhaustive.",
        "Functions whose SQL takes no user value pass trivially; Clinic Q013 rejects every benign value too, so its "
        "same-SQL comparison is vacuous and it passes by rejection only.",
        "Unresolved sites have no generated code and are not tested.",
        "Runtime hostile testing on MySQL exists only for HMS (E1).",
    ]
    measurements.append(_measurement("all", "current-code", "current code (E4, 2026-10-17)", "e4", "e4_benign_calls",
                                     "E4 benign calls", value=e4["totals"]["benign_calls"], unit="count", status="derived",
                                     file=f"{E4_REPORT}; {E4_SCRIPT}", derivation="functions x len(BENIGN) in the script"))

    log = (ROOT / TEST_LOG).read_text(encoding="utf-8")
    line = [l for l in log.splitlines() if re.match(r"^\d+ passed", l)][-1]
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|warnings?)", line)}
    tests = {"file": TEST_LOG, "summary_line": line, "passed": counts.get("passed", 0), "failed": counts.get("failed", 0),
             "skipped": counts.get("skipped", 0), "warnings": counts.get("warning", counts.get("warnings", 0)),
             "head": re.search(r"^# git HEAD: (\w+)", log, re.M).group(1),
             "note": "Non-MySQL suite; the 61 skipped tests need MySQL. An earlier attempt failed only on the Windows "
                     "260-character path limit (disclosed in the log)."}
    for metric in ("passed", "failed", "skipped"):
        measurements.append(_measurement("all", "current-code", "current code (tests, 2026-10-17)", "tests", f"tests_{metric}",
                                         f"Non-MySQL tests {metric}", value=tests[metric], unit="count", status="measured",
                                         file=TEST_LOG, fields={"value": f"summary line '{line}'"},
                                         note="pytest omits zero counts; absent means 0." if metric not in counts else None))
    return e1, e2, e3, e4, tests, e2_dates, e3_dates


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
        ("Non-MySQL test run, 17 Oct 2026", TEST_LOG),
        ("Commit hash map (history rewrite)", "evidence/commit_hash_map_2026-10-15.md"),
    ]
    return [{"title": t, "path": p, "exists": (ROOT / p).is_file(), "downloadable": False,
             "note": "Repository file; open it in the repository (not served by the dashboard)."} for t, p in entries] + [
        {"title": "Dashboard data (this file)", "path": "components/c3_data_layer/dashboard/data/dashboard_data.json",
         "exists": True, "downloadable": True, "href": "data/dashboard_data.json", "note": "Served with the dashboard."}]


def _privacy_check(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            _privacy_check(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _privacy_check(value, f"{path}[{i}]")
    elif isinstance(node, str):
        for pattern in FORBIDDEN_TEXT:
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
    disclosures = _disclosures(records, measurements)
    e1, e2, e3, e4, tests, e2_dates, e3_dates = _experiments(measurements)
    for metric, label, note in [
        ("execution_time", "Pipeline execution time", "Only single wall-clock runs exist in run logs; no systematic timing."),
        ("migration_effort", "Measured migration effort (developer time)", "Not measured. The manual-review rate is derived, not effort."),
        ("sqlacodegen_baseline", "Schema-only baseline (sqlacodegen)", "Planned, not run."),
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
        "experiments": {"e1": e1, "e2": e2, "e2_dates": e2_dates, "e3": e3, "e3_dates": e3_dates, "e4": e4, "tests": tests},
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
             "text": "The converter accepts static INNER JOIN SELECTs only. Recovered sites with other join forms stay unconverted "
                     "and are flagged with CONVERSION_UNSUPPORTED.",
             "evidence": "unresolved_queries.json of the current-code run", "sites": joins},
        ],
        "reports": _reports(),
    }
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

    lines.append("- **Join forms not converted:** the conversion-stage rejections for the current code are listed with IDs, "
                 "file:line and reason in \"Unresolved at conversion stage (current code)\" above. The historical evaluation files do not record them.")
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
