# H1: review of the 4 HMS sites C3 did not recover (2026-10-15)

Before H1 (HEAD `56ff575`), HMS was 44 / 40 / 40. All 4 sites failed with `UNKNOWN_EXPR`. Each was reviewed against the PHP (the source is not quoted here because HMS has no licence) and against the verified ground truth (`benchmarks/ground truth/hms_kishan0725/queries.json`).

| Site | Shape | Ground truth | Verdict | Why |
|---|---|---|---|---|
| `admin-panel.php:71` | UPDATE `appointmenttb`; `ID` value is `$_GET['ID']` concatenated inside quotes | recoverable | **missed recovery** | The structure is fixed. The only dynamic part is one array element with a literal key in a value position. C3 handled `$var` but not `$array['key']`. |
| `admin-panel.php:86` | SELECT `prestb` JOIN `appointmenttb`, inside a function; values `$pid` and `$_GET['ID']` in the JOIN ON clause | recoverable | **missed recovery** | Same cause (`$_GET['ID']`). |
| `doctor-panel.php:8` | UPDATE `appointmenttb`; `ID` value is `$_GET['ID']` | recoverable | **missed recovery** | Same cause. |
| `include/header.php:35` | SELECT from `users`, value `$_SESSION['id']` | **ambiguous** | recovery is mechanically correct; **schema mismatch** | The SQL text is static. However, `users` and `fullName` are not in `myhmsdb.sql`, no file includes this header, and `$con` is undefined there. It is now recovered and reported as a schema-mismatch site; the evaluator excludes it from precision and recall (ambiguous). |

**Fix (general rule, no app or query IDs):** an array element with a literal key becomes a named value placeholder `<array>_<key>`. This covers `$_GET['ID']`, `$_SESSION["pid"]`, `$row['id']` and `$a[0]`, whether concatenated or inside a double-quoted string as `{$a['k']}`. The leading underscore is dropped, so `$_GET['ID']` becomes `GET_ID`. The record keeps `dynamic_variable_sources` (`GET_ID` → `$_GET['ID']`).
- These still abstain: dynamic keys, nested elements, object properties, and elements in structural positions (the existing position check applies).
- Soundness fix found on the way: single-quoted PHP strings no longer interpolate `$x`.
- The Clinic Q016 template gate now checks the new, more precise representation (`{{GET_department_id}}`). It is still converted, and the result is unchanged. To be covered in the Sunday template audit.

**Result:** HMS is **44 / 44 / 44**. No query changed on the other 8 apps (`regression.txt`). Tests: `tests/test_array_element_recovery.py` (9 tests with independent fixtures, including 4 must-abstain cases).
