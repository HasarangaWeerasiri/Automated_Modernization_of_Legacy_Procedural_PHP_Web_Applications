# Component 2 — Agent Context (read this first, every task)

_Version 7 — 10 Oct 2026 (updated after the Stage 3–4 fix-up review; Stage 5 starts)._

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
| 5 | Generation | Next.js **Server Components** (`.tsx`) + typed fetch layer | **In progress** — spec `docs/generation-spec.md` v0.1 |

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
- `loc {startLine, endLine, startCol, endCol}` on sequence entries — **optional**. Mock convention: 1-based,
  columns in bytes, end inclusive (Member 01 Q13–Q15). Used only for file:line in reports.
- `condExpr` on branch enclosures — **optional**, byte-exact PHP condition source (Member 01 Q2). Used only
  by Stage 5's whitelist translator; missing → `condition_unavailable`, never guessed.

## 4. Hard rules

1. `src/mapping/loader.py` is the **only** file that reads Member 01's / C1's JSON. Everything else uses internal types.
2. **Never use `ruleId`** in any decision (C1's IDs are placeholders). Use `concern`; `basis` is for reporting only.
3. **Never derive or guess a concern.** Missing label → keep the node, status `review`, reason `unlabelled`.
4. `undecided` → render but mark `review`. `unresolved` reads → flag, never guess.
4a. A node's Stage 1 status comes from **its own label only** and never spreads to the nodes inside it.
    An enclosure's status (e.g. a loop labelled `mixed` because its condition fetches rows) is **information
    for Stage 2, not a block**: a `mixed` or `business_logic` loop/guard is still used as a structural boundary.
5. `session` and `request` reads are **excluded** from contract matching.
6. `ambiguous` reads (e.g. `SELECT *`) need C3's schema → until then, flag them.
7. Output must be **deterministic**: same input → byte-identical output, stable ordering.
8. Do not modify mock files unless the task says so. Never change `raw` values.
9. **Commit policy (owner decision, 10 Oct):** the agent **commits locally** on `frontend-migration-` at the end of every task and before any branch switch (`component2_frontend/` does not exist on `dev`), using the attribution trailers the tool adds. **Never push** — the owner pushes. This replaces the 8 Oct "don't commit" instruction.
10. Never edit HMS files with Git Bash `sed` (it converts CRLF → LF).

## 5. Corpus rules

| Group | Apps | Agent may |
|---|---|---|
| Rule discovery | HMS (`legacy-apps/hms`), WackoPicko (`legacy-apps/wackopicko`, MIT, PHP under `website/`). Pinned commits are recorded in `legacy-apps/PINS.md` | Read, build mocks, test |
| Evaluation | crud-php-mysqli, PHP-MySQL-CRUD-Application (+ more to come) | **Do not open, read or build mocks from these.** They stay locked until the owner **freezes** the boundary rules (the rules doc header says *Frozen*). Finishing a stage does not unlock them |

## 6. Repo state (as of 10 Oct 2026)

- Branch `frontend-migration-`. Pushed: `65674b8` (Stage 2 v0.3 decisions), `b695210` (CONTEXT v4 + rules v0.3), `cf56444` (main Stage 2 work). Stage 3–4 committed by the owner as `f14a1cc` (pushed). The loc / fromAbstained fix-up (21 modified + 1 new file) is committed by the owner before Stage 5 starts (see `git log`).
- Substitute endpoint map: `mocks/endpoint_map.json` (maps only list_while, list_foreach, admin, detail; others → `no_endpoint`). The contract and map were both written by the agent, so the `contact` gap is planted, not discovered.
  History was rewritten on 19 Sep (commit trailers removed): old `20d5e2a` = `fdc62b3`, `878df5c` = `1d91be0`, `c9c9535` = `a7cdedc`.
- Mocks: `timeline_{list_while,list_foreach,admin,detail,edge_cases}.json`, `labels_*.json` (5), `sample_contract.json`, legacy `sample_ast.json` (untouched).
- WackoPicko mocks: `{timeline,labels}_wp_{guestbook,view,login,header,thumbnails}.json` (generated). Their **labels are generator placeholders**, not C1 output, and every WackoPicko read is `unresolved` (data comes through class methods in `include/*.php`). Use them for Stage 2 only.
- Deliberate contract gap: field **`contact`** (flagged on list pages only). `sku` appears in no schema.
- `undecided` nodes: edge_cases (1, `auth_or_display`), wp_login (1, placeholder `logic_or_display`), wp_thumbnails (1, placeholder `logic_or_display`).
- Tests: 380 passing (after the Stage 3–4 fix-up). All 10 timeline mocks carry `loc`; `tests/test_loc.py` checks every loc against the real source bytes. The Stage 2 CLI test forces UTF-8 (the owner's Windows console uses code page cp932). Extra fixtures: `tests/fixtures/{timeline,labels}_unlabelled.json`, `tests/fixtures/{timeline,labels}_switch_case.json` (`caseNodeId`, `Stmt_Switch` are placeholders).
- Loader: accepts `iteration`, `branch`, `switch_case`, `try_catch` roles (unknown fields of the last two kept as opaque `extra`); rejects any other role as format drift. `queries` map optional; validated only when present.
- Review nodes in list_while / list_foreach / detail = the loop itself, `mixed`, reason `fetch_in_loop_header` (expected; rule 4a).
  Generator `tools/gen_timelines.py` needs PHP and HMS at `777fda4`.
- Stage 1 summary: list_while 35→32 ok/1 review/2 excl · list_foreach same · admin 18→15/0/3 · detail 27→24/1/2 · edge_cases 27→23/1/3.
  Summary semantics: `kept` = status ok; ok + review + excluded = total.
- `src/main.py` keeps dict-returning aliases only so the original 50 mock tests pass; all format knowledge is in the loader.
- Single-table `SELECT *` currently `resolved` in the list mocks — **pending Member 01**, do not change yet.

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
