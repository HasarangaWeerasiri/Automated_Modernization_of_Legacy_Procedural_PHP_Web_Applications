# Component 2 — Phase 4 Results: Runtime Rendering Check (SO4)

| Item | Value |
|---|---|
| Date | 16 October 2026 (HMS: Phase 4a–4d; phpBlog: Phase 4e); 18 October 2026 (Phase 4f: change 4, phpBlog fixture; Phase 4g: change 5, parameterised phpBlog pages, re-measure — **rules v1.1 closed**) |
| Apps | HMS (`kishan0725/Hospital-Management-System` @ `777fda4`) and phpBlog (`codeplay-code/phpBlog-blogScript` @ `4ffe3c9`), both **rule-discovery** apps. No evaluation app was used |
| Rules | boundary rules v1.0 (frozen, commit `27c380e`); v1.1-pre-4 (post-freeze changes 1–3); v1.1-change-4 (changes 1–4); **v1.1 final** (changes 1–5, `docs/post-freeze-log.md`; closed). Both apps are reported under all four |
| Tools | `tools/runtime/` (capture, compare, classify, CSS simulation); `tools/runtime/hms/`, `tools/runtime/phpblog/` (legacy sides); `tools/runtime/replay/` (data fixture) |

The question Phase 4 answers: **when the legacy page and the generated Next.js page run on the same data, does the
generated page render the same document, and is every difference explained by a flag the tool emitted at that
node?** An unexplained difference would be an unsound result hidden from the developer.

## 1. Setup

| Side | How it runs |
|---|---|
| Legacy | The app **unmodified**, from a `git archive` of the pinned commit, in Docker, seeded with its own SQL dump. HMS: `php:7.4-apache` + `mysqli`, `mariadb:10.1.48` (the dump's major version), `localhost` through a shared socket. phpBlog: `php:8.2-apache` + `mysqli` + mod_rewrite, `mariadb:10.6`; its two installer-written configuration files made exactly as `install/done.php` makes them and mounted in (`tools/runtime/phpblog/README.md`). `display_errors=Off` in both (notices go to a log) |
| Data for the generated side | A **replay API** (test fixture standing in for Component 1): one endpoint per query that the harness resolved as a plain literal `SELECT` with no request input, answered from the same database by a SELECT-only user; an endpoint map entry per query, keyed by its query node id (reconciliation-spec v0.3). HMS: 7 of 16 Lists get data; phpBlog: 29 of 43 |
| Generated | Stages 1–5 with that contract / map, assembled as one Next.js app (`next build`, 0 tsc errors), served by `next start` |

## 2. Method

1. **Capture.** Every generated page and its legacy URL, in Chromium (Playwright 1.62.0) with **JavaScript off** (inline
   JS is out of scope; Server Components need none), a plain **GET**, no login. Pages whose output depends on request
   or session input are marked `get_only`: what is compared is their anonymous GET view. A page whose legacy side
   **redirects** the anonymous GET (an HTTP redirect, or a `<meta http-equiv="refresh">`, which a browser follows with
   JavaScript off) is recorded with its target and **left out** of the comparison: it would compare one page with
   another.
2. **Compare** the two `<body>` element trees (`tools/runtime/compare.py`), after this normalisation:

   | What | Normalisation |
   |---|---|
   | Comments | dropped |
   | `<script>` | elements dropped with their content |
   | Text | whitespace collapsed, trimmed |
   | Attributes | sorted, values whitespace-collapsed |
   | `style` | declarations in a canonical form (spaces around `:` / `;` removed, trailing `;` dropped, order kept) |
   | Boolean attributes | valued `""` (presence is their meaning) |
   | Next.js | the empty `<div hidden>` streaming boundary first in the generated `<body>` dropped |
   | Alignment | siblings of the same tag are paired to give, at each level, first the most attribute-identical pairs, then the most matched elements in their subtrees (a weighted longest common subsequence, recursively). Phase 4e: a plain tag-name LCS paired a missing sibling's neighbours wrongly on phpBlog; the HMS results are the same under both |

   **Render match** = matched elements / legacy elements.
3. **Classify** every difference (`tools/runtime/classify.py`), by the node that printed the differing element (or,
   when the element cannot be found by its own attributes, the nearest ancestor that can, marked `via`):

   | Class | Meaning |
   |---|---|
   | `flag:<kind>` | a flag the pipeline emitted **at that node** explains it (a deliberate, visible gap) |
   | `flag-but-wrong:<kind>` | a flag exists at the node, but the generated tree contradicts the browser's tree: the flag claimed browser-equivalent handling, or (`unresolvedCondition`) an arm rendered because its untranslated condition defaults to false (before change 4) |
   | `framework:react_hoist` | React 19 renders `<meta>`, `<title>` and non-stylesheet `<link>` elements into the document `<head>`: the element was generated in the body (as the browser builds it) and React moved it. Explained, not a generator error; changes nothing visible |
   | `env:<reason>` | caused by the run (e.g. a legacy runtime error) |
   | `norm:` | caused by the comparison (none were needed) |
   | `UNEXPLAINED` | none of these: a hidden wrong result. Root cause recorded in `tools/runtime/<app>/findings.json` |

   List rows are told from static elements by comparing with the same rules' build **without** data. A reviewed
   finding names the rules and the data (seed or fixture) it was reviewed on (`classify.py --data`).

## 3. HMS results

| | v1.0 (frozen) | v1.1 after changes 1–2 | v1.1-pre-4 (changes 1–3) | v1.1-change-4 (changes 1–4) | **v1.1 final** (changes 1–5) |
|---|---|---|---|---|---|
| Legacy elements | 1386 | 1386 | 1386 | 1386 | 1386 |
| Matched | 1312 | 1374 | 1380 | 1380 | **1380** |
| **Render match** | 0.947 | 0.991 | 0.996 | 0.996 | **0.996** |
| List elements matched | 497 / 501 | 497 / 501 | 497 / 501 | 497 / 501 | 497 / 501 |
| Static elements matched | 815 / 885 | 877 / 885 | 883 / 885 | 883 / 885 | 883 / 885 |
| Differences | 69 | 61 | 54 | 54 | 54 |
| **UNEXPLAINED** | **2** | 0 | 0 | 0 | **0** |
| **flag-but-wrong** | **17** | 9 | 0 | 0 | **0** |
| `framework:react_hoist` | 0 | 0 | 2 | 2 | 2 |
| `flag:inline_js_dropped` | 20 | 22 | 22 | 22 | 22 |
| `flag:document_shell` | 10 | 10 | 10 | 10 | 10 |
| `flag:attribute_dropped` | 6 | 6 | 6 | 6 | 6 |
| `flag:attribute_omitted` | 6 | 6 | 6 | 6 | 6 |
| `flag:C2Todo` | 5 | 4 | 4 | 4 | 4 |
| `flag:option_selected_not_converted` | 3 | 3 | 3 | 3 | 3 |
| `flag:unresolvedCondition` | 0 | 1 | 1 | 1 | 1 |
| `flag-but-wrong:document_shell` | 11 | 7 | 0 | 0 | 0 |
| `flag-but-wrong:implicit_close` | 6 | 2 | 0 | 0 | 0 |
| Unsound decisions | 1 (R-I1, `search.php`) | 0 | 0 | 0 | 0 |

Runs: `output/runtime/hms-v1.0`, `hms-v1.1`, `hms-v1.1-final` (= v1.1-pre-4), `hms-v1.1-c4` (= v1.1-change-4),
`hms-final` (= v1.1 final, Phase 4g), each with its no-data build as the baseline.

Per page (matched / render match / differences), where anything differs between the versions:

| Page | v1.0 | v1.1 (1–2) | v1.1 final |
|---|---|---|---|
| `admin-panel.php` | 132 / 0.863 / 14 | 149 / 0.974 / 11 | 149 / 0.974 / 11 |
| `admin-panel1.php` | 604 / 0.977 / 17 | 618 / 1.000 / 16 | 618 / 1.000 / 16 |
| `doctor-panel.php` | 217 / 0.927 / 4 | 234 / 1.000 / 3 | 234 / 1.000 / 3 |
| `prescribe.php` | 38 / 0.717 / 10 | 52 / 0.981 / 9 | 53 / 1.000 / 7 |
| `index1.php` | 53 / 0.883 / 8 | 53 / 0.883 / 8 | 58 / 0.967 / 3 |
| `search.php` | 0 / – / 2 (UNEXPLAINED) | 0 / – / 0 | 0 / – / 0 |

v1.1-change-4 and v1.1 final are identical to v1.1-pre-4 on every page (§3.3, §3.4). All other pages are identical across the runs: 10 at 1.000, `include/config.php` (an error text only, no
element), and 9 that print nothing on GET on either side. The 52 flagged differences of the final run are deliberate,
visible gaps: inline JS (`onclick`, out of scope), the document shell (`<body style>`, which belongs in
`app/layout.tsx`), attributes React's types do not accept, form values whose expression is not generated, and Lists with
no data endpoint; the other 2 are `index1.php`'s two `<meta>` elements, generated in the body as the browser builds them
and moved into `<head>` by React 19 (`framework:react_hoist`).

### 3.1 The one unsound decision, found and fixed

`search.php` prints its whole page inside `if (isset($_POST['search_submit']))` (line 4). Boundary rule R-I1 (v1.0)
treated an `if` around the whole page as a page guard and rendered the page unconditionally; on a GET the legacy page
prints nothing, the generated page printed the POST-only results view. No specific flag said so: the only flag at the
guard was the generic `unlabelled` review. Post-freeze change 1 (rules v1.1) restricts R-I1 to guards whose other path
redirects; otherwise the guard is a conditional whose condition (request input) is not translated
(`unresolvedCondition`, `needs_request_context`), so the content is hidden and flagged. Both sides are now blank on GET.

### 3.2 Wrong results behind a flag, fixed by browser equivalence

Four HTML5 parser behaviours made the generated tree differ from the browser's although a flag was present
(`flag-but-wrong`, 17 in v1.0). They are fixed HTML5 rules, so generating them is faithful, not a guess:

| Change | Case (HMS) | Elements |
|---|---|---|
| 2a (spec v0.14) | a `<nav>` written inside `<head>` ends the head; it is body content (4 pages) | 58 |
| 2b (spec v0.14) | `</h2>` closes an open `<h4>` (`admin-panel.php` 267, 283) | 4 differences |
| 3a (spec v0.15) | a `<head>` after body content is ignored; its `<link>`s and `<meta>`s stay in the body (`index1.php` 6) | 7 |
| 3b (spec v0.15) | `</form>` removes only the form; the `<div>` opened inside it stays open (`prescribe.php` 147) | 2 differences |

### 3.3 Change 4 on HMS: no `else` arm that happened to match is lost

Change 4 (spec v0.16) turns 7 HMS conditionals in the runtime pipeline into one flagged gap each (`Arms_<id>()` holds
their arms): `admin-panel.php` f001#00806 (in a row of `AdminPanelList1`), `doctor-panel.php` f005#00188 and #00210
(rows of `DoctorPanelList1`), and the `Section2` conditional (#00041) of `appsearch.php`, `doctorsearch.php`,
`messearch.php`, `patientsearch.php`. None of them was rendered by the generated pages on an anonymous GET before
change 4 either: the two Lists have no replay endpoint (`no_endpoint`), so the generated List has no rows (the legacy
rows are already a flagged `collection` gap), and the four search sections print only after a POST (both sides blank on
GET). So **no `if / else` whose `else` arm matched the legacy page became a gap**: render match, differences and
classes are unchanged (0.996, 54, 0 UNEXPLAINED, 0 flag-but-wrong). With data for those Lists, or after a POST, the
gaps would show; that is not measured here (GET only, replay for literal SELECTs only).

### 3.4 Change 5 on HMS: no list guard counts rows

HMS uses `mysqli_num_rows` 6 times (4 of them in an `if` condition the harness records): all are login checks
(`if (mysqli_num_rows($result) == 1)` then a redirect), none guards a List. Change 5 has nothing to translate in HMS;
its v1.1 final run is identical to v1.1-change-4 (0.996, 54 differences, 0 UNEXPLAINED, 0 flag-but-wrong).

## 4. Styling (simulation)

HMS has no shared header / footer pair, so each page keeps its own `<head>` and the generated pages carry no root
layout (`document_shell`, flagged). As a clearly labelled **simulation** (never written into generated code), the
capture tool adds each legacy page's `<link rel="stylesheet">`s to the generated HTML in transit and takes a second
screenshot (`output/runtime/<run>/generated-with-legacy-css/`). In the final HMS run the generated pages are already
styled where the navbar is generated, because the navbar carries the legacy stylesheet links; what remains visibly
different is the icon font (a relative stylesheet URL the generated server does not serve) and the body's top padding
(`<body style>`, a `document_shell` gap). Screenshots: `output/runtime/hms-v1.1-final/admin-panel1.php.side-by-side.png`,
`output/runtime/phpblog/index.php.side-by-side.png`; with the phpBlog fixture (§5.3):
`output/runtime/phpblog-pre4-fx/index.php.side-by-side.png` (v1.1-pre-4) and
`output/runtime/phpblog-c4-fx/index.php.side-by-side.png` (v1.1-change-4); v1.1 final (§5.6):
`output/runtime/phpblog-final-fx/index.php.side-by-side.png` and
`output/runtime/phpblog-final-fx/post.php@name=welcome-to-the-blog.side-by-side.png`.

## 5. phpBlog (Phase 4e; re-measured in Phases 4f and 4g)

phpBlog ran **unmodified** (PHP 8.2, MariaDB 10.6; `tools/runtime/phpblog/README.md`) on two databases:

- **Shipped seed** (`install/database.sql`): **no posts, no comments and no users** (an admin user is created only by
  the installer's form, which was not run): every post list compares only its empty state, and nobody can log in.
- **Seed + fixture** (Phase 4f, owner): `tools/runtime/phpblog/initdb/03-fixture.sql`, a small fixed **data-only**
  file loaded after the seed: 3 published posts in 2 categories (one featured), 1 static page, 2 approved guest
  comments, 1 menu item; still no users. No code changes, on either side.

| | phpBlog |
|---|---|
| Page units | 43 (plus `rss.php`, `sitemap.php`: `non_html_response`; `core.php`, `install/core.php`: `error_output_only` — not generated, not compared) |
| Not compared: the legacy page redirects an anonymous GET | 36 (both databases): the 25 admin pages (→ `../login`), `category`, `post`, `search` (no parameter → `blog`), `my-comments`, `profile` (→ `login`), `page`, `unsubscribe` (→ the site), the three install pages (→ `../`), `logout`. The generated side renders 35 of them (R-I8 redirect guard: the redirect is Component 1's, flagged `redirect_guard`); `logout` also refreshes |
| Compared | 7: `index`, `blog`, `gallery`, `contact`, `login`, `error404`, `install/upgrade-v2.2`; with the fixture (Phase 4g) also 6 parameterised GETs: `post?name=` × 3, `category?name=` × 2, `page?name=terms` (§5.8) — 13 |

### 5.1 Shipped seed

| | v1.0 (frozen) | v1.1-pre-4 (changes 1–3) | v1.1-change-4 (changes 1–4) | **v1.1 final** (changes 1–5) |
|---|---|---|---|---|
| Legacy elements | 771 | 771 | 771 | 771 |
| Matched | 401 | 401 | 383 | **385** |
| **Render match** | 0.520 | 0.520 | 0.497 | **0.499** |
| List elements matched | 2 / 316 | 2 / 316 | 2 / 316 | 4 / 317 |
| Static elements matched | 399 / 455 | 399 / 455 | 381 / 455 | 381 / 454 |
| Differences | 166 | 166 | 135 | 133 |
| **UNEXPLAINED** | **0** | **0** | **0** | **0** |
| **flag-but-wrong** (target 0) | **37** | **37** | **0** | **0** |
| `flag:C2Todo` | 70 | 70 | 70 | 69 |
| `flag:attribute_omitted` | 41 | 41 | 41 | 41 |
| `flag:unresolvedCondition` | 12 | 12 | 18 | 17 |
| `env:legacy_runtime_error` | 6 | 6 | 6 | 6 |
| Unsound decisions | 2 sites (§5.3) | 2 sites (§5.3) | **0** | **0** |

v1.0 and v1.1-pre-4 generate the same phpBlog pages (changes 1–3 have no phpBlog instance; the normalised bodies are
identical), so their columns are equal. Per page (legacy elements / matched / render match / differences), v1.1-pre-4
→ v1.1-change-4: `index` 123 / 57 / 0.463 / 26 → 54 / 0.439 / 21 · `blog` 122 / 56 / 0.459 / 26 → 53 / 0.434 / 20 ·
`gallery` 101 / 66 / 0.654 / 25 → 63 / 0.624 / 20 · `contact` 144 / 71 / 0.493 / 33 → 68 / 0.472 / 28 · `login`
158 / 93 / 0.589 / 26 → 90 / 0.570 / 21 · `error404` 122 / 57 / 0.467 / 24 → 54 / 0.443 / 19 · `install/upgrade-v2.2`
1 / 1 / 1.000 / 6 (unchanged). v1.1 final: `index` 55 matched (the Recent Posts guard renders its Empty, "There are no
published posts", as the legacy page does), `gallery` 64 (the same for its albums); the other pages as v1.1-change-4.
Runs: `output/runtime/phpblog-v1.0`, `phpblog` (v1.1-pre-4), `phpblog-c4`, `phpblog-final-seed`.

### 5.2 Seed + fixture

Each column of Phase 4g was captured on a **freshly created** database: `post.php` counts views on every GET (line 24,
`UPDATE posts SET views = views + 1`) and the legacy sidebar orders popular posts by views, so the legacy pages would
otherwise differ between runs. On the 7 pages of Phase 4f the v1.0, v1.1-pre-4 and v1.1-change-4 columns reproduce
Phase 4f exactly.

The 7 pages compared in Phase 4f:

| | v1.0 (frozen) | v1.1-pre-4 (changes 1–3) | v1.1-change-4 (changes 1–4) | **v1.1 final** (changes 1–5) |
|---|---|---|---|---|
| Legacy elements | 1337 | 1337 | 1337 | 1337 |
| Matched | 456 | 456 | 383 | **454** |
| **Render match** | 0.341 | 0.341 | 0.286 | **0.340** |
| List elements matched | 56 / 752 | 56 / 752 | 2 / 752 | 73 / 774 |
| Static elements matched | 400 / 585 | 400 / 585 | 381 / 585 | 381 / 563 |
| Differences | 195 | 195 | 147 | 167 |
| **UNEXPLAINED** | **0** | **0** | **0** | **0** |
| **flag-but-wrong** (target 0) | **36** | **36** | **0** | **0** |
| `flag:C2Todo` | 90 | 90 | 78 | 93 |
| `flag:attribute_omitted` | 47 | 47 | 41 | 48 |
| `flag:unresolvedCondition` | 16 | 16 | 22 | 20 |
| `env:legacy_runtime_error` | 6 | 6 | 6 | 6 |
| Unsound decisions | 1 site (§5.3) | 1 site (§5.3) | **0** | **0** |

All 13 pages (Phase 4g: plus the 6 parameterised GETs, §5.8):

| | v1.0 (frozen) | v1.1-pre-4 (changes 1–3) | v1.1-change-4 (changes 1–4) | **v1.1 final** (changes 1–5) |
|---|---|---|---|---|
| Legacy elements | 2758 | 2758 | 2758 | 2758 |
| Matched | 871 | 871 | 778 | **849** |
| **Render match** | 0.316 | 0.316 | 0.282 | **0.308** |
| List elements matched | 56 / 1536 | 56 / 1536 | 2 / 1536 | 73 / 1558 |
| Static elements matched | 815 / 1222 | 815 / 1222 | 776 / 1222 | 776 / 1200 |
| Differences | 390 | 390 | 309 | 329 |
| **UNEXPLAINED** | **0** | **0** | **0** | **0** |
| **flag-but-wrong** (target 0) | **75** | **75** | **0** | **0** |
| `flag:C2Todo` | 190 | 190 | 178 | 193 |
| `flag:attribute_omitted` | 86 | 86 | 80 | 87 |
| `flag:unresolvedCondition` | 33 | 33 | 45 | 43 |
| `env:legacy_runtime_error` | 6 | 6 | 6 | 6 |
| Unsound decisions | 2 sites (§5.3, §5.8) | 2 sites | **0** | **0** |

Per page, v1.1 final (legacy / matched / render match / differences): `index` 285 / 124 / 0.435 / 46 · `blog` 289 / 53 /
0.183 / 24 · `gallery` 110 / 64 / 0.582 / 20 · `contact` 220 / 68 / 0.309 / 29 · `login` 234 / 90 / 0.385 / 22 ·
`error404` 198 / 54 / 0.273 / 20 · `install/upgrade-v2.2` 1 / 1 / 1.000 / 6 · `post?name=` 252 / 79 / 0.313 / 31,
252 / 79 / 0.313 / 31, 241 / 79 / 0.328 / 31 · `category?name=site-news` 226 / 53 / 0.235 / 23 · `tutorials` 253 / 53 /
0.209 / 24 · `page?name=terms` 197 / 52 / 0.264 / 22. Runs: `output/runtime/phpblog-v1.0-fx2`, `phpblog-pre4-fx2`,
`phpblog-c4-fx2`, `phpblog-final-fx` (Phase 4f's 7-page runs: `phpblog-v1.0-fx`, `phpblog-pre4-fx`, `phpblog-c4-fx`),
classified with `--data fixture`. In v1.1 final the List and static denominators move by 22 elements against the other
columns: the List-row baseline is each rules version's own no-data build, and in v1.1 final the Recent Posts List is
rendered, so its rows are told apart from static elements.

The fixture adds 566 legacy elements (the posts in the lists, the featured-post carousel, the sidebar's category,
popular-post and comment lists) and the generated side gains almost none of them: the sidebar and the carousel are
behind `$settings[...]` conditions (untranslatable), and the shell's queries are not served by the replay fixture.
Four differences needed hand-reviewed links (`tools/runtime/phpblog/findings.json`, `data: fixture`): the carousel
shifts the Recent Posts row from `div[0]` to `div[1]`, so the seed's reviewed link (`index.php` 85, `class="row <?php
echo $mt3_i; ?>"`, now valued `row mt-3`) no longer applies by path, and under v1.0 / v1.1-pre-4 the three post cards'
`class` (an `if` on `posts_per_row`, flag `attribute_omitted` at f036#00279) equals the literal class of a `<div>` in the
shell's footer. The three seed findings for `login`, `blog`'s footer and `install/upgrade-v2.2` were re-checked on the
fixture run (same paths, values and root causes) and apply to both databases. Phase 4g added 3 reviewed links, one per
`post?name=` page: the same footer `<span>` as `blog`'s (`core.php` 1006, C2Todo f033#02168).

**Why the render match is low.** phpBlog drives its layout from `$settings[...]` (read from `config_settings.php`) and
its login state: every condition on them is untranslatable, so the content they guard is hidden (flagged
`unresolvedCondition`): the sidebar (`if ($settings['sidebar_position'] == 'Right') sidebar();`), the latest-posts bar,
each page's empty-state message. Every value printed from them (site name, description, e-mail, year) is a C2Todo,
and every `class` built from them (`container` / `container-fluid`) is an omitted attribute. The menu and the sidebar
lists come from queries in the shared shell, which the replay fixture does not serve. These are visible, flagged gaps,
not hidden ones.

**`env:legacy_runtime_error` (6).** `install/upgrade-v2.2.php` dies after its first line on PHP 8: line 46 queries a
`settings` table the seed does not create (uncaught `mysqli_sql_exception`, in the PHP error log). Before that, line 41
tries to rewrite `../config.php` on a plain GET (blocked: mounted read-only).

### 5.3 The unsound decision found in Phase 4e, fixed by change 4

Before change 4, spec §6 generated an untranslatable condition as `unresolvedCondition(...)`, which is `false`. For
`{cond && …}` that hides the content: a flagged abstain. For an **`if / else`** it selected the **`else` arm on every
request**: a guess.

| Where | Legacy (anonymous GET) | v1.0 / v1.1-pre-4 | v1.1-change-4 and final | flag-but-wrong (seed / fixture, 7 pages) |
|---|---|---|---|---|
| `core.php` 652, the shared shell (every public page): `if ($logged == 'No') { Sign In \| Register } else { Profile menu }` | Sign In \| Register | the logged-in Profile menu | nothing: `C2Todo` f033#01506; both arms in `Arms_f033_01506()` | 36 / 36 → 0 |
| `blog.php` 29: `if ($count <= 0) { 'There are no published posts' } else { the posts loop, pagination }` | seed: the message; fixture: the posts | an empty list and its pagination | nothing: `C2Todo`; both arms in `Arms_<id>()` (change 5 does not apply: the List's query has `LIMIT $rows, …`, not replayed, so (d) fails) | 1 / 0 → 0 |

**Correction to the Phase 4e count.** Phase 4e reported 35 differences at `core.php` 652 and 2 at `blog.php` 29. By
hand, one of the two is the menu's icon (`<i class="fas fa-user">`): the classifier ties a generated element to the
first `else` arm that prints its start tag, and `blog.php`'s `else` arm (the post card's "Posted by" icon) prints the
same tag as the Profile menu. So: menu 36, `blog.php` 1 (the pagination `<center>`). The class (`flag-but-wrong`)
and the total (37) are right; only the node of that one difference is not. Not changed in the classifier (it does not
change any class); recorded here.

With the fixture, `blog.php` 29's browser takes the `else` arm too (there are posts), so before change 4 that arm was
right **by accident**, not by translation.

### 5.4 The cost of change 4 (honest)

Change 4 renders **nothing** where it cannot know the arm, including where the defaulted arm happened to be right.
Render match goes down:

| Database | v1.1-pre-4 → v1.1-change-4 | Lost matched elements |
|---|---|---|
| Seed | 0.520 → **0.497** | 18: on each of the 6 public pages, the 3 elements (`<li><a><i>`) the Profile menu paired with structurally (tags equal, attributes different) |
| Seed + fixture | 0.341 → **0.286** | 73: the 54 elements of `index.php`'s Recent Posts rows (R-I2b: `if ($count <= 0) { message } else { the loop }`, `$count` = `mysqli_num_rows(...)`, untranslatable: before change 4 the else arm rendered the replayed rows and, with posts in the database, the browser took it too); the menu's 3 per page (18); `blog.php`'s pagination `<center>` (1) |

The 54 rows are the cost that matters: they were correct only because the condition defaulted to the arm the data
happened to select (with an empty `posts` table the same arm rendered an empty list and hid the page's "There are no
published posts" message). The
generated code now says so (one `C2Todo` at the guard; the List and its Empty in `Arms_f036_00232()`). Seen in the
side-by-side screenshots: `output/runtime/phpblog-pre4-fx/index.php.side-by-side.png` (the Profile menu and the Recent
Posts cards) and `output/runtime/phpblog-c4-fx/index.php.side-by-side.png` (neither). Change 5 (§5.6) wins these rows
back by translating the guard, not by choosing an arm.

### 5.5 Phase 4e comparison fixes: what each moved out of UNEXPLAINED

The phpBlog seed run (v1.1-pre-4) started with 169 of 175 differences UNEXPLAINED. Each fix below was made for a
stated reason (in `tools/runtime/compare.py` / `classify.py`), in this order; the table records the counts after each
one, as they were measured. Two of them (alignment) change the **difference set** itself, not only its classes.

| # | Fix | UNEXPLAINED | Differences | Moved into |
|---|---|---|---|---|
| 0 | Phase 4d tools on phpBlog | 169 | 175 | (`flag-but-wrong:implicit_close` 3, `flag:attribute_omitted` 1, `flag:unresolvedCondition` 2) |
| 1 | A page inside a shell (R-F2) is classified with the shell's header / footer units | 108 (−61) | 175 | `flag:C2Todo` +41, `flag:attribute_omitted` +17, `flag:unresolvedCondition` +4 (`flag-but-wrong:implicit_close` 3 → 2) |
| 2 | Weighted sibling alignment, entities in literal text, nearest-ancestor region | 99 | 217 (re-aligned) | `flag:C2Todo` 81, `flag:attribute_omitted` 26, `flag:unresolvedCondition` 11, `flag-but-wrong` 0 |
| 3 | The defaulted-arm rule (`flag-but-wrong:unresolvedCondition`: the generated element is in an `else` arm of an untranslated if, the legacy one is not) | 55 (−44) | 217 | `flag-but-wrong:unresolvedCondition` 55 (44 from UNEXPLAINED, 6 from `flag:C2Todo`, 5 from `flag:attribute_omitted`) |
| 4 | Attribute anchors, word-by-word value match | 52 (−3) | 214 | `flag:C2Todo` 74, `flag:attribute_omitted` 21, `flag:unresolvedCondition` 12 |
| 5 | Flags on the `if` inside a start tag explain its attribute (R-I3 / R-I3b) | 34 (−18) | 214 | `flag:attribute_omitted` 21 → 39 |
| 6 | Per-level lexicographic alignment (attribute-identical pairs first) | 9 | 166 (re-aligned) | `flag-but-wrong:unresolvedCondition` 37, `flag:C2Todo` 69, `flag:attribute_omitted` 39, `flag:unresolvedCondition` 12 |
| 7 | Hand review (`findings.json`): `install/upgrade-v2.2` page-wide (`env:`, 6) and 3 reviewed links | **0** | 166 | `env:legacy_runtime_error` 6, `flag:attribute_omitted` +2, `flag:C2Todo` +1 |

The classifier rules (1, 3, 4, 5) each move differences into a flag class for a stated reason, and they apply to every
app (HMS is classified by the same code). The last 9 were not reached by tuning a rule: they were reviewed by hand and
recorded with their evidence. Phase 4f added no classifier rule: only the `data` scope of reviewed findings and 4
reviewed links for the fixture (§5.2).

### 5.6 Change 5: the List's own row count (spec v0.17)

Every list guard (R-I2 / R-I2b) whose condition uses a row count, as the `row_count_guard` flag of the runtime
pipeline (replay map) reports it; in the five-app run (no endpoint map) every one fails (d), by construction:

| Guard | Condition | v1.1 final | Why |
|---|---|---|---|
| phpBlog `index.php` 14 (R-I2b, carousel) | `$count > 0` | **translated**: `apiC2ReplayF03600024.rows.length > 0` | (a)–(d) hold; `$count` is reassigned at line 89, but its definition reaching line 14 is line 13 |
| phpBlog `index.php` 90 (Recent Posts) | `$count <= 0` | **translated**: `items.length <= 0` | (a)–(d) hold |
| phpBlog `gallery.php` 33 | `$count <= 0` | **translated**: `items.length <= 0` | (a)–(d) hold |
| phpBlog `gallery.php` 101 (then-only, generated in the page) | `$countimg > 0` | **translated**: `apiC2ReplayF03500216.rows.length > 0` | (a)–(d) hold |
| phpBlog `core.php` 819 (sidebar, popular posts) | `$count <= 0` | **translated**: `items.length <= 0` | (a)–(d) hold |
| phpBlog `core.php` 861 (sidebar, recent comments) | `$cmnts == "0"` | not translated (no arm: change 4) | (a)–(d) hold, but the count is compared with a quoted string: number vs string is not on the §6 whitelist |
| phpBlog `admin/dashboard.php` 177 | `$cmnts == "0"` | not translated (no arm) | as above |
| phpBlog `blog.php` 29 | `$count <= 0` | not translated (no arm) | (d): the query has `LIMIT $rows, …` (request input): no replay endpoint |
| phpBlog `category.php` 47 | `$count <= 0` | not translated (no arm) | (d): the query interpolates `$category_id` |
| phpBlog `post.php` 76 (comments) | `$count <= 0` | not translated (no arm) | (d): the query interpolates `$row[id]` |
| phpBlog `search.php` 47 | `$count <= 0` | not translated (no arm) | (d): the query interpolates `$word` (request input) |
| phpBlog `my-comments.php` 29 | `$count <= 0` | not translated (no arm) | (d): the query interpolates `$user_id` (the logged-in user) |
| attendance `reset.php` 70 | `$row == 0` | not translated (no arm) | (b): `$query` is reassigned at line 79, between the count and the loop; and (d) |

phpBlog's other 39 row counts in conditions are not list guards (counts printed, existence checks before a redirect);
HMS (4, login checks) and attendance's other 3 are not list guards; WackoPicko and warehouse have none.
The generated Recent Posts guard under v1.1 final (`components/IndexList3.tsx`):

```tsx
export function IndexList3({ items }: { items: ApiC2ReplayF03600219Response["rows"][number][] }) {
  return (
    <div>
      <h5>
        <i className="fa fa-list" />
        {" Recent Posts"}
      </h5>
      {items.length <= 0 ? (
        <IndexList3Empty />
      ) : (
        <>
          {items.map((row, i) => (
            <IndexList3Item key={i} row={row} />
          ))}
        </>
      )}
    </div>
  );
}
```

Effect on the render match (v1.1-change-4 → v1.1 final): seed 383 → 385 (the Empty messages of `index` and `gallery`),
fixture 383 → 454 on the 7 pages and 778 → 849 on all 13 (`index` 54 → 124: the 3 Recent Posts rows and the carousel's
rows; `gallery` 63 → 64). flag-but-wrong stays 0: a translated guard renders the arm the data selects.

### 5.7 Where the List elements go (seed + fixture, v1.1 final, 13 pages)

`classify.py` counts as List elements the legacy elements matched to a row the data wiring added, plus the elements of
missing differences it ties to a List's `collection` gap. The 1558 of the v1.1 final run, reviewed by hand by region:

| Where | Elements | What it is |
|---|---|---|
| Matched rows | 73 | rendered from the replay data |
| Shell menu (`core.php` `menu` query, every page) | 336 | a **data-wiring gap**: the shell's query has no replay endpoint (`no_endpoint`) |
| Inside rendered `index` rows: `<svg>` placeholders, `<button class="active">` | 17 | **data flow**: markup kept in a PHP variable (`$image`, `$active1`) is an unresolved read |
| One `gallery` album pane (`gallery.php` 65) | 4 | a List row the generated List does not render |
| **Not List elements** — tied to the menu's gap by source order (`via <body> 1 of 1`): the whole sidebar (`div.col-md-4`, 11 × 102) | 1122 | hidden behind `$settings['sidebar_position']` (`unresolvedCondition`); a flagged gap, but at another node |
| **Not List elements**, same attribution: the post / page body `<p>` (HTML stored in the database, printed through `html_entity_decode`) and 2 contact-form `<br>` | 6 | markup from data; form markup |

So the true List elements are 430: 73 matched, 336 + 4 data-wiring gaps, 17 data flow inside rendered rows. **None is a
boundary error.** The 1128 others show a limit of the classifier's by-order attribution (the class is a flag, the node
is not the one that hides them); like the 36 / 1 correction of §5.3 it is recorded, not tuned.

### 5.8 Parameterised GETs (Phase 4g)

With the fixture, `post`, `category` and `page` render when given their real parameter. `tools/runtime/phpblog/gets.json`
lists 6 GETs with the fixture's own slugs; `capture.py --gets` captures each as one more page `<unit>@<query>` and keeps
the bare URLs (they still redirect, 36 as before). The generated pages do not read the query string (request input is
not generated), so their side is the bare page with its gaps flagged. Under v1.0 / v1.1-pre-4 the new pages show the
`core.php` 652 menu (72 differences on all 13 pages, 3 of them attributed by the classifier to a page's own `else` arm
that prints the same icon, as in §5.3) and one more `else`-arm site: `post.php` 142 `if ($cancomment == 'Yes') { the
comment form } else { … }` (3 differences). Change 4 fixed both before they were seen.

## 6. Two-app summary (v1.1 final)

| | HMS | phpBlog (seed) | phpBlog (seed + fixture, 7 pages) | phpBlog (seed + fixture, 13 pages) |
|---|---|---|---|---|
| Pages compared (of page units) | 26 of 26 | 7 of 43 (36 redirect an anonymous GET) | 7 of 43 | 13 (7 + 6 parameterised GETs) |
| Legacy elements | 1386 | 771 | 1337 | 2758 |
| **Render match** | **0.996** | **0.499** | **0.340** | **0.308** |
| Differences | 54 | 133 | 167 | 329 |
| `flag:` (explained, visible gaps) | 52 | 127 | 161 | 323 |
| `framework:react_hoist` | 2 | 0 | 0 | 0 |
| `env:` | 0 | 6 | 6 | 6 |
| **flag-but-wrong** | **0** | **0** | **0** | **0** |
| **UNEXPLAINED** | **0** | **0** | **0** | **0** |
| Unsound decisions found | 1 under v1.0 (R-I1 on `search.php`), fixed by change 1 | 2 sites under v1.0 / v1.1-pre-4 (spec §6 `else` arm: `core.php` 652, `blog.php` 29), fixed by change 4 | 1 site (`core.php` 652), fixed by change 4 | 2 sites (`core.php` 652, `post.php` 142), fixed by change 4 |

**Rules v1.1 are closed** after change 5 (spec v0.17): Phase 5 runs v1.0 (freeze `27c380e`) and v1.1 final (changes
1–5, the owner's Phase 4g commit). A later finding is reported as a v1.1 limitation, not fixed.

## 7. Limits

- **Two discovery apps.** Phase 4 tests the method and found unsound decisions (two, both fixed: change 1 and
  change 4); it is not the evaluation. WackoPicko needs PHP 5 (`mysql_*`) and was not run.
- **GET only, no session.** Nobody logs in and nothing is posted: get_only pages are compared on their anonymous view,
  and phpBlog's 36 redirecting pages not at all. Behaviour after a POST or with a session is not measured (Component 4).
- **Seeds.** phpBlog's shipped seed has no posts (its lists compare only their empty states); the recorded fixture
  (Phase 4f) adds a few rows. Its post, category and static pages redirect an anonymous GET without a `name` parameter;
  with the fixture they are compared through 6 parameterised GETs (Phase 4g), not on the shipped seed.
- **v1.1 limitations recorded, not fixed** (rules closed): a row count compared with a quoted string (`$cmnts == "0"`,
  phpBlog `core.php` 861, `admin/dashboard.php` 177) is not translated; the classifier ties some missing subtrees to a
  gap by source order, at a node that is not the one hiding them (§5.3, §5.7) — the class is right, the node is not.
- **Replay data only for literal SELECTs** of pages and function components (HMS 7 of 16 Lists, phpBlog 29 of 43); a
  shared shell's queries (phpBlog's menu) are not served.
- **Element trees, not pixels.** CSS is compared only in the simulation screenshots; the render match counts elements,
  attributes and text.
- **The replay API is a test fixture**, not Component 1's contract; the real contract may name and shape data
  differently (Stage 4 then reports it).
