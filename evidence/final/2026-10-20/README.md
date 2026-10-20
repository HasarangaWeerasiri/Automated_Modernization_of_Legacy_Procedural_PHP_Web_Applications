# FR6 join sprint, Part 1 evidence (2026-10-20)

Evidence for the LEFT JOIN / comma-join result-shape rule (commits `cba45fc` and `4d62a90`), recorded at HEAD
`4d62a90`, before the MySQL run (Part 2). The pre-sprint reference is the local tag `c3-pre-joins-20261020` on
`adeb1c8`. Counts, query IDs, function names and hashes only: no SQL text, no PHP source.

This folder is dated 2026-10-20, the day its files were produced. It is separate from `evidence/final/2026-10-21/`
(the earlier value-position sprint, also produced on 2026-10-20), which is not modified.

## Contents

| Path | What it shows |
|---|---|
| `test_run_non_mysql.txt` | Full non-MySQL suite at `4d62a90`: 990 passed, 74 skipped (the skips are the tests that need MySQL, now 13 more for the join rule). |
| `gate/gate_result.txt` | Site-scoped 9-app gate: every difference against `adeb1c8`, per query ID and per function, each marked allowed. Frozen HMS signatures hash match. |
| `gate/allowed_changes.json` | The approved allowed-changes list used by the gate. |
| `gate/prejoins.sha256.json`, `gate/t2_final.sha256.json` | Per-file hashes of the two runs (run-folder path normalised). |
| `s15/` | S15 recovery evaluation of the five ground-truth applications on the `4d62a90` outputs; each file is byte-identical to `evidence/final/2026-10-21/s15/`. |
| `conversion_coverage_post_t2.json` | Post-T2 conversion coverage of ground-truth recoverable sites, before and after, with the newly converted IDs. |
| `e4/injection_report.json`, `e4/new_join_functions.json` | E4 (12 payloads, recording session, no database) on all nine applications, and the four new join functions. |
| `ams_native_post_t2.json` | ams-native re-run at `adeb1c8` and `4d62a90`: identical outputs. |

## Results

- **Changed sites (all allowed):**
  - Dignity-Dev Q007, Q008, Q009 (implicit comma joins) and Helpdesk Q007 (two LEFT JOINs with COUNT, GROUP BY and ORDER BY) are now converted.
  - Fee-Rainbow Q007 and Q054 still abstain with the same reason code and stage. Only their reason text changed, because the old text ("Only static SELECT INNER JOINs are supported") is no longer true. Their tables are not in the parsed schema.
  - Every other file of the nine applications is byte-identical, HMS included.
- **Conversion coverage of ground-truth recoverable sites:**
  - Dignity-Dev: 4 of 7 → 7 of 7.
  - Helpdesk: 8 of 9 → 9 of 9.
  - Fee-Rainbow: 39 of 40, unchanged (Q054 abstains).
  - HMS and NEWS-BUZZ: unchanged.
  - This is conversion coverage, not recovery accuracy and not behavioural equivalence.
- **Recovery (S15):** unchanged for all five ground-truth applications.
- **E4:** 163 of 163 functions SAFE (159 plus the four new ones).
  - Dignity Q007 and Q009 bind all 12 payloads.
  - Dignity Q008 and Helpdesk Q007 take no value and pass trivially.
- **ams-native:** 108 converted, 4 unresolved, all output files identical before and after.
  - The original ZIP was located on 2026-10-20. Its SHA-256 is recorded here, and its `LICENSE.txt` is an MIT licence.
  - `evidence/baseline_unseen/ams_native/2026-10-20/README.md` (committed before the ZIP was found) still says the ZIP hash and licence are unknown; this file records the later finding.

## Not shown here

- **MySQL behaviour of the join rule.** `tests/benchmarks/clinic_hard/test_join_rule_mysql.py` covers a LEFT JOIN with NULL-extended rows, a comma join, an INNER JOIN regression and a bound hostile value. It runs only with MySQL (Part 2).
  - It is a **direct-SQL vs generated function** check: the same SQL is run through PHP mysqli and read with `mysqli_fetch_assoc`.
  - It is **not** PHP differential testing of the original application code. The Clinic application has no LEFT JOIN or comma-join site, so the existing PHP-vs-SQLAlchemy comparator cannot cover these shapes.
- **E1 behavioural equivalence** exists for HMS only. The four new functions have no E1 run.
