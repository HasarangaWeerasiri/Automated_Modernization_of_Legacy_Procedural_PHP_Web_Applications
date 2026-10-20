# ams-native: pre-improvement baseline (2026-10-20)

Counts-only C3 baseline for one unseen application, ams-native-3c4d475, taken before the structural-position
improvement sprint. It is the "before" side of the ams-native table in `docs/PP1_readiness_2026-10-19.md`, section 21;
the "after" side is `evidence/final/2026-10-21/ams_native_post_t1.json`.

## What this is

- **Pipeline version:** commit `351be5b` (tag `c3-pre-improve-20261020`), before `17eebc9` and `d263cbb`.
- **Origin of the inputs:** a temporary Try It upload in the C3 Migration Studio (run `80f803de0d01419f`,
  2026-10-20 11:30). That run kept only the extracted `.php` files (42; 28 non-PHP files were skipped) and the
  uploaded schema bytes.
- **How the counts were produced:** a CLI run (`python -m c3.cli run`) at `351be5b` on exactly those `.php` files and
  schema bytes, written to a temporary folder outside the repository. After path normalisation its outputs equal the
  Try It outputs; only the docstring labels differ (the Try It labels carry an extra `ams-native/` prefix).

## What this is not

- **Not a reproducible independent holdout.** The original ZIP was not found on this machine, so its hash was not
  recorded (`inputs.zip.sha256` is `null` in `baseline.json`). The inputs can be identified only by the schema hash and
  the per-file `.php` hashes recorded here.
- **Licence unknown.** Whether the original archive contained a LICENSE file could not be checked. For that reason no
  SQL text, PHP source, schema content or generated code is stored here or anywhere in the repository.
- **No ground truth.** No recovery or conversion accuracy can be computed for this application. The counts describe
  what C3 did, not whether it was correct.
- **No MySQL evidence.** Neither E1 behavioural equivalence nor MySQL E2/E3 was run for ams-native.

## Files

| File | Contents |
|---|---|
| `baseline.json` | Inputs (schema SHA-256, `.php` set hash, ZIP hash `null`), counts, reason codes by stage, the 13 unresolved sites (query ID, `file:line`, stage, reason code, reason text), security-report summary. |
| `php_files.sha256` | One `sha256  ./ams-native/<path>` line per `.php` file (42 lines). File names and hashes only. |

## Baseline counts (commit `351be5b`)

| Measure | Value |
|---|---|
| Detected SQL sites | 112 |
| Recovered | 108 |
| Converted (all by general rules) | 99 |
| Unresolved: recovery / conversion | 13: 4 / 9 |
| Legacy injection sites remediated | 42 of 50 |
| Generated-code violations | 0 |

- **Unresolved at conversion** (`CONVERSION_UNSUPPORTED`, placeholder in an unsupported position): Q006, Q010, Q039,
  Q042, Q063, Q070, Q076, Q077, Q097.
- **Unresolved at recovery:** Q060 and Q062 (`UNSUPPORTED_SHAPE`), Q061 (`STRUCTURAL`, dynamic table identifier),
  Q064 (`LOOP_OR_SWITCH_CONTROL_FLOW`).

After `d263cbb` the nine conversion-stage sites are converted and the four recovery-stage sites stay unresolved (see
the post-sprint record). Those new conversions have unit-test, E4-style and byte-gate evidence only; their MySQL
behavioural equivalence is not confirmed.
