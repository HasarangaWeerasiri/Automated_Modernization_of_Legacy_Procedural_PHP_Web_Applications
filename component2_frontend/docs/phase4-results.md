# Component 2 — Phase 4 Results: Runtime Rendering Check (SO4)

| Item | Value |
|---|---|
| Date | 16 October 2026 (HMS: Phase 4a–4d; phpBlog: Phase 4e) |
| Apps | HMS (`kishan0725/Hospital-Management-System` @ `777fda4`) and phpBlog (`codeplay-code/phpBlog-blogScript` @ `4ffe3c9`), both **rule-discovery** apps. No evaluation app was used |
| Rules | boundary rules v1.0 (frozen, commit `27c380e`) and v1.1 (post-freeze changes 1–3, `docs/post-freeze-log.md`); phpBlog under v1.1 final |
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
   | `flag-but-wrong:<kind>` | a flag exists at the node, but the generated tree contradicts the browser's tree: the flag claimed browser-equivalent handling, or (`unresolvedCondition`) an arm rendered because its untranslated condition defaults to false |
   | `framework:react_hoist` | React 19 renders `<meta>`, `<title>` and non-stylesheet `<link>` elements into the document `<head>`: the element was generated in the body (as the browser builds it) and React moved it. Explained, not a generator error; changes nothing visible |
   | `env:<reason>` | caused by the run (e.g. a legacy runtime error) |
   | `norm:` | caused by the comparison (none were needed) |
   | `UNEXPLAINED` | none of these: a hidden wrong result. Root cause recorded in `tools/runtime/<app>/findings.json` |

   List rows are told from static elements by comparing with the same rules' build **without** data.

## 3. HMS results

| | v1.0 (frozen) | v1.1 after changes 1–2 | **v1.1 final** (changes 1–3) |
|---|---|---|---|
| Legacy elements | 1386 | 1386 | 1386 |
| Matched | 1312 | 1374 | **1380** |
| **Render match** | 0.947 | 0.991 | **0.996** |
| List elements matched | 497 / 501 | 497 / 501 | 497 / 501 |
| Static elements matched | 815 / 885 | 877 / 885 | 883 / 885 |
| Differences | 69 | 61 | 54 |
| **UNEXPLAINED** | **2** | 0 | **0** |
| **flag-but-wrong** | **17** | 9 | **0** |
| `framework:react_hoist` | 0 | 0 | 2 |
| `flag:inline_js_dropped` | 20 | 22 | 22 |
| `flag:document_shell` | 10 | 10 | 10 |
| `flag:attribute_dropped` | 6 | 6 | 6 |
| `flag:attribute_omitted` | 6 | 6 | 6 |
| `flag:C2Todo` | 5 | 4 | 4 |
| `flag:option_selected_not_converted` | 3 | 3 | 3 |
| `flag:unresolvedCondition` | 0 | 1 | 1 |
| `flag-but-wrong:document_shell` | 11 | 7 | 0 |
| `flag-but-wrong:implicit_close` | 6 | 2 | 0 |

Per page (matched / render match / differences), where anything differs between the versions:

| Page | v1.0 | v1.1 (1–2) | v1.1 final |
|---|---|---|---|
| `admin-panel.php` | 132 / 0.863 / 14 | 149 / 0.974 / 11 | 149 / 0.974 / 11 |
| `admin-panel1.php` | 604 / 0.977 / 17 | 618 / 1.000 / 16 | 618 / 1.000 / 16 |
| `doctor-panel.php` | 217 / 0.927 / 4 | 234 / 1.000 / 3 | 234 / 1.000 / 3 |
| `prescribe.php` | 38 / 0.717 / 10 | 52 / 0.981 / 9 | 53 / 1.000 / 7 |
| `index1.php` | 53 / 0.883 / 8 | 53 / 0.883 / 8 | 58 / 0.967 / 3 |
| `search.php` | 0 / – / 2 (UNEXPLAINED) | 0 / – / 0 | 0 / – / 0 |

All other pages are identical across the three runs: 10 at 1.000, `include/config.php` (an error text only, no
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

## 4. Styling (simulation)

HMS has no shared header / footer pair, so each page keeps its own `<head>` and the generated pages carry no root
layout (`document_shell`, flagged). As a clearly labelled **simulation** (never written into generated code), the
capture tool adds each legacy page's `<link rel="stylesheet">`s to the generated HTML in transit and takes a second
screenshot (`output/runtime/<run>/generated-with-legacy-css/`). In the final HMS run the generated pages are already
styled where the navbar is generated, because the navbar carries the legacy stylesheet links; what remains visibly
different is the icon font (a relative stylesheet URL the generated server does not serve) and the body's top padding
(`<body style>`, a `document_shell` gap). Screenshots: `output/runtime/hms-v1.1-final/admin-panel1.php.side-by-side.png`,
`output/runtime/phpblog/index.php.side-by-side.png`.

## 5. phpBlog (Phase 4e, rules v1.1 final)

phpBlog ran **unmodified** (PHP 8.2, MariaDB 10.6; `tools/runtime/phpblog/README.md`). Its seed has **no posts, no
comments and no users** (an admin user is created only by the installer's form, which was not run): every post list
compares only its empty state, and nobody can log in.

| | phpBlog |
|---|---|
| Page units | 43 (plus `rss.php`, `sitemap.php`: `non_html_response`; `core.php`, `install/core.php`: `error_output_only` — not generated, not compared) |
| Not compared: the legacy page redirects an anonymous GET | 36: the 25 admin pages (→ `../login`), `category`, `post`, `search` (no parameter, no posts → `blog`), `my-comments`, `profile` (→ `login`), `page`, `unsubscribe` (→ the site), the three install pages (→ `../`), `logout`. The generated side renders 35 of them (R-I8 redirect guard: the redirect is Component 1's, flagged `redirect_guard`); `logout` also refreshes |
| Compared | 7: `index`, `blog`, `gallery`, `contact`, `login`, `error404`, `install/upgrade-v2.2` |
| Legacy elements | 771 |
| Matched | 401 |
| **Render match** | **0.520** |
| List elements matched | 2 / 316 |
| Static elements matched | 399 / 455 |
| Differences | 166 |
| **UNEXPLAINED** | **0** (3 differences tied to their flag by hand review, `tools/runtime/phpblog/findings.json`) |
| **flag-but-wrong** | **37** (`unresolvedCondition`: the unsound decision below) |
| `flag:C2Todo` | 70 |
| `flag:attribute_omitted` | 41 |
| `flag:unresolvedCondition` | 12 |
| `env:legacy_runtime_error` | 6 |

Per page (legacy elements / matched / render match / differences): `index` 123 / 57 / 0.463 / 26 · `blog` 122 / 56 /
0.459 / 26 · `gallery` 101 / 66 / 0.654 / 25 · `contact` 144 / 71 / 0.493 / 33 · `login` 158 / 93 / 0.589 / 26 ·
`error404` 122 / 57 / 0.467 / 24 · `install/upgrade-v2.2` 1 / 1 / 1.000 / 6.

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

### 5.1 An unsound decision under v1.1 (post-freeze finding, not fixed)

Spec §6 generates an untranslatable condition as `unresolvedCondition(...)`, which is `false`. For `{cond && …}` that
hides the content: a flagged abstain. For an **`if / else`** it selects the **`else` arm on every request**: a guess.

| Where | Legacy (anonymous GET) | Generated | Differences |
|---|---|---|---|
| `core.php` 652, the shared shell (every public page): `if ($logged == 'No') { Sign In \| Register } else { Profile menu }` | Sign In \| Register | the logged-in Profile menu | 35 |
| `blog.php` 29: `if ($count <= 0) { 'There are no published posts' } else { the posts loop, pagination }` | the message | an empty list and its pagination | 2 |

The flag (`unresolvedCondition`, todo) is at the `if`, so these are `flag-but-wrong`, not unexplained; but the result is
wrong content shown to every visitor. Root cause (ii): a v1.1 spec rule produced it. **Not fixed** (rules v1.1 final);
recorded in `tools/runtime/phpblog/findings.json` for the owner's decision. HMS shows no difference of this kind on GET (the classifier
finds none in its final run).

## 6. Two-app summary (v1.1 final)

| | HMS | phpBlog |
|---|---|---|
| Pages compared (of page units) | 26 of 26 | 7 of 43 (36 redirect an anonymous GET) |
| Legacy elements | 1386 | 771 |
| **Render match** | **0.996** | **0.520** |
| Differences | 54 | 166 |
| `flag:` (explained, visible gaps) | 52 | 123 |
| `framework:react_hoist` | 2 | 0 |
| `env:` | 0 | 6 |
| **flag-but-wrong** | **0** | **37** |
| **UNEXPLAINED** | **0** | **0** |
| Unsound-decision candidates | 0 (1 under v1.0: R-I1 on `search.php`, fixed by change 1) | **2 sites** (spec §6 `else` arm: `core.php` 652, `blog.php` 29) |

## 7. Limits

- **Two discovery apps.** Phase 4 tests the method and found unsound decisions; it is not the evaluation. WackoPicko
  needs PHP 5 (`mysql_*`) and was not run.
- **GET only, no session.** Nobody logs in and nothing is posted: get_only pages are compared on their anonymous view,
  and phpBlog's 36 redirecting pages not at all. Behaviour after a POST or with a session is not measured (Component 4).
- **Seeds as shipped.** phpBlog has no posts: its lists compare only their empty states.
- **Replay data only for literal SELECTs** of pages and function components (HMS 7 of 16 Lists, phpBlog 29 of 43); a
  shared shell's queries (phpBlog's menu) are not served.
- **Element trees, not pixels.** CSS is compared only in the simulation screenshots; the render match counts elements,
  attributes and text.
- **The replay API is a test fixture**, not Component 1's contract; the real contract may name and shape data
  differently (Stage 4 then reports it).
