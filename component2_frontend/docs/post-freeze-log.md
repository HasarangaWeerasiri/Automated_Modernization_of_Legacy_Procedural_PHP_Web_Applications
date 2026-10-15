# Component 2 — Post-freeze change log

The boundary rules were frozen at v1.0 on 15 October 2026 (`docs/php-analysis/boundary-rules.md`; freeze commit
in CONTEXT.md §6). Every later change to the rules, to the generation spec, or to code that changes a decision is
recorded here, with its reason, and is also logged in boundary-rules §10 as *post-freeze*. If a change comes after
the evaluation (Phase 5) has run, the evaluation is re-run and reported both before and after it.

`tests/test_freeze.py` fails if the rules document stops saying it is frozen.

| Date | Change | Reason | Evaluation re-run? |
|---|---|---|---|
