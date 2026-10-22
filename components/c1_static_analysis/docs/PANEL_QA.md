# Panel questions: Input · Process · Output · Verification (Component 1)

These are short answers to say out loud, plus the exact code to open if the panel asks "show me how it's done". All paths are under `components/c1_static_analysis/`.

---

## 1. Input

**Say:** "The input is the source folder of any legacy procedural PHP app. The database dump (`.sql`) is optional, and gives us column types. Nothing else is needed: no running server, no database connection. The legacy app is only read, never changed."

**Show:** `python -m c1 run "<app folder>" --out <folder> --schema <dump.sql>`

| Ask | Open |
|---|---|
| "How do you read PHP?" | `tools/php_ast/extract_ast.php` line 238: nikic/PHP-Parser builds the syntax tree. `src/c1/extract.py` line 119 `extract_tree()` walks the folder, numbers every node and writes `ast/<file>.json` and `manifest.json` (with a hash of the whole input). |

---

## 2. Process

**Say:** "Twelve stages. Each reads the previous stage's files and writes its own, so every step can be inspected."

| # | Stage | One line to say | Code to open |
|---|---|---|---|
| 1 | Parse | PHP → syntax tree with stable node ids | `extract.py:119` |
| 2 | Control flow | one graph per page/function; `exit`/`die` end a path | `cfg.py:692` `build_project_cfgs` |
| — | Dominance | "a check dominates a line if every path to it passes the check" (Cooper–Harvey–Kennedy) | `dominance.py:41` `immediate_dominators` |
| 3 | Includes | which file each `include` loads (Hills et al. 2014) | `includes.py` |
| 4 | Data flow | where every value comes from: request input, session, DB column | `dfg.py:405` `class Origins`, `:531` `of_expr` |
| 5 | Concern labels | 19 rules label each statement view / logic / data | `concerns.py:363` `build_labels` |
| 6 | Login analysis | login checks, guard helpers, page protection by dominance, redirect-without-exit | `auth.py:379` `guard_points`, `:98` `point_dominates`, `:534` `ear` |
| 7 | Endpoints | form handlers + SQL → method, path, params, response | `endpoints.py:302` `recover`, `:523` `endpoint_for` |
| 8 | Contract + backend | OpenAPI 3.1, FastAPI routes, Pydantic models, JWT | `contract.py:61` `build_openapi` |
| 9 | Metrics | before/after security and tangling | `metrics.py:64`, `:158` |
| 10 | Architecture | scores 3 architectures with reasons | `architecture.py:71` `areas`, `:130` `score` |
| 11 | Timeline | page output order for C2 | `timeline.py` |
| 12 | Report | one HTML page for review | `report.py` |

**If asked to trace one example:** open `endpoint-map.json` and pick `POST /api/contact`. Its `code` field gives `contact.php:11`. Open that PHP line, then `contract/openapi.json` (the same path), then `backend/app/routes.py` (the same route). This shows **traceability from the PHP line to the API**.

---

## 3. Output

**Say:** "The main outputs are the OpenAPI 3.1 contract and a FastAPI backend with JWT login. Alongside them: the endpoint map for C4, the timeline and labels for C2, the data-access stubs for C3, a flag list of everything a person must decide, the before/after metrics, the architecture suggestion and an HTML report."

| File | For |
|---|---|
| `contract/openapi.json` | the new API contract |
| `backend/app/` (`routes.py`, `models.py`, `auth.py`, `data_access.py`) | the generated backend (runs with uvicorn) |
| `endpoint-map.json` | C4 (testing) |
| `labels.json`, `timeline/` | C2 (frontend) |
| `flags.json` | items a person must decide |
| `metrics.json`, `architecture.json`, `report.html` | results and review |

---

## 4. How the output is verified, deterministically

**Never say "I checked it by hand".** Everything below is automatic, or comes from an independent source.

**Say:** "Verification is automatic, at four levels."

1. **Same input → same output (determinism).**
   - Every run ends with an output fingerprint, a SHA-256 hash over every output file.
   - An automated test runs the whole pipeline twice, in different folders, and checks that the outputs are byte-identical (`tests/test_pipeline_e2e.py:63`). The CI workflow (`ci/c1-determinism.yml`) does the same on every push.
   - Every output file also records the hash of the input it was built from (`generatedFor`), so results from different inputs can never be mixed.
   - **Demo:** run twice, same fingerprint.

2. **Each algorithm is checked against known answers (159 automated tests).**
   - Each test is a small PHP snippet with the result written down in advance (e.g. "this page has no login check", "`cpassword` must equal `password`").
   - The dominance algorithm is compared against an **independent library (networkx) on 300 random graphs**, and they agree on all of them (`tests/test_dominance.py:33`).
   - Every control-flow graph is checked structurally (`cfg.py:629` `validate_unit`): every statement appears exactly once. The result is 0 problems on all 4 apps.
   - **Command:** `python -m pytest -q` → 159 passed.

3. **The generated backend is checked against its own contract.**
   - A test starts the generated FastAPI app and checks that the API it serves is **identical to the generated OpenAPI contract**.
   - It also checks that a protected endpoint returns **401 without a token**, and that the token's user reaches the data layer (`tests/test_contract.py:62`).

4. **Independent checks (not by me).**
   - C2's and C4's hand-made samples were written independently. Our output matched them (C2's timeline: 29/29 entries).
   - **C4 is the behavioural check:** it sends the same requests to the old and the new system and compares the results, driven by our endpoint map.
   - **Accuracy:** two other people label 200 random statements without seeing our answers. We measure their agreement (Cohen's kappa) and score the tool against them. *(Planned, not done yet.)*

---

## If they say "show me in the code"

1. Stay calm and open **`src/c1/__main__.py`**. It lists all the stages in order (`_steps`), which is a map of the whole tool.
2. Follow the stage they ask about, using the table in section 2.
3. For "how do you know a page is protected?", open `auth.py:98` `point_dominates`: *"a guard protects a line only if it dominates it, i.e. every path to that line passes through the guard."*
4. For "how is it deterministic?", open `__main__.py:74` `fingerprint()` and `extract.py` `canonical_json` (sorted keys, fixed format).

## Honest "not done yet" (say it before they ask)

- The accuracy study needs two labellers.
- Only the layered architecture is generated; the others come in PP2.
- The data layer is filled by C3.
- Full old-vs-new behaviour comparison is C4's.
