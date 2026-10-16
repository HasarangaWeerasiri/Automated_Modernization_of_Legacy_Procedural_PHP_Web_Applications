"""Generate docs/C3_results_summary.md.

Two sources, nothing else:
  1. Historical stages: the S15 evaluation JSON files in evidence/evaluation/<app>/.
  2. Current code: a fresh C3 run on all 9 apps, written to a NEW temp folder
     outside the repo (never output/), read from each app's summary.json and
     unresolved_queries.json.

Every number in the output is copied from, or counted from, a JSON field; the
sourcing table names the file and field of each number. A value no file
records is written as "not measured" and listed as missing. Every stage row is
checked to sum to its live sites; the script fails loudly otherwise.

Usage (repo root):
    python scripts/summarize_evidence.py
"""

import datetime
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

    # Clinic: general rules + query-specific templates.
    summary = runs["clinic_inventory_legacy_hard"][0]
    general, template = summary["converted_by_general_rules"], summary["converted_by_query_specific_template"]
    if general + template != summary["converted_queries"]:
        raise SystemExit("Clinic: converted_by_general_rules + converted_by_query_specific_template != converted_queries")
    notes.append(f"- **Clinic:** converted {summary['converted_queries']} = general-rule {general} + query-specific templates {template}.")
    sources.append(("Clinic", "current code", "converted by general rules", str(general),
                    f"{TEMP}/clinic_inventory_legacy_hard/summary.json", "converted_by_general_rules"))
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


def main() -> None:
    sources, missing, missing_converted = [], [], []
    rows, used_files = historical_rows(sources, missing_converted)
    head = git("rev-parse", "HEAD")
    pipeline_unchanged_since(head)
    base = run_current_code()

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
