# Component 2 — Agent Context (read this first, every task)

_Version 29 — 18 Oct 2026 (Phase 4f accepted: change 4 in, flag-but-wrong 0 on both apps; post-freeze change 5 (spec v0.17, the last) translates the List's own row count; Phase 4g = implement, parameterised phpBlog URLs, re-measure; then v1.1 is closed)._

This file is the handoff between the research chat and the coding agent. It records decisions
already made. **Do not change a decision recorded here.** If something here looks wrong or a task
conflicts with it, stop and report it instead of working around it.

---

## 1. Project in one paragraph

SLIIT final-year research project J26-SE-329 (four members). An automated, mostly deterministic
pipeline migrates **legacy procedural PHP + MySQL** web apps (HTML, logic, SQL and session checks
mixed in the same files) to a **three-tier** app: Next.js (frontend), FastAPI (backend),
SQLAlchemy (data). Anything the tool cannot resolve is **flagged, not guessed** (sound-or-abstain).

| Component | Owner | Job |
|---|---|---|
| Core / static analysis | Member 01 | AST, CFG, DFG, `output-timeline.json` |
| C1 | Backend & Auth | Concern labels (`labels.json`), API contract (OpenAPI 3.1) |
| **C2 (this folder)** | Jayawardhana R D L L | Frontend migration |
| C3 | Data layer | SQL recovery, schema |
| C4 | Test & validation | Behavioural equivalence |

## 2. What C2 does

| Stage | Name | Input → Output | Status |
|---|---|---|---|
| 1 | Presentation isolation | timeline + labels → a status for **every node the timeline mentions** (output nodes, guards, loops, queries, fetches), each output node keeping its full enclosure chain | **Done** (commit `1d91be0`) |
| 2 | Boundary inference | Stage 1 result → component tree, using `docs/php-analysis/boundary-rules.md` | **Done** (`cf56444`, `65674b8`) |
| 3 | Data requirement recovery | per component: union of its output nodes' `reads` → fields needed | **Done on mocks** (`f14a1cc` + loc fix-up) — spec `docs/reconciliation-spec.md` v0.2. Real input needs the DFG |
| 4 | Contract reconciliation | needs vs contract → matched / missing / unused / excluded / cannot reconcile | **Done on mocks** (`contact` gap found). Real `SELECT *` needs C3 schema; real evidence needs C1's contract + endpoint map |
| 5 | Generation | Next.js **Server Components** (`.tsx`) + typed fetch layer | **Done on mocks** (`aead7db`, `ee21ff8`; 0 tsc errors, strict, on all 10 mocks) — spec `docs/generation-spec.md` v0.4 |

Inline JS / AJAX is **out of scope**: detect and flag it, never convert it.
Everything is generated as a Server Component, **by construction**.

## 3. Input format (Member 01's schema 1.0)

**Timeline** (`output-timeline.json`, mocks: `mocks/timeline_*.json`) — output nodes in source order.
Each sequence entry has a stable ID (e.g. `f012#00347`) in the field **`id`** (enclosures, queries and
label keys use `nodeId`; field naming pending Member 01's confirmation — the loader follows the mocks), nikic `kind` (`Stmt_Echo`, `Stmt_InlineHTML`,
`Expr_Print`), byte-exact `raw`, and:

- `enclosedBy`: list, **outermost → innermost**
  - loop: `{nodeId, kind, role: "iteration", iterExpr, iterSourceKind, valueVar, keyVar}` — loop kinds
    accepted: `Stmt_While`, `Stmt_Foreach`, `Stmt_For`, `Stmt_Do` (C-style `for` fields pending Member 01)
  - branch: `{nodeId, kind: "Stmt_If", role: "branch", branch: "then"|"elseif"|"else", condNodeId}`
  - other roles: `switch_case`, `try_catch` — the loader **must accept and model** them. No boundary
    rule covers them yet, so Stage 2 **abstains and flags** any node enclosed by them. Never crash on them
- `reads`: `{expr, var, path, sourceKind, confidence, source: {fetchNodeId, queryNodeId, table, column}}`
  - `sourceKind`: `db_row_field | session | request | server | literal | computed | unresolved`
  - `confidence`: `resolved | ambiguous | unresolved`
  - `computed` reads carry `derivedFrom: [...]`

**Labels** (`labels.json`, mocks: `mocks/labels_*.json`) — from C1:
`{schemaVersion, labels: {<nodeId>: {concern, basis, ruleId, reason}}}`
- `concern`: `presentation | business_logic | data_access | mixed | undecided`
- `basis`: `rule | abstained`

**Agent-chosen extensions (pending Member 01's confirmation — do not build logic that depends on them):**
- `queries` map keyed by `queryNodeId` — **optional** in the loader. If present, use it only for raw SQL
  (reporting, later C3 join). Reads must still work with `source.queryNodeId` alone. Stage 1 and Stage 2 must not need it.
- `source: null` for non-DB reads; an `else` branch's `condNodeId` points at its `if` condition;
  `_provenance` blocks (kept as opaque metadata, never used in a decision).
**Member 01's answers (12 Oct 2026) — these are now the conventions. "Confirmed" = built; "proposed" = design, not built.**

| Topic | Convention | State |
|---|---|---|
| AST | `ast/<fileId>.json`: `{schemaVersion "1.0", fileId, path, sha256, bytes, parseOk, parseErrors, rootId, nodeCount, nodes:[{id, kind, parent, children, fields, attrs, loc}]}`. Roles are in `fields` (`cond`, `stmts`, `else`, `elseifs`, `exprs`, `parts`…). Literal values in `attrs` (`value`, `_rawValue`) | **Confirmed** — real file `legacy-apps/real/f001_admin-panel1_php_ast.json` (HMS `admin-panel1.php` @ `777fda4`, sha256 `492d63d9…`, 602 nodes) |
| Node ids | `fNNN#NNNNN`, numbered in **pre-order** (parent before children, children in `children` order) from `#00000` = root `File`. **The id is the join key** between AST, timeline and labels | **Confirmed** |
| `loc` | `{startLine, endLine}` 1-based; `{startCol, endCol}` **0-based, end exclusive**; `{startByte, endByte}` 0-based, end exclusive; `source[startByte:endByte]` = exact node text. Lines/cols are computed from bytes | **Confirmed**. Timeline entries and loops will carry the same six numbers |
| `condExpr` | Exact source of the `cond` node (no parentheses, already trimmed); none on `else`; each `elseif` has its own; comments kept | **Confirmed** (Q2/Q3) |
| Function output (Q4) | No inlining. At the call site the timeline has an entry `{kind: "call", callee: <function id> \| null, outputsInside: true \| false \| "unknown"}`; the function's echoes stay in the function's own timeline | **Proposed** |
| `SELECT *` (Q1) | `ambiguous` by default; `resolved` only if a schema is in the repo **and** one table, no JOIN; then the read carries `basis: "schema-expanded"` | **Proposed** (to confirm with C3) |
| DFG through calls (Q11) | v1: plain function calls; `new ClassName`, `Class::method` when the class name is unique; `$obj->method()` only with a visible `new` in the same flow; else `unresolved` | **Proposed** — not built |
| Timeline, labels, contract, endpoint map | Do not exist yet (need the CFG). Keep the hand-written contract and endpoint map; their `missing` results stay "planted" | — |

**C2 extensions still pending:** `queries` map (optional); `source: null` for non-DB reads; `_provenance`
(opaque); harness `includes` list and `parts` on echo entries (Q16 — the AST already holds them as
`Scalar_InterpolatedString.parts` / concat nodes, so `parts` must reference AST node ids); copy
propagation and interpolated-SQL values (Q17). All **optional** in the loader. The harness is a **test
harness, not Member 01's core** (`_provenance.generator = "c2-harness"`).

## 4. Hard rules

1. `src/mapping/loader.py` is the **only** file that reads Member 01's / C1's JSON. Everything else uses internal types.
2. **Never use `ruleId`** in any decision (C1's IDs are placeholders). Use `concern`; `basis` is for reporting only.
3. **Never derive or guess a concern.** Missing label → keep the node, status `review`, reason `unlabelled`.
4. `undecided` → render but mark `review`. `unresolved` reads → flag, never guess.
4a. A node's Stage 1 status comes from **its own label only** and never spreads to the nodes inside it.
    An enclosure's status (e.g. a loop labelled `mixed` because its condition fetches rows) is **information
    for Stage 2, not a block**: a `mixed` or `business_logic` loop/guard is still used as a structural boundary.
5. `session` and `request` reads are **excluded** from contract matching.
6. `ambiguous` reads need C3's schema → until then, flag them. `SELECT *` follows Member 01's Q1 rule (table above): resolved only with a schema in the repo, one table, no JOIN, `basis: "schema-expanded"`.
7. Output must be **deterministic**: same input → byte-identical output, stable ordering.
8. Do not modify mock files unless the task says so. Never change `raw` values.
9. **Commit policy (owner decision, 11 Oct):** the **owner commits**. The agent does **not** commit or push. At the end of every task it lists the changed and new files and proposes a commit message whose last line is the trailer `AI-assisted-by: Claude Code (Anthropic)` (this replaces `Co-Authored-By`). AI involvement is always disclosed: in that trailer and in the report's Appendix F. Before any branch switch, stop and ask the owner to commit (`component2_frontend/` does not exist on `dev`).
10. Never edit HMS files with Git Bash `sed` (it converts CRLF → LF).

## 5. Corpus rules

| Group | Apps | Agent may |
|---|---|---|
| Rule discovery (active) | HMS (`legacy-apps/hms`), WackoPicko (`legacy-apps/wackopicko`, MIT, PHP under `website/`), **phpBlog** (`legacy-apps/phpblog`, `codeplay-code/phpBlog-blogScript` @ `4ffe3c9`, GPL-3.0 — keep its LICENSE file with it; opened 13 Oct for Phase 3). Pinned commits and document roots are recorded in `legacy-apps/PINS.md` | Read, run the harness, test |
| Rule discovery (active, opened 14 Oct) | **attendance** (`ihumaunkabir/online-attendance-management-system-1.0` @ `3178104`, MIT, `mysql_*`) → `legacy-apps/attendance`, git-ignored clone like the others | Read, run the harness, test |
| Rule discovery (active, opened 14 Oct) | **warehouse** (`siamon123/warehouse-inventory-system` @ `69fdd1c`, MIT; DB through a `MySqli_DB` wrapper class — many reads will stay `unresolved`, which is data flow, not a pattern) → `legacy-apps/warehouse`, git-ignored. **The agent's machine cannot reach github.com**, so the owner supplies `warehouse-69fdd1c.zip` (a `git archive` of commit `69fdd1ca1364a37e6dfbc7d90ded693964d87efe`, no `.git`) and `warehouse-69fdd1c.sha256` (63 files). The harness reads the folder directly and **must verify every file against the manifest first** | Read, run the harness, test |
| Evaluation | crud-php-mysqli (GitHub zip download, upload sha256 `0cea4883…`), PHP-MySQL-CRUD-Application (zip download, `4380927f…`), College-Notes-Gallery @ `8c1cf3d`, ams-native @ `3c4d475`, School-Management-System (omd01) @ `1e5426d` — each supplied to the owner as an archive + sha256 manifest (`c2-eval-apps.zip`) | Rules are frozen; still **locked for the agent** until the owner's gold standard is committed (roadmap Phase 5). The agent may only run `tools/sample_eval_pages.py` when the owner asks (it lists file names), never the harness or any stage |

## 6. Repo state (as of 12 Oct 2026)

- Branch `frontend-migration-`. Pushed: `65674b8` (Stage 2 v0.3 decisions), `b695210` (CONTEXT v4 + rules v0.3), `cf56444` (main Stage 2 work). Stage 3–4 committed by the owner as `f14a1cc` (pushed). The loc / fromAbstained fix-up (21 modified + 1 new file) is committed by the owner before Stage 5 starts (see `git log`).
- Substitute endpoint map: `mocks/endpoint_map.json` (maps only list_while, list_foreach, admin, detail; others → `no_endpoint`). The contract and map were both written by the agent, so the `contact` gap is planted, not discovered.
  History was rewritten on 19 Sep (commit trailers removed): old `20d5e2a` = `fdc62b3`, `878df5c` = `1d91be0`, `c9c9535` = `a7cdedc`. Rewritten again on 10–11 Oct (local commits only, never pushed; `Co-Authored-By` replaced by `AI-assisted-by`): `ab64238` = `aead7db`, `ecdb8e0` = `ee21ff8`.
- Mocks: `timeline_{list_while,list_foreach,admin,detail,edge_cases}.json`, `labels_*.json` (5), `sample_contract.json`, legacy `sample_ast.json` (untouched).
- WackoPicko mocks: `{timeline,labels}_wp_{guestbook,view,login,header,thumbnails}.json` (generated). Their **labels are generator placeholders**, not C1 output, and every WackoPicko read is `unresolved` (data comes through class methods in `include/*.php`). Use them for Stage 2 only.
- Deliberate contract gap: field **`contact`** (flagged on list pages only). `sku` appears in no schema.
- `undecided` nodes: edge_cases (1, `auth_or_display`), wp_login (1, placeholder `logic_or_display`), wp_thumbnails (1, placeholder `logic_or_display`).
- **What the freeze covers (made explicit 15 Oct):** boundary rules (Stage 2), Stage 1–3 decisions and Stage 5 **markup** generation — everything Phase 5 measures. **Data wiring** (Stage 4 mapping to a contract, where fetches happen) is not measured in Phase 5 (the evaluation apps have no contracts) and may change only as a logged post-freeze change **with a proof that, with no contract / endpoint map, every output of the five discovery apps and the mocks is byte-identical**.
- Phase 4f (18 Oct): change 4 in (spec v0.16). 1098 tests; v1.0 guard byte-identical. HMS 0.996 (unchanged, 7 sites, none rendered on GET before). phpBlog seed 0.520 → 0.497, seed + fixture 0.341 → 0.286; flag-but-wrong 37 / 36 → 0; UNEXPLAINED 0. Fixture `initdb/03-fixture.sql` via `docker-compose.fixture.yml`. 4e count corrected to 36 / 1 (class and total unchanged).
- Phase 4e (16 Oct): phpBlog runs unmodified (`tools/runtime/phpblog/`, PHP 8.2, MariaDB 10.6; `config.php` / `config_settings.php` made exactly as `install/done.php` does, mounted in). 7 of 43 pages compared (36 redirect an anonymous GET), render match 0.520, UNEXPLAINED 0, flag-but-wrong 37 = the `else`-arm unsound decision (core.php 652, blog.php 29). The seed has no posts / users. Comparison tools improved (HMS unchanged).
- Phase 4d (16 Oct): change 3 (spec v0.15) done; **v1.1 final**. HMS final: render match **0.996** (1380 / 1386), 54 differences, UNEXPLAINED 0, flag-but-wrong 2 = React 19 hoisting `<meta>` into `<head>` (class `framework:react_hoist`, not a generator error). `docs/phase4-results.md` written. Phase 5 tools: `tools/eval/score.py`, `tools/eval/run_eval.py` (tested on `tests/fixtures/eval_synth/` only); `docs/eval-mapping.md` v1.1.
- Phase 4c (16 Oct): `rules_version` (default 1.1); `--rules v1.0` reproduces the freeze byte for byte (five apps `ba8e4ac7…25d9`, mocks `71b746eb…0cf6`). HMS v1.0 → v1.1: render match 0.947 → **0.991**, UNEXPLAINED 2 → 0, flag-but-wrong 17 → 9. R-I1 sites: 11 → 0 stay R-I1 (WackoPicko login-style pages now conditionals, forms hidden on GET, flagged).
- Phase 4b (16 Oct): 7 / 16 HMS Lists get replay data; render match 0.588 → **0.947** (1312 / 1386); 69 differences, 67 `flag:`, 2 UNEXPLAINED (`search.php`, R-I1). Guard proof vs `27c380e`: five apps `ba8e4ac7…25d9`, mocks `71b746eb…0cf6`. **Post-freeze changes 1 (R-I1 scope → rules v1.1) and 2 (head content, heading end tags → spec v0.14)** come from this discovery-app evidence; Phase 5 reports v1.0 and v1.1.
- Phase 4a: HMS runs unmodified in Docker (`tools/runtime/hms/`, MariaDB 10.1.48, shared socket, `display_errors=Off`); replay API `tools/runtime/replay/` (SELECT-only user `c2replay`) on :18090; generated app `next start` on :18100. Raw result (no data wired): 26 pages, 1386 legacy / 836 generated elements, 815 matched, 202 differences.
- **Rules frozen** (boundary-rules v1.0, spec v0.12) by the owner's commit of Phase 3 step 5 — write its hash here after committing: `________`. No rule or spec change after it without a *post-freeze* entry in boundary-rules §10.
- Tests: 988 passing, 0 skipped (after step 4 part B). Warehouse: 48 PHP files, 41 pages, 1 shell (`layouts/header.php` + `footer.php`), 138 abstains (`unresolved_call` 76 — the wrapper, Q11; `cross_file_output` 40 — mostly the whitespace-only `load.php`), 0 tsc errors, `next build` ok. Manifest `legacy-apps/warehouse-69fdd1c.sha256` committed; the harness verifies it before reading.
- Earlier: 981 passing, 2 skipped (after step 4 part A). phpBlog abstains 57 → 15, `unresolved_call` 20 → 0, `exit_arm_not_covered` 24 → 0; `header_redirect` HMS 13, attendance 15 (files that print nothing → `index.json` `silentFileRedirects`); R-I8c flags at attendance's 12 `connect.php` includes.
- Earlier: 957 passing (after Phase 3 step 3). Attendance: 19 files, 14 pages, 0 shells, abstains 11 (`try_catch_enclosure` 6), 0 tsc errors, `next build` ok. R-I8c flags on WackoPicko login / register / similar; phpBlog `cross_file_output` 24 → 0.
- Earlier: 938 passing (after Phase 3 step 2). phpBlog: abstains 101 → 52, all 9 previously-lost units generated, admin header/footer pair (route group `(admin-header)`), `rss.php` / `sitemap.php` not generated (`non_html_response`). `config.php` analysed from `install/config.tpl` (PINS.md). Rules fired in phpBlog: R-I3b 58, R-I2b 1, R-I8 37 (all redirect guards).
- Earlier: 886 passing (after Phase 3 step 1). phpBlog: 52 units (49 pages, 1 function component, 2 shells), 0 crashes, 0 tsc errors, `next build` ok; abstains 101 (tag_crosses_function 48 — fixed by the R-F2 refinement; unresolved_call 23; cross_file_output 18; …). Includes are timeline entries (`{kind: "include", path, loc}`, harness extension pending Member 01 Q4). phpBlog's clone is git-ignored like the other apps; `PINS.md` records how to restore it.
- Earlier: 826 passing (after Phase 2b). Each app is assembled and checked as **one Next.js app** (`tsc --noEmit` 0 errors, `next build` ok for HMS and WackoPicko; build project in `output/test-app/c2-build/<app>/`). R-F1 (HMS 2, WackoPicko 8 call sites), R-F1b 4, R-F2 (WackoPicko `our_header`/`our_footer`, 24 pages, route group `(our_header)`), R-I7 10, §5b repairs. Nesting flags WackoPicko 9 → 5. Harness extensions now include call `args`, `function.params[].readOnly`, loc on echo parts (Member 01 Q19).
- Earlier: 779 passing (after Phase 2a). Echo `parts` from the AST, copy propagation (same block only — strict on purpose; the real DFG is Member 01's), interpolated-SQL values (`valuesInterpolated`, flag `sql_interpolated_values`), R-L3b, every generated file a module, checker uses the test app's own tsconfig. HMS: `no_rule` abstains 4 → 0, components 59 → 66, unresolved reads 75 → 27. Todo counts are now **per expression part** (finer than before) — compare flag rates only within the same counting unit. Harness file ids differ from Member 01's (`admin-panel1.php` = `f002` vs his `f001`) — Member 01 Q18.
- Earlier: 706 passing, 0 skipped (after Phase 1b). **AST acceptance:** the harness's AST for HMS `admin-panel1.php` is **byte-identical** to Member 01's (602/602 nodes + header). Fixes needed: `null` sub-nodes in `fields`, `_kind` copied, sorted keys. Still our own choice (the real file cannot show it): `parseErrors` item shape, other nikic attributes, non-ASCII JSON escaping, echoes closed by `?>`. `.gitattributes` keeps `legacy-apps/real/` byte-exact (owner's git has `core.autocrlf=true`). Timeline builder gives the same 49 entries from either AST. Mocks and whole-app outputs unchanged by the fixes.
- HMS `callee: null` calls: 16 — 15 are TCPDF `$obj_pdf->…()` method calls (Q11, stay unknown); 1 (`display_docs()`, `admin-panel.php:359`) resolves through the page's literal includes to `newfunc.php` (Phase 2b).
- Earlier: 636 passing after Phase 1. Whole-app harness `tools/harness/` (nikic/php-parser 5.9.0, composer.lock committed, `vendor/` ignored); reads each app at its pinned commit via `git archive` (the HMS working copy has 11 locally patched files). Whole-app outputs `output/apps/` (ignored); tables `docs/c2-apps-results.md`, `docs/php-analysis/pattern-log.md`. Phase 1 totals: HMS 33 timelines, WackoPicko 33, 0 crashes, 0 tsc errors. Review counts are dominated by `unlabelled` (no C1 labels for whole apps). Docker 29.7.2 is installed (Phase 4).
- Earlier: 573 passing after Stage 5 v0.4 / rules v0.7 (`3d1d798` + owner commit).
- Earlier: 568 passing after the Stage 5 close-out (`3d1d798`). Results table: `tools/results_table.py` → `docs/c2-results.md` (re-run after each commit).
- Earlier: 564 passing after Stage 5 v0.2 (`ee21ff8`). `html5lib==1.1` pinned for the nesting check. All-mock totals (spec v0.4): 58 todo, 39 review, 120 info, **flag rate 97**, 9/27 components without C2Todo, 0 tsc errors, 2 nesting flags. Stage 1–4 outputs unchanged since `ee21ff8` (482,900 bytes compared).
- Earlier: 537 passing after Stage 5 v0.1 (`aead7db`). Node 24.16.0 on the owner's machine; test app `output/test-app` (create-next-app scaffold, versions pinned).
- Stage 5 modules: `stage5_generate.py`, `markup.py`, `jsx.py`, `conditions.py`, `generation_config.py`; config `generation.json`, `react_dom.json` (from `tools/gen_react_dom.py`); `tools/compile_check.py`.
- Earlier: 380 passing after the Stage 3–4 fix-up. All 10 timeline mocks carry `loc`; `tests/test_loc.py` checks every loc against the real source bytes. The Stage 2 CLI test forces UTF-8 (the owner's Windows console uses code page cp932). Extra fixtures: `tests/fixtures/{timeline,labels}_unlabelled.json`, `tests/fixtures/{timeline,labels}_switch_case.json` (`caseNodeId`, `Stmt_Switch` are placeholders).
- Loader: accepts `iteration`, `branch`, `switch_case`, `try_catch` roles (unknown fields of the last two kept as opaque `extra`); rejects any other role as format drift. `queries` map optional; validated only when present.
- Review nodes in list_while / list_foreach / detail = the loop itself, `mixed`, reason `fetch_in_loop_header` (expected; rule 4a).
  Generator `tools/gen_timelines.py` needs PHP and HMS at `777fda4`.
- Stage 1 summary: list_while 35→32 ok/1 review/2 excl · list_foreach same · admin 18→15/0/3 · detail 27→24/1/2 · edge_cases 27→23/1/3.
  Summary semantics: `kept` = status ok; ok + review + excluded = total.
- `src/main.py` keeps dict-returning aliases only so the original 50 mock tests pass; all format knowledge is in the loader.
- Single-table `SELECT *` in the list mocks: Phase 1b applies Member 01's Q1 rule (HMS ships `myhmsdb.sql`, so `appointmenttb` reads stay resolved, now with `basis: "schema-expanded"`).

- Stage 5 compile check needs Node.js + `npx tsc`; the test skips with a clear message if Node is missing (report it).

## 7. Boundary rules

See `docs/php-analysis/boundary-rules.md` (current draft: see its header). Rules are **settings, not code**:
put rule thresholds (e.g. N = 4) in a config file so the owner can change them without code changes.
The rules are decided by the owner, not the agent. If code reveals a case no rule covers, **report it**;
do not invent a rule.

## 8. How to report back (every task)

1. Files created / changed
2. Test count and result (all must pass)
3. Commit hash, and whether it was pushed
4. **Choices you made where the spec was silent** — listed separately
5. Questions for the owner or for Member 01
