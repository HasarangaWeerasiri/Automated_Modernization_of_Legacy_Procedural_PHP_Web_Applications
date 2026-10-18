# Component 2 — Assets layer (opt-in deployment aid, not measured)

| Item | Value |
|---|---|
| Date | 18 October 2026 (after the routing layer `ff820a6`, before the Phase 5 evaluation run) |
| Decision | **Owner, 18 Oct 2026:** add it as an opt-in layer after Stage 5, like the routing layer (option A). The pipeline's rule for scripts stands: "detect and flag it, never convert it" (CONTEXT §2) |
| Tool | `tools/assets/legacy_assets.py`; `tools/runtime/generated_app.py --assets` |
| Tests | `tests/test_assets.py` |
| Status | **Not part of the pipeline, not a post-freeze change, not measured.** Off unless asked for |

## 1. Why

The generated pages keep the legacy markup (classes, ids, `data-toggle="tab"`), but not what makes it look and
behave as it did:

- a plain page's `<head>` is dropped ("`<head>` and its content dropped", flag `document_shell`), so its
  stylesheets are not loaded (a shell keeps its `<head>` links in its layout, generation-spec §8a);
- every `<script>` is dropped (flag `inline_js_dropped`), shells included: in the discovery builds, 0 `<script src>`
  remain (phpBlog, warehouse, WackoPicko, attendance, HMS);
- the legacy app's static files (CSS, JS, images, fonts) are not served.

So on HMS `index.php` the Patient / Doctor / Receptionist tabs (Bootstrap `data-toggle="tab"`) show all three forms
at once, unstyled, and a click changes nothing.

## 2. What it does

The browser loads **the legacy files, as they were**: no JavaScript is converted, rewritten or run by the tool.

| Step | Rule |
|---|---|
| Read | the run's `index.json` (pages, groups, shells, functions) and the legacy PHP source in the pinned clone (`legacy-apps/<app>/`), through PHP's own tokenizer (`tools/tok.php`). Never a timeline, a stage output or Member 01's JSON |
| Scopes, per generated page | the page file and the files it includes by one literal path (recursively), **top-level code only** (function and class bodies left out); for a page in a shell group, first the shell's header and last its footer (a file's top-level code, or a function's body) |
| Collected | in literal HTML (inline HTML, string literals), every `<link rel="stylesheet">` and every `<script src>`, in source order, attributes kept (`integrity`, `crossorigin`, `defer`, …) |
| Left out | HTML comments (incl. `<!--[if IE]>`); `rel="icon"` and other links; inline `<script>` blocks (still `inline_js_dropped`); a stylesheet the shell's layout already has |
| Skipped and listed, never guessed | a tag built by a PHP expression (`'…bootswatch.com/5/' . $theme . '/…'`, `<?= ?>` in an attribute): `tag_built_by_php`; an include whose path is an expression: `include_not_literal` |

Into the build project (`output/test-app/c2-build/<name>/`) it writes `public/` (the static files under their path from
the document root, so relative URLs resolve as on the PHP server), `src/c2-legacy-assets-data.ts` (the lists per page),
`src/c2-legacy-assets.tsx` (two server components), `src/proxy.ts` (tells them which page is requested: route, legacy
URL or folder), `assets.json`, and in each group's `layout.tsx` the stylesheets at the end of `<head>` and the scripts
after `{children}`. Page files and every Stage 1–5 output are untouched.

Discovery apps (`output/apps/<app>/index.json`):

| App | Pages | Stylesheets | Script files | Skipped |
|---|---|---|---|---|
| HMS | 26 | 44 | 49 | 6 (`include_not_literal`: TCPDF's own includes) |
| phpBlog | 43 | 143 (26 with the shell layouts' links removed) | 161 | 13 (`tag_built_by_php`: the theme stylesheet, `core.php` 380) |
| attendance | 14 | 50 | 12 | 0 |
| warehouse | 29 | 76 | 96 | 156 (`include_not_literal`: `LIB_PATH_INC.'…'` in `includes/load.php`) |
| WackoPicko | 30 | 72 | 0 | 0 |

## 3. How to use it

From `component2_frontend/`, after a build exists (`generated_app.py` or `compile_check.next_build`):

```
venv/Scripts/python.exe tools/assets/legacy_assets.py <index.json> --build <name>
```

writes the layer and runs `next build` again in place (`--no-build`: files only; without `--build`: print the table,
`--out assets.json` to write it). With the routing layer: `routes.py … --build <name> --no-build`, then this.
Or: `tools/runtime/generated_app.py <app> --routes --assets`.

## 4. Why it cannot change a result

Same as the routing layer (`docs/routing.md` §4): outside the pipeline, no pipeline or evaluation file imports it
(`tests/test_assets.py`), off by default, written only into a build project. Guard proof (18 Oct 2026, with the layer
in the tree): `tools/rules_guard.py apps --rules v1.0` → `ba8e4ac7…25d9`, `mocks --rules v1.0` → `71b746eb…0cf6`, both
byte-identical to the freeze 27c380e; no change under `src/`, `config/`, `tools/harness/`, `tools/eval/`. Tests:
1,246 passed (1,232 + 14). **Phase 5: keep it off** (eval-mapping §4 item 2). Phase 4's runtime check ran without it.

## 5. Demonstrations (18 Oct 2026, headless Chromium)

| Build | Check | Result |
|---|---|---|
| HMS (`hms-assets-runtime` = the Phase 4 build `hms-final-runtime` + routing + assets; 907 static files) | `/index.php` on load | only the Patient form shown, styled as the legacy page |
| | click Doctor / Receptionist / Patient | only that tab's form shown each time |
| | `/admin-panel1.php`, click "Doctor List" | the doctor table (replay data) replaces the dashboard |
| | `window.jQuery`, page errors | `function`, none |
| phpBlog (`phpblog-assets-runtime` = `phpblog-final-runtime` + routing + assets; replay on the phpBlog database) | `/index.php`, `/blog.php` | jQuery and Bootstrap 5 loaded, `assets/css/phpblog.css` served; no page errors |
| | `/admin/index.php` | nothing added: the legacy file loads no stylesheet or script (it only redirects) |

The Phase 4 builds were copied, not changed (their results stay as recorded).

## 6. What it does not do (limits)

- **Inline scripts** (`<script>…</script>`, `onclick=`) are not loaded or converted; what depends on them stays
  inert (HMS's `alert()` messages after a POST, validation `onkeyup`).
- **Tags built by PHP** and **includes by an expression** are skipped and listed (§2); conditional tags are
  collected whatever the condition (the scan is file-level, not flow-aware).
- **Order:** scripts the legacy page had in `<head>` now load at the end of `<body>` (in their order); a script that
  needs to run before the body is parsed may behave differently.
- **React and legacy JS:** the legacy scripts change the DOM React rendered (classes, visibility). The generated
  pages are Server Components with no client state, so React does not re-render them; a page with client
  components could conflict.
- The layouts read the request headers, so every page is rendered per request (dynamic).
- Forms, sessions and redirects remain Component 1's; AJAX endpoints are not generated (`non_html_response`).
- Nothing about the look of a page is measured or reported as a result.
