
"""
C3 Data Layer Migration
Hard CRUD - MySQL Behavioral Comparison

Compare original PHP and migrated Python LIKE-search results.
"""

import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

LEGACY_FILE = BASE_DIR / "hard_legacy_results.json"
MIGRATED_FILE = BASE_DIR / "hard_migrated_results.json"
CONVERTED_FILE = (
    Path(__file__).resolve().parents[4]
    / "output"
    / "hard_crud"
    / "converted_queries.json"
)

SEARCH_TERMS = ["Ali", "Alex", "Bob", "Charlie", "xyz"]


def load_json(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def main():
    legacy = load_json(LEGACY_FILE)
    migrated = load_json(MIGRATED_FILE)
    conversions = load_json(CONVERTED_FILE)

    print("=" * 55)
    print("C3 HARD CRUD - MYSQL BEHAVIORAL COMPARISON")
    print("=" * 55)

    passed = 0
    failed = 0

    # Compare actual PHP and Python LIKE-search results.
    metadata_valid = (
        legacy.get("benchmark") == "hard_crud"
        and migrated.get("benchmark") == "hard_crud"
        and legacy.get("query_id") == "Q001"
        and migrated.get("query_id") == "Q001"
    )

    if not metadata_valid:
        print("[FAIL] Benchmark or query metadata mismatch")
        return 1

    for term in SEARCH_TERMS:
        php_results = legacy.get("search_results", {}).get(term)
        python_results = migrated.get("search_results", {}).get(term)

        if (
            isinstance(php_results, list)
            and isinstance(python_results, list)
            and php_results == python_results
        ):
            print(f"[PASS] LIKE SEARCH: {term}")
            passed += 1
        else:
            print(f"[FAIL] LIKE SEARCH: {term}")
            print(f"  PHP:    {php_results}")
            print(f"  Python: {python_results}")
            failed += 1

    # Check C3's conversion decisions for Q001-Q005.
    status_by_id = {
        query["query_id"]: query["status"]
        for query in conversions
    }

    expected_statuses = {
        "Q001": "CONVERTED",
        "Q002": "UNRESOLVED",
        "Q003": "UNRESOLVED",
        "Q004": "UNRESOLVED",
        "Q005": "UNRESOLVED",
    }

    status_passed = status_by_id == expected_statuses

    if status_passed:
        print("[PASS] CONVERSION STATUS CHECK")
        passed += 1
    else:
        print("[FAIL] CONVERSION STATUS CHECK")
        print(f"  Expected: {expected_statuses}")
        print(f"  Actual:   {status_by_id}")
        failed += 1

    total = len(SEARCH_TERMS) + 1

    report = {
        "benchmark": "hard_crud",
        "database": "MySQL 8.0",
        "tested_query": "Q001",
        "search_cases": len(SEARCH_TERMS),
        "passed": passed,
        "failed": failed,
        "total": total,
        "recorded_results_match": failed == 0,
        "unresolved_queries": [
            "Q002", "Q003", "Q004", "Q005"
        ],
    }

    report_file = BASE_DIR / "hard_comparison_report.json"

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
