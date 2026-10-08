# Running this benchmark on your existing C3 CLI (PowerShell)

From repository root after extracting `clinic_inventory_legacy_hard` under `benchmarks/apps/`:

```powershell
cd components/c3_data_layer
python -m c3.cli run --php ../../benchmarks/apps/clinic_inventory_legacy_hard/legacy --schema ../../benchmarks/apps/clinic_inventory_legacy_hard/database/schema.sql --output ../../output/clinic_inventory_legacy_hard
```

Then inspect:
```powershell
Get-Content ../../output/clinic_inventory_legacy_hard/summary.json
Get-Content ../../output/clinic_inventory_legacy_hard/unresolved_queries.json
Get-Content ../../output/clinic_inventory_legacy_hard/converted_queries.json
```

**If the pipeline raises an error, capture the full traceback and do not modify the benchmark to force success.** Record the issue as an algorithm finding. The SQL parser may not support every MySQL `CREATE TABLE` clause; if so, log the exact construct, then fix your schema parser on a separate branch/test.

## Optional MySQL setup (only for isolated behavioral validation)

Create a disposable database using your existing local MySQL installation and an authorized test account. In your MySQL client (do not use production data):

```sql
CREATE DATABASE c3_clinic_legacy_hard CHARACTER SET utf8mb4;
```

Import `database/schema.sql` and `database/seed.sql` into `c3_clinic_legacy_hard` in that order. Configure PHP with environment variables `C3_BENCH_DB_HOST`, `C3_BENCH_DB_USER`, `C3_BENCH_DB_PASSWORD`, `C3_BENCH_DB_NAME`. Never commit secrets. Prefer separate fresh databases for legacy PHP and migrated Python comparisons.

Do **not** run all legacy PHP files in sequence: some have destructive INSERT/UPDATE/DELETE statements and some intentionally accept unsanitized inputs. For differential testing, execute only individually selected cases on independently reset copies and capture observed outputs.

## Interpreting results
- Count **execution sites**, not only successfully converted functions.
- Distinguish recovered / unresolved and converted / conversion-rejected.
- Inspect the SQL actually emitted to ensure structure and bind semantics, not just file existence.
- Use the CSV as human-annotated expected behavior, but correct any annotation errors before scoring.
- A query with dynamic table, `IN` list, branch, or JOIN should not silently become an unsafe scalar bound SQL statement.
