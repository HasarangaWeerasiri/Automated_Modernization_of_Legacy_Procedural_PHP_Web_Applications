# Concern labels and authorization analysis (schema 1.0)

```
python -m c1.concerns analysis-out          # -> labels.json      (format agreed with C2)
python -m c1.auth analysis-out --src <app>  # -> auth.json
```

## Concern labels (`labels.json`)

`{schemaVersion, generatedFor, rules, labels: {<nodeId>: {concern, basis, ruleId, reason, detail?}}, totals}`

- `concern`: `presentation | business_logic | data_access | mixed | undecided`
- `basis`: `rule | abstained`. `undecided` is always `abstained`.

| Rule | Concern | When |
|---|---|---|
| R01 | presentation | echo / print / inline HTML / printf |
| R02 | data_access | runs a query |
| R03 | data_access | fetches a row |
| R04 | data_access | connects to / configures the DB |
| R05 | data_access | builds SQL text that reaches a query (found through the DFG) |
| R06 | business_logic | branch/loop whose body runs a query or changes state |
| R07 | business_logic | session, cookie, redirect, exit, file or mail side effect |
| R08 | presentation | branch whose bodies only output |
| R09 | business_logic | other computation |
| R10 | undecided | branch mixes output with computation: **abstain** |
| R11 | data_access | row counts, insert ids, escaping, prepared-statement plumbing |
| R12 | presentation | loop over fetched rows that only outputs |
| R13 | business_logic | reads request input into a variable |
| R14 | mixed | one statement both outputs and touches the DB (often a helper call) |
| R15 | business_logic | return / break / continue |
| R16 | undecided | branch calls code we cannot classify: **abstain** |
| R17 | presentation | plain copy whose every later use is output (forward use-def) |
| R18 | business_logic | include / require |

- R06/R08/R10 follow the master-reference step-7 rule. The test `test_master_reference_worked_example` uses its worked example exactly.
- Function and method effects are summarised and propagate to call sites (fixpoint), so `show_rows();` is labelled from what the function does.

## Authorization (`auth.json`)

### `checks[]`

One entry per branch whose predicate reads `$_SESSION`, or an app session cookie (`cookie:<name>`). Reads through variables and helper functions count.

| Field | Values |
|---|---|
| `kind` | `authentication` (signed in?) · `authorization:role` (compared with a literal) · `authorization:ownership` (compared with data/input) · `unknown` |
| `effect` | `terminating-guard` (failing side always exits) · `gates-data` · `display-only` · `predicate-helper` (only returns: part of a guard helper) · `broken-guard` (redirects but does not exit) · `ambiguous` |
| `serverSide` | true → becomes a FastAPI `Depends()`; false → the frontend may render it; null → flagged |

- Presence tests (`strlen($_SESSION['x']) == 0`, `== ''`, `!= null`) are authentication, not role checks.

**Control dependence.** WackoPicko's `is_logged_in()` returns `True`/`False` literals inside `if (current_user())`. The session decides the result through control flow, so data flow alone misses it. We mark a function *session-dependent* when a branch or return in it depends on the session, directly or through another such function (fixpoint).

### Guard points and protection (`pages[]`)

A guard point is any of:
- a terminating guard;
- a call to a **guard helper**: a function whose every normal exit is dominated by a passing terminating guard, e.g. `require_login()`;
- an include of a **guard file**.

A statement is protected when a guard point dominates it. Dominators come from the CFG stage (Cooper–Harvey–Kennedy).

Page `status`:
- `guarded` · `partially-guarded` · `unguarded` · `no-data-access`;
- only queries and fetches count as data access (R02/R03/R14), not DB connections or building SQL text.

`unguarded` and `partially-guarded` pages carry `needsDecision: true`, `unguardedWrites`, and `writesSession` (a login-page hint). Public page or missing check is the app owner's decision. Master reference §9.5: never guess a business-domain decision.

### `redirectWithoutExit[]`

`header('Location: …')` followed by more statements on some path. That code still runs. This is an **Execution After Redirect** (EAR) flaw: Doupé, Boe, Kruegel, Vigna, "Fear the EAR: Discovering and Mitigating Execution After Redirect Vulnerabilities", ACM CCS 2011, doi:10.1145/2046707.2046736 (verified). It is also listed as CWE-698. Doupé also wrote WackoPicko, one of our benchmark apps.

## Results

| | HMS | WackoPicko |
|---|---|---|
| Session checks | 1 (`checklogin.php`: **broken guard**, and no page includes it) | 8 |
| Guard helpers | none | `require_login` (session `userid`), `require_admin_login` (cookie `session`) |
| Pages guarded / unguarded / no data | 0 / **13** / 14 | 17 / 16 / 5 |
| Redirect without exit | 11 | 1 |

HMS has 13 pages that read or write data with no guard. 7 of them run INSERT/UPDATE/DELETE. This matches the earlier manual finding.
