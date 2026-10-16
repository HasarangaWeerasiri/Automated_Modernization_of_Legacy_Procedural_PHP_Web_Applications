# Component 2 — Phase 4 Results: Runtime Rendering Check (SO4)

| Item | Value |
|---|---|
| Date | 16 October 2026 (Phase 4 closed) |
| App | HMS (`kishan0725/Hospital-Management-System` @ `777fda4`), a **rule-discovery** app. No evaluation app was used |
| Rules | boundary rules v1.0 (frozen, commit `27c380e`) and v1.1 (post-freeze changes 1–3, `docs/post-freeze-log.md`) |
| Tools | `tools/runtime/` (capture, compare, classify, CSS simulation); `tools/runtime/hms/` (legacy side); `tools/runtime/replay/` (data fixture) |

The question Phase 4 answers: **when the legacy page and the generated Next.js page run on the same data, does the
generated page render the same document, and is every difference explained by a flag the tool emitted at that
node?** An unexplained difference would be an unsound result hidden from the developer.

## 1. Setup

| Side | How it runs |
|---|---|
| Legacy | HMS **unmodified**, from a `git archive` of the pinned commit, in Docker: `php:7.4-apache` with `mysqli`, `mariadb:10.1.48` (the major version its dump came from), seeded with HMS's own `myhmsdb.sql`. Host `localhost` reaches the database by a shared socket; `display_errors=Off` (notices go to a log, not into the HTML). No HMS file is edited |
| Data for the generated side | A **replay API** (test fixture standing in for Component 1): one endpoint per query that the harness resolved as a plain literal `SELECT` with no request input, answered from the same database by a SELECT-only user. An endpoint map entry per query, keyed by its query node id (reconciliation-spec v0.3). 7 of the 16 HMS Lists get data this way; the other 9 read request input or have no typed columns and stay `no_endpoint` (flagged) |
| Generated | Stages 1–5 with that contract / map, assembled as one Next.js app (`next build`, 0 tsc errors), served by `next start` |

## 2. Method

1. **Capture.** Every generated page and its legacy URL, in Chromium (Playwright 1.62.0) with **JavaScript off** (inline
   JS is out of scope; Server Components need none), a plain **GET**, no login. 26 pages, both sides 200. Pages whose
   output depends on request or session input (13) are marked `get_only`: what is compared is their anonymous GET view.
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
   | Alignment | children matched by the longest common subsequence of tag names, recursively |

   **Render match** = matched elements / legacy elements.
3. **Classify** every difference (`tools/runtime/classify.py`), by the node that printed the differing element:

   | Class | Meaning |
   |---|---|
   | `flag:<kind>` | a flag the pipeline emitted **at that node** explains it (a deliberate, visible gap) |
   | `flag-but-wrong:<kind>` | a flag exists at the node, but the generated tree contradicts the browser's tree (the flag claimed browser-equivalent handling) |
   | `env:` / `norm:` | caused by the run / by the comparison (none were needed) |
   | `UNEXPLAINED` | none of these: a hidden wrong result. Root cause recorded in `tools/runtime/hms/findings.json` |

   List rows are told from static elements by comparing with the same rules' build **without** data.

## 3. Results

| | v1.0 (frozen) | v1.1 after changes 1–2 | **v1.1 final** (changes 1–3) |
|---|---|---|---|
| Legacy elements | 1386 | 1386 | 1386 |
| Matched | 1312 | 1374 | **1380** |
| **Render match** | 0.947 | 0.991 | **0.996** |
| List elements matched | 497 / 501 | 497 / 501 | 497 / 501 |
| Static elements matched | 815 / 885 | 877 / 885 | 883 / 885 |
| Differences | 69 | 61 | 54 |
| **UNEXPLAINED** | **2** | 0 | **0** |
| **flag-but-wrong** | **17** | 9 | **2** |
| `flag:inline_js_dropped` | 20 | 22 | 22 |
| `flag:document_shell` | 10 | 10 | 10 |
| `flag:attribute_dropped` | 6 | 6 | 6 |
| `flag:attribute_omitted` | 6 | 6 | 6 |
| `flag:C2Todo` | 5 | 4 | 4 |
| `flag:option_selected_not_converted` | 3 | 3 | 3 |
| `flag:unresolvedCondition` | 0 | 1 | 1 |
| `flag-but-wrong:document_shell` | 11 | 7 | 0 |
| `flag-but-wrong:implicit_close` | 6 | 2 | 0 |
| `flag-but-wrong:implied_end_tag` | 0 | 0 | 2 |

Per page (matched / render match / differences), where anything differs between the versions:

| Page | v1.0 | v1.1 (1–2) | v1.1 final |
|---|---|---|---|
| `admin-panel.php` | 132 / 0.863 / 14 | 149 / 0.974 / 11 | 149 / 0.974 / 11 |
| `admin-panel1.php` | 604 / 0.977 / 17 | 618 / 1.000 / 16 | 618 / 1.000 / 16 |
| `doctor-panel.php` | 217 / 0.927 / 4 | 234 / 1.000 / 3 | 234 / 1.000 / 3 |
| `prescribe.php` | 38 / 0.717 / 10 | 52 / 0.981 / 9 | 53 / 1.000 / 7 |
| `index1.php` | 53 / 0.883 / 8 | 53 / 0.883 / 8 | 58 / 0.967 / 3 |
| `search.php` | 0 / – / 2 (UNEXPLAINED) | 0 / – / 0 | 0 / – / 0 |

All other pages are identical across the three runs: 10 at 1.000, `include/config.php` (an error text only, no element), and 9 that print nothing on GET on either side.
The 52 remaining differences of the final run are deliberate, flagged gaps: inline JS (`onclick`, out of scope), the
document shell (`<body style>`, which belongs in `app/layout.tsx`), attributes React's types do not accept, form values
whose expression is not generated, and Lists with no data endpoint.

### 3.1 The one unsound decision, found and fixed

`search.php` prints its whole page inside `if (isset($_POST['search_submit']))` (line 4). Boundary rule R-I1 (v1.0)
treated an `if` around the whole page as a page guard and rendered the page unconditionally; on a GET the legacy page
prints nothing, the generated page printed the POST-only results view. No specific flag said so: the only flag at the
guard was the generic `unlabelled` review. This is the **one unsound decision** Phase 4 found. Post-freeze change 1
(rules v1.1) restricts R-I1 to guards whose other path redirects; otherwise the guard is a conditional whose condition
(request input) is not translated (`unresolvedCondition`, `needs_request_context`), so the content is hidden and
flagged. Both sides are now blank on GET.

### 3.2 Wrong results behind a flag, fixed by browser equivalence

Four HTML5 parser behaviours made the generated tree differ from the browser's although a flag was present
(`flag-but-wrong`, 17 in v1.0). They are fixed HTML5 rules, so generating them is faithful, not a guess:

| Change | Case (HMS) | Elements |
|---|---|---|
| 2a (spec v0.14) | a `<nav>` written inside `<head>` ends the head; it is body content (4 pages) | 58 |
| 2b (spec v0.14) | `</h2>` closes an open `<h4>` (`admin-panel.php` 267, 283) | 4 differences |
| 3a (spec v0.15) | a `<head>` after body content is ignored; its `<link>`s stay in the body (`index1.php` 6) | 5 |
| 3b (spec v0.15) | `</form>` removes only the form; the `<div>` opened inside it stays open (`prescribe.php` 147) | 2 differences |

**Residual (2):** `index1.php`'s two `<meta>` elements are generated in the body, as the browser builds them, but React
19 always moves `<meta>` into the document `<head>` when it renders (its document-metadata behaviour). The generated
source is right; the rendered DOM differs. They are counted as `flag-but-wrong` (the comparison sees the rendered DOM);
they have no visible effect.

## 4. Styling (simulation)

HMS has no shared header / footer pair, so each page keeps its own `<head>` and the generated pages carry no root
layout (`document_shell`, flagged). As a clearly labelled **simulation** (never written into generated code), the
capture tool adds each legacy page's `<link rel="stylesheet">`s to the generated HTML in transit and takes a second
screenshot (`output/runtime/hms-v1.1-final/generated-with-legacy-css/`). In the final run the generated pages are
already styled where the navbar is generated, because the navbar carries the legacy stylesheet links; what remains
visibly different is the icon font (a relative stylesheet URL the generated server does not serve) and the body's top
padding (`<body style>`, a `document_shell` gap). Screenshots: `output/runtime/hms-v1.1-final/admin-panel1.php.side-by-side.png`.

## 5. Limits

- **One app.** HMS is a rule-discovery app; Phase 4 tests the method and found one unsound rule, it is not the
  evaluation. WackoPicko needs PHP 5 (`mysql_*`) and was not run.
- **GET only, no session.** Nobody logs in and nothing is posted: 13 pages are compared on their anonymous view only.
  Behaviour after a POST or with a session is not measured (Component 4).
- **Replay data only for literal SELECTs.** 9 of 16 Lists have no endpoint (request input in the query, or no typed
  columns); their rows are not generated (in the final run 4 list elements, the `display_specs()` options, are missing, flagged `no_endpoint`).
- **Element trees, not pixels.** CSS is compared only in the simulation screenshots; the render match counts elements,
  attributes and text.
- **The replay API is a test fixture**, not Component 1's contract; the real contract may name and shape data
  differently (Stage 4 then reports it).
