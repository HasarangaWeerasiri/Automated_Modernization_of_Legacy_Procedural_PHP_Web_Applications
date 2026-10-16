"""Site-level evaluation against a verified ground truth (S15).

Ground truth: benchmarks/ground truth/<app>/queries.json, one entry per SQL
execution site (file, line, site_status, expected_recoverable true / false /
"ambiguous", expected_* columns). C3 records are matched by (file, line),
never by query ID (C3 query IDs are positional).

Rules
  * Only live sites count. Non-live GT sites (commented out, dead code such
    as an empty SQL string) that C3 reports are listed under
    non_live_sites_reported with their site_status. They are not scored.
  * "ambiguous" sites are excluded from precision / recall and listed.
  * Recovery: expected true & RECOVERED = TP, expected true & abstained =
    FN (false abstention), expected false & RECOVERED = FP (false
    recovery, the unsound case), expected false & abstained = TN.
  * Column use (TP sites): operation exact; tables, read, write and filter
    columns as case-insensitive sets.
  * Schema-mismatch sites (the SQL names a table or column the schema does
    not define) stay in precision / recall but are reported separately and
    left out of the column-use accuracy, which is also given for them alone.
"""

import json
from pathlib import Path
from typing import Any


def _relative(path: str, root: Path) -> str:
    resolved = str(Path(path).resolve()).replace("\\", "/").lower()
    prefix = str(root.resolve()).replace("\\", "/").lower().rstrip("/") + "/"
    return resolved[len(prefix):] if resolved.startswith(prefix) else Path(path).name.lower()


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in (values or [])}


def _schema_mismatch(usage: dict | None, expected: dict, schema: dict) -> list[str]:
    columns = {t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in schema.get("tables", [])}
    problems = []
    tables = _lower_set(expected.get("expected_tables")) or _lower_set((usage or {}).get("tables"))
    for table in sorted(tables):
        if table not in columns:
            problems.append(f"table {table} not in schema")
    known = set().union(*(columns.get(t, set()) for t in tables)) if tables else set()
    used = (_lower_set(expected.get("expected_read_columns")) | _lower_set(expected.get("expected_write_columns"))
            | _lower_set(expected.get("expected_filter_columns"))) - {"*"}
    for column in sorted(used):
        if tables and all(t in columns for t in tables) and column not in known:
            problems.append(f"column {column} not in {sorted(tables)}")
    return problems


def _percentage(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def evaluate_sites(
    ground_truth: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    column_usage: list[dict[str, Any]],
    schema: dict,
    php_root: str | Path,
) -> dict[str, Any]:
    root = Path(php_root)
    c3_sites = {(_relative(q["source_file"], root), int(q["source_line"])): q for q in queries}
    usage_by_id = {u["query_id"]: u for u in column_usage}

    counts = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
    details, ambiguous, non_live_reported, schema_mismatch = [], [], [], []
    matched_keys = set()
    fields = ("operation", "tables", "read", "write", "filter")
    column_correct = {"main": dict.fromkeys(fields, 0), "schema_mismatch": dict.fromkeys(fields, 0)}
    column_total = {"main": 0, "schema_mismatch": 0}

    for entry in ground_truth:
        key = (entry["source_file"].replace("\\", "/").lower(), int(entry["line"]))
        record = c3_sites.get(key)
        if record is not None:
            matched_keys.add(key)

        if entry.get("site_status") != "live":
            if record is not None:
                non_live_reported.append({"site": f"{entry['source_file']}:{entry['line']}",
                                          "site_status": entry.get("site_status"), "c3_query_id": record["query_id"],
                                          "c3_status": record.get("status")})
            continue

        detail = {"gt_id": entry.get("query_id"), "site": f"{entry['source_file']}:{entry['line']}",
                  "expected_recoverable": entry.get("expected_recoverable"),
                  "c3_query_id": record["query_id"] if record else None,
                  "c3_status": record["status"] if record else "NOT_DETECTED",
                  "c3_reason_code": record.get("reason_code") if record else None}

        if entry.get("expected_recoverable") == "ambiguous":
            detail["outcome"] = "EXCLUDED_AMBIGUOUS"
            ambiguous.append(detail)
            details.append(detail)
            continue

        recovered = record is not None and record.get("status") == "RECOVERED"
        expected = entry.get("expected_recoverable") is True
        outcome = {(True, True): "TP", (True, False): "FN", (False, True): "FP", (False, False): "TN"}[(expected, recovered)]
        counts[outcome] += 1
        detail["outcome"] = outcome

        if outcome == "TP":
            usage = usage_by_id.get(record["query_id"], {})
            mismatch = _schema_mismatch(usage, entry, schema)
            bucket = "schema_mismatch" if mismatch else "main"
            checks = {
                "operation": (usage.get("operation") or "").upper() == (entry.get("expected_operation") or "").upper(),
                "tables": _lower_set(usage.get("tables")) == _lower_set(entry.get("expected_tables")),
                "read": _lower_set(usage.get("read_columns")) == _lower_set(entry.get("expected_read_columns")),
                "write": _lower_set(usage.get("write_columns")) == _lower_set(entry.get("expected_write_columns")),
                "filter": _lower_set(usage.get("filter_columns")) == _lower_set(entry.get("expected_filter_columns")),
            }
            column_total[bucket] += 1
            for name, ok in checks.items():
                column_correct[bucket][name] += ok
            detail["column_checks"] = checks
            if not all(checks.values()):
                detail["column_differences"] = {
                    "expected": {k: entry.get(f"expected_{k}") for k in
                                 ("operation", "tables", "read_columns", "write_columns", "filter_columns")},
                    "c3": {k: usage.get(k) for k in
                           ("operation", "tables", "read_columns", "write_columns", "filter_columns")},
                }
            if mismatch:
                detail["schema_mismatch"] = mismatch
                schema_mismatch.append(detail)
        details.append(detail)

    extra_c3 = [{"site": f"{k[0]}:{k[1]}", "c3_query_id": q["query_id"]}
                for k, q in sorted(c3_sites.items()) if k not in matched_keys]

    tp, fn, fp, tn = counts["TP"], counts["FN"], counts["FP"], counts["TN"]
    live = [e for e in ground_truth if e.get("site_status") == "live"]
    detected_live = sum(1 for e in live if (e["source_file"].replace("\\", "/").lower(), int(e["line"])) in c3_sites)

    def accuracy(bucket):
        return {name: _percentage(column_correct[bucket][name], column_total[bucket]) for name in fields} | {
            "sites": column_total[bucket]}

    return {
        "matching": "ground truth (file, line) = C3 (source_file relative to the PHP root, source_line)",
        "gt_entries": len(ground_truth),
        "gt_live_sites": len(live),
        "detection": {"live_detected": detected_live, "recall": _percentage(detected_live, len(live)),
                      "non_live_sites_reported": non_live_reported, "c3_sites_not_in_gt": extra_c3},
        "recovery": {
            "excluded_ambiguous": len(ambiguous),
            "evaluated": tp + fn + fp + tn,
            **counts,
            "precision": _percentage(tp, tp + fp),
            "recall": _percentage(tp, tp + fn),
            "accuracy": _percentage(tp + tn, tp + fn + fp + tn),
            "false_abstentions": [d["site"] for d in details if d.get("outcome") == "FN"],
            "false_recoveries": [d["site"] for d in details if d.get("outcome") == "FP"],
        },
        "column_use_accuracy": accuracy("main"),
        "schema_mismatch": {"sites": [{"site": d["site"], "problems": d["schema_mismatch"]} for d in schema_mismatch],
                            "column_use_accuracy": accuracy("schema_mismatch")},
        "ambiguous_sites": ambiguous,
        "details": details,
    }


def evaluate_output(ground_truth_file, c3_output_dir, php_root, report_file=None) -> dict[str, Any]:
    out = Path(c3_output_dir)

    def load(name):
        return json.loads((out / name).read_text(encoding="utf-8"))

    result = evaluate_sites(
        json.loads(Path(ground_truth_file).read_text(encoding="utf-8")),
        load("queries.json"), load("column_usage.json"), load("schema.json"), php_root,
    )
    result["ground_truth_file"] = Path(ground_truth_file).as_posix()
    if report_file:
        Path(report_file).parent.mkdir(parents=True, exist_ok=True)
        Path(report_file).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
