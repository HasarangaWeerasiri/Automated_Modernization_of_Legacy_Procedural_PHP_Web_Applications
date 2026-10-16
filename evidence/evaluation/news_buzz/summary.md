# S15: NEWS-BUZZ evaluation against the author-verified ground truth (2026-10-16)

- **Ground truth:** `benchmarks/ground truth/news_buzz/queries.json`, author-verified on 16 Oct 2026 (`AUTHOR_VERIFICATION.md`). It has 32 live sites: 30 recoverable and 2 non-recoverable (G002 `admin/editposts.php:17`, G006 `admin/post.php:17`).
- **Matching:** by (file, line). Evaluator: `c3 evaluate` (`c3.evaluation.site_evaluator`).
- **No pipeline rule, ground truth, HMS output, signature or tag was changed.** The historical evidence in `evidence/preadaptation/` is read only.
- **NEWS-BUZZ is not a fresh holdout.** Dignity-Dev is the only one.

## Runs evaluated (labels)

| Run | Code | Label | C3 output evaluated | Evidence |
|---|---|---|---|---|
| Historical | `371fddf` (before S1–S14) | **pre-adaptation** | `evidence/preadaptation/news_buzz/output/` (frozen). The pipeline stopped at stage 3/6 (`Unsupported MySQL type: INT(4)`), so only detection and recovery can be scored; there is no column analysis or generated code | `evaluation_preadaptation.json` (recovery only; column fields deliberately empty) |
| Current | `0ad276b` | **post-adaptation**. The rule changes since then were informed partly by several apps | fresh run into a temp folder | `evaluation.json` |

## Results (32 live sites)

| Measure | Pre-adaptation (historical) | Post-adaptation (current) |
|---|---|---|
| SQL site detection | 32/32 | 32/32 |
| Recovery TP / FN / FP / TN | 4 / 26 / 0 / 2 | **30 / 0 / 0 / 2** |
| Recovery precision | 4/4 = 100% | 30/30 = 100% |
| Recovery recall | 4/30 = 13.3% | 30/30 = 100% |
| Correct abstentions (non-recoverable sites left unresolved) | 2 of 2 (G002, G006) | **2 of 2** (G002, G006) |
| Incorrect recoveries / extra recoveries | 0 / 0 | 0 / 0 |
| Conversion coverage (generated function) | 4 in `converted_queries.json`; no `data_access.py` produced (crash) | **29 of 30** recoverable sites |
| Column use on TP sites: operation / tables / read / write / filter | not measurable (crash before column analysis) | 29/30, 29/30, 30/30, 29/30, 29/30 |
| Schema-mismatch sites | not scored | none |

## Missed or unconverted queries and causes

**Pre-adaptation:** 26 false abstentions, all with reason `BRANCHING`. The SQL is built and executed inside the same conditional block (e.g. `if (isset($_POST[...])) { $sql = ...; mysqli_query(...); }`). The old rule abstained whenever the SQL was built under any control flow. The general block-containment rule S13 fixes this class, so the current run has 0 false abstentions.

**Current, the only defect: G003 `admin/editposts.php:88`.**
- The site is counted as recovered (TP), but the recovered SQL text is **not faithful**.
- The PHP string has a literal TAB character before `status`. The PHP pretty-printer that C3 reads writes it as the escape `\t`. C3's `_decode_php_string` (`sql_recovery.py`) decodes `\r\n`, `\n`, `\r` and `\"`, but **not `\t`**, so the recovered SQL contains a literal backslash-t.
- The conversion stage then fails to parse it ("SQL parse error ... Col: 82"), so the site is left unresolved at conversion (`CONVERSION_UNSUPPORTED`) and no function is generated. The defect is caught before code generation; no incorrect function is emitted.
- Column use for this site is `UNKNOWN`, which explains the single miss in operation, tables, write and filter.
- This is a C3 string-decoding defect (a missing escape), not a ground-truth issue. It is **not fixed here**.

**Correct abstentions (current):** G002 and G006 stay unresolved at recovery (`BRANCHING`). In both, an if/else selects one of two SELECT statements with different WHERE clauses, matching the ground truth's non-recoverable label.

`summary.json` of the current run gives 3 unresolved entries: 2 at recovery (G002, G006) and 1 at conversion (G003).

## Commands

```powershell
py3 -m c3.cli run --php benchmarks\apps\_app_only\news_buzz --schema benchmarks\apps\news_buzz\db\NEWSBUZZ.sql --output $env:TEMP\nb_current
py3 -m c3.cli evaluate --php benchmarks\apps\_app_only\news_buzz --output $env:TEMP\nb_current --ground-truth "benchmarks\ground truth\news_buzz\queries.json" --report evidence\evaluation\news_buzz\evaluation.json
```

The pre-adaptation file was produced with the same evaluator (`evaluate_sites`) on `evidence/preadaptation/news_buzz/output/queries.json` and its `schema.json`. The column-use fields were removed because that run never produced column analysis.
