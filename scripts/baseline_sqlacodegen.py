"""sqlacodegen schema-only baseline: an EVALUATION comparison, not part of the C3 pipeline.

For each of the 9 apps, sqlacodegen reflects the app's existing E2 database (c3_e3_<app>, loaded
with the app's original schema by the last E2 run) and writes SQLAlchemy models. Those models are
compared with the models.py C3 generates for the same app: tables, columns, primary keys,
nullability, foreign keys and column types. sqlacodegen reads only the database; it never reads the
PHP code, so the C3 query-level counts (live sites, recovered sites, data-access functions, manual
review, abstentions) are recorded for C3 only and are "not applicable" for sqlacodegen.

Two Python environments are used:
  * the component venv runs this script, C3 and the comparison;
  * a separate venv OUTSIDE the repo holds sqlacodegen (never a C3 dependency). This script calls
    itself there (`generate`) to reflect one database. The password is read from C3_DB_PASSWORD,
    passed to the driver in-process (never on a command line) and never printed or written.
    Every connection is put in a READ ONLY session before reflection; nothing is created or dropped.

Usage (repo root, component venv; C3_DB_PASSWORD set for this terminal only):
    python scripts/baseline_sqlacodegen.py --sqlacodegen-python <venv>\\Scripts\\python.exe
        [--out evidence/baseline/2026-10-19] [--apps hms_kishan0725,...] [--c3-runs <existing run folder>]

The output folder must not exist; results are written to a temp folder first and moved into
place at the end, so an interrupted run leaves nothing behind. Failures are recorded, not hidden:
the exit code is 1 if sqlacodegen failed for any app.
"""

import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_OUT = ROOT / "evidence" / "baseline" / "2026-10-19"
DEFAULT_SQLACODEGEN_PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "c3_sqlacodegen_venv" / "Scripts" / "python.exe"
GATE_HASHES = ROOT / "evidence" / "final" / "2026-10-19" / "gate" / "final_2026-10-19.sha256.json"
DB_USER, DB_HOST = "c3_tester", "127.0.0.1"
GENERATOR = "declarative"  # sqlacodegen's default generator, default options
NOT_APPLICABLE = "not applicable (sqlacodegen reads only the database schema, never the PHP code)"
DIMENSIONS = ("tables", "columns", "primary_keys", "nullability", "types", "foreign_keys")
C3_FILES = ("models.py", "data_access.py", "queries.json", "unresolved_queries.json", "summary.json")

# C3 run folder -> the existing E2 database (scripts/schema_comparison.py: c3_e3_<app key>).
E2_DATABASES = {
    "hms_kishan0725": "c3_e3_hms_kishan0725",
    "dignity_dev_hms": "c3_e3_dignity_dev_hms",
    "helpdesk_legacy_holdout": "c3_e3_helpdesk",
    "news_buzz": "c3_e3_news_buzz",
    "fee_rainbow": "c3_e3_fee_rainbow",
    "clinic_inventory_legacy_hard": "c3_e3_clinic",
    "simple_crud": "c3_e3_simple_crud",
    "mixed_crud": "c3_e3_mixed_crud",
    "hard_crud": "c3_e3_hard_crud",
}


def sanitise(text: str) -> str:
    """Remove the password (and any user:password@ URL part) from text before it is stored or shown."""

    password = os.environ.get("C3_DB_PASSWORD")
    if password:
        text = text.replace(password, "***")
    return re.sub(r"://[^:/@\s]+:[^@\s]+@", "://***:***@", text)


# ------------------------------------------------------- generate (sqlacodegen venv)

def generate(database: str, outfile: Path, infofile: Path) -> None:
    """Reflect one database read-only with sqlacodegen's declarative generator (same calls as its CLI)."""

    from importlib.metadata import entry_points
    from sqlalchemy import MetaData, create_engine, text
    from sqlalchemy.engine import URL

    url = URL.create("mysql+pymysql", username=DB_USER, password=os.environ["C3_DB_PASSWORD"], host=DB_HOST,
                     database=database)
    engine = create_engine(url, connect_args={"init_command": "SET SESSION TRANSACTION READ ONLY"})
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT @@SESSION.transaction_read_only")).scalar() != 1:
                raise RuntimeError("the session is not read-only; refusing to reflect")
            server = connection.execute(text("SELECT VERSION()")).scalar()
            # Schema facts only: no TABLE_ROWS, no AUTO_INCREMENT, no row data.
            rows = connection.execute(text(
                "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, CREATE_TIME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = :db ORDER BY TABLE_NAME"), {"db": database}).all()
        if not rows:
            raise RuntimeError(f"database {database} has no tables (it is loaded by scripts/run_e2_mysql.ps1); "
                               "nothing to reflect")
        created = sorted(str(r[3]) for r in rows if r[3] is not None)
        info = {"database": database, "mysql_server": server,
                "tables": {r[0]: {"type": r[1], "engine": r[2]} for r in rows},
                "tables_created": {"first": created[0] if created else None, "last": created[-1] if created else None}}

        generator_class = next(ep for ep in entry_points(group="sqlacodegen.generators") if ep.name == GENERATOR).load()
        metadata = MetaData()
        generator = generator_class(metadata, engine, set())
        metadata.reflect(engine, None, generator.views_supported, None)
        code = generator.generate()
    finally:
        engine.dispose()
    outfile.write_text(code, encoding="utf-8")
    infofile.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def tool_versions(python: Path) -> dict:
    probe = ("import importlib.metadata as m, json, platform; "
             "print(json.dumps({'python': platform.python_version(), "
             "**{d: m.version(d) for d in ('sqlacodegen', 'SQLAlchemy', 'PyMySQL')}}))")
    done = subprocess.run([str(python), "-I", "-B", "-c", probe], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"cannot read versions from {python}: {sanitise(done.stderr)[-600:]}")
    return json.loads(done.stdout)


def run_generate(python: Path, database: str, outfile: Path, infofile: Path) -> str | None:
    """Run `generate` in the sqlacodegen venv; return the sanitised error text, or None on success."""

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([str(python), "-I", "-B", str(Path(__file__).resolve()), "generate", "--database", database,
                           "--outfile", str(outfile), "--infofile", str(infofile)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if done.returncode == 0 and outfile.exists():
        return None
    return sanitise((done.stderr or done.stdout or f"exit code {done.returncode}, no output").strip())


# ------------------------------------------------------------ compare (component venv)

_module_counter = itertools.count()


def _norm_type(rendered: str) -> str:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from schema_comparison import norm_type  # E2's normaliser, so both studies spell types the same way

    return norm_type(rendered)


def normalise(metadata) -> dict:
    """Table -> columns (MySQL-rendered type, nullability), primary key and foreign keys. Names lower-cased."""

    from sqlalchemy.dialects import mysql

    dialect = mysql.dialect()
    tables = {}
    for table in metadata.tables.values():
        columns = {}
        for column in table.columns:
            try:
                rendered = str(column.type.compile(dialect=dialect))
            except Exception:  # a type MySQL cannot render; keep its own spelling
                rendered = getattr(column.type, "ddl", None) or type(column.type).__name__
            columns[column.name.lower()] = {"type": _norm_type(rendered), "rendered": rendered,
                                            "nullable": bool(column.nullable)}
        foreign_keys = set()
        for constraint in table.foreign_key_constraints:
            targets = [element.target_fullname.lower().rsplit(".", 2)[-2:] for element in constraint.elements]
            foreign_keys.add((tuple(element.parent.name.lower() for element in constraint.elements),
                              targets[0][0], tuple(t[1] for t in targets)))
        tables[table.name.lower()] = {"columns": columns,
                                      "primary_key": sorted(c.name.lower() for c in table.primary_key.columns),
                                      "foreign_keys": sorted(foreign_keys)}
    return tables


def load_models(models_file: Path) -> dict:
    """Import a generated models file, normalise its single MetaData, then dispose its mappers."""

    from sqlalchemy import MetaData

    name = f"baseline_models_{next(_module_counter)}"
    spec = importlib.util.spec_from_file_location(name, models_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # string annotations are resolved through sys.modules
    try:
        spec.loader.exec_module(module)
        metadatas, registries = {}, {}
        for value in vars(module).values():
            if isinstance(value, MetaData):
                metadatas[id(value)] = value
            elif isinstance(value, type):
                if isinstance(getattr(value, "metadata", None), MetaData):
                    metadatas[id(value.metadata)] = value.metadata
                registry = getattr(value, "registry", None)
                if registry is not None and hasattr(registry, "dispose"):
                    registries[id(registry)] = registry
        if len(metadatas) != 1:
            raise ValueError(f"expected one SQLAlchemy MetaData in {models_file.name}, found {len(metadatas)}")
        tables = normalise(next(iter(metadatas.values())))
        for registry in registries.values():
            registry.dispose()
        return tables
    finally:
        sys.modules.pop(name, None)


def _fk_text(fk) -> dict:
    return {"columns": list(fk[0]), "referred_table": fk[1], "referred_columns": list(fk[2])}


def compare_models(c3: dict, sqlacodegen: dict) -> dict:
    """Matches and differences per dimension. Neither side is ground truth; both are listed as found."""

    counts = {d: {"compared": 0, "matches": 0, "differences": 0} for d in DIMENSIONS}
    differences = []

    def count(dimension, same, entry=None):
        counts[dimension]["compared"] += 1
        counts[dimension]["matches" if same else "differences"] += 1
        if not same:
            differences.append({"dimension": dimension, **entry})

    for table in sorted(set(c3) | set(sqlacodegen)):
        if table not in c3 or table not in sqlacodegen:
            side = "c3" if table in c3 else "sqlacodegen"
            count("tables", False, {"table": table, "only_in": side,
                                    "columns_not_compared": len((c3.get(table) or sqlacodegen[table])["columns"])})
            continue
        count("tables", True)
        a, b = c3[table], sqlacodegen[table]
        for column in sorted(set(a["columns"]) | set(b["columns"])):
            if column not in a["columns"] or column not in b["columns"]:
                count("columns", False, {"table": table, "column": column,
                                         "only_in": "c3" if column in a["columns"] else "sqlacodegen"})
                continue
            count("columns", True)
            x, y = a["columns"][column], b["columns"][column]
            count("nullability", x["nullable"] == y["nullable"],
                  {"table": table, "column": column, "c3": x["nullable"], "sqlacodegen": y["nullable"]})
            count("types", x["type"] == y["type"],
                  {"table": table, "column": column, "c3": x["type"], "sqlacodegen": y["type"],
                   "c3_rendered": x["rendered"], "sqlacodegen_rendered": y["rendered"]})
        count("primary_keys", a["primary_key"] == b["primary_key"],
              {"table": table, "c3": a["primary_key"], "sqlacodegen": b["primary_key"]})
        fa, fb = set(map(tuple, a["foreign_keys"])), set(map(tuple, b["foreign_keys"]))
        for fk in sorted(fa | fb):
            count("foreign_keys", fk in fa and fk in fb,
                  {"table": table, "foreign_key": _fk_text(fk), "only_in": "c3" if fk in fa else "sqlacodegen"})
    verdict = "MATCH" if not differences else f"{len(differences)} difference(s)"
    return {"verdict": verdict, "counts": counts, "differences": differences}


def annotate(comparison: dict, database_tables: dict) -> list[str]:
    """Explain differences that come from the database rather than from either tool; return app-level notes."""

    tables = {name.lower(): facts for name, facts in database_tables.items()}
    for diff in comparison["differences"]:
        facts = tables.get(diff["table"], {})
        if diff["dimension"] == "tables" and diff["only_in"] == "sqlacodegen" and facts.get("type") == "VIEW":
            diff["note"] = "a VIEW in the database; sqlacodegen reflects views by default, C3 models base tables"
        if diff["dimension"] == "foreign_keys" and diff["only_in"] == "c3":
            engines = {(facts.get("engine") or "").upper(),
                       (tables.get(diff["foreign_key"]["referred_table"], {}).get("engine") or "").upper()}
            if "MYISAM" in engines:
                diff["note"] = ("MyISAM table: MySQL does not store foreign keys for MyISAM, so no reflection "
                                "tool can see this key; the comparison of this key is not meaningful")
    notes = []
    myisam = sorted(n for n, f in tables.items() if (f.get("engine") or "").upper() == "MYISAM")
    if myisam:
        notes.append(f"{len(myisam)} table(s) use MyISAM ({', '.join(myisam)}): MySQL keeps no foreign keys for them, "
                     "so foreign-key differences on these tables say nothing about either tool")
    views = sorted(n for n, f in tables.items() if f.get("type") == "VIEW")
    if views:
        notes.append(f"database views reflected by sqlacodegen: {', '.join(views)}")
    return notes


# -------------------------------------------------------------- C3-only counts

def c3_counts(run: Path, review: dict) -> dict:
    """C3 query-level counts for one app, copied from its run outputs (summary.json, unresolved_queries.json)."""

    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    abstentions = [dict(e, live=True) for e in review["sites"]] + [dict(e, live=False) for e in review["excluded_non_live"]]
    return {
        "label": review["label"],
        "live_query_sites": review["live_sites"],
        "live_query_sites_source": review["live_sites_source"],
        "detected_sites": summary["total_queries"],
        "recovered_sites": summary["recovered_queries"],
        "recovered_sites_note": "summary.json recovered_queries: all detected sites, including any non-live ones",
        "data_access_functions_generated": summary["converted_queries"],
        "data_access_functions_breakdown": {k: summary[k] for k in ("converted_by_general_rules",
                                                                    "converted_by_query_specific_template")},
        "manual_review_count": review["not_converted_live"],
        "manual_review_rate": review["value"],
        "abstentions": {
            "total": summary["unresolved_total"],
            "live": len(review["sites"]),
            "non_live": len(review["excluded_non_live"]),
            "by_stage": dict(Counter(e["stage"] for e in abstentions)),
            "by_reason_code": dict(sorted(Counter(e["reason_code"] for e in abstentions).items())),
            # Reason codes only (no reason text): HMS has no licence, so free text from its code is not exported.
            "sites": [{k: e.get(k) for k in ("query_id", "gt_id", "site", "stage", "reason_code", "live")}
                      for e in abstentions],
        },
        "source": "C3 run outputs summary.json, unresolved_queries.json, queries.json (manual review as in "
                  "scripts/summarize_evidence.py compute_manual_review)",
    }


def sqlacodegen_not_applicable() -> dict:
    """The C3 query-level fields for sqlacodegen: not applicable (it never reads PHP), never zero."""

    return {k: NOT_APPLICABLE for k in ("live_query_sites", "recovered_sites", "data_access_functions_generated",
                                         "manual_review_count", "abstentions")}


def gate_check(folder: str, run: Path, gate: dict | None) -> dict:
    """Hash the C3 outputs used here and compare them with the 2026-10-19 byte-identity gate record."""

    hashes = {}
    for name in C3_FILES:
        data = (run / name).read_bytes()
        for variant in {str(run), str(run).replace("\\", "/"), json.dumps(str(run))[1:-1]}:
            data = data.replace(variant.encode("utf-8"), b"<RUN>")
        hashes[name] = hashlib.sha256(data).hexdigest()
    if gate is None:
        return {"sha256_normalised": hashes, "matches_gate_2026_10_19": "gate record not found"}
    same = all(gate.get(f"{folder}/{name}", {}).get("normalised") == h for name, h in hashes.items())
    return {"sha256_normalised": hashes, "matches_gate_2026_10_19": same}


# -------------------------------------------------------------------- reports

def _cell(counts: dict) -> str:
    return f"{counts['matches']}/{counts['differences']}"


def write_text_summary(report: dict, path: Path) -> None:
    lines = [report["title"], "", f"Run: {report['generated']['date_utc']}  git HEAD {report['generated']['git_head']}",
             f"sqlacodegen {report['tools']['sqlacodegen']['sqlacodegen']} (SQLAlchemy "
             f"{report['tools']['sqlacodegen']['SQLAlchemy']}, PyMySQL {report['tools']['sqlacodegen']['PyMySQL']})", "",
             "Model parity, C3 models.py vs sqlacodegen (matches/differences):", ""]
    header = ["app", "status"] + list(DIMENSIONS)
    rows = []
    for app, entry in report["apps"].items():
        comparison = entry.get("comparison")
        if comparison and "counts" in comparison:
            rows.append([app, comparison["verdict"]] + [_cell(comparison["counts"][d]) for d in DIMENSIONS])
        else:
            rows.append([app, entry["status"]] + ["-"] * len(DIMENSIONS))
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        lines.append("  " + "  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    lines += ["", "Differences:"]
    for app, entry in report["apps"].items():
        comparison = entry.get("comparison") or {}
        for note in entry.get("notes", []):
            lines.append(f"  {app}: NOTE {note}")
        for d in comparison.get("differences", []):
            detail = {k: v for k, v in d.items() if k not in ("dimension", "table")}
            lines.append(f"  {app}: {d['dimension']} {d['table']} {json.dumps(detail, default=str)}")
        if entry["status"] != "compared":
            lines.append(f"  {app}: NOT COMPARED - {entry.get('reason')}")
    lines += ["", "sqlacodegen failures:"]
    failures = [(a, e) for a, e in report["apps"].items() if e["sqlacodegen"]["status"] == "failed"]
    lines += [f"  {a}: {e['sqlacodegen']['error'].splitlines()[-1] if e['sqlacodegen']['error'] else ''}"
              for a, e in failures] or ["  none"]
    lines += ["", "C3 only (sqlacodegen: " + NOT_APPLICABLE + "):", ""]
    header = ["app", "live sites", "recovered", "functions", "manual review", "abstentions (live/non-live)", "label"]
    rows = []
    for app, entry in report["apps"].items():
        c = entry["c3_only"]
        rows.append([app, c["live_query_sites"], c["recovered_sites"], c["data_access_functions_generated"],
                     f"{c['manual_review_count']} "
                     + ("(no live sites)" if c["manual_review_rate"] is None else f"({c['manual_review_rate']}%)"),
                     f"{c['abstentions']['total']} ({c['abstentions']['live']}/{c['abstentions']['non_live']})",
                     c["label"] or "verified ground truth"])
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        lines.append("  " + "  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    lines += ["", "Abstention reason codes (C3):"]
    for app, entry in report["apps"].items():
        lines.append(f"  {app}: {json.dumps(entry['c3_only']['abstentions']['by_reason_code'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_readme(report: dict, path: Path) -> None:
    tools = report["tools"]["sqlacodegen"]
    text = f"""# sqlacodegen schema-only baseline ({path.parent.name})

**This is an evaluation comparison. It is not used by the C3 pipeline**, and nothing in
`components/c3_data_layer` imports or depends on sqlacodegen.

sqlacodegen is a models-only tool: it reflects an existing database and writes SQLAlchemy models.
It never reads PHP code, so it recovers no queries, generates no data-access functions and makes no
abstentions. Where C3 produces more (query recovery, data-access functions, manual-review lists), that
is **by design of the two tools, not a win for C3**. The like-for-like part is the model layer: tables,
columns, primary keys, nullability, foreign keys and column types. C3 query counts are recorded for C3
only; for sqlacodegen they are written "not applicable", never zero.

## Version, date, inputs

- sqlacodegen **{tools['sqlacodegen']}**, SQLAlchemy {tools['SQLAlchemy']}, PyMySQL {tools['PyMySQL']},
  Python {tools['python']}, in a separate venv outside the repo (`{report['tools']['sqlacodegen_venv']}`).
- Generator: `{GENERATOR}` with default options (views included, as the CLI does by default).
- Run: {report['generated']['date_utc']} (UTC), git HEAD `{report['generated']['git_head']}`.
- Databases: the existing E2 databases `c3_e3_<app>` (loaded with each app's original schema file by
  `scripts/run_e2_mysql.ps1`), reflected read-only as `c3_tester`. Every connection runs
  `SET SESSION TRANSACTION READ ONLY` and the script checks `@@SESSION.transaction_read_only = 1` before
  reflecting. No database, table or grant is created, changed or dropped. Only schema facts are read
  (information_schema table name, type, engine, create time); no rows, row counts or AUTO_INCREMENT values.
- C3 side: {report['tools']['c3']['run']}. The hashes of the C3 files used are compared with the
  2026-10-19 byte-identity gate record (`{GATE_HASHES.relative_to(ROOT).as_posix()}`), per app in `summary.json`.

## Commands

```powershell
# once: the separate venv (outside the repo)
python -m venv "$env:LOCALAPPDATA\\c3_sqlacodegen_venv"
& "$env:LOCALAPPDATA\\c3_sqlacodegen_venv\\Scripts\\python.exe" -m pip install sqlacodegen=={tools['sqlacodegen']} PyMySQL=={tools['PyMySQL']} SQLAlchemy=={tools['SQLAlchemy']}

# each run (repo root); the password is typed, kept in this terminal only, never written
$env:C3_DB_PASSWORD = Read-Host "c3_tester password"
$env:PYTHONDONTWRITEBYTECODE = "1"
components\\c3_data_layer\\.venv\\Scripts\\python.exe scripts\\baseline_sqlacodegen.py --sqlacodegen-python "$env:LOCALAPPDATA\\c3_sqlacodegen_venv\\Scripts\\python.exe"
Remove-Item Env:\\C3_DB_PASSWORD
```

Per app the script does in-process what the CLI command
`sqlacodegen --generator {GENERATOR} mysql+pymysql://c3_tester:<password>@127.0.0.1/c3_e3_<app>` does
(`MetaData.reflect` + `DeclarativeGenerator.generate`), so the password never appears on a command line.

## How the comparison works

Both model files are imported and their SQLAlchemy `MetaData` is normalised the same way: names
lower-cased (MySQL on Windows stores table names in lower case), each column type rendered for MySQL
and normalised with E2's `norm_type` (`scripts/schema_comparison.py`; e.g. `INT(11)` -> `INT`, collations
dropped), nullability, primary-key columns, and foreign keys as (columns, referenced table, referenced columns).

| dimension | compared over | a match means |
|---|---|---|
| tables | union of table names | the table is in both |
| columns | union of columns of tables in both | the column is in both |
| primary_keys | tables in both | same primary-key columns |
| nullability | columns in both | same nullable flag |
| types | columns in both | same normalised MySQL type |
| foreign_keys | union of foreign keys of tables in both | the key is in both |

Neither side is ground truth: every difference is listed with both values. Schema fidelity against the
original schema is measured by E2 (`evidence/mysql_runs/<run>/e2/`); this baseline compares two tools'
output for the same schema. Differences the database itself explains (MyISAM tables keep no foreign
keys; views) are annotated in `summary.json`, not removed. Unique constraints, indexes, defaults and
relationships are not compared.

## Files

- `<app>/sqlacodegen_models.py`: sqlacodegen output, unchanged. On failure, `<app>/sqlacodegen_error.txt`.
- `summary.json`: per app the comparison counts and every difference, database facts, the C3-only counts
  and abstentions (reason codes only), the gate check, and any failure with its error text.
- `summary.txt`: the same, as tables.
"""
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    gen = sub.add_parser("generate", help="internal: reflect one database (runs in the sqlacodegen venv)")
    gen.add_argument("--database", required=True)
    gen.add_argument("--outfile", type=Path, required=True)
    gen.add_argument("--infofile", type=Path, required=True)
    parser.add_argument("--sqlacodegen-python", type=Path, default=DEFAULT_SQLACODEGEN_PYTHON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--apps", default=",".join(E2_DATABASES))
    parser.add_argument("--c3-runs", type=Path, help="existing C3 run folder with one sub-folder per app "
                                                    "(default: a fresh current-code run into a temp folder)")
    args = parser.parse_args(argv)

    if args.command == "generate":
        try:
            generate(args.database, args.outfile, args.infofile)
        except Exception:
            print(sanitise(traceback.format_exc()), file=sys.stderr)
            return 1
        return 0

    out = args.out if args.out.is_absolute() else ROOT / args.out
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite (move it away to rerun)")
    if not os.environ.get("C3_DB_PASSWORD"):
        raise SystemExit('C3_DB_PASSWORD is not set. Run: $env:C3_DB_PASSWORD = Read-Host "c3_tester password"')
    if not args.sqlacodegen_python.exists():
        raise SystemExit(f"sqlacodegen venv python not found: {args.sqlacodegen_python}")
    apps = [a for a in args.apps.split(",") if a]
    unknown = [a for a in apps if a not in E2_DATABASES]
    if unknown:
        raise SystemExit(f"unknown app(s): {unknown}; choose from {list(E2_DATABASES)}")

    sys.path.insert(0, str(SCRIPTS))
    import summarize_evidence as evidence

    versions = tool_versions(args.sqlacodegen_python)
    head = evidence.git("rev-parse", "HEAD")
    evidence.pipeline_unchanged_since(head)
    if args.c3_runs:
        base, run_note = args.c3_runs.resolve(), "existing C3 run outputs (--c3-runs)"
    else:
        base, run_note = evidence.run_current_code(), "fresh current-code C3 run into a temp folder outside the repo"
    review = evidence.compute_manual_review(base)
    labels = {folder: label for label, folder, *_ in evidence.CURRENT_RUNS}
    gate = json.loads(GATE_HASHES.read_text(encoding="utf-8")) if GATE_HASHES.exists() else None

    report = {
        "title": "sqlacodegen schema-only baseline: evaluation comparison, not used by the C3 pipeline",
        "generated": {"date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_head": head,
                      "script": "scripts/baseline_sqlacodegen.py"},
        "tools": {"sqlacodegen": versions, "sqlacodegen_venv": str(args.sqlacodegen_python.parent.parent),
                  "generator": GENERATOR, "c3": {"git_head": head, "run": run_note}},
        "not_applicable_for_sqlacodegen": NOT_APPLICABLE,
        "apps": {},
    }
    staging = Path(tempfile.mkdtemp(prefix="c3_baseline_"))
    failures = 0
    try:
        for folder in apps:
            database = E2_DATABASES[folder]
            app_dir = staging / folder
            app_dir.mkdir()
            models_out, info_out = app_dir / "sqlacodegen_models.py", staging / f"{folder}_database.json"
            print(f"{folder}: reflecting {database} read-only with sqlacodegen ...", flush=True)
            error = run_generate(args.sqlacodegen_python, database, models_out, info_out)
            entry = {"label": labels[folder], "database": database, "c3_run": "<temp>/" + folder,
                     "c3_outputs": gate_check(folder, base / folder, gate),
                     "c3_only": c3_counts(base / folder, review[folder]),
                     "sqlacodegen_only": sqlacodegen_not_applicable()}
            if error:
                failures += 1
                models_out.unlink(missing_ok=True)
                (app_dir / "sqlacodegen_error.txt").write_text(error + "\n", encoding="utf-8")
                entry.update(status="not compared", reason="sqlacodegen failed; see sqlacodegen.error",
                             sqlacodegen={"status": "failed", "error": error})
                print(f"{folder}: sqlacodegen FAILED: {error.splitlines()[-1] if error else ''}")
            else:
                info = json.loads(info_out.read_text(encoding="utf-8"))
                text = models_out.read_text(encoding="utf-8")
                entry["sqlacodegen"] = {"status": "ok", "error": None, "models_file": f"{folder}/sqlacodegen_models.py",
                                        "models_sha256": hashlib.sha256(models_out.read_bytes()).hexdigest(),
                                        "mentions_auto_increment": bool(re.search("auto_increment", text, re.I)),
                                        "database_facts": info}
                try:
                    c3_tables, sqla_tables = load_models(base / folder / "models.py"), load_models(models_out)
                except Exception as exc:  # a model file that does not import cannot be compared
                    entry.update(status="not compared", reason=sanitise(f"model import failed: {type(exc).__name__}: {exc}"))
                else:
                    comparison = compare_models(c3_tables, sqla_tables)
                    entry["notes"] = annotate(comparison, info["tables"])
                    entry["status"] = "compared"
                    entry["comparison"] = comparison
                    print(f"{folder}: {comparison['verdict']} "
                          + " ".join(f"{d} {_cell(comparison['counts'][d])}" for d in DIMENSIONS))
            report["apps"][folder] = entry
        report["failures"] = {a: e["sqlacodegen"]["error"] for a, e in report["apps"].items()
                              if e["sqlacodegen"]["status"] == "failed"}
        (staging / "summary.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        write_text_summary(report, staging / "summary.txt")
        write_readme(report, staging / "README.md")
        for leftover in staging.glob("*_database.json"):  # folded into summary.json
            leftover.unlink()
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shown = out.relative_to(ROOT) if ROOT in out.parents else out
    print(f"wrote {shown} ({len(apps)} apps, {failures} sqlacodegen failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
