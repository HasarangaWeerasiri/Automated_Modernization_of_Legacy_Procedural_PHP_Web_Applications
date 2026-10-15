"""E4: injection negatives on every generated data-access function.

Each CONVERTED function of every benchmark app is called with hostile values
for every parameter (template functions that take an `inputs` mapping get the
payload for every key they read). A recording session captures each
statement and its bound parameters, so no database is needed and every
function can be exercised. The function is SAFE for a payload when either

  * it rejects the value (ValueError / TypeError before executing), or
  * the payload never appears in the SQL text, the SQL text is one the
    function also produces for benign values, and the payload travels only
    as a bound parameter value.

Runtime behaviour on MySQL with real rows is covered by the hostile cases of
E1 (scripts/equivalence), which compare against the legacy PHP.

Usage (repo root, component venv):
    python scripts/e4_injection_check.py --out <dir>
"""

import argparse
import importlib.util
import inspect
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components" / "c3_data_layer" / "src"))
APPS_DIR = ROOT / "benchmarks" / "apps"

APPS = {
    "simple_crud": ("simple_crud/legacy", "simple_crud/schema.sql"),
    "mixed_crud": ("mixed_crud/legacy", "mixed_crud/schema.sql"),
    "hard_crud": ("hard_crud/legacy", "hard_crud/schema.sql"),
    "clinic": ("clinic_inventory_legacy_hard/legacy", "clinic_inventory_legacy_hard/database/schema.sql"),
    "helpdesk": ("helpdesk_legacy_holdout/legacy", "helpdesk_legacy_holdout/database/schema.sql"),
    "hms_kishan0725": ("_app_only/hms_kishan0725", "hms_kishan0725/myhmsdb.sql"),
    "news_buzz": ("_app_only/news_buzz", "news_buzz/db/NEWSBUZZ.sql"),
    "fee_rainbow": ("_app_only/fee_rainbow", "fee_rainbow/database/db.sql"),
    "dignity_dev_hms": ("_app_only/dignity_dev_hms", "dignity_dev_hms/127_0_0_1.sql"),
}

PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1 -- ",
    "\" OR \"\"=\"",
    "1; DROP TABLE users;-- ",
    "1 UNION SELECT NULL, NULL -- ",
    "\\' OR 1=1 #",
    "%' OR '%'='",
    "’ OR 1=1 -- ",
    "id DESC, (SELECT SLEEP(5))",
    "patients WHERE 1=1 -- ",
    "0x27206f7220313d31",
    "abc\x00' OR '1'='1",
]
BENIGN = ["1", "a", "id", "0"]
SIGNATURES = ["OR '1'='1", "OR 1=1", "DROP TABLE", "UNION SELECT", "SLEEP(", "WHERE 1=1", "OR \"\"=\"", "0x2720"]


class AnyKey(dict):
    """An `inputs` mapping that returns the same value for every key."""

    def __init__(self, value):
        super().__init__()
        self.value = value

    def __getitem__(self, key):
        return self.value

    def get(self, key, default=None):
        return self.value

    def __contains__(self, key):
        return True


class _Result:
    rowcount = 0
    lastrowid = 0

    def mappings(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def scalar(self):
        return None

    def scalar_one_or_none(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter([])


class RecordingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None, *args, **kwargs):
        from sqlalchemy.dialects import mysql

        compiled = statement.compile(dialect=mysql.dialect()) if hasattr(statement, "compile") else statement
        bound = dict(params or {})
        bound.update({k: v for k, v in getattr(compiled, "params", {}).items() if k not in bound})
        self.statements.append({"sql": str(compiled), "params": bound})
        return _Result()

    def scalars(self, statement, params=None, **kwargs):
        return self.execute(statement, params)

    def commit(self):
        pass

    def rollback(self):
        pass

    def flush(self):
        pass


def call(function, value):
    names = [n for n, p in inspect.signature(function).parameters.items()
             if n != "session" and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL, p.KEYWORD_ONLY)]
    arguments = {n: (AnyKey(value) if n == "inputs" else value) for n in names}
    session = RecordingSession()
    try:
        function(session, **arguments)
    except (ValueError, TypeError, KeyError) as error:
        return {"outcome": "REJECTED", "error": f"{type(error).__name__}: {error}"[:200], "statements": session.statements}
    except Exception as error:  # reported, never hidden
        return {"outcome": "ERROR", "error": f"{type(error).__name__}: {error}"[:200], "statements": session.statements}
    return {"outcome": "EXECUTED", "statements": session.statements}


def _contains(text: str, payload: str) -> bool:
    upper = text.upper()
    return payload in text or any(sig.upper() in upper and sig.upper() in payload.upper() for sig in SIGNATURES)


def check_function(function) -> dict:
    benign_texts = set()
    for value in BENIGN:
        result = call(function, value)
        benign_texts |= {s["sql"] for s in result["statements"]}

    cases = []
    for payload in PAYLOADS:
        result = call(function, payload)
        problems = []
        bound = False
        for statement in result["statements"]:
            if _contains(statement["sql"], payload):
                problems.append("payload text in SQL")
            if benign_texts and statement["sql"] not in benign_texts:
                problems.append("SQL text differs from every benign call")
            values = [v for v in statement["params"].values()]
            flat = [x for v in values for x in (v if isinstance(v, (list, tuple)) else [v])]
            if any(isinstance(v, str) and payload in v for v in flat):
                bound = True
        if result["outcome"] == "ERROR":
            problems.append(result["error"])
        safe = not problems
        cases.append({
            "payload": payload, "outcome": result["outcome"], "bound_as_parameter": bound,
            "safe": safe, "problems": problems, **({"error": result["error"]} if "error" in result else {}),
        })
    return {
        "verdict": "SAFE" if all(c["safe"] for c in cases) else "UNSAFE",
        "rejected": sum(c["outcome"] == "REJECTED" for c in cases),
        "bound": sum(c["bound_as_parameter"] for c in cases),
        "sql_variants_for_benign_values": len(benign_texts),
        "cases": cases,
    }


def generated_functions(module, converted: dict):
    for name, function in inspect.getmembers(module, inspect.isfunction):
        # Generated query functions end in _q<NNN>; inlined runtime helpers do not.
        if function.__module__ != module.__name__ or name.startswith("unresolved_query_") or not re.search(r"_q\d+$", name):
            continue
        query_id = name.rsplit("_", 1)[-1].upper()
        yield query_id, name, function, converted.get(query_id, {}).get("conversion_rule")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--apps", default=",".join(APPS))
    args = parser.parse_args()

    from c3.pipeline import run_pipeline

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report = {"experiment": "E4 injection negatives (recording session, no database)",
              "date_utc": datetime.now(timezone.utc).isoformat(), "git_head": head,
              "payloads": PAYLOADS, "apps": {}}

    for app in [a for a in args.apps.split(",") if a]:
        php, schema = APPS[app]
        with tempfile.TemporaryDirectory() as tmp:
            c3_out = Path(tmp) / app
            run_pipeline(APPS_DIR / php, APPS_DIR / schema, c3_out)
            converted = {q["query_id"]: q for q in json.loads((c3_out / "converted_queries.json").read_text(encoding="utf-8"))}
            spec = importlib.util.spec_from_file_location(f"e4_{app}", c3_out / "data_access.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            functions = {}
            for query_id, name, function, rule in generated_functions(module, converted):
                result = check_function(function)
                functions[name] = {"query_id": query_id, "conversion_rule": rule, **result}

        safe = sum(f["verdict"] == "SAFE" for f in functions.values())
        report["apps"][app] = {"functions": len(functions), "safe": safe, "unsafe": len(functions) - safe, "detail": functions}
        print(f"{app}: {safe}/{len(functions)} generated functions SAFE "
              f"({sum(f['conversion_rule'] == 'query_specific_template' for f in functions.values())} template)")

    (out / "injection_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return 0 if all(a["unsafe"] == 0 for a in report["apps"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
