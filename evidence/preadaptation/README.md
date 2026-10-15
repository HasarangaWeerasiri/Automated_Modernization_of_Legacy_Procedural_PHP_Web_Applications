# Pre-adaptation runs on unseen third-party apps (frozen)

These are the first C3 runs on three third-party apps, made **before any converter change** (plan v2, rule 4). Do not edit or regenerate these files. Re-runs after fixes go to `evidence/postadaptation/<app>/`.

- **Code:** repo HEAD `cb8a6fe`. `components/c3_data_layer/src` and `tools/` are identical to `01869d8` (the Helpdesk baseline code).
- **Date:** 2026-10-15.
- **Input:** an app-only copy of each app's PHP files, made with `benchmarks/tools/make_app_only_copy.py`. The exact list is in `<app>/app_only_file_list.json`, and pinned commits are in `benchmarks/benchmark_manifest.json`.
- **Command** (from `components/c3_data_layer`, `PYTHONPATH=src`):
  `python -m c3.cli run --php ../../benchmarks/apps/_app_only/<app> --schema ../../benchmarks/apps/<app>/<schema.sql> --output ../../evidence/preadaptation/<app>/output`

## Results

| App | Exit | Stopped at | Error | SQL sites | Recovered | Unresolved (reason codes) | Converted | Conversion-unresolved |
|---|---|---|---|---|---|---|---|---|
| hms_kishan0725 | 1 | stage 3/6 | `ValueError: Unsupported MySQL type: INT(11)` | 44 | 19 | 25 (BRANCHING 21, UNKNOWN_EXPR 4) | 19 | 25 |
| news_buzz | 1 | stage 3/6 | `ValueError: Unsupported MySQL type: INT(4)` | 32 | 4 | 28 (BRANCHING 28) | 4 | 28 |
| fee_rainbow | 1 | stage 3/6 | `ValueError: Unsupported MySQL type: INT(11)` | 22 | 19 | 3 (BRANCHING 1, none 2) | 18 | 4 |

Per-app details are in `<app>/partial_summary.json` and `<app>/run_log.txt`. Only `schema.json`, `queries.json` and `converted_queries.json` were written. Models, data access and reports were never reached.

## Findings

1. **Crash on display-width integers.** `INT(11)` / `INT(4)`, the default in phpMyAdmin dumps, aborts every run at stage 3. The type map accepts `INT` but not `INT(n)`.
2. **ALTER-style primary keys are lost.** All three schemas declare PKs with `ALTER TABLE … ADD PRIMARY KEY`. `schema.json` has **0** primary-key columns, so ORM models could not be mapped even without the crash.
3. **Detection is complete for these apps.** An independent grep (`detection_check.py`, output in `detection_check_output.txt`) found every non-commented executor line among the detected sites. The 8 undetected HMS lines are all commented out.
4. **BRANCHING dominates the unresolved queries** (21/25 HMS, 28/28 NEWS-BUZZ). This is the Helpdesk Q002/Q004 class (SQL executed inside an `if` block), task S13. It still needs a manual check before it can be called a false abstention.
5. **Coverage gap.** None of these apps uses ENUM/SET or PDO. Those features are covered only by fixtures.
