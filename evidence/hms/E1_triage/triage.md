# HMS E1 triage: run `evidence/mysql_runs/2026-10-15_181758/e1/` (HEAD `eea6a6e`)

Result: 44 sites: **27 PASS, 5 FAIL, 12 INCONCLUSIVE**. Every non-PASS case was checked against the case data in the report. The legacy SQL was rebuilt locally in build mode (not committed: no HMS licence), and the column definitions in `myhmsdb.sql` were checked. The MySQL server runs in its default strict mode (STRICT_TRANS_TABLES).

Categories:
- **(a)** generated function wrong
- **(b)** harness or input value wrong
- **(c)** real PHP-vs-SQLAlchemy behaviour difference, or a legacy-app failure that cannot be removed
- **(d)** schema drift (only the 7 known site-columns)

## The 5 FAILs: all (b), from the harness's verdict ordering

In every FAIL, the failing case is `hostile_quote` and the other cases are not FAIL. The HMS PHP does not escape these inputs, so `' OR '1'='1` changes the **structure** of the legacy SQL:

| Site | Legacy SQL after the payload (shape) | Legacy result | Generated result |
|---|---|---|---|
| `doctor-panel.php:8` (H1 `GET_ID`) | `... WHERE ID = '' OR '1'='1'` | **executes and updates every row** | the bound string `' OR '1'='1` compared with INT `ID` in an UPDATE → MySQL strict error 1292; nothing changed |
| `admin-panel.php:71` (H1 `GET_ID`) | same | same | same |
| `contact.php:11` | `VALUES('' OR '1'='1', ...)`: each value becomes the expression `'' OR '1'='1'` = 1 | executes (inserts `1`s) | the 11-character string into `contact varchar(10)` → error 1406 (data too long); nothing inserted |
| `func2.php:14` | same, into `patreg` | executes | `gender varchar(10)` → error 1406 |
| `admin-panel1.php:15` | same, into `doctb` | executes | the string into INT `docFees` → error 1366 |

The generated code is right: the payload stays a value and MySQL rejects it. `normal` and `empty_result` PASS at all 5 sites, and the `GET_ID` binding works. The FAIL came from `compare.py`, which returned FAIL on "generated side raised" **before** checking whether the hostile payload had changed the legacy SQL. In the legacy-error branch, the same situation was already classified as `LEGACY_INJECTABLE`.

## Second harness defect found (b): LEGACY_INJECTABLE was over-claimed

`LEGACY_INJECTABLE` was granted when the payload text appeared verbatim in the legacy SQL and the results differed or the legacy SQL failed. That is too weak. In `contact.php:11 hostile_stacked`, the payload sits inside a quoted literal (`VALUES('1; DROP TABLE ...;-- ', ...)`), so the structure is unchanged, and both sides fail with "data too long". In `admin-panel.php:42 hostile_wildcard` (`appdate='%_'`), both sides fail on the invalid DATE. Neither is an injection. The label must require evidence that the payload changed the SQL **structure**.

## The 12 INCONCLUSIVEs

| Site | Cause | Category |
|---|---|---|
| `func.php:34`, `func2.php:41`, `func3.php:24`, `newfunc.php:23` | `appointmenttb.payment` does not exist (MySQL 1054) | **(d)** |
| `func2.php:66`, `func3.php:49` | `doctb.name` does not exist (1054) | **(d)** |
| `include/header.php:35` | table `users` does not exist (1146) | **(d)** |
| `func.php:62` | The legacy INSERT omits `spec`, which is `NOT NULL` with no default. Under strict MySQL even `normal` fails (1364) on both sides, because the generated SQL is the same statement. | **(c)**: a legacy-app defect that shows under strict mode. It was not in the 7 known drift columns (the columns exist; a required value is missing). |
| `newfunc.php:84` | The legacy INSERT gives only `username`; `password` is `NOT NULL` with no default (1364). Same as above. | **(c)** |
| `admin-panel.php:42` | `normal` and `empty_result` PASS. Only `type_mismatch` (`'abc'` for the DATE/TIME inputs) is INCONCLUSIVE: both sides are rejected by MySQL (1525). | **(c)**: invalid input rejected by both sides; nothing to compare |
| `admin-panel.php:45` | `normal` and `empty_result` PASS. `type_mismatch`: the PHP interpolates `pid` **unquoted**, so `abc` becomes a column name (1054); the generated side binds `'abc'` and fails on the INT column. Both are rejected, with different errors. | **(c)** |
| `prescribe.php:33` | `normal` and `empty_result` PASS. `type_mismatch`: `'abc'` into INT/DATE columns, rejected by both sides (1366). | **(c)** |

The FAIL sites above also have `type_mismatch` INCONCLUSIVE cases of the same both-rejected kind. After the FAIL fix, those sites become INCONCLUSIVE, not PASS, because the site rule stays strict (PASS only if every case is PASS or LEGACY_INJECTABLE).

**No (a) cases: no generated function is wrong.** No input value you supplied is wrong. The `type_mismatch` and `hostile_*` values are the pre-filled plan values.

## Fixes (harness only, no comparison rule weakened)

1. `compare.py`: a hostile case where the generated side raised is `LEGACY_INJECTABLE` only if (i) the payload changed the legacy SQL structure and (ii) the generated side left the database unchanged. Otherwise it stays FAIL.
2. `compare.py` and `run_e1.py`: "payload changed the structure" is decided by building the legacy SQL a second time with a benign value in place of the payload. The two token streams are compared, with literal contents ignored. This replaces the verbatim-text test and makes the label stricter.
3. Both-rejected cases stay INCONCLUSIVE. The report now records both MySQL error codes (numbers only, safe to keep for HMS) and whether they are equal, so that "both sides rejected the same invalid input" is visible.
