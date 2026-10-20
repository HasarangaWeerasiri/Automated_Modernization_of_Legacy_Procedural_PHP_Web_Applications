# C3 PP1 audit inventory (read-only audit, 2026-10-20)

- **Scope:** branch `database-migration` at HEAD `25b458c`. Pipeline source is unchanged since `4f75e79`, which only changed the reason string.
- **Data source:** every count comes from the current-code outputs of the 9-app gate run `t2_reason` (code `4f75e79`) and the ams-native run on the same code. Both are temporary runs outside the repository.
- **Restricted applications:** HMS, Dignity-Dev, ams-native and Fee-Rainbow appear as counts and query IDs only. This file contains no SQL text, PHP source, dump values or credentials.
- **Status of this file:** uncommitted audit notes. It changes no source, test, dashboard, evidence or doc file.

Sources read:
- `docs/c3_fr_status.json` (requirement text, quoted verbatim from `docs/C3_audit_2026-10-17.md`);
- `docs/C3_results_summary.md` and `docs/PP1_readiness_2026-10-19.md`;
- `benchmarks/benchmark_manifest.json` and the ground-truth metadata;
- `components/c3_data_layer/src`, `tools/php_ast`, `tests`, `dashboard`;
- `scripts/summarize_evidence.py`, `evidence/`;
- `requirements.txt`, `composer.json` / `composer.lock`.

---

## 1. Functional requirements FR1-FR10

The status column is the reviewed status in `docs/c3_fr_status.json`. The examples use project-authored or MIT applications (Clinic, Helpdesk, NEWS-BUZZ, the CRUD fixtures) wherever a code-level example could be shown.

### FR1 SQL Operation Identification — Implemented and measured
- **Requirement (verbatim):** "The system must identify database query operations within the legacy PHP source code. This includes 'mysqli', 'mysql', and PDO-based queries."
- **Implementation:**
  - `tools/php_ast/extract_sql_calls.php`: nikic/php-parser v5.9.0 AST. Procedural executors: `mysqli_query`, `mysqli_real_query`, `mysqli_multi_query`, `mysqli_prepare`, `mysqli_execute_query`, `mysqli_stmt_prepare`, `mysql_query`, `mysql_unbuffered_query`, `mysql_db_query`. Method calls: `query`, `prepare`, `exec`, `real_query`, `multi_query`, `execute_query`. Other DBMS APIs become `UNSUPPORTED_EXECUTOR` rows.
  - `src/c3/mapping/sql_locator.py` `analyze_php_ast` / `locate_sql_calls`.
- **Algorithm:**
  - The PHP AST is walked for executor calls, including PHP 8 named arguments.
  - Single-parameter wrapper functions that pass their argument to an executor are detected, and calls to them become sites (S-wrapper, `4fdd0d0`).
  - Each site keeps its file, line, executor and SQL-argument expression.
- **Example:** Clinic Q022 (`mysqli_prepare`, converted); NEWS-BUZZ 32 of 32 live sites detected.
- **Tests:** `test_sql_locator.py` (7), `test_executor_coverage.py` (21), `test_sql_wrappers.py` (13), `test_prepared_statements.py` (9).
- **Evidence:** S15 `detection` in `evidence/evaluation/<app>/...json`. Detection is 100% of ground-truth live sites in all five ground-truth apps: HMS 44/44, Dignity-Dev 13/13, Helpdesk 9/9, NEWS-BUZZ 32/32, Fee-Rainbow 54/54.
- **Improvements:** wrapper-call detection (`4fdd0d0`); PHP escape decoding (`a85a1a1`); PDO and prepared statements (`9b25bc1`).
- **Limitations:**
  - No benchmark application uses PDO, so PDO is covered by fixtures and unit tests only (manifest note).
  - Only the single-parameter wrapper shape is detected.
- **Future work:** multi-parameter wrappers and class-based DB layers.

### FR2 SQL Recovery — Partial
- **Requirement (verbatim):** "SQL queries constructed using variables, string concatenation, or conditional statements must be reconstructed whenever they can be safely resolved."
- **Implementation:** `src/c3/mapping/sql_recovery.py`. It recovers through:
  - `recover_queries_from_ast:1436`, `_split_concat_expression` and `_expression_to_template`;
  - `_decode_php_string:130`;
  - array-element and interpolation handling.

  Guards: `_detect_branching_assignment:665`, `_detect_loop_or_switch_dependency:741`, `_detect_structural_placeholder:801`, `_detect_sql_fragment_variable:976`. `src/c3/mapping/branch_recovery.py` `recover_simple_if_else_candidate:82` handles the guarded Clinic Q014 case.
- **Algorithm:**
  - The assignment chain of the SQL variable is followed backwards (`=` and `.=`), and literals, concatenation and interpolation are rebuilt into a template with `{{var}}` placeholders.
  - SQL that is built and executed in the same conditional block is recovered (S13, `5c7dd9b`).
  - Chains that depend on which branch, loop iteration or switch case ran, or whose variables carry SQL text (fragments, identifiers), are marked UNRESOLVED with a reason code.
- **Examples:**
  - NEWS-BUZZ Q002 and Q006: BRANCHING, abstained; ground truth says "not recoverable".
  - Clinic Q014 and Q016: branch-dependent, rebuilt only by query-specific templates.
- **Tests:** `test_sql_recovery.py` (16), `test_guarded_block_recovery.py` (14), `test_loop_switch_soundness.py` (16), `test_sql_fragment_variables.py` (15), `test_array_element_recovery.py` (10), `test_php_string_decoding.py` (22), and the `test_q014_*` and `test_q016_*` files.
- **Evidence:** S15 `recovery`. Precision and recall are 100% in all five ground-truth apps (ambiguous sites excluded). Latest files: `evidence/final/2026-10-20/s15/`, byte-identical to `evidence/final/2026-10-21/s15/`.
- **Improvements:** S13 same-block conditionals; array elements (`d542436`); escape decoder; fragment-variable rule; loop/switch soundness.
- **Limitation:** no general rule rebuilds SQL whose text differs between branches; only Clinic Q014 and Q016 do, through templates.
- **Is Partial still needed?**
  - **Against the ground truth,** every site the annotators judged recoverable is recovered (recall 100%). The branch-dependent sites are labelled "abstain".
  - **Against the wording,** "conditional statements ... whenever they can be safely resolved": Clinic Q014 and Q016 show such SQL *can* be resolved for exact shapes, but no general rule does it.
  - **Recommendation:** keep Partial. "Implemented within scope" is defensible only if the ground-truth abstain label is adopted as the definition of "cannot be safely resolved".
- **Future work:** general branch-variant reconstruction (FR2 branch recovery, deferred).

### FR3 Schema Analysis — Implemented and measured
- **Requirement (verbatim):** "Database tables, columns, data types, primary keys, foreign keys, and relationships must be identified from the MySQL schema."
- **Implementation:** `src/c3/mapping/schema_reader.py`:
  - `read_schema:565`, `parse_create_table`, `apply_alter_table_statements`;
  - VIEW/TRIGGER/PROCEDURE/FUNCTION/EVENT recorded in `unsupported_objects`.
- **Algorithm:**
  - Comments are stripped, then CREATE TABLE bodies are split and parsed into columns, keys, defaults, unique/check constraints and indexes.
  - phpMyAdmin-style `ALTER TABLE ... ADD PRIMARY KEY / MODIFY ... AUTO_INCREMENT` is applied afterwards (`71cfe98`).
  - Relationships come from declared foreign keys.
- **Example:** Helpdesk, 3 tables, 12 columns, 2 FKs; Clinic, 5 tables, 25 columns, 5 FKs.
- **Tests:** `test_schema_reader.py` (6), `test_alter_table_schema.py` (4), `test_schema_types_robustness.py` (33), `test_schema_fidelity.py` (9), `test_schema_comparison.py` (5).
- **Evidence:** E2 9 of 9 apps MATCH on every view (`evidence/e2/2026-10-15/`, `evidence/mysql_runs/2026-10-15_173248/e2/`).
- **Limitation:** relationships are inferred from declared FKs only; five apps declare none.
- **Note:** the Fee-Rainbow `db.sql` supplied with the app defines one table (see section 3, schema mismatch).

### FR4 Query and Column Analysis — Partial
- **Requirement (verbatim):** "The tables and columns used by each recovered query must be identified, as well as how those columns are subsequently used within the application logic."
- **Implementation:**
  - `src/c3/analysis/column_analyzer.py`: `column_roles:256`, `analyze_sql_columns:343`, alias resolution, `SELECT *` expansion against the schema.
  - `column_usage_generator.py` writes `column_usage.json`.
- **Algorithm:** each recovered or converted statement is parsed with SQLGlot. Tables and aliases are attributed, and columns are classified as read / write / filter / join / group / order / having.
- **Example:** Helpdesk Q007 (teams, agents, tickets; join, group and order columns).
- **Tests:** `test_column_analyzer.py` (11), `test_column_roles.py` (7), `test_column_usage_generator.py` (2), `test_sql_parser.py` (6).
- **Evidence:** S15 `column_use_accuracy`. 100% everywhere except Dignity-Dev write, 71.4%.
- **Improvement:** roles, table attribution and star expansion (`a33e0fc`).
- **Limitation:** how result columns are used later in the PHP is not traced.
- **Partial needed?** **Yes.** The second half of the requirement is not implemented.
- **Future work:** FR4 tracer of fetched-row keys into PHP use (deferred; it could change frozen HMS bytes).

### FR5 ORM Generation — Implemented and measured
- **Requirement (verbatim):** "The system must generate SQLAlchemy models with appropriate data types, keys, and relationships."
- **Implementation:**
  - `src/c3/conversion/model_builder.py` `generate_models_source:453`, `_build_relationships`, `key_columns`;
  - `src/c3/conversion/type_map.py` `mysql_to_sqlalchemy:111`.
- **Algorithm:**
  - One ORM class per table with a primary key; tables without one become Core `Table` objects.
  - Generic SQLAlchemy types are used where they are exact, with MySQL variants where the generic type would change the column (`9aeb3f2`).
  - `TINYINT(1)` is Boolean only for non-key columns (`17eebc9`). Relationships come from FKs.
- **Example:** Clinic `models.py` (5 tables).
- **Tests:** `test_model_builder.py` (10), `test_type_map.py` (11), `test_schema_types_robustness.py`.
- **Evidence:**
  - E2 9/9;
  - sqlacodegen baseline parity on tables, columns, PKs, nullability and types (`evidence/baseline/2026-10-19/`);
  - FKs comparable in 4 apps (12 relationships).
- **Limitation:** Core tables for key-less tables (Dignity-Dev: 4 of 6 have no PK).

### FR6 Data Access Generation — Partial
- **Requirement (verbatim):** "SQL operations that can be safely recovered must be converted into parameterized SQLAlchemy data access functions."
- **Implementation:**
  - `src/c3/conversion/query_converter.py`: `_convert_query:274`; `_has_unsafe_placeholder_position:110`; `_is_supported_join_select:821` (INNER); `_join_select_problem` / `_outer_join_shape_problem:701` (LEFT and comma); `_name_prepared_placeholders`.
  - Seven Clinic templates under `conversion/*_converter.py`.
  - `src/c3/generation/emitter.py` `generate_data_access_source:555`.
- **Algorithm:**
  - The recovered template is parameterized: quoted `{{v}}` become `:v`, and `%{{v}}%` LIKE patterns are bound with wildcards.
  - The SQL is parsed with SQLGlot. A single-table statement is accepted if every placeholder is in an accepted value position. A multi-table SELECT must satisfy the INNER JOIN rule or the LEFT/comma result-shape rule.
  - One `text()` function with named binds is emitted per site. SQL text is unchanged.
- **Examples:**
  - Helpdesk Q007 (LEFT JOIN, converted since `cba45fc`);
  - Dignity-Dev Q007-Q009 (comma joins);
  - Fee-Rainbow Q054 (abstains: tables not in parsed schema).
- **Tests:** `test_query_converter.py` (19), `test_outer_join_rule.py` (49), `test_value_positions.py` (36), `test_emitter.py` (10), `test_behavioral_execution.py` (10), the template tests, and `tests/benchmarks/clinic_hard/*` (76 with MySQL).
- **Evidence:**
  - conversion coverage of ground-truth recoverable sites: 128 of 129 (HMS 43/43, Dignity-Dev 7/7, Helpdesk 9/9, NEWS-BUZZ 30/30, Fee-Rainbow 39/40), in `evidence/final/2026-10-20/conversion_coverage_post_t2.json`;
  - E1 (HMS only): 27 PASS, 0 FAIL, 17 INCONCLUSIVE of 44;
  - Clinic MySQL verification 76/76.
- **Improvements:**
  - prepared statements (`9b25bc1`);
  - BETWEEN and allow-listed function arguments (`d263cbb`);
  - LEFT/comma join rule (`cba45fc`);
  - standalone generated module (`12d5759`);
  - transaction and LIMIT policy (`fd7b2f8`).
- **Limitations:**
  - Fee-Rainbow Q054 (recoverable) is not converted.
  - The INNER JOIN rule has no result-shape check. 4 converted INNER JOIN sites project `*`/`table.*`: Fee-Rainbow Q038 and Q041, ams-native Q016 and Q019. Their `result.mappings()` keys are not verified.
    - **Per-site finding (added 2026-10-20; record `evidence/final/2026-10-20/result_shape_star_join_sites.json`):**
      - **Fee-Rainbow Q038, Q041:** the projection is a single `table.*` with no other column. A table's column names are unique, so duplicate output names cannot arise. The column list itself cannot be determined, because the joined tables are not in the parsed schema.
      - **ams-native Q016, Q019:** `SELECT *` over 2 tables expands to 20 output columns, of which 2 names occur twice.
    - **Observation, SQLite only, not MySQL evidence:** with SQLAlchemy 2.1.3 on an in-memory SQLite database, a `text()` result with duplicate column names keeps every key in `keys()`. `mapping[name]` and `dict(mapping)` return the last duplicate, which is the same "last duplicate wins" behaviour as PHP `mysqli_fetch_assoc`.
  - E1 exists for HMS only.
- **Partial needed?** **Yes:**
  - one recoverable site is still unconverted;
  - behavioural equivalence covers HMS (with INCONCLUSIVE sites) and Clinic only;
  - the new join conversions have direct-SQL evidence only.
- **Future work:** apply the result-shape rule to INNER JOINs; complete the Fee-Rainbow schema; E1 for Dignity-Dev, Helpdesk and NEWS-BUZZ.

### FR7 Safety Checking — Implemented and measured
- **Requirement (verbatim):** "It must be verified whether runtime values can be safely parameterized, and dynamic values used in structural positions such as table names or column names must be identified."
- **Implementation:**
  - `query_converter._has_unsafe_placeholder_position` / `_is_value_position`;
  - `sql_recovery._detect_structural_placeholder`;
  - `conversion/identifier_allowlist.py` (schema-derived allow-lists);
  - `analysis/security_report.py` `build_security_report:329`.
- **Algorithm:**
  - A placeholder is bound only in a scalar value position: comparison RHS, IN item, INSERT value, `col + value`, BETWEEN bound, LIMIT/OFFSET, or the value argument of an allow-listed function.
  - Identifiers, fragments and every other position are flagged STRUCTURAL or abstain.
  - The security report traces legacy user inputs to placeholders and scans the generated code for non-literal `text()` calls.
- **Examples:** hard_crud Q002 (dynamic ORDER BY, STRUCTURAL); Clinic Q012 (schema allow-listed ORDER BY template); Clinic `test_q002_sql_injection_is_blocked`.
- **Tests:** `test_security_report.py` (9), `test_value_positions.py` (36), `test_e4_injection.py` (5), `test_input_binding.py` (34), the `test_q01x` template tests.
- **Evidence:** E4 163/163 SAFE, 12 payloads, recording session (`evidence/final/2026-10-20/e4/`); security report 0 generated-code violations in all 10 apps.
- **Limitation:** E4 is not a proof of safety. Runtime hostile tests on MySQL are limited to HMS (E1), Clinic `test_q002`, and one join-rule case.

### FR8 Safe Abstention — Implemented and measured
- **Requirement (verbatim):** "Queries that cannot be migrated confidently or safely must be flagged for human review rather than generating incorrect code."
- **Implementation:**
  - reason codes at recovery (`sql_recovery.py`) and conversion (`query_converter.py`);
  - `analysis/unresolved_report.py` `build_unresolved_report:16`;
  - `emitter._build_unresolved_stub` (a stub that raises, no SQL).
- **Algorithm:** every site that fails a guard is written to `unresolved_queries.json` with its stage, code and reason, and gets a stub function instead of generated SQL.
- **Examples:** hard_crud Q003 (IN-list fragment); NEWS-BUZZ Q002 (BRANCHING).
- **Tests:** `test_unresolved_report.py` (6), `test_report_consistency.py` (15).
- **Evidence:** correct abstentions in S15: Dignity-Dev 6/6, NEWS-BUZZ 2/2, Fee-Rainbow 12/12.
- **Limitation:** measurable only where the ground truth has non-recoverable sites. Fee-Rainbow Q054 is a recoverable site that abstains, which is a conversion gap, not a correct abstention.

### FR9 Migration Support — Implemented and measured
- **Requirement (verbatim):** "Alembic migration information required for the modernized data layer must be generated."
- **Implementation:** `src/c3/generation/alembic_emitter.py` `build_initial_revision:305`, `generate_alembic`.
- **Algorithm:** writes `alembic.ini`, `env.py` and `script.py.mako`, plus an initial revision that creates the tables in FK dependency order.
- **Evidence:** E3 9/9 PASS on MySQL (`evidence/mysql_runs/2026-10-19_000500/e3/`).
- **Tests:** `test_alembic_emitter.py` (7).
- **Limitation:** initial-schema revision only; no data migration.

### FR10 Traceability — Implemented and measured
- **Requirement (verbatim):** "The original PHP file and source location must be maintained for each recovered database operation. This information can be used for review and debugging purposes."
- **Implementation:** `src/c3/analysis/traceability.py` `build_traceability:28`; source labels in the generated docstrings (`emitter._display_source`).
- **Algorithm:** each site (converted or not) maps file:line → recovery status → conversion status/rule → generated function name and kind.
- **Example:** Helpdesk Q007 `report.php:5` → `select_teams_q007`.
- **Tests:** `test_traceability.py` (5), `test_handoff_site_labels.py` (8), `test_hms_handoff_signatures.py` (1).
- **Evidence:** S15 file:line matching; frozen HMS `signatures.json` hash `FA8C…DDC1`.
- **Limitation:** `HMS_HANDOFF.md` cites the Q036 site without its `include/` directory (documented).

---

## 2. SQL support matrix

Status values:
- **S** — supported and tested;
- **SR** — supported with restrictions;
- **X** — explicitly rejected;
- **EU** — encountered and unresolved;
- **NI** — not implemented;
- **NE** — not encountered;
- **NT** — not tested;
- **OOS** — out of scope.

"Probe" means the converter was run in memory on a synthetic statement during this audit.

### 2a. Construct occurrence in the 10 apps (SQLGlot parse of all stored recovered SQL; counts of statements, no SQL text)

| App | SELECT/INSERT/UPDATE/DELETE | Other stmt | Joins | Clauses (statements containing) | Functions | Parse failed |
|---|---|---|---|---|---|---|
| HMS | 28/9/6/1 | - | inner 1 | WHERE 21, AND 7, DISTINCT 1 | - | 0 |
| Dignity-Dev | 8/3/1/1 | - | comma 3 | WHERE 9, AND 4 | COUNT 1 | 0 |
| Helpdesk | 6/1/1/1 | - | inner 3, left 2 | WHERE 5, ORDER 4, GROUP 1, LIKE 1 | COUNT 1 | 0 |
| NEWS-BUZZ | 18/3/7/4 | - | - | WHERE 24, ORDER 4, AND 4, LIKE 1 | - | 0 |
| Fee-Rainbow | 31/5/4/5 | - | inner 2, left 4 | WHERE 22, ORDER 16, LIMIT 6, OFFSET 3, DISTINCT 2, LIKE 2, GROUP 1 | CURRENT_TIMESTAMP 14, COUNT 6, RAND 1 | 7 (unresolved fragment sites) |
| Clinic | 17/1/3/1 | - | inner 3 | WHERE 16, IN 2, LIMIT 1, OFFSET 1, GROUP 1, HAVING 1, ORDER 1, LIKE 1, subquery 1; multi-table UPDATE 1 | COUNT 2, SUM 1 | 0 |
| simple_crud | 1/1/1/1 | - | - | WHERE 2 | - | 0 |
| mixed_crud | 3/0/1/1 | 1 non-SQL (unresolved Q006) | - | WHERE 3 | - | 0 |
| hard_crud | 5/0/0/0 | - | inner 1 | WHERE 4, IN 1, LIKE 1, ORDER 1 | - | 0 |
| ams-native | 77/8/15/9 | SHOW 2, 1 non-CRUD | inner 2 | WHERE 61, LIMIT 11, ORDER 10, OFFSET 7, BETWEEN 4, LIKE 4, AND 3, OR 1 | MD5 5, NOW 4, CAST 1 | 0 |

**Never encountered in any of the 10 apps:** UNION, CTE, window functions, RIGHT/FULL/CROSS/NATURAL/USING/STRAIGHT_JOIN joins, derived tables, self-joins, mixed join kinds in one statement, multi-table DELETE, and placeholders inside a longer string literal.

### 2b. Matrix

| Pattern | Status | Rule (file:function) | Benchmark ID | Test | Reason / note |
|---|---|---|---|---|---|
| SELECT / INSERT / UPDATE / DELETE (single table) | S | `query_converter._convert_query` (one table, safe placeholders) | simple_crud (all four operations); NEWS-BUZZ (no joins) | `test_query_converter.py` | Core path. |
| Multi-table UPDATE | SR | `join_update_converter.convert_q020_join_update` (Clinic Q020 template only); generic form → X `MULTI_TABLE_REASON` | Clinic Q020 | `test_q020_join_update.py`, `test_q020_equivalence.py` | Template matches the exact Clinic file; probe of a generic UPDATE JOIN: UNRESOLVED. |
| Multi-table DELETE | X / NE | `MULTI_TABLE_REASON` | - | `test_outer_join_rule.py` (DELETE JOIN rejected) | Not encountered. |
| WHERE, AND, OR | S | value-position rule | ams-native (OR 1) | `test_query_converter.py` | |
| IN list of bound values | S / NE | `_is_value_position` (In expressions) | - (the IN occurrences found are the Clinic Q010 subquery and the Q011 / hard_crud Q003 fragments) | `test_value_positions.py`, `test_query_converter.py` | Unit-tested; not encountered as bound values. |
| IN with a whole-list fragment `IN ({{ids}})` | X (at recovery) | `sql_recovery._detect_structural_placeholder` → STRUCTURAL; Clinic Q011 template with integer-list binding | hard_crud Q003; Clinic Q011 | `test_q011_in_list.py` | The probe shows the converter alone would bind it as one value. The guard is at recovery only. |
| BETWEEN | SR | Between low/high of a column in WHERE/HAVING/ON | ams-native (4 statements) | `test_value_positions.py` | Since `d263cbb`. |
| LIKE `'%{{v}}%'` | S | `LIKE_WILDCARD_PATTERN`, wildcard parameter | Helpdesk, Clinic | `test_like_parameterization.py`, `test_like_generated_function.py` | |
| ORDER BY static | S | text unchanged | Fee-Rainbow, ams-native | `test_query_converter.py` | |
| ORDER BY dynamic column | X / SR | STRUCTURAL at recovery; Clinic Q012 schema allow-list template | hard_crud Q002; Clinic Q012 | `test_q012_order_by.py` | |
| GROUP BY / HAVING | S (NT for a bound HAVING value) | text unchanged; HAVING value position | Helpdesk Q007, Clinic | probe only for a bound HAVING value | |
| LIMIT / OFFSET bound | S | Limit/Offset expression; integer coercion in generated code | ams-native, Fee-Rainbow, Clinic Q019 | `test_transaction_and_limit.py` | |
| DISTINCT | S | text unchanged | HMS, Fee-Rainbow | covered by the conversion tests of those apps (no dedicated unit test) | |
| UNION | SR / NE / NT | One table → single-table path (probe: CONVERTED). Two tables → X | - | none | Not encountered, not tested. |
| Subquery | SR | One table → single-table path (probe: CONVERTED). Multi-table only via the Clinic Q010 template; generic → X | Clinic Q010 | `test_q010_subquery.py`; `test_outer_join_rule.py` (rejected under joins) | |
| CTE | X / NE | `WITH` alias counts as a second table → `MULTI_TABLE_REASON` | - | probe only | Not encountered. |
| Window functions | SR / NE / NT | single-table path (probe: CONVERTED) | - | none | Not encountered, not tested. |
| Aggregate and scalar functions in text | S | text unchanged (COUNT, SUM, NOW, CURRENT_TIMESTAMP, RAND encountered) | Helpdesk Q007, ams-native | conversion tests | No restriction unless a placeholder is inside. |
| Function with a bound argument | SR | `VALUE_FUNCTIONS` = MD5, SHA1 (`exp.SHA`), SHA2 (first argument), LOWER, UPPER, TRIM, DATE (`TsOrDsToDate` without format), CAST (value operand); one level, call in a value position | ams-native (MD5 5, CAST 1) | `test_value_positions.py` | Others (SLEEP, BENCHMARK, LOAD_FILE, CONCAT, nested calls) abstain. |
| INNER JOIN with ON | SR | `_is_supported_join_select` (no schema or result-shape check) | Helpdesk, Clinic, HMS, Fee-Rainbow, ams-native, hard_crud | `test_query_converter.py`, `test_outer_join_rule.py` (`test_inner_join_rule_is_unchanged`) | `*` allowed (see FR6 limitation). |
| LEFT [OUTER] JOIN with ON | SR | `_outer_join_shape_problem` (schema tables, explicit distinct outputs, unique column owners) | Helpdesk Q007 converted; Fee-Rainbow Q007 and Q054 EU | `test_outer_join_rule.py`, `test_join_rule_mysql.py` | |
| Implicit comma join | SR | `_is_comma_join` and the same rule | Dignity-Dev Q007-Q009 | same | |
| JOIN without ON (bare) | X | not comma, not LEFT → `MULTI_TABLE_REASON` | - | `test_comma_join_is_told_apart_from_join_without_on` | Not encountered. |
| RIGHT / FULL / CROSS / NATURAL / USING / STRAIGHT_JOIN | X / NE | `MULTI_TABLE_REASON` or the join-rule "shape" reason | - | `test_outer_join_rule.py` (REJECTED) | Not encountered. |
| Mixed INNER + LEFT | SR / NE | join rule | - | `test_outer_join_rule.py` | Not encountered in one statement. |
| Mixed comma + JOIN | X / NE | join-rule "shape" reason | - | `test_outer_join_rule.py` | Not encountered. |
| Self-join | SR / NE | LEFT/comma: join rule (aliases required). INNER: single-table path | - | `test_outer_join_rule.py` | Not encountered. |
| Derived-table join | X / NE | rejected | - | `test_outer_join_rule.py` | Not encountered. |
| PHP literal SQL | S | `_decode_php_string` | Clinic Q001 | `test_php_string_decoding.py` | Heuristic count on the SQL argument: 55 sites over the 10 apps. |
| Concatenation (`.`, `.=`) | S | `_split_concat_expression`, assignment chain | HMS, ams-native | `test_sql_recovery.py` | |
| Interpolation (`"... $v ..."`, `{$a['k']}`) | S | interpolation and array-element patterns | Clinic, ams-native, Fee-Rainbow | `test_array_element_recovery.py` | |
| Prepared statements (`?`, `:name`) | S | `_name_prepared_placeholders` | Clinic Q022 (`mysqli_prepare`) | `test_prepared_statements.py` (9) | Mixed `?` and named → X. |
| Function-parameter sources | SR | cross-file / function-argument resolution in `sql_recovery`; the generated function takes the value as a parameter | Dignity-Dev (SQL built from function parameters; STRUCTURAL where the parameter is an identifier) | `test_sql_recovery.py` | |
| Branch-dependent SQL | EU / SR | BRANCHING at recovery; same-block S13 recovered; Clinic Q014/Q016 templates | NEWS-BUZZ Q002/Q006, Dignity-Dev Q006, hard_crud Q004 | `test_guarded_block_recovery.py`, `test_q014_*`, `test_q016_*` | See FR2. |
| Loops / switch | EU | `LOOP_OR_SWITCH_CONTROL_FLOW` | Dignity-Dev Q003, ams-native Q064 | `test_loop_switch_soundness.py` | |
| Wrapper functions | SR | single-parameter wrapper detection | Fee-Rainbow (32 wrapper sites) | `test_sql_wrappers.py` | |
| Dynamic table / column names | EU / SR | STRUCTURAL; Clinic Q013 table allow-list template | Dignity-Dev Q001, Q002, Q013, Q014; ams-native Q061; Clinic Q013 | `test_q013_dynamic_table.py` | |
| Placeholder inside a string literal (`'x{{v}}y'`) | X / NE | `_has_unsafe_placeholder_position` string-literal guard (`d263cbb`) | - | `test_value_positions.py` | Not encountered. |
| SQL-fragment variables (clause text) | EU | `_detect_sql_fragment_variable` → STRUCTURAL | Fee-Rainbow (10 sites) | `test_sql_fragment_variables.py` | |
| SHOW and other non-CRUD statements | EU | UNSUPPORTED_SHAPE | ams-native Q060, Q062 | `test_sql_recovery.py` | |
| Multi-statement executors (`mysqli_multi_query`) | X / NE | MULTI_STATEMENT_EXECUTOR | - | `test_executor_coverage.py` | |
| Stored procedures, triggers, views, functions, events | OOS | `schema_reader` records them in `unsupported_objects`; `migration_notes.md` lists them | - | `test_schema_reader.py` | Not migrated, listed for review. |

---

## 3. Reason-code inventory (current code)

Codes come from `sql_recovery.py`, `_unanalysable_call_reason` and `recover_queries_from_ast` (recovery stage), and from `unresolved_report.py` (conversion stage). Counts are current-code unresolved entries. Clinic Q011, Q012, Q013 (STRUCTURAL) and Q014, Q016 (BRANCHING) are recovery-unresolved but are converted by templates, so they do not appear as unresolved.

**Code totals (counted from the source; corrected 2026-10-20).** An earlier summary of this audit said "seven recovery codes in use". That figure was wrong.
- **Recovery stage:** **8 defined**, **5 in use** (STRUCTURAL, BRANCHING, LOOP_OR_SWITCH_CONTROL_FLOW, UNKNOWN_EXPR, UNSUPPORTED_SHAPE). The 3 unused codes are UNSUPPORTED_EXECUTOR, MISSING_SQL_ARGUMENT and MULTI_STATEMENT_EXECUTOR.
- **Conversion stage:** 1 defined and in use (CONVERSION_UNSUPPORTED).
- **STRUCTURAL** has four distinct causes: dynamic table, dynamic ORDER BY, IN-list fragment and SQL-fragment variable.
- **UNKNOWN_EXPR** has two:
  - unsupported expression;
  - no recoverable assignment chain. That recovery record carries no code, and `unresolved_report.py` assigns UNKNOWN_EXPR as a fallback (Fee-Rainbow Q021, Q022).
- **Non-live sites** (dead code, for example Dignity-Dev Q010, and commented-out lines) are a category of their own, not a reason code.

| Stage / code | Meaning | Count per app (query IDs) | Manual review | Possible future solution |
|---|---|---|---|---|
| recovery / STRUCTURAL: dynamic table identifier | A variable supplies a table name | Dignity-Dev 4 (Q001, Q002, Q013, Q014); ams-native 1 (Q061) | Check the possible table values; hand-write per-table functions | Schema allow-list generalised from the Clinic Q013 template |
| recovery / STRUCTURAL: dynamic ORDER BY | A variable supplies a sort column | hard_crud 1 (Q002) | Map the allowed sort keys | Allow-list generalised from Clinic Q012 |
| recovery / STRUCTURAL: IN-list fragment | A variable holds a comma list | hard_crud 1 (Q003) | Confirm element types | Expanding bind generalised from Clinic Q011 |
| recovery / STRUCTURAL: SQL-fragment variable | A variable holds clause text | Fee-Rainbow 10 (Q023-Q030, Q049, Q050) | Rebuild the clause logic by hand | Fragment templates (out of scope) |
| recovery / BRANCHING | SQL text depends on which branch ran | Dignity-Dev 1 (Q006); NEWS-BUZZ 2 (Q002, Q006); hard_crud 1 (Q004) | Review each variant | General branch-variant IR (FR2 future work) |
| recovery / LOOP_OR_SWITCH_CONTROL_FLOW | Built across loop iterations or switch cases | Dignity-Dev 1 (Q003); ams-native 1 (Q064) | Rewrite as a batch or explicit statement | Bounded unrolling (not planned) |
| recovery / UNKNOWN_EXPR | Unsupported dynamic expression, or no recoverable assignment chain | mixed_crud 1 (Q006); Fee-Rainbow 2 (Q021, Q022) | Read the PHP | Wider expression support |
| recovery / UNSUPPORTED_SHAPE | Recovered text is not a CRUD statement (SHOW, empty or dead SQL) | Dignity-Dev 1 (Q010, non-live in ground truth); ams-native 2 (Q060, Q062) | Usually none (admin or diagnostic SQL) | Classify as non-data-access |
| recovery / UNSUPPORTED_EXECUTOR | Another DBMS's API | 0 in all apps | - | - |
| recovery / MISSING_SQL_ARGUMENT | SQL argument not identifiable | 0 | - | - |
| recovery / MULTI_STATEMENT_EXECUTOR | `multi_query`-style call | 0 | - | - |
| conversion / CONVERSION_UNSUPPORTED: join rule, table not in parsed schema | LEFT or comma join whose tables are missing from the schema | Fee-Rainbow 2 (Q007 ambiguous; Q054 recoverable) | Supply the full schema | Complete `db.sql` |
| conversion / CONVERSION_UNSUPPORTED: other reasons | Multi-table form outside the rules, placeholder in a non-value position, parse error, unsupported operation, prepared mixed placeholders, binding plan | 0 in the current runs | - | - |

**Other outcomes that are not reason codes:**
- **Ambiguous (ground truth):** excluded from precision and recall. HMS 1 (G041); Fee-Rainbow 2 (G007 = Q007 not converted, G011 converted).
- **Non-live sites:** HMS 8 commented-out; Fee-Rainbow 4 commented-out; Dignity-Dev 1 dead code (Q010). They are detected but excluded from the live-site denominators.
- **E1 INCONCLUSIVE:** HMS 17 of 44, triaged in `evidence/hms/E1_triage/triage.md`. INCONCLUSIVE is not PASS.
- **Schema mismatch (S15 `schema_mismatch`):** Fee-Rainbow 36 sites and HMS 6 sites reference tables or columns that are not in the supplied schema. Fee-Rainbow's `db.sql` defines one table; E2 records query-schema drift of 17 site-columns.

---

## 4. Applications register

Licence sources:
- `benchmarks/benchmark_manifest.json` (URL, commit, author, licence);
- `APP_INFO` in `scripts/summarize_evidence.py`;
- the app's own `LICENSE`;
- `evidence/baseline_unseen/ams_native/2026-10-20/CORRECTION_2026-10-20.md`.

Counts are current code. The evidence codes are S15, E1, E2, E3, E4, BG (byte gate), J (join-rule MySQL tests) and CM (Clinic MySQL verification).

| App | Source / licence (proof) | Category / why included | PHP files | Schema tables / cols / FKs | Detected / recovered / converted / unresolved | Ground truth | Evidence | Code display |
|---|---|---|---|---|---|---|---|---|
| HMS (kishan0725) | github.com/kishan0725/Hospital-Management-System @ 777fda46 (manifest); **no licence** (manifest) | unseen third-party; main E1 app | 29 | 6 / 42 / 0 | 44 / 44 / 44 / 0 | yes, 52 entries (44 live, 1 ambiguous) | S15, E1, E2, E3, E4, BG, frozen handoff | **counts only** |
| Dignity-Dev | github.com/Dignity-Dev/Hospital-Management-System @ b9e939d6; MIT (manifest; `LICENSE` in the app) | fresh holdout, first run frozen 2026-10-15 | 18 | 6 / 25 / 0 | 14 / 7 / 7 / 7 | yes, 14 entries (13 live) | S15 (holdout + current), E2, E3, E4, BG | counts only (restricted by your rule; licence permits more) |
| Helpdesk | project-authored synthetic holdout (manifest) | holdout; baseline tag `c3-helpdesk-baseline-6of9` | 9 | 3 / 12 / 2 | 9 / 9 / 9 / 0 | yes, 9 | S15, E2, E3, E4, BG | allowed (Before/After detail) |
| NEWS-BUZZ | github.com/anirbandutta9/NEWS-BUZZ @ 3bf43cfd; MIT, Copyright (c) 2017 Anirban Dutta (manifest) | unseen third-party | 34 | 2 / 16 / 0 | 32 / 30 / 30 / 2 | yes, 32 | S15, E2, E3, E4, BG | allowed |
| Fee-Rainbow | github.com/jimmyyrp/Fee-Rainbow @ 6285fb53; the manifest says "MIT (Copyright (c) 2025 Randa)", but `APP_INFO` says "LICENSE file present; licence type not verified". **Discrepancy at audit time; resolved later on 2026-10-20** by `evidence/provenance/fee_rainbow_licence.json`. The `LICENSE` at the recorded commit was compared word for word with the standard MIT text and found identical; `APP_INFO` now cites the record. Fee-Rainbow stays counts-only. | unseen third-party; wrappers, fragments | 56 | 1 / 5 / 0 (supplied `db.sql`) | 54 / 42 / 40 / 14 | yes, 58 entries (54 live, 2 ambiguous) | S15, E2, E3, E4, BG | **counts only** |
| Clinic | project-authored synthetic (manifest) | development; 7 query-specific templates | 24 | 5 / 25 / 5 | 22 / 17 / 22 (15 general + 7 template) / 0 | no | E2, E3, E4, BG, CM 76/76 (63 PHP-vs-generated + J 13) | allowed |
| simple_crud | project fixture | development | 4 | 2 / 8 / 1 | 4 / 4 / 4 / 0 | fixture expectations (`benchmarks/ground_truth/`) | E2, E3, E4, BG | allowed |
| mixed_crud | project fixture | development | 6 | 1 / 3 / 0 | 6 / 5 / 5 / 1 | fixture expectations | E2, E3, E4, BG | allowed |
| hard_crud | project fixture | development | 5 | 5 / 25 / 4 | 5 / 2 / 2 / 3 | fixture expectations | E2, E3, E4, BG | allowed |
| ams-native-3c4d475 | no URL or commit recorded. **Original state:** ZIP hash not recorded and licence unknown (baseline README, committed `adeb1c8`). **Later on 2026-10-20:** ZIP found, PHP and schema hashes match, `LICENSE.txt` is MIT (correction note) | unseen, no ground truth; value-position sprint check | 42 | 7 / 58 / 0 | 112 / 108 / 108 / 4 | no | E4-style (108 SAFE), SQLite Alembic round trip, BG-style comparison, pre/post counts | **counts only**; not on the dashboard |

Notes:
- **HMS dump:** the only HMS application file kept in the repository is `myhmsdb.sql`. Its values are never exported.
- **Earlier HMS E1 run:** superseded; it showed 27 PASS, 5 FAIL, 12 INCONCLUSIVE.

---

## 5. Technology inventory (verified imports)

| Library | Version | Where it is actually used | What C3 adds |
|---|---|---|---|
| nikic/php-parser | v5.9.0 (`composer.lock`) | `tools/php_ast/extract_sql_calls.php` (run by `sql_locator._run_ast_extractor`) | Executor and wrapper detection, argument positions, assignment and branch facts as JSON |
| sqlglot | 30.21.0 | `src`: `query_converter`, `column_analyzer`, `identifier_allowlist`, `branch_ir`, `branch_recovery`, `sql_parser`; scripts: `schema_comparison`, `equivalence/*`, `make_handoff` | Value-position safety rule, join and result-shape rules, column roles; SQL text is never rewritten by SQLGlot |
| SQLAlchemy | 2.1.3 | `src`: `alembic_emitter` only (imports), plus generated-code text (`text()`, models); tests (31 files); E1/E3/E4/baseline scripts | Generated models, typed functions with named binds, the TINYINT/variant type policy |
| Alembic | 1.20.0 | `src/c3/generation/alembic_emitter.py`; `scripts/alembic_roundtrip.py`, `run_e3_mysql.ps1` | Initial revision in FK dependency order |
| PyMySQL | 1.2.3 | scripts and tests only (E1, E2 live, E3, sqlacodegen baseline, Clinic MySQL tests); not in `src` | - |
| sqlacodegen | 4.0.4 | `scripts/baseline_sqlacodegen.py` only (comparison baseline; `src` does not use it) | - |
| pytest | 9.1.1 | tests | - |
| fastapi / starlette / httpx | 0.142.4 / 1.7.0 / 0.28.1 | `tests/backend_verification/app.py` and `test_app.py` only | - |
| standard library `http.server` | - | `components/c3_data_layer/dashboard/server.py` (Try It bridge; no web framework) | Job isolation, temporary folders, ZIP download |

Also checked:
- **Pinned in `requirements.txt` but not imported in `src`:** inflect, typer, pydantic, Jinja2 (used by Mako, which Alembic depends on), uvicorn, rich and others. These are transitive or tool dependencies.
- **`components/c3_data_layer/pyproject.toml`:** empty (0 bytes). pytest uses it only as the root marker.

---

## 6. Output artifacts (from `src/c3/pipeline.py run_pipeline`)

| Artifact | Stage | Fields / content | Purpose |
|---|---|---|---|
| `queries.json` | 1 recover | `query_id`, `source_file`, `source_line`, `executor`, `sql_argument`, `recovered_sql`, `operation`, `dynamic_variables`, `status`, `reason_code`, `reason`; optional `wrapper`, `dynamic_variable_sources`, `sql_variants`, `binding_plan`, `cross_file_*` | Recovery result per site |
| `converted_queries.json` | 2 convert | `query_id`, `status`, `reason`, `operation`, `table`, `parameters`, `parameterized_sql`, `like_wildcard_parameters`, `conversion_rule`; template extras | Conversion result per site |
| `schema.json` | 3 schema | `tables[]` (name, columns with `data_type`, `nullable`, `primary_key`, `auto_increment`, `default`, `on_update`, `unique`; foreign keys, indexes, unique/check constraints, `unsupported_features`); `unsupported_objects` | Parsed schema |
| `models.py` | 3 | SQLAlchemy ORM classes and Core Tables | **Primary output** |
| `migration_notes.md` | 3 | Unsupported column types; tables without a PK; table features not migrated; MySQL-specific DDL kept; database objects not migrated | Review list |
| `data_access.py` | 4 emit | One function per converted site; a stub for each unresolved site; runtime helpers inlined (no `c3` import) | **Primary output** |
| `column_usage.json` | 5 | `tables`, `read`/`write`/`filter`/`join`/`group_by`/`order_by`/`having` columns, `qualified_columns`, `expanded_read_columns`, `dynamic_identifiers`, `analysis_source` | FR4 |
| `traceability_report.json` | 6 | file:line, executor, statuses, rule, reason, tables and columns, `generated_function`, `generated_function_kind`, `action` | FR10 |
| `unresolved_queries.json` | 6 | `query_id`, file:line, `stage`, `reason_code`, `reason`, `action`, `recovered_sql`, `sql_argument` | **Primary output** (FR8) |
| `security_report.json` | 6 | `summary` (legacy injection sites, remediated, bound or allow-listed, violations), `scope`, `queries[]`, `generated_code_scan` | FR7 |
| `alembic/` | 7 | `alembic.ini`, `env.py`, `script.py.mako`, `versions/0001_initial_schema.py` | FR9 |
| `summary.json` | end | counts by stage, `primary_outputs`, `supporting_artifacts`, `output_directory` | Run summary |

`src/c3/integration.py` `ARTIFACT_NAMES` lists 8 names. It omits `traceability_report.json`, `security_report.json`, `migration_notes.md` and `alembic/`, which the pipeline does write.

---

## 7. Metric definitions (dashboard) and misreading risks

| Metric | Numerator / denominator | Evidence | Misreading risk |
|---|---|---|---|
| SQL sites detected (count) | all detected sites, including non-live ones | `summary.total_queries` | Dignity-Dev 14 includes non-live Q010 |
| SQL detection % | detected ground-truth live sites / live sites | S15 `detection` | Detection, not conversion |
| Recovery precision / recall | TP / (TP + FP); TP / (TP + FN); ambiguous excluded | S15 `recovery` | Fee-Rainbow excludes 2 ambiguous, HMS 1 |
| Correct abstentions | TN detected / (TN + FP) | S15 | Only where the ground truth has non-recoverable sites |
| Converted functions (count) | `summary.converted_queries` (general + template) | current run | Clinic 22 > recovered 17 (templates); Fee-Rainbow 40 = 39 recoverable + 1 ambiguous |
| Conversion coverage (ground truth) | converted ground-truth-recoverable / (TP + FN) | `conversion_coverage_gt` | Dignity-Dev 7/7. **Not** 7/14 (detected) or 6/13 (manual review) |
| Migration Impact "x of y detected sites converted" | converted / all detected | `before_after` facts | Dignity-Dev **7 of 14** |
| Manual-review rate | (live unresolved at recovery + conversion) / live sites | `manual_review` | Dignity-Dev **6 of 13 = 46.2%**; a proxy, not effort |
| General-rule share (Clinic) | general / all converted = 15/22 | current run | Templates are query-specific |
| E1 | per-site PASS / FAIL / INCONCLUSIVE, HMS only: 27 / 0 / 17 of 44 | `evidence/mysql_runs/2026-10-15_195730/e1/` | INCONCLUSIVE is not PASS |
| Clinic MySQL verification | 76/76 = 63 PHP-vs-generated + 13 direct-SQL join-rule tests | `evidence/mysql_runs/2026-10-20_135617/` | **Not E1**; the 13 are not application-PHP comparisons |
| Join-rule MySQL tests | 13/13 | same log | Counted inside the 76 |
| E2 | apps with every view MATCH = 9/9 | `evidence/e2/2026-10-15/` | Fee-Rainbow MATCH is on a one-table schema |
| E3 | apps PASS = 9/9 (MySQL) | `evidence/mysql_runs/2026-10-19_000500/e3/` | Initial schema only |
| E4 | SAFE functions / functions = 163/163; 12 payloads; benign calls = functions × 4 | `evidence/final/2026-10-20/e4/` | Recording session; functions without a bound value pass trivially |
| Tests | non-MySQL 990 passed / 74 skipped; with MySQL 1064/0/0 | `evidence/final/2026-10-20/test_run_non_mysql.txt`, `evidence/mysql_runs/2026-10-20_135617/` | Same test tree: 990 + 74 = 1064; Clinic 76 inside 1064; older rows are history |
| sqlacodegen baseline | matching tables / columns / PKs / nullability / types / FKs | `evidence/baseline/2026-10-19/` | FKs comparable in 4 apps only |
| Execution time | median of 3 CLI runs, 0.59-0.98 s | `evidence/performance/2026-10-18/` | One machine |
| Before the FR6 join rule | ground-truth coverage at `adeb1c8` | `conversion_coverage_post_t2.json` | Not the holdout result |

---

## 8. Gaps: what the dashboard does not show (priority for a 4-hour build)

1. **Result-shape warning for converted INNER JOIN `*` sites.** Fee-Rainbow Q038 and Q041 (`table.*`) and ams-native Q016 and Q019 convert without a result-shape check. The dashboard shows them as plain conversions. *(Done 2026-10-20: the SQL Support Matrix shows the warning with the per-site finding from section 1, FR6.)*
2. **SQL support matrix.** Section 2, including "not encountered" versus "not implemented" from the construct counts.
3. **Reason-code inventory** with per-app counts, IDs, manual-review guidance and future solution (section 3). The dashboard groups by code and family only.
4. **Denominator legend:** 7/14 vs 6/13 vs 7/7; 40 converted vs 39/40 recoverable; Clinic 22 converted vs 17 recovered.
5. **Applications register** with licence-proof files and an evidence-applied matrix. Flag the Fee-Rainbow licence discrepancy (manifest "MIT" vs generator "not verified"). *(Done 2026-10-20: the register is on the dashboard. The discrepancy was resolved by the provenance record; see section 4.)*
6. **ams-native is absent from the dashboard.** It appears only as a Try It upload. A counts-only card could show pre/post (99 → 108), its evidence and its licence history.
7. **Schema-mismatch counts** (Fee-Rainbow 36 sites, HMS 6) and the one-table Fee-Rainbow schema next to the E2 MATCH.
8. **E4 trivially-safe count:** functions with no bound value (for example Dignity-Dev Q008 and Helpdesk Q007).
9. **Technology and artifact inventory** (sections 5-6), including the `integration.py` artifact-list omission.
10. **Converter-level note on the IN-list fragment.** Only recovery blocks `IN ({{ids}})`; the converter alone would bind it. A defence-in-depth gap, not a current defect.
