# Component 2 — Agent Context (read this first, every task)

_Version 2 — 6 Oct 2026 (updated after the Stage 1 review)._

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
| 2 | Boundary inference | Stage 1 result → component tree, using `docs/php-analysis/boundary-rules.md` | **Next** |
| 3 | Data requirement recovery | per component: backward slice → fields needed | Not started (needs DFG) |
| 4 | Contract reconciliation | needs vs contract → matched / missing / unused | Not started (needs DFG + C3 schema) |
| 5 | Generation | Next.js **Server Components** (`.tsx`) + typed fetch layer | Later |

Inline JS / AJAX is **out of scope**: detect and flag it, never convert it.
Everything is generated as a Server Component, **by construction**.

## 3. Input format (Member 01's schema 1.0)

**Timeline** (`output-timeline.json`, mocks: `mocks/timeline_*.json`) — output nodes in source order.
Each sequence entry has a stable ID (e.g. `f012#00347`) in the field **`id`** (enclosures, queries and
label keys use `nodeId`; field naming pending Member 01's confirmation — the loader follows the mocks), nikic `kind` (`Stmt_Echo`, `Stmt_InlineHTML`,
`Expr_Print`), byte-exact `raw`, and:

- `enclosedBy`: list, **outermost → innermost**
  - loop: `{nodeId, kind, role: "iteration", iterExpr, iterSourceKind, valueVar, keyVar}`
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
9. **Commit and push** on `frontend-migration-` before any branch switch (`component2_frontend/` does not exist on `dev`).
10. Never edit HMS files with Git Bash `sed` (it converts CRLF → LF).

## 5. Corpus rules

| Group | Apps | Agent may |
|---|---|---|
| Rule discovery | HMS (commit `777fda4`, in `legacy-apps/hms`), WackoPicko (`github.com/adamdoupe/WackoPicko`, MIT — to be added to `legacy-apps/wackopicko`, pinned commit) | Read, build mocks, test |
| Evaluation | crud-php-mysqli, PHP-MySQL-CRUD-Application (+ more to come) | **Do not open, read or build mocks from these.** They are locked until Stage 2 is finished |

## 6. Repo state (as of 6 Oct 2026)

- Branch `frontend-migration-`, latest commit `3c0e249` (pushed). History was rewritten on 19 Sep
  (commit trailers removed): old `20d5e2a` = `fdc62b3`, `878df5c` = `1d91be0`, `c9c9535` = `a7cdedc`.
- Mocks: `timeline_{list_while,list_foreach,admin,detail,edge_cases}.json`, `labels_*.json` (5), `sample_contract.json`, legacy `sample_ast.json` (untouched).
- Deliberate contract gap: field **`contact`** (flagged on list pages only). `sku` appears in no schema.
- Exactly **one** `undecided` node across all mocks (edge-case file, reason `auth_or_display`).
- Tests: 82 passing (50 mock/fixture + 32 Stage 1). Extra fixtures: `tests/fixtures/{timeline,labels}_unlabelled.json`.
  Generator `tools/gen_timelines.py` needs PHP and HMS at `777fda4`.
- Stage 1 summary: list_while 35→32 ok/1 review/2 excl · list_foreach same · admin 18→15/0/3 · detail 27→24/1/2 · edge_cases 27→23/1/3.
  Summary semantics: `kept` = status ok; ok + review + excluded = total.
- `src/main.py` keeps dict-returning aliases only so the original 50 mock tests pass; all format knowledge is in the loader.
- Single-table `SELECT *` currently `resolved` in the list mocks — **pending Member 01**, do not change yet.

## 7. Boundary rules

See `docs/php-analysis/boundary-rules.md` (draft v0.1). Rules are **settings, not code**:
put rule thresholds (e.g. N = 4) in a config file so the owner can change them without code changes.
The rules are decided by the owner, not the agent. If code reveals a case no rule covers, **report it**;
do not invent a rule.

## 8. How to report back (every task)

1. Files created / changed
2. Test count and result (all must pass)
3. Commit hash, and whether it was pushed
4. **Choices you made where the spec was silent** — listed separately
5. Questions for the owner or for Member 01
