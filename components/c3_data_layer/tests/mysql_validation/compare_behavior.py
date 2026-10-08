
"""
C3 Data Layer Migration
Automated MySQL Behavioral Comparison

Compares results from original PHP and migrated Python.
"""

import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

LEGACY_FILE = BASE_DIR / "legacy_results.json"
MIGRATED_FILE = BASE_DIR / "migrated_results.json"

STAGES = ["create", "read", "update", "delete", "final_state"]


def load_results(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing results file: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main():
    legacy = load_results(LEGACY_FILE)
    migrated = load_results(MIGRATED_FILE)

    print("=" * 55)
    print("C3 DATA LAYER - MYSQL BEHAVIORAL COMPARISON")
    print("=" * 55)

    passed = 0
    failed = 0

    for stage in STAGES:
        legacy_result = legacy.get(stage)
        migrated_result = migrated.get(stage)

        if stage in legacy and stage in migrated and legacy_result == migrated_result:
            print(f"[PASS] {stage.upper()}")
            passed += 1
        else:
            print(f"[FAIL] {stage.upper()}")
            print(f"  Legacy PHP:      {legacy_result}")
            print(f"  Migrated Python: {migrated_result}")
            failed += 1

    print("-" * 55)
    print(f"Passed: {passed}/{len(STAGES)}")
    print(f"Failed: {failed}/{len(STAGES)}")

    report = {
        "benchmark": "simple_crud",
        "database": "MySQL 8.0",
        "passed": passed,
        "failed": failed,
        "total": len(STAGES),
        "behaviorally_equivalent_for_recorded_stages": failed == 0,
    }

    report_file = BASE_DIR / "comparison_report.json"

    report_file.write_text(
        json.dumps(report, indent=4),
        encoding="utf-8",
    )

    print(f"Report saved to: {report_file}")

    if failed:
        print("\nRESULT: FAIL")
        return 1

    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
