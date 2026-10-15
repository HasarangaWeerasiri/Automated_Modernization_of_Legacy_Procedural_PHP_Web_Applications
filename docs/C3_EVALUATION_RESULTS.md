# C3 – Data Layer Migration: Evaluation Results

**Last updated:** 2026-10-15. These figures replace the 8-Oct version of this document (3 benchmarks, 113 tests). Every number here has a row in `evidence/evidence_register.md`, with its command and raw output.

## 1. Introduction

Component 3 (C3) of the Automated Modernization of Legacy Procedural PHP Web Applications project migrates database operations in legacy procedural PHP into parameterized SQLAlchemy data access (`text()` + bind parameters). It recovers SQL statically, converts what it can recover safely, and abstains (unresolved, with file, line and reason) when it cannot.

**Primary outputs** (per the proposal): `models.py`, `data_access.py` and `unresolved_queries.json`. Every other file C3 writes is a supporting artifact.

## 2. Benchmark applications

| Application | Role | Origin |
|---|---|---|
| simple_crud, mixed_crud, hard_crud | development | original synthetic fixtures |
| Clinic (`clinic_inventory_legacy_hard`) | development | original synthetic benchmark; 7 of its 22 sites are converted by guarded, query-ID-specific templates |
| Helpdesk (`helpdesk_legacy_holdout`) | holdout | original synthetic; first run frozen at tag `c3-helpdesk-baseline-6of9` |
| HMS (kishan0725), NEWS-BUZZ, Fee-Rainbow | unseen third-party | public GitHub apps at pinned commits (`benchmarks/benchmark_manifest.json`) |

## 3. SQL recovery and conversion

Columns are total SQL sites / recovered / converted.

| Application | Pre-adaptation | Current code (post-adaptation) |
|---|---|---|
| simple_crud | — | 4 / 4 / 4 |
| mixed_crud | — | 6 / 5 / 5 (Q006 `buildQuery` unresolved) |
| hard_crud | — | 5 / 2 / 2 (dynamic ORDER BY, dynamic IN, conditional append unresolved) |
| Clinic | — | 22 / 17 / 21: **14 by general rules + 7 by guarded templates**; S22 (`mysqli_prepare`) is detected but not converted |
| Helpdesk | **9 / 7 / 6** (frozen baseline) | 9 / 9 / 8 (Q007 LEFT JOIN + GROUP BY not converted) |
| HMS | 44 / 19 / 19, crashed at stage 3/6 | 44 / 40 / 40 |
| NEWS-BUZZ | 32 / 4 / 4, crashed at stage 3/6 | 32 / 30 / 29 |
| Fee-Rainbow | 22 / 19 / 18, crashed at stage 3/6 | 22 / 20 / 19 |

The post-adaptation rows for Helpdesk and the unseen apps follow the S13 rule (SQL built and executed in the same conditional block). That rule was designed after inspecting these apps' pre-adaptation results, so these rows are **not** independent evidence of generalisation (`evidence/postadaptation/S13/README.md`). All 50 queries that S13 newly recovered were reviewed by hand, and none was unsound.

The audit also found unsound conversions, and they have been fixed:
- SQL built across loop iterations or `switch` cases now abstains (S1).
- SQL assigned in `catch` blocks now abstains (S13).
- Database calls are no longer silently dropped (S2).
- Schema types no longer crash the pipeline (S3).
- ALTER-style keys are recovered (S3b).

See `evidence/soundness/`.

## 4. MySQL behavioural validation

| Scope | Date / code | Result | Evidence |
|---|---|---|---|
| simple/mixed/hard CRUD (legacy PHP vs generated SQLAlchemy, recorded stages) | 2026-10-08, code before the audit | 18/18 checks (5 + 7 + 6) | `evidence/mysql_2026-10-08/` |
| Clinic fixtures (61 equivalence tests incl. write/rollback and injection) | 2026-10-15, `e77f854`, MySQL 8.0.39 | 61 passed | `evidence/mysql_2026-10-15/clinic_mysql_equivalence_log.txt` |
| Helpdesk and the 3 unseen apps | — | **not yet run** | planned as E1 (`docs/E1_equivalence_plan.md`) |

## 5. Unresolved query handling

Unresolved queries are not converted. Each is reported with file, line, reason code and reason (`unresolved_queries.json`), and `data_access.py` contains a stub that raises `NotImplementedError` for it. Reason codes:
- `BRANCHING`
- `LOOP_OR_SWITCH_CONTROL_FLOW`
- `STRUCTURAL` (dynamic ORDER BY / IN-list / table)
- `UNKNOWN_EXPR`
- `UNSUPPORTED_EXECUTOR`
- `MULTI_STATEMENT_EXECUTOR`
- `MISSING_SQL_ARGUMENT`
- `UNSUPPORTED_SHAPE`

## 6. Automated tests

**642 passed (581 without MySQL + 61 MySQL), 0 failed, as of 2026-10-15** (`evidence/mysql_2026-10-15/full_suite_with_mysql_log.txt`). After S13 and S4 the suite without MySQL is 597 passed, 61 skipped (`evidence/test_runs/2026-10-15_after_S4_pytest.txt`).

## 7. Limitations

See `docs/limitations_and_threats.md`. In short:
- **Scale:** results cover 8 small-to-mid applications.
- **Clinic:** it contains query-specific templates.
- **Adaptation:** the post-adaptation results were tuned after looking at the data.
- **Behavioural equivalence:** not yet shown on MySQL for the unseen apps.
- **Coverage gaps:** ENUM/SET and PDO are covered by fixtures only.
- **Generated code:** it uses `text()` + bind parameters, not ORM query constructs.

## 8. Conclusion

Within the stated scope, C3 recovers and converts the supported SQL of all eight applications end to end. It abstains, with location and reason, on dynamic constructs it cannot resolve safely. The Clinic MySQL run (61/61) provides behavioural evidence for the Clinic fixtures. Equivalence on the unseen applications and a fresh, untouched holdout remain to be shown.
