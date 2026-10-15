# E1: MySQL equivalence for all benchmark apps (plan)

**Status:** planned. Clinic already has real-MySQL evidence (61 tests, 2026-10-15, register rows 19–20). This plan covers what is missing: **simple_crud, mixed_crud, hard_crud, Helpdesk, HMS, NEWS-BUZZ, Fee-Rainbow**.
**Output:** `evidence/php_vs_sqlalchemy_equivalence.json` (one record per SQL site) plus raw logs under `evidence/mysql_<date>/e1/`.

## 1. Verdicts (per SQL site, per input case)

| Verdict | Meaning |
|---|---|
| **PASS** | Legacy and generated runs, starting from identical database snapshots, give the same ordered rows (column names, values, NULLs), the same affected-row count and the same table checksums afterwards. |
| **FAIL** | Any difference. Kept and explained, never hidden. |
| **INCONCLUSIVE** | Equivalence cannot be established safely: the site is UNRESOLVED or conversion-unresolved (always INCONCLUSIVE, never PASS), the legacy SQL cannot be built without executing app logic, the input needs state the seed lacks, or the legacy run errors for a reason unrelated to the query. |

A site's overall verdict is FAIL if any case fails, PASS only if every case passes, and INCONCLUSIVE otherwise.

## 2. Method (no circularity)

The legacy SQL must be produced by **PHP itself**, not by C3's recovered template. Otherwise a recovery error would be copied into both sides.

1. **Site list.** Use the independent grep from `evidence/preadaptation/detection_check.py` (unseen apps) or the ground-truth files (fixtures), not C3's output.
2. **Legacy side (PHP CLI harness, `scripts/equivalence/legacy_runner.php`).** For each site, the harness takes the *verbatim* PHP statements that build the SQL (the assignment chain from the PHP AST: `$sql = …; $sql .= …;`, or the inline argument expression). It sets the case inputs as `$_GET`/`$_POST`/`$_SESSION`/local variables and evaluates those statements in an isolated `php` process.
   - Only string-building constructs are allowed: literals, variables, `.`, interpolation, `?:`/`??`, `mysqli_real_escape_string`, `intval`/`(int)`, `trim`, `htmlspecialchars`.
   - Any other call makes the case **INCONCLUSIVE**. No other app code is executed.
   - The resulting SQL string is run with `mysqli` on database `c3_e1_<app>_legacy`. Rows, `affected_rows` and the post-state are captured as JSON.
3. **Generated side (`scripts/equivalence/migrated_runner.py`).** The generated `data_access.py` function is called with the same inputs on `c3_e1_<app>_migrated` inside a transaction. Same captures.
4. **Comparison (`scripts/equivalence/compare.py`).** Ordered rows (unordered when the SQL has no `ORDER BY`), affected rows, and per-table `CHECKSUM TABLE` before and after. It writes the JSON record and the verdict.
5. **State isolation.** Before every write case, both databases are dropped and recreated from schema + seed (the same idempotent reset as `run_mysql_tests.ps1`). Read cases share one fresh reset per app.

## 3. Input cases per converted site

| Case | Values |
|---|---|
| normal | values that exist in the seed (ids, names) |
| empty-result | an id or term that matches nothing |
| boundary | 0, a large id, an empty string where the column allows it |
| hostile | `' OR '1'='1`, `1; DROP TABLE x;--`, `%`, `_`, a unicode quote. Legacy runs on a disposable DB only. The expected outcome is often FAIL in the "legacy is injectable" sense; that is reported as `legacy_injectable: true`, and the generated side must leave the DB unchanged. |
| type mismatch | `'abc'` for numeric parameters; `'10'`/`'abc'` for LIMIT/OFFSET (the Q019 class) |

Values come from a hand-written case file per app (`benchmarks/equivalence_cases/<app>.json`) that the student reviews. They are not copied from C3 output.

## 4. Per-app setup

| App | Schema | Seed | Converted sites now | Notes |
|---|---|---|---|---|
| simple_crud | `schema.sql` | none, so a small hand-written seed is needed | 4 | reuse the 2026-10-08 scenario values |
| mixed_crud | `schema.sql` | none, hand-written seed | 5 (+1 INCONCLUSIVE) | |
| hard_crud | `schema.sql` | none, hand-written seed | 2 (+3 INCONCLUSIVE) | |
| Helpdesk | `database/schema.sql` | 3 INSERT blocks in the schema | 6 (+3 INCONCLUSIVE) | holdout; report beside the 6/9 baseline |
| HMS | `myhmsdb.sql` | 6 INSERT blocks (contains plaintext demo passwords, local only) | 19 (+25 INCONCLUSIVE) | no licence: logs record row counts and checksums only, not raw rows |
| NEWS-BUZZ | `db/NEWSBUZZ.sql` | 2 INSERT blocks | 4 (+28 INCONCLUSIVE) | |
| Fee-Rainbow | `database/db.sql` | 1 INSERT block | 18 (+4 INCONCLUSIVE) | |

The "converted" counts are those of code `e77f854`. They will be re-measured after S13, and E1 runs on whatever code is frozen at that time.

**Databases:** `c3_e1_<app>_legacy`, `c3_e1_<app>_migrated`, created by `scripts/run_mysql_tests.ps1 -E1` (to be added). The runner needs `GRANT ALL ON c3_e1\_%.* TO 'c3_tester'@'localhost'` once, run by you as admin. The password stays session-only, as now.

## 5. Who runs what

- **Claude:** writes the harness, the runners and the comparison, with unit tests that mock the database layer. Writes the case-file skeletons, which list only parameter names and types and no expected values.
- **Student:** reviews and fills the case files, grants the `c3_e1_%` rights, runs `scripts/run_mysql_tests.ps1 -E1` in their own terminal, and commits the logs.
- **Then:** Claude builds `php_vs_sqlalchemy_equivalence.json` and the summary table from the logs and adds register rows.

## 6. Order and budget

Run after S13 + S4 + S5 so the evidence reflects the code that will be frozen. Budget: about 4 h harness, about 1 h case files (student), about 30 min run. If the budget runs out, cut in this order: hostile/type cases for unseen apps → NEWS-BUZZ → Fee-Rainbow. Report the honest N.

## 7. Limits (go into `docs/limitations_and_threats.md`)

- Query-level equivalence on selected inputs only, not application-level equivalence. Level C (running ≥2 legacy pages via `php -S`) is optional.
- Statements that call app functions are INCONCLUSIVE by design.
- Single MySQL version (8.0.39), single annotator for the case files.
