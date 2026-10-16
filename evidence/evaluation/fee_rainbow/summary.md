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

## Post-wrapper-support evaluation (third stage, 2026-10-16, uncommitted code on top of `a85a1a1`)

**Label: post-adaptation.** Wrapper-call detection was developed after the S15 evaluation above exposed the wrapper limitation on Fee-Rainbow.

**The change.** The extractor now recognises a user-defined function as an SQL wrapper only when ONE parameter reaches ONE direct executor call unmodified, as the whole SQL argument. It abstains, with the reason recorded, for:
- conditional or namespaced definitions;
- definitions repeated with different bodies;
- recursion;
- a modified, by-reference or variadic parameter;
- several executor uses;
- a parameter concatenated into a larger string.

Variable-function and dynamic-method calls are never matched. Each direct call to a wrapper becomes its own SQL site at the call's file and line, with the wrapper's underlying executor, so the **same** recovery and conversion rules apply.

**Wrapper found:** `query($query)` (two identical definitions: `admin/config/helpers.php:2`, `config/helpers.php:2`; executor `mysqli_query`).

**Evidence:** `evaluation_wrapper_support.json`. The earlier `evaluation_preadaptation.json` and `evaluation.json` are unchanged.

| Measure | Post-adaptation (before wrapper support) | **Post-wrapper-support** |
|---|---|---|
| Live sites detected | 22/54 | **54/54** (22 direct + 32 wrapper calls) |
| Recovery TP / FN / FP / TN (2 ambiguous excluded) | 18 / 22 / 0 / 12 | **40 / 0 / 10 / 2** |
| Recovery precision | 18/18 = 100% | **40/50 = 80.0%** |
| Recovery recall | 18/40 = 45.0% | **40/40 = 100%** |
| Correct abstentions (non-recoverable sites detected and left unresolved by recovery) | 2 (G021, G022) | **2** (G021, G022) |
| Wrong recoveries (non-recoverable sites recovered) | 0 | **10** (all 10 dynamic wrapper calls) |
| Extra recoveries | 0 | 0 |
| Recoverable sites converted to a function | 18/40 | **39/40** (18 direct + 21 of 22 wrapper calls) |
| Column use on the 40 TP sites (all fields) | 18/18 | 40/40 (4 schema-consistent + 36 schema-mismatch sites) |

**Expected vs measured:**
- Expected 12/12 correct abstentions and 0 wrong recoveries.
- Measured **2 correct abstentions and 10 wrong recoveries at the recovery stage**.
- No rule was tuned to change this.

**Cause of the 10 wrong recoveries.** The sites are G023–G030, G051 and G052:
- **What happens:** each wrapper call interpolates `$searchQuery`, an SQL fragment (`WHERE ... LIKE ...` or `AND ... LIKE ...`, or the empty string) chosen by a ternary on `$search`. The existing general recovery rule treats any interpolated variable as a value placeholder. It does not inspect what the variable holds, so the statement is marked RECOVERED with `{{searchQuery}}` in a structural position.
- **The same applies to direct sites:** this rule is not specific to wrappers. Direct `mysqli_query` sites with the same shape would be recovered in the same way.
- **Contained at conversion:** all 10 are rejected by the conversion stage ("Dynamic placeholder occurs in an unsupported structural SQL position" or "SQL parse error"). They get unresolved stubs; **no data-access function is generated for any of them**.

**Conversion failure among recoverable wrapper calls (1).** G058 `app/views/products/detail.php:8` is a multi-table `LEFT JOIN` SELECT. The converter supports only `INNER JOIN ... ON`, so this is a missing feature, not a safety limit.

**Query IDs.** Wrapper sites are appended after all direct sites. Fee-Rainbow's 22 direct sites keep Q001–Q022 (byte-identical records), and the 32 wrapper sites are Q023–Q054. No other app's IDs or outputs changed.

**Security report:** injection-safety coverage stays 100% (9/9 user-controlled values in converted queries bound), with 0 generated-code violations.

### Revision: SQL-fragment variable rule (2026-10-16, uncommitted)

**Disclosed finding 1: first measurement.** The first wrapper-support measurement (table above) recovered all 10 dynamic wrapper calls: 10 wrong recoveries, precision 80.0%. The S15 evaluation caught it. Cause: the general recovery rule treats any interpolated variable as a value placeholder.

**The fix (general, conservative only).** When a site would otherwise be RECOVERED, recovery now reads each interpolated variable's assignments before the site in the same file, nearest first, up to the first unconditional one (so both branches of an if/else are seen). If a literal that **is the assigned value** starts an SQL clause, the site becomes UNRESOLVED (`STRUCTURAL`): "Interpolated variable $x supplies SQL text …, not a value".
- **Which literals count as the value:** the whole right-hand side, the alternatives of a ternary, `?:` or `??`, and top-level `.` operands.
- **Clause keywords:** `WHERE`, `AND`, `OR`, `ORDER BY`, `GROUP BY`, `HAVING`, `LIMIT`, `JOIN`, `SET`, ….
- **Never inspected:** literals used as array keys or function arguments.
- **One direction only:** the rule can only turn a recovered site into an unresolved one, never the reverse.

**Disclosed finding 2: first attempt failed the regression gate.**
- **What happened:** the first version scanned every string literal in the assignment. It turned Clinic `19_limit_offset.php:6` (Q019, `LIMIT $limit OFFSET $offset`, label `REVIEW_REQUIRED`, previously recovered and converted correctly) UNRESOLVED.
- **Why:** the array key in `$_GET['limit']` starts with `LIMIT`.
- **Response:** development stopped there, and the rule was corrected once, to inspect only value literals. It was not adjusted further.

**Gate 1 (corrected rule):** all 9 apps re-run against baseline `a85a1a1`. The 8 non-Fee-Rainbow apps are byte-identical (135 files compared), including Clinic Q019 and HMS. The frozen HMS handoff hashes are unchanged.

**Per-site result (wrapper calls).**
- **Recoverable wrapper calls:** all 22 are RECOVERED. 21 are converted; G058 is not (LEFT JOIN, unsupported).
- **The 10 fragment sites:** 6 moved to UNRESOLVED (`STRUCTURAL`, `$searchQuery supplies SQL text`): G023, G024, G025, G026, G029, G030. **4 did not move** and remain RECOVERED: G027 and G028 (`admin/app/models/products.php:17`, `:42`), and G051 and G052 (`app/models/products.php:17`, `:42`).
- **Why the 4 did not move:** their fragment literal begins with a line break and indentation (`"\r\n        WHERE p.name LIKE …"`). The rule matches the clause keyword against the literal as printed in the PHP expression, where the line break is the two-character escape `\r\n`, not whitespace. So `WHERE` is not at the start and the rule does not fire. Decoding the literal with the existing PHP string decoder before matching would cover them; this is **not applied** (pending approval).
- **Contained at conversion:** all 4 are still rejected by the conversion stage; no data-access function is generated for them.

**Measured (corrected rule, current working tree):**

| Measure | First measurement | **Corrected rule** | Expected |
|---|---|---|---|
| Live sites detected | 54/54 | 54/54 | 54/54 |
| Recovery TP / FN / FP / TN (2 ambiguous excluded) | 40 / 0 / 10 / 2 | **40 / 0 / 4 / 8** | 40 / 0 / 0 / 12 |
| Precision / recall | 80.0% / 100% | **40/44 = 90.9% / 100%** | 100% / 100% |
| Correct abstentions | 2 of 12 | **8 of 12** (G021, G022 + 6 fragment sites) | 12 of 12 |
| Wrong recoveries | 10 | **4** (G027, G028, G051, G052) | 0 |
| Extra recoveries | 0 | 0 | 0 |
| Recoverable sites converted | 39/40 | 39/40 (G058 not converted: LEFT JOIN) | – |
| Column use on TP sites (all fields) | 40/40 | 40/40 | – |

**Tests:** 772 passed / 61 skipped (was 760 / 61). The +12 are in `tests/test_sql_fragment_variables.py`:
- a ternary WHERE fragment, unresolved at a direct site and at a wrapper call;
- an if/else-selected fragment, unresolved;
- a `'$id'` value, still recovered and bound;
- the LIMIT/OFFSET regression for Q019;
- request keys named `where`/`order`/`limit`/`and`, which never trigger the rule;
- identifier interpolation, unchanged;
- a dot-concatenated fragment, unresolved;
- the value-literal extraction itself.

The MySQL tests (61) were not run (no database credentials in this session).

### Revision 2: decode value literals before matching (2026-10-16, uncommitted; last iteration on this rule)

**Disclosed finding 3: the first correction reached 6 of 10.** After the first correction, G027, G028 (`admin/app/models/products.php:17`, `:42`), G051 and G052 (`app/models/products.php:17`, `:42`) were still recovered.
- **Cause:** their `WHERE` fragment begins with a line break and indentation. The rule matched the clause keyword against the literal as printed in the PHP expression, where the line break is the escape `\r\n`, so `WHERE` was not at the start.

**Change (approved):** each assigned-value literal is decoded with the existing PHP string decoder (the one used for SQL recovery), and leading whitespace is ignored before the clause-keyword check. Nothing else changed.

**Gate 1:** all 9 apps re-run against baseline `a85a1a1`. The 8 non-Fee-Rainbow apps are byte-identical (135 files compared), Clinic and HMS included. The frozen HMS handoff hashes are unchanged.

**Per site (wrapper calls):**
- **The 10 fragment sites** (G023, G024, G025, G026, G027, G028, G029, G030, G051, G052) are UNRESOLVED (`STRUCTURAL`: "Interpolated variable $searchQuery supplies SQL text …").
- **The 22 recoverable wrapper calls** are RECOVERED. 21 are converted; G058 is not (LEFT JOIN, unsupported by the converter).

**Result now:**

| Measure | First measurement | First correction | **Revision 2 (now)** | Expected |
|---|---|---|---|---|
| Live sites detected | 54/54 | 54/54 | **54/54** | 54/54 |
| Recovery TP / FN / FP / TN (2 ambiguous excluded) | 40 / 0 / 10 / 2 | 40 / 0 / 4 / 8 | **40 / 0 / 0 / 12** | 40 / 0 / 0 / 12 |
| Precision / recall | 80.0% / 100% | 90.9% / 100% | **100% / 100%** | 100% / 100% |
| Correct abstentions | 2 of 12 | 8 of 12 | **12 of 12** | 12 of 12 |
| Wrong recoveries / extra recoveries | 10 / 0 | 4 / 0 | **0 / 0** | 0 / 0 |
| Recoverable sites converted | 39/40 | 39/40 | **39/40** (G058: LEFT JOIN) | – |
| Column use on TP sites (all fields) | 40/40 | 40/40 | **40/40** (4 schema-consistent + 36 schema-mismatch) | – |

**Tests:** 775 passed / 61 skipped (was 772 / 61). The +3 tests:
- a fragment literal starting with `\r\n` and indentation is detected;
- an ordinary value with a leading line break is still recovered;
- leading whitespace alone does not trigger the rule.

The 61 MySQL tests were not run (no database credentials in this session).

**Evidence:** `evaluation_wrapper_support.json` now holds this measurement. The earlier measurements are kept in this text.
