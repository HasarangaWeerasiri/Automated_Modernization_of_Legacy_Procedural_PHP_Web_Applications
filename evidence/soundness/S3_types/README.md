# S3: schema types never abort the pipeline

| Run | Schema | Before (audit code / pre-adaptation code) | After (this commit) |
|---|---|---|---|
| Audit ENUM/SET probe | `schema_with_enum_set.sql` | crash at stage 3/6: `ValueError: Unsupported MySQL type: ENUM('ACTIVE','BLOCKED')` (values upper-cased) | completes (exit 0); `status = Enum('active', 'blocked')`, `flags = SET('a', 'b')`, case preserved (`after_run_log.txt`) |
| HMS (kishan0725) | `myhmsdb.sql` | crash at stage 3/6: `INT(11)` (`evidence/preadaptation/hms_kishan0725/run_log.txt`) | completes all 6 stages |
| NEWS-BUZZ | `db/NEWSBUZZ.sql` | crash at stage 3/6: `INT(4)` | completes all 6 stages |
| Fee-Rainbow | `database/db.sql` | crash at stage 3/6: `INT(11)` | completes all 6 stages |

Genuinely unsupported types (e.g. `GEOMETRY`) no longer abort the run. The column keeps its MySQL DDL verbatim through a generated `UnmappedMySQLType` and is listed in the new pipeline output `migration_notes.md`.

The remaining blocker for the unseen apps: their generated models still fail `configure_mappers()` because ALTER-style primary keys are not recovered. That is fixed separately in S3b.
