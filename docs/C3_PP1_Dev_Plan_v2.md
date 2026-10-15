# C3 Data Layer — PP1 Development Plan v2 (audit-based)

**Project:** J26-SE-329 · Component C3 (`components/c3_data_layer`) · **PP1:** 20 Oct 2026 · **Work days:** 15–19 Oct (freeze code the evening of 19 Oct)
**Supersedes** `docs/C3_100_Percent_Execution_Plan.md` (v1). v1 was written without seeing the code; this version is built from `docs/C3_audit_report.md` (audit of `database-migration` @ `01869d8`). If the two disagree, **this file and the audit win.**

> **Prompt for Claude Code:** "Read `docs/C3_audit_report.md` and `docs/C3_PP1_Dev_Plan_v2.md` completely. Work on branch `pp1-c3-complete`. Follow the work order in section 4. Commit after every task (small commits, clear messages), run the full test suite before each commit, and update the Status Ledger in section 9. Ask me only for the decisions in section 2 that are marked CONFIRM."

---

## 0. Rules (non-negotiable)

1. **Soundness before features.** C3's claim is *sound abstention*: when unsure, flag, never guess. Anything that converts unsoundly (loops, `switch`, silent drops) is fixed before any new capability.
2. **No new query-ID or benchmark-specific code.** Never write `query_id == "Qxxx"`, a file name, a Clinic/Helpdesk table or column name, or a regex of one benchmark's exact source into `src/`. Every rule must be general and tested on independently written fixtures.
3. **Frozen baselines stay frozen:** tag `c3-helpdesk-baseline-6of9` (Helpdesk 9/7/6/2/3) and the Clinic 12-Oct snapshot. Re-runs after changes are labelled *post-adaptation* and shown beside them.
4. **Unseen apps are run once, untouched, before the related fix** (pre-adaptation), then again after.
5. **Every number on a slide must come from a command that was run, with the output saved** under `evidence/`.
6. Only disposable MySQL with a throwaway password; never commit credentials; never run unknown PHP against real data.
7. Pipeline/test runs write to a temp folder or `tmp_path`, not into committed `output/` folders (except when deliberately regenerating evidence).
8. Do not claim universal compatibility with PHP apps. Claims are "within the stated scope on N benchmark applications".

---

## 1. Starting facts (from the audit)

| Area | Fact |
|---|---|
| Tests | 514 passed, 61 skipped (all MySQL), 0 failed = 575 collected. No MySQL test has been re-run since 8 Oct (that evidence is gitignored, 18/18 checks on MySQL 8.0). Day 3 behaviour tests ran on **SQLite**. |
| Helpdesk | 9 / 7 / 6 / 2 / 3 reproduces exactly. |
| Clinic | **22** SQL scenarios, not 21. Fresh run: converted 21 = **14 general + 7 query-ID-specific templates** (Q010, Q011, Q012, Q013, Q014, Q016, Q020); **S22 (`mysqli_prepare`) silently dropped**. On-disk output is stale (14/21). |
| Unsound recovery | SQL built in `foreach` / `while` / `switch` is treated as straight-line code → marked RECOVERED/CONVERTED with wrong SQL (including a DELETE `WHERE 1=0 OR id = :i`). |
| Crash | `ENUM`/`SET` columns abort the pipeline at stage 3/6 (`type_map.py:123`) and values are upper-cased. |
| Models | `configure_mappers()` fails for self-referencing FK and for two FKs to the same table (duplicate relationship name). |
| Schema loss | UNIQUE, indexes, ON DELETE/UPDATE, CHECK, defaults (not emitted), unsigned (dropped in models). |
| FR8 | `unresolved_queries.json` omits conversion-unresolved items; lists items that are CONVERTED; `summary.json` shows converted (21) > recovered (16). |
| Data access | `text()` + bind params only (no ORM constructs); inconsistent commit policy; generated Clinic code imports `c3.*` at runtime; `LIMIT :limit` only tested with ints. |
| Missing | Alembic (FR9); `security_report.json`; schema comparison; MySQL equivalence evidence in repo; schema-only baseline; benchmark manifest; evidence register; demo script; traceability not wired into the pipeline. |
| Packaging | `requirements.txt` is UTF-16; `pyproject.toml` empty; every command needs `PYTHONPATH=src`. |

---

## 2. Decisions (defaults chosen; CONFIRM = user must confirm)

| # | Decision | Default in this plan |
|---|---|---|
| 1 | Clinic presentation (**CONFIRM**) | Do **not** delete the guarded templates. Report Clinic as: "21 converted of 22 sites — 14 by general rules, 7 by guarded query-specific templates; S22 now detected". Never present "21/21" as an algorithmic result. Generalise only what is cheap (schema-derived allowlists, task S6). |
| 2 | Stale Clinic output | Kept as dated snapshot `output/clinic_inventory_legacy_hard_snapshot_2026-10-12`; regenerate the live folder after the P0 fixes. |
| 3 | MySQL evidence JSONs | Committed as copies under `evidence/mysql_2026-10-08/`. |
| 4 | Branch | New branches (`c3-pp1-baseline`, then `pp1-c3-complete`); do not touch `origin/database-migration` or `main`. |
| 5 | MySQL environment (**CONFIRM**) | Existing local MySQL 8 with a throwaway `c3_tester` account if I know an admin login; otherwise Docker `mysql:8.0` on port 3317 plus a small change to make test host/port configurable (`C3_DB_HOST`, `C3_DB_PORT`). |
| 6 | Transaction policy | Generated write functions take a session and an explicit `commit: bool = True` argument (default preserves current behaviour); documented; rollback test added. Make Clinic Q020 consistent. |
| 7 | ORM constructs | **Skip.** All docs/slides say "parameterized SQLAlchemy (`text()` + bind parameters)", never "ORM queries". List as a limitation. |
| 8 | Third-party apps (**CONFIRM**) | I choose 2–3; Claude may propose candidates meeting the criteria in section 5 (E5). |
| 9 | `evaluate_c3_holdout.py` | Keep as provenance; fix its output path later (P2). |
| 10 | `C3_source_review.zip` | Stays out of git. |

---

## 3. Realistic effort and cut line

Audit estimates: P0 ≈ 15–20 h, P1 ≈ 35–45 h. Five full days will not fit all of it. **Never cut:** S0–S5, S8, S10, S12, E-series evidence, docs. **Cut in this order if behind:** P2 items → S13 (Helpdesk Q002/Q004 class) → S11 (column-use roles) → S9 (prepared statements) → S7 (standalone generated code) → number of unseen apps (2 → 1). Report honest numbers whatever you reach.

---

## 4. Work order

### Phase A — before changing any code (≈2 h, Thursday morning)
**A1. Pre-adaptation runs on unseen third-party apps (E5).** Pick 2–3 apps (section 5, E5), clone at a pinned commit, run `c3 run` once on each **before any fix**, save outputs and logs to `evidence/preadaptation/<app>/`. A crash (e.g. ENUM) is a valid pre-adaptation result — record it. Add `benchmarks/benchmark_manifest.json` (name, URL, pinned commit, licence, LOC, DB API style, role: dev / holdout / unseen). Clinic and Helpdesk are **original synthetic fixtures** — record that explicitly.
**A2. Get MySQL working (E0).** Decision 5. Run the 61 skipped tests (`tests/benchmarks/clinic_hard`) after regenerating Clinic into `tmp`, save the raw log to `evidence/clinic_mysql_equivalence_log.txt`.

### Phase B — P0 soundness and crash fixes (Thursday–Friday)
| ID | Task | Where | Acceptance |
|---|---|---|---|
| **S1** (P0-2) | Abstain when any assignment in the SQL chain sits inside a loop (`foreach/for/while/do`) or `switch`. Reason code e.g. `LOOP_OR_SWITCH_CONTROL_FLOW`. | `mapping/sql_recovery.py` (`_find_relevant_assignments`, `_select_assignment_chain`, l.303–415); extractor statement context if needed | Negative tests for foreach-append, while-DELETE, switch; all → UNRESOLVED with file/line/reason. Clinic/Helpdesk/fixture results unchanged except intended. |
| **S2** (P0-3) | No silent drops: add `mysqli_prepare`, `mysqli_multi_query`, `mysqli_real_query` (and `stmt->execute` where recoverable). Any unrecognised DB executor call → UNRESOLVED row with reason `UNSUPPORTED_EXECUTOR`. | `tools/php_ast/extract_sql_calls.php:606–717` | Probe with 11 call sites yields 11 rows; Clinic S22 appears in `queries.json`. |
| **S3** (P0-4) | ENUM/SET: map to `sqlalchemy.Enum(...)` preserving case, or record in `migration_notes.md` as unsupported and continue; **never abort the pipeline for one column**. Remove the upper-casing of enum values. | `conversion/type_map.py:123`, `mapping/schema_reader.py` | Pipeline completes on a schema with ENUM/SET; models import; test added. |
| **S4** (P0-5) | Evidence hygiene: regenerate `output/hard_crud`, `output/mixed_crud`, `output/clinic_inventory_legacy_hard`; fix `docs/C3_EVALUATION_RESULTS.md` (113 tests, hard 1/5), `docs/C3_INTEGRATION_HANDOFF.md` ("JOIN unsupported"), `contracts/c3_data_layer_contract.json` ("one table per query"); make Clinic tests generate into `tmp_path` instead of reading untracked output. | listed files; `tests/benchmarks/clinic_hard/test_behavioral_equivalence.py:33` | Clean-clone test run works; docs match code. |
| **S5** (P1-4) | FR8 consistency: add conversion-unresolved items to `unresolved_queries.json` (file, line, reason, action); remove converted items from it; make `summary.json` unambiguous (recovered / converted / converted-by-template / unresolved / conversion-unresolved / total); fix Q015 `safe_for_conversion` contradiction. | `analysis/unresolved_report.py`, `pipeline.py:79–92` | Every non-converted query appears exactly once with an action; counts reconcile. |

### Phase C — P1 required features (Friday–Sunday)
| ID | Task | Where | Acceptance |
|---|---|---|---|
| **S6** (FR7) | `security_report.json`: classify every placeholder as value vs structural position; replace hard-coded allowlists with allowlists **derived from `schema.json`**; general detection of superglobal data (`$_GET/$_POST/$_REQUEST/$_COOKIE`) reaching SQL, with a count of legacy injection sites and which are remediated; injection-safety coverage metric. | new `analysis/security_report.py`; `conversion/order_by_binding.py`, `table_binding.py` | Report produced by the pipeline for every benchmark; no hard-coded Clinic identifiers left in allowlists. |
| **S7** | Generated code standalone (inline the few runtime helpers or emit `c3_runtime.py` beside `data_access.py`); relative paths in docstrings and `summary.json`. | `generation/emitter.py` | `data_access.py` imports and runs without `c3` on `PYTHONPATH`. |
| **S8** (P1-6, P1-8) | Transaction policy (decision 6) + rollback test; LIMIT/OFFSET integer coercion for string inputs. | `generation/emitter.py` | Rollback test passes; Q019-class test with `'10'`, `'0'`, `'abc'` behaves safely on MySQL. |
| **S9** | FR5 models: self-referencing FK (`remote_side`), several FKs to one table (`foreign_keys=` + unique relationship names), class names for plural/odd table names. FR3 schema: UNIQUE, indexes, ON DELETE/UPDATE, CHECK, defaults → `server_default`, unsigned. | `conversion/model_builder.py` (~l.216), `mapping/schema_reader.py` | `configure_mappers()` passes on new self-ref and multi-FK fixtures; schema comparison (E2) has zero unexplained diffs. |
| **S10** (FR9) | **Alembic.** `pip install alembic`, add to `requirements.txt` (UTF-8). `generation/alembic_emitter.py` as pipeline stage 7: `alembic.ini` (URL from env), `env.py` (`target_metadata` = generated models), `script.py.mako`, `versions/0001_initial_schema.py` with explicit `op.create_table` in FK dependency order (cycles via `op.create_foreign_key`), reverse `downgrade()`. Unsupported features (views, triggers, partitions, generated columns, spatial/fulltext) → `migration_notes.md`, never silent. | new emitter; `pipeline.py`; update tests that assume six stages | E3 round trip passes on every benchmark schema. |
| **S11** (FR4) | Column use: table-qualified columns, JOIN-ON / GROUP BY / ORDER BY / HAVING roles, `SELECT *` expansion from schema, alias handling; fix Clinic Q020 (`d.hourly_rate`) and Q017 (`fee`); Q011/Q013 column use must not be UNKNOWN when converted. | `analysis/column_analyzer.py` | Unit tests for each shape; ground-truth comparison improves. |
| **S12** (FR10) | Wire `traceability_report.json` into the pipeline; record function line for cross-file Q015. | `pipeline.py`, `analysis/traceability.py` | Produced for every run; tests green. |
| **S13** | Helpdesk Q002/Q004 class: the SQL string is not branch-dependent; the **call site** sits inside `if (POST)`/`else`. General rule: single reaching definition inside a guarded block is recoverable. **Time-box 3–4 h.** Report as post-adaptation beside the frozen 6/9. | `sql_recovery._detect_branching_assignment` (l.524) | Independent fixture (`k_insert_ifbody`-style) passes; Helpdesk post-adaptation saved separately; Clinic/fixtures unchanged. |
| **S14** (P1-9) | Prepared statements (`?` and `:name`) → conversion by mapping positional to named binds. | `query_converter._has_unsafe_placeholder_position` | Probe with `->prepare/execute` converts; negative cases still abstain. |
| **S15** (P1-12) | Evaluator: fix `expected_tables` vs `expected_table` mismatch (`evaluation/evaluator.py:126`); add `c3 evaluate` CLI; independent ground truth for Clinic and Helpdesk (**the student annotates by hand** from an independent grep of the raw PHP — Claude must not copy expected values from tool output). | `evaluation/` | `hard_crud` evaluation no longer shows 0% table accuracy because of format; GT files committed. |

### Phase D — experiments (see section 5) — run alongside Phase C as each prerequisite lands.

### Phase E — evidence, demo, docs (Sunday–Monday)
- **E-docs:** `evidence/evidence_register.md` (claim → file → command → date → result), `docs/test_protocol.md`, `docs/limitations_and_threats.md`, updated output-contract doc, `README_C3.md`, `docs/pp1_slides_c3.md` (text for slides 17–20), `benchmarks/benchmark_manifest.json`.
- **Schema-only baseline (proposal §3.3):** `c3 baseline` using `sqlacodegen` (installed 4.0.4) or schema-only mode of the model builder. Compare on the same apps: models, data-access functions provided (baseline 0), column-use info (baseline none), queries left for manual work, flagged-with-reason. Output `evidence/baseline_comparison.json` + `.md`. (The existing `hard_crud/baseline_*.json` are a *recovery* baseline, not this.)
- **Demo:** `scripts/demo.ps1` (mandatory): show scattered/concatenated SQL in a PHP file → `c3 run` on an unseen app → `models.py`, a `data_access.py` function with traceability, `column_usage.json`, `unresolved_queries.json` with file/line/reason → MySQL equivalence on a few queries (PASS, plus one INCONCLUSIVE) → hostile-input test → `alembic upgrade head` / `downgrade base`. Prepare a pre-recorded fallback. A FastAPI/HTML UI only if everything else is done.
- **Packaging (P2, cheap):** real `pyproject.toml`, UTF-8 `requirements.txt`, Windows long-path handling, remove benchmark-name defaults in `analysis/column_usage_generator.py:59,66` and `analysis/unresolved_report.py:96,103`.

---

## 5. Experiments (record raw logs under `evidence/`)

| ID | Experiment | Prerequisite | Command / method |
|---|---|---|---|
| **E0** | Usable MySQL | decision 5 | Local: as MySQL admin create `c3_tester` + `c3_clinic_legacy_test` + `c3_clinic_migrated_test`, grant on those DBs only, `$env:C3_DB_PASSWORD = Read-Host ...` (session-only). Docker: `docker run -d --name c3-mysql-test -e MYSQL_ROOT_PASSWORD=throwaway_only -p 3317:3306 mysql:8.0` + host/port env override. |
| **E1** | Real-MySQL equivalence | E0, S4 | `pytest tests\benchmarks\clinic_hard -v -rs` → `evidence/clinic_mysql_equivalence_log.txt`; plus `tests\mysql_validation\run_legacy.php`, `run_migrated.py`, `compare_behavior.py` for simple/mixed/hard. Add Q019 with **string** inputs `'2'`, `'0'`. Output also as `evidence/php_vs_sqlalchemy_equivalence.json` (per query PASS / FAIL / INCONCLUSIVE, with inputs, rows, before/after state; unresolved queries are INCONCLUSIVE, never PASS). Add Level C: run ≥2 legacy pages with `php -S` against the disposable DB and compare DB state with the generated functions. |
| **E2** | Schema ↔ ORM ↔ live DB | E0, S9 | Import each `schema.sql` into a disposable DB; `sqlalchemy.inspect` reflection (columns, PK, FK, unique, indexes) vs `models.Base.metadata` vs `schema.json` → `evidence/schema_comparison.json`; every diff classified `parser_defect` / `emitter_defect` / `documented_unsupported` / `benign_equivalent`. Runtime ORM test log → `evidence/orm_mapping_test_log.txt`. Parse `schema.sql` independently of the project's `schema_reader` (e.g. SQLGlot). |
| **E3** | Alembic round trip | S10, E0 | `alembic -c output\<app>\alembic\alembic.ini upgrade head` → reflect/compare (E2) → `check` → `downgrade base` (no tables left) for every benchmark schema → `evidence/alembic_roundtrip.json`, `alembic_test_log.txt`. |
| **E4** | Injection negatives on every generated function | S6 | For each `def` in every `data_access.py` call with `' OR '1'='1`, `1; DROP TABLE users;--`, `%`, `_`, unicode quotes, `id; DROP TABLE x` for identifiers, `'abc'` for LIMIT/numeric. Assert row counts and table checksums unchanged afterwards. SQLite now; MySQL after E0. Also an AST scan asserting no SQL built with f-string / `%` / `.format` / `+` from variables except the allowlisted identifier cases. |
| **E5** | Unseen third-party apps | none (do first) | Criteria: procedural PHP (not an MVC framework), MySQL, permissive licence recorded, runnable with its DB setup, small-to-mid size, mixed SQL styles (`mysqli`/`mysql_*`/PDO; concatenation + interpolation + some conditionals). `git clone <url> benchmarks\apps\<name>; git -C benchmarks\apps\<name> checkout <hash>`; then `$env:PYTHONPATH="src"; python -m c3.cli run --php ... --schema ... --output ...`. Pre-adaptation result first; post-adaptation after fixes. Budget ≤2 h per app, otherwise swap it. |
| **E6** | Performance | none | `Measure-Command` per app (today 0.38–0.73 s); add a timing field to `summary.json`. |
| **E7** | Evaluation metrics on all apps | S15, annotated GT | Seven proposal metrics per app and combined (recovery precision/recall, column-use accuracy, ORM/schema fidelity from E2, behavioural equivalence from E1, injection-safety coverage from E4/S6, abstention rate with **false-abstention and false-recovery audits by manual review**, migration effort vs schema-only baseline) → `evidence/evaluation/<app>/evaluation.json`, `evaluation_summary.json`, `results_tables.md`. For apps with >40 SQL sites annotate a documented stratified sample (≥30 sites). |

---

## 6. Schedule

| Day | Date | Work |
|---|---|---|
| 1 | Thu 15 Oct | Manual commit/push (done by me) · **A1** pre-adaptation runs · **A2/E0** MySQL · **S1, S2, S3** |
| 2 | Fri 16 Oct | **S4, S5** · **E1** MySQL equivalence · **S6** start · annotate ground truth (me) |
| 3 | Sat 17 Oct | **S9** · **S10 + E3** · **E2** schema comparison · **S12** |
| 4 | Sun 18 Oct | **S6 finish + E4** · **S7, S8** · **S13** (time-boxed) · baseline comparison · E5 post-adaptation reruns · **E7** |
| 5 | Mon 19 Oct | **S11/S14/S15 if time** · demo script + fallback recording · docs + evidence register + slide text · full regression · tag `c3-pp1-final` · two rehearsals |
| PP1 | Tue 20 Oct | Present. No code changes. |

---

## 7. How to report results honestly (use on slides)

- **Say:** "Pipeline runs end to end on N applications (2 original synthetic benchmarks, M unseen third-party). Helpdesk holdout: 6/9 pre-adaptation, X/9 post-adaptation. Clinic: 21 converted of 22 sites — 14 by general rules, 7 by guarded query-specific templates." "Unresolved queries are flagged with file, line and reason; unsound conversions found during self-audit (loops, switch) were fixed." "Behavioural equivalence verified on MySQL 8: P PASS / F FAIL / I INCONCLUSIVE."
- **Do not say:** "21/21 general accuracy", "100% accuracy", "ORM queries" (it is parameterized SQLAlchemy `text()`), "works on arbitrary PHP apps", "575 passing" (it is 514 pass + 61 MySQL-dependent; update after E1).
- **Strength to present:** the self-audit itself — the soundness bugs found and fixed, the frozen baselines and the pre-/post-adaptation protocol are evidence of research rigour.

---

## 8. Definition of Done (component, excluding integration)

- [ ] S1–S5 done (no unsound conversion, no silent drop, no crash on ENUM, evidence and docs consistent with code).
- [ ] FR1–FR10 each have a status with an evidence file; remaining limits listed in `limitations_and_threats.md`.
- [ ] Pipeline emits for every benchmark: `schema.json`, `queries.json`, `converted_queries.json`, `models.py`, `data_access.py`, `column_usage.json`, `unresolved_queries.json`, `summary.json`, `traceability_report.json`, `security_report.json`, `alembic/`, `migration_notes.md`.
- [ ] `evidence/` contains: `schema_comparison.json`, `orm_mapping_test_log.txt`, `php_vs_sqlalchemy_equivalence.json`, `clinic_mysql_equivalence_log.txt`, `alembic_roundtrip.json`, `baseline_comparison.*`, `evaluation_summary.json`, `preadaptation/`, `evidence_register.md`; plus `benchmarks/benchmark_manifest.json`.
- [ ] Alembic upgrade/downgrade passes on every benchmark schema; injection negatives pass for every generated function.
- [ ] Full test suite green on a clean clone; MySQL tests run and logged.
- [ ] `scripts/demo.ps1` runs end to end; fallback recording ready.
- [ ] Code tagged `c3-pp1-final` on the evening of 19 Oct.

---

## 9. Status Ledger (Claude fills in and updates after every task)

Branch: `database-migration` (user override: no `pp1-c3-complete`). Starting point: `62b499d`, tag `c3-helpdesk-baseline-6of9` present. Starting test run on 2026-10-15: **514 passed, 61 skipped (MySQL), 0 failed**. Log: `evidence/test_runs/2026-10-15_pre_dev_pytest.txt`.

| Task | Status | Evidence / commit | Notes |
|---|---|---|---|
| A1 pre-adaptation runs | **done** | `evidence/preadaptation/` (+ manifest) | HMS, NEWS-BUZZ and Fee-Rainbow at pinned commits. **All 3 crash at stage 3/6 on `INT(n)`**, and ALTER-style PKs are lost (0 PKs). Detection is complete (independent grep). BRANCHING dominates the unresolved queries. |
| A2 MySQL (E0) | waiting on user | — | Decision: local MySQL80 service. The user runs the MySQL tests in their own terminal and pastes the logs; the password is never written to a file or chat. |
| S1 loop/switch soundness | **done** | `evidence/soundness/S1_loop_switch/`, `tests/test_loop_switch_soundness.py` | The extractor now records enclosing loops and switch cases (`loop_context`), and recovery abstains with `LOOP_OR_SWITCH_CONTROL_FLOW`. 9 must-abstain and 6 must-recover fixtures, all written independently. Regression check: 0 changed queries on 8 apps. Tests: 530 passed / 61 skipped. |
| S2 silent drops | not started | — | `mysqli_prepare`, `mysqli_multi_query` and `mysqli_real_query` dropped; Clinic S22 missing. |
| S3 ENUM/SET | not started | — | `type_map.py:123` raises and aborts stage 3. Values are upper-cased. |
| S4 evidence hygiene | partly done | `414ff53`, `fe35517`, `061f02b`, `8e0a842` | Baseline, Clinic snapshot and MySQL 2026-10-08 JSONs are committed. Still to do: live Clinic/hard/mixed outputs are stale, docs and contract are stale, and Clinic tests read the untracked output. |
| S5 FR8 consistency | not started | — | |
| S6 security report | not started | — | Value/structural AST check exists. Allowlists are hard-coded per query. |
| S7 standalone generated code | not started | — | Generated Clinic code imports `c3.conversion.*`. |
| S8 transaction + LIMIT | not started | — | Mixed commit policy. LIMIT is only tested with ints. |
| S9 models + schema fidelity | not started | — | Self-ref and dual-FK models fail `configure_mappers()`. UNIQUE, indexes, ON DELETE and CHECK are lost. |
| S10 Alembic | not started | — | Alembic not installed. |
| S11 column use | not started | — | |
| S12 traceability | not started | — | Module and tests exist; not wired into the pipeline. |
| S13 Helpdesk Q002/Q004 class | not started | — | |
| S14 prepared statements | not started | — | |
| S15 evaluator + GT | not started | — | hard_crud GT `expected_tables` vs evaluator `expected_table`. |
| E1–E7 | not started | — | E1–E3 need MySQL. |
| Baseline comparison | not started | — | |
| Demo | not started | — | |
| Docs + evidence register | started | `evidence/evidence_register.md` | Register created with the pre-dev test run. |

---

## 10. Working rules for the coding assistant

- Work on `pp1-c3-complete`; small commits; never force-push; never delete or rewrite files under `evidence/baseline_*`, `evidence/preadaptation/` or the tagged baselines.
- Before each commit: full test suite, and confirm Clinic, simple_crud, mixed_crud, hard_crud and Helpdesk results only changed where the task intends. Keep `c3 run` CLI arguments backward compatible.
- Handle paths with spaces (`benchmarks/ground truth/`) and Windows long paths.
- Never claim something is verified unless the command was run and its output saved.
- If a task conflicts with section 0, follow section 0 and tell me.
