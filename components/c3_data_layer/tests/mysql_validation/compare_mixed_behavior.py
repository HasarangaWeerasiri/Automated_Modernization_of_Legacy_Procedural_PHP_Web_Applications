
"""
C3 Data Layer Migration
Mixed CRUD MySQL Behavioral Comparison

Compares observed legacy PHP results with migrated Python results.
"""

import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

LEGACY_FILE = BASE_DIR / "mixed_legacy_results.json"
MIGRATED_FILE = BASE_DIR / "mixed_migrated_results.json"

STAGES = [
    "q001",
    "q002",
    "q004_state",
    "q005",
    "q003_deleted",
    "final_state",
]


def load_results(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing results file: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main():
    legacy = load_results(LEGACY_FILE)
    migrated = load_results(MIGRATED_FILE)

    print("=" * 55)
    print("C3 MIXED CRUD - MYSQL BEHAVIORAL COMPARISON")
    print("=" * 55)

    passed = 0
    failed = 0

    for stage in STAGES:
        if (
            stage in legacy
            and stage in migrated
            and legacy[stage] == migrated[stage]
        ):
            print(f"[PASS] {stage.upper()}")
            passed += 1
        else:
            print(f"[FAIL] {stage.upper()}")
            print(f"  PHP:    {legacy.get(stage)}")
            print(f"  Python: {migrated.get(stage)}")
            failed += 1

    # Verify that the unsupported dynamic query remains excluded.
    q006_safe = (
        legacy.get("q006") == "manual_review_required"
        and migrated.get("q006") == "manual_review_required"
    )

    if q006_safe:
        print("[PASS] Q006 MANUAL REVIEW MARKER")
        passed += 1
    else:
        print("[FAIL] Q006 MANUAL REVIEW MARKER")
        failed += 1

    total = len(STAGES) + 1

    report = {
        "benchmark": "mixed_crud",
        "database": "MySQL 8.0",
        "passed": passed,
        "failed": failed,
        "total": total,
        "recorded_results_match": failed == 0,
        "unsupported_query": "Q006",
        "unsupported_query_executed": False,
    }

    report_file = BASE_DIR / "mixed_comparison_report.json"

    report_file.write_text(
        json.dumps(report, indent=4),
        encoding="utf-8",
    )

    print("-" * 55)
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {failed}/{total}")
    print(f"Report saved to: {report_file}")

    print("\nRESULT: PASS" if failed == 0 else "\nRESULT: FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
