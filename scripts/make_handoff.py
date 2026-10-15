"""Integration handoff for one C3 output folder (H4).

Writes <output>/<APP>_HANDOFF.md and <output>/signatures.json from the
generated files, and checks that data_access.py and models.py run with C3
blocked (a child process where `import c3` fails).

Usage (repo root, component venv):
    python scripts/make_handoff.py <output_dir> --php <php root> --schema <schema.sql> --title "HMS (kishan0725)" --name HMS
"""

import argparse
import importlib.util
import inspect
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schema_comparison import query_schema_drift  # noqa: E402

STANDALONE_CHILD = r"""
import importlib.util, inspect, json, sys
sys.modules["c3"] = None  # any import of C3 fails
from sqlalchemy.dialects import mysql

class Result:
    rowcount = 0
    lastrowid = 0
    def mappings(self): return self
    def all(self): return []

class Session:
    def __init__(self): self.statements = 0
    def execute(self, statement, params=None):
        statement.compile(dialect=mysql.dialect())
        self.statements += 1
        return Result()
    def commit(self): pass

out = {}
for name in ("models", "data_access"):
    spec = importlib.util.spec_from_file_location(name, sys.argv[1] + "/" + name + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out[name] = module

called, failures = 0, []
for name, function in inspect.getmembers(out["data_access"], inspect.isfunction):
    if function.__module__ != "data_access" or name.startswith("unresolved_query_"):
        continue
    params = [p for p, v in inspect.signature(function).parameters.items()
              if p != "session" and v.kind == v.POSITIONAL_OR_KEYWORD]
    if not name.rsplit("_", 1)[-1][1:].isdigit():
        continue
    try:
        session = Session()
        function(session, **{p: "1" for p in params})
        assert session.statements == 1
        called += 1
    except Exception as error:
        failures.append(f"{name}: {type(error).__name__}: {error}")
print(json.dumps({"tables": len(out["models"].Base.metadata.tables), "functions_called": called, "failures": failures}))
"""


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def return_shape(sql: str, operation: str, usage: dict) -> str:
    if operation != "SELECT":
        return "`CursorResult` (use `.rowcount`; `.lastrowid` after INSERT)"
    try:
        tree = sqlglot.parse_one(sql.replace(":", ":"), read="mysql")
        names = []
        for projection in tree.expressions:
            if isinstance(projection, exp.Star) or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)):
                names += usage.get("expanded_read_columns") or ["*"]
            else:
                names.append(projection.alias_or_name or projection.sql())
        return "list of row mappings: " + ", ".join(f"`{n}`" for n in dict.fromkeys(names))
    except Exception:
        return "list of row mappings"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--php", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--e1", help="E1 per-app report (run_e1.py output) for the per-function E1 status column")
    parser.add_argument("--e1-fix-note", default="", help="note for FAILs that a later harness fix reclassifies")
    args = parser.parse_args()

    out = Path(args.output)
    queries = {q["query_id"]: q for q in load(out / "queries.json")}
    converted = {q["query_id"]: q for q in load(out / "converted_queries.json")}
    usage = {u["query_id"]: u for u in load(out / "column_usage.json")}
    schema = load(out / "schema.json")
    unresolved = load(out / "unresolved_queries.json")
    summary = load(out / "summary.json")
    php_root = Path(args.php).resolve()

    spec = importlib.util.spec_from_file_location("handoff_data_access", out / "data_access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def relative(path: str) -> str:
        try:
            return Path(path).resolve().relative_to(php_root).as_posix()
        except ValueError:
            return Path(path).name

    functions = []
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if function.__module__ != module.__name__ or not name.rsplit("_", 1)[-1][1:].isdigit():
            continue
        query_id = name.rsplit("_", 1)[-1].upper()
        if name.startswith("unresolved_query_"):
            continue
        record = converted.get(query_id, {})
        signature = inspect.signature(function)
        positional = [p for p, v in signature.parameters.items() if p != "session" and v.kind == v.POSITIONAL_OR_KEYWORD]
        keyword_only = [f"{p}={v.default!r}" for p, v in signature.parameters.items() if v.kind == v.KEYWORD_ONLY]
        operation = record.get("operation") or queries[query_id].get("operation")
        sources = record.get("parameter_sources") or {}
        functions.append({
            "function": name,
            "query_id": query_id,
            "signature": f"{name}{signature}",
            "parameters": [{"name": p, "php_value": sources.get(p, f"${p}")} for p in positional],
            "keyword_only": keyword_only,
            "operation": operation,
            "returns": return_shape(record.get("parameterized_sql") or "", operation, usage.get(query_id, {})),
            "commit": ("commits by default; pass `commit=False` to leave the transaction to the caller"
                       if operation != "SELECT" else "read only (no commit)"),
            "php_site": f"{relative(queries[query_id]['source_file'])}:{queries[query_id]['source_line']}",
            "conversion_rule": record.get("conversion_rule"),
        })
    functions.sort(key=lambda f: f["query_id"])

    e1_status, e1_counts, e1_source = {}, {}, None
    if args.e1:
        report = load(Path(args.e1))
        e1_source = Path(args.e1).as_posix()
        drift_ids = {d["query_id"] for d in query_schema_drift(list(usage.values()), schema)}
        for site in report["results"]:
            e1_counts[site["verdict"]] = e1_counts.get(site["verdict"], 0) + 1
            cases = {c["case"]: c["verdict"] for c in site["cases"]}
            valid = [cases.get(k) for k in ("normal", "empty_result") if k in cases]
            if site["verdict"] == "PASS":
                text = "**PASS**"
            elif "INCONCLUSIVE" in valid:
                text = ("INCONCLUSIVE: schema drift (statement fails on both sides)" if site["c3_query_id"] in drift_ids
                        else "INCONCLUSIVE: the legacy statement fails under strict MySQL (missing NOT NULL value), same on both sides")
            elif site["verdict"] == "FAIL":
                text = ("valid inputs PASS; FAIL only on a hostile input that injected the legacy SQL"
                        + (f" ({args.e1_fix_note})" if args.e1_fix_note else ""))
            else:
                text = "valid inputs PASS; INCONCLUSIVE only on invalid input rejected by both sides"
            e1_status[site.get("generated_function")] = text

    drift = query_schema_drift(list(usage.values()), schema)
    pk_less = [t["name"] for t in schema["tables"] if not any(c["primary_key"] for c in t["columns"])]

    child = subprocess.run([sys.executable, "-c", STANDALONE_CHILD, str(out.resolve())], capture_output=True, text=True,
                           cwd=out, env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    standalone = json.loads(child.stdout) if child.returncode == 0 else {"error": child.stderr[-500:]}
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()

    (out / "signatures.json").write_text(json.dumps(
        {f["function"]: {"signature": f["signature"], "php_site": f["php_site"]} for f in functions},
        indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# {args.title}: C3 data-layer handoff",
        "",
        f"Generated {datetime.now(timezone.utc).date().isoformat()} from code `{head}` by `scripts/make_handoff.py`. "
        "Regenerate rather than edit. **Function signatures are frozen at tag `hms-handoff-v1`.**",
        "",
        "## Files",
        "",
        "| File | Use |",
        "|---|---|",
        "| `models.py` | SQLAlchemy models (ORM classes for tables with a PK, Core `Table` objects for the rest). |",
        "| `data_access.py` | One function per converted SQL site (below). Imports only SQLAlchemy and the standard library. |",
        "| `unresolved_queries.json` | SQL sites that need manual work. |",
        "| `alembic/` | Initial migration that creates the schema (E3 verified on MySQL 8). |",
        "| `signatures.json` | The frozen function signatures (checked by `tests/test_hms_handoff_signatures.py`). |",
        "",
        "## How to call",
        "",
        "```python",
        "from sqlalchemy import create_engine",
        "from sqlalchemy.orm import Session",
        "import data_access as da",
        "",
        "engine = create_engine(\"mysql+pymysql://user:password@host/myhmsdb\")",
        "with Session(engine) as session:",
        f"    rows = da.{functions[0]['function']}(session{''.join(', ' + p['name'] + '=...' for p in functions[0]['parameters'])})",
        "```",
        "",
        "- Pass the value the PHP code used, without SQL escaping (binding replaces `mysqli_real_escape_string`).",
        "- Parameters named `<ARRAY>_<key>` come from PHP array elements: `GET_ID` is `$_GET['ID']` and `SESSION_pid` is `$_SESSION['pid']`.",
        "- SELECT functions return `result.mappings().all()`: a list of dict-like rows keyed by the column names shown.",
        "- Write functions commit by default. Use `commit=False` and call `session.commit()` or `session.rollback()` yourself to group several writes in one transaction.",
        "",
        f"## Summary",
        "",
        f"- SQL sites: {summary.get('total_queries')}; recovered: {summary.get('recovered_queries')}; converted: {summary.get('converted_queries')} "
        f"(general rules: {summary.get('converted_by_general_rules')}, query-specific templates: {summary.get('converted_by_query_specific_template')}); "
        f"unresolved: {len(unresolved)}.",
        f"- Standalone check (C3 blocked in a child process): "
        + (f"models.py and data_access.py import; {standalone['functions_called']} functions called with a recording session; "
           f"failures: {len(standalone['failures'])}." if "error" not in standalone else f"FAILED: {standalone['error']}"),
        "",
        "## Functions",
        "",
        "| Function | Parameters (PHP value) | Returns | Commit | PHP site | E1 (PHP vs SQLAlchemy on MySQL) |",
        "|---|---|---|---|---|---|",
    ]
    if e1_source:
        lines[lines.index("## Functions") + 1:lines.index("## Functions") + 1] = [
            "",
            f"E1 status comes from `{e1_source}` (site verdicts: {e1_counts}). **PASS** means every input case behaved "
            "identically to the PHP on MySQL (rows, affected rows, insert id, table checksums), or the PHP was injectable and the "
            "generated code bound the payload as a value. Use non-PASS functions with care; see `evidence/hms/E1_triage/triage.md`.",
        ]
    for f in functions:
        params = "<br>".join(f"`{p['name']}` ← `{p['php_value']}`" for p in f["parameters"]) or "none"
        commit = "commit by default (`commit=False` available)" if f["operation"] != "SELECT" else "no (read)"
        lines.append(f"| `{f['function']}` | {params} | {f['returns']} | {commit} | `{f['php_site']}` | "
                     f"{e1_status.get(f['function'], 'not run')} |")

    lines += ["", "## Unresolved sites (manual work)", ""]
    if unresolved:
        lines += ["| Site | Stage | Reason | Action |", "|---|---|---|---|"]
        for item in unresolved:
            lines.append(f"| `{relative(item.get('source_file', ''))}:{item.get('source_line')}` | {item.get('stage')} | "
                         f"{item.get('reason_code') or ''} {item.get('reason') or ''} | {item.get('action') or ''} |")
    else:
        lines.append("None: every SQL site was converted.")

    lines += ["", "## Schema drift (SQL uses columns or tables that `myhmsdb.sql` does not define)", "",
              "These functions are generated as the PHP wrote them. On a database built from the dump they fail exactly as the PHP does. "
              "Add the column or table to the database, or do not call them.", "",
              "| Function site | Problem |", "|---|---|"]
    for d in drift:
        problem = (f"table `{d['table']}` not in schema" if d["kind"] == "table_not_in_schema"
                   else f"column `{d['table']}.{d['column']}` ({d['role']}) not in schema")
        site = next((f["php_site"] for f in functions if f["query_id"] == d["query_id"]), f"{d['source_file']}:{d['source_line']}")
        lines.append(f"| `{site}` ({d['query_id']}) | {problem} |")

    lines += ["", "## Tables without a primary key", "",
              f"{', '.join('`' + t + '`' for t in pk_less)} have no PK in the dump and are Core `Table` objects in `models.py` "
              "(no key is invented). They do not affect any function above. Options: `evidence/hms/H2_tables_without_pk/proposal.md`.",
              "", "## Evidence", "",
              "- Recovery and column use vs the verified ground truth: `evidence/evaluation/hms_kishan0725/summary.md`",
              "- Injection negatives (E4), E1 dry run and E2: `evidence/hms/E_checks/summary.md`",
              "- Alembic on MySQL (E3): `evidence/mysql_runs/2026-10-15_152247/e3/hms_kishan0725.json`",
              ""]
    name = f"{args.name}_HANDOFF.md"
    (out / name).write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"handoff": str(out / name), "functions": len(functions), "unresolved": len(unresolved),
                      "drift": len(drift), "standalone": standalone}, indent=1))
    return 0 if "error" not in standalone and not standalone.get("failures") else 1


if __name__ == "__main__":
    raise SystemExit(main())
