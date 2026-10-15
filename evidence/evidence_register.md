# C3 Evidence Register

Every number used in slides or reports must appear here, with the command that produced it and the saved raw output.
Commands run from `components/c3_data_layer` with `PYTHONPATH=src` and the project venv unless stated otherwise.

| # | Date | Claim | Command | Result | Raw output file | Commit |
|---|---|---|---|---|---|---|
| 1 | 2026-10-08 | MySQL 8.0 behavioural validation, simple/mixed/hard CRUD | `tests/mysql_validation/run_*.php` + `run_*_migrated.py` + `compare_*.py` (run by the student) | 18/18 checks (5 + 7 + 6) | `evidence/mysql_2026-10-08/*.json` | `8e0a842` |
| 2 | 2026-10-13 | Helpdesk holdout pre-adaptation baseline | `python -m c3.cli run --php ../../benchmarks/apps/helpdesk_legacy_holdout/legacy --schema ../../benchmarks/apps/helpdesk_legacy_holdout/database/schema.sql --output ../../output/helpdesk_legacy_holdout` | total 9, recovered 7, converted 6, unresolved 2, conversion-unresolved 3; reproduced byte-identical on 2026-10-15 (audit) | `output/helpdesk_legacy_holdout/summary.json` | `fe35517`, tag `c3-helpdesk-baseline-6of9` |
| 3 | 2026-10-15 | Test suite before PP1 development (no MySQL credentials) | `python -m pytest -q -rs -p no:cacheprovider` | 514 passed, 61 skipped (all MySQL), 0 failed, 575 collected | `evidence/test_runs/2026-10-15_pre_dev_pytest.txt` | (this commit) |
