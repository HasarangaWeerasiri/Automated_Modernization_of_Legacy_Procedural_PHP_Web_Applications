# S15: Helpdesk evaluation against the author-verified ground truth (2026-10-16)

- **Ground truth:** `benchmarks/ground truth/helpdesk_legacy_holdout/queries.json`, author-verified on 16 Oct 2026 (`AUTHOR_VERIFICATION.md`). It has 9 live sites, all recoverable: 0 non-recoverable, 0 ambiguous.
- **Matching:** by (file, line). Evaluator: `c3 evaluate` (`c3.evaluation.site_evaluator`).
- **No pipeline rule, ground truth, generated HMS output, signature or tag was changed.**

## Runs evaluated (labels)

| Run | Code | Label | C3 output evaluated | Evidence |
|---|---|---|---|---|
| Historical baseline | tag `c3-helpdesk-baseline-6of9` (`fe35517`) | **pre-adaptation (frozen holdout baseline)** | `output/helpdesk_legacy_holdout/` as committed at the tag (unchanged since), exported from the tag | `evaluation_baseline_6of9.json` |
| Current | `6632dd1` | **post-adaptation.** Rule changes since the baseline (notably S13) were informed partly by Helpdesk's failures, so this is **not** fresh-holdout evidence | fresh run into a temp folder | `evaluation.json` |

## Results (9 live sites)

| Measure | Baseline (pre-adaptation) | Current (post-adaptation) |
|---|---|---|
| SQL detection | 9/9 | 9/9 |
| Recovery TP / FN / FP / TN | 7 / 2 / 0 / 0 | **9 / 0 / 0 / 0** |
| Recovery precision | 7/7 = 100% | 9/9 = 100% |
| Recovery recall | 7/9 = 77.8% | 9/9 = 100% |
| Correct abstentions | n/a: the ground truth has no non-recoverable site | n/a |
| False abstentions | 2: `create.php:13`, `edit.php:10` | 0 |
| False recoveries / extra recoveries | 0 / 0 | 0 / 0 |
| End-to-end conversion coverage (generated function) | **6/9** | **8/9** |
| Column use on TP sites: operation / tables / read / write / filter | 7/7, 7/7, 7/7, 7/7, **4/7** | 9/9 on each field |
| Schema-mismatch sites | none | none |

## Missed queries and causes

| Site | Baseline | Current | Cause |
|---|---|---|---|
| `create.php:13` (G002) | **missed at recovery** (`BRANCHING`) | recovered, converted | The SQL is built and executed inside the same `if` block. The baseline recovery abstained whenever the SQL was built under control flow. Fixed by the general block-containment rule S13 (post-adaptation: S13 was developed from this failure class). |
| `edit.php:10` (G004) | **missed at recovery** (`BRANCHING`) | recovered, converted | Same cause and fix as above. |
| `report.php:5` (G007) | recovered, **not converted** | recovered, **not converted** (the only unresolved entry, conversion stage) | `SELECT ... FROM teams m LEFT JOIN agents ... LEFT JOIN tickets ... GROUP BY ... ORDER BY`. The converter's `_is_supported_join_select` rejects any join with a side (`if side: return False`, `query_converter.py` line 582); only `INNER JOIN ... ON` is supported. This is a missing feature, not an injection-safety limit: the statement has no dynamic values at all. |

**Column-use difference in the baseline (filter 4/7):** `agents.php:5`, `index.php:5` and `report.php:5` had empty filter columns. The baseline counted only `WHERE` columns as filters. The ground truth, and the current analyzer since S11, also count `JOIN ... ON` and `HAVING` columns. So the baseline gap is partly a definition difference and partly a missing role; the current code matches on all 9.

## Commands

```powershell
py3 -m c3.cli run --php benchmarks\apps\helpdesk_legacy_holdout\legacy --schema benchmarks\apps\helpdesk_legacy_holdout\database\schema.sql --output $env:TEMP\hd_current
py3 -m c3.cli evaluate --php benchmarks\apps\helpdesk_legacy_holdout\legacy --output $env:TEMP\hd_current --ground-truth "benchmarks\ground truth\helpdesk_legacy_holdout\queries.json" --report evidence\evaluation\helpdesk_legacy_holdout\evaluation.json
# baseline: export output/helpdesk_legacy_holdout/{queries,column_usage,schema,converted_queries}.json from tag c3-helpdesk-baseline-6of9 into a folder, then run the same evaluate command on it (report evaluation_baseline_6of9.json)
```
