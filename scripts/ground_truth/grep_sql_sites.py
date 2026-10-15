"""Ground-truth candidate sites from an independent grep of the raw PHP.

This script does NOT import or run C3. It scans the original PHP files of an
application with plain regular expressions for SQL execution calls and writes
benchmarks/ground truth/<app>/candidates.csv with blank annotation fields.

Executor patterns (fixed list, chosen from the PHP manual, not from C3):
  functions: mysqli_query, mysqli_real_query, mysqli_multi_query,
             mysqli_prepare, mysqli_stmt_prepare, mysql_query,
             mysql_unbuffered_query, mysql_db_query
  methods:   ->query(, ->real_query(, ->multi_query(, ->prepare(, ->exec(

Function *definitions* (e.g. "function query(") are not sites. Lines that
start inside a comment are listed (grep_note) but are outside the sampling
frame (in_sample=no, stratum=excluded_comment): commented-out code does not
execute.

Sampling: if an app has more than 40 non-comment sites, a stratified sample of at least
30 is marked in_sample=yes. Strata = PHP file. Each file with sites gets
max(1, round(30 * file_sites / total_sites)) sites (capped at its own count);
inside a file, sites are picked with random.Random(SEED) after sorting by
line. All sites are still listed (in_sample=no for the rest), so the sample
can be audited.

Usage (repo root):
    python scripts/ground_truth/grep_sql_sites.py
"""

import csv
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "benchmarks" / "ground truth"
SEED = 20261015
SAMPLE_THRESHOLD = 40
SAMPLE_SIZE = 30

# app -> (raw PHP root, directories excluded as third-party per the manifest)
APPS = {
    "helpdesk": ("benchmarks/apps/helpdesk_legacy_holdout/legacy", []),
    "hms_kishan0725": ("benchmarks/apps/hms_kishan0725", ["TCPDF", "vendor", "ckeditor"]),
    "news_buzz": ("benchmarks/apps/news_buzz", ["GUMP-master"]),
    "fee_rainbow": ("benchmarks/apps/fee_rainbow", []),
    "dignity_dev_hms": ("benchmarks/apps/dignity_dev_hms", []),
}

FUNCTIONS = (
    "mysqli_query", "mysqli_real_query", "mysqli_multi_query", "mysqli_prepare",
    "mysqli_stmt_prepare", "mysql_query", "mysql_unbuffered_query", "mysql_db_query",
)
METHODS = ("query", "real_query", "multi_query", "prepare", "exec")

CALL = re.compile(
    r"(?<![\w$>])(?P<func>" + "|".join(FUNCTIONS) + r")\s*\("
    r"|->\s*(?P<method>" + "|".join(METHODS) + r")\s*\(",
    re.IGNORECASE,
)
DEFINITION = re.compile(r"\bfunction\s+&?\s*(" + "|".join(FUNCTIONS + METHODS) + r")\s*\(", re.IGNORECASE)

ANNOTATION_FIELDS = [
    "expected_recoverable",
    "expected_operation",
    "expected_tables",
    "expected_read_columns",
    "expected_write_columns",
    "expected_filter_columns",
    "annotator_notes",
]
FIELDS = ["site_id", "file", "line", "call", "in_sample", "stratum", "grep_note"] + ANNOTATION_FIELDS


def php_files(root: Path, excluded: list[str]):
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() != ".php" or not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.lower() in {e.lower() for e in excluded} for part in relative.parts[:-1]):
            continue
        yield relative.as_posix(), path


def grep_sites(root: Path, excluded: list[str]) -> list[dict]:
    sites = []
    for relative, path in php_files(root, excluded):
        text = path.read_text(encoding="utf-8", errors="replace")
        in_block_comment = False
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            note = ""
            if in_block_comment or stripped.startswith(("//", "#", "/*", "*")):
                note = "line starts in a comment"
            if "/*" in line and "*/" not in line.split("/*", 1)[1]:
                in_block_comment = True
            if in_block_comment and "*/" in line:
                in_block_comment = "/*" in line.rsplit("*/", 1)[1]
            if DEFINITION.search(line):
                continue
            for match in CALL.finditer(line):
                call = match.group("func") or "->" + match.group("method")
                sites.append({"file": relative, "line": number, "call": call, "grep_note": note})
    for index, site in enumerate(sites, start=1):
        site["site_id"] = f"S{index:03d}"
    return sites


def mark_sample(all_sites: list[dict]) -> str:
    for site in all_sites:
        if site["grep_note"]:
            site["in_sample"] = "no"
            site["stratum"] = "excluded_comment"
    sites = [s for s in all_sites if not s["grep_note"]]

    if len(sites) <= SAMPLE_THRESHOLD:
        for site in sites:
            site["in_sample"] = "yes"
            site["stratum"] = site["file"]
        return f"all {len(sites)} sites (<= {SAMPLE_THRESHOLD})"

    by_file: dict[str, list[dict]] = {}
    for site in sites:
        by_file.setdefault(site["file"], []).append(site)
        site["in_sample"] = "no"
        site["stratum"] = site["file"]

    rng = random.Random(SEED)
    chosen = 0
    for file in sorted(by_file):
        group = sorted(by_file[file], key=lambda s: s["line"])
        quota = min(len(group), max(1, round(SAMPLE_SIZE * len(group) / len(sites))))
        for site in rng.sample(group, quota):
            site["in_sample"] = "yes"
            chosen += 1

    # Top up to SAMPLE_SIZE from files with unsampled sites, in file order.
    remaining = [s for s in sites if s["in_sample"] == "no"]
    while chosen < SAMPLE_SIZE and remaining:
        pick = rng.choice(remaining)
        pick["in_sample"] = "yes"
        remaining.remove(pick)
        chosen += 1

    return (
        f"stratified sample of {chosen} of {len(sites)} non-comment sites; strata = PHP file; "
        f"quota max(1, round({SAMPLE_SIZE} * file_sites / total)); seed {SEED}"
    )


def main() -> None:
    summary = {}
    for app, (root, excluded) in APPS.items():
        root_path = ROOT / root
        if not root_path.is_dir():
            raise SystemExit(f"missing raw PHP for {app}: {root_path}")
        sites = grep_sites(root_path, excluded)
        sampling = mark_sample(sites)

        out_dir = OUT_ROOT / app
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "candidates.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for site in sites:
                writer.writerow({field: site.get(field, "") for field in FIELDS})

        summary[app] = {
            "raw_php_root": root,
            "excluded_directories": excluded,
            "php_files_scanned": sum(1 for _ in php_files(root_path, excluded)),
            "sites": len(sites),
            "sites_flagged_comment": sum(1 for s in sites if s["grep_note"]),
            "in_sample": sum(1 for s in sites if s["in_sample"] == "yes"),
            "sampling": sampling,
        }
        print(app, summary[app]["sites"], "sites,", summary[app]["in_sample"], "in sample")

    (OUT_ROOT / "candidates_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
