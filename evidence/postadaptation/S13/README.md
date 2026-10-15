# Post-adaptation runs after S13 (labelled post-adaptation, NOT holdout results)

Frozen pre-adaptation baselines are unchanged: Helpdesk at tag `c3-helpdesk-baseline-6of9` (`output/helpdesk_legacy_holdout/`), and the unseen apps in `evidence/preadaptation/`.

| App | Role | Pre-adaptation total / recovered / converted | Post-adaptation (after S1, S2, S3, S3b, S13) | Change from S13 alone |
|---|---|---|---|---|
| Helpdesk | holdout | 9 / 7 / **6** | 9 / 9 / **8** | +2 (Q002 `create.php`, Q004 `edit.php`). Q007 (LEFT JOIN + GROUP BY) is still conversion-unresolved. |
| HMS (kishan0725) | unseen | 44 / 19 / **19** (crashed at stage 3/6) | 44 / 40 / **40** (all 6 stages) | +21 |
| NEWS-BUZZ | unseen | 32 / 4 / **4** (crashed at stage 3/6) | 32 / 30 / **29** (all 6 stages) | +26 recovered; one of them (Q003) is not converted: a literal tab inside the PHP string is not decoded, so the SQL fails to parse and conversion abstains |
| Fee-Rainbow | unseen | 22 / 19 / **18** (crashed at stage 3/6) | 22 / 20 / **19** (all 6 stages) | +1 |

Other fixes changed no per-query result on these apps; S1–S3b changed only crash and model behaviour (see the regression checks in the evidence register). The development fixtures (simple, mixed, hard, Clinic) are unchanged by S13.

## How to read these numbers (threats to validity)
- S13 targets the pattern class behind Helpdesk Q002/Q004: SQL built and executed in the same conditional block. The unseen apps' pre-adaptation summaries were inspected before S13 was written, and they showed the same class dominating their unresolved queries (HMS 21/25, NEWS-BUZZ 28/28 BRANCHING). These runs are therefore **post-adaptation** for all four apps. They show that the general rule applies there, but they are **not** independent evidence of generalisation.
- The rule is general (no app, file, query-ID or table names) and was tested on independently written fixtures (`components/c3_data_layer/tests/test_guarded_block_recovery.py`: 6 must-recover, 7 must-abstain, 1 extractor check).
- **False-recovery audit:** all 50 queries that S13 newly recovered were reviewed by hand (`evidence/soundness/S13_guarded_blocks/false_recovery_audit_raw.md`). Every one is a single assignment followed by the call in the same block, so none is unsound. A mechanical containment re-check also passed for 50/50.
- "Converted" is not "equivalent": MySQL behavioural equivalence for these apps is still pending (E1).
- **Recommended:** run the reserve app (Dignity-Dev, MIT) once, untouched, with the current code, to obtain a genuinely fresh holdout result.
