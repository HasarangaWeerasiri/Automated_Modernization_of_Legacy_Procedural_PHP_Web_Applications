# Why Dignity-Dev G007–G009 (implicit comma joins) are not converted (2026-10-16)

Analysis only. **No rule was changed.**

## The sites

| GT | C3 | Site | Shape |
|---|---|---|---|
| G007 | Q007 | `library.php:219` | `SELECT ... FROM patient_info, appointments WHERE <join predicate> AND <value filter>` |
| G008 | Q008 | `library.php:226` | `SELECT ... FROM patient_info, appointments WHERE <join predicate>` |
| G009 | Q009 | `library.php:233` | `SELECT ... FROM patient_info, appointments WHERE <join predicate> AND appointment_no = <parameter>` |

All three are **RECOVERED** correctly at the recovery stage (GT TP). They become UNRESOLVED only at the **conversion** stage, with the reason "Unsupported multi-table SQL structure. Only static SELECT INNER JOINs are supported."

## Which check rejects them

`components/c3_data_layer/src/c3/conversion/query_converter.py`:

1. `_convert_query` (around line 364) finds 2 tables. A multi-table statement is converted only if `_is_supported_join_select(tree)` is true. Otherwise (the q010 subquery template does not apply) the query is UNRESOLVED.
2. In `_is_supported_join_select` (line 542), the statement passes every check except the last:
   - it is a SELECT with no subquery;
   - the FROM clause is a plain table;
   - the joined item is a plain table;
   - the join has no `kind`/`side` (a comma join, which sqlglot parses as a `Join` with neither).
3. **The rejecting check is at line 586:** `if join.args.get("on") is None: return False`, with the comment "A JOIN without an ON condition is unsupported." In a comma join the join predicate sits in `WHERE`, so there is no `ON`.

Measured on the current recovered SQL (sqlglot):
- join `kind` = `side` = `on` = None for all three;
- each `WHERE` contains a column = column join predicate;
- `_has_unsafe_placeholder_position` = **False** for all three: every dynamic value is in a scalar value position.

## Safety limit or missing feature?

**A missing feature: a deliberately conservative scope, not an injection-safety limit.**
- **Injection safety is checked separately** (the placeholder-position check) and passes for G007–G009. The generated code would be the same `text()` statement with bound values, so it would not be less safe than the converted INNER JOIN sites.
- **The `ON` rule** limits the general converter to the join shape it was written and tested for (`INNER JOIN ... ON`). Its purpose is to avoid emitting multi-table statements whose join condition was not explicitly identified, e.g. an accidental Cartesian product.
- **Semantics:** a comma join with the join predicate in `WHERE` is equivalent to an inner join. Supporting it means recognising that shape (or any comma join, since `text()` keeps the SQL verbatim), plus tests and an E1 check. That would be a new general rule, and it is **not** done here.

Applied to Dignity-Dev, the fresh holdout, any such rule would make Dignity-Dev post-adaptation for this shape.

## Do other benchmark apps use implicit joins?

Every recovered statement of the current outputs of all 9 apps was scanned for a `Join` with no `ON`, no `USING` and no kind or side. A raw-source grep for `FROM <table>, <table>` in SELECTs was run as a cross-check.

| App | Implicit (comma) joins | Handling |
|---|---|---|
| HMS (kishan0725) | 0 | - (its one multi-table query, `admin-panel.php:86`, is an explicit `INNER JOIN ... ON`; converted) |
| Clinic | 0 | - |
| Helpdesk | 0 | - |
| NEWS-BUZZ | 0 | - |
| Fee-Rainbow | 0 | - |
| simple_crud, mixed_crud, hard_crud | 0 | - |
| Dignity-Dev | **3** (G007, G008, G009) | recovered; conversion UNRESOLVED (`CONVERSION_UNSUPPORTED`) with an unresolved stub |

Limits of the scan:
- It covers SQL that C3 recovered.
- Sites left unresolved at recovery have no parseable statement and are covered only by the raw grep. The grep found only the 3 Dignity-Dev lines.
