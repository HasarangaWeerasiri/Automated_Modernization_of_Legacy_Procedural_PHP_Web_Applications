# Component 2 — Known limitations (as recorded in the repository)

Every limitation below is already recorded in a committed file; each item names that file and section and quotes
or closely follows its wording. Nothing here is new: this is a collected list for the report. Where the repository
records no remedy, none is suggested here.

## 1. Rules v1.1 limitations (rules closed after post-freeze change 5)

v1.1 is closed: "a later finding is reported as a v1.1 limitation in Phase 5 / 6, not fixed"
(`docs/post-freeze-log.md`, change 5; `docs/generation-spec.md` §13, 18 Oct 2026).

| Limitation | Recorded in |
|---|---|
| A row count compared with a **quoted string** is not translated: `$cmnts == "0"` (phpBlog `core.php` 861, `admin/dashboard.php` 177). Conditions (a)–(d) hold, but "number vs string is not on the whitelist (§6), so they stay gaps — recorded as a v1.1 limitation, not fixed" | `docs/post-freeze-log.md` (change 5); `docs/phase4-results.md` §5.6 and §7 |
| **(d) failures:** 5 phpBlog list guards with a row count stay untranslated because "the List's query interpolates a value, so the replay serves no endpoint for it" (`blog.php` 29 `LIMIT $rows`, `category.php` 47, `post.php` 76, `search.php` 47, `my-comments.php` 29 `$user_id`); attendance `reset.php` 70 fails (b) and (d). Without a data source every guard fails (d): "in the five-app run (no endpoint map) all 12 fail (d) by construction" | `docs/post-freeze-log.md` (change 5); `docs/phase4-results.md` §5.6 |
| **`if`s that only set a variable printed later** (HMS `appsearch.php` 52 / 56 / 61, `$appstatus`) have no node: the value chosen by ifs is data flow. "The tool may miss it; that is a fair miss" | `docs/gold-standard-guide.md` §5 (hard cases); `docs/phase5r-rehearsal.md` §3.3 and §6 |
| Copy propagation is **same block only** and stops at any control statement between the copy and the print (`appsearch.php` reads stay `unresolved`) | `CONTEXT.md` §6 (Phase 2a: "same block only — strict on purpose; the real DFG is Member 01's"); `docs/phase5r-rehearsal.md` §5 item 6 |

## 2. Redirect-guarded pages render their content

An R-I8 redirect guard is not a component: "the page renders as if not redirected; the redirect is C1's, flagged"
(`redirect_guard`, review) — at any depth in a page (`docs/php-analysis/boundary-rules.md` §3, R-I8, scope v0.15).
In the phpBlog runtime check, of the 36 pages whose legacy side redirects an anonymous GET, "the generated side renders
35 of them (R-I8 redirect guard: the redirect is Component 1's, flagged `redirect_guard`)"
(`docs/phase4-results.md` §5). The same simplification applies to R-I1 page guards whose other path redirects
(`boundary-rules.md` §3, R-I1: "the condition is form/redirect logic owned by Component 1"). **No remedy is recorded in
the repository** (where the redirect is enforced belongs to Component 1).

## 3. The comparison's attribution and the hand-reviewed links

| Limitation | Recorded in |
|---|---|
| **Approximate region attribution.** The classifier ties some missing subtrees to a gap by source order, "at a node that is not the one hiding them — the class is right, the node is not". phpBlog fixture run: of 1558 "List elements", 1128 are not List elements (the hidden sidebar, 1122; database HTML and form markup, 6) tied to the menu's gap by order; "**None is a boundary error.**" | `docs/phase4-results.md` §5.7 and §7; `CONTEXT.md` §6 (Phase 4g) |
| **One attribution corrected by hand:** Phase 4e's "35 at `core.php` 652, 2 at `blog.php` 29" is 36 / 1 — the menu icon was tied to `blog.php`'s `else` arm; class and total unchanged, classifier not changed | `docs/phase4-results.md` §5.3; `tools/runtime/phpblog/findings.json` (`unsoundDecisions[0].correction`) |
| **Hand-reviewed links.** Differences the classifier cannot tie to a flag mechanically are reviewed by hand and recorded with their evidence: phpBlog 10 reviewed links (`flag:` class: 1 seed, 2 seed + fixture, 7 fixture) and 1 page-wide `env:legacy_runtime_error` finding; HMS 2 noted root causes (v1.0 `search.php`, left UNEXPLAINED under v1.0) | `tools/runtime/phpblog/findings.json`; `tools/runtime/hms/findings.json`; `docs/phase4-results.md` §5.2, §5.5 |
| The UNEXPLAINED count reached 0 on phpBlog through classifier rules **and** hand review (the last 9 of the seed run were reviewed by hand) | `docs/phase4-results.md` §5.5 |

## 4. The harness stands in for Member 01's core (roadmap row 7)

"The harness is a **test harness, not Member 01's core**" (`CONTEXT.md` §3). For the whole-app results: "**Input comes from the C2
test harness, not from Member 01's core**: no labels (every node is unlabelled, so Stage 2 marks it `review`), no
contract and no endpoint map (every page is `no_endpoint`)" (`docs/c2-apps-results.md`, header). Roadmap row 7 (real
integration) is "partly" done: "real AST for 1 page arrived 12 Oct (used in 1b). Timeline/labels/contract do not exist
yet"; if they do not arrive, "row 7 is closed as 'blocked — harness used', and the report says so plainly"
(`docs/c2-roadmap.md` §1, row 7 and its fallback). Harness file ids differ from Member 01's (Member 01 Q18,
`CONTEXT.md` §6, Phase 2a).

## 5. Data wiring without Component 1's contract

| Limitation | Recorded in |
|---|---|
| The evaluation apps have no contracts: data wiring (Stage 4 mapping, where fetches happen) "is not measured in Phase 5" | `CONTEXT.md` §6 ("What the freeze covers") |
| "**Replay data only for literal SELECTs** of pages and function components (HMS 7 of 16 Lists, phpBlog 29 of 43); a shared shell's queries (phpBlog's menu) are not served" — e.g. the phpBlog shell menu (336 List elements) is a data-wiring gap (`no_endpoint`) | `docs/phase4-results.md` §7 and §5.7 |
| "**The replay API is a test fixture**, not Component 1's contract; the real contract may name and shape data differently (Stage 4 then reports it)" | `docs/phase4-results.md` §7 |
| Stage 4's real `SELECT *` resolution needs C3's schema; real evidence needs C1's contract and endpoint map; the `contact` contract gap is planted (the agent wrote the substitute contract and map) | `CONTEXT.md` §2 (Stage 4) and §6 |

## 6. Other recorded limits of the measurements

| Limitation | Recorded in |
|---|---|
| **Two discovery apps** in the runtime check; "it is not the evaluation". WackoPicko needs PHP 5 (`mysql_*`) and was not run | `docs/phase4-results.md` §7 |
| **GET only, no session**: nobody logs in, nothing is posted; redirecting pages are not compared; behaviour after a POST or with a session is not measured (Component 4) | `docs/phase4-results.md` §7 |
| **Element trees, not pixels**: CSS is compared only in the simulation screenshots | `docs/phase4-results.md` §7 |
| **Seeds**: phpBlog's shipped seed has no posts; the recorded fixture adds a few rows; post / category / page are compared only through 6 parameterised GETs with the fixture | `docs/phase4-results.md` §7 |
| Inline JS / AJAX is out of scope: "detect and flag it, never convert it" | `CONTEXT.md` §2 |
| The HMS rehearsal numbers are **not evaluation results**: HMS is a discovery app and the practice keys were drafted with knowledge of the rules, so they are biased upward | `CONTEXT.md` §6 (Phase 5R2); `docs/phase5r-rehearsal.md` |
| Every file is a page unit: POST endpoints and include-only files are generated as (empty) pages | `docs/phase5r-rehearsal.md` §5 item 5 |

## 7. Links between generated pages

Links and form actions to `.php` pages are "kept as literal strings. Flag `legacy_link` (info; routing is not C2's
job)" (`docs/generation-spec.md` §5), while a page's route has no `.php` (§8a): in the generated app a click on a
legacy link finds no route. The opt-in routing layer (`docs/routing.md`, not part of the pipeline, **not measured**)
serves the legacy URLs with the routes for a demonstration; it does not cover:

| Limitation | Recorded in |
|---|---|
| Links built by a PHP expression are reached only if the page prints the same URL; links to files with no page (`noPage`: not generated, crashed, outside the document root), to files the app does not have, or to static files stay broken (404) | `docs/routing.md` §2, §6 |
| `.htaccess` / server rewrite rules of the legacy app are not read | `docs/routing.md` §6 |
| A link reaches a page, but a POST, a login or a redirect needs Component 1 | `docs/routing.md` §6; `docs/phase4-results.md` §7 |

## 8. Styles and script behaviour of the generated pages

A plain page's `<head>` is dropped (`document_shell`) and every `<script>` is dropped (`inline_js_dropped`: "detect
and flag it, never convert it", `CONTEXT.md` §2), so the generated pages show the legacy markup without the
legacy stylesheets (plain pages) and without the legacy script behaviour (e.g. Bootstrap tabs). The opt-in assets
layer (`docs/assets-layer.md`, owner decision 18 Oct 2026, not part of the pipeline, **not measured**) loads the
legacy page's own stylesheets and script files as they were, for a demonstration; it does not cover:

| Limitation | Recorded in |
|---|---|
| Inline scripts (`<script>…</script>`, `onclick=`) are never converted; the `--inline-js` option runs them verbatim, but skips (and lists) code under an `if` / `else` / `switch`, code or handlers built by PHP, `document.write`, and ambiguous handlers | `docs/assets-layer.md` §6, §7 |
| PHP-valued handlers (`onclick="del(<?= $id ?>)"`) would need Stage 5 to translate the attribute (a pipeline change; future work after Phase 5) | `docs/assets-layer.md` §7 |
| DOM-based XSS patterns in static legacy JS are listed (`dom_sink`), not fixed | `docs/assets-layer.md` §7 |
| Stylesheet / script tags built by PHP and includes by an expression are skipped; the scan is file-level (a tag under a condition is collected whatever the condition) | `docs/assets-layer.md` §2, §6 |
| Scripts from the legacy `<head>` load at the end of `<body>`; legacy scripts change DOM that React rendered (safe for Server Components without client state, not in general) | `docs/assets-layer.md` §6 |
