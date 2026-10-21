# Labelling the concern corpus (200 statements)

File: `docs/eval/concern_labelling_sheet.csv` (100 HMS, 70 WackoPicko, 30 crud-php-complete).

- **Seed:** 329. The rows are drawn at random, stratified by file and statement kind.
- **Our labels are not in the sheet,** so they cannot bias you.

## What to fill

| Column | Value |
|---|---|
| `gold` | `presentation`, `business_logic`, `data_access`, `mixed`, or `skip` (cannot judge / not real code) |
| `unsure` | `x` if the case was hard |
| `notes` | anything worth discussing |

Label the statement on its own role in the page. Open the file at `line` when the `code` column is not enough.

## Classes

- **presentation**: produces output, or decides only what is shown.
  - Examples: `echo`, inline HTML, a branch whose bodies only echo, a loop that only renders rows, a variable copied only to be echoed.
- **data_access**: talks to the database.
  - Examples: connect, build the SQL string, run the query, fetch a row, row counts / insert ids / escaping.
- **business_logic**: decides or computes.
  - Examples: reading request input into variables, session/cookie/redirect/exit, a branch or loop whose body runs a query or changes state, calculations, include, return.
- **mixed**: one statement that clearly does two of the above, e.g. `echo mysqli_num_rows(mysqli_query(...))`, or a call to a helper that both queries and prints.

## Rules of thumb

- A **branch** (`if`/`switch`/loop) is labelled by its **bodies**, not its condition. This is the master-reference step 7 rule: query or state change inside → business_logic; only output → presentation.
- Do not label by file name or habit. Label what this statement does.
- Two people should label the same sheet independently. Report agreement (Cohen's kappa) and resolve disagreements before scoring.

## Score

```
PYTHONPATH=src python -m c1.evaluate score docs/eval/concern_labelling_sheet.csv \
  --labels hms=<hms analysis-out> --labels wackopicko=<wacko analysis-out> \
  --labels crud-php-complete=<crud analysis-out> --json docs/eval/concern_scores.json
```

The analysis folders must come from the same versions of the apps (pins in `docs/BENCHMARKS.md`); node ids depend on them.
