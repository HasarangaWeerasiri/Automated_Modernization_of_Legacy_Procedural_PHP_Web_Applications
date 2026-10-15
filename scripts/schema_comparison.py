"""E2: schema fidelity. Compares, per app, four views of the same schema.

  A  original schema.sql parsed INDEPENDENTLY with sqlglot (not C3's reader)
  B  C3's schema.json
  C  the generated ORM metadata (models.py), types rendered for MySQL
  D  live MySQL: the ORIGINAL schema.sql loaded into an empty database and
     reflected with SQLAlchemy (only with --mysql; needs C3_DB_PASSWORD)

Compared per table: columns, normalised type, nullability, primary key,
foreign keys (columns, referenced table/columns), unique constraints
(UNIQUE columns and UNIQUE keys) and plain indexes. The comparisons reported
are A-B (parser fidelity), A-C (model fidelity) and D-C (live fidelity).

Usage (repo root, component venv):
    python scripts/schema_comparison.py --out <dir>            # A, B, C
    python scripts/schema_comparison.py --out <dir> --mysql    # + D, into c3_e3_<app>

The --mysql mode reuses the E3 databases c3_e3_<app> (empty after the E3
downgrade): each is dropped, recreated and loaded with the original DDL.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components" / "c3_data_layer" / "src"))
APPS_DIR = ROOT / "benchmarks" / "apps"
MYSQL_CLIENT = os.environ.get("C3_MYSQL_CLIENT", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")

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

INT_FAMILY = {"TINYINT", "SMALLINT", "MEDIUMINT", "INT", "INTEGER", "BIGINT"}


# ------------------------------------------------------------------ normalise

def norm_type(text: str) -> str:
    """One spelling per MySQL type: INT(11) -> INT, INTEGER -> INT, no spaces."""

    t = re.sub(r"\s+", " ", text.upper()).strip()
    t = re.sub(r"\s+(CHARACTER SET|CHARSET|COLLATE)\s+\S+", "", t)
    t = t.replace(", ", ",").replace(" (", "(")
    t = re.sub(r"^INTEGER\b", "INT", t)
    t = re.sub(r"^(BOOL|BOOLEAN)$", "TINYINT", t)
    t = re.sub(r"^(TINYINT|SMALLINT|MEDIUMINT|INT|BIGINT)\(\d+\)", r"\1", t)
    t = re.sub(r"^(DATETIME|TIMESTAMP|TIME)\(0\)", r"\1", t)
    t = re.sub(r"^(DECIMAL|NUMERIC)$", "DECIMAL(10,0)", t)
    t = re.sub(r"^NUMERIC", "DECIMAL", t)
    t = re.sub(r"^(DOUBLE|FLOAT|REAL)\(\d+,\d+\)", r"\1", t)
    t = t.replace("DOUBLE PRECISION", "DOUBLE")
    t = re.sub(r"\bZEROFILL\b", "", t).strip()
    t = t.replace('"', "'")
    return t


def low(names) -> tuple:
    return tuple(n.lower() for n in names)


def empty_table() -> dict:
    return {"columns": {}, "primary_key": [], "foreign_keys": [], "unique": [], "indexes": []}


# --------------------------------------------------- A: independent sqlglot parse

def _column_from_def(column: exp.ColumnDef, table: dict) -> None:
    name = column.name.lower()
    kind = column.args.get("kind")
    nullable = True
    for constraint in column.args.get("constraints") or []:
        kind_c = constraint.args.get("kind")
        if isinstance(kind_c, exp.NotNullColumnConstraint):
            nullable = bool(kind_c.args.get("allow_null"))
        elif isinstance(kind_c, exp.PrimaryKeyColumnConstraint):
            table["primary_key"] = [name]
            nullable = False
        elif isinstance(kind_c, exp.UniqueColumnConstraint):
            table["unique"].append((name,))
    table["columns"][name] = {"type": norm_type(kind.sql(dialect="mysql")) if kind else "?", "nullable": nullable}


def _apply_constraint(item, table: dict) -> None:
    def cols(node):
        return [e.name.lower() if hasattr(e, "name") and e.name else e.sql().strip("`").lower()
                for e in node.expressions]

    if isinstance(item, exp.PrimaryKey):
        table["primary_key"] = cols(item)
        for column in table["primary_key"]:
            if column in table["columns"]:
                table["columns"][column]["nullable"] = False
    elif isinstance(item, exp.ForeignKey):
        reference = item.args.get("reference")
        target = reference.this if reference else None
        if isinstance(target, exp.Schema):
            table["foreign_keys"].append((tuple(cols(item)), target.this.name.lower(), tuple(c.name.lower() for c in target.expressions)))
    elif isinstance(item, exp.Constraint):
        for inner in item.expressions:
            _apply_constraint(inner, table)
    elif isinstance(item, exp.UniqueColumnConstraint):
        schema = item.this
        if isinstance(schema, exp.Schema):
            table["unique"].append(tuple(c.name.lower() for c in schema.expressions))
    elif isinstance(item, exp.IndexColumnConstraint):
        kind = (item.args.get("kind") or "").upper()
        names = tuple(e.name.lower() if hasattr(e, "name") else str(e) for e in item.expressions)
        if kind == "UNIQUE":
            table["unique"].append(names)
        elif kind in ("", "KEY", "INDEX"):
            table["indexes"].append(names)
        # FULLTEXT / SPATIAL are not plain indexes; not compared


_MODIFY = re.compile(r"\bMODIFY\s+(?:COLUMN\s+)?(`?\w+`?\s+.+?)(?=,\s*(?:MODIFY|ADD|CHANGE|DROP|AUTO_INCREMENT)\b|$)", re.IGNORECASE | re.DOTALL)
_ADD_KEY = re.compile(r"\bADD\s+(UNIQUE\s+)?(?:KEY|INDEX)\s+`?\w+`?\s*\(([^)]*)\)", re.IGNORECASE)
_ADD_PK = re.compile(r"\bADD\s+PRIMARY\s+KEY\s*\(([^)]*)\)", re.IGNORECASE)
_ADD_FK = re.compile(r"\bADD\s+(?:CONSTRAINT\s+`?\w+`?\s+)?FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+`?(\w+)`?\s*\(([^)]*)\)", re.IGNORECASE)


def _names(text: str) -> tuple:
    return tuple(re.sub(r"\(\d+\)", "", n).strip().strip("`").lower() for n in text.split(","))


def _apply_alter_text(text: str, tables: dict) -> None:
    match = re.match(r"\s*ALTER\s+TABLE\s+`?(\w+)`?", text, re.IGNORECASE)
    if not match or match.group(1).lower() not in tables:
        return
    table = tables[match.group(1).lower()]
    for pk in _ADD_PK.findall(text):
        table["primary_key"] = list(_names(pk))
        for column in table["primary_key"]:
            if column in table["columns"]:
                table["columns"][column]["nullable"] = False
    for unique, columns in _ADD_KEY.findall(text):
        (table["unique"] if unique else table["indexes"]).append(_names(columns))
    for columns, ref_table, ref_columns in _ADD_FK.findall(text):
        table["foreign_keys"].append((_names(columns), ref_table.lower(), _names(ref_columns)))
    for definition in _MODIFY.findall(text):
        parsed = sqlglot.parse_one(f"CREATE TABLE t ({definition.strip().rstrip(',;')})", read="mysql")
        column = parsed.this.expressions[0]
        previous = table["columns"].get(column.name.lower())
        _column_from_def(column, table)
        if previous and column.name.lower() in table["primary_key"]:
            table["columns"][column.name.lower()]["nullable"] = False


def sqlglot_schema(schema_file: Path) -> dict:
    sql = schema_file.read_text(encoding="utf-8-sig", errors="replace")
    tables: dict[str, dict] = {}
    alters = []
    for chunk in re.split(r";\s*\n", sql):
        statement = chunk.strip()
        if not statement:
            continue
        head = re.sub(r"^(\s*(--[^\n]*|/\*.*?\*/)\s*)*", "", statement, flags=re.DOTALL).lstrip()
        if re.match(r"CREATE\s+TABLE", head, re.IGNORECASE):
            parsed = sqlglot.parse_one(head, read="mysql")
            schema = parsed.this
            name = schema.this.name.lower()
            table = tables.setdefault(name, empty_table())
            for item in schema.expressions:
                if isinstance(item, exp.ColumnDef):
                    _column_from_def(item, table)
                else:
                    _apply_constraint(item, table)
        elif re.match(r"ALTER\s+TABLE", head, re.IGNORECASE):
            alters.append(head)
    for text in alters:
        _apply_alter_text(text, tables)
    return tables


# --------------------------------------------------------------- B: schema.json

def json_schema(schema_json: Path) -> dict:
    data = json.loads(schema_json.read_text(encoding="utf-8"))
    tables = {}
    for t in data["tables"]:
        table = empty_table()
        for c in t["columns"]:
            table["columns"][c["name"].lower()] = {"type": norm_type(c["data_type"]), "nullable": bool(c["nullable"]) and not c["primary_key"]}
            if c.get("unique"):
                table["unique"].append((c["name"].lower(),))
        table["primary_key"] = [c["name"].lower() for c in t["columns"] if c["primary_key"]]
        for fk in t.get("foreign_keys", []):
            columns = fk.get("columns") or [fk["column"]]
            ref_columns = fk.get("referenced_columns") or [fk["referenced_column"]]
            table["foreign_keys"].append((low(columns), fk["referenced_table"].lower(), low(ref_columns)))
        for u in t.get("unique_constraints", []):
            table["unique"].append(low(u["columns"]))
        for i in t.get("indexes", []):
            kind = (i.get("kind") or i.get("type") or "").upper()
            if kind in ("FULLTEXT", "SPATIAL"):
                continue
            (table["unique"] if i.get("unique") or kind == "UNIQUE" else table["indexes"]).append(low(i["columns"]))
        tables[t["name"].lower()] = table
    return tables


# ------------------------------------------------------------------- C: models

def orm_schema(models_file: Path):
    from sqlalchemy.dialects import mysql
    from sqlalchemy.orm import clear_mappers, configure_mappers

    spec = importlib.util.spec_from_file_location(f"e2_models_{abs(hash(str(models_file)))}", models_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configure_mappers()
    metadata = module.Base.metadata
    dialect = mysql.dialect()
    tables = {}
    for t in metadata.sorted_tables:
        table = empty_table()
        for c in t.columns:
            try:
                rendered = c.type.compile(dialect=dialect)
            except Exception:  # UnmappedMySQLType etc.
                rendered = getattr(c.type, "ddl", None) or type(c.type).__name__
            table["columns"][c.name.lower()] = {"type": norm_type(rendered), "nullable": bool(c.nullable)}
            if c.unique:
                table["unique"].append((c.name.lower(),))
        table["primary_key"] = [c.name.lower() for c in t.primary_key.columns]
        for fk in t.foreign_key_constraints:
            table["foreign_keys"].append((
                low(e.parent.name for e in fk.elements), fk.referred_table.name.lower(), low(e.column.name for e in fk.elements)))
        for constraint in t.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint":
                table["unique"].append(low(c.name for c in constraint.columns))
        for index in t.indexes:
            (table["unique"] if index.unique else table["indexes"]).append(low(c.name for c in index.columns))
        tables[t.name.lower()] = table
    clear_mappers()
    return tables


# ---------------------------------------------------------------- D: live MySQL

def load_original_ddl(schema_file: Path, database: str) -> None:
    lines = [line for line in schema_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
             if not re.match(r"^\s*(CREATE\s+DATABASE|USE\s)", line, re.IGNORECASE)]
    script = f"DROP DATABASE IF EXISTS `{database}`;\nCREATE DATABASE `{database}` CHARACTER SET utf8mb4;\nUSE `{database}`;\n" + "\n".join(lines) + "\n"
    env = dict(os.environ, MYSQL_PWD=os.environ["C3_DB_PASSWORD"])
    completed = subprocess.run([MYSQL_CLIENT, "-u", "c3_tester", "-h", "127.0.0.1", "--default-character-set=utf8mb4"],
                               input=script, capture_output=True, text=True, encoding="utf-8", env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"loading the original DDL into {database} failed: {completed.stderr[-400:]}")


def live_schema(database: str) -> dict:
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.dialects import mysql
    from sqlalchemy.engine import URL

    engine = create_engine(URL.create("mysql+pymysql", username="c3_tester", password=os.environ["C3_DB_PASSWORD"],
                                      host="127.0.0.1", database=database))
    inspector = inspect(engine)
    dialect = mysql.dialect()
    tables = {}
    try:
        for name in inspector.get_table_names():
            table = empty_table()
            for c in inspector.get_columns(name):
                table["columns"][c["name"].lower()] = {"type": norm_type(c["type"].compile(dialect=dialect)), "nullable": bool(c["nullable"])}
            table["primary_key"] = list(low(inspector.get_pk_constraint(name).get("constrained_columns") or []))
            for fk in inspector.get_foreign_keys(name):
                table["foreign_keys"].append((low(fk["constrained_columns"]), fk["referred_table"].lower(), low(fk["referred_columns"])))
            for index in inspector.get_indexes(name):
                columns = low(index["column_names"])
                if index.get("unique"):
                    table["unique"].append(columns)
                elif (index.get("type") or "").upper() not in ("FULLTEXT", "SPATIAL"):
                    # Indexes MySQL adds for FKs are ignored in compare(ignore_fk_indexes=True).
                    table["indexes"].append(columns)
            tables[name.lower()] = table
    finally:
        engine.dispose()
    return tables


# ------------------------------------------------------------------- compare

def compare(reference: dict, candidate: dict, ignore_fk_indexes: bool = False) -> list[dict]:
    diffs = []
    for name in sorted(set(reference) | set(candidate)):
        if name not in candidate:
            diffs.append({"table": name, "kind": "table_missing"})
            continue
        if name not in reference:
            diffs.append({"table": name, "kind": "table_extra"})
            continue
        r, c = reference[name], candidate[name]
        for column in sorted(set(r["columns"]) | set(c["columns"])):
            if column not in c["columns"]:
                diffs.append({"table": name, "kind": "column_missing", "column": column})
            elif column not in r["columns"]:
                diffs.append({"table": name, "kind": "column_extra", "column": column})
            else:
                for key in ("type", "nullable"):
                    if r["columns"][column][key] != c["columns"][column][key]:
                        diffs.append({"table": name, "kind": key, "column": column,
                                      "reference": r["columns"][column][key], "candidate": c["columns"][column][key]})
        if sorted(r["primary_key"]) != sorted(c["primary_key"]):
            diffs.append({"table": name, "kind": "primary_key", "reference": r["primary_key"], "candidate": c["primary_key"]})
        for key in ("foreign_keys", "unique", "indexes"):
            ref_set, cand_set = set(map(tuple, r[key])), set(map(tuple, c[key]))
            if key == "indexes" and ignore_fk_indexes:
                # MySQL creates an index for every FK; such indexes are not schema differences.
                fk_cols = {fk[0] for fk in r["foreign_keys"]} | {fk[0] for fk in c["foreign_keys"]}
                ref_set, cand_set = ref_set - fk_cols, cand_set - fk_cols
            if key == "unique":
                ref_set, cand_set = {u for u in ref_set if u}, {u for u in cand_set if u}
            if ref_set != cand_set:
                diffs.append({"table": name, "kind": key,
                              "missing": sorted(map(list, ref_set - cand_set)), "extra": sorted(map(list, cand_set - ref_set))})
    return diffs


def summarise(tables: dict) -> dict:
    return {
        "tables": len(tables),
        "columns": sum(len(t["columns"]) for t in tables.values()),
        "foreign_keys": sum(len(t["foreign_keys"]) for t in tables.values()),
        "unique": sum(len(t["unique"]) for t in tables.values()),
        "indexes": sum(len(t["indexes"]) for t in tables.values()),
        "tables_with_pk": sum(1 for t in tables.values() if t["primary_key"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--mysql", action="store_true")
    parser.add_argument("--apps", default=",".join(APPS))
    args = parser.parse_args()
    if args.mysql and not os.environ.get("C3_DB_PASSWORD"):
        raise SystemExit("C3_DB_PASSWORD is not set")

    from c3.pipeline import run_pipeline

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report = {"experiment": "E2 schema fidelity", "date_utc": datetime.now(timezone.utc).isoformat(), "git_head": head,
              "views": {"A": "schema.sql parsed with sqlglot (independent of C3)", "B": "C3 schema.json",
                        "C": "generated models.py metadata rendered for MySQL",
                        "D": "original schema.sql loaded into MySQL and reflected" if args.mysql else "not run (no --mysql)"},
              "apps": {}}
    log = []

    for app in [a for a in args.apps.split(",") if a]:
        php, schema = APPS[app]
        with tempfile.TemporaryDirectory() as tmp:
            c3_out = Path(tmp) / app
            run_pipeline(APPS_DIR / php, APPS_DIR / schema, c3_out)
            a = sqlglot_schema(APPS_DIR / schema)
            b = json_schema(c3_out / "schema.json")
            try:
                c = orm_schema(c3_out / "models.py")
                log.append(f"{app}: models.py imported, configure_mappers() OK, {len(c)} tables")
            except Exception as error:
                c = {}
                log.append(f"{app}: models.py FAILED {type(error).__name__}: {error}")
        entry = {"A": summarise(a), "B": summarise(b), "C": summarise(c),
                 "A_vs_B": compare(a, b), "A_vs_C": compare(a, c)}
        if args.mysql:
            database = f"c3_e3_{app}"
            load_original_ddl(APPS_DIR / schema, database)
            d = live_schema(database)
            entry["D"] = summarise(d)
            entry["D_vs_C"] = compare(d, c, ignore_fk_indexes=True)
            entry["mysql_database"] = database
        verdicts = {k: ("MATCH" if not entry[k] else f"{len(entry[k])} DIFF") for k in entry if "_vs_" in k}
        entry["verdicts"] = verdicts
        report["apps"][app] = entry
        line = f"{app}: " + ", ".join(f"{k} {v}" for k, v in verdicts.items())
        log.append(line)
        print(line)

    name = "schema_comparison_mysql.json" if args.mysql else "schema_comparison.json"
    (out / name).write_text(json.dumps(report, indent=2, default=list) + "\n", encoding="utf-8")
    (out / ("orm_mapping_test_log_mysql.txt" if args.mysql else "orm_mapping_test_log.txt")).write_text(
        "\n".join([f"# E2 {report['date_utc']} HEAD {head}"] + log) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
