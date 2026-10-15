# HMS E1: what to fill (about 30 minutes)

**File to edit:** `benchmarks/equivalence_cases/hms_kishan0725_inputs.csv`. It has one row per distinct legacy input (46 rows), and only two columns are yours: `normal` and `empty_result`. Open it in Excel and save it as "CSV UTF-8".

| Column | Meaning |
|---|---|
| `input` | The PHP input the legacy code reads (`$_POST['demail']`, `$_SESSION['pid']`, ...). |
| `binds_to (table.column)` | The column the value is compared with or written to. PHP built the SQL with a marker value to find it. |
| `sites` | Every converted site that reads this input (site id, file:line). One value serves all of them. |
| `normal` | **Fill.** A value that **exists in the seed data** of that column (look it up in `myhmsdb.sql`). If an input binds to two columns (e.g. `$_POST['doctor']` → `appointmenttb.doctor` and `doctb.username`), pick a value present in both. |
| `empty_result` | **Fill.** A value that matches **nothing** (e.g. `999999`, `nobody@example.com`). For inputs that are only written (INSERT), any other valid value will do. |

Leave a cell blank to skip that case for every site using the input. `boundary` and the pre-filled `hostile_*` / `type_mismatch` cases stay as they are in `hms_kishan0725.json`.

**Then (repo root):**

```powershell
components\c3_data_layer\.venv\Scripts\python.exe scripts\equivalence\input_table.py apply hms_kishan0725
components\c3_data_layer\.venv\Scripts\python.exe scripts\equivalence\run_e1.py dry-run --apps hms_kishan0725 --out $env:TEMP\e1_hms_check
$env:C3_DB_PASSWORD = Read-Host "c3_tester password"; .\scripts\run_e1_mysql.ps1 -Apps hms_kishan0725
```

The dry run must report `READY: 44`. The MySQL run needs `c3_e1_hms_kishan0725_legacy` and `c3_e1_hms_kishan0725_migrated` (in `scripts/e1_create_databases.sql`).

**Expected INCONCLUSIVE (schema drift, not a C3 error):** the legacy SQL fails on MySQL because the column or table does not exist in `myhmsdb.sql`.
- `$_POST['status']` → `appointmenttb.payment` (func.php:34, func2.php:41, func3.php:24, newfunc.php:23)
- `$_POST['name']` → `doctb.name` (func2.php:66, func3.php:49)
- `$_SESSION['id']` → table `users` (include/header.php:35)

You can still fill these; they will be reported as INCONCLUSIVE.

**Licence note:** HMS has no licence. The filled CSV and JSON contain values copied from the HMS dump, including demo passwords. Decide yourself whether to commit them; the E1 evidence for HMS is redacted (no SQL text, no values).
