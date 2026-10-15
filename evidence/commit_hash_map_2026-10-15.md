# Commit hash map: Claude co-author trailer removed (2026-10-15)

On 2026-10-15 the 58 commits from `cb8a6fe` to the head of `database-migration` were rewritten with `git filter-branch --msg-filter`. The rewrite removed the `Co-Authored-By: Claude` trailer from 52 commit messages. File content (trees), authors, author and committer dates, and subjects are unchanged; only the hashes changed. The tag `hms-handoff-v1` was re-pointed to the rewritten commit with the same tree.

Hand-written docs (`docs/`, the evidence register, `evidence/hms/`, HMS_HANDOFF.md) were updated to the new hashes. Raw logs and run outputs (`run_log.txt`, pytest logs, `git_head` fields in JSON, `# git HEAD:` lines), and everything under `evidence/preadaptation/`, keep the hash that was current when they were produced. Use this table to resolve those.

| Old | New | Subject |
|---|---|---|
| `cb8a6fe` | `371fddf` | docs(c3): fill status ledger and start evidence register (514 passed / 61 skipped) |
| `1f85363` | `63db48b` | evidence(c3): pre-adaptation runs on 3 unseen third-party apps (HMS, NEWS-BUZZ, Fee-Rainbow) |
| `dab577a` | `f7a4119` | test(c3): add runner script for MySQL tests on the local MySQL80 service |
| `82baac3` | `5ad2dbf` | fix(c3): abstain on SQL that depends on loop iterations or switch cases (S1) |
| `b5da075` | `90a6cdb` | fix(c3): report every database execution call, no silent drops (S2) |
| `217c5c3` | `1241491` | fix(c3): never abort the pipeline on a column type (S3) |
| `e77f854` | `71cfe98` | fix(c3): recover ALTER TABLE keys; emit key-less tables as Core tables (S3b) |
| `3b13ef9` | `222d74e` | evidence(c3): MySQL 8 Clinic equivalence run, 61 passed; full suite 642 passed, 0 skipped |
| `7a08033` | `6b814ea` | fix(c3): make MySQL test reset idempotent and write UTF-8 logs |
| `fd99762` | `2a300d7` | docs(c3): record 2026-10-15 MySQL run (642 passed) and plan E1 equivalence |
| `5b9fba4` | `5c7dd9b` | feat(c3): recover SQL built and executed in the same conditional block (S13) |
| `aa2adfe` | `a9abae8` | docs(c3): fix escaped tab example in limitations |
| `d7c4efe` | `18279a4` | fix(c3): evidence hygiene, fresh Clinic test output and docs matching code (S4) |
| `6c13926` | `e46427a` | evidence(c3): clean-clone test run passes (597 passed, 61 MySQL skipped) |
| `1608d21` | `378a0c0` | fix(c3): consistent unresolved report and unambiguous summary counts (S5) |
| `89d14a9` | `17b3e51` | feat(c3): security report and schema-derived identifier allowlists (S6) |
| `2695883` | `c9190a1` | evidence(c3): preserve 688-test MySQL verification run |
| `4d02c96` | `eb825f2` | evidence(c3): record 688-passed MySQL re-run; timestamped MySQL run folders |
| `f389774` | `cd046ba` | evidence(c3): fresh holdout run on Dignity-Dev HMS (current rules, post-S13) |
| `84f02ff` | `ca7da89` | docs(c3): explicit E1/E3 disposable database statements for MySQL Workbench |
| `33f5bf4` | `89aa819` | feat(c3): mark the three primary outputs; everything else is supporting |
| `0153283` | `806d36c` | feat(c3): schema fidelity and robust ORM relationships (S9) |
| `cc7a8a8` | `2f43376` | feat(c3): Alembic migration emitter as pipeline stage 7 (S10) |
| `367c393` | `bc8fd9e` | test(c3): Alembic emitter tests and E3 round-trip harness (S10) |
| `0bdb823` | `3caed79` | fix(c3): set path_separator in generated alembic.ini (removes Alembic deprecation warning) |
| `cda40ed` | `40e2165` | evidence(c3): E3 Alembic round trip on SQLite (7 PASS, 2 N/A) and MySQL runner (S10) |
| `25c5c71` | `218acf7` | feat(c3): traceability report as a pipeline output (S12) |
| `bb91032` | `2f55b2c` | evidence(c3): E3 Alembic MySQL verification 9 of 9 passed |
| `e6ce597` | `ced55e2` | docs(c3): record E3 MySQL 9/9 PASS; FR9 verified in Status Ledger |
| `162b60c` | `261a20b` | evidence(c3): ground-truth candidate sites from independent grep for 5 apps (annotation templates) |
| `0642416` | `b8c9f2a` | feat(c3): E1 equivalence harness (PHP-built legacy SQL vs generated SQLAlchemy, per site) |
| `fa7f983` | `91dfccd` | evidence(c3): E1 case-file skeletons, fixture seeds and dry run (all converted sites READY) |
| `5a82ed0` | `9aeb3f2` | fix(c3): keep exact MySQL types where the generic type would change them (TIMESTAMP, TINYINT, CHAR, DOUBLE, fsp, TEXT/BLOB sizes, YEAR, BINARY) |
| `5ec054a` | `fd9f097` | evidence(c3): E2 schema comparison (independent sqlglot parse vs schema.json vs ORM): MATCH 9/9 after type fix; MySQL live runner |
| `ea46d01` | `bef0430` | evidence(c3): E4 injection negatives on every generated function (132/132 SAFE) with negative controls |
| `7139a66` | `12d5759` | feat(c3): standalone generated data_access.py (runtime helpers inlined, no c3 imports) with PHP-root-relative source paths (S7) |
| `85626b0` | `fd7b2f8` | feat(c3): explicit transaction policy (commit: bool = True) and LIMIT/OFFSET integer coercion in generated code (S8) |
| `9e860c6` | `9886dd6` | evidence(c3): independent ground truth drafts (blind, pending author verification) |
| `91c91e9` | `a33e0fc` | feat(c3): column-use roles, table attribution, SELECT * expansion and template column use (S11, FR4) |
| `b20f1d1` | `9b25bc1` | feat(c3): convert prepared statements by naming ? and :name placeholders (S14); Clinic Q022 converts |
| `18d12d2` | `56ff575` | evidence(c3): verify HMS ground truth |
| `65e807d` | `d542436` | fix(c3): recover array elements with literal keys as named values; HMS H1 review (44/44/44) |
| `a791cd8` | `d516a08` | evidence(c3): HMS E2 offline MATCH + schema drift, E4 44/44 SAFE, E1 dry run 44/44 READY |
| `4a909c5` | `e14aa38` | docs(c3): HMS H2 proposal for tables without primary keys (no invented keys) |
| `a43c155` | `0f47511` | feat(c3): site-level evaluator and c3 evaluate CLI; HMS evaluation (S15) |
| `e4fe10d` | `7e64601` | evidence(c3): HMS E1 fill-in table (one row per legacy input, bound columns located by PHP-built SQL) |
| `a4ed6b2` | `7ee1091` | fix(c3): generated alembic env.py must not add C3_DB_PASSWORD to SQLite URLs |
| `dd15f54` | `6b1c7ef` | evidence(c3): student MySQL runs: E3 9/9 PASS, E2 live MATCH 9/9, Clinic 63 passed, full suite 1 failure (fixed in a4ed6b2) |
| `3458bbb` | `62b8c82` | feat(c3): HMS handoff package (HMS_HANDOFF.md, frozen signatures, standalone check); parameter sources in docstrings (H4) |
| `72a599f` | `f04f939` | fix(c3): security report credits array-element parameters (HMS coverage 100%) |
| `c60e32c` | `6b6c1d2` | docs(c3): HMS FR1-FR10 matrix with evidence |
| `10c98f8` | `eea6a6e` | docs(c3): Status Ledger at HMS handoff state |
| `6963241` | `500473e` | evidence(c3): HMS E1 MySQL run (27/5/12) and triage of every non-PASS site |
| `26402af` | `6b54bf0` | fix(c3): E1 classifies injected-legacy/rejected-generated hostile cases correctly and requires structural evidence for LEGACY_INJECTABLE |
| `f9d17e7` | `bab56de` | docs(c3): HMS FR6 from E1 (27/44 PASS) and per-function E1 status in HMS_HANDOFF.md |
| `01f427e` | `d96987c` | docs(c3): HMS FR matrix open item points to the E1 re-run |
| `65b6f21` | `42029b0` | HMS: update E1 results in FR matrix and handoff |
| `1d6977f` | `244cfa7` | evidence: HMS E1 MySQL re-run summary (27 PASS, 17 INCONCLUSIVE) |
