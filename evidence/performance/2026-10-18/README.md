# C3 execution time - 2026-10-18

**What this is:** end-to-end CLI analysis time on this machine, includes Python start-up; no database.

Raw data: `execution_time.json` (all runs, the run order, the machine, the code version).

## Method

| Item | Value |
|---|---|
| Code | git HEAD `6769ae010bb6a2e0f55f18a8490805be7c8e6bfc`; `components/c3_data_layer/src` and `tools` had no uncommitted changes (the script refuses otherwise) |
| When | 2026-10-18, 15:12:03 to 15:12:25 +05:30 |
| Command per run | `python -m c3.cli run --php <app PHP root> --schema <app schema> --output <new temp folder>`, cwd `components/c3_data_layer`, `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`, no database credentials in the environment |
| Timer | `time.perf_counter()` around the whole subprocess, from process start to exit. This includes Python interpreter start-up, module imports, the PHP AST extractor process, all 7 pipeline stages and writing every output file |
| Runs | 3 runs per app, one run at a time, round-robin: round 1 runs all 9 apps, then round 2, then round 3. Outputs go to new folders under `C:\Users\sanjula\AppData\Local\Temp\c3perf` (outside the repository) |
| Machine | Microsoft Windows 11 Pro (10.0.26200); 11th Gen Intel Core i5-1135G7 @ 2.40 GHz, 4 physical cores / 8 logical processors; 15.7 GB RAM; CPython 3.13.5; PHP 8.2.12 |
| Inputs | The 9 benchmark apps with the same PHP roots and schemas as the evaluation runs (`scripts/regenerate_outputs.ps1`, `scripts/summarize_evidence.py`) |

## Results (seconds, all runs)

| App | Sites detected | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) |
|---|---|---|---|---|---|
| hms_kishan0725 | 44 | 0.866 | 0.854 | 1.014 | 0.866 |
| dignity_dev_hms | 14 | 0.748 | 0.739 | 0.811 | 0.748 |
| helpdesk_legacy_holdout | 9 | 0.610 | 0.729 | 0.625 | 0.625 |
| news_buzz | 32 | 0.949 | 1.037 | 0.976 | 0.976 |
| fee_rainbow | 54 | 0.929 | 1.018 | 0.896 | 0.929 |
| clinic_inventory_legacy_hard | 22 | 0.924 | 1.019 | 0.889 | 0.924 |
| simple_crud | 4 | 0.571 | 0.610 | 0.601 | 0.601 |
| mixed_crud | 6 | 0.565 | 0.615 | 0.592 | 0.592 |
| hard_crud | 5 | 0.611 | 0.925 | 0.650 | 0.650 |

## Limits

- One machine, one session, 3 runs per app. This is not a benchmark of other hardware, and three runs give no reliable estimate of variance.
- Wall-clock time of a laptop with other processes running; individual runs vary (e.g. hard_crud 0.611 / 0.925 / 0.650 s).
- Python start-up and imports are a large, roughly constant share of every run; the apps here are small (4 to 54 SQL sites), so these numbers say little about how the time grows with application size.
- No database is used. The time to apply the Alembic migration or to run the generated functions is not included.
- This is analysis time only. It is not developer time and not a measure of migration effort.
