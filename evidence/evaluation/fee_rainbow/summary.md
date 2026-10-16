# S15: Fee-Rainbow evaluation against the author-verified ground truth (2026-10-16)

- **Ground truth:** `benchmarks/ground truth/fee_rainbow/queries.json`, author-verified (`AUTHOR_VERIFICATION.md`). It has 58 entries:
  - 54 live sites: 40 recoverable, 12 non-recoverable, 2 ambiguous (G007, G011, excluded from precision and recall);
  - 4 commented-out calls (non-live).
- **Site kinds:** 22 direct `mysqli_query` sites, and 36 `query()` wrapper calls, 32 of them live.
- **Matching:** by (file, line). Evaluator: `c3 evaluate` (`c3.evaluation.site_evaluator`).
- **What was not changed:** pipeline code, ground truth, historical evidence, HMS outputs, signatures and tags. The PHP escape-decoder fix (`\t`) has **not** been applied.
- **Fee-Rainbow is not a fresh holdout.**

## Runs evaluated (labels)

| Run | Code | Label | C3 output | Evidence |
|---|---|---|---|---|
| Historical | `371fddf` (before S1–S14) | **pre-adaptation** | `evidence/preadaptation/fee_rainbow/output/` (frozen). It stopped at stage 3/6 (`Unsupported MySQL type: INT(11)`), so only detection and recovery are evaluated; there is no column analysis or generated code | `evaluation_preadaptation.json` (recovery only, with the frozen `schema.json`) |
| Current | `dc05120` (before the escape-decoder fix) | **post-adaptation** | fresh run into a temp folder | `evaluation.json` |

## Results (54 live sites; 52 scored, the 2 ambiguous excluded)

| Measure | Pre-adaptation | Current |
|---|---|---|
| Detection: direct sites | 22/22 | 22/22 |
| Detection: live `query()` wrapper calls | **0/32** | **0/32** |
| Detection: all live sites | 22/54 | 22/54 |
| Correct recoveries (TP) | 17 | 18 |
| Missed recoveries (FN) | 23 = 1 direct (G015) + 22 undetected wrapper calls | **22 = 0 direct + 22 undetected wrapper calls** |
| Wrong recoveries (FP) | 0 | 0 |
| Extra recoveries | 0 | 0 |
| Recovery precision | 17/17 = 100% | 18/18 = 100% |
| Recovery recall, all recoverable sites | 17/40 = 42.5% | **18/40 = 45.0%** |
| Recovery recall, direct sites only | 17/18 | **18/18** |
| Correct abstentions (non-recoverable sites C3 detected and left unresolved) | 2 of 2 detected (G021, G022) | **2 of 2 detected** (G021, G022) |
| Non-recoverable wrapper calls (10) | not detected; counted TN by the evaluator, but **not** correct abstentions | same |
| Ambiguous G007 / G011 (excluded) | both recovered | both recovered; G011 converted, G007 not (LEFT JOIN) |
| Conversion coverage (generated function), recoverable sites | not measurable (crash) | **18/40**; 18 of the 18 recovered recoverable sites |
| Column use on TP sites (operation / tables / read / write / filter) | not measurable | all fields match on all 18 TP sites: 4 schema-consistent + 14 schema-mismatch |

**Reading the TN count:** the evaluator scores TN = 12 (2 + 10). Only 2 of these are genuine abstentions by C3. The other 10 are wrapper calls C3 never detected, so it made no decision on them. They are reported here separately and are not claimed as correct abstentions.

## Schema mismatches and limitations

- **Missing tables:** the supplied schema (`database/db.sql`) defines only `admin`. `categories`, `occasions`, `products` and `subcategories` are missing.
- **Schema-mismatch sites:** 14 of the 18 TP sites use those tables. They are reported separately; column use matches the ground truth on all 14.
- **Generated functions on those 14 sites** would fail on a database built from the supplied dump, exactly as the PHP does.
- **E2 drift:** the 15 October E2 live run reported 17 query-schema drift site-columns for Fee-Rainbow (`evidence/mysql_runs/2026-10-15_173248/e2/`).

## Exact reasons for failures

1. **22 missed recoverable wrapper calls (current and pre-adaptation): a detection limitation.**
   - **What happens:** C3 detects SQL at the line of the execution call. In Fee-Rainbow, 36 calls pass their SQL to a user-defined helper `function query($query)`, defined in `admin/config/helpers.php:2` and `config/helpers.php:2`. Inside the helper, `mysqli_query($conn, $query)` executes it.
   - **Effect:** C3 detects only the helper's line (G021/G022). It abstains there correctly (`UNKNOWN_EXPR`: "No recoverable assignment chain found", because the SQL is the function parameter). It never visits the 36 call sites where the SQL is actually written.
   - **Not handled today:** cross-file resolution handles SQL *built* by a function and returned (Clinic Q015). It does not handle an executor wrapper called with SQL. Supporting this would be a new general rule (treating a user function that forwards a parameter to an executor as an executor), which is not done here.
2. **G015 `admin/app/models/products.php:165` (pre-adaptation only):** abstained with `BRANCHING` because the SQL sits inside `if ($row)`. It is recovered and converted now, after the general block-containment rule S13.
3. **G007 (ambiguous, excluded):** recovered, but not converted. Its `LEFT JOIN` is outside the converter's supported join shape (`INNER JOIN ... ON` only). It does not affect the metrics.
4. **PHP escape-decoder defect (`\t` not decoded):** **no effect on Fee-Rainbow.** No recovered statement contains an escape sequence (checked on the current output).

## Commands

```powershell
py3 -m c3.cli run --php benchmarks\apps\_app_only\fee_rainbow --schema benchmarks\apps\fee_rainbow\database\db.sql --output $env:TEMP\fr_current
py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\fee_rainbow --output $env:TEMP\fr_current --ground-truth "benchmarks\ground truth\fee_rainbow\queries.json" --report evidence\evaluation\fee_rainbow\evaluation.json
```

The pre-adaptation file was produced with the same evaluator (`evaluate_sites`) on `evidence/preadaptation/fee_rainbow/output/queries.json` and `schema.json`. The column fields were removed because that run never produced column analysis.
