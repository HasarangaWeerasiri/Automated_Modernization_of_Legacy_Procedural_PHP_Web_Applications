# Component 2 — Roadmap to 100% (v2.4)

| Item | Value |
|---|---|
| Date | 12 October 2026 |
| Owner | Jayawardhana R D L L (IT23213876) |
| Rule | A phase is **done** only when its exit check passes and the result is committed. No phase is "done on mocks only" |

## 1. What 100% means for C2

C2 is 100% when every row below is ✅. Rows 0–6 depend only on C2. Row 7 depends on Member 01 / C1;
it has a fallback so C2 can still finish.

| # | Phase | Exit check | Depends on | Status (16 Oct) |
|---|---|---|---|---|
| 0 | Stages 1–5 on mocks | 573 tests, 0 strict `tsc` errors, every gap visible | — | ✅ done |
| 1 | **Whole-app harness** | Every page of HMS and WackoPicko runs through Stages 1–5 with no crash; each page compiles or each failure is explained; pattern log written | C2 only | ✅ done 12 Oct (66 timelines, 0 crashes, 0 tsc errors, 9 new patterns) |
| 1b | **Align with Member 01's real AST** | Harness reproduces the real `admin-panel1.php` AST **exactly** (all 602 nodes: id, kind, parent, children, fields, loc); harness and mocks use Member 01's ids, `loc` convention, `call` entries and `SELECT *` rule; loader reads the real AST | Real AST (arrived 12 Oct) | ✅ done 13 Oct (602/602, harness AST byte-identical to Member 01's; 706 tests) |
| 2a | Echo-string parts, copy propagation, interpolated-SQL values, R-L3b, module fix | HMS one-echo rows generate as Lists/Items (no `no_rule` abstains for them); every generated file is a module; checker override removed | Phase 1b | ✅ done 13 Oct (HMS `no_rule` 4 → 0, unresolved reads 75 → 27; 779 tests) |
| 2b | Functions and shell (R-F1, R-F2) | Function output becomes components at the call site; WackoPicko header/footer becomes root layout + shell component; `document_shell` and the `html_functions` route clash disappear for WackoPicko; browser-equivalent markup (spec §5b) leaves only foster-parenting nesting flags | Phase 2a | ✅ done 13 Oct (both apps one Next.js app, 0 tsc errors, `next build` ok; nesting 9 → 5; 826 tests) |
| 3 | Rule saturation and **freeze** | phpBlog, then attendance, run through the harness; 2 consecutive apps add no new pattern (definition: boundary-rules §8) → rules doc header says **Frozen** | Phases 1–2 | ✅ **Frozen 15 Oct** (boundary-rules v1.0): phpBlog 4 new → 0 / 2; attendance none → 1 / 2; warehouse none → **2 / 2**. Step 5 committed = freeze commit (five-app totals at freeze in `docs/c2-apps-results.md`) |
| 4 | Runtime rendering check (SO4, measured) | HMS runs in Docker (PHP + MySQL, its own `myhmsdb.sql`); the generated pages run with the same data; rendered DOM compared; every difference is explained by a flag. Unexplained difference = bug | Phase 2 | ✅ HMS closed 16 Oct (v1.0 **0.947** → v1.1 final **0.996**, 0 unexplained, 1 unsound decision found and fixed; `docs/phase4-results.md`). ⏳ 4e: second runtime app (phpBlog) |
| 5 | Evaluation on the 5 locked apps | Owner's **gold standard committed before** the tool runs; metrics: boundary precision/recall, **unsound decisions (target 0)**, abstain/flag rate, compile rate, render match where runnable | Phase 3 freeze | ⬜ — **owner starts the gold standard now** (docs/gold-standard-guide.md), in parallel with Phase 4 |
| 6 | Report chapter + appendices | C2 method, results (from `tools/results_table.py`), honest limits, Appendix E decision logs, Appendix F AI-use disclosure | Phases 1–5 | ⬜ |
| 7 | Real integration | Member 01's timeline and C1's labels/contract replace the harness and substitutes for at least one HMS page; differences vs the harness explained | Member 01, C1 | 🟡 partly: real AST for 1 page arrived 12 Oct (used in 1b). Timeline/labels/contract do not exist yet |

**Fallback for row 7:** if the real inputs have not arrived by the date the owner sets (write it here: `____`),
row 7 is closed as "blocked — harness used", and the report says so plainly. The loader isolation (hard rule 1)
means the swap later is a loader change only.

## 2. Why this order

- The whole-app harness comes first because **every later phase needs whole apps**: saturation must be judged
  on whole apps (excerpts were chosen by us and hide patterns), R-F rules need real function/include evidence,
  and evaluation needs a way to run apps without Member 01.
- The harness is a **test harness, not Member 01's core**. It emits schema 1.0 (plus our marked extensions),
  so when Member 01's real output arrives nothing past the loader changes. Tell Member 01 it exists so there
  is no overlap or confusion.
- The gold standard (Phase 5) is written **by the owner, by hand, before** the tool runs on an evaluation app,
  and committed with a date. Otherwise the evaluation is not independent.

## 3. Phase 5 metrics (fixed now, before any evaluation app is opened)

| Metric | Definition |
|---|---|
| Boundary precision | generated components that match a gold component / generated components (excluding abstains) |
| Boundary recall | gold components matched / gold components |
| **Unsound decisions** | generated (non-abstained) components or data bindings that are **wrong** vs gold. The sound-or-abstain claim holds only if this is 0 |
| Abstain rate | abstained regions / gold components |
| Flag rate | todo + review per page (spec v0.4 §12) |
| Compile rate | pages with 0 strict `tsc` errors / pages |
| Render match | where the app runs: matched elements / legacy elements in generated regions (Phase 4 method) |

## 4. Change log

| Date | Change |
|---|---|
| 12 Oct 2026 | v1.0 — roadmap created; 3 evaluation apps added and 2 discovery apps queued by random split (rules §7) |
| 12 Oct 2026 | v1.1 — Phase 1 done; Phase 2 split into 2a (input normalisation from the pattern log) and 2b (R-F1/R-F2) |
| 12 Oct 2026 | v1.2 — Member 01 answers + real AST arrived; Phase 1b (alignment) inserted before 2a |
| 13 Oct 2026 | v1.3 — Phase 1b done |
| 13 Oct 2026 | v1.4 — Phase 2a done; 2b also takes the 2a follow-ups (per-part ids, browser-equivalent markup, R-I7) |
| 13 Oct 2026 | v1.5 — Phase 2b done; Phase 3 starts with phpBlog |
| 13 Oct 2026 | v1.6 — phpBlog judged: 4 new patterns, saturation counter 0 / 2 |
| 14 Oct 2026 | v1.7 — Phase 3 step 2 done; attendance opened |
| 14 Oct 2026 | v1.8 — attendance judged: no new pattern, counter 1 / 2; reserve app opened |
| 15 Oct 2026 | v1.9 — warehouse judged: no new pattern → **rules frozen**; gold standard starts; Phase 4 next |
| 15 Oct 2026 | v2.0 — freeze committed; evaluation apps packaged for the owner's annotation; Phase 4a starts |
| 15 Oct 2026 | v2.1 — Phase 4a done; freeze scope made explicit (data wiring may change, guarded) |
| 16 Oct 2026 | v2.2 — Phase 4b done; post-freeze changes 1–2 (rules v1.1, spec v0.14) from discovery evidence; Phase 5 will report v1.0 and v1.1 |
| 16 Oct 2026 | v2.3 — Phase 4c done; change 3 (spec v0.15) makes v1.1 final; Phase 5 mapping + scorer prepared |
| 16 Oct 2026 | v2.4 — Phase 4d done (HMS 0.996); scorer tested; 4e adds phpBlog runtime while the gold standard is written |
