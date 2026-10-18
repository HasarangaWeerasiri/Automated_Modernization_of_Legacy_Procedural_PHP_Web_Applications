# sqlacodegen schema-only baseline (c3_baseline_u5_qk_8d)

**This is an evaluation comparison. It is not used by the C3 pipeline**, and nothing in
`components/c3_data_layer` imports or depends on sqlacodegen.

sqlacodegen is a models-only tool: it reflects an existing database and writes SQLAlchemy models.
It never reads PHP code, so it recovers no queries, generates no data-access functions and makes no
abstentions. Where C3 produces more (query recovery, data-access functions, manual-review lists), that
is **by design of the two tools, not a win for C3**. The like-for-like part is the model layer: tables,
columns, primary keys, nullability, foreign keys and column types. C3 query counts are recorded for C3
only; for sqlacodegen they are written "not applicable", never zero.

## Version, date, inputs

- sqlacodegen **4.0.4**, SQLAlchemy 2.1.5, PyMySQL 1.2.3,
  Python 3.13.5, in a separate venv outside the repo (`C:\Users\sanjula\AppData\Local\c3_sqlacodegen_venv`).
- Generator: `declarative` with default options (views included, as the CLI does by default).
- Run: 2026-10-18T18:03:18+00:00 (UTC), git HEAD `d40a56af6804ab150c6feac33f0f2ce9772fccc2`.
- Databases: the existing E2 databases `c3_e3_<app>` (loaded with each app's original schema file by
  `scripts/run_e2_mysql.ps1`), reflected read-only as `c3_tester`. Every connection runs
  `SET SESSION TRANSACTION READ ONLY` and the script checks `@@SESSION.transaction_read_only = 1` before
  reflecting. No database, table or grant is created, changed or dropped. Only schema facts are read
  (information_schema table name, type, engine, create time); no rows, row counts or AUTO_INCREMENT values.
- C3 side: fresh current-code C3 run into a temp folder outside the repo. The hashes of the C3 files used are compared with the
  2026-10-19 byte-identity gate record (`evidence/final/2026-10-19/gate/final_2026-10-19.sha256.json`), per app in `summary.json`.

## Commands

```powershell
# once: the separate venv (outside the repo)
python -m venv "$env:LOCALAPPDATA\c3_sqlacodegen_venv"
& "$env:LOCALAPPDATA\c3_sqlacodegen_venv\Scripts\python.exe" -m pip install sqlacodegen==4.0.4 PyMySQL==1.2.3 SQLAlchemy==2.1.5

# each run (repo root); the password is typed, kept in this terminal only, never written
$env:C3_DB_PASSWORD = Read-Host "c3_tester password"
$env:PYTHONDONTWRITEBYTECODE = "1"
components\c3_data_layer\.venv\Scripts\python.exe scripts\baseline_sqlacodegen.py --sqlacodegen-python "$env:LOCALAPPDATA\c3_sqlacodegen_venv\Scripts\python.exe"
Remove-Item Env:\C3_DB_PASSWORD
```

Per app the script does in-process what the CLI command
`sqlacodegen --generator declarative mysql+pymysql://c3_tester:<password>@127.0.0.1/c3_e3_<app>` does
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
