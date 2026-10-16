# S15: Dignity-Dev evaluation against the author-verified ground truth (2026-10-16)

- **Ground truth:** `benchmarks/ground truth/dignity_dev_hms/queries.json`, author-verified on 16 Oct 2026 (`AUTHOR_VERIFICATION.md`). It has 14 calls; 13 are live sites (7 recoverable, 6 non-recoverable). G010 (`library.php:249`, empty SQL) is excluded from the metrics.
- **Matching:** by (file, line). Evaluator: `c3 evaluate` (`c3.evaluation.site_evaluator`).
- **No pipeline rule or generated code was changed** for this evaluation or for Dignity-Dev at any time.

## Runs evaluated (labels)

| Run | Code | Label | Evidence |
|---|---|---|---|
| First run on Dignity-Dev (frozen output) | `cd046ba` (post-S13 rules) | **fresh holdout, first run.** No earlier run exists, so there is **no pre-adaptation measurement** for this app and none was reconstructed | `evidence/fresh_holdout/dignity_dev_hms/output/`, `evaluation_first_holdout_run.json` |
| Current code | `d5b9b42` (after S14, H1, S11, S7/S8; none of them made for Dignity-Dev) | **fresh holdout, current rules.** Still not adapted to Dignity-Dev. Its `converted_queries.json` is identical to the first run apart from paths | temp output, `evaluation.json` |

Both runs give **identical** site-level results.

## Results (13 live sites)

| Metric | Result |
|---|---|
| Live sites detected | 13/13 |
| Recovery TP / FN / FP / TN | **7 / 0 / 0 / 6** |
| Recovery precision / recall | 7/7 = 100% / 7/7 = 100% |
| Extra recoveries (non-recoverable sites recovered) | **none** |
| Conversion of the 7 recoverable sites | 4 converted (G004, G005, G011, G012); **3 not converted** (G007, G008, G009) |
| Column use on the 7 TP sites | operation 7/7, tables 7/7, read 7/7, filter 7/7, **write 5/7** |
| Schema-mismatch sites | none found (all tables and columns named by the ground truth exist in `127_0_0_1.sql`, case-insensitively) |
| Non-live site reported by C3 | G010 `library.php:249` (empty SQL): detected and left unresolved (`UNSUPPORTED_SHAPE`), not recovered. The evaluator lists it under `false_detections_commented_out`, a generic bucket for non-live sites |

## The 10 unresolved entries (`unresolved_queries.json`)

| Entry | Site | Stage / reason code | Ground truth | Verdict |
|---|---|---|---|---|
| Q001 | `library.php:31` (G001) | recovery / STRUCTURAL | non-recoverable (dynamic table) | **correct abstention** |
| Q002 | `library.php:53` (G002) | recovery / STRUCTURAL | non-recoverable (dynamic table) | **correct abstention** |
| Q003 | `library.php:96` (G003) | recovery / LOOP_OR_SWITCH_CONTROL_FLOW | non-recoverable (switch, dynamic table) | **correct abstention** |
| Q006 | `library.php:203` (G006) | recovery / BRANCHING | non-recoverable (if/else, dynamic column) | **correct abstention** |
| Q013 | `library.php:313` (G013) | recovery / STRUCTURAL | non-recoverable (dynamic table) | **correct abstention** |
| Q014 | `library.php:320` (G014) | recovery / STRUCTURAL | non-recoverable (dynamic table) | **correct abstention** |
| Q010 | `library.php:249` (G010) | recovery / UNSUPPORTED_SHAPE | excluded (empty SQL, not live) | correct (no SQL to recover); outside the metrics |
| Q007 | `library.php:219` (G007) | **conversion** / CONVERSION_UNSUPPORTED (implicit comma join) | recoverable | **false abstention at conversion** (SQL recovered correctly; no data-access function generated) |
| Q008 | `library.php:226` (G008) | **conversion** / CONVERSION_UNSUPPORTED (implicit comma join) | recoverable | **false abstention at conversion** |
| Q009 | `library.php:233` (G009) | **conversion** / CONVERSION_UNSUPPORTED (implicit comma join) | recoverable | **false abstention at conversion** |

**Summary of the 10 entries.**
- **Recovery stage:** 0 false abstentions; all 6 non-recoverable live sites are correct abstentions, and G010 is outside the metrics.
- **Conversion stage:** 3 false abstentions, all on recoverable sites. The general converter supports only explicit `INNER JOIN` multi-table SELECTs, so it abstained safely on the implicit comma joins (`FROM a, b WHERE a.x = b.x`). End to end, 4 of the 7 recoverable sites get a generated function.

`summary.json` reports `unresolved_queries: 7`. That is the legacy recovery-stage count; the report itself has 10 entries, 7 from recovery and 3 from conversion.

## Column-usage issues (reported separately)

| Site | Issue |
|---|---|
| G004 `library.php:158` | `INSERT INTO patient_info VALUES (...)` without a column list: ground truth = all 6 table columns in schema order; C3 write columns = `[]` |
| G005 `library.php:178` | `INSERT INTO appointments VALUES (...)` without a column list: ground truth = all 7 columns; C3 write columns = `[]` |

C3 does not expand a column-less INSERT to the table's columns. This is a known FR4 gap; no rule was changed in this evaluation.

## Commands

```powershell
py3 -m c3.cli run --php benchmarks\apps\_app_only\dignity_dev_hms --schema benchmarks\apps\dignity_dev_hms\127_0_0_1.sql --output $env:TEMP\dd_s15
py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\dignity_dev_hms --output $env:TEMP\dd_s15 --ground-truth "benchmarks\ground truth\dignity_dev_hms\queries.json" --report evidence\evaluation\dignity_dev_hms\evaluation.json
py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\dignity_dev_hms --output evidence\fresh_holdout\dignity_dev_hms\output --ground-truth "benchmarks\ground truth\dignity_dev_hms\queries.json" --report evidence\evaluation\dignity_dev_hms\evaluation_first_holdout_run.json
```
