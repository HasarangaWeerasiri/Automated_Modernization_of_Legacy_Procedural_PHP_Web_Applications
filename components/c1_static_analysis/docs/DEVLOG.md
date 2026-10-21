# C1 dev log

Short notes per task: what, how, result. Newest at the bottom.

## 2026-10-17 — Setup
- Repo linked from your laptop (`Backend-and-Auth`, clean). The laptop sandbox has Python 3.10 and no PHP and no root.
- How I work: I develop and run the full tests in my cloud copy (PHP 8.3 + parser). Then I sync the files to the repo and re-run the tests there on Python 3.10.
- I never commit. The groups for you to commit are in `docs/COMMIT_GROUPS.md`.

## 2026-10-17 — Task 1: CFG stage
- **What:** `src/c1/cfg.py` (CFG builder), `dominance.py` (Cooper–Harvey–Kennedy), `astdoc.py` (shared AST reader).
- **How:** one graph per unit (`{main}`, functions, methods, closures). Labelled edges. `exit`/`die` are terminators. `X or die()` is split into a real branch. Includes are marked at block ends. Calls to always-exiting functions are cut, found by fixpoint.
- **PHP rules checked in the manual first:** `or` short-circuit and precedence, `exit`, `break N`/`continue N`, `continue` in `switch`.
- **Tests:** 31 new (`test_cfg.py`, `test_dominance.py`). Our dominators match networkx on 300 random graphs.
- **Real apps:**
  - HMS: 33 files, 107 units, 0 problems. HMS contains **no `exit` at all**, which confirms it has no terminating login guard.
  - WackoPicko: 49 files, 106 units, 0 problems. `http_redirect` and `error_404` were found as always-exiting (45 call sites cut). One dead statement was found, `upload.php:57`, after `http_redirect`. Checked by hand: correct.
- **Tests on your laptop:** 33 passed, 22 skipped (PHP and networkx are not installed there).

## 2026-10-17 — Task 2: Include resolver
- **What:** `src/c1/includes.py` writes `index/includes.json`.
- **How:** Hills et al. 2014 path folding plus unique file match. The PHP-manual lookup order (include_path, then caller dir, then cwd) is applied per entry page.
- **Tests:** 13 new (`test_includes.py`).
- **Result:** HMS 12/13 resolved (TCPDF excluded). WackoPicko 116/117 resolved (the `$_GET['page']` front controller is flagged).

## 2026-10-17 — Task 3: DFG (reaching definitions, use-def, origins)
- **What:** `src/c1/dfg.py` writes `dfg/<fileId>.json`.
- **How:** fixpoint dataflow over the CFG. Weak updates for array cells. Include sites define the included file's variables; unresolved includes and `extract()`/`eval()` "havoc". The origin chain is query → row → column. Function and static-method return summaries are used.
- **Acceptance test from the master reference (`$_GET['id']` → assignments → `echo`):** passes.
- **Tests:** 21 new (`test_dfg.py`).
- **Result:** echo reads that reach a real query column: HMS 122/139, WackoPicko 70/87.

## 2026-10-17 — Task 4: Concern classifier (labels.json)
- **What:** `src/c1/concerns.py` writes `labels.json` in the format C2 already uses.
- **How:** 18 rules (R01–R18). Data access comes from call names plus DFG (SQL text found by walking back from each query). The branch rule is the master-reference step 7. Function effects are summarised. A display binding is found by forward use-def.
- **Tests:** 10 new. One caught a real gap: a computation inside a display branch now abstains.
- **Result:** HMS 1646 labels (29 undecided, all in vendored upload code). WackoPicko 1460 labels (14 undecided).

## 2026-10-17 — Task 5: Authorization gates + page protection (auth.json)
- **What:** `src/c1/auth.py` writes `auth.json`.
- **How:** session checks found by DFG plus control dependence. Kind is authentication / role / ownership. Effect is terminating guard / gates data / display / broken guard / abstain. Guard helpers and guard files are found by dominance. Page protection uses dominators. Redirect-without-exit (EAR) is detected.
- **Tests:** 13 new.
- **Result:**
  - HMS: 0 guarded pages, **13 unguarded pages with data access** (7 run INSERT/UPDATE/DELETE). The one check (`checklogin.php`) is a broken guard and is never included.
  - WackoPicko: `require_login`/`require_admin_login` found as guard helpers. 17 pages guarded, 16 public or need a decision.
- **Fix found while testing on real code:** WackoPicko's admin area uses its own cookie session, so `$_COOKIE` reads now count as identity (marked `cookie:`).

## 2026-10-17 — Task 6: SQL shape + endpoint recovery + response shape
- **What:** `sqlshape.py` (SQL template from the DFG, read its structure, schema dump reader) and `endpoints.py` writing `endpoints.json` and `endpoint-map.json`.
- **How:**
  - Form handlers are the outermost request-triggered branches. Render SELECTs give per-query GETs.
  - The CRUD table follows the master reference. Session-filtered selects become `/api/me/...`. The login handler becomes `/api/auth/login`.
  - Values are traced through function/method parameters back to the call site.
  - Responses hold only the used columns, typed from the `.sql` dump.
- **Fixes found on real code:** WackoPicko `sprintf` SQL; values wrapped in `SHA1(...)`; inner validation `if` mistaken for a handler; register = INSERT (first write) not UPDATE; HMS login in an included `func.php`; patient view vs doctor view of `prestb` kept apart.
- **Tests:** 12 new.
- **Result:** HMS 19 endpoints, WackoPicko 25. `POST /api/contact` and the patient search match C4's hand-written map exactly.

## 2026-10-17 — Task 7: OpenAPI 3.1 contract + FastAPI/Pydantic backend + flags + one-command pipeline
- **What:** `contract.py` writes `contract/openapi.json`, `backend/app/*` and `flags.json`. `python -m c1 run <app>` runs every stage.
- **How:**
  - OpenAPI is written from `endpoints.json` (deterministic, with `x-c1` provenance).
  - Pydantic models, FastAPI routes and `auth.py` are generated (JWT with python-jose, `Depends(get_current_user)`, `require_claim`).
  - `data_access.py` has stubs for C3, each carrying the legacy SQL.
- **Tests:** 4 new. One boots the generated app: same paths as the contract, 401 without a token, the token claim reaches the data layer.
- **Result:** both apps generate a backend that boots. The whole pipeline is byte-identical on re-run.

## 2026-10-17 — Task 8: Output timeline for C2
- **What:** `src/c1/timeline.py` writes `timeline/<fileId>.json` in C2's format. `python -m c1 run` now includes it.
- **How:** output statements come in source order. Enclosures come from the AST (loop / branch / switch / try). Reads come from DFG origins: `db_row_field` with fetch, query, table and column; session, request, literal, or computed with `derivedFrom`. Call and include entries follow Q4; `parts` follows Q16.
- **Check:** for HMS `admin-panel1.php`, all 29 entries of C2's hand-built mock are identical in `enclosedBy` and `reads`.
- **Tests:** 10 new.
- **Result:** WackoPicko leaf reads reaching a DB column: 69/133. C2's mock had 0/27, because it had no DFG through classes.

## 2026-10-17 — Task 9: Two more benchmark apps + fixes they exposed
- **What:** ran the pipeline on chapagain/crud-php-complete and chetans9/core-php-admin-panel. The screening greps were run on all four apps (`docs/BENCHMARKS.md`).
- **Found:** crud-php-complete has 5 broken guards (redirect without `exit`), so `delete.php` deletes without login (EAR).
- **Fixes:**
  - `mysqli_real_escape_string($link, $value)` now keeps the origin of `$value`. crud's login is now found.
  - Query-builder/ORM calls (`$db->get()` …) are no longer silently missed. New rule R19 (abstain, flagged in `flags.json`, counted as data access for page protection).
- No change on HMS/WackoPicko results.
- **Tests:** 3 new.

## 2026-10-17 — Task 10: Evaluation harness + 200-statement labelling sheet
- **What:** `src/c1/evaluate.py`: `sample` writes a stratified labelling sheet with our labels hidden; `score` reports coverage, accuracy, per-class P/R/F1 and a confusion matrix, per app.
- **Sheet:** `docs/eval/concern_labelling_sheet.csv` (200 statements: 100 HMS, 70 WackoPicko, 30 crud), with `docs/eval/LABELLING_GUIDE.md`.
- **Needs people:** two annotators, then score. No accuracy numbers exist until then.
- **Tests:** 3 new.

## 2026-10-17 — Task 11: HTML review report + end-to-end test + housekeeping
- **What:** `src/c1/report.py` writes `report.html`: pages and guards, session checks, endpoints, and items to review. It is self-contained, works in light/dark mode and on a phone. It is now the last step of `python -m c1 run`.
- **Test:** `tests/test_pipeline_e2e.py` runs every stage after extract from cached ASTs, so it works without PHP. It checks the cross-stage results and that the pipeline is byte-identical across folders.
- **Housekeeping:** `pyproject.toml` now requires Python ≥3.10 (the code is tested on 3.10 and 3.13). The CI workflow runs the whole pipeline twice and diffs the outputs.

## 2026-10-19 — Task 12: Friendlier, colourful console
- **What:** `src/c1/console.py`. `python -m c1 run` now shows a "BACKEND & AUTH MIGRATION" banner, numbered steps with a plain-English result and time each, a results box and the next commands to type.
- **How:** plain ANSI colours, no new dependency (banner text pre-drawn with pyfiglet). Colour is turned on in the Windows console via `SetConsoleMode`. Plain text when the output is not a terminal, with `NO_COLOR`, or with `--plain`. A failing step prints a red FAIL line and stops (exit code 1).
- **Check:** the files written are byte-identical to before (diffed on HMS). Ran on Windows (cmd) by Amindu.
- **Tests:** 4 new (`test_console.py`); 150 pass.

## 2026-10-19 — Run from any folder
- **What:** `pyproject.toml` can now be installed: `pip install -e .` once in this folder, then `python -m c1 run <app> --out <folder>` (or `c1-migrate run ...`) works from any folder, with no `PYTHONPATH`.
- **Check:** installed in a clean venv and ran from `/`: same output as from this folder. The legacy app is only read, never changed.

## 2026-10-19 — One-click setup
- **What:** `install.bat` (Windows, double-click) and `install.sh` (macOS/Linux). They check Python 3.10+, PHP and Composer; run `composer install` if `vendor/` is missing; `pip install -e ".[backend,dev]"` (now includes uvicorn); add the folder holding `c1-migrate.exe` to the user PATH (not inside a venv); then run a self-check. Safe to run again.
- **Check:** `install.sh` tested in a clean venv. `install.bat` written for cmd but not yet run on Windows.

## 2026-10-19 — Generated backend: "/" opens the docs
- **Why:** opening `http://localhost:8000/` gave `{"detail":"Not Found"}` (the API has no home page), which looked like an error in the demo.
- **What:** the generated `main.py` redirects `/` to `/docs`. The route is hidden from the schema, so `openapi.json` and the contract test are unchanged.
- **Check:** `/` → 307 → `/docs` (200); 150 tests pass.

## 2026-10-21 — Task 13: Before/after metrics (supervisor: "show the migration succeeded")
- **What:** `src/c1/metrics.py` writes `metrics.json`. The report has a new "What the migration changed" table, and the console results box shows before -> after.
- **How:**
  - Security as attack surface (Manadhata & Wing 2011): missing authorization (CWE-862), EAR (CWE-698), SQL built from input (CWE-89), untyped inputs, password columns.
  - Structure as concern tangling (Eaddy et al. 2008), plus McCabe V(G) from our CFGs.
  - Vendored code is left out.
- **HMS:** 13 unguarded pages -> 8 token-protected + 10 flagged; EAR 12 -> 0; SQL from input 26 -> 0; tangled files 69% -> 0%.
- **Honest limits:** passwords are still flagged, not removed. V(G) is not compared after (the stubs would make it look unfairly good).

## 2026-10-21 — Task 14: Output fingerprint
- **What:** one 16-character code (SHA-256 over every output file) printed after each run. Same input gives the same code, from any folder. Checked on HMS: `FB4F 4367 86F3 95D2` from two different folders.

## 2026-10-21 — Task 15: Architecture advisor (supervisor: "more than one target architecture")
- **What:** `src/c1/architecture.py` writes `architecture.json`. The report shows 3 selectable cards (layered / modular monolith / microservices), each with a score, its reasons and a module plan. `--architecture` records the choice.
- **How:** business areas come from page co-use of tables (Jaccard >= 0.5, plus table names like cart/cart_items), with identity kept apart. The rules are explained on each card; a tie goes to the simpler option. Sources: Service Cutter, Mono2Micro, MonolithFirst, Taibi & Lenarduzzi.
- **Results:** HMS -> modular monolith (80 vs layered 75). WackoPicko -> modular (90). crud -> layered (95). core-php-admin-panel -> "not enough evidence".
- **Not done:** only the layered monolith is generated (the rest is PP2).
- **Tests:** 5 new; 155 pass.

## 2026-10-22 — Task 16: Endpoint map fixes asked by C4
C4 checked `endpoint-map.json` against their 11 needs: 7 were fully met, 3 partly and 1 missing. All 4 gaps are fixed:
- **Item 6, search pages said `records: "none"`.** A read handler whose rows the page uses now says `"only"` when it is the page's only results table, and `"unknown"` when the page has other tables too. It is never `"none"`. The 5 HMS search pages are now `"only"`.
- **Item 9, a field must repeat another.** `if ($password == $cpassword)` is found from the DFG (both sides are request input). The source gets `constraints: [{field: cpassword, same_as: password, line: 12}]` and the parameter gets `same_as`.
- **Item 8, login details.**
  - Each login form gets `login: {userType, fields, sets, redirects}`. The user type is the table its password is checked against. `sets` lists the session keys and cookies it writes, including inside called functions.
  - Each endpoint gets `auth: {required, userTypes, sessionKeys}`. The user types are the logins that set every key the endpoint needs, from session bindings, role checks and guard helpers.
  - The top level gives the token location: `access_token`, sent as `Authorization: Bearer`.
  - HMS: patient = patreg (pid...), doctor = doctb (dname), admin = admintb. WackoPicko: users (userid), admin (cookie session).
- **Item 10, the column behind each response field.** `response_fields: {field: table.column}`. Joins are resolved from `alias.column` in the select list, otherwise from the schema; ambiguous columns are flagged. This also fixed the type of `docFees` in the HMS bill query (it comes from appointmenttb).
- **Not blocking.** `GET /api/me/users` (include/header.php, table `users`) is now flagged `table-not-in-schema` instead of being dropped, so the endpoint counts stay the same.
- **Effect:** HMS still has 19 endpoints, with 46 review items (+table-not-in-schema, +user-type-unknown, -1 type-unknown). The fingerprint is now `D012 91EA 3AC8 A46E`.
- **Tests:** 4 new; 159 pass.
