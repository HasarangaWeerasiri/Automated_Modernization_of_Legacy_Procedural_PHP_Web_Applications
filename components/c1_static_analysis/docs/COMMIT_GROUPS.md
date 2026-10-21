# Commit groups (for you to commit, in order)

Run the commands from `components/c1_static_analysis/`. Each group only lists its own files. README, DEVLOG and this file go last, in the docs group.

Before each commit, run `python -m pytest -q` (on Windows, PHP-based tests run too).

---

## Group 1 — fix(extract): keep default excluded dirs
```
git add src/c1/extract.py
git commit -m "fix(extract): keep .git and node_modules excluded with --exclude-dir" -m "Passing --exclude-dir replaced the default list, so .git and node_modules
were parsed again. Given folders are now added to the defaults."
```

## Group 2 — feat(cfg): control-flow graphs + dominators
```
git add src/c1/astdoc.py src/c1/dominance.py src/c1/cfg.py docs/DESIGN_CFG.md tests/conftest.py tests/test_cfg.py tests/test_dominance.py tests/fixtures/ast_cache
git commit -m "feat(cfg): build control-flow graphs and dominator trees" -m "- One CFG per page, function, method and closure, with labelled edges
- exit/die end a path; \`X or die()\` becomes a real branch
- Calls to always-exiting helpers (e.g. http_redirect) cut the path
- Dominators by Cooper-Harvey-Kennedy, checked against networkx
- Cached ASTs in tests/fixtures so the tests run without PHP"
```
Note: `tests/fixtures/ast_cache/` is cached parser output for test snippets, so the tests run without PHP. Later groups add more files there. Committing them early does no harm.

## Group 3 — feat(includes): include resolver
```
git add src/c1/includes.py tests/test_includes.py
git commit -m "feat(includes): resolve include/require targets per entry page" -m "- PHP manual lookup order: include_path, caller dir, then cwd
- Path folding for dynamic includes (Hills et al. 2014)
- Includes that cannot be resolved are flagged, not guessed"
```

## Group 4 — feat(dfg): reaching definitions, use-def chains, origins
```
git add src/c1/dfg.py tests/test_dfg.py docs/DESIGN_INCLUDES_DFG.md
git commit -m "feat(dfg): trace where every value comes from" -m "- Reaching definitions and use-def chains over the CFG
- Origins: request input, session, cookie, DB query column
- Return summaries for functions and static methods
- extract(), eval() and unresolved includes reset what is known"
```

## Group 5 — feat(concerns): concern labels (labels.json)
```
git add src/c1/concerns.py tests/pipeline.py tests/test_concerns.py
git commit -m "feat(concerns): label statements as presentation, logic or data access" -m "- 19 rules (R01-R19), written in C2's labels.json format
- Mixed or unclear statements abstain instead of guessing
- Query-builder/ORM calls are flagged (R19), never silently missed"
```

## Group 6 — feat(auth): authorization gates and page protection (auth.json)
```
git add src/c1/auth.py tests/test_auth.py docs/DESIGN_CONCERNS_AUTH.md
git commit -m "feat(auth): detect login/role checks and find unprotected pages" -m "- Classify session checks: authentication, role, ownership
- Find guard helper functions and guard include files
- A page counts as protected only if a guard dominates its data access
- Detect redirect without exit (EAR, Doupe et al. 2011)"
```

## Group 7 — feat(endpoints): SQL shape, endpoint recovery, response shape, endpoint map
```
git add src/c1/sqlshape.py src/c1/endpoints.py tests/test_endpoints.py
git commit -m "feat(endpoints): recover REST endpoints from form handlers and queries" -m "- Rebuild SQL templates from the DFG, including sprintf
- Map CRUD to HTTP method + path; /api/me/... and /api/auth/login
- Responses hold only the columns the page uses, typed from the .sql dump
- Write endpoint-map.json in C4's format"
```

## Group 8 — feat(contract): OpenAPI 3.1 + FastAPI/Pydantic backend + flags
```
git add src/c1/contract.py tests/test_contract.py docs/DESIGN_ENDPOINTS_CONTRACT.md
git commit -m "feat(contract): generate OpenAPI 3.1 and a FastAPI backend skeleton" -m "- Pydantic models, FastAPI routes and JWT auth (python-jose)
- Data-access stubs for C3, each carrying the legacy SQL
- flags.json lists everything that needs human review
- Test boots the generated app: served schema equals the contract"
```
Note: `tests/test_contract.py` needs `pip install fastapi "python-jose[cryptography]" httpx` for the boot test (it skips without them). Add them to requirements.txt if you want that test in CI.

## Group 9 — feat(timeline): output timeline for C2 + HTML report + one-command pipeline
```
git add src/c1/timeline.py src/c1/report.py src/c1/__main__.py src/c1/console.py src/c1/metrics.py src/c1/architecture.py tests/test_timeline.py tests/test_pipeline_e2e.py tests/test_console.py tests/test_metrics_architecture.py docs/DESIGN_TIMELINE.md docs/DESIGN_METRICS_ARCHITECTURE.md
git commit -m "feat(timeline): add C2 output timelines, HTML report and one-command run" -m "- timeline/<fileId>.json: output order, enclosures and typed reads
- report.html: pages, guards, endpoints and items to review
- \`python -m c1 run <app>\` runs every stage, with a colourful console
- End-to-end test; output is byte-identical across runs
- Before/after metrics: attack surface (CWE-862/698/89) and concern tangling
- Architecture advisor: scores layered / modular monolith / microservices
- Output fingerprint printed after each run"
```

## Group 10 — feat(evaluate): evaluation harness + labelling sheet + benchmark notes
```
git add src/c1/evaluate.py tests/test_evaluate.py docs/eval docs/BENCHMARKS.md
git commit -m "feat(evaluate): add a labelling sheet and accuracy scoring" -m "- Stratified 200-statement sheet with our labels hidden
- Scores coverage, accuracy, per-class P/R/F1 and a confusion matrix
- Benchmark notes for the 4 apps"
```

## Group 11 (last) — docs: README, dev log, commit groups
```
git add README.md docs/DEVLOG.md docs/COMMIT_GROUPS.md docs/PP1_PROGRESS.md docs/PP1_DEMO_SCRIPT.md pyproject.toml ci/c1-determinism.yml install.bat install.sh tools/where_scripts.py
git commit -m "docs(c1): document every stage, the dev log and the commit plan" -m "- README covers all stages and benchmark results
- Add DEVLOG, COMMIT_GROUPS and the PP1 progress summary
- Require Python >=3.10; CI runs the pipeline twice and diffs
- One-click setup (install.bat / install.sh); run from any folder"
```

## Group 12 — feat(contract): send "/" to the API docs
```
git add src/c1/contract.py
git commit -m "feat(contract): redirect / to /docs in the generated backend" -m "- Opening http://localhost:8000/ showed {\"detail\":\"Not Found\"}; it now opens the docs
- The route is hidden from the OpenAPI schema, so the contract is unchanged"
```

## Group 13 — feat(endpoints): endpoint map fields for C4
```
git add src/c1/endpoints.py tests/test_endpoints.py tests/fixtures/ast_cache docs/DESIGN_ENDPOINTS_CONTRACT.md
git commit -m "feat(endpoints): add search result tables, same_as fields, login user types and response columns to the endpoint map" -m "- Search handlers report records only/unknown instead of none (C4 item 6)
- Confirmation fields (cpassword == password) carry same_as (item 9)
- Login forms: user type, fields, session keys, redirects; endpoints: user types; token location (item 8)
- response_fields map each field to table.column, joins resolved (item 10)
- Endpoints on tables missing from the schema are flagged table-not-in-schema"
```

---
Checked on 2026-10-17: committing groups 1→11 in order on top of your last push, the tests pass after **every** group (20, 55, 68, 90, 101, 115, 127, 131, 147, 150, 150 tests; console added 2026-10-19). On 2026-10-21 metrics + architecture were added to group 9; the full suite is now 155 (the per-group counts for 9–12 were not re-checked after that). So any prefix of the groups is safe to push.
