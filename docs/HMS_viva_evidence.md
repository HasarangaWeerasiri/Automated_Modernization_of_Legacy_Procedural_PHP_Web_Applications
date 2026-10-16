# HMS (kishan0725): viva evidence for C3 Data Layer Migration (PP1)

Scope: **HMS only**. Every number below comes from a committed evidence file. Verdicts used:
- **verified**: tested on HMS, with saved evidence and no open cases
- **partly verified**: tested; the counts are given
- **not tested**

Frozen handoff state: tag `hms-handoff-v1` (`eea6a6e`), output `output/hms_kishan0725/`. Live demo output: `output/hms_pp1_demo/`. Its `data_access.py` and `models.py` are byte-identical to the frozen handoff (checked 2026-10-16).

Commit hashes were rewritten on 2026-10-15 to remove co-author trailers. Older hashes in raw logs resolve through `evidence/commit_hash_map_2026-10-15.md`.

**Before the live commands** (PowerShell, repo root, once per session):

```powershell
$env:PYTHONPATH = "components\c3_data_layer\src"; Set-Alias py3 components\c3_data_layer\.venv\Scripts\python.exe
```

---

## 1. Objectives and FR1–FR10 → HMS work → evidence → live command

### 1a. Proposal objectives

The six objectives are from the C3 research proposal (supplied by the author; the proposal document itself is not in this repository). Each verdict is the weakest verdict of the FRs it relies on. The per-FR detail is in 1b.

| # | Proposal objective | FRs | What was done on HMS | Verdict | Evidence file | Live command (one line) |
|---|---|---|---|---|---|---|
| O1 | Recover Database Schema | FR3 (FR9 recreates it) | `myhmsdb.sql` recovered into `schema.json`, then checked three ways: an independent sqlglot parse, the generated ORM metadata, and the original DDL loaded into MySQL 8 and reflected | **verified**: all views match (6 tables, 42 columns). 7 query-vs-schema drift site-columns reported separately (a property of the legacy app) | `evidence/hms/E_checks/e2_offline/schema_comparison.json`, `evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json` | `py3 scripts\schema_comparison.py --apps hms_kishan0725 --out $env:TEMP\e2_demo` |
| O2 | Recover SQL Behavior | FR1, FR2 | AST detection of every SQL execution site; reconstruction of concatenated and interpolated SQL, including array elements (H1); evaluated against the verified ground truth | **verified, post-adaptation**: 44/44 live sites detected; 43/43 recoverable sites recovered; 0 false recoveries; 1 ambiguous site excluded. Before H1: 40/43 (section 2) | `evidence/evaluation/hms_kishan0725/evaluation.json`, `evidence/evaluation/hms_kishan0725/summary.md` | `py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\hms_kishan0725 --output output\hms_pp1_demo --ground-truth "benchmarks\ground truth\hms_kishan0725\queries.json"` |
| O3 | Analyze Data Usage | FR4 | Read, write and filter columns, plus JOIN/GROUP BY/ORDER BY/HAVING roles, with table attribution | **verified**: operation, tables, read, write and filter match the ground truth on 37/37 schema-consistent sites; the 6 schema-mismatch sites also match (reported separately) | `evidence/evaluation/hms_kishan0725/evaluation.json`, `output/hms_kishan0725/column_usage.json` | same command as O2 (see `column_use_accuracy`) |
| O4 | Generate ORM Data Layer | FR5, FR6, FR9 | `models.py` (2 ORM classes, 4 Core tables), `data_access.py` (44 `text()` functions with bind parameters), `alembic/` | **partly verified**: models and Alembic verified (`configure_mappers()` OK; MySQL E3 PASS). Data-access behaviour vs PHP on MySQL: **27 PASS, 0 FAIL, 17 INCONCLUSIVE of 44** (section 2b) | `evidence/hms/E_checks/e2_offline/orm_mapping_test_log.txt`, `evidence/mysql_runs/2026-10-15_173218/e3/hms_kishan0725.json`, `evidence/mysql_runs/2026-10-15_195730/e1/hms_kishan0725.json` | `py3 -m c3.cli run --php benchmarks\apps\_app_only\hms_kishan0725 --schema benchmarks\apps\hms_kishan0725\myhmsdb.sql --output $env:TEMP\hms_viva` |
| O5 | Ensure Safe Migration | FR7, FR8 (FR6 equivalence) | Injection negatives (E4), taint report, hostile cases on MySQL (E1), abstention with unresolved-site reporting | **partly verified**: injection safety verified (44/44 functions safe; 62/62 user values bound; 26/26 legacy injection sites remediated). **Abstention cannot be shown on HMS** (0 unresolved sites); it is shown on other apps (section 2c). Equivalence: 27/44 PASS | `evidence/hms/E_checks/e4/injection_report.json`, `output/hms_kishan0725/security_report.json`, `evidence/fresh_holdout/dignity_dev_hms/README.md` | `py3 scripts\e4_injection_check.py --apps hms_kishan0725 --out $env:TEMP\e4_demo` |
| O6 | Support Backend Generation | FR10 (+ handoff) | `traceability_report.json`; standalone `data_access.py` (no C3 dependency); frozen signatures; `HMS_HANDOFF.md` with parameters, return shapes, commit behaviour, PHP site and E1 status per function | **partly verified**: the handoff package is verified (44/44 sites traced; 44/44 functions run with C3 blocked; signature test passes). **Integration into a backend or the HMS pages has not been tested** (team integration starts 19 Oct) | `output/hms_kishan0725/HMS_HANDOFF.md`, `output/hms_kishan0725/traceability_report.json`, `output/hms_kishan0725/signatures.json` | `py3 -m pytest components\c3_data_layer\tests\test_hms_handoff_signatures.py components\c3_data_layer\tests\test_standalone_generated_code.py -q` |

### 1b. Functional requirements

| FR | What was done on HMS | Verdict | Evidence file | Live command (one line) |
|---|---|---|---|---|
| FR1 SQL execution-point identification | AST extraction of every `mysqli_query` site in the app-only copy, compared with an independent grep and with the verified ground truth | **verified**: 44/44 live sites detected; 0 of 8 commented-out lines reported | `evidence/evaluation/hms_kishan0725/evaluation.json` | `py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\hms_kishan0725 --output output\hms_pp1_demo --ground-truth "benchmarks\ground truth\hms_kishan0725\queries.json"` |
| FR2 Fragmented / conditional SQL recovery | Concatenation, interpolation and array elements (`$_GET['ID']`, rule H1); block-containment rule (S13) | **verified on HMS shapes**: 43/43 recoverable sites recovered; 0 false recoveries; 1 ambiguous site excluded. HMS has little conditional SQL | `evidence/evaluation/hms_kishan0725/summary.md`, `evidence/hms/H1_unresolved_review/review.md` | same command as FR1 (see `recovery`) |
| FR3 Schema recovery | `myhmsdb.sql` parsed by C3 and, independently, by sqlglot; original DDL loaded into MySQL 8 and reflected | **verified**: all views match (6 tables, 42 columns). 7 query-vs-schema drift site-columns reported separately | `evidence/hms/E_checks/e2_offline/schema_comparison.json`, `evidence/mysql_runs/2026-10-15_173248/e2/schema_comparison_mysql.json` | `py3 scripts\schema_comparison.py --apps hms_kishan0725 --out $env:TEMP\e2_demo` |
| FR4 Column-use analysis | Read, write and filter roles plus table attribution, compared with the ground truth | **verified**: operation, tables, read, write and filter match on 37/37 schema-consistent sites; the 6 schema-mismatch sites also match (reported separately) | `evidence/evaluation/hms_kishan0725/evaluation.json` | same command as FR1 (see `column_use_accuracy`) |
| FR5 ORM model generation | `models.py`: 2 ORM classes (tables with a PK) and 4 Core `Table` objects (no PK in the dump) | **verified**: imports, `configure_mappers()` OK, live MySQL reflection matches | `evidence/hms/E_checks/e2_offline/orm_mapping_test_log.txt`, `evidence/hms/H2_tables_without_pk/proposal.md` | `py3 scripts\schema_comparison.py --apps hms_kishan0725 --out $env:TEMP\e2_demo` |
| FR6 Parameterised SQLAlchemy data access | 44 `text()` functions with bind parameters, compared with the PHP on real MySQL 8 (E1: legacy SQL built by PHP itself) | **partly verified: 27 PASS, 0 FAIL, 17 INCONCLUSIVE of 44** (causes in section 3) | `evidence/mysql_runs/2026-10-15_195730/e1/hms_kishan0725.json`, `evidence/hms/E1_triage/triage.md` | `Get-Content evidence\mysql_runs\2026-10-15_195730\e1\php_vs_sqlalchemy_equivalence.json` |
| FR7 Injection safety | 12 hostile payloads against every function (E4); taint report; hostile cases inside E1 on MySQL | **verified (static and recording session)**: 44/44 functions safe; 62/62 user values bound; 26/26 legacy injection sites remediated. On MySQL, E1 recorded 34 `LEGACY_INJECTABLE` cases where the PHP was injected and the generated code was not | `evidence/hms/E_checks/e4/injection_report.json`, `output/hms_kishan0725/security_report.json` | `py3 scripts\e4_injection_check.py --apps hms_kishan0725 --out $env:TEMP\e4_demo` |
| FR8 Abstention and unresolved reporting | `unresolved_queries.json`; schema-drift and ambiguous sites flagged in the handoff | **partly verified**: 0 false recoveries on HMS. HMS has 0 unresolved sites, so abstention is not exercised here; it is shown on Dignity-Dev, NEWS-BUZZ and Fee-Rainbow (section 2c) | `output/hms_pp1_demo/unresolved_queries.json`, `evidence/fresh_holdout/dignity_dev_hms/README.md` | `Get-Content output\hms_pp1_demo\unresolved_queries.json` |
| FR9 Alembic migration | `alembic/` generated; MySQL 8: upgrade → live schema = models → `alembic check` → downgrade | **verified** | `evidence/mysql_runs/2026-10-15_173218/e3/hms_kishan0725.json` | `py3 scripts\alembic_roundtrip.py output\hms_pp1_demo --url "sqlite:///$env:TEMP/hms_viva.db" --report $env:TEMP\e3_demo.json` (SQLite mechanics; MySQL result in the file) |
| FR10 Traceability | `traceability_report.json`: PHP file:line → recovery → column use → generated function; docstrings name each parameter's PHP value | **verified**: 44/44 sites traced to a generated function | `output/hms_kishan0725/traceability_report.json`, `output/hms_kishan0725/HMS_HANDOFF.md` | `Select-String -Path output\hms_pp1_demo\data_access.py -Pattern "Migrated from" \| Select-Object -First 5` |
| Handoff stability (supports FR6) | Signatures frozen in `signatures.json`; generated code runs with C3 blocked | **verified**: signature test passes; 44/44 functions called with C3 blocked | `output/hms_kishan0725/signatures.json`, `evidence/hms/H4_handoff/make_handoff_result.json` | `py3 -m pytest components\c3_data_layer\tests\test_hms_handoff_signatures.py components\c3_data_layer\tests\test_standalone_generated_code.py -q` |

---

## 2. HMS results at three stages

### 2a. Recovery and conversion

All three stages are scored against the **same** verified ground truth: 44 live sites, of which 43 are recoverable and 1 is ambiguous (excluded). They are matched by (file, line), with the same evaluator (`c3.evaluation.site_evaluator`).

| Measure | Stage 1: original pipeline, before S1–S14 | Stage 2: after the general fixes, before H1 | Stage 3: after H1 (handoff) |
|---|---|---|---|
| Code | `371fddf` (output frozen in `evidence/preadaptation/hms_kishan0725/`) | `9b25bc1` (after S1–S14) | `d542436`; tag `hms-handoff-v1` = `eea6a6e` |
| Pipeline completes | **No**: stopped at stage 3/6 (`Unsupported MySQL type: INT(11)`) | Yes, all 7 stages | Yes, all 7 stages |
| Live SQL sites detected | 44/44 | 44/44 | 44/44 |
| Recoverable sites recovered (TP / 43) | **19/43** (recall 44.2%) | **40/43** (recall **93.0%**) | **43/43** |
| False abstentions (FN) | 24 | 3 (`admin-panel.php:71`, `admin-panel.php:86`, `doctor-panel.php:8`) | 0 |
| False recoveries (FP) | 0 (precision 100%) | 0 (precision **100%**) | 0 |
| Live SQL sites converted to data-access functions | 19 in `converted_queries.json`; `models.py` and `data_access.py` never produced (crash) | 40/44 | **44/44** (all by general rules; 0 by query-specific templates) |
| Column use vs ground truth | not measurable: the crash happened before column analysis | all 5 fields match on 34/34 schema-consistent sites | all 5 fields match on 37/37 schema-consistent sites (+6 schema-mismatch sites, also matching) |
| Evidence | `evidence/preadaptation/README.md`, `evidence/evaluation/hms_kishan0725/evaluation_preadaptation.json` | `evidence/evaluation/hms_kishan0725/evaluation_pre_H1.json` | `evidence/evaluation/hms_kishan0725/evaluation.json` |

**Evaluation conditions**
- **Stage 1** is the untouched pipeline on the HMS app-only copy, run on 2026-10-15 before any converter change. Its recovery output (`queries.json`) survived the stage-3 crash and was scored afterwards against the ground truth. That ground truth was verified later, on 2026-10-15, and is scored by file and line, so the stage-1 run did not see it.
- **Stage 2** follows general fixes that were driven mainly by the development apps and the Helpdesk holdout:
  - S1/S2 soundness;
  - S3/S3b types and ALTER keys, first exposed by the crash of HMS and the other unseen apps;
  - S13 block containment, from Helpdesk;
  - S14 prepared statements.

  The HMS abstentions had not yet been reviewed.
- **Stage 3** follows H1. The 4 remaining HMS abstentions were reviewed against the PHP, and 3 were missed recoveries. They were fixed by a general rule (array elements with literal keys, e.g. `$_GET['ID']`). The rule has no app or query-ID logic and changed 0 queries on the other 8 apps.

  So **stage 3 is post-adaptation for HMS**. The fourth abstention, `include/header.php:35`, is ambiguous in the ground truth and is excluded.

### 2b. Behavioural equivalence on MySQL (E1), stage 3 only

Reported separately from 2a, because it measures something different: whether each generated function behaves like the PHP on real MySQL 8. Stages 1 and 2 have no E1 run.

| Sites | PASS | FAIL | INCONCLUSIVE | Evidence |
|---|---|---|---|---|
| 44 | **27** | **0** | **17** | `evidence/mysql_runs/2026-10-15_195730/e1/php_vs_sqlalchemy_equivalence.json`, `.../hms_kishan0725.json`, triage `evidence/hms/E1_triage/triage.md` |

The causes of the 17 INCONCLUSIVE sites are in section 3. That run recorded the old head hash `01f427e`, which is `d96987c` after the 2026-10-15 rewrite; see `evidence/commit_hash_map_2026-10-15.md`.

### 2c. Abstention: shown on other apps, not on HMS

HMS has **0 unresolved sites** at stage 3: every live site is recovered and converted. So HMS cannot show that C3 abstains correctly, meaning that it leaves a site unresolved, with a reason, instead of guessing. That behaviour has to be shown on other benchmark apps (current code, 2026-10-15 regression run):

| App | Sites / recovered / converted | Unresolved sites (reason codes) | How the abstentions were checked |
|---|---|---|---|
| Dignity-Dev HMS (fresh holdout) | 14 / 7 / 4 | 10 (4 `STRUCTURAL`, e.g. `FROM $table` from a function parameter; 3 conversion-unsupported; 1 loop/switch; 1 branching; 1 unsupported shape) | Dignity-Dev produced 10 unresolved entries. Their correctness against the ground truth is pending author verification and S15 evaluation. The draft ground truth contains 6 non-recoverable sites out of 13 live sites. |
| NEWS-BUZZ | 32 / 30 / 29 | 3 (2 `BRANCHING`, 1 conversion-unsupported) | Its ground truth is a blind draft, **not yet author-verified** (verification due on Sunday). The draft marks `admin/editposts.php:17` and `admin/post.php:17` as not recoverable (if/else picks the SQL), which agrees with the 2 BRANCHING abstentions; not yet a verified result |
| Fee-Rainbow | 22 / 20 / 19 | 3 (2 `UNKNOWN_EXPR`, 1 conversion-unsupported) | Ground truth not yet author-verified |

Live command (writes only to a temp folder):

```powershell
py3 -m c3.cli run --php benchmarks\apps\_app_only\dignity_dev_hms --schema benchmarks\apps\dignity_dev_hms\127_0_0_1.sql --output $env:TEMP\dd_viva; Get-Content $env:TEMP\dd_viva\unresolved_queries.json
```

Each entry shows file, line, reason code, reason and `manual_review_required`.

**HMS is not the generalization evidence.** HMS was used to find the missed recoveries. Its 4 abstentions were reviewed and led to the general array-element rule (H1). INT(n) and ALTER-key handling were also first exposed by HMS and the other unseen apps. Stage 3 is therefore **post-adaptation**. The generalization evidence is:
- **Dignity-Dev HMS**: the only genuine **fresh holdout**. It was run untouched on the current rules: 14 sites, 7 recovered, 4 converted, detection 14/14, and its abstentions were checked as correct (`evidence/fresh_holdout/dignity_dev_hms/README.md`).
- **Helpdesk holdout**: frozen pre-adaptation baseline 9/7/6 (tag `c3-helpdesk-baseline-6of9`). It is post-adaptation after S13.
- **NEWS-BUZZ and Fee-Rainbow**: frozen pre-adaptation runs in `evidence/preadaptation/`; later runs are post-adaptation. Their ground truth is not yet author-verified, so they have not been evaluated against it.
- **No query changed on the other 8 apps** when H1 was added (`evidence/hms/H1_unresolved_review/regression.txt`). This shows the rule did not break them; it does not show generalization.

---

## 3. What was not tested

- **The HMS web pages running on the generated layer.** E1 compares individual SQL sites (PHP-built SQL vs the generated function on the same database). No HMS page was rewired to call `data_access.py`, and no end-to-end browser or page test was run.
- **The 7 schema-drift site-columns.** The SQL uses columns or tables that `myhmsdb.sql` does not define, so the statement fails on both sides and cannot be compared:
  - `appointmenttb.payment` at `func.php:34`, `func2.php:41`, `func3.php:24`, `newfunc.php:23`
  - `doctb.name` at `func2.php:66`, `func3.php:49`
  - table `users` at `include/header.php:35` (ambiguous in the ground truth: the file is unreachable)
- **The 17 E1 INCONCLUSIVE sites, with causes:**

| Group | Sites | Cause | What *was* shown |
|---|---|---|---|
| Schema drift | 7 (above) | The column or table is missing; MySQL error 1054/1146 on both sides | Same error on both sides |
| Legacy INSERT fails under strict MySQL | `func.php:62`, `newfunc.php:84` | The PHP omits a `NOT NULL` column (`spec`, `password`); error 1364 on both sides, even with valid input | Same error on both sides |
| Invalid input rejected by both sides | `admin-panel.php:42`, `admin-panel.php:45`, `admin-panel.php:71`, `admin-panel1.php:15`, `contact.php:11`, `doctor-panel.php:8`, `func2.php:14`, `prescribe.php:33` | `normal` and `empty_result` PASS. Some hostile or type-mismatch values are rejected by MySQL on both sides, so the strict site rule gives INCONCLUSIVE | Valid inputs PASS; in all but one case both sides return the same MySQL error code (the exception is `admin-panel.php:45`: 1054 vs 1366) |

- **Other limits:**
  - one MySQL version (8.0.39) in strict mode;
  - E1 uses hand-chosen inputs (`normal`, `empty_result`, hostile, type mismatch), which is not exhaustive testing;
  - the 4 tables without a primary key were not given an ORM identity (by decision; see section 4).

---

## 4. Questions an examiner may ask

**Why are not all 44 sites verified?**
27 sites pass every E1 case on real MySQL. The other 17 cannot be compared, not because they differ:
- 7 use columns or tables missing from the provided schema;
- 2 are PHP INSERTs that fail under strict MySQL on both sides;
- 8 pass all valid inputs but contain invalid-input cases rejected by both sides, which the strict site rule counts as INCONCLUSIVE.

Triage found 0 incorrect generated functions. An earlier run showed 5 FAILs, caused by a harness classification bug (`6b54bf0`). The bug was fixed and documented in `evidence/hms/E1_triage/triage.md`, and the re-run confirmed the result. None of these sites is reported as passing.

**How independent is the ground truth?**
It was drafted blind by an LLM from the raw PHP and the schema only. The drafter did not open the C3 code, outputs or tests. The author then verified all 52 entries and made 0 corrections (`benchmarks/ground truth/hms_kishan0725/VERIFICATION.md`). The risks are disclosed:
- the drafter is from the same model family as the assistant that helped write C3, so shared blind spots are possible;
- there was a single human verifier;
- matching is by file and line, never by C3 query IDs.

The independent grep (`candidates.csv`) and E1, which builds the legacy SQL with PHP itself, are checks that do not depend on that model.

**Did you tune on HMS?**
Yes, and it is labelled. The H1 rule (array elements such as `$_GET['ID']` as named values) came from reviewing the 4 HMS abstentions. It is a general rule with no app-specific or query-ID logic, and it changed 0 queries on the other 8 apps. The HMS results are therefore post-adaptation. The pre-adaptation numbers (section 2) are kept, and Dignity-Dev is the fresh-holdout evidence.

**Why `text()` with bind parameters instead of ORM queries?**
- **Faithfulness:** the generated function runs the same SQL structure as the PHP. Only the values change, from string-pasted to bound parameters, and that is what makes the E1 comparison meaningful.
- **Safety:** values can never become SQL, which E4 and the MySQL hostile cases show.
- **Scope:** it also covers shapes an automatic ORM rewrite could get subtly wrong (MySQL-specific syntax, JOIN UPDATE, tables without a PK). The ORM models are generated as well (FR5) for new code.
- **Trade-off:** the data-access layer is not idiomatic ORM code; rewriting it to ORM queries is future work.

**Why are 4 tables Core tables?**
`admintb`, `contact`, `doctb` and `prestb` have no primary key in `myhmsdb.sql`. An ORM class needs an identity, and C3 does not invent keys: a guessed key could silently merge rows in the identity map. The tables are generated as Core `Table` objects, which no function depends on, and the options are documented:
- keep them as Core tables (chosen);
- add a mapper-only key after the owner confirms it and a duplicate check passes;
- add an opt-in migration with real constraints.

See `evidence/hms/H2_tables_without_pk/proposal.md`.

**What happens at an unresolved site?**
- **No SQL is guessed.** The site gets an entry in `unresolved_queries.json` with file, line, reason code, reason and the action `manual_review_required`.
- **A stub is generated.** `data_access.py` gets `unresolved_query_<id>(session, **kwargs)`, which raises `NotImplementedError` naming the PHP file and line, so it can never run silently.
- **The traceability report** marks the site `manual_review_required`.
- **On HMS** there are 0 unresolved sites. Dignity-Dev shows the behaviour: 10 of 14 sites are left unresolved, for example where a table name comes from a function parameter.

---

## 5. Five-minute demo order

**`scripts/demo.ps1` does not exist in the repository.** The order below uses the commands from section 1, all checked on 2026-10-16; each takes a few seconds. Run the setup line from the top of this document first. Nothing here writes into `output/hms_kishan0725/`.

| Time | Step | Command | What to point at |
|---|---|---|---|
| 0:00–1:00 | Run the pipeline on HMS into a scratch folder | `py3 -m c3.cli run --php benchmarks\apps\_app_only\hms_kishan0725 --schema benchmarks\apps\hms_kishan0725\myhmsdb.sql --output $env:TEMP\hms_viva` | 7 stages; summary 44 / 44 / 44; 0 query-specific templates |
| 1:00–1:45 | Generated code | `Get-Content $env:TEMP\hms_viva\data_access.py -TotalCount 120` | `text()` + bind parameters; docstring with PHP file:line and parameter sources (`GET_ID ← $_GET['ID']`) |
| 1:45–2:30 | Accuracy vs the verified ground truth | `py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\hms_kishan0725 --output $env:TEMP\hms_viva --ground-truth "benchmarks\ground truth\hms_kishan0725\queries.json"` | 44/44 detected; 43 TP, 0 FP, 0 FN; 1 ambiguous excluded. Say "post-adaptation" |
| 2:30–3:15 | Injection safety | `py3 scripts\e4_injection_check.py --apps hms_kishan0725 --out $env:TEMP\e4_demo` | 44/44 SAFE |
| 3:15–4:00 | Equivalence on real MySQL (recorded) | `Get-Content evidence\mysql_runs\2026-10-15_195730\e1\php_vs_sqlalchemy_equivalence.json` | 27 PASS, 0 FAIL, 17 INCONCLUSIVE; then open `evidence/hms/E1_triage/triage.md` for the causes |
| 4:00–4:30 | Schema and Alembic | `py3 scripts\schema_comparison.py --apps hms_kishan0725 --out $env:TEMP\e2_demo` | MATCH; 7 drift site-columns shown separately; MySQL E3 PASS in `evidence/mysql_runs/2026-10-15_173218/e3/` |
| 4:30–5:00 | Handoff | open `output/hms_kishan0725/HMS_HANDOFF.md` | per-function E1 status column; unresolved = none; drift and tables without a PK listed |

**Fallback if a live command fails:** show the evidence file named in section 1 for that step. The `output/hms_pp1_demo/` folder already holds a successful 7-stage run.

**Sensitive data:** do not open `myhmsdb.sql`, the filled E1 case files (`benchmarks/equivalence_cases/hms_kishan0725*.json/.csv`) or the HMS PHP on screen. They contain demo patient records and plaintext demo passwords from an unlicensed app. The HMS E1 evidence is redacted (no SQL text, no values).
