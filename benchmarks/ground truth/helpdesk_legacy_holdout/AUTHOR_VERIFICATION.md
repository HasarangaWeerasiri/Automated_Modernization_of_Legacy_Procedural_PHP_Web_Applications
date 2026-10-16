# Helpdesk ground truth: author verification

- **Date:** 16 October 2026
- **Verifier:** the author (single human verifier)
- **File verified:** `benchmarks/ground truth/helpdesk_legacy_holdout/queries.json`

## 1. Scope

The author reviewed all 9 Helpdesk SQL sites (G001–G009) by hand against their original PHP code. All 9 sites:
- match on file name and line number;
- match the PHP on operation, tables and column-usage labels;
- are recoverable under the same conservative full-site criterion used in the Dignity-Dev note: a single fixed statement, with values coming only from parameters.

No ground-truth label changes are required.

## 2. Search

**Searched:** all `.php` files under `benchmarks/apps/helpdesk_legacy_holdout`.

**Patterns used:**
- `mysqli_query(`
- `->query(`
- `->prepare(`
- `->execute(`
- `mysqli_prepare(`
- `multi_query(`
- `real_query(`
- `->exec(`

**Result:** exactly 9 `mysqli_query` calls, and no other matches:
- `agents.php:5`
- `create.php:13`
- `delete.php:8`
- `edit.php:10`
- `edit.php:15`
- `index.php:5`
- `report.php:5`
- `search.php:6`
- `view.php:6`

**Extension check:** the author searched the whole Helpdesk project with PowerShell for `.inc`, `.phtml`, `.html` and `.htm` files. Result: **0 files found**.

## 3. Limits of the search

The search covers the listed patterns in `.php` files only. It does not prove the absence of every possible custom database wrapper, or of SQL executed through some other mechanism.

## 4. Status of Helpdesk

Helpdesk was the original holdout. Its pre-adaptation baseline is frozen: tag `c3-helpdesk-baseline-6of9`, 6 of 9.

Rule changes since then were informed partly by its failures, so current Helpdesk results are **post-adaptation** and are not fresh-holdout evidence. **Dignity-Dev is the only fresh holdout.**

## 5. Limitations

- **How the ground truth was made:** it was drafted blind by an LLM and verified by a single human.
- **Same model family:** the drafting model is from the same model family as the assistant used during development, so they may share blind spots.
