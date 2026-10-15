# HMS item 2: E2, E4 and E1 dry run (2026-10-15, after H1)

| Check | Result | Evidence |
|---|---|---|
| E2 offline (A: schema dump parsed independently with sqlglot; B: C3 schema.json; C: generated ORM) | **A–B MATCH, A–C MATCH** (6 tables, 42 columns, 2 with a PK) | `e2_offline/schema_comparison.json`, `orm_mapping_test_log.txt` |
| E2 query-vs-schema drift (reported separately: a fact about the legacy app, not a C3 error) | **7 site-columns**: `appointmenttb.payment` written by func.php:34, func2.php:41, func3.php:24, newfunc.php:23; `doctb.name` written by func2.php:66, func3.php:49; table `users` read by include/header.php:35. The same set the verified GT notes | `e2_offline/schema_comparison.json` (`query_schema_drift`) |
| E2 live (original DDL loaded into MySQL and reflected) | **not run yet**: needs the student's MySQL run (`.\scripts\run_e2_mysql.ps1 -Apps hms_kishan0725`) | — |
| E4 injection negatives, every HMS function | **44/44 SAFE** (12 payloads each; all bound as values; none in the SQL text) | `e4/injection_report.json` |
| E1 dry run (PHP builds the legacy SQL; every generated parameter binds) | **44/44 READY**. The 3 H1 sites bind `GET_ID` to `$_GET['ID']` through `dynamic_variable_sources` | `e1_dry_run/`, `e1_dry_run.log` |

No failures. No test was weakened. E1 binding now also resolves array-element parameters (`GET_ID`, `SESSION_id`). The HMS case file was regenerated because it had no edits, and it now covers all 44 converted sites.
