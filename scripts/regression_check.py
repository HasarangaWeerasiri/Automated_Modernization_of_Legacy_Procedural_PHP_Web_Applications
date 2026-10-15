"""Run C3 on every benchmark into a temp folder and compare per-query results.

Used before each commit to confirm that results changed only where a task
intends. It never writes into committed output/ or evidence/ folders.

Usage (from repo root, project venv):
    python scripts/regression_check.py <out_dir> [--reference <previous out_dir>]

For each app it prints the summary counts. With --reference, it also lists
every query whose recovery or conversion status or reason code changed.
Runs that stop early (e.g. a schema crash) are reported, and whatever
queries.json / converted_queries.json they wrote is still compared.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "c3_data_layer"
APPS = ROOT / "benchmarks" / "apps"

BENCHMARKS = {
    "simple_crud": (APPS / "simple_crud" / "legacy", APPS / "simple_crud" / "schema.sql"),
    "mixed_crud": (APPS / "mixed_crud" / "legacy", APPS / "mixed_crud" / "schema.sql"),
    "hard_crud": (APPS / "hard_crud" / "legacy", APPS / "hard_crud" / "schema.sql"),
    "clinic": (APPS / "clinic_inventory_legacy_hard" / "legacy", APPS / "clinic_inventory_legacy_hard" / "database" / "schema.sql"),
    "helpdesk": (APPS / "helpdesk_legacy_holdout" / "legacy", APPS / "helpdesk_legacy_holdout" / "database" / "schema.sql"),
    "hms_kishan0725": (APPS / "_app_only" / "hms_kishan0725", APPS / "hms_kishan0725" / "myhmsdb.sql"),
    "news_buzz": (APPS / "_app_only" / "news_buzz", APPS / "news_buzz" / "db" / "NEWSBUZZ.sql"),
    "fee_rainbow": (APPS / "_app_only" / "fee_rainbow", APPS / "fee_rainbow" / "database" / "db.sql"),
}


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _per_query(app_out: Path) -> dict[str, tuple]:
    converted = {q["query_id"]: q for q in _load(app_out / "converted_queries.json")}
    result = {}
    for query in _load(app_out / "queries.json"):
        conv = converted.get(query["query_id"], {})
        key = f'{query["query_id"]} {Path(query.get("source_file") or "").name}:{query.get("source_line")}'
        result[key] = (query.get("status"), query.get("reason_code"), conv.get("status"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--reference")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    env = {**os.environ, "PYTHONPATH": str(COMPONENT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    changed_total = 0

    for name, (php, schema) in BENCHMARKS.items():
        if not php.exists():
            print(f"== {name}: SKIPPED (source not present: {php})")
            continue

        app_out = out_dir / name
        proc = subprocess.run(
            [sys.executable, "-m", "c3.cli", "run", "--php", str(php), "--schema", str(schema), "--output", str(app_out)],
            cwd=COMPONENT, env=env, capture_output=True, text=True,
        )
        (out_dir / f"{name}_log.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")

        per_query = _per_query(app_out)
        recovered = sum(1 for s in per_query.values() if s[0] == "RECOVERED")
        converted = sum(1 for s in per_query.values() if s[2] == "CONVERTED")
        error = "" if proc.returncode == 0 else " | EXIT %d: %s" % (
            proc.returncode, (proc.stderr.strip().splitlines() or ["?"])[-1])
        print(f"== {name}: total {len(per_query)}, recovered {recovered}, converted {converted}{error}")

        if args.reference:
            reference = _per_query(Path(args.reference) / name)
            for key in sorted(set(per_query) | set(reference)):
                if per_query.get(key) != reference.get(key):
                    changed_total += 1
                    print(f"   CHANGED {key}: {reference.get(key)} -> {per_query.get(key)}")

    if args.reference:
        print(f"\nTotal changed queries: {changed_total}")


if __name__ == "__main__":
    main()
