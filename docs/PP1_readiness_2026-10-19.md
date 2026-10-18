# C3 Data Layer Migration - PP1 readiness review, 19 October 2026

Project J26-SE-329, component C3 (Data Layer Migration), branch `database-migration`, HEAD `881e9b8af4069eb31dfd5539983a53d14a07cd67` (0 commits ahead of and 0 behind `origin/database-migration`). PP1 presentation: Tuesday 20 October 2026.

**How this review was done**
- It is a verification review, not development. No pipeline, ground-truth, evidence or frozen-HMS file was changed. The first version of this report was the only file written.
- **Later the same day, with approval,** the dashboard generator, the dashboard data, `app.js` and the dashboard tests were corrected so that the Studio matches the latest evidence (section 13.1).
- **Numbers.** Every number below is copied from the repository evidence named next to it. Results observed live during this review are marked **[this review]**. They come from runs in temporary folders outside the repository and are **not** saved as committed evidence.
- **No database.** No MySQL connection was made and no password was used. E1, E2 and E3 results are read from existing evidence only.
- **No PHP execution.** No uploaded application PHP was executed; C3 only parses PHP with its AST extractor.
- **Privacy.** No HMS SQL, dump value, input value or credential is reproduced; for HMS only counts are given.

---

## 1. Executive summary

**Decision: READY WITH LIMITATIONS.**

- **Workflow.** The C3 Migration Studio worked end to end in a real browser (headless Microsoft Edge) against the real `server.py` bridge [this review]: upload, analysis, counts, previews, a ZIP download that belongs to the job, controlled error messages, and the recorded NEWS-BUZZ fallback.
- **Requirements.**
  - Seven of the ten functional requirements are implemented and measured within their documented scope: FR1, FR3, FR5, FR7, FR8, FR9 and FR10.
  - Three are partial: FR2, FR4 and FR6.
  - This matches `docs/c3_fr_status.json` and the dashboard Functions view.
- **Evidence.** Every headline number was re-checked against its evidence file. Verified recovery accuracy exists only for the five applications with author-verified ground truth.
- **Test counts.**
  - **Evidence-backed totals: the latest committed logs**, both run 2026-10-18 at HEAD `1d93e93`, filed under `evidence/final/2026-10-19/`:
    - non-MySQL suite: 873 passed, 61 skipped (`evidence/final/2026-10-19/test_run_non_mysql.txt`);
    - full suite with MySQL: 934 passed, 0 failed (`evidence/final/2026-10-19/mysql/full_suite_with_mysql_log.txt`).
  - **Later runs without a saved log** [this review]. These are not committed evidence and are not shown on the dashboard:
    - 885 passed / 61 skipped at HEAD `881e9b8` (873 plus the 12 sqlacodegen-baseline tests from `bae8e6b`);
    - after the dashboard consistency fix and the FR9/FR10 reference update (section 13.1), 890 passed / 61 skipped (plus 5 new dashboard tests).
- **No hard blocker was found.** The dashboard inconsistencies found in the first version of this review (stale tests, E3 and sqlacodegen cards, and the FR9/FR10 references) were corrected the same day (section 13.1).

---

## 2. C3 architecture and pipeline overview

Entry point: `python -m c3.cli run --php <app root> --schema <schema.sql> --output <dir>` (`components/c3_data_layer/src/c3/pipeline.py:111 run_pipeline`). The CLI prints seven stages:

| Stage | What it does | Main code |
|---|---|---|
| 1 Recover legacy SQL | PHP AST extraction of SQL calls (mysqli, mysql, PDO method names, single-parameter wrappers); reconstruction of literals, concatenation, interpolation, assignment chains and block-contained branches; abstention on loops/switch, structural placeholders and SQL fragments | `tools/php_ast/extract_sql_calls.php` (`SQL_FUNCTION_ARGUMENT_POSITIONS` :114, `SQL_METHOD_NAMES` :130, `findSqlWrappers` :481); `src/c3/mapping/sql_locator.py:146 analyze_php_ast`; `src/c3/mapping/sql_recovery.py:1436 recover_queries_from_ast` |
| 2 Convert recovered queries | Parameterized conversion; static INNER JOIN SELECTs only; seven Clinic query-specific templates | `src/c3/conversion/query_converter.py:185 convert_query`, `:542 _is_supported_join_select` |
| 3 Schema + SQLAlchemy models | MySQL DDL parsing incl. ALTER TABLE; ORM classes (Core `Table` for tables without a primary key); Python-safe identifiers | `src/c3/mapping/schema_reader.py:565 read_schema`; `src/c3/conversion/model_builder.py:644 generate_models`; `src/c3/generation/identifiers.py:21 python_name`, `:42 assign_names` |
| 4 Data-access functions | `text()` with named bind parameters; stubs for unresolved sites | `src/c3/generation/emitter.py:683 generate_data_access`, `:127 _build_function`, `:244 _build_unresolved_stub` |
| 5 Column-use report | Tables/columns read, written and filtered per SQL statement | `src/c3/analysis/column_analyzer.py:343 analyze_sql_columns` |
| 6 Unresolved, traceability, security reports | Reason codes, file:line labels, generated-code scan; generated Python is compile-checked | `src/c3/analysis/unresolved_report.py:16`, `traceability.py:28`, `security_report.py:329`; `src/c3/pipeline.py:36 check_generated_python` |
| 7 Alembic environment | Initial-schema revision `0001_initial_schema` | `src/c3/generation/alembic_emitter.py:381 generate_alembic`, `:304 build_initial_revision` |

**Frontend:**
- The C3 Migration Studio is in `components/c3_data_layer/dashboard/` (`index.html`, `app.js`, `styles.css`, `data/dashboard_data.json`, `data/sample_run.json`).
- It is served, together with the local Try It bridge, by `server.py`, bound to 127.0.0.1 only.
- The bridge runs the same CLI in a temporary folder outside the repository (`%LOCALAPPDATA%\Temp\c3try`).

---

## 3. FR1-FR10 acceptance matrix

Classification key:
- **PASS** = implemented and measured within the stated scope;
- **PARTIAL** = part of the requirement is not implemented;
- **NOT VERIFIED** = no evidence.

All test counts are collected test cases [this review, `pytest --collect-only`].

| FR | Proposed requirement (verbatim, `docs/c3_fr_status.json`) | Implementing code | Supported functionality | Tests (collected cases) | Evidence | Apps evaluated | Class. | Remaining limitation |
|---|---|---|---|---|---|---|---|---|
| FR1 | "The system must identify database query operations within the legacy PHP source code. This includes 'mysqli', 'mysql', and PDO-based queries." | `tools/php_ast/extract_sql_calls.php` (:114, :130, `findSqlWrappers` :481); `mapping/sql_locator.py:146` | mysqli (procedural and OO), mysql, PDO `query`/`prepare`/`exec` by method name, single-parameter user wrappers | `test_sql_locator` (7), `test_executor_coverage` (21), `test_sql_wrappers` (13), `test_prepared_statements` (9) | `evidence/final/2026-10-19/s15/<app>.evaluation.json` (`detection`); `evidence/evaluation/*/evaluation*.json` | HMS, Dignity-Dev, Helpdesk, NEWS-BUZZ, Fee-Rainbow (verified GT) | PASS | PDO tested on fixtures only (no benchmark app uses PDO); wrapper detection limited to the single-parameter shape |
| FR2 | "SQL queries constructed using variables, string concatenation, or conditional statements must be reconstructed whenever they can be safely resolved." | `mapping/sql_recovery.py` (`_decode_php_string` :130, `_split_concat_expression` :203, `_collect_branch_sql_variants` :556, `_detect_loop_or_switch_dependency` :741, `_detect_structural_placeholder` :801, `_detect_sql_fragment_variable` :976) | Literals, escapes, concatenation, interpolation, assignment chains, SQL executed inside the same conditional block; values bound as parameters; abstention on loops/switch, structural placeholders and fragments | `test_sql_recovery` (16), `test_guarded_block_recovery` (14), `test_loop_switch_soundness` (16), `test_php_string_decoding` (22), `test_sql_fragment_variables` (15) | S15 files above | 5 verified-GT apps | PARTIAL | No general reconstruction of SQL whose text differs between branches (only Clinic templates Q014/Q016); `$from`-style structural values and uncertain structures are abstained |
| FR3 | "Database tables, columns, data types, primary keys, foreign keys, and relationships must be identified from the MySQL schema." | `mapping/schema_reader.py` (`read_schema` :565, `parse_create_table` :371, `apply_alter_table_statements` :435); `conversion/model_builder.py:277 _build_relationships` | CREATE TABLE + ALTER TABLE parsing, types, PK, FK, unique, indexes | `test_schema_reader` (6), `test_schema_fidelity` (9), `test_alter_table_schema` (4), `test_type_map` (10), `test_schema_comparison` (5) | `evidence/e2/2026-10-15/schema_comparison.json`; `evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json` | All 9 | PASS | Relationships come from declared foreign keys only |
| FR4 | "The tables and columns used by each recovered query must be identified, as well as how those columns are subsequently used within the application logic." | `analysis/column_analyzer.py` (`column_roles` :256, `analyze_sql_columns` :343, `analyze_recovered_query` :416) | SQL-level tables and columns per statement: read, write, filter | `test_column_analyzer` (11), `test_column_roles` (7), `test_column_usage_generator` (2) | S15 `column_use_accuracy` | 5 verified-GT apps | PARTIAL | Downstream PHP use of result columns (`$row['col']`, output, conditions) is not traced |
| FR5 | "The system must generate SQLAlchemy models with appropriate data types, keys, and relationships." | `conversion/model_builder.py` (`generate_models` :644, `generate_models_source` :426, `_build_column` :207, `_build_relationships` :277); `generation/identifiers.py` | ORM classes with types, PK, FK, relationships; Python-safe attribute names that keep database column names | `test_model_builder` (9), `test_standalone_generated_code` (5), `test_python_identifiers` (18), `test_baseline_sqlacodegen` (12) | E2 files above; `evidence/baseline/2026-10-19/summary.json` | All 9 | PASS | Tables without a primary key become SQLAlchemy Core `Table` objects, not ORM classes |
| FR6 | "SQL operations that can be safely recovered must be converted into parameterized SQLAlchemy data access functions." | `conversion/query_converter.py:185`; `generation/emitter.py:683` | Parameterized `text()` functions with named binds; static INNER JOIN SELECTs; Clinic templates reported separately | `test_query_converter` (19), `test_emitter` (10), `test_behavioral_execution` (10), `tests/benchmarks/clinic_hard/*` (63, MySQL) | `docs/C3_results_summary.md` (current-code table); `evidence/mysql_runs/2026-10-15_195730/e1/php_vs_sqlalchemy_equivalence.json`; `evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt` | All 9 (conversion counts); HMS (E1); Clinic (MySQL tests) | PARTIAL | LEFT JOIN and implicit comma-join conversion unsupported (six evaluated sites, section 8.3) |
| FR7 | "It must be verified whether runtime values can be safely parameterized, and dynamic values used in structural positions such as table names or column names must be identified." | `mapping/sql_recovery.py:801`, `:976`; `analysis/security_report.py:329 build_security_report`, `:283 scan_generated_code` | Structural-position detection and abstention; bound-parameter check; generated-code scan | `test_security_report` (9), `test_e4_injection` (5), `test_sql_fragment_variables` (15) | `evidence/e4/2026-10-17/`; `evidence/final/2026-10-19/e4/` | All 9 | PASS (within tested scope) | E4 is a bounded check: MySQL-dialect compilation with a recording session, no database execution; not proof of complete runtime injection safety |
| FR8 | "Queries that cannot be migrated confidently or safely must be flagged for human review rather than generating incorrect code." | `analysis/unresolved_report.py:16`; `generation/emitter.py:244` | Unresolved sites get stage, reason code, reason and a stub that raises; no SQL is generated for them | `test_unresolved_report` (6), `test_report_consistency` (15) | S15 files (TN outcomes) | Dignity-Dev, NEWS-BUZZ, Fee-Rainbow (apps whose GT has non-recoverable sites) | PASS | Correct-abstention accuracy measurable only where verified GT has non-recoverable sites |
| FR9 | "Alembic migration information required for the modernized data layer must be generated." | `generation/alembic_emitter.py:381`, `:304` | `alembic/` environment with initial-schema revision `0001_initial_schema` | `test_alembic_emitter` (7) | `evidence/mysql_runs/2026-10-15_173218/e3/`; `evidence/mysql_runs/2026-10-19_000500/e3/` | All 9 | PASS | Initial-schema revision only; no data migration |
| FR10 | "The original PHP file and source location must be maintained for each recovered database operation. This information can be used for review and debugging purposes." | `analysis/traceability.py:28`; docstrings in `generation/emitter.py`; `scripts/make_handoff.py` | `traceability_report.json`; "Migrated from <file>:<line>" in every generated function; file:line on every unresolved site | `test_traceability` (5), `test_handoff_site_labels` (8), `test_hms_handoff_signatures` (1) | S15 files (sites matched on file and line) | 5 verified-GT apps | PASS | `output/hms_kishan0725/HMS_HANDOFF.md` cites Q036 as `header.php:35` instead of `include/header.php:35` (documentation only; `signatures.json` is correct) |

**Agreement with `docs/c3_fr_status.json`:**
- My classification matches the status file for all ten FRs: seven "Implemented and measured" and three "Partial".
- Two wording notes, not disagreements:
  - **FR6 and FR7 text.** The status file says MySQL behavioural equivalence (E1), and runtime hostile testing on MySQL, exist "for HMS only". That is true of the E1 harness. However, Clinic also has 63 passing MySQL equivalence tests, a separate harness that includes `test_q002_sql_injection_is_blocked` (`evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt`).
  - **Dashboard wording.** The dashboard's "Not yet measured" list shows Clinic E1 as not measured. That is correct for the E1 harness, but it can mislead a viewer; explain it if asked.

---

## 4. Implementation coverage by application

| Application | Role (`dashboard_data.json` `apps`) | Verified GT | S15 accuracy | Conversion counts | E1 / MySQL equivalence | E2 | E3 | E4 | sqlacodegen baseline | Timing |
|---|---|---|---|---|---|---|---|---|---|---|
| HMS (kishan0725) | post-adaptation benchmark (third-party, no licence) | yes | yes | yes | E1 27 PASS / 0 FAIL / 17 INCONCLUSIVE | MATCH | PASS | 44/44 | parity (FK: none declared) | yes |
| Dignity-Dev | fresh independent holdout | yes | yes | yes | not measured | MATCH | PASS | 4/4 | parity (FK: none declared) | yes |
| Helpdesk | holdout baseline, post-adaptation after S13 | yes | yes | yes | not measured | MATCH | PASS | 8/8 | parity incl. 2 FK | yes |
| NEWS-BUZZ | post-adaptation benchmark (third-party) | yes | yes | yes | not measured | MATCH | PASS | 30/30 | parity (MyISAM, no FK) | yes |
| Fee-Rainbow | post-adaptation benchmark (third-party) | yes | yes | yes | not measured | MATCH | PASS | 40/40 | parity (FK: none declared) | yes |
| Clinic | development fixture | no | pipeline counts only | yes (15 general + 7 templates) | 63/63 MySQL tests (not E1 harness) | MATCH | PASS | 22/22 | parity incl. 5 FK | yes |
| simple_crud | development fixture | no | pipeline counts only | yes | 18/18 checks across simple/mixed/hard, 2026-10-08, old code (`evidence/mysql_2026-10-08/`) | MATCH | PASS | 4/4 | parity incl. 1 FK | yes |
| mixed_crud | development fixture | no | pipeline counts only | yes | (as above) | MATCH | PASS | 5/5 | parity | yes |
| hard_crud | development fixture | no | pipeline counts only | yes | (as above) | MATCH | PASS | 2/2 | parity incl. 4 FK | yes |

**Sources for the table:**
- E1: `evidence/mysql_runs/2026-10-15_195730/e1/php_vs_sqlalchemy_equivalence.json`.
- E2: `evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json`.
- E3: `evidence/mysql_runs/2026-10-19_000500/e3/<app>.json`.
- E4: `evidence/final/2026-10-19/e4/injection_report.json`.
- Baseline: `evidence/baseline/2026-10-19/summary.json`.
- Timing: `evidence/performance/2026-10-18/execution_time.json`.
- Clinic MySQL tests: `evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt`.

---

## 5. Successful end-to-end migration demonstrations

### 5.1 Live browser run: simple_crud [this review]

**Setup:**
- **Server:** the existing `components/c3_data_layer/dashboard/server.py` on `http://127.0.0.1:8765/` (PID 23852, started 2026-10-18 15:03). It was not started or stopped by this review. Its `server.py` on disk (last modified 14:55 on 18 Oct) predates its start, so it runs the current bridge code.
- **Browser:** headless Microsoft Edge with a fresh profile and the cache disabled, driven through the DevTools protocol.
- **Inputs:** a ZIP of `benchmarks/apps/simple_crud/legacy` (4 `.php` files) and `benchmarks/apps/simple_crud/schema.sql`. The ZIP was built in a temporary folder.
- Files were attached to the page's file inputs through DevTools; the operating-system file dialog itself was not exercised.

| # | Step | Result |
|---|---|---|
| 1 | Frontend and API | Page loaded; Try It status "Connected to the local C3 bridge on this computer…"; button "Analyze & Generate Data Layer" |
| 2-3 | ZIP and `.sql` selected | "simple_crud.zip (1 KB) selected", "schema.sql (1 KB) selected" |
| 4-5 | Analyze, wait | Progress text shown while running; button read "Analysing…"; progress hidden afterwards |
| 6-7 | Counts | SQL detected 4, recovered 4, converted functions 4, unresolved sites 0; "Converted by general rules: 4; by query-specific templates: 0"; badge "Live run on this computer"; label "Counts from this upload, no ground truth, not an accuracy figure"; no "%" in the result panels |
| 8 | Traceability | Each generated function cites its source: `simple_crud/create_user.php:9`, `delete_user.php:8`, `update_user.php:8`, `users.php:4`; `traceability_report.json` in the ZIP |
| 9-10 | Previews | `models.py` and `data_access.py` previews rendered |
| 11 | Alembic | Not previewed in the UI (by design); in the ZIP: `alembic/versions/0001_initial_schema.py` (`revision = "0001_initial_schema"`, `down_revision = None`) |
| 12 | Individual downloads | **Not provided by design**: the UI previews `models.py`/`data_access.py` and offers one complete ZIP |
| 13 | Complete ZIP | `c3_output_37200df8f3f4c240.zip` downloaded through the real link (state "completed") |
| 14 | ZIP contents | 15 files (section 6); `models.py` and `data_access.py` compile |
| 15 | Belongs to this job | Run ID `37200df8f3f4c240` in the file name and in `summary.json` `output_directory`; `models.py` and `data_access.py` in the ZIP are identical to the UI previews; `summary.json` counts equal the UI counts (4/4/4/0). The run folder was deleted after the download (`/api/runs/<id>/summary` then returned 404) |

### 5.2 Live bridge run: Helpdesk [this review, API]

This run went through the same bridge, via HTTP, not the browser.

**Inputs:** a ZIP of `benchmarks/apps/helpdesk_legacy_holdout/legacy` (9 `.php` files, 1 other file ignored) and its `database/schema.sql`.

**Result:** 9 detected, 9 recovered, 8 converted, 1 unresolved:
- the unresolved site is Q007 `helpdesk/report.php:5`, at the conversion stage, reason `CONVERSION_UNSUPPORTED` (the documented LEFT JOIN);
- this equals the Helpdesk row of `docs/C3_results_summary.md`;
- the run folder was deleted after the download.

### 5.3 Controlled errors

**Observed in the browser [this review]:**
- Only the ZIP selected: the button stays disabled (`aria-disabled="true"`).
- ZIP without `.php`: "The ZIP contains no .php files."
- Schema without CREATE TABLE: "The schema file contains no CREATE TABLE statement."
- Schema with the wrong extension: "The schema must be a .sql file."

**Covered by passing tests, not exercised in the browser:**
- **Failed processing** (stage, classification and kept log): `test_http_failure_returns_stage_classification_and_log`, `test_describe_failure_names_stage_frame_generated_line_and_c3_defect`, `test_failed_run_keeps_only_a_sanitized_log` and `test_try_it_shows_the_failure_details`.
- **Size limits, zip-slip, symlinks, concurrency and timeout:** `test_tryit_bridge.py`, 42 cases.

---

## 6. Actual generated artifacts

The files the pipeline actually writes, as in the simple_crud ZIP [this review]; the CLI lists them as "Primary outputs" and "Supporting artifacts":

| File | Purpose |
|---|---|
| `models.py` | SQLAlchemy models (primary) |
| `data_access.py` | Parameterized data-access functions and unresolved stubs (primary) |
| `unresolved_queries.json` | Sites flagged for review (primary) |
| `column_usage.json` | Column use per SQL site |
| `converted_queries.json` | Conversion result per site, incl. `conversion_rule` |
| `migration_notes.md` | Notes, e.g. tables without primary key |
| `queries.json` | Recovered queries with file:line |
| `schema.json` | Parsed schema |
| `security_report.json` | Parameterization and generated-code scan |
| `summary.json` | Counts |
| `traceability_report.json` | Source file:line per operation |
| `alembic/alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial_schema.py` | Alembic environment with the initial-schema revision |

Not produced, by design: a `requirements.txt`, a README or a data-migration revision.

---

## 7. School application identifier-regression result

**The defect:** a MySQL column `class`, reached through a legal PHP variable `$class`, produced the Python identifier `class`, which is invalid. The fix is commit `f86dce5`: `generation/identifiers.py`, `model_builder.py`, `emitter.py`, and `pipeline.py check_generated_python`.

**Isolated CLI re-run [this review]:**
- **Inputs:** the school app (21 `.php` files) and its schema, taken from the earlier debugging copies in `%TEMP%\c3dbg`. These are outside the repository; the app's licence is unknown and nothing from it is in the repository.
- **Output:** written to `%TEMP%\pp1s`.

| Check | Result |
|---|---|
| Pipeline completes (incl. stage 6, security report) | Yes: all 7 stages, exit 0; 22 detected, 22 recovered, 22 converted (general rules), 0 unresolved |
| Valid Python identifier | `class_` (no Python keyword used as a name in `models.py`) |
| Original column name kept | `class_ = mapped_column("class", String(255), nullable=False)` |
| SQL placeholders and bind keys unchanged | `:class` in SQL (2 occurrences), bind dict `"class": class_` (2) |
| `models.py` / `data_access.py` compile | Both compile (`py_compile`) |
| ORM mapping preserves the schema | ORM metadata table and column names equal `schema.json` (6 tables, 32 columns) |
| Regression tests | `test_python_identifiers.py`: 18 cases pass, incl. reserved names, renames that never collide, keyword table names, keyword FK relationship names, keyword parameters binding under the original key, duplicate parameters after renaming, and a compile-check failure naming file, line and text |
| No unrelated output changed | `evidence/final/2026-10-19/gate/gate_result.txt`: 135 generated files of the 9 benchmark apps compared; content differences: none; "BYTE-IDENTICAL (content)" (raw differences only in `summary.json` run paths) |

**Gate provenance:**
- The "before" side is `before_2026-10-18.sha256.json`. Its source file was written at 14:41 on 18 Oct, before the fix was committed (15:09) and before the last edit of `identifiers.py` (14:44).
- The gate record itself does not name the commit of the "before" run.

**Distinctions:**
- **Python syntax validity** is verified by compiling.
- **ORM mapping** is verified at metadata level: names and types match the schema.
- **Runtime database equivalence was not tested** for the school app (no database).

---

## 8. S15 detection / recovery / conversion evidence

### 8.1 Verified accuracy (five apps with author-verified ground truth)

Re-evaluated on 19 Oct with current code: `evidence/final/2026-10-19/s15/<app>.evaluation.json`. The comparison with the committed evaluations is in `evidence/final/2026-10-19/comparison_with_earlier_results.txt`: no metric changed.

| App (stage) | Live sites | Detected | TP | FN | FP | TN (correct abstention) | Ambiguous excluded | Precision | Recall |
|---|---|---|---|---|---|---|---|---|---|
| HMS (post-adaptation, after H1) | 44 | 44 | 43 | 0 | 0 | 0 | 1 | 100.0% | 100.0% |
| Dignity-Dev (fresh independent holdout) | 13 | 13 | 7 | 0 | 0 | 6 | 0 | 100.0% | 100.0% |
| Helpdesk (post-adaptation) | 9 | 9 | 9 | 0 | 0 | 0 | 0 | 100.0% | 100.0% |
| NEWS-BUZZ (post escape fix) | 32 | 32 | 30 | 0 | 0 | 2 | 0 | 100.0% | 100.0% |
| Fee-Rainbow (post wrapper support + fragment rule) | 54 | 54 | 40 | 0 | 0 | 12 | 2 | 100.0% | 100.0% |

**Notes:**
- **Stage history** is in `docs/C3_results_summary.md` and `evidence/evaluation/<app>/`. For example, HMS recall went 44.2% → 93.0% → 100% (`evaluation_preadaptation.json`, `evaluation_pre_H1.json`, `evaluation.json`). Fee-Rainbow's first wrapper measurement had 10 wrong recoveries (precision 80.0%), disclosed in `evidence/evaluation/fee_rainbow/summary.md`.
- **Column use** (S15 `column_use_accuracy`) is 100% on every field for HMS, Helpdesk, NEWS-BUZZ and Fee-Rainbow. Dignity-Dev's write field is 71.4% (5/7, `evidence/evaluation/dignity_dev_hms/evaluation_first_holdout_run.json`).
- **Ground truth** is LLM-drafted blind and verified by one author (`benchmarks/ground truth/*/`).

### 8.2 Pipeline counts, not accuracy (no verified ground truth)

From `docs/C3_results_summary.md` (current-code table; "no author-verified ground truth: these are pipeline counts, not accuracy"):

| App | Detected | Recovered | Converted | Query-specific templates | Unresolved (recovery / conversion) |
|---|---|---|---|---|---|
| Clinic | 22 | 17 | 22 | 7 | 0 / 0 |
| simple_crud | 4 | 4 | 4 | 0 | 0 / 0 |
| mixed_crud | 6 | 5 | 5 | 0 | 1 / 0 |
| hard_crud | 5 | 2 | 2 | 0 | 3 / 0 |

### 8.3 Conversion (D2)

- **Clinic:** 22 conversions = 15 general-rule + 7 query-specific templates (Q010, Q011, Q012, Q013, Q014, Q016, Q020).
  - Sources: `docs/C3_results_summary.md`; `dashboard_data.json` measurement `clinic_inventory_legacy_hard|current code|general_rule_share` (15/22 = 68.2%); `docs/C3_audit_2026-10-17.md` §1.
  - The templates are ID- and file-text-dependent and must always be reported separately.
- **Fee-Rainbow:** 40 converted = 39 of 40 GT-recoverable sites (97.5%, `fee_rainbow|current code|conversion_coverage_gt`) + the ambiguous site G011. G007, also ambiguous, is not converted (`dashboard_data.json` `manual_review.apps.fee_rainbow.ambiguous`).
- **Dignity-Dev:** 4 of 7 recoverable sites converted (57.1%, `dignity_dev_hms|current code|conversion_coverage_gt`).
- **Other apps:** HMS 43/43, Helpdesk 8/9, NEWS-BUZZ 30/30 (same measurement family).
- **Six unsupported join-conversion sites**, all reason `CONVERSION_UNSUPPORTED` (`dashboard_data.json` `limitations` FR6; `docs/C3_results_summary.md`):

  | Join kind | App | Query / GT | Site |
  |---|---|---|---|
  | implicit comma join | Dignity-Dev | Q007 / G007 | `library.php:219` |
  | implicit comma join | Dignity-Dev | Q008 / G008 | `library.php:226` |
  | implicit comma join | Dignity-Dev | Q009 / G009 | `library.php:233` |
  | LEFT JOIN | Helpdesk | Q007 / G007 | `report.php:5` |
  | LEFT JOIN | Fee-Rainbow | Q007 / G007 (ambiguous) | `admin/app/models/categories.php:100` |
  | LEFT JOIN | Fee-Rainbow | Q054 / G058 | `app/views/products/detail.php:8` |

---

## 9. E1 / E2 / E3 / E4 evaluation summary

All read from existing evidence; nothing was re-run.

**E1, behavioural equivalence on MySQL:**
- **HMS:** 44 sites, **27 PASS, 0 FAIL, 17 INCONCLUSIVE**. INCONCLUSIVE is not counted as a pass.
  - Source: `evidence/mysql_runs/2026-10-15_195730/e1/php_vs_sqlalchemy_equivalence.json`, `git_head` `01f427e`, a pre-rewrite hash mapped in `evidence/commit_hash_map_2026-10-15.md`.
  - The first run's 5 FAILs were a harness defect: `evidence/hms/E1_triage/triage.md`.
- **Clinic:** 63 of 63 MySQL equivalence tests passed (MySQL 8.0.39, HEAD `1d93e93`). This is the Clinic test suite, not the E1 harness. Source: `evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt`.
- **simple/mixed/hard:** 18/18 checks on 2026-10-08, old code (`evidence/mysql_2026-10-08/`).
- **Not measured:** Dignity-Dev, Helpdesk, NEWS-BUZZ, Fee-Rainbow. Successful conversion is not behavioural equivalence.

**E2, schema fidelity:** all three comparisons MATCH on 9 of 9 apps:
- original DDL vs `schema.json` (A-B);
- original DDL vs ORM metadata (A-C);
- MySQL-reflected original DDL vs ORM metadata (D-C).

Sources: `evidence/e2/2026-10-15/schema_comparison.json` and `evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json`, both 15 Oct. The 19 Oct sqlacodegen baseline (section 12) independently found current-code model parity against the same reflected databases.

**E3, Alembic round trip on MySQL:** the steps are upgrade head, live schema vs models, `alembic check`, downgrade base.
- 9/9 PASS on 2026-10-15 (`evidence/mysql_runs/2026-10-15_173218/e3/`).
- 9/9 PASS on the current code on 2026-10-19 (`evidence/mysql_runs/2026-10-19_000500/e3/summary.txt` and `<app>.json`, HEAD `bae8e6b`). Every step in every app is PASS, with 0 schema diffs and 0 tables left after downgrade.
- **Scope:** initial-schema revision only.

**E4, injection negatives** (`evidence/e4/2026-10-17/README.md`, `injection_report.json`; repeated at HEAD `1d93e93` in `evidence/final/2026-10-19/e4/` with 0 changed cases):

| Measure | Value |
|---|---|
| Generated functions tested | 159, all SAFE |
| Hostile payloads per function | 12 |
| Hostile cases | 1,908 |
| Benign calls | 636 (4 per function) |
| Rejected cases | 120 (10 functions) |

**What E4 does and does not cover:**
- It is MySQL-dialect SQL compilation in a recording session; no database is executed in this E4 session.
- **44 fixed-SQL functions** received no hostile value as a bound parameter; their check is trivially satisfied.
- **Clinic Q013** rejects all four benign values too, so its benign-SQL comparison is vacuous; it is SAFE only because it rejects every payload.
- E4 alone does **not** prove injection safety.

---

## 10. Execution-time evaluation

Measured; source `evidence/performance/2026-10-18/execution_time.json` and `README.md`.

**How it was measured:**
- **Timer:** `time.perf_counter()` around the whole CLI subprocess, including Python start-up, with no database.
- **Runs:** 3 rounds per app.
- **Code:** HEAD `6769ae0`; the pipeline source is unchanged since `f86dce5`.
- **Machine:** Windows 11 Pro 10.0.26200, 11th Gen Intel Core i5-1135G7 @ 2.40 GHz (4 cores / 8 threads), 15.7 GB RAM, Python 3.13.5, PHP 8.2.12.
- **Scope:** one machine. This is not a benchmark of other hardware and not a measure of developer time.

| App | Sites detected | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) |
|---|---|---|---|---|---|
| HMS | 44 | 0.866 | 0.854 | 1.014 | 0.866 |
| Dignity-Dev | 14 | 0.748 | 0.739 | 0.811 | 0.748 |
| Helpdesk | 9 | 0.610 | 0.729 | 0.625 | 0.625 |
| NEWS-BUZZ | 32 | 0.949 | 1.037 | 0.976 | 0.976 |
| Fee-Rainbow | 54 | 0.929 | 1.018 | 0.896 | 0.929 |
| Clinic | 22 | 0.924 | 1.019 | 0.889 | 0.924 |
| simple_crud | 4 | 0.571 | 0.610 | 0.601 | 0.601 |
| mixed_crud | 6 | 0.565 | 0.615 | 0.592 | 0.592 |
| hard_crud | 5 | 0.611 | 0.925 | 0.650 | 0.650 |

---

## 11. Manual-review-site proxy

**This is a derived proxy, not measured developer hours.**

- **Formula** (`dashboard_data.json` `manual_review.formula`): (live sites unresolved at the recovery stage + live sites unresolved at the conversion stage) / live sites.
- **Counting rules:**
  - each site is counted once;
  - non-live sites are excluded;
  - ambiguous GT sites are included and listed.
- It says nothing about how long a site takes to migrate by hand, or whether converted code is correct.
- **Developer migration effort has not been measured**: `dashboard_data.json` measurement `all|not measured|migration_effort`.

| App | Unique live sites needing review / live sites | Rate | Recovery / conversion stage | Live-sites source |
|---|---|---|---|---|
| HMS | 0 / 44 | 0.0% | 0 / 0 | `evidence/evaluation/hms_kishan0725/evaluation.json` |
| Dignity-Dev | 9 / 13 | 69.2% | 6 / 3 (1 non-live site excluded) | `evidence/evaluation/dignity_dev_hms/evaluation.json` |
| Helpdesk | 1 / 9 | 11.1% | 0 / 1 | `evidence/evaluation/helpdesk_legacy_holdout/evaluation.json` |
| NEWS-BUZZ | 2 / 32 | 6.2% | 2 / 0 | `evidence/evaluation/news_buzz/evaluation_post_escape_fix.json` |
| Fee-Rainbow | 14 / 54 | 25.9% | 12 / 2 | `evidence/evaluation/fee_rainbow/evaluation_wrapper_support.json` |
| Clinic* | 0 / 22 | 0.0% | 0 / 0 | `summary.json` total (no GT) |
| simple_crud* | 0 / 4 | 0.0% | 0 / 0 | idem |
| mixed_crud* | 1 / 6 | 16.7% | 1 / 0 | idem |
| hard_crud* | 3 / 5 | 60.0% | 3 / 0 | idem |

\* Pipeline counts, not verified.

---

## 12. sqlacodegen schema-only baseline

Source: `evidence/baseline/2026-10-19/summary.json`, `summary.txt` and `README.md`. The script is `scripts/baseline_sqlacodegen.py`. It is an evaluation comparison only and is **not used by the pipeline**.

**Tool and setup:**
- sqlacodegen 4.0.4 (SQLAlchemy 2.1.5, PyMySQL 1.2.3), installed in a separate venv outside the repository.
- It reflected the existing E2 databases read-only.

**Results, summed over the 9 apps from `summary.json` `apps.*.comparison.counts`:**

| Dimension | Compared | Matches | Differences |
|---|---|---|---|
| Tables | 31 | 31 | 0 |
| Columns | 161 | 161 | 0 |
| Primary keys (per table) | 31 | 31 | 0 |
| Nullability | 161 | 161 | 0 |
| Types | 161 | 161 | 0 |
| Foreign keys | 12 | 12 | 0 |

- **sqlacodegen failures:** none (`summary.json` `failures`).
- **Foreign keys:** they exist in four apps only (Helpdesk 2, Clinic 5, simple_crud 1, hard_crud 4). The other five declare none, and NEWS-BUZZ's tables are MyISAM.
- **"Primary key" matches** include tables where neither tool finds a primary key, for example 4 of the 6 HMS tables.

**How to read it:**
- sqlacodegen is a models-only tool. That C3 also produces data-access functions, recovery and review lists is **by design, not a win** over sqlacodegen.
- The C3-only query counts are recorded as "not applicable" for sqlacodegen.
- The HMS baseline model file is kept uncommitted (`?? evidence/baseline/2026-10-19/hms_kishan0725/`).
- **On the dashboard:** the Evaluation view shows these totals in the "Schema-only baseline (sqlacodegen)" card as measured, with the source file and fields (section 13.1).

**Four separate kinds of result:**
- **Measured execution time:** section 10.
- **Derived manual-review-site rate:** section 11.
- **Schema-only comparison:** this section.
- **Developer migration effort:** not measured.

---

## 13. Frontend functionality and demo readiness

Observed in headless Edge on `http://127.0.0.1:8765/` with a fresh profile [this review]. Screenshots were taken in `%TEMP%\pp1b` and are not in the repository.

| # | Check | Observed result |
|---|---|---|
| 1 | Functions FR1-FR10 | 10 cards; "7 implemented and measured, 3 partial"; FR2, FR4, FR6 badge "Partial" |
| 2 | Overview | Rendered; 9 KPI cards; all-applications table |
| 3 | SQL Results | Rendered; site table |
| 4 | Models & Migration | Rendered; "Generated files", "Schema and migration verification" |
| 5 | Evaluation | Rendered; 15 evidence cards incl. E1, E4, E2/E3, manual review, execution time, "Not yet measured", known limitations |
| 6 | Reports | Rendered |
| 7 | Backend connection | "Connected to the local C3 bridge…"; no "connection pending" with the server running |
| 8 | ZIP and SQL inputs | Both accepted; button disabled until both are chosen |
| 9 | Live migration | simple_crud run completed (section 5.1) |
| 10 | Progress and errors | Progress text during the run; three controlled error messages (section 5.3) |
| 11 | Code previews | `models.py`, `data_access.py` |
| 12 | Downloads | Complete ZIP only; download link appears only after the run summary exists; individual-file download not provided by design |
| 13 | Recorded NEWS-BUZZ fallback | Badge "Recorded run of the NEWS-BUZZ benchmark, not live · recorded 2026-10-18 12:10"; counts 32 detected, 30 converted, 2 unresolved; "Download is only available for live runs." |
| 14 | Light theme default | Fresh profile: `data-theme="light"` |
| 15 | Dark toggle | Dark applied and kept after reload; toggles back to light |
| 16 | Responsive | At 390 px width, no horizontal scroll on Functions, Overview, SQL, Evaluation, Try It (scroll width = viewport width) |
| 17 | Functions navigation and direct URL | `#/evidence/models` opened directly; Functions sub-tab returns to `#/evidence/functions` |
| 18 | Refresh, Back/Forward | Reload kept the section; Back and Forward moved between `#/evidence/sql` and `#/evidence/reports` |
| 19 | Console errors | 0 JavaScript errors or exceptions. The only failed requests were the deliberate negative tests (3 × HTTP 400) and one deliberate 404 check after download |
| 20 | No persistent "connection pending" | Confirmed |

**Labels checked:**
- The application selector shows "Dignity-Dev - fresh independent holdout".
- NEWS-BUZZ's sample is labelled "Recorded … not live".
- Uploaded projects show counts with "no ground truth, not an accuracy figure" and no percentages.

**Not verified in this review:**
- the operating-system file chooser (files were attached through DevTools);
- a visible, non-headless browser window;
- browsers other than Edge;
- failure-path rendering with a real failing upload (covered by tests only).

**Not available in the UI:** per-file downloads and an Alembic revision preview. The generated files, including `alembic/versions/0001_initial_schema.py`, are all in the complete migration ZIP (section 5.1, steps 11-14).

### 13.1 Dashboard consistency fix (19 Oct, after the first version of this review)

**What changed:**
- `scripts/summarize_evidence.py` now reads the evidence below.
- `dashboard_data.json` was regenerated with `--no-markdown`.
- `app.js` renders the new data.
- `tests/test_dashboard_data.py` reconciles every new figure with its file.
- No evidence file, pipeline code or FR classification changed.

| Dashboard item | Before | After | Source |
|---|---|---|---|
| sqlacodegen baseline | "Not yet measured: Planned, not run" | New card "Schema-only baseline (sqlacodegen)", status measured. Completed 2026-10-19, sqlacodegen 4.0.4, 9 of 9 apps compared, failures none; tables 31/31, columns 161/161, primary keys 31/31, nullability 161/161, types 161/161, foreign keys 12/12 (Helpdesk, Clinic, simple_crud, hard_crud), 0 differences. The card states that sqlacodegen never reads PHP, that matching properties are not a correctness guarantee, and that no accuracy or speed comparison is made | `evidence/baseline/2026-10-19/summary.json`, fields `apps.<app>.comparison.counts.<property>.{compared,matches,differences}` and `failures`; method `evidence/baseline/2026-10-19/README.md` |
| Regression tests | One row: 783 passed / 0 failed / 61 skipped (17 Oct log) | Card "Regression tests (saved logs)" with three dated rows: non-MySQL 873/0/61 (run 2026-10-18, HEAD `1d93e93`); full suite with MySQL 934/0/0 (run 2026-10-18, HEAD `1d93e93`); earlier non-MySQL 783/0/61 (2026-10-17, HEAD `6e92711`, marked "history"). Only committed logs are shown; the test checks each file is tracked by git | `evidence/final/2026-10-19/test_run_non_mysql.txt`; `evidence/final/2026-10-19/mysql/full_suite_with_mysql_log.txt`; `evidence/tests/test_run_non_mysql_2026-10-17.txt` (pytest summary lines) |
| E3 | 2026-10-15 run only (9/9 PASS) | Per-application E3 now from the 2026-10-19 run (HEAD `bae8e6b`, 9/9 PASS, every step PASS); the 2026-10-15 run (HEAD `a791cd8`, 9/9 PASS) kept as dated history | `evidence/mysql_runs/2026-10-19_000500/e3/<app>.json` and `summary.txt`; `evidence/mysql_runs/2026-10-15_173218/e3/` |
| E1 card | HMS chart; Clinic only in "not measured" | Adds "Separate MySQL test suite (not the E1 harness)": Clinic 63 of 63, MySQL 8.0.39, run 2026-10-18 | `evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt` (summary line, "collected 63 items") |
| E4 limits | 5 items; "Runtime hostile testing on MySQL exists only for HMS (E1)" | First item: "A bounded check with a fixed payload set; it is not a proof of general injection safety." Last item names HMS (E1 harness) and the one Clinic MySQL injection test | `evidence/e4/2026-10-17/README.md` |
| Stage meaning text | "current code" described as "Fresh run … by this script" for every current-code item | Says that tests, E3, E4, timing and the baseline cite their own saved file, date and HEAD | presentation text only |

**Unchanged and confirmed in the browser:**
- Recovery, detection, conversion, abstention and manual-review charts show the same figures as before.
- Functions shows "7 implemented and measured, 3 partial".
- Dignity-Dev is labelled fresh holdout, with 9/13 sites needing review under the derived proxy.
- Clinic shows 15 general + 7 templates; Fee-Rainbow shows 40 = 39 + G011.
- HMS E1 shows 27 PASS / 0 FAIL / 17 INCONCLUSIVE.

**FR9 and FR10 references (approved separately).** In `docs/c3_fr_status.json`, only the measurement references were changed:
- FR9 now cites `all|current code (E3, 2026-10-19)|e3_roundtrip_pass` (from `evidence/mysql_runs/2026-10-19_000500/e3/`).
- FR10 now cites `all|saved log (2026-10-18, HEAD 1d93e93)|tests_passed` and `…|tests_with_mysql_passed` (from `evidence/final/2026-10-19/test_run_non_mysql.txt` and `evidence/final/2026-10-19/mysql/full_suite_with_mysql_log.txt`).
- The 2026-10-15 E3 and 2026-10-17 test measurements remain in the dashboard data as dated history.
- `test_fr9_and_fr10_cite_the_newest_committed_evidence` checks these references.

**Intermittent test (fixed in the test only).** `test_tryit_bridge.py::test_timeout_stops_the_run_and_cleans_up` failed in 1 of 20 runs of the dashboard and Try It tests:
- **Symptom:** an empty run folder was left after the forced timeout.
- **Cause:** on Windows, a just-killed child of the CLI can still hold the run folder as its working directory, so `rmtree` deletes every file but not the folder. This was reproduced in isolation.
- **What the test now checks:** no uploaded file remains, and the bridge's own cleanup removes the empty folder within 5 s.
- **Result:** 20 of 20 subsequent runs passed.
- **Not changed:** the product code (`dashboard/server.py`).

---

## 14. Known limitations

**Implementation scope:**
1. **FR1:** PDO tested on fixtures only; wrapper detection limited to the single-parameter shape.
2. **FR2:** no general reconstruction of branch-dependent SQL; structural values (e.g. `$from`) and uncertain structures are abstained. Only Clinic templates Q014/Q016 rebuild branch-dependent SQL.
3. **FR3:** relationships only from declared foreign keys.
4. **FR4:** SQL-level column use only; downstream PHP use of result columns is not traced.
5. **FR5:** tables without a primary key are Core `Table` objects (HMS: 4 tables, decision in `evidence/hms/H2_tables_without_pk/proposal.md`).
6. **FR6:**
   - LEFT JOIN and implicit comma-join conversion is unsupported (six evaluated sites).
   - The generated code is `text()` with bound parameters, not ORM query constructs.
   - The 7 Clinic templates are query-specific, not general rules.
7. **FR7:** E4 is bounded (12 payloads, compile-only, no database); 44 functions trivially satisfy it; Clinic Q013 rejects benign values.
8. **FR8:** correct-abstention accuracy is measurable only where verified GT has non-recoverable sites.
9. **FR9:** initial-schema revision only; no data migration.
10. **FR10:** a documentation limitation. `output/hms_kishan0725/HMS_HANDOFF.md` cites the Q036 site as `header.php:35` instead of `include/header.php:35`. The frozen `signatures.json` and the generated code carry the correct label, and the frozen handoff is not regenerated.

**Evidence and evaluation:**
11. **E1 coverage:** E1 only for HMS (17 of 44 sites INCONCLUSIVE). Clinic has its own MySQL tests. The other four third-party/holdout apps have no runtime equivalence evidence.
12. **Ground truth:** five apps only. It was drafted blind by an LLM from the same model family as the development assistant and verified by one author, so there is a shared-blind-spot risk.
13. **Effort and scale:** developer migration effort not measured; scalability not measured beyond 54 sites / 9 apps.
14. **Pre-rewrite hashes:** raw evidence `git_head` values from before 15 Oct are pre-rewrite hashes (`evidence/commit_hash_map_2026-10-15.md`).

---

## 15. Recommended future development

1. Convert LEFT JOIN and implicit comma joins (the six documented sites are the test set).
2. Trace result-column use into PHP logic to complete FR4.
3. General rules for branch-dependent SQL to replace the Clinic templates, or make the templates opt-in.
4. E1 runs for Dignity-Dev, Helpdesk, NEWS-BUZZ and Fee-Rainbow, and a re-run of HMS on current code.
5. Runtime hostile testing on MySQL beyond HMS and Clinic Q002; wider payload sets for E4.
6. Data-migration revisions in Alembic.
7. A real PDO-based benchmark application; multi-parameter wrappers.
8. A measured developer-effort study to replace the manual-review proxy.
9. Point the dashboard at the latest evidence: tests, E3 and the sqlacodegen baseline.

---

## 16. Exact demo launch commands

From the repository root in PowerShell:

```powershell
# 1. Check whether the Studio is already running (it was, as PID 23852, during this review)
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue

# 2. If nothing is listening, start it (no MySQL, no password needed)
components\c3_data_layer\.venv\Scripts\python.exe components\c3_data_layer\dashboard\server.py

# 3. Open the Studio
start http://127.0.0.1:8765/
```

- **Port already in use:** a second server on a busy port fails loudly by design (`test_port_already_in_use_fails_loudly`). Either use the running one or start another with `--port 8766` and open `http://127.0.0.1:8766/`.
- **Fresh look:** use a new private window, which starts in light theme with no cached files.
- **Demo inputs:** prepare them before the talk, as ZIPs in a temporary folder:
  - `benchmarks/apps/simple_crud/legacy` with `benchmarks/apps/simple_crud/schema.sql`;
  - optionally `benchmarks/apps/helpdesk_legacy_holdout/legacy` with its `database/schema.sql`.

---

## 17. Primary demo application and recorded fallback

- **Primary live demo:** simple_crud, a project-authored fixture. 4 detected, 4 converted, 0 unresolved; about 0.5 s in the bridge [this review]. It shows the whole workflow, previews and the ZIP.
- **Optional second live demo:** Helpdesk, also project-authored. 9 detected, 8 converted, 1 unresolved LEFT JOIN with `CONVERSION_UNSUPPORTED`. It shows safe abstention with file:line [this review, bridge API].
- **Recorded fallback:** "Load recorded sample run" (NEWS-BUZZ, recorded 2026-10-18; `components/c3_data_layer/dashboard/data/sample_run.json`). It is clearly badged as recorded, not live.
- **Never demo HMS live:** the source has no licence and the dump is private.

---

## 18. PP1 slide and viva evidence references

| Claim | Evidence |
|---|---|
| FR status | `docs/c3_fr_status.json`; `docs/C3_audit_2026-10-17.md` §2 |
| Detection / recovery accuracy | `evidence/final/2026-10-19/s15/`; `evidence/evaluation/<app>/` |
| Current-code counts, Clinic split | `docs/C3_results_summary.md` |
| Manual-review proxy, all measurements with sources | `components/c3_data_layer/dashboard/data/dashboard_data.json` |
| E1 | `evidence/mysql_runs/2026-10-15_195730/e1/`; `evidence/hms/E1_triage/triage.md` |
| Clinic MySQL equivalence | `evidence/final/2026-10-19/mysql/clinic_mysql_equivalence_log.txt` |
| E2 | `evidence/e2/2026-10-15/`; `evidence/mysql_runs/2026-10-15_173248/e2/` |
| E3 | `evidence/mysql_runs/2026-10-15_173218/e3/`; `evidence/mysql_runs/2026-10-19_000500/e3/` |
| E4 | `evidence/e4/2026-10-17/README.md`; `evidence/final/2026-10-19/e4/` |
| Tests | `evidence/final/2026-10-19/test_run_non_mysql.txt`; `evidence/final/2026-10-19/mysql/full_suite_with_mysql_log.txt` |
| Byte identity and frozen HMS hash | `evidence/final/2026-10-19/gate/gate_result.txt` |
| Timing | `evidence/performance/2026-10-18/` |
| sqlacodegen baseline | `evidence/baseline/2026-10-19/` |
| Hash mapping after history rewrite | `evidence/commit_hash_map_2026-10-15.md` |

**Viva questions to prepare:**
- Why is Clinic 22/22 not a generalization claim? Answer: 15 general + 7 templates.
- Why is INCONCLUSIVE not a pass?
- Why is E4 not proof of safety?
- Why is the manual-review rate not effort?
- Why does sqlacodegen "producing less" not mean C3 wins?
- What happens to the six join sites?

---

## 19. Git status and private-file safety review

**Repository state:**
- **Branch:** `database-migration`. **HEAD:** `881e9b8`. **Ahead of / behind origin:** 0 / 0. **Staged:** none. `git diff --check`: clean.
- **`git status --porcelain`** at the start of this review:

  ```
   M benchmarks/equivalence_cases/hms_kishan0725.json
   M benchmarks/equivalence_cases/hms_kishan0725_inputs.csv
  ?? evidence/baseline/2026-10-19/hms_kishan0725/
  ?? evidence/mysql_runs/2026-10-15_195730/e1/hms_kishan0725.json
  ?? output/clinic_inventory_legacy_hard/
  ?? output/hms_pp1_demo/
  ```

  At the end of the first version it was the same, plus `?? docs/PP1_readiness_2026-10-19.md` (this report).

  After the dashboard consistency fix (section 13.1), these were also modified:
  - ` M scripts/summarize_evidence.py`
  - ` M components/c3_data_layer/dashboard/app.js`
  - ` M components/c3_data_layer/dashboard/data/dashboard_data.json`
  - ` M components/c3_data_layer/tests/test_dashboard_data.py`
  - ` M components/c3_data_layer/tests/test_tryit_bridge.py` (intermittent-test fix, test only)
  - ` M docs/c3_fr_status.json` (FR9 and FR10 measurement references only)

  No evidence, pipeline, ground-truth, frozen-HMS or private file was touched, and nothing was committed.

**Private-file checks:**
- **Private HMS equivalence cases** remain uncommitted.
  - The committed versions are templates.
  - In the committed CSV, the "seed_examples (from the dump)" column holds one placeholder text in every row ("see the schema dump (not copied: no licence)").
  - The committed JSON holds only the synthetic hostile and type-mismatch cases.
  - The filled-in `normal` and `empty_result` cases exist only in the uncommitted working copy.
- **Other uncommitted private or temporary files:**
  - the private HMS MySQL-run JSON (`evidence/mysql_runs/2026-10-15_195730/e1/hms_kishan0725.json`);
  - the HMS sqlacodegen model file;
  - the temporary benchmark outputs (`output/clinic_inventory_legacy_hard/`, `output/hms_pp1_demo/`).
- **SQL files:** no third-party SQL dump is tracked. Tracked `.sql` files are project-authored fixture schemas, seeds and probes, plus `scripts/e1_create_databases.sql`, which contains no password and no `IDENTIFIED BY`.
- **Frozen HMS output:**
  - `output/hms_kishan0725/` is unchanged.
  - `signatures.json` SHA-256 is `FA8C1AFE46576D51A3939067B3699F488E2021B5B8F2A5CDEC2D7ED9D931DDC1`, matching the recorded value; it is identical to tag `hms-handoff-v1`.
  - Tags were not touched (`hms-handoff-v1` → `eea6a6e`).
- **Not C3, already reported:** in the 17 Oct audit (§4.11), another team's remote branch (`origin/frontend-migration-`) tracks an HMS dump. Raise it with that branch's owner.

---

## 20. Final readiness decision

**READY WITH LIMITATIONS.**

**Why:**
- The full workflow works live, and every reported number traces to committed evidence.
- The requirement statuses are honest: three Partial.
- The limitations are documented in the status file, the dashboard and this report.
- Readiness does **not** mean every requirement is fully supported for every application: FR2, FR4 and FR6 are partial, and runtime equivalence exists for HMS (E1) and Clinic (MySQL tests) only.

**Hard blockers:** none.

**Resolved on 19 Oct** (section 13.1):
- the FR9 and FR10 references, which pointed at older dated measurements;
- the sqlacodegen card, which said "Planned, not run";
- the regression-test card, which showed 783 from 17 Oct;
- the E3 card, which cited 15 Oct only;
- the Clinic MySQL suite, previously missing from the E1 card;
- the E4 wording.

**Remaining, each needing your decision:**
1. **No committed test log at the current HEAD.** The 885/61 (HEAD `881e9b8`) and 890/61 (after the fixes) results are not saved. Cite 873/61 and 934/0 (HEAD `1d93e93`) unless a new log is saved under `evidence/`.
2. **`HMS_HANDOFF.md` Q036 label** (`header.php:35` instead of `include/header.php:35`).
   - This is a documentation limitation only: `signatures.json` and the generated code carry the correct label.
   - It stays disclosed under FR10. The frozen handoff is not regenerated.

Stop here for approval before any commit, push, tag or component freeze.
