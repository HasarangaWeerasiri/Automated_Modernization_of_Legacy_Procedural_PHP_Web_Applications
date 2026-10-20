# Correction note, 2026-10-20 (later the same day)

This note corrects two statements in `README.md` and `baseline.json` in this folder. Those files are kept exactly as
committed (`adeb1c8`); they were accurate for what was available when they were written.

| Statement in README.md / baseline.json | Status after 2026-10-20 14:00 |
|---|---|
| "The original ZIP was not found on this machine, so its hash was not recorded" (`inputs.zip.sha256` is `null`) | **Superseded.** The original `ams-native-3c4d475.zip` was located outside the repository. SHA-256: `643fde998fa7f6045abeee4ea4ecbfcca9fa35b6bb830aed19a991d3d2680829`. |
| "Licence unknown. Whether the original archive contained a LICENSE file could not be checked." | **Superseded.** The ZIP contains `ams-native/LICENSE.txt`, an MIT License (copyright 2016, the original author). |

## What was verified, and how

- **Files:** read directly from the ZIP, without running anything from it.
- **Source hashes:**
  - The ZIP holds 81 entries, 42 of them `.php` files.
  - The set hash of those 42 files equals this baseline's `php_files.set_sha256` (`372d476f…b088`), computed the same way (`<sha256>  ./ams-native/<path>`, sorted).
  - The schema file `ams-native/database/ams_native.sql` has SHA-256 `c9d9a0f6…e994`, equal to this baseline's schema hash.
- **What this establishes:** the baseline counts were produced from exactly the files in the original archive.

## What does not change

- **Baseline content:** the counts, query IDs and reasons in `baseline.json` are unchanged and still describe the run at `351be5b`.
- **What gets committed:** no SQL text, PHP source or schema content is committed, as before. A permissive licence would allow more, but this folder keeps its counts-only form.
- **Ground truth:** ams-native still has none. Its counts describe what C3 did, not whether it was correct.
- **MySQL evidence:** neither E1 nor MySQL E2/E3 was run, so the MySQL behavioural equivalence of its converted sites is not confirmed.

The same finding is recorded in `evidence/final/2026-10-20/ams_native_post_t2.json` (inputs) and
`evidence/final/2026-10-20/README.md`.
