# HMS (kishan0725): C3 data-layer handoff

Generated 2026-10-15 from code `dd15f54` by `scripts/make_handoff.py`. Regenerate rather than edit. **Function signatures are frozen at tag `hms-handoff-v1`.**

## Files

| File | Use |
|---|---|
| `models.py` | SQLAlchemy models (ORM classes for tables with a PK, Core `Table` objects for the rest). |
| `data_access.py` | One function per converted SQL site (below). Imports only SQLAlchemy and the standard library. |
| `unresolved_queries.json` | SQL sites that need manual work. |
| `alembic/` | Initial migration that creates the schema (E3 verified on MySQL 8). |
| `signatures.json` | The frozen function signatures (checked by `tests/test_hms_handoff_signatures.py`). |

## How to call

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import data_access as da

engine = create_engine("mysql+pymysql://user:password@host/myhmsdb")
with Session(engine) as session:
    rows = da.select_appointmenttb_q001(session, doctor=..., appdate=..., apptime=...)
```

- Pass the value the PHP code used, without SQL escaping (binding replaces `mysqli_real_escape_string`).
- Parameters named `<ARRAY>_<key>` come from PHP array elements: `GET_ID` is `$_GET['ID']` and `SESSION_pid` is `$_SESSION['pid']`.
- SELECT functions return `result.mappings().all()`: a list of dict-like rows keyed by the column names shown.
- Write functions commit by default. Use `commit=False` and call `session.commit()` or `session.rollback()` yourself to group several writes in one transaction.

## Summary

- SQL sites: 44; recovered: 44; converted: 44 (general rules: 44, query-specific templates: 0); unresolved: 0.
- Standalone check (C3 blocked in a child process): models.py and data_access.py import; 44 functions called with a recording session; failures: 0.

## Functions

| Function | Parameters (PHP value) | Returns | Commit | PHP site |
|---|---|---|---|---|
| `select_appointmenttb_q001` | `doctor` ← `$doctor`<br>`appdate` ← `$appdate`<br>`apptime` ← `$apptime` | list of row mappings: `apptime` | no (read) | `admin-panel.php:42` |
| `insert_appointmenttb_q002` | `pid` ← `$pid`<br>`fname` ← `$fname`<br>`lname` ← `$lname`<br>`gender` ← `$gender`<br>`email` ← `$email`<br>`contact` ← `$contact`<br>`doctor` ← `$doctor`<br>`docFees` ← `$docFees`<br>`appdate` ← `$appdate`<br>`apptime` ← `$apptime` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `admin-panel.php:45` |
| `update_appointmenttb_q003` | `GET_ID` ← `$_GET['ID']` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `admin-panel.php:71` |
| `select_prestb_q004` | `pid` ← `$pid`<br>`GET_ID` ← `$_GET['ID']` | list of row mappings: `pid`, `ID`, `fname`, `lname`, `doctor`, `appdate`, `apptime`, `disease`, `allergy`, `prescription`, `docFees` | no (read) | `admin-panel.php:86` |
| `select_doctb_q005` | none | list of row mappings: `username`, `spec` | no (read) | `admin-panel.php:144` |
| `select_doctb_q006` | none | list of row mappings: `username`, `spec` | no (read) | `admin-panel.php:313` |
| `select_appointmenttb_q007` | `fname` ← `$fname`<br>`lname` ← `$lname` | list of row mappings: `ID`, `doctor`, `docFees`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `admin-panel.php:473` |
| `select_prestb_q008` | `pid` ← `$pid` | list of row mappings: `doctor`, `ID`, `appdate`, `apptime`, `disease`, `allergy`, `prescription` | no (read) | `admin-panel.php:550` |
| `insert_doctb_q009` | `doctor` ← `$doctor`<br>`dpassword` ← `$dpassword`<br>`demail` ← `$demail`<br>`spec` ← `$spec`<br>`docFees` ← `$docFees` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `admin-panel1.php:15` |
| `delete_doctb_q010` | `demail` ← `$demail` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `admin-panel1.php:27` |
| `select_doctb_q011` | none | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `admin-panel1.php:278` |
| `select_patreg_q012` | none | list of row mappings: `pid`, `fname`, `lname`, `gender`, `email`, `contact`, `password`, `cpassword` | no (read) | `admin-panel1.php:329` |
| `select_prestb_q013` | none | list of row mappings: `doctor`, `pid`, `ID`, `fname`, `lname`, `appdate`, `apptime`, `disease`, `allergy`, `prescription` | no (read) | `admin-panel1.php:385` |
| `select_appointmenttb_q014` | none | list of row mappings: `pid`, `ID`, `fname`, `lname`, `gender`, `email`, `contact`, `doctor`, `docFees`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `admin-panel1.php:458` |
| `select_contact_q015` | none | list of row mappings: `name`, `email`, `contact`, `message` | no (read) | `admin-panel1.php:568` |
| `select_appointmenttb_q016` | `contact` ← `$contact` | list of row mappings: `pid`, `ID`, `fname`, `lname`, `gender`, `email`, `contact`, `doctor`, `docFees`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `appsearch.php:17` |
| `insert_contact_q017` | `name` ← `$name`<br>`email` ← `$email`<br>`contact` ← `$contact`<br>`message` ← `$message` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `contact.php:11` |
| `update_appointmenttb_q018` | `GET_ID` ← `$_GET['ID']` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `doctor-panel.php:8` |
| `select_appointmenttb_q019` | `dname` ← `$dname` | list of row mappings: `pid`, `ID`, `fname`, `lname`, `gender`, `email`, `contact`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `doctor-panel.php:186` |
| `select_prestb_q020` | `doctor` ← `$doctor` | list of row mappings: `pid`, `fname`, `lname`, `ID`, `appdate`, `apptime`, `disease`, `allergy`, `prescription` | no (read) | `doctor-panel.php:280` |
| `select_appointmenttb_q021` | none | list of row mappings: `pid`, `ID`, `fname`, `lname`, `gender`, `email`, `contact`, `doctor`, `docFees`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `doctor-panel.php:332` |
| `select_doctb_q022` | `contact` ← `$contact` | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `doctorsearch.php:16` |
| `select_patreg_q023` | `email` ← `$email`<br>`password` ← `$password` | list of row mappings: `pid`, `fname`, `lname`, `gender`, `email`, `contact`, `password`, `cpassword` | no (read) | `func.php:8` |
| `update_appointmenttb_q024` | `status` ← `$status`<br>`contact` ← `$contact` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func.php:34` |
| `insert_doctb_q025` | `doctor` ← `$doctor`<br>`dpassword` ← `$dpassword`<br>`demail` ← `$demail`<br>`docFees` ← `$docFees` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func.php:62` |
| `select_doctb_q026` | `dname` ← `$dname`<br>`dpass` ← `$dpass` | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `func1.php:8` |
| `select_doctb_q027` | none | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `func1.php:43` |
| `insert_patreg_q028` | `fname` ← `$fname`<br>`lname` ← `$lname`<br>`gender` ← `$gender`<br>`email` ← `$email`<br>`contact` ← `$contact`<br>`password` ← `$password`<br>`cpassword` ← `$cpassword` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func2.php:14` |
| `select_patreg_q029` | none | list of row mappings: `pid`, `fname`, `lname`, `gender`, `email`, `contact`, `password`, `cpassword` | no (read) | `func2.php:26` |
| `update_appointmenttb_q030` | `status` ← `$status`<br>`contact` ← `$contact` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func2.php:41` |
| `insert_doctb_q031` | `name` ← `$name` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func2.php:66` |
| `select_admintb_q032` | `username` ← `$username`<br>`password` ← `$password` | list of row mappings: `username`, `password` | no (read) | `func3.php:8` |
| `update_appointmenttb_q033` | `status` ← `$status`<br>`contact` ← `$contact` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func3.php:24` |
| `select_doctb_q034` | none | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `func3.php:36` |
| `insert_doctb_q035` | `name` ← `$name` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `func3.php:49` |
| `select_users_q036` | `SESSION_id` ← `$_SESSION['id']` | list of row mappings: `fullName` | no (read) | `include/header.php:35` |
| `select_contact_q037` | `contact` ← `$contact` | list of row mappings: `name`, `email`, `contact`, `message` | no (read) | `messearch.php:15` |
| `update_appointmenttb_q038` | `status` ← `$status`<br>`contact` ← `$contact` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `newfunc.php:23` |
| `select_doctb_q039` | none | list of row mappings: `(spec)` | no (read) | `newfunc.php:45` |
| `select_doctb_q040` | none | list of row mappings: `username`, `password`, `email`, `spec`, `docFees` | no (read) | `newfunc.php:57` |
| `insert_doctb_q041` | `username` ← `$username` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `newfunc.php:84` |
| `select_patreg_q042` | `contact` ← `$contact` | list of row mappings: `pid`, `fname`, `lname`, `gender`, `email`, `contact`, `password`, `cpassword` | no (read) | `patientsearch.php:15` |
| `insert_prestb_q043` | `doctor` ← `$doctor`<br>`pid` ← `$pid`<br>`ID` ← `$ID`<br>`fname` ← `$fname`<br>`lname` ← `$lname`<br>`appdate` ← `$appdate`<br>`apptime` ← `$apptime`<br>`disease` ← `$disease`<br>`allergy` ← `$allergy`<br>`prescription` ← `$prescription` | `CursorResult` (use `.rowcount`; `.lastrowid` after INSERT) | commit by default (`commit=False` available) | `prescribe.php:33` |
| `select_appointmenttb_q044` | `contact` ← `$contact`<br>`docname` ← `$docname` | list of row mappings: `pid`, `ID`, `fname`, `lname`, `gender`, `email`, `contact`, `doctor`, `docFees`, `appdate`, `apptime`, `userStatus`, `doctorStatus` | no (read) | `search.php:8` |

## Unresolved sites (manual work)

None: every SQL site was converted.

## Schema drift (SQL uses columns or tables that `myhmsdb.sql` does not define)

These functions are generated as the PHP wrote them. On a database built from the dump they fail exactly as the PHP does. Add the column or table to the database, or do not call them.

| Function site | Problem |
|---|---|
| `func.php:34` (Q024) | column `appointmenttb.payment` (write) not in schema |
| `func2.php:41` (Q030) | column `appointmenttb.payment` (write) not in schema |
| `func2.php:66` (Q031) | column `doctb.name` (write) not in schema |
| `func3.php:24` (Q033) | column `appointmenttb.payment` (write) not in schema |
| `func3.php:49` (Q035) | column `doctb.name` (write) not in schema |
| `include/header.php:35` (Q036) | table `users` not in schema |
| `newfunc.php:23` (Q038) | column `appointmenttb.payment` (write) not in schema |

## Tables without a primary key

`admintb`, `contact`, `doctb`, `prestb` have no PK in the dump and are Core `Table` objects in `models.py` (no key is invented). They do not affect any function above. Options: `evidence/hms/H2_tables_without_pk/proposal.md`.

## Evidence

- Recovery and column use vs the verified ground truth: `evidence/evaluation/hms_kishan0725/summary.md`
- Injection negatives (E4), E1 dry run and E2: `evidence/hms/E_checks/summary.md`
- Alembic on MySQL (E3): `evidence/mysql_runs/2026-10-15_152247/e3/hms_kishan0725.json`
