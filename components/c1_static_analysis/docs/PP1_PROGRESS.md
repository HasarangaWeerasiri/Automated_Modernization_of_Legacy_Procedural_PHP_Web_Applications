# Component 1: progress for PP1 (22–27 Oct 2026)

Owner: R D A P Rajamuni (IT23265738). State on 17 Oct 2026. The numbers come from the benchmark apps: HMS (777fda4) and WackoPicko, plus crud-php-complete and core-php-admin-panel, run on 17 Oct (`docs/BENCHMARKS.md`).

## The 14 steps in the master reference (§3.1)

| # | Step | State | Evidence |
|---|---|---|---|
| 1 | Parse PHP to AST | **done** | 33 + 49 files, 0 parse failures; deterministic across machines |
| 2 | Control-flow graph | **done** | 213 units, 0 structural problems; `exit`/`die`, `or die`, always-exit helpers |
| 3 | Data-flow graph | **done** | Reaching definitions, use-def chains, origins through includes, functions, static methods. Acceptance test (`$_GET['id']` → `echo`) passes |
| 4 | Classify concerns | **done (v1 rules)** | 18 rules; abstains on 1.8% (HMS) / 1.0% (WackoPicko) of statements |
| 5 | Cluster into endpoints | **done** | HMS 19, WackoPicko 25 endpoints |
| 6 | Recover response shape | **done** | Only the columns the page uses, typed from the schema dump |
| 7 | Identify authorization gates | **done** | Authentication / role / ownership × guard / gates data / display / broken guard / abstain; guard helpers; page protection by dominance |
| 8 | Map to HTTP method + path | **done** | Master-reference CRUD table, `/api/me/…` for session filters, `/api/auth/login` |
| 9 | Session → JWT | **done** | Generated `auth.py`: `Depends(get_current_user)`, token claims, `require_claim` |
| 10 | Pydantic models | **done** | Generated `models.py` |
| 11 | Abstain and flag | **done** | `flags.json`: HMS 45 items, WackoPicko 62 |
| 12 | FastAPI route files | **done (skeleton)** | Generated app boots and serves the contract. Data access is a C3 interface |
| 13 | Pydantic = contract → OpenAPI 3.1 | **done** | `contract/openapi.json` matches what FastAPI serves (tested) |
| 14 | Auth module | **done** | See 9 |

Also built for the team:
- `labels.json` and the output timeline for C2. On HMS `admin-panel1.php` the timeline is identical to C2's hand-built mock.
- `endpoint-map.json` in C4's format. `POST /api/contact` and the patient search match C4's hand-written map.

## Findings worth showing

- **HMS has no working login gate.** 13 pages read or write data with no guard, and 7 of them run INSERT/UPDATE/DELETE. Its only check (`include/checklogin.php`) redirects without `exit` and is included nowhere.
- **HMS shows passwords.** Doctor and patient passwords are output on admin pages (flag `sensitive-field`).
- **Mutating GET.** The HMS cancel link (`?cancel=…`) changes data on a GET. Mapped to `PUT` and flagged; supervisor decision pending.
- **WackoPicko guards.** `require_login()` and `require_admin_login()` are found automatically as guard helpers, the admin one through its own cookie session. 17 pages are guarded.
- **Redirect without exit (EAR, Doupé et al. CCS 2011).** 11 cases in HMS. crud-php-complete is the textbook case: 5 guards redirect but don't `exit`, so `delete.php` deletes without login.
- **Out-of-scope apps are flagged, not guessed.** core-php-admin-panel uses a query builder (MysqliDb). We make 0 endpoints and flag every call (R19); its guard include is still found (10 pages guarded).

## What is not done (honest list)

1. **Evaluation.** The harness and the 200-statement sheet are ready (`docs/eval/`). Two people still need to label the sheet; no accuracy numbers exist until they do. Contract completeness and flag rate can be reported now from `flags.json` and `endpoints.json`.
2. **Bounded LLM layer for ambiguous cases.** Not built. Today every ambiguous case abstains (the safe default).
3. **More benchmark apps.** 4 run (3 in scope), out of the planned 5–8.
4. **Integration.**
   - C3: ORM types and data-access implementations go into the generated stubs.
   - C4: needs a reader for `c1-endpoint-map/1` (same structure as their stand-in).
   - C2: switch from mocks to our timeline/labels/contract.
5. **Known analysis limits.** `$obj->method()` on non-`$this` receivers; array cells are not tracked separately; `finally` is approximated.

## How it is built (one line each)

- Python pipeline over the nikic/PHP-Parser AST: `python -m c1 run <app>`, 12 stages, every output tagged with the input hash.
- 146 tests (and an end-to-end test that runs every stage without PHP). The whole pipeline is byte-identical on re-run and from another folder.
- Design choices are tied to sources: Xie & Aiken 2006; Cooper–Harvey–Kennedy 2006; Hills et al. 2014; RoleCast 2011; Doupé et al. 2011; the PHP manual.

## Update 21 Oct 2026 (after the supervisor meeting)

Supervisor asked for: (1) a measured success factor, (2) more than one target architecture, (3) a sharper novelty pitch, (4) a 2:30 demo. Done:

### 1. Success factors (`metrics.json`, report section "What the migration changed")

Security is measured as attack surface (Manadhata & Wing, IEEE TSE 2011). Structure is measured as concern tangling (Eaddy et al., IEEE TSE 2008), plus McCabe V(G). HMS, before → after:

| Measure | Before | After |
|---|---|---|
| Data pages with no working login check (CWE-862) | 13 | 8 token-protected + 10 flagged for a decision |
| Redirects that don't stop (EAR, CWE-698) | 12 | 0 |
| SQL built from request input (CWE-89) | 26 | 0 (typed arguments) |
| Untyped request inputs | 90 | 0 (45 declared parameters) |
| Files mixing HTML/logic/SQL | 69% | 0% |
| Password columns sent | 2 | still flagged |

### 2. Architecture advisor (`architecture.json`, report cards, `--architecture`)

It scores layered monolith / modular monolith / microservices with listed reasons. Business areas come from how pages use tables (after Service Cutter and Mono2Micro). The rules follow MonolithFirst and the shared-persistence smell.

| App | Suggestion |
|---|---|
| HMS | modular monolith (80 vs layered 75) |
| WackoPicko | modular monolith (90) |
| crud | layered (95) |
| core-php-admin-panel | not enough evidence |

Only layered is generated now; the rest is PP2.

### 3. Reproducibility

An output fingerprint is printed after each run. The same input gives the same code from any folder (checked).

### 4. Pitch and demo

- Novelty slide: `c1_novelty_api_boundary.png`. It uses real tool output: admin-panel.php switches concern 54 times and becomes 9 endpoints, with locks and flags.
- 2:30 script: `docs/PP1_DEMO_SCRIPT.md`.
- Laravel: proposed out of scope, because frameworks already declare their routes, so there is no API to recover.

### Open decision

Secure by default: lock the endpoints whose legacy page had no check (10 on HMS) and let a person mark the public ones. Recommended, waiting for agreement.

Tests: 155 pass.
