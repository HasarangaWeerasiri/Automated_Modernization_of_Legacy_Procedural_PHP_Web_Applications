"""E1 comparison of one legacy run and one generated-code run (pure functions).

Values are compared in the form the MySQL text protocol gives PHP (mysqli
returns every value as a string or NULL), so the generated side's Python
values are normalised to that form first.
"""

import datetime
import decimal
import hashlib
import json
import re

ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


def mysql_text(value):
    """A driver value as the MySQL text protocol would print it."""

    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, decimal.Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value).removesuffix(".0") if value.is_integer() else repr(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        seconds = int(value.total_seconds())
        sign = "-" if seconds < 0 else ""
        seconds = abs(seconds)
        return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (set, frozenset)):
        return ",".join(sorted(value))
    return str(value)


def normalise_rows(rows, ordered: bool):
    rows = [[mysql_text(v) for v in row] for row in rows]
    return rows if ordered else sorted(rows, key=lambda r: json.dumps(r))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def payload_verbatim(sql: str | None, inputs: dict) -> bool:
    """True when a hostile payload appears unescaped in the legacy SQL text."""

    if not sql:
        return False
    for value in inputs.values():
        values = value.values() if isinstance(value, dict) else [value]
        for item in values:
            if isinstance(item, str) and item and item in sql and any(c in item for c in "'\";-%_"):
                return True
    return False


def compare_case(case: str, legacy: dict, migrated: dict, inputs: dict) -> dict:
    """Verdict for one input case.

    legacy:   output of legacy_runner.php run (status executed / sql_error / ...)
              plus "checksums_after".
    migrated: {"status": "ok"|"error"|"unbound_parameter", "columns", "rows",
               "affected_rows", "insert_id", "error", "checksums_after"}.
    """

    hostile = case.startswith("hostile")
    record = {"case": case, "differences": []}
    legacy_status = legacy.get("status")

    if legacy_status not in ("executed", "sql_error"):
        record["verdict"] = "INCONCLUSIVE"
        record["reason"] = f"legacy SQL not built: {legacy_status} {legacy.get('error') or legacy.get('missing') or ''}".strip()
        return record
    if migrated.get("status") == "unbound_parameter":
        record["verdict"] = "INCONCLUSIVE"
        record["reason"] = f"generated parameter(s) {migrated.get('missing')} match no PHP variable at the call"
        return record

    record["legacy_payload_verbatim"] = hostile and payload_verbatim(legacy.get("sql"), inputs)

    if legacy_status == "sql_error":
        if hostile and record["legacy_payload_verbatim"]:
            record["verdict"] = "LEGACY_INJECTABLE"
            record["reason"] = f"hostile input changed the legacy SQL (MySQL error {legacy.get('error')}); generated side: {migrated.get('status')}"
        else:
            record["verdict"] = "INCONCLUSIVE"
            record["reason"] = f"legacy SQL error: {legacy.get('error')}"
        return record

    if migrated.get("status") == "error":
        record["verdict"] = "FAIL"
        record["reason"] = f"generated code raised {migrated.get('error')}"
        return record

    ordered = bool(ORDER_BY.search(legacy.get("sql") or ""))
    record["ordered_comparison"] = ordered

    if "rows" in legacy or "rows" in migrated:
        if "rows" not in legacy or "rows" not in migrated:
            record["differences"].append("one side returned rows, the other did not")
        else:
            legacy_rows = normalise_rows(legacy["rows"], ordered)
            migrated_rows = normalise_rows(migrated["rows"], ordered)
            record["row_count"] = {"legacy": len(legacy_rows), "migrated": len(migrated_rows)}
            record["rows_digest"] = {"legacy": digest(legacy_rows), "migrated": digest(migrated_rows)}
            if migrated_rows and [c.lower() for c in legacy.get("columns", [])] != [c.lower() for c in migrated.get("columns", [])]:
                record["differences"].append("column names differ")
            if legacy_rows != migrated_rows:
                record["differences"].append("rows differ")
    else:
        for key in ("affected_rows", "insert_id"):
            record[key] = {"legacy": legacy.get(key), "migrated": migrated.get(key)}
            if legacy.get(key) != migrated.get(key) and not (key == "insert_id" and not legacy.get(key) and not migrated.get(key)):
                record["differences"].append(f"{key} differ")

    if legacy.get("checksums_after") != migrated.get("checksums_after"):
        tables = sorted(
            t for t in set(legacy.get("checksums_after", {})) | set(migrated.get("checksums_after", {}))
            if legacy.get("checksums_after", {}).get(t) != migrated.get("checksums_after", {}).get(t)
        )
        record["differences"].append(f"table state differs after the call: {tables}")

    if not record["differences"]:
        record["verdict"] = "PASS"
    elif hostile and record["legacy_payload_verbatim"]:
        record["verdict"] = "LEGACY_INJECTABLE"
        record["reason"] = "hostile input changed the legacy SQL; generated code bound it as a value"
    else:
        record["verdict"] = "FAIL"
    return record


def site_verdict(case_records: list[dict]) -> str:
    verdicts = [r["verdict"] for r in case_records]
    if "FAIL" in verdicts:
        return "FAIL"
    if "PASS" in verdicts and all(v in ("PASS", "LEGACY_INJECTABLE") for v in verdicts):
        return "PASS"
    return "INCONCLUSIVE"
