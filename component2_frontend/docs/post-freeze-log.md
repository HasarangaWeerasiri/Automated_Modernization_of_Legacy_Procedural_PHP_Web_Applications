# Component 2 — Post-freeze change log

The boundary rules were frozen at v1.0 on 15 October 2026 (`docs/php-analysis/boundary-rules.md`; freeze commit
in CONTEXT.md §6). Every later change to the rules, to the generation spec, or to code that changes a decision is
recorded here, with its reason, and is also logged in boundary-rules §10 as *post-freeze*. If a change comes after
the evaluation (Phase 5) has run, the evaluation is re-run and reported both before and after it.

`tests/test_freeze.py` fails if the rules document stops saying it is frozen.

| Date | Change | Reason | Evaluation re-run? |
|---|---|---|---|
| 15 Oct 2026 | **Bug fix, harness assembly** (`tools/harness/run_apps.py`, `merge_lib`): when several units of one app write different `lib/api.ts` / `lib/api-types.ts` (each holds only its own endpoint's fetch function and response types), the whole-app assembly now merges them (union of declarations, one import line); before, the first unit's file was kept and the others were only listed as collisions, so `next build` failed (`api.ts` imported a type `api-types.ts` did not export). No rule, spec or stage changed | Found in Phase 4a: the substitute replay map gives HMS two pages with an endpoint, the first whole-app run with more than one. Every earlier run had no endpoint, so all units wrote the same stub and nothing changed: the five apps' outputs and the mocks are byte-identical before and after | No (no evaluation has run) |
