# E4 injection negatives - 2026-10-17 (current code)

**Purpose.** Re-run E4 on the current implementation, so that it covers the functions added since the 2026-10-15 run (`evidence/e4/2026-10-15/`, kept unchanged). The new functions come from wrapper support (Fee-Rainbow), the escape fix (NEWS-BUZZ), H1 (HMS) and prepared statements (Clinic Q022).

## Run conditions

| Item | Value |
|---|---|
| Date | 2026-10-17, 15:20:25–15:20:28 +05:30 (`date_utc` in the report: 2026-10-17T09:50:25Z) |
| Code | git HEAD `6e927117e42893a734a93bd9c2c0e0eb5276cb70` (`git_head` in the report). `components/c3_data_layer/src` and `tools` had no uncommitted changes. The uncommitted working-tree files at the time were docs, the summary script, the test log and pre-existing untracked outputs; none is pipeline code |
| Command | PowerShell, repo root: `components\c3_data_layer\.venv\Scripts\python.exe scripts\e4_injection_check.py --out evidence\e4\2026-10-17`, with `TEMP`/`TMP` = `C:\Users\sanjula\AppData\Local\Temp\c3e4` (short path, new and empty), `PYTHONDONTWRITEBYTECODE=1`, `C3_DB_PASSWORD` unset |
| Database | None. A recording session captures each statement and its bound parameters; SQL is only compiled for the MySQL dialect |
| Pipeline output | Each app is run by `run_pipeline` into a `tempfile.TemporaryDirectory` under the short temp path, which is deleted afterwards (0 files left) |
| Exit code | 0 (no UNSAFE function) |
| Repository files written | only `injection_report.json` and `summary.txt` in this folder; this README was added afterwards |

## Results (from `injection_report.json`)

| App | Functions tested | SAFE | UNSAFE | Template functions | Hostile cases | Rejected | Executed with payload bound as a value | Executed, payload never reached the SQL |
|---|---|---|---|---|---|---|---|---|
| simple_crud | 4 | 4 | 0 | 0 | 48 | 0 | 36 | 12 |
| mixed_crud | 5 | 5 | 0 | 0 | 60 | 0 | 36 | 24 |
| hard_crud | 2 | 2 | 0 | 0 | 24 | 0 | 24 | 0 |
| Clinic | 22 | 22 | 0 | 7 | 264 | 72 | 120 | 72 |
| Helpdesk | 8 | 8 | 0 | 0 | 96 | 0 | 72 | 24 |
| HMS (kishan0725) | 44 | 44 | 0 | 0 | 528 | 0 | 372 | 156 |
| NEWS-BUZZ | 30 | 30 | 0 | 0 | 360 | 0 | 288 | 72 |
| Fee-Rainbow | 40 | 40 | 0 | 0 | 480 | 48 | 276 | 156 |
| Dignity-Dev | 4 | 4 | 0 | 0 | 48 | 0 | 36 | 12 |
| **Total** | **159** | **159** | **0** | **7** | **1,908** | **120** | **1,260** | **528** |

- **Cases per function:**
  - 12 hostile payloads (the `payloads` list in the report);
  - 4 benign values (`1`, `a`, `id`, `0`), used only to collect the SQL text a function normally produces: 636 benign calls in total. Benign calls are not stored as cases.
- **SAFE criteria.** A function is SAFE when, for every payload, it either rejects the value (`ValueError`/`TypeError`) or executes SQL that:
  - does not contain the payload;
  - is identical to a benign-call SQL text;
  - carries the payload only as a bound parameter.
- **Functions tested:** every converted function in each app's generated `data_access.py`, i.e. names ending in `_q<NNN>`. `unresolved_query_*` stubs are skipped by design; they raise `NotImplementedError`. Per app, functions tested = converted count in `docs/C3_results_summary.md` (current-code table).
- **Rejections (120 cases, 10 functions):**
  - Clinic templates Q011, Q012, Q013, Q016 and Q020, plus Clinic general Q019 (LIMIT/OFFSET integer check);
  - Fee-Rainbow Q011, Q045, Q047 and Q048 (LIMIT/OFFSET integer check). Fee-Rainbow Q011 is that app's own query ID, not the Clinic template.
- **Weaker checks, stated plainly:**
  - **Clinic Q013 (`select_q013`)** rejects all 4 benign values too, so its "same SQL as a benign call" comparison is vacuous. It is SAFE only because it rejects all 12 payloads.
  - **44 functions** received no hostile value as a bound parameter and did not reject. The payload never reached their SQL text or parameters (fixed SQL), so E4 is trivially satisfied for them. They are counted in the "payload never reached the SQL" column (528 cases).
- **Errors:** 0 `ERROR` outcomes.

## What this does and does not show

- It shows that, for these 12 payloads, no generated function in the 9 apps puts the payload into SQL text. Every payload is either rejected or bound as a value.
- It does **not** run SQL on a database, does not test other payloads or encodings, does not cover unresolved sites (no code is generated for them), and does not test the legacy PHP. Runtime behaviour on MySQL with hostile inputs exists only for HMS (E1, `evidence/mysql_runs/2026-10-15_195730/e1/`).
- The report contains payloads, outcomes and generated-code error messages only. It contains no SQL text, schema data or HMS dump values.
