# S13: SQL built and executed in the same conditional block

**Rule.** The SQL value at an execution point is deterministic when every conditional block (if/elseif/else/catch body) enclosing each assignment in the chain also encloses the call. The extractor now records `branch_context` (block identities). An else body is not counted as inside its if's then-body.

**Before → after** (per-query diff vs the S3b code): 50 queries changed, all from `UNRESOLVED/BRANCHING`. 49 are now CONVERTED and 1 is RECOVERED but conversion-unresolved (NEWS-BUZZ Q003, tab escape). No change on simple/mixed/hard/Clinic.

**Side effect (soundness).** `catch` blocks are now tracked. Before S13, `try { … } catch (…) { $sql = "B"; } mysqli_query($conn, $sql);` was recovered as "B" unconditionally. It now abstains (fixture `assign_in_catch_call_after`).

**False-recovery audit:** `false_recovery_audit_raw.md`, produced by `audit_newly_recovered.py`. 50/50 reviewed by hand; 50/50 pass the mechanical check.

**Time-box:** within the 3–4 h budget.
