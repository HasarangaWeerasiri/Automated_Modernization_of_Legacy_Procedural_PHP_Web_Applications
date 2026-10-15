# Ground-truth annotation (Helpdesk, HMS, NEWS-BUZZ, Fee-Rainbow, Dignity-Dev)

`<app>/candidates.csv` lists every SQL execution site found by an **independent
grep of the raw PHP** (`scripts/ground_truth/grep_sql_sites.py`). The script
uses its own fixed list of executor calls taken from the PHP manual, and it
never imports or runs C3. The annotation columns are blank. Fill them in **from
the PHP source only**: do not open C3 outputs (`output/`, `evidence/`) while
annotating.

| App | Sites listed | Sampling frame | To annotate (`in_sample=yes`) |
|---|---|---|---|
| helpdesk | 9 | 9 | 9 (all) |
| hms_kishan0725 | 52 (8 on commented-out lines) | 44 | 33 (stratified sample) |
| news_buzz | 32 | 32 | 32 (all) |
| fee_rainbow | 22 | 22 | 22 (all) |
| dignity_dev_hms | 14 | 14 | 14 (all) |

**Sampling.** Only HMS has more than 40 sites, so it is the only app sampled.
- Sites on lines that start inside a comment are listed but kept outside the frame (`stratum=excluded_comment`), because commented-out code never runs.
- The strata are PHP files. Each file gets `max(1, round(30 × file_sites / 44))` sites, chosen with a fixed seed (`20261015`). This produced 33 sites.
- The full list stays in the CSV so the sample can be audited.
- Scope: the third-party directories excluded in `benchmarks/benchmark_manifest.json` (HMS `TCPDF`, `vendor`, `ckeditor`; NEWS-BUZZ `GUMP-master`) are not scanned.

Regenerating is deterministic, but it **overwrites** the CSVs: never rerun it after you start annotating.

## How to fill in a row

Open `file` at `line` in the raw PHP (the gitignored clone under `benchmarks/apps/<app>/`, or `legacy/` for Helpdesk). Annotate only rows with `in_sample=yes`. Leave the other rows blank.

| Column | Values | Rule |
|---|---|---|
| `expected_recoverable` | `yes` / `abstain` / `no` | **yes**: a sound tool can determine the complete SQL text at this call from the source (literals, concatenation or interpolation of values, constant includes). **abstain**: a sound tool should NOT emit SQL here, because the statement's structure depends on runtime data (a table or column name, or an operator from input, a loop or branch that changes the clauses, SQL from an unknown function or file, etc.). **no**: not a real SQL execution site (a grep false hit, unreachable code, or not SQL). |
| `expected_operation` | `SELECT` / `INSERT` / `UPDATE` / `DELETE` / `REPLACE` / `OTHER` | Fill only for `yes`. |
| `expected_tables` | `t1;t2` | Every table the statement touches (FROM, JOIN, INTO, UPDATE, DELETE FROM, subqueries). Use the real table names, not aliases. |
| `expected_read_columns` | `c1;c2` or `*` | Columns in the SELECT list. Write `*` for `SELECT *` (also `t.*`). |
| `expected_write_columns` | `c1;c2` | INSERT column list, or UPDATE SET targets. |
| `expected_filter_columns` | `c1;c2` | Columns in WHERE, JOIN … ON and HAVING. |
| `annotator_notes` | free text | Why a row is `abstain` or `no`, and anything uncertain. Put it in quotes if it contains a comma. |

**Conventions**
- Use lower case.
- Use bare column names, without the table prefix or alias (`p.name` → `name`).
- Separate items with `;` and sort them in any order; the evaluator compares them as sets.
- Write `-` for a list that is genuinely empty (e.g. an INSERT has no read columns).
- A blank cell means "not annotated".
- For `abstain` and `no` rows, leave the operation, tables and column fields blank.
- If you are unsure about a whole row, leave `expected_recoverable` blank and write a note. The row is then reported as unannotated, not guessed.
- Don't edit `site_id`, `file`, `line`, `call`, `in_sample` or `stratum`.
- Save the file as UTF-8 CSV. In Excel, use "CSV UTF-8 (Comma delimited)".

## How the evaluator reads the file (S15)

1. **Rows evaluated.** It reads `candidates.csv` with Python's `csv` module (UTF-8, header names as above). It keeps rows with `in_sample=yes` and a non-blank `expected_recoverable`. Blank rows are counted as `unannotated`.
2. **Matching to C3.** It joins each row to C3's `queries.json` record by **(`file`, `line`)**. C3 stores absolute paths, so the evaluator first makes each one relative to the `--php` root (`/` separators, case-insensitive). The PHP roots are the same files: the `_app_only` copies are byte copies. Checked on 2026-10-15: NEWS-BUZZ 32/32 and HMS 44/44 grep sites join to exactly one C3 record. Query IDs are not used, because they are positional.
   - `yes` or `abstain` with no C3 record is a **detection miss**.
   - A C3 record that matches no `in_sample` row is reported separately (no metric).
3. **Recovery decision.** C3 status `RECOVERED` counts as "recovered", anything else as "abstained".
   - `yes`: recovered → TP, abstained → FN (false abstention).
   - `abstain`: abstained → TN, recovered → FP (**false recovery**: the unsound case).
   - `no`: excluded from precision and recall, and counted as a grep false positive.
4. **Column use.** Only for `yes` rows that C3 recovered. It compares operation (exact match) and tables, read, write and filter columns (set equality, with `-` = empty) against C3's `column_usage.json`. The result is accuracy per field.
5. **Output.** `evidence/evaluation/<app>/evaluation.json`, which includes the sampling statement from `candidates_summary.json`. For HMS, the metrics are labelled "stratified sample (33/44)".
