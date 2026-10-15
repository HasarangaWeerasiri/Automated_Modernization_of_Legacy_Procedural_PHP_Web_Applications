"""E1: PHP versus generated SQLAlchemy equivalence on MySQL, per SQL site.

Sites come from the independent grep (scripts/ground_truth/grep_sql_sites.py),
not from C3. For each site:
  * legacy side: legacy_runner.php rebuilds the SQL with PHP from the verbatim
    source and runs it with mysqli on c3_e1_<app>_legacy;
  * generated side: the C3 function for the same (file, line) runs on
    c3_e1_<app>_migrated, with parameters bound by name to the PHP variables
    (migrated_runner.py);
  * compare.py compares rows / affected rows / insert id and CHECKSUM TABLE
    of every table afterwards.
Both databases are rebuilt from schema + seed before EVERY case.

Commands (repo root, component venv):
  python scripts/equivalence/run_e1.py skeleton [--apps a,b]   # case files, no DB
  python scripts/equivalence/run_e1.py dry-run  --out <dir>     # build SQL + bind params, no DB
  python scripts/equivalence/run_e1.py run      --out <dir>     # MySQL; needs C3_DB_PASSWORD

The password is read from C3_DB_PASSWORD only and is passed to child
processes through the environment (MYSQL_PWD for the mysql client).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_SRC = ROOT / "components" / "c3_data_layer" / "src"
sys.path.insert(0, str(COMPONENT_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "ground_truth"))

import compare  # noqa: E402
import migrated_runner  # noqa: E402
from grep_sql_sites import grep_sites  # noqa: E402

LEGACY_RUNNER = Path(__file__).resolve().parent / "legacy_runner.php"
CASES_DIR = ROOT / "benchmarks" / "equivalence_cases"
APPS_DIR = ROOT / "benchmarks" / "apps"
MYSQL_CLIENT = os.environ.get("C3_MYSQL_CLIENT", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")

# app -> php root, schema (+ seed rows inside), extra seed file, redact (no licence)
APPS = {
    "simple_crud": ("simple_crud/legacy", "simple_crud/schema.sql", "simple_crud.seed.sql", False),
    "mixed_crud": ("mixed_crud/legacy", "mixed_crud/schema.sql", "mixed_crud.seed.sql", False),
    "hard_crud": ("hard_crud/legacy", "hard_crud/schema.sql", "hard_crud.seed.sql", False),
    "helpdesk": ("helpdesk_legacy_holdout/legacy", "helpdesk_legacy_holdout/database/schema.sql", None, False),
    "hms_kishan0725": ("_app_only/hms_kishan0725", "hms_kishan0725/myhmsdb.sql", None, True),
    "news_buzz": ("_app_only/news_buzz", "news_buzz/db/NEWSBUZZ.sql", None, False),
    "fee_rainbow": ("_app_only/fee_rainbow", "fee_rainbow/database/db.sql", None, False),
    "dignity_dev_hms": ("_app_only/dignity_dev_hms", "dignity_dev_hms/127_0_0_1.sql", None, False),
}

HOSTILE = {
    "hostile_quote": "' OR '1'='1",
    "hostile_stacked": "1; DROP TABLE c3_e1_canary;-- ",
    "hostile_wildcard": "%_",
    "hostile_unicode": "\u2019 OR 1=1 -- \u00e9",
}
STUDENT_CASES = ("normal", "empty_result", "boundary")


# --------------------------------------------------------------------- helpers

def php_root(app: str) -> Path:
    return APPS_DIR / APPS[app][0]


def php(*args: str) -> dict:
    completed = subprocess.run(["php", str(LEGACY_RUNNER), *args], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        return {"status": "runner_error", "error": (completed.stderr or completed.stdout)[-500:]}
    return json.loads(completed.stdout)


def legacy(app: str, site: dict, inputs: dict, mode: str, execute: bool, database: str | None = None) -> dict:
    spec = {
        "file": str(php_root(app) / site["file"]), "line": site["line"], "call": site["call"],
        "mode": mode, "inputs": inputs, "execute": execute, "database": database,
        "host": "127.0.0.1", "user": "c3_tester",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(spec, handle)
    try:
        return php("run", handle.name)
    finally:
        os.unlink(handle.name)


def run_c3(app: str, out: Path) -> None:
    from c3.pipeline import run_pipeline

    run_pipeline(php_root(app), APPS_DIR / APPS[app][1], out)


def c3_records(out: Path, app: str) -> dict:
    root = str(php_root(app).resolve()).replace("\\", "/").lower().rstrip("/") + "/"
    queries = json.loads((out / "queries.json").read_text(encoding="utf-8"))
    converted = {q["query_id"]: q for q in json.loads((out / "converted_queries.json").read_text(encoding="utf-8"))}
    records = {}
    for query in queries:
        path = str(Path(query["source_file"]).resolve()).replace("\\", "/").lower()
        relative = path[len(root):] if path.startswith(root) else path
        conversion = converted.get(query["query_id"], {})
        records[(relative, int(query["source_line"]))] = {
            "query_id": query["query_id"],
            "recovery_status": query.get("status"),
            "recovery_reason": query.get("reason_code"),
            "conversion_status": conversion.get("status"),
            "conversion_rule": conversion.get("conversion_rule"),
            "conversion_reason": conversion.get("reason"),
            "dynamic_variable_sources": query.get("dynamic_variable_sources") or {},
        }
    return records


def sites_for(app: str) -> list[dict]:
    return [s for s in grep_sites(php_root(app), []) if not s["grep_note"]]


# -------------------------------------------------------------------- skeleton

def skeleton(apps: list[str], force: bool) -> None:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for app in apps:
        target = CASES_DIR / f"{app}.json"
        if target.exists() and not force:
            print(f"{app}: {target.name} exists, not overwritten (student edits are kept)")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / app
            run_c3(app, out)
            records = c3_records(out, app)

        entries = []
        for site in sites_for(app):
            sliced = php("slice", str(php_root(app) / site["file"]), str(site["line"]), site["call"])
            record = records.get((site["file"].lower(), site["line"]), {})
            converted = record.get("conversion_status") == "CONVERTED"
            entry = {
                "site_id": site["site_id"], "file": site["file"], "line": site["line"], "call": site["call"],
                "c3_conversion_status (information only)": record.get("conversion_status") or "NOT_DETECTED",
                "legacy_slice_status": sliced.get("status"),
                "inputs": [
                    {k: i[k] for k in ("name", "kind", "keys", "note") if k in i}
                    for i in sliced.get("inputs", [])
                ],
            }
            if not converted:
                entry["cases"] = "not needed: C3 did not convert this site, so it is INCONCLUSIVE by rule"
            elif sliced.get("status") != "ok":
                entry["cases"] = f"not needed: the legacy SQL cannot be rebuilt ({sliced.get('status')}), so INCONCLUSIVE"
            elif not entry["inputs"]:
                entry["cases"] = {"normal": {}}  # constant SQL: one case, nothing to fill
            else:
                def value(payload, item):
                    if item["kind"] == "array":
                        return {key: payload for key in item["keys"]}
                    return payload

                cases = {name: {i["name"]: None for i in entry["inputs"]} for name in STUDENT_CASES}
                for name, payload in HOSTILE.items():
                    cases[name] = {i["name"]: value(payload, i) for i in entry["inputs"]}
                cases["type_mismatch"] = {i["name"]: value("abc", i) for i in entry["inputs"]}
                entry["cases"] = cases
            entries.append(entry)

        document = {
            "app": app,
            "generated": datetime.now(timezone.utc).date().isoformat(),
            "instructions": (
                "Fill the null values of normal / empty_result / boundary by hand from the schema and seed data "
                "(not from C3 output). Superglobal inputs are strings; an 'array' input is an object with the "
                "listed keys. Delete a case you do not want; a case with any null value is skipped. hostile_* and "
                "type_mismatch are pre-filled from docs/E1_equivalence_plan.md section 3; review or delete them."
            ),
            "sites": entries,
        }
        target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ready = sum(1 for e in entries if isinstance(e["cases"], dict))
        print(f"{app}: {len(entries)} sites, {ready} need cases -> {target.relative_to(ROOT)}")


# ------------------------------------------------------------------ databases

def _mysql_env() -> dict:
    env = dict(os.environ)
    env["MYSQL_PWD"] = os.environ["C3_DB_PASSWORD"]
    return env


USE_OR_CREATE_DB = re.compile(r"^\s*(CREATE\s+DATABASE|USE\s)", re.IGNORECASE)


def reset_database(app: str, database: str) -> None:
    schema = (APPS_DIR / APPS[app][1]).read_text(encoding="utf-8-sig", errors="replace")
    lines = [line for line in schema.splitlines() if not USE_OR_CREATE_DB.match(line)]
    if APPS[app][2]:
        lines += (CASES_DIR / APPS[app][2]).read_text(encoding="utf-8").splitlines()
    script = f"DROP DATABASE IF EXISTS `{database}`;\nCREATE DATABASE `{database}` CHARACTER SET utf8mb4;\nUSE `{database}`;\n"
    script += "\n".join(lines) + "\n"
    completed = subprocess.run(
        [MYSQL_CLIENT, "-u", "c3_tester", "-h", "127.0.0.1", "--default-character-set=utf8mb4"],
        input=script, capture_output=True, text=True, encoding="utf-8", env=_mysql_env(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"reset of {database} failed: {completed.stderr[-400:]}")


def engine_for(database: str):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    return create_engine(URL.create(
        "mysql+pymysql", username="c3_tester", password=os.environ["C3_DB_PASSWORD"],
        host="127.0.0.1", database=database, query={"charset": "utf8mb4"},
    ))


def checksums(engine) -> dict:
    from sqlalchemy import inspect, text

    tables = sorted(inspect(engine).get_table_names())
    if not tables:
        return {}
    with engine.connect() as connection:
        rows = connection.execute(text("CHECKSUM TABLE " + ", ".join(f"`{t}`" for t in tables))).all()
    return {row[0].split(".", 1)[-1]: row[1] for row in rows}


# --------------------------------------------------------------------- running

def filled_cases(entry: dict) -> dict:
    if not isinstance(entry.get("cases"), dict):
        return {}
    def complete(value):
        return value is not None and (not isinstance(value, dict) or all(v is not None for v in value.values()))
    return {name: inputs for name, inputs in entry["cases"].items() if all(complete(v) for v in inputs.values())}


def evaluate_app(app: str, out_dir: Path, execute: bool) -> dict:
    case_file = CASES_DIR / f"{app}.json"
    if not case_file.exists():
        raise SystemExit(f"{case_file} is missing: run the skeleton command first")
    entries = json.loads(case_file.read_text(encoding="utf-8"))["sites"]
    redact = APPS[app][3]

    # Generated code stays outside evidence/ (HMS has no licence).
    c3_out = Path(tempfile.mkdtemp(prefix=f"c3_e1_{app}_")) / app
    run_c3(app, c3_out)
    records = c3_records(c3_out, app)
    module = migrated_runner.load_module(c3_out / "data_access.py")

    legacy_db, migrated_db = f"c3_e1_{app}_legacy", f"c3_e1_{app}_migrated"
    engines = (engine_for(legacy_db), engine_for(migrated_db)) if execute else (None, None)

    results = []
    for entry in entries:
        record = records.get((entry["file"].lower(), entry["line"]), {})
        site = {k: entry[k] for k in ("site_id", "file", "line", "call")}
        site.update({
            "c3_query_id": record.get("query_id"),
            "c3_recovery_status": record.get("recovery_status"),
            "c3_conversion_status": record.get("conversion_status"),
            "c3_conversion_rule": record.get("conversion_rule"),
            "cases": [],
        })
        if not record:
            site.update(verdict="INCONCLUSIVE", reason="site not detected by C3")
        elif record.get("conversion_status") != "CONVERTED":
            site.update(verdict="INCONCLUSIVE", reason=(
                f"C3 did not convert: {record.get('recovery_status')}/{record.get('conversion_status')} "
                f"{record.get('recovery_reason') or record.get('conversion_reason') or ''}").strip())
        else:
            function = migrated_runner.function_for(module, record["query_id"])
            site["generated_function"] = function.__name__ if function else None
            cases = filled_cases(entry)
            if function is None:
                site.update(verdict="INCONCLUSIVE", reason="generated function not found")
            elif not cases:
                site.update(verdict="INCONCLUSIVE", reason="no complete input case in the case file")
            else:
                entry = {**entry, "_sources": record.get("dynamic_variable_sources", {})}
                for name, inputs in cases.items():
                    site["cases"].append(run_case(app, entry, name, inputs, function, engines, execute, redact))
                site["verdict"] = (
                    compare.site_verdict(site["cases"]) if execute
                    else ("READY" if all(c["verdict"] == "READY" for c in site["cases"]) else "NOT_READY")
                )
                site["legacy_injectable"] = any(c["verdict"] == "LEGACY_INJECTABLE" for c in site["cases"])
                site["normal_case_run"] = any(c["case"] == "normal" for c in site["cases"])
        results.append(site)
        print(f"  {app} {site['site_id']} {site['file']}:{site['line']} -> {site['verdict']}")

    for engine in engines:
        if engine is not None:
            engine.dispose()

    counts = {}
    for site in results:
        counts[site["verdict"]] = counts.get(site["verdict"], 0) + 1
    return {"app": app, "sites": len(results), "verdicts": counts, "redacted": redact, "results": results}


def run_case(app, entry, name, inputs, function, engines, execute, redact) -> dict:
    raw = legacy(app, entry, inputs, "raw", execute=False)
    if raw.get("status") != "built":
        return {"case": name, "verdict": "INCONCLUSIVE", "reason": f"raw build: {raw.get('status')} {raw.get('error', '')}".strip()}
    sources = entry.get("_sources", {})
    arguments, missing = migrated_runner.bind(function, raw["variables"], sources, raw.get("arrays"))

    if not execute:
        built = legacy(app, entry, inputs, "legacy", execute=False)
        verdict = "READY" if built.get("status") == "built" and not missing else "NOT_READY"
        record = {"case": name, "verdict": verdict, "unbound_parameters": missing}
        if not redact:
            record.update(legacy_sql=built.get("sql"), arguments=arguments)
        return record

    legacy_engine, migrated_engine = engines
    reset_database(app, f"c3_e1_{app}_legacy")
    reset_database(app, f"c3_e1_{app}_migrated")
    before = checksums(migrated_engine)

    legacy_result = legacy(app, entry, inputs, "legacy", execute=True, database=f"c3_e1_{app}_legacy")
    legacy_result["checksums_after"] = checksums(legacy_engine)
    migrated_result = migrated_runner.call(function, migrated_engine, raw["variables"], sources, raw.get("arrays"))
    migrated_result["checksums_after"] = checksums(migrated_engine)

    record = compare.compare_case(name, legacy_result, migrated_result, inputs)
    record["migrated_state_changed"] = before != migrated_result["checksums_after"]
    record["legacy_status"] = legacy_result.get("status")
    record["migrated_status"] = migrated_result.get("status")
    if not redact:
        record["inputs"] = inputs
        record["legacy_sql"] = legacy_result.get("sql")
        record["arguments"] = migrated_result.get("arguments")
        for key in ("error",):
            if legacy_result.get(key):
                record["legacy_error"] = legacy_result[key]
            if migrated_result.get(key):
                record["migrated_error"] = migrated_result[key]
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["skeleton", "dry-run", "run"])
    parser.add_argument("--apps", default=",".join(APPS))
    parser.add_argument("--out")
    parser.add_argument("--force", action="store_true", help="skeleton: overwrite existing case files")
    args = parser.parse_args()
    apps = [a for a in args.apps.split(",") if a]

    if args.command == "skeleton":
        skeleton(apps, args.force)
        return 0

    execute = args.command == "run"
    if execute and not os.environ.get("C3_DB_PASSWORD"):
        raise SystemExit("C3_DB_PASSWORD is not set")
    if not args.out:
        raise SystemExit("--out is required")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    summary = {
        "experiment": "E1 PHP vs generated SQLAlchemy equivalence" + ("" if execute else " (dry run: no database)"),
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": head,
        "mysql": "c3_e1_<app>_legacy / c3_e1_<app>_migrated, rebuilt before every case" if execute else None,
        "apps": {},
    }
    for app in apps:
        print(f"=== {app}")
        report = evaluate_app(app, out_dir, execute)
        report["c3_output"] = "regenerated from the current code into a temp folder (not kept)"
        (out_dir / f"{app}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        summary["apps"][app] = {"sites": report["sites"], "verdicts": report["verdicts"]}

    name = "php_vs_sqlalchemy_equivalence.json" if execute else "e1_dry_run.json"
    (out_dir / name).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["apps"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
