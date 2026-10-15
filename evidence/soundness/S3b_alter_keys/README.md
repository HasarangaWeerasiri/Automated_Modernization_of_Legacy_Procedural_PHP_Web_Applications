# S3b: ALTER TABLE keys and tables without a primary key (pulled forward from S9)

All three unseen apps declare keys phpMyAdmin-style (`ALTER TABLE … ADD PRIMARY KEY`, `MODIFY … AUTO_INCREMENT`). Before this fix, `schema.json` had 0 primary-key columns for all of them, and after S3 their models failed `configure_mappers()` ("could not assemble any primary key columns").

The schema reader now applies `ADD PRIMARY KEY` (single and composite), `MODIFY`/`CHANGE` (type, nullability, AUTO_INCREMENT, default) and `ADD [CONSTRAINT] FOREIGN KEY`. Unique keys and indexes are still not recovered; that remains part of S9.

Tables that genuinely have no primary key (4 of 6 in HMS: `admintb`, `contact`, `doctb`, `prestb`) are emitted as SQLAlchemy Core `Table` objects. A primary key is never invented. No relationships are generated for them, and they are listed in `migration_notes.md`.

Result: `configure_mappers()` passes on all 8 apps (`models_check_output.txt`). Per-query results are unchanged. On the 5 original benchmarks `schema.json` is identical, and `models.py` only gains the `Column`, `Table` imports.
