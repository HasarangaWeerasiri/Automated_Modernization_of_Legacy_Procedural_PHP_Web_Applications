# C3 Data Layer Migration — Pre-development Audit Report

**Project:** J26-SE-329 · Component C3 (`components/c3_data_layer`)
**Audit date:** 2026-10-15 · **Branch audited:** `database-migration` @ `01869d8` (11 commits ahead of `origin/database-migration`)
**Mode:** read-only. No git state was changed, and no existing file was modified. Every pipeline, test and probe run wrote to `C:\Users\sanjula\AppData\Local\Temp\c3audit\` (outside the repo). pytest ran with `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`. Before and after the runs there were 12 `__pycache__` folders, all pre-existing and gitignored, and `.pytest_cache` kept its 2026-10-06 timestamp. **No caches were created.**

Every number below comes from a command run during this audit, unless it is explicitly marked as a claim.

---

## 0. Headline findings (read this first)

> **Update after the audit (current test figure):** 642 passed (581 without MySQL + 61 MySQL), 0 failed, as of 2026-10-15. The MySQL run is in `evidence/mysql_2026-10-15/full_suite_with_mysql_log.txt`, and the without-MySQL run is `evidence/test_runs/2026-10-15_after_S3b_pytest.txt`. The figures below are what the audit measured before development started.

1. **The Clinic "21/21" depends on query-ID-specific code.** Seven of the 21 conversions (Q010, Q011, Q012, Q013, Q014, Q016, Q020) go through converters gated on `query_id == "Q0xx"`. Most of them also need a **whole-file regex** that matches the exact Clinic PHP source text, with Clinic table names, column names, `$_GET` keys and default values. Two examples: `in_list_source_gate.py` hard-codes `SELECT id, full_name FROM patients WHERE id IN ($ids)` with default `'1,2'`, and `table_binding.py` hard-codes `ALLOWED_Q013_TABLES = frozenset({"patients"})`. The same pattern classes in another app are **not** converted. hard_crud Q002 (dynamic ORDER BY), Q003 (dynamic IN) and Q004 (conditional append) all stay UNRESOLVED. **By general rules alone, Clinic is 14 of 22 sites, not 21/21.**
2. **The Clinic benchmark has 22 SQL scenarios, not 21.** `22_prepared_statement.php` (`mysqli_prepare` + `mysqli_stmt_bind_param`) **appears in no output file**. It is silently dropped.
3. **Unsound recoveries produce wrong code without any warning.** SQL built in `foreach`, in `while` and in `switch` is treated as straight-line code, marked RECOVERED and CONVERTED. One case is a **DELETE**: `DELETE FROM users WHERE 1=0 OR id = :i` generated from a loop.
4. **An `ENUM` or `SET` column crashes the whole pipeline at stage 3/6** (`ValueError: Unsupported MySQL type: ENUM('ACTIVE','BLOCKED')`, and the values are upper-cased). Most real third-party PHP/MySQL apps use ENUM, so a live demo on an unseen app is at high risk.
5. **Generated `models.py` fails `configure_mappers()`** for a self-referencing FK and for two FKs to the same table.
6. **Committed and on-disk evidence is stale or missing.** `output/clinic_inventory_legacy_hard/` (untracked) shows **14/21**, not 21/21. `output/hard_crud/` and `docs/C3_EVALUATION_RESULTS.md` say 1/5 converted, but the code now gives 2/5. The MySQL validation results from 8 Oct are gitignored. Tracked Clinic tests read the untracked Clinic output folder.
7. **What does reproduce exactly:** Helpdesk **9 / 7 / 6 / 2 / 3**, simple_crud 4/4, mixed_crud 5 + Q006 unresolved, and `evaluation_summary.json` (byte-identical). The test suite gives **514 passed, 61 skipped (MySQL), 0 failed = 575 collected**.

---

## 1. Git readiness

### 1.1 Current state (real output)

```
$ git status
On branch database-migration
Your branch is ahead of 'origin/database-migration' by 11 commits.
Untracked files:
	C3_source_review.zip
	benchmarks/apps/helpdesk_legacy_holdout/
	docs/C3_100_Percent_Execution_Plan.md
	output/clinic_inventory_legacy_hard/
	output/helpdesk_legacy_holdout/
	output/integration_test/
nothing added to commit but untracked files present

$ git branch -a
* database-migration
  main
  remotes/origin/HEAD -> origin/main
  remotes/origin/database-migration
  remotes/origin/frontend-migration-
  remotes/origin/main
  remotes/origin/test-generation-behavior-validation

$ git remote -v
origin  https://github.com/HasarangaWeerasiri/Automated_Modernization_of_Legacy_Procedural_PHP_Web_Applications.git (fetch/push)

$ git log --oneline -15
01869d8 feat(c3): migrate Q020 cross-table UPDATE JOIN with safe binding
79b94b0 feat(c3): migrate Q016 conditional SQL concatenation with safe binding
ccea3c7 feat(c3): migrate Q013 dynamic table identifiers using allowlist
58b07af feat(c3): migrate Q012 dynamic ORDER BY using validated identifiers
ae569d6 feat(c3): migrate Q011 dynamic IN-list using safe expanding parameters
6a3ff39 feat(c3): support validated Q010 static IN-subquery migration
efd3647 feat(c3): integrate guarded Q014 conditional SQL migration
fbf4ee0 feat(c3): add Q014 conditional SQL diagnostic recovery and equivalence tests
a905a05 feat(c3): support cross-file PHP SQL recovery and safe parameter binding
877e4bc Improve SQL recovery diagnostics and cross-file PHP analysis
1a649ae Improve C3 SQL recovery and safe query conversion
5214a60 feat(c3): add unresolved stubs reason codes and integration tests
e2e1b97 docs(c3): add consolidated migration evaluation results
cc6da39 test(c3): add mixed and hard CRUD MySQL behavioral validation
fece818 feat(c3): improve data layer migration and add MySQL behavioral validation

$ git diff --stat
(empty: no modified tracked files)
$ git stash list ; git tag -l
(both empty)
```

No tracked files are modified. Tracked file count is 227, of which 131 are under `components/c3_data_layer`.

### 1.2 Untracked files by type

| Type | Path | Notes |
|---|---|---|
| Benchmarks | `benchmarks/apps/helpdesk_legacy_holdout/` (README, `database/schema.sql`, `legacy/*.php` ×9, `style.css`) | Original synthetic holdout. Commit it. |
| Benchmark tooling | `benchmarks/apps/helpdesk_legacy_holdout/evaluate_c3_holdout.py` | Old preliminary runner. **Writes inside the repo** (`evaluation_output/`) when run. Commit it as provenance, but don't run it again. |
| Output / evidence | `output/helpdesk_legacy_holdout/` (8 pipeline files + `conversion_report.json` + `generated_data_access.py`) | **Matches a fresh run byte-for-byte** (except the absolute path in `summary.json`). `generated_data_access.py` is identical to `data_access.py`. `conversion_report.json` (timestamp `2026-10-13T04:47:47Z`) is the first 6/9 measurement, made by the old runner. |
| Output / evidence | `output/clinic_inventory_legacy_hard/` | **STALE**: written 2026-10-12 10:40, before the Q010–Q020 commits. Shows `converted 14 / conversion_unresolved 7`, but current code gives 21/0. |
| Output / scratch | `output/integration_test/` | 4/4 simple-CRUD run from a manual integration check. Duplicates `output/simple_crud`. Scratch. |
| Docs | `docs/C3_100_Percent_Execution_Plan.md` | Your plan. |
| Docs (new, this audit) | `docs/C3_audit_report.md` | This file. Uncommitted. |
| Other | `C3_source_review.zip` (227 KB, 130 entries) | Snapshot of `src/c3` and `tests/`, **including 25 `__pycache__` entries and the gitignored MySQL result JSONs**. Duplicates tracked code. **Do not commit.** |

### 1.3 Secrets and junk scan

| Check | Result |
|---|---|
| Hard-coded passwords, API keys, tokens | **None found.** All DB code reads `C3_DB_PASSWORD`, `C3_BENCH_DB_PASSWORD` or `HOLDOUT_DB_PASSWORD` from the environment. `test_app.py:19` uses a value commented as a dummy. Helpdesk `db.php` defaults to `root` with an empty password (fixture only, no secret). |
| `.env`, `*.pem`, `*.key`, `*.sqlite`, `*.db`, `node_modules`, `.idea`, `.vscode`, `.DS_Store`, `Thumbs.db` | None in the working tree outside `.venv`. The only `*.pem` is `.venv/.../certifi/cacert.pem`, which is ignored. |
| DB dumps | `benchmarks/db_dumps/` is empty. `clinic.../database/seed.sql` holds synthetic data and is already tracked. |
| Virtualenv / vendor / caches | `components/c3_data_layer/.venv`, `vendor/`, `.pytest_cache`, all `__pycache__`: **ignored** (verified below). |
| Files > 5 MB, or even > 1 MB, outside `.git`/`.venv`/`vendor` | **None.** |
| Privacy | 13 output files contain 75 absolute paths with your Windows username (`C:\Users\sanjula\Desktop\...`) in `summary.json`, docstrings and `source_file` fields. This isn't a secret, but it is a portability and privacy point. |

`git check-ignore -v` results:

```
.gitignore:4:.venv/        components/c3_data_layer/.venv
.gitignore:13:vendor/      components/c3_data_layer/vendor
.gitignore:8:.pytest_cache/ components/c3_data_layer/.pytest_cache
.gitignore:6:__pycache__/  components/c3_data_layer/src/c3/__pycache__
.gitignore:6:__pycache__/  output/clinic_inventory_legacy_hard/__pycache__
.gitignore:49:**/tests/mysql_validation/hard_legacy_results.json  (and the other 8 result JSONs)
.gitignore:4:.venv/        .venv/Lib/site-packages/certifi/cacert.pem
NOT IGNORED  C3_source_review.zip
NOT IGNORED  output/integration_test/summary.json
NOT IGNORED  output/helpdesk_legacy_holdout/conversion_report.json
NOT IGNORED  output/helpdesk_legacy_holdout/generated_data_access.py
NOT IGNORED  benchmarks/apps/helpdesk_legacy_holdout/evaluate_c3_holdout.py
NOT IGNORED  docs/C3_audit_report.md
```

### 1.4 Lines to add to `.gitignore`

```gitignore
# ============================================
# Archives / review bundles (code snapshots duplicate tracked source)
# ============================================
*.zip

# ============================================
# Scratch pipeline outputs (not research evidence)
# ============================================
output/integration_test/
benchmarks/apps/*/evaluation_output/

# ============================================
# Secrets and local environment
# ============================================
.env
.env.*
*.pem
*.key

# ============================================
# Local databases / dumps with real data
# ============================================
*.sqlite
*.sqlite3
*.db
benchmarks/db_dumps/*
!benchmarks/db_dumps/.gitkeep

# ============================================
# Tool caches / misc OS junk
# ============================================
node_modules/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/
desktop.ini
```

The current file already covers `.venv/`, `venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `vendor/`, `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `*.tmp` and `*.log`.
**Decision needed (section 9):** the 9 MySQL validation result JSONs under `tests/mysql_validation/` are gitignored, but they are your *only* MySQL behavioural evidence (8 Oct, 18/18 checks). If you want them committed, remove `.gitignore` lines 34–52, or copy them into an `evidence/` folder first.

### 1.5 Licences

- `vendor/nikic/php-parser/LICENSE` (BSD-3-Clause) and `vendor/composer/LICENSE` are present locally. `vendor/` is ignored and restorable from the tracked `composer.lock`, so nothing needs committing. Cite nikic/PHP-Parser in the references.
- Benchmarks: `clinic_inventory_legacy_hard/README.md` says *"original synthetic benchmark, **not** an external real-world GitHub application"*. Helpdesk's README calls it a "separate framework-free procedural PHP/MySQL application" with new domain tables. **No third-party code is present, so no third-party licence is required.** Record in a manifest who authored the Clinic and Helpdesk fixtures and how they were obtained. Both READMEs read as if supplied from outside ("Copy the folder under your research repository…"), and an examiner may ask about provenance.

### 1.6 Which `output/` folders are evidence

| Folder | Status | Verdict |
|---|---|---|
| `output/simple_crud/` (tracked) | Fresh run identical except the absolute path in `summary.json` | **Evidence**, current. |
| `output/mixed_crud/` (tracked) | Small diffs: new `cross_file_diagnostics` field, `like_wildcard_parameters: []`, and the new `unresolved_query_q006` stub in `data_access.py`. Counts unchanged (5 + 1). | **Evidence**, slightly stale. Regenerate. |
| `output/hard_crud/` (tracked) | **Stale.** Committed `converted_queries: 1`, fresh run gives `2`. Q005 JOIN is now converted. | Evidence of an *earlier* state. Regenerate, or label as historical. |
| `output/evaluation_summary.json` (tracked) | Byte-identical to a fresh evaluator run (simple + mixed). | **Evidence**, current. |
| `output/c3_viva_demo/` (tracked) | 4/4 simple-CRUD copy, used by `tests/backend_verification/app.py`. | Keep (demo fixture). |
| `output/helpdesk_legacy_holdout/` (untracked) | Reproduces exactly. | **Key evidence: commit and tag as the 6/9 baseline.** |
| `output/clinic_inventory_legacy_hard/` (untracked) | **Stale (14/21)**, 12 Oct 10:40. | Decision: keep as a dated historical snapshot, or regenerate (section 9). Don't commit it as "21/21 evidence". |
| `output/integration_test/` (untracked) | Scratch duplicate. | Ignore. |

### 1.7 Suggested commit grouping (commands for you to run; I did not run them)

Run from the repo root in PowerShell. They create a new branch so the shared `database-migration` branch isn't disturbed.

```powershell
# 0. New branch from the current HEAD (01869d8)
git switch -c c3-pp1-baseline

# 1. Ignore rules (after pasting the section 1.4 lines into .gitignore)
git add .gitignore
git commit -m "chore: ignore review archives, scratch outputs, env and db files"

# 2. Helpdesk holdout benchmark (original synthetic fixture)
git add benchmarks/apps/helpdesk_legacy_holdout
git commit -m "test(c3): add Helpdesk legacy holdout benchmark (original synthetic, untouched)"

# 3. Helpdesk pre-adaptation baseline evidence + tag
git add output/helpdesk_legacy_holdout
git commit -m "evidence(c3): Helpdesk holdout pre-adaptation baseline 9/7/6/2/3 (code @01869d8, reproduced 2026-10-15)"
git tag -a c3-helpdesk-baseline-6of9 -m "Helpdesk holdout pre-adaptation baseline: total 9, recovered 7, converted 6, recovery-unresolved 2, conversion-unresolved 3. Generated by code at 01869d8; re-run 2026-10-15 gave identical outputs."

# 4. Clinic outputs: choose ONE (see section 9, decision 2)
#  4a. Preserve the stale 14/21 snapshot under a dated name, then regenerate:
Copy-Item -Recurse output\clinic_inventory_legacy_hard output\clinic_inventory_legacy_hard_snapshot_2026-10-12
cd components\c3_data_layer
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\clinic_inventory_legacy_hard\legacy --schema ..\..\benchmarks\apps\clinic_inventory_legacy_hard\database\schema.sql --output ..\..\output\clinic_inventory_legacy_hard
cd ..\..
git add output\clinic_inventory_legacy_hard output\clinic_inventory_legacy_hard_snapshot_2026-10-12
git commit -m "evidence(c3): Clinic outputs @01869d8 (21 converted: 14 general + 7 query-specific templates; S22 not detected) + 12-Oct 14/21 snapshot"

# 5. Docs
git add docs\C3_100_Percent_Execution_Plan.md
git commit -m "docs(c3): add PP1 execution plan"
# (optional, after you have read it)  git add docs\C3_audit_report.md ; git commit -m "docs(c3): add pre-development audit report"

# 6. Push branch and tag
git push -u origin c3-pp1-baseline
git push origin c3-helpdesk-baseline-6of9
```

Don't add `C3_source_review.zip` or `output/integration_test/`.
Optional: regenerate `output/hard_crud` and `output/mixed_crud` in the same way and commit them as `evidence(c3): refresh hard/mixed outputs @01869d8`. Also update `docs/C3_EVALUATION_RESULTS.md`, which still says 113 tests and hard_crud 1/5.

---

## 2. Environment

| Tool | Status | Version / note |
|---|---|---|
| Python (venv) | Installed | 3.13.5 (`components/c3_data_layer/.venv`) |
| PHP | Installed | 8.2.12 (XAMPP, `C:\xampp\php`). Extensions include `mysqli`, `mysqlnd`, `PDO`, `pdo_mysql`, `pdo_sqlite` |
| Composer | Installed | 2.10.3. `vendor/nikic/php-parser` present (`^5.9`) |
| MySQL server | **Running but unusable for this audit** | Something listens on `:3306` and `:33060` (X-protocol, so MySQL 8.x). The XAMPP client is MariaDB 10.4.32 and fails with `Plugin caching_sha2_password could not be loaded`. No `C3_*` credentials are set in the environment. **No MySQL test could run.** |
| MySQL CLI on PATH | Missing | Only `C:\xampp\mysql\bin\mysql.exe` (MariaDB client) exists |
| Docker | CLI installed, **daemon not running** | `Docker version 29.4.0`; `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` |
| git | Installed | 2.53.0.windows.1 |

`pip freeze` (venv), relevant packages:

| Package | Present? |
|---|---|
| SQLAlchemy | ✅ 2.1.3 |
| sqlglot | ✅ 30.21.0 |
| pytest | ✅ 9.1.1 |
| PyMySQL | ✅ 1.2.3 |
| sqlacodegen | ✅ 4.0.4 (installed but never imported in `src/`) |
| fastapi | ✅ 0.142.4 (installed, **not in `requirements.txt`**) |
| **alembic** | ❌ **Not installed, not in `requirements.txt`, not referenced anywhere in code** |

Packaging notes:
- `requirements.txt` is **UTF-16 LE with BOM** (a PowerShell `pip freeze >` artefact) and omits fastapi, starlette, httpx and uvicorn.
- `pyproject.toml` is **0 bytes**. `c3` is not installed, so every command needs `PYTHONPATH=src`. `python -c "import c3"` gives `ModuleNotFoundError: No module named 'c3'`.

---

## 3. Claims vs measured results

> **Update after the audit (current test figure):** 642 passed (581 without MySQL + 61 MySQL), 0 failed, as of 2026-10-15. The MySQL run is in `evidence/mysql_2026-10-15/full_suite_with_mysql_log.txt`, and the without-MySQL run is `evidence/test_runs/2026-10-15_after_S3b_pytest.txt`. The figures below are what the audit measured before development started.

| Item | Your claim | Measured (2026-10-15) | Match |
|---|---|---|---|
| Test suite | 575 (roadmap: "575 passing") | `514 passed, 61 skipped, 1 warning in 10.36s` = **575 collected**. 61 skips are all MySQL (`C3_DB_PASSWORD not configured` / `MySQL credentials are not configured`). Warning: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`. | **Yes, as a count.** "575 *passing*" cannot be verified without MySQL, and no log of a 575-pass run exists in the repo. |
| Test suite | 104 (Day 5 diary) | `git grep "def test_"` at `ef95841` (7 Oct, "add ground truth evaluation"): **104 test functions** | **Yes** (historical) |
| Test suite | 113 (`C3_EVALUATION_RESULTS.md`) | At `fece818` (8 Oct): **113 test functions** | **Yes** (historical, doc now stale) |
| simple_crud | 4/4 recovered | total 4, recovered 4, converted 4, unresolved 0 | **Yes** |
| mixed_crud | 5 recovered + 1 unresolved (Q006) | total 6, recovered 5, converted 5, unresolved 1 (Q006 `UNKNOWN_EXPR`, `buildQuery`) | **Yes** |
| hard_crud | 1/5 converted (docs and committed output) | total 5, recovered 2, **converted 2** (Q001 LIKE, Q005 JOIN), unresolved 3 | **No**: committed evidence and docs are stale |
| Clinic | 21/21 converted | `total 21, recovered 16, converted 21, unresolved 5, conversion_unresolved 0` | **Partly.** Count matches, but (a) the benchmark has **22** sites and S22 is not detected, (b) 7/21 conversions are query-ID-specific templates, (c) the on-disk output shows 14/21 |
| Helpdesk | total 9, recovered 7, converted 6, unresolved 2, conversion-unresolved 3 | `total 9, recovered 7, converted 6, unresolved 2, conversion_unresolved 3` | **Yes, exactly.** All 8 files identical to the untracked output |
| `evaluation_summary.json` | simple + mixed 100% | Re-run is byte-identical to the committed file | **Yes** |
| hard_crud evaluation | (not claimed) | `table_accuracy 0.0`, read/filter 50%. **Cause: ground-truth format mismatch.** `hard_crud/queries.json` uses `expected_tables` (list), but `evaluator.py:126` reads `expected_table` | n/a (evaluator/GT defect) |
| Day 3 behavioural DB | "portable CRUD" | Day 3 diary: *"Isolated in-memory SQLite test database"* and *"authentication using the available local root credentials failed… isolated SQLite validation was used instead."* | Confirmed **SQLite** |
| MySQL 8 validation (8 Oct) | 18/18 checks | Local, **gitignored** result files: `comparison_report.json` 5/5, `mixed_comparison_report.json` 7/7, `hard_comparison_report.json` 6/6, all `"database": "MySQL 8.0"`. **Not re-run** (no credentials). | Exists locally, not reproducible from the repo |
| Pipeline runtime | — | 0.38–0.73 s wall-clock per app (simple 0.38, mixed 0.40, hard 0.41, clinic 0.44, helpdesk 0.73) | — |

Per-query Clinic breakdown (fresh run):

| Route | Query IDs | Count |
|---|---|---|
| General recovery + general converter | Q001–Q009, Q015, Q017, Q018, Q019, Q021 | **14** |
| Query-ID-specific converters (whole-file regex or exact-AST gate) | Q010 (exact AST equality with a hard-coded SQL string), Q011, Q012, Q013, Q014, Q016, Q020 | **7** |
| Not detected at all | S22 `22_prepared_statement.php` (`mysqli_prepare`) | **1** |

Diffing temp outputs against on-disk outputs: simple and Helpdesk are SAME. mixed differs in 4 files (new fields and the stub). hard differs in 5 files (Q005 now converted). Clinic differs in `converted_queries.json` (101 lines), `data_access.py` (85), `queries.json` (22) and `summary.json`.

Two failures appeared in a first pytest run (`test_integration_adapter_success`, `test_c3_integration_contract`): `FileNotFoundError` when the output path exceeded the Windows 260-character limit (because of my long temp path). With a short temp dir, both pass. **Robustness note:** the pipeline has no long-path handling.

---

## 4. FR1–FR10 status (with evidence)

Probes were tiny PHP files written to `%TEMP%\c3audit\probes\` and run through `python -m c3.cli run`.

| FR | Status | Evidence |
|---|---|---|
| **FR1** SQL identification | **Partial** | Detected and converted: `mysqli_query` (proc), `$mysqli->query` (OO), `mysql_query`, PDO `->query`, PDO `->exec`. Detected but **never converted**: `->prepare` with `?` or `:name` placeholders (`"Dynamic placeholder occurs in an unsupported structural SQL position."`), so the *safest* legacy style always fails conversion. **Silently dropped** (no output row at all): `mysqli_prepare`, `mysqli_multi_query`, `mysqli_real_query`. Probe: 11 call sites in, 8 rows out. The same drop hits Clinic S22. Extractor list: `extract_sql_calls.php:628–717`. Tests: `test_sql_locator.py` (7). |
| **FR2** fragmented / conditional SQL | **Partial (with soundness bugs)** | ✅ concatenation, interpolation `{$x}`, reassignment (last wins), unconditional `.=`. Abstains (safe): if/else, ternary, same-file function, heredoc, `sprintf`, include-defined `$sql` (`"No recoverable assignment chain found."`), `if` without `else`, conditional `.=`. ❌ **Wrong (unsound):** `foreach` append, giving `SELECT id FROM users WHERE 1=0 OR name = :n` (CONVERTED); `while` append on a DELETE, giving `DELETE FROM users WHERE 1=0 OR id = :i` (CONVERTED); `switch`, where only the default branch `SELECT id FROM teams` is CONVERTED. Branch conversion exists only for the Q014/Q016 Clinic templates. Cross-file function works only on the Clinic Q015 shape; mixed Q006 `buildQuery` stays unresolved. |
| **FR3** schema recovery | **Partial** | `schema.json` has: base type, length/precision, nullability, PK (incl. composite), FK (incl. self-ref), auto-increment, raw defaults. **Lost:** UNIQUE (inline and `UNIQUE KEY`; Clinic loses `uq_department_code`, Helpdesk loses `team_name UNIQUE`), indexes (Clinic `idx_patients_department`, `idx_appointments_patient`), `ON DELETE`/`ON UPDATE`, `CHECK`, FK constraint names. `UNSIGNED` is kept in `schema.json` but dropped in models. Defaults are **not** emitted to `models.py`. **ENUM/SET crash the pipeline** and are upper-cased. Tests: `test_schema_reader.py` (6), `test_type_map.py` (10). |
| **FR4** column use | **Partial** | `SELECT *` gives `['*']` (not expanded). `SELECT u.name AS n, t.name … JOIN … ON t.id = u.team_id … ORDER BY t.name` gives `R=['name'] F=['status']`: the two `name` columns merge, there's no table attribution, and JOIN-ON and ORDER BY columns are missing. `GROUP BY team_id HAVING SUM(balance)>5` gives `R=['team_id'] F=[]`, so the HAVING column is lost. Clinic Q020 misses `d.hourly_rate` (read). Q017 misses `fee` as read in `fee = fee + :delta`. Clinic Q011/Q013 are CONVERTED but column usage says `UNKNOWN/UNRESOLVED`. Tests: `test_column_analyzer.py` (11). |
| **FR5** ORM generation | **Partial** | `configure_mappers()` OK and `create_all` on SQLite OK for Clinic, Helpdesk, simple, mixed and hard. Clinic and Helpdesk relationships are present on both sides. Composite PK OK (`Membership PK=['user_id','team_id']`). ❌ Self-reference gives `ArgumentError: Team.parent and back-reference Team.teams are both of the same direction ONETOMANY. Did you mean to set remote_side…`. ❌ Two FKs to the same table generate **duplicate attribute** `tasks = relationship(...)` twice, giving `NoForeignKeysError`. Class name `people` → `People`. No MySQL runtime ORM test. Tests: `test_model_builder.py` (9). |
| **FR6** data access | **Partial (Demonstrated for supported single-statement cases)** | All generated code is `text()` + named bind params. **No ORM constructs** (0 `select(`/`insert(`/`update(`/`delete(`). Supports SELECT/INSERT/UPDATE/DELETE, static INNER JOIN SELECT, aggregates, LIKE (`f"%{q}%"` value wrapping, still bound), LIMIT/OFFSET bound. Dynamic WHERE, ORDER BY and IN only through the Clinic templates. Generated Clinic code imports `c3.conversion.*` at runtime, so it is **not standalone**. Q019 `LIMIT :limit` is tested only with ints `(2, 0)`. With `$_GET` strings, pymysql renders `'10'` (verified via `escape_item`), and MySQL is expected to reject that (**not verified on MySQL**). Behaviour tests: SQLite (`test_behavioral_execution.py`, 10, pass). MySQL ones skip. |
| **FR7** safety | **Partial** | ✅ A general AST check (`query_converter._has_unsafe_placeholder_position`) allows placeholders only in scalar value positions (comparison RHS, INSERT VALUES, IN items, LIMIT/OFFSET, `col + :x`). The recovery reason code `STRUCTURAL` flags ORDER BY, IN-fragment and table identifiers. `WHERE {{where}}` raw fragment is rejected (via a parse error, not an explicit rule). ❌ Allowlists are **hard-coded per query** (`{"id","full_name"}`, `{"patients"}`), not derived from `schema.json`. ❌ No general superglobal→SQL detection or report (`_GET` appears in src only inside the Q-specific gates and binding plans). No `security_report.json`. No injection-negative tests. |
| **FR8** abstention | **Partial (two classes of violation)** | `unresolved_queries.json` rows carry `source_file`, `source_line`, `reason_code`, `reason`, `action: manual_review_required`, but **only for recovery-unresolved**. Conversion-unresolved items (Helpdesk Q007, PDO `prepare`) are missing from it and appear only in `converted_queries.json` (reason, no action) plus a `NotImplementedError` stub. **Silent drops:** 3 APIs (FR1), including Clinic S22. **Should-have-been-flagged but converted:** loop, while-DELETE, switch (FR2). **Contradiction:** Clinic `unresolved_queries.json` lists Q011, Q012, Q013, Q014, Q016 as `manual_review_required` while `converted_queries.json` marks them CONVERTED. Q015 says `"safe_for_conversion": false, "resolution_status": "DIAGNOSTIC_ONLY"` yet is CONVERTED. `summary.json` reports converted (21) > recovered (16). |
| **FR9** Alembic | **Not implemented** | No `alembic` package, dependency, code, or artefact. The only mention in the repo is the execution plan. |
| **FR10** traceability | **Partial** | Every `queries.json`/`converted_queries.json` row and every generated function docstring has file + line (absolute path). `analysis/traceability.py` exists with tests (`test_traceability.py`, 4) but is **not called by `pipeline.py`**. The only `traceability.json` is the Day 4 committed `output/mixed_crud/traceability.json`. Cross-file Q015 records `function_source_file` (`query_factory.php`) but no line. Include-defined `$sql` is not resolved. |

---

## 5. Deliverables checklist

| Deliverable | Exists | Generated by pipeline? | Non-empty / valid | Test coverage | Real or stub |
|---|---|---|---|---|---|
| `schema.json` | ✅ | ✅ stage 3 | ✅ | `test_schema_reader` | Real (lossy, see FR3) |
| `queries.json` | ✅ | ✅ stage 1 | ✅ | `test_sql_recovery` etc. | Real |
| `converted_queries.json` | ✅ | ✅ stage 2 | ✅ | `test_query_converter` + Q-tests | Real (7 Clinic rows template-based) |
| `models.py` | ✅ | ✅ stage 3 | ✅ compiles. Fails mapper config on self-ref and dual FK | `test_model_builder` | Real |
| `data_access.py` | ✅ | ✅ stage 4 | ✅ compiles | `test_emitter`, SQLite behaviour tests | Real (imports `c3` runtime) |
| `column_usage.json` | ✅ | ✅ stage 5 | ✅ | `test_column_usage_generator` | Real (partial) |
| `unresolved_queries.json` | ✅ | ✅ stage 6 | ✅ | `test_unresolved_report` | Real (recovery-unresolved only) |
| `summary.json` | ✅ | ✅ | ✅ (absolute path, no timings) | `test_integration_contract` | Real |
| `traceability_report.json` | ⚠️ module only | ❌ not in pipeline | Day-4 `output/mixed_crud/traceability.json` | `test_traceability` | Real module, not wired |
| Alembic (`alembic.ini`, `env.py`, `versions/`) | ❌ | ❌ | — | — | **Missing** |
| `migration_notes.md` | ❌ | ❌ | — | — | **Missing** |
| `security_report.json` | ❌ | ❌ | — | — | **Missing** |
| `schema_comparison.json` | ❌ | ❌ | — | — | **Missing** |
| `orm_mapping_test_log.txt` | ❌ | ❌ | — | — | **Missing** |
| `php_vs_sqlalchemy_equivalence.json` | ❌ | ❌ | Nearest: gitignored `tests/mysql_validation/*comparison_report.json` (8 Oct) | MySQL tests skip | **Missing** |
| Test protocol | ❌ | — | — | — | **Missing** |
| Schema-only baseline comparison | ❌ (`output/hard_crud/baseline_*.json` are an earlier *recovery* baseline, not schema-only) | — | — | — | **Missing** |
| Benchmark manifest | ❌ | — | — | — | **Missing** |
| Evidence register | ❌ | — | — | — | **Missing** |
| Output-contract doc | ⚠️ `contracts/c3_data_layer_contract.json` + `docs/C3_INTEGRATION_HANDOFF.md` | hand-made | ✅ (says "supports one referenced table per query", now outdated) | `test_integration_contract` | Real but stale |
| Demo script / UI | ❌ (`tests/backend_verification/app.py` is a FastAPI check over `output/c3_viva_demo`) | — | — | `test_app.py` (5) | **Missing** |
| `evaluation.json` / `evaluation_summary.json` | ✅ simple, mixed | ❌ hand-invoked (no CLI) | ✅ | `test_evaluator`, `test_evaluation_summary` | Real (no GT for Clinic/Helpdesk; hard GT format mismatch) |

---

## 6. Honesty and quality findings

### 6.1 Hard-coding inside `src/` (this affects the evaluation)

- **96 occurrences of literal query IDs (`Q010`–`Q020`) in 20 files**, plus `sql_recovery.py:1262` importing `validate_q014_source_shape`. Files:
  `conversion/{conditional_concat_binding, conditional_concat_converter, conditional_concat_source_gate, conditional_converter, in_list_converter, in_list_source_gate, input_binding (comment "Q015"), join_update_binding, join_update_converter, join_update_source_gate, order_by_binding, order_by_converter, order_by_source_gate, query_converter, table_binding, table_converter, table_source_gate}.py`, `generation/{emitter, branch_emitter}.py`, `mapping/branch_source_gate.py`. `conversion/in_list_binding.py` has no upper-case ID, but defines `resolve_q011_ids` and hard-codes the fallback `"1,2"`.
- Dispatch in `query_converter.py:162–242`: `if query.get("query_id") == "Q014"/"Q011"/"Q012"/"Q013"/"Q016"/"Q020"`, and `_is_supported_q010_subquery` (line 415) checks `query_id != "Q010"` and compares against a hard-coded SQL string.
- Clinic **table, column and key names** in src: `patients`, `doctors`, `appointments`, `full_name`, `hourly_rate`, `department_id`, `active`, `'1,2'`, `'ids'`, `'sort'`, `'table'`. Examples: `order_by_binding.py:4 ALLOWED_SORT_COLUMNS = frozenset({"id", "full_name"})`, `table_binding.py:5 ALLOWED_Q013_TABLES = frozenset({"patients"})`, `branch_emitter.py:30 "def select_doctors_q014(session, inputs):"`, `emitter.py:207 query.get("table") != "doctors"`.
- **Whole-file regexes** for Q011, Q012, Q013, Q014, Q016, Q020 (`*_source_gate.py`) accept only the exact Clinic file text (`\A<\?php … \Z`).
- **Benchmark names** in src: `analysis/column_usage_generator.py:59,66` (`"simple_crud"`) and `analysis/unresolved_report.py:96,103` (`"mixed_crud"`) as default paths in helper entry points. These are minor (not used by the pipeline) but should go.
- **Positive side:** the gates are fail-closed. They don't produce false conversions on other apps; they simply never fire there. The problem is how they're *counted*: Clinic 21/21 must not be presented as an algorithmic result. Of the 575 tests, **≈330 belong to the Q010–Q016/Q020 files** (q016 56, q020 41, q013 39, q011 34, q012 31, q014 ≈86, q015 29, q010 14), and 57 more are Clinic MySQL equivalence tests.

### 6.2 Generated code

- **Commits inside functions:** simple_crud has 3 (INSERT, UPDATE, DELETE), mixed 2, Clinic 4 (Q003, Q004, Q005, Q017), Helpdesk 1 (Q003 DELETE). Clinic Q020 instead says *"caller manages the transaction"*, so the policy is **inconsistent**. No `rollback` anywhere.
- **f-strings building SQL:** Clinic `text(f"SELECT id, full_name FROM patients ORDER BY {column}")` (Q012) and `text(f"SELECT id FROM {table} WHERE id = 1")` (Q013). Both are guarded by hard-coded allowlists. No `%`, `.format` or `+` SQL building was found. The other f-strings are value wrappers for LIKE (`"q": f"%{q}%"`), which are bound, so that's fine.
- Generated Clinic code does `from c3.conversion.order_by_binding import resolve_q012_sort` (and 5 similar imports), so `data_access.py` only runs where the C3 tool is on `PYTHONPATH`.
- Docstrings embed absolute local paths.

### 6.3 Tests

- **No absolute or Windows-only paths** in tests. They use `Path(__file__).parents[n]`.
- **Tests that depend on output folders:**
  - `test_behavioral_execution.py` and `test_model_builder.py` read the tracked `output/simple_crud/`.
  - `backend_verification/app.py` reads `output/c3_viva_demo/`.
  - `tests/benchmarks/clinic_hard/test_behavioral_equivalence.py` and `test_write_equivalence.py` read **untracked** `output/clinic_inventory_legacy_hard/data_access.py`. On a clean clone they can't run, and against the stale on-disk file they would exercise the 14/21 code.
- **Tests that depend on local MySQL:** 61 items in `tests/benchmarks/clinic_hard/*`. They hard-code `host="127.0.0.1"` (default port 3306), user `c3_tester`, and DBs `c3_clinic_legacy_test` / `c3_clinic_migrated_test`. They are skipped via `pytest.skip("C3_DB_PASSWORD not configured")` or `skipif`. **No xfail.**
- **Is there any MySQL-based test?** Yes: those 61 pytest items, plus the standalone `tests/mysql_validation/run_*.php|py` / `compare_*.py` scripts (not pytest). **Day 3 was SQLite.** The only recorded MySQL runs are the 8 Oct result JSONs, which are gitignored.
- `test_sql_recovery.py:19 test_recovery_returns_six_queries` hard-codes the mixed_crud count. That's fine as a fixture test, but note that the plan's change to "seven stages" won't affect it.

### 6.4 Stale, confusing or contradictory artefacts

- `output/helpdesk_legacy_holdout/conversion_report.json` and `generated_data_access.py` come from the old `evaluate_c3_holdout.py` (13 Oct 10:17). They agree with the pipeline (6/9), and the generated file is identical to `data_access.py`. Label them as legacy-runner artefacts.
- `evaluate_c3_holdout.py` still writes to `HERE / "evaluation_output"` inside `benchmarks/`. Its README still points there. Running it would recreate the deleted folder.
- `output/clinic_inventory_legacy_hard/` is stale (14/21). `output/hard_crud/` is stale (1/5). `docs/C3_EVALUATION_RESULTS.md` says 113 tests and hard 1/5. `docs/C3_INTEGRATION_HANDOFF.md` says "JOIN conversion is currently unsupported" and hard_crud "1 converted". `contracts/c3_data_layer_contract.json` says "supports one referenced table per query". **All are contradicted by current code.**
- `output/hard_crud/baseline_*.json` and `post_soundness_*.json` are an earlier *recovery* soundness baseline, not the schema-only baseline the proposal means. The names could mislead.
- Clinic's own `ground_truth/site_inventory.csv` marks 10 sites `ABSTAIN_EXPECTED` (08–16, 20) and 4 `REVIEW_REQUIRED`. The tool converts all of them except S22, so the "observed_status" column is empty. The benchmark author's expectation and the tool's 21/21 have not been reconciled.

### 6.5 Diary and roadmap claims not supported by code or evidence

| Claim | Problem |
|---|---|
| "Clinic 21/21 (100% on that benchmark)" | 22 sites. 7 conversions are query-specific templates. On-disk output says 14/21. |
| "575 passing tests" | Only 514 run without MySQL. No preserved log of 575 passes. |
| "FR1 Demonstrated" | 3 mysqli APIs silently dropped. Prepared statements never converted. |
| "FR8 Demonstrated" (sound abstention) | Loop, while and switch are converted unsoundly. Silent drops exist. Unresolved and converted lists contradict each other. |
| "FR10 Demonstrated" | Traceability report is not a pipeline output. Cross-file line is missing. |
| "Models: Team, Agent, Ticket with 2 FKs, 4 relationships" (Helpdesk) | ✅ Confirmed: `Agent.team`, `Agent.tickets`, `Team.agents`, `Ticket.agent`. |
| "All 8 core Helpdesk artifacts generated, non-empty, valid" | ✅ Confirmed. |
| "Helpdesk Q002/Q004 are conditional-control-flow INSERT/UPDATE" | Misleading: the SQL string itself isn't branch-dependent. The *call site* sits inside `if (POST)`/`else`. An independent probe (`k_insert_ifbody.php`) reproduces it. **This is a probable false abstention class, not a genuinely ambiguous query.** |

---

## 7. Prioritised gap list

Effort estimates assume one developer who knows the codebase. "Start" names the file or function to open first.

### P0 — blocks a defensible PP1 demo

| # | Gap | Effort | Start |
|---|---|---|---|
| P0-1 | **Query-ID-specific converters (Q010–Q020).** Either (a) disclose and report Clinic as "14/22 general + 7 guarded templates + 1 missed", or (b) replace with class-level rules: ORDER BY / table allowlist derived from `schema.json`, expanding IN for integer lists, bounded conditional `.=`. | (a) 1 h · (b) 1.5–2 days | `conversion/query_converter.py:160–242`, `*_source_gate.py` |
| P0-2 | **Unsound recovery of loop, while and switch.** Abstain when any assignment in the chain sits inside a loop or switch body. Add negative tests. | 3–4 h | `mapping/sql_recovery.py` `_find_relevant_assignments` / `_select_assignment_chain` (l.303–415) and the extractor's statement-context info |
| P0-3 | **Silent drops** (`mysqli_prepare`, `mysqli_multi_query`, `mysqli_real_query`, plus any unknown DB call). Add them to the extractor. Unhandled executors must produce an UNRESOLVED row. | 2–3 h | `tools/php_ast/extract_sql_calls.php:606–717` |
| P0-4 | **ENUM/SET crash.** Map to `sqlalchemy.Enum`, or record as unsupported in notes and continue. Preserve case. Never abort the whole pipeline on one column. | 2–3 h | `conversion/type_map.py:123`, `mapping/schema_reader.py` (upper-casing) |
| P0-5 | **Evidence hygiene.** Commit and tag the Helpdesk baseline. Decide on Clinic stale vs regenerated. Refresh hard/mixed. Fix the docs that state 113 / 1/5 / "JOIN unsupported". Stop Clinic tests reading untracked outputs (generate into `tmp_path`). | 2–3 h | section 1.7 commands, `tests/benchmarks/clinic_hard/test_behavioral_equivalence.py:33` |
| P0-6 | **A working MySQL for any equivalence claim.** No MySQL test can run today. Get the `c3_tester` password for the local MySQL 8 service, or start Docker Desktop. Run the 61 skipped tests and save the log. | 1–2 h (setup) | section 8, E1 |

### P1 — required for "100% within scope"

| # | Gap | Effort | Start |
|---|---|---|---|
| P1-1 | FR9 Alembic emitter + round trip | 5–7 h | new `generation/alembic_emitter.py`, `pip install alembic` |
| P1-2 | FR5 self-ref (`remote_side`) and multi-FK (`foreign_keys=`, unique relationship names) | 3–4 h | `conversion/model_builder.py` (relationship naming ~l.216) |
| P1-3 | FR3 UNIQUE, indexes, ON DELETE/UPDATE, CHECK, defaults → `server_default`, unsigned | 3–4 h | `mapping/schema_reader.py`, `model_builder.py` |
| P1-4 | FR8 consistency: add conversion-unresolved to `unresolved_queries.json`; drop converted items from it; fix `summary.json` semantics (converted > recovered); fix Q015 `safe_for_conversion` contradiction | 2–3 h | `analysis/unresolved_report.py`, `pipeline.py:79–92` |
| P1-5 | FR7 `security_report.json`: general superglobal→SQL taint count, schema-derived allowlist, injection-negative tests per generated function | 5–6 h | new `analysis/security_report.py` |
| P1-6 | Transaction policy (no hidden commits, or a `commit=` flag) + rollback test | 1–2 h | `generation/emitter.py` |
| P1-7 | Generated code standalone (inline helpers or ship a `c3_runtime.py` next to `data_access.py`) | 2 h | `generation/emitter.py` imports |
| P1-8 | LIMIT/OFFSET int coercion (Q019 class) | 1 h | `generation/emitter.py` |
| P1-9 | Prepared statements (`?` / `:name`) → conversion (map positional to named) | 2–3 h | `query_converter._has_unsafe_placeholder_position` |
| P1-10 | FR4 table-qualified columns, JOIN-ON / GROUP BY / ORDER BY / HAVING roles, `*` expansion from schema | 3–4 h | `analysis/column_analyzer.py` |
| P1-11 | FR10 wire `traceability_report.json` into the pipeline; record factory function line | 1 h | `pipeline.py`, `analysis/traceability.py` |
| P1-12 | Evaluator: fix the `expected_tables` vs `expected_table` mismatch; add a CLI; independent GT for Clinic and Helpdesk (student annotates) | 2 h + annotation | `evaluation/evaluator.py:126` |
| P1-13 | Helpdesk Q002/Q004 class ("single reaching definition inside a guarded block") → general rule, reported post-adaptation | 3–4 h (time-box) | `sql_recovery._detect_branching_assignment` (l.524) |

### P2 — polish and robustness

| # | Gap | Effort |
|---|---|---|
| P2-1 | Relative paths in outputs and docstrings (privacy and portability) | 1 h |
| P2-2 | `requirements.txt` → UTF-8 with fastapi/alembic; real `pyproject.toml` so `PYTHONPATH=src` isn't needed | 1 h |
| P2-3 | Windows long-path robustness | 0.5 h |
| P2-4 | Same-file function, heredoc, `sprintf`, ternary, include-defined `$sql` recovery (currently safe abstention) | 1 day+ |
| P2-5 | Remove benchmark-name defaults in `column_usage_generator.py` and `unresolved_report.py` | 0.5 h |
| P2-6 | Timing field in `summary.json` | 0.5 h |
| P2-7 | `scripts/demo.ps1` + recorded fallback | 2–3 h |

---

## 8. Experiments to run next (not run; each needs setup)

**E0. Make MySQL usable (pick one).**

Option A uses the existing local MySQL 8 service. The tests hard-code `127.0.0.1:3306` and user `c3_tester`.

```powershell
# in a MySQL 8 client that supports caching_sha2_password (not the XAMPP MariaDB client), as an admin:
#   CREATE USER 'c3_tester'@'localhost' IDENTIFIED BY '<throwaway>';
#   CREATE DATABASE c3_clinic_legacy_test; CREATE DATABASE c3_clinic_migrated_test;
#   GRANT ALL ON c3_clinic_legacy_test.* TO 'c3_tester'@'localhost';
#   GRANT ALL ON c3_clinic_migrated_test.* TO 'c3_tester'@'localhost';
$env:C3_DB_PASSWORD = Read-Host "C3 throwaway test DB password"   # session-only; never commit
```

Option B uses disposable Docker. Start Docker Desktop first. Port 3306 is taken, so the tests need a host/port env override first, because they hard-code `127.0.0.1:3306`.

```powershell
docker run -d --name c3-mysql-test -e MYSQL_ROOT_PASSWORD=throwaway_only -p 3317:3306 mysql:8.0
```

**E1. Real-MySQL equivalence (existing tests).**
```powershell
cd components\c3_data_layer
# regenerate the Clinic output first (tests read it)
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\clinic_inventory_legacy_hard\legacy --schema ..\..\benchmarks\apps\clinic_inventory_legacy_hard\database\schema.sql --output ..\..\output\clinic_inventory_legacy_hard
.\.venv\Scripts\python.exe -m pytest tests\benchmarks\clinic_hard -v -rs 2>&1 | Tee-Object ..\..\evidence\clinic_mysql_equivalence_log.txt
# plus the standalone simple/mixed/hard scripts:
php tests\mysql_validation\run_legacy.php ; .\.venv\Scripts\python.exe tests\mysql_validation\run_migrated.py ; .\.venv\Scripts\python.exe tests\mysql_validation\compare_behavior.py
```
Add Q019 with **string** inputs (`'2'`, `'0'`) to confirm or refute the LIMIT-quoting risk.

**E2. Schema vs live DB reflection.** Import each `schema.sql` into a disposable DB, then:
```python
from sqlalchemy import create_engine, inspect
insp = inspect(create_engine("mysql+pymysql://c3_tester:<pw>@127.0.0.1/c3_clinic_legacy_test"))
# compare insp.get_columns / get_pk_constraint / get_foreign_keys / get_unique_constraints / get_indexes
# against generated models.Base.metadata and schema.json, and write schema_comparison.json
```
Expected diffs today: missing UNIQUE (Clinic, Helpdesk), indexes (Clinic), defaults, unsigned.

**E3. Alembic round trip** (after P1-1):
```powershell
.\.venv\Scripts\python.exe -m pip install alembic
alembic -c output\<app>\alembic\alembic.ini upgrade head
alembic -c output\<app>\alembic\alembic.ini check
alembic -c output\<app>\alembic\alembic.ini downgrade base
```

**E4. Injection negatives on every generated function** (SQLite can run today; MySQL after E0). For each `def` in `data_access.py`, call it with `"' OR '1'='1"`, `"1; DROP TABLE users;--"`, `"%"`, `"_"`, unicode quotes, `"id; DROP TABLE x"` for identifiers, and `"abc"` for LIMIT. Assert that row count and table checksum are unchanged.

**E5. Unseen third-party app** (decision needed on which). Pin a commit and run once, untouched:
```powershell
git clone <url> benchmarks\apps\<name> ; git -C benchmarks\apps\<name> checkout <hash>
cd components\c3_data_layer
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\<name> --schema <its schema.sql> --output ..\..\output\<name>
```
**Expect** an ENUM crash (P0-4) if the schema uses ENUM. Record that as the pre-adaptation result.

**E6. Performance timing.**
```powershell
foreach ($a in 'simple_crud','mixed_crud','hard_crud') { Measure-Command { .\.venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\$a\legacy --schema ..\..\benchmarks\apps\$a\schema.sql --output $env:TEMP\c3t\$a } }
```
Today's wall-clock is 0.38–0.73 s per app, measured with bash `time`.

**E7. Re-run evaluator incl. hard_crud** after fixing the GT key mismatch, and add Clinic/Helpdesk GT.

---

## 9. Decisions you need to make

1. **Clinic presentation.** Disclose the 7 query-specific templates (and report 14/22 general), remove them, or generalise them before 19 Oct. This is the most important decision for the viva.
2. **Stale Clinic output.** Keep it as a dated 12-Oct snapshot (14/21) beside a regenerated folder, or discard it.
3. **MySQL evidence files.** Commit the 9 gitignored `tests/mysql_validation/*results*.json` / `*comparison_report.json` (your only MySQL proof) or not.
4. **Branch strategy.** Push to a new `c3-pp1-baseline` branch (recommended) or fast-forward `origin/database-migration` (11 commits).
5. **MySQL environment.** Existing local MySQL 8 with a `c3_tester` account, or Docker (requires making the test host and port configurable).
6. **Transaction policy** for generated functions: commit inside, or caller-controlled.
7. **W7 wording.** Upgrade to ORM constructs, or state "parameterized SQLAlchemy (`text()` + bind parameters)".
8. **Third-party apps** to use as unseen benchmarks (and whether ENUM support lands before their first run).
9. **`evaluate_c3_holdout.py`.** Keep as provenance (and fix its output path) or retire it.
10. **`C3_source_review.zip`.** Confirm it can stay out of git (recommended), and delete or move it yourself.

---

### Appendix: audit artefacts (outside the repo)

- Pytest logs: `C:\Users\sanjula\AppData\Local\Temp\claude\c--Users-sanjula-Desktop-Research-Automated-Modernization-of-Legacy-Procedural-PHP-Web-Applications\ab280332-7890-475a-85d2-34a96ff63f26\scratchpad\pytest\pytest_q_shortpath.txt` (514/61) and `...\pytest_q.txt` (long-path run, 2 failures).
- Fresh pipeline outputs: `C:\Users\sanjula\AppData\Local\Temp\c3audit\run\<app>\`
- Probe fixtures and outputs: `C:\Users\sanjula\AppData\Local\Temp\c3audit\probes\` and `...\probe_out\` (with ENUM, crash), `...\probe_out2\` (ENUM-free)
- Evaluator re-run: `C:\Users\sanjula\AppData\Local\Temp\c3audit\eval\`
- Diary text extracts: `C:\Users\sanjula\AppData\Local\Temp\c3audit\*.docx.txt`
