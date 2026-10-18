# Component 2 — Routing layer (opt-in deployment aid, not measured)

| Item | Value |
|---|---|
| Date | 18 October 2026 (after Phase 6E, before the Phase 5 evaluation run) |
| Tool | `tools/routing/routes.py`; `tools/runtime/generated_app.py --routes` |
| Tests | `tests/test_routing.py` |
| Status | **Not part of the pipeline, not a post-freeze change, not measured.** Off unless asked for |

## 1. Why

A generated page keeps its links to other pages as they were in the PHP (`href="logout.php"`): "Links / form
actions to `.php` pages: kept as literal strings. Flag `legacy_link` (info; routing is not C2's job)"
(`docs/generation-spec.md` §5). Its route is its path from the document root without `.php`
(generation-spec v0.7 §8a). The generated app therefore has every page, but a click on a legacy link
(`/logout.php`) finds no route: Next.js serves `/logout`.

The routing layer serves the legacy URLs with the generated routes, **without changing any page**. The decision
above stands: the pipeline still keeps links literal and still flags `legacy_link`; the layer is added afterwards
to a build, for a demonstration or a deployment.

## 2. What it does

It reads the run's `index.json` only (no stage, rule, timeline or source file) and writes a route table and the
matching `next.config.ts` of a build project (`output/test-app/c2-build/<name>/`):

| Legacy file | Rule | Why |
|---|---|---|
| a page `p.php` (route `/p`) | rewrite `/p.php` → `/p` | the browser keeps the legacy URL, so relative links resolve as they did on the PHP server; the query string is kept |
| a folder's index `admin/index.php` (route `/admin`) | rewrite `/admin/index.php` → `/admin`, and redirect `/admin` → `/admin/index.php` (temporary) | on the PHP server a relative link on `/admin/` resolves inside `admin/`; on `/admin` it would not. The root index needs no redirect |
| a file with no generated page | no rule; listed under `noPage` with its reason | not generated (spec v0.8 §6a, e.g. `non_html_response`, `error_output_only`), crashed, or outside the document root |
| a path that is not a literal URL (path-to-regexp syntax, spaces) | refused (`RouteError`) | never escaped by guess; none of the five discovery apps has one |

The output is deterministic (sorted, no date). Discovery apps (from `output/apps/<app>/index.json`):

| App | Rewrites | Redirects | Files with no page |
|---|---|---|---|
| HMS | 26 | 0 | none |
| phpBlog | 43 | 2 | `core.php`, `install/core.php` (`error_output_only`); `rss.php`, `sitemap.php` (`non_html_response`) |
| attendance | 14 | 3 | `connect.php`, `admin/`, `student/`, `teacher/connect.php` (`error_output_only`) |
| warehouse | 29 | 0 | `ajax.php` (`non_html_response`) |
| WackoPicko | 30 | 0 | none (document root `website/`) |

## 3. How to use it

From `component2_frontend/`, after a build exists:

```
venv/Scripts/python.exe tools/routing/routes.py <index.json> --build <name>
```

writes `next.config.ts` and `routes.json` into `output/test-app/c2-build/<name>/` and runs `next build` there
again. Or, for the runtime check's app: `tools/runtime/generated_app.py <app> --routes` (Stages 1–5, build, then
the layer). Without `--build`, the tool only prints the table (`--out routes.json` writes it).

## 4. Why it cannot change a result

- It lives outside the pipeline (`src/`, `config/`, `tools/harness/`), so `run_eval.py` records `codeChanges: []`;
  no pipeline or evaluation file imports it (`tests/test_routing.py`).
- It is off by default: `compile_check.next_build` copies the test app's plain `next.config.ts` (no rewrites);
  only `--build` / `--routes` add the layer, to a build project, never to `output/apps/<app>/next-app/` or a
  stage output.
- Guard proof (18 Oct 2026, with the layer in the tree): `tools/rules_guard.py apps --rules v1.0` → five apps 2,079
  files `ba8e4ac7…25d9`, byte-identical to the freeze 27c380e; `tools/rules_guard.py mocks --rules v1.0` →
  `71b746eb…0cf6`, byte-identical; `git status` shows no change under `src/`, `config/`, `tools/harness/`. Tests:
  1,232 passed (1,217 + 15).
- **Phase 5:** keep it off. Eval-mapping §4 item 2: the tool runs "no other setting changed"; links are not
  scored. The Phase 5 runtime check (Phase 4) also ran without it.

## 5. HMS demonstration (18 Oct 2026)

A copy of the Phase 4 HMS build (`hms-final-runtime`, rules v1.1-final, replay data) as
`c2-build/hms-routes-runtime`, with the layer: `next build` ok; 26 rewrites.

| Request | Result |
|---|---|
| `/index.php`, `/admin-panel.php`, `/admin-panel1.php?x=1`, `/doctor-panel.php`, `/logout.php`, `/logout1.php`, `/index1.php` | 200; the page HTML (scripts aside) is the same as at its route (`/admin-panel.php` = `/admin-panel`, `/doctor-panel.php` = `/doctor-panel`) |
| click `logout.php` on `/admin-panel.php` (headless Chromium) | `/logout.php`: "You have logged out. Back to Login Page" |
| click `logout1.php` on `/doctor-panel.php` | `/logout1.php`: "You have logged out. Back to Home Page" |
| click `index1.php` on `/index.php` | `/index1.php`: the Patient Login page |
| `/dashboard.php`, `/services.html` | 404: HMS has no such file (links in its templates) |

The original build `hms-final-runtime` was not touched (Phase 4's results stay as recorded).

## 6. What it does not do (limits)

- **Links built by a PHP expression** (`href="post.php?id=<?= $id ?>"`) are reached only if the generated page
  prints the same URL; the layer rewrites paths, it does not translate links.
- **Links to files with no page** (`noPage`), to files the app does not have, or to static files (`.html`, CSS,
  images; HMS's `document_shell`) stay broken: 404. (Static files: the assets layer serves them,
  `docs/assets-layer.md`, also opt-in and not measured.)
- **`.htaccess` / server rewrite rules** of the legacy app are not read.
- **Forms and session:** a link reaches a page, but a POST to it, a login or a redirect is Component 1's
  (form actions are kept literal; nothing is posted in Phase 4).
- **Inline JS** (e.g. Bootstrap tabs, `#list-pat`) is not converted ("detect and flag it, never convert it");
  a `#` link changes nothing here. The assets layer (`docs/assets-layer.md`) loads the legacy script files as they
  were, so Bootstrap tabs work again; inline scripts stay dropped.
- No "link coverage" number is measured or reported as a result.
