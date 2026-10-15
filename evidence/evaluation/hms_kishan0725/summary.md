# S15: HMS evaluation against the verified ground truth (2026-10-15)

Ground truth: `benchmarks/ground truth/hms_kishan0725/queries.json`. It has 52 entries: 44 live, 43 recoverable, 1 ambiguous and 8 commented out. It was written blind and verified by the author. Matching is by (file, line). Command:

`python -m c3.cli evaluate --php benchmarks/apps/_app_only/hms_kishan0725 --output <c3 out> --ground-truth "benchmarks/ground truth/hms_kishan0725/queries.json" --report <file>`

| Metric | Before H1 (`b20f1d1`) | After H1 (current) |
|---|---|---|
| Detection of live sites | 44/44 (100%) | 44/44 (100%) |
| False detections (commented-out lines reported) | 0 | 0 |
| Recovery TP / FN / FP / TN (ambiguous excluded: 1) | 40 / 3 / 0 / 0 | **43 / 0 / 0 / 0** |
| Recovery precision / recall | 100.0 / 93.0 | **100.0 / 100.0** |
| False recoveries (unsound) | none | none |
| Column use (operation, tables, read, write, filter), schema-consistent TP sites | 100% on each (34 sites) | **100% on each (37 sites)** |
| Schema-mismatch sites, reported separately | 6 (column use 100%) | 6 (column use 100%): `appointmenttb.payment` at func.php:34, func2.php:41, func3.php:24, newfunc.php:23; `doctb.name` at func2.php:66, func3.php:49 |
| Ambiguous, excluded | include/header.php:35 | include/header.php:35 (table `users` not in the schema) |

**Label: post-adaptation.** H1 was found by reviewing the 4 HMS abstentions, so the "after" column is no longer a holdout result for HMS. The H1 rule is general (no app or query IDs), and it changed nothing on the other 8 apps. The "before" column is the last pre-adaptation measurement on HMS. Fresh-holdout evidence remains Dignity-Dev (`evidence/fresh_holdout/`).

The HMS SQL is simple (single-table CRUD, one JOIN), which explains the 100% column-use results. They do not carry over to harder apps.
