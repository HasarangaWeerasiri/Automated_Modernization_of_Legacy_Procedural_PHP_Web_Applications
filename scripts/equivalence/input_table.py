"""E1 helper: one fill-in row per distinct legacy input, and applying it.

    python scripts/equivalence/input_table.py make  <app>   # writes benchmarks/equivalence_cases/<app>_inputs.csv
    python scripts/equivalence/input_table.py apply <app>   # copies the filled values into <app>.json

`make` finds, for every input of every converted site, the table column it
is compared with or written to. It builds the legacy SQL with PHP (build
mode, no database) using a unique marker per input and locates the marker
in the parsed SQL. It also lists up to 3 example values of that column from
the schema dump's INSERT rows. The `normal` and `empty_result` columns are
left blank for the student. `apply` writes them into every site that reads
that input, and leaves `boundary` and the pre-filled hostile cases untouched.
"""

import csv
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_e1  # noqa: E402

ROOT = run_e1.ROOT
CASES = run_e1.CASES_DIR


def seed_values(app: str) -> dict[tuple[str, str], list[str]]:
    sql = (run_e1.APPS_DIR / run_e1.APPS[app][1]).read_text(encoding="utf-8-sig", errors="replace")
    values: dict[tuple[str, str], list[str]] = {}
    for statement in sqlglot.parse(sql, read="mysql", error_level=sqlglot.ErrorLevel.IGNORE):
        if not isinstance(statement, exp.Insert) or not isinstance(statement.this, exp.Schema):
            continue
        table = statement.this.this.name.lower()
        columns = [c.name.lower() for c in statement.this.expressions]
        for row in statement.expression.expressions:
            for column, value in zip(columns, row.expressions):
                text = value.name if isinstance(value, exp.Literal) else value.sql()
                bucket = values.setdefault((table, column), [])
                if text not in bucket:
                    bucket.append(text)
    return values


def marker_targets(sql: str, markers: dict[str, str]) -> dict[str, list[str]]:
    """Input name -> ["table.column", ...] where its marker lands in the SQL."""

    found: dict[str, list[str]] = {name: [] for name in markers}
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return found
    aliases = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)})

    def column_name(column: exp.Column) -> str:
        table = aliases.get(column.table.lower(), column.table.lower()) if column.table else (tables[0] if len(tables) == 1 else "?")
        return f"{table}.{column.name.lower()}"

    for literal in tree.find_all(exp.Literal):
        for name, marker in markers.items():
            if marker not in literal.name:
                continue
            parent = literal.parent
            while isinstance(parent, (exp.Paren, exp.Cast)):
                parent = parent.parent
            if isinstance(parent, (exp.EQ, exp.NEQ, exp.Like, exp.GT, exp.LT, exp.GTE, exp.LTE)):
                other = parent.this if parent.expression is literal or parent.expression.find(exp.Literal) is literal else parent.expression
                if isinstance(other, exp.Column):
                    found[name].append(column_name(other))
            elif isinstance(parent, exp.Tuple) and isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema):
                index = parent.expressions.index(literal)
                columns = tree.this.expressions
                if index < len(columns):
                    found[name].append(f"{tree.this.this.name.lower()}.{columns[index].name.lower()}")
    return found


def make(app: str) -> Path:
    document = json.loads((CASES / f"{app}.json").read_text(encoding="utf-8"))
    seeds = seed_values(app)
    rows: dict[str, dict] = {}
    for entry in document["sites"]:
        if not isinstance(entry.get("cases"), dict) or not entry["inputs"]:
            continue
        markers = {i["name"]: f"C3IN{k}X" for k, i in enumerate(entry["inputs"])}
        inputs = {name: ({key: marker for key in next(i for i in entry["inputs"] if i["name"] == name).get("keys", [])}
                         if next(i for i in entry["inputs"] if i["name"] == name)["kind"] == "array" else marker)
                  for name, marker in markers.items()}
        built = run_e1.legacy(app, entry, inputs, "legacy", execute=False)
        targets = marker_targets(built.get("sql") or "", markers)
        for item in entry["inputs"]:
            row = rows.setdefault(item["name"], {"input": item["name"], "kind": item["kind"], "sites": [], "binds_to": []})
            row["sites"].append(f"{entry['site_id']} {entry['file']}:{entry['line']}")
            for target in targets.get(item["name"], []):
                if target not in row["binds_to"]:
                    row["binds_to"].append(target)
    out = CASES / f"{app}_inputs.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["input", "binds_to (table.column)", "sites", "seed_examples (from the dump)", "normal", "empty_result"])
        for name in sorted(rows):
            row = rows[name]
            examples = []
            for target in row["binds_to"]:
                table, _, column = target.partition(".")
                examples += [v for v in seeds.get((table, column), [])[:3] if v not in examples]
            if run_e1.APPS[app][3]:
                # No licence (HMS): do not copy dump data into a committed file.
                examples = ["see the schema dump (not copied: no licence)"]
            writer.writerow([name, "; ".join(row["binds_to"]) or "(not compared with a column)",
                             "; ".join(row["sites"]), " | ".join(examples[:3]), "", ""])
    return out


def apply(app: str) -> None:
    path = CASES / f"{app}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    with (CASES / f"{app}_inputs.csv").open(encoding="utf-8-sig", newline="") as handle:
        values = {row["input"]: row for row in csv.DictReader(handle)}
    filled = 0
    for entry in document["sites"]:
        if not isinstance(entry.get("cases"), dict) or not entry["inputs"]:
            continue
        for case in ("normal", "empty_result"):
            if case not in entry["cases"]:
                continue
            for item in entry["inputs"]:
                value = (values.get(item["name"]) or {}).get(case, "")
                if value != "":
                    entry["cases"][case][item["name"]] = {k: value for k in item.get("keys", [])} if item["kind"] == "array" else value
                    filled += 1
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{app}: {filled} input values written into {path.relative_to(ROOT)}")


if __name__ == "__main__":
    command, app = sys.argv[1], sys.argv[2]
    if command == "make":
        print(make(app).relative_to(ROOT))
    else:
        apply(app)
