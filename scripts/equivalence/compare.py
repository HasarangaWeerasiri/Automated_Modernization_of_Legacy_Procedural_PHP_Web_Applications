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


MYSQL_CODE = re.compile(r"^\s*(\d{4})\b|\((\d{4}),")


def sql_shape(sql: str | None):
    """Token types of a SQL string with literal contents ignored (None if it does not tokenize)."""

    if sql is None:
        return None
    try:
        from sqlglot.dialects.mysql import MySQL
        from sqlglot.tokens import TokenType

        literals = {TokenType.STRING, TokenType.NUMBER, TokenType.NATIONAL_STRING, TokenType.HEX_STRING,
                    TokenType.BIT_STRING, TokenType.BYTE_STRING}
        return [("LIT" if t.token_type in literals else t.token_type.name) for t in MySQL().tokenize(sql)]
    except Exception:
        return None


def structure_changed(hostile_sql: str | None, benign_sql: str | None) -> bool:
    """True when the hostile value changed the legacy SQL structure, not just a literal.

    benign_sql is the same legacy statement built with a harmless value ("1")
    for every input. A hostile statement that no longer tokenizes counts as changed.
    """

    if hostile_sql is None or benign_sql is None:
        return False
    hostile, benign = sql_shape(hostile_sql), sql_shape(benign_sql)
    if benign is None:
        return False
    return hostile is None or hostile != benign


def error_code(text: str | None) -> int | None:
    match = MYSQL_CODE.search(text or "")
    return int(match.group(1) or match.group(2)) if match else None


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
    # Injection needs evidence: the payload changed the SQL structure, not only a literal.
    record["legacy_structure_changed"] = hostile and structure_changed(legacy.get("sql"), legacy.get("benign_sql"))
    injected = record["legacy_structure_changed"]
    record["legacy_error_code"] = error_code(legacy.get("error")) if legacy_status == "sql_error" else None
    record["migrated_error_code"] = error_code(migrated.get("error")) if migrated.get("status") == "error" else None

    if legacy_status == "sql_error":
        if injected:
            record["verdict"] = "LEGACY_INJECTABLE"
            record["reason"] = (f"hostile input changed the legacy SQL structure (MySQL error {legacy.get('error')}); "
                                f"generated side: {migrated.get('status')}")
        else:
            both = migrated.get("status") == "error"
            record["verdict"] = "INCONCLUSIVE"
            record["both_rejected"] = both
            record["same_error_code"] = both and record["legacy_error_code"] == record["migrated_error_code"]
            record["reason"] = (f"legacy SQL error: {legacy.get('error')}"
                                + (f"; generated side also rejected (MySQL {record['migrated_error_code']})" if both else ""))
        return record

    if migrated.get("status") == "error":
        if injected and not migrated.get("state_changed"):
            # Legacy ran an injected statement; the generated code kept the
            # payload a value, MySQL rejected it and nothing changed.
            record["verdict"] = "LEGACY_INJECTABLE"
            record["reason"] = ("hostile input changed the legacy SQL structure and it executed; generated code bound it "
                                f"as a value, MySQL rejected it (error {record['migrated_error_code']}), database unchanged")
        else:
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
    elif injected:
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
