# Blind ground truth: `<app>/queries.json` (draft, pending author verification)

Written by reading the raw PHP and the schema dumps only. C3 code, outputs, evidence, tests, docs, scripts and the
`candidates.csv` files were not opened. Format follows `benchmarks/ground_truth/simple_crud/queries.json`, with extra fields.

## Scope and sampling

Every SQL execution site was annotated (no sampling, including HMS). Sites are lines with `mysqli_query(`,
`->query(`, or a call to a user-defined `query()` wrapper. No app uses `prepare`/`execute`, `multi_query`,
`real_query`, `mysql_query` or PDO. Excluded third-party directories: HMS `TCPDF`, `vendor`, `ckeditor`;
NEWS-BUZZ `GUMP-master`. Helpdesk paths are relative to `legacy/`; the other apps' paths are relative to the app root.

| App | Rows | Live sites | yes | no | ambiguous | Not live |
|---|---|---|---|---|---|---|
| helpdesk_legacy_holdout | 9 | 9 | 9 | 0 | 0 | 0 |
| hms_kishan0725 | 52 | 44 | 43 | 0 | 1 | 8 commented out |
| news_buzz | 32 | 32 | 30 | 2 | 0 | 0 |
| fee_rainbow | 58 | 54 (22 direct + 32 wrapper calls) | 40 | 12 | 2 | 4 commented out |
| dignity_dev_hms | 14 | 13 | 7 | 6 | 0 | 1 empty-SQL dead code |

## Fields

- `site_kind`: `direct_executor` (the `mysqli_query` / `->query` line itself) or `wrapper_call` (Fee-Rainbow
  `query("...")` calls; the SQL is written there and run at `config/helpers.php:4` or `admin/config/helpers.php:4`).
  If you only want direct-executor rows, filter on this field.
- `site_status`: `live`, `commented_out` or `dead_code_empty_sql`. Only `live` rows are real sites.
- `expected_recoverable`: `true` means the complete SQL text is statically determined: values may be interpolated,
  but the structure is fixed. `false` means the structure depends on runtime data (dynamic table or column
  identifier, a branch or ternary that changes clauses, SQL passed in as a parameter), or the row is not a live site.
  `"ambiguous"` means the author decides (see below).
- For `false` and `ambiguous` rows the `expected_*` fields are empty/null. Whatever is statically known goes in
  `static_facts`.
- `dynamic_parts` lists the structural dynamic parts. `notes` gives the reasoning.
- Conventions (as in the CSV README): lower case, bare column names, `*` for `SELECT *`/`t.*`. Filter columns
  include WHERE, JOIN ... ON and HAVING (not GROUP BY or ORDER BY). `COUNT(*)` adds no read column.
  `COUNT(t.id)` adds `id`.

## Ambiguous rows (author to decide)

1. `hms_kishan0725 include/header.php:35`: static SQL. But table `users` is not in `myhmsdb.sql`, no file includes this
   header, and `$con` is undefined there. It looks like a leftover template.
2. `fee_rainbow admin/app/models/categories.php:100` (`searchCategories`): static SQL, but it can never run. The only
   caller passes 2 of the 4 required args, and `escape($conn, $keyword)` passes the mysqli object as the string.
3. `fee_rainbow admin/app/models/occasions.php:92` (`searchOccasion`): static SQL, but the function has no caller and
   would hit the same `escape()` TypeError.

## Judgement calls worth checking

- NEWS-BUZZ `admin/editposts.php:17` and `admin/post.php:17` are `false`. An if/else picks one of two known SELECT
  strings with different WHERE clauses. Both variants are in `static_facts.variants` in case you prefer to treat
  enumerable variants as recoverable.
- HMS `admin-panel.php:313` is `true` and live. It is a `<?php ... ?>` block inside an HTML comment, so PHP still
  runs it.
- Static SQL that references columns or tables missing from the schema is kept as `true`, with the columns copied
  from the SQL text:
  - HMS `UPDATE appointmenttb SET payment` (func.php:34, func2.php:41, func3.php:24, newfunc.php:23).
  - HMS `INSERT INTO doctb(name)` (func2.php:66, func3.php:49).
  - Fee-Rainbow: `database/db.sql` defines only `admin`, so `categories`, `subcategories`, `occasions` and `products`
    are unverifiable against the schema.
- Dignity-Dev `library.php:158` and `:178` use `INSERT ... VALUES` without a column list. Their write columns are all
  table columns in schema order.
- Values interpolated without quoting or casting (for example helpdesk `view.php:6`, NEWS-BUZZ `publicposts.php:16`,
  Fee-Rainbow `auth.php:9`) are still `true`, because the structure is fixed. They are injectable, and this is noted.
