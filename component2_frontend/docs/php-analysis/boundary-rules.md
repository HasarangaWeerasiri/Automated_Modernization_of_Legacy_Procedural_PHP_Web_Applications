# Component 2 — UI Boundary Rules (DRAFT v0.15)

| Item | Value |
|---|---|
| Status | **Draft** — not frozen. Freeze when 2 consecutive new rule-discovery apps add no new pattern (saturation) |
| Date | 7 October 2026 (v0.2: source spot-check corrections · v0.3: owner decisions after the first Stage 2 run · v0.4: wording fixes after the v0.3 run · v0.5, 10 Oct: Member 01 Q14–Q15 added, Q2 note · v0.6, 10 Oct: Q2 note gives the exact condExpr form · v0.7, 11 Oct: R-L3 scope clarified; filter-in-loop added to the checklist as not yet seen · v0.8, 12 Oct: corpus extended — 2 discovery apps queued, 3 evaluation apps added by random split · v0.9, 12 Oct: after the Phase 1 whole-app run — echo-string parts (§1), R-L3b, R-F1/R-F2 written in full, R-I5 evidence re-opened, Q16 · v0.10, 12 Oct: Member 01's answers recorded in §9 · v0.11, 13 Oct: R-I5 hits classified, R-I7 added, R-F1/R-F2 completed for Phase 2b, record-guard candidate, Q18 · v0.12, 13 Oct: Phase 2b accepted; §8 defines "new pattern" for saturation; Q19; phpBlog opened for Phase 3 · v0.13, 13 Oct: phpBlog judged — 4 new patterns (R-I3b, R-I2b, R-I8, `non_html_response`), R-L4 and R-F2 refined, saturation counter 0 / 2 · v0.14, 14 Oct: step-2 corrections (R-F2 end-tag scope, R-I3b later attribute, R-I8 scope + R-I8c, R-L4 reads); attendance opened · v0.15, 14 Oct: attendance judged — no new pattern, counter 1 / 2; R-I8 case (1) scope corrected; `or die` normalisation; built-in mysqli methods; reserve app opened) |
| Owner | Jayawardhana R D L L (IT23213876) — Component 2, Frontend Migration |
| Rule-discovery corpus | HMS (`kishan0725/Hospital-Management-System`, commit `777fda4`), WackoPicko (`adamdoupe/WackoPicko`, commit `cabc1b3`, MIT) |
| Evaluation corpus | **Locked — do not open** (see §7) |

> Rule IDs here (`R-L*`, `R-I*`, `R-F*`) are Component 2 boundary rules. They are **not** the
> `R01`-style `ruleId` values in `labels_*.json`, which are Component 1 concern-rule placeholders.

---

## 1. Method

1. Shape scan of every candidate app (counts only) → scope filter → corpus split, approved by the owner.
2. On rule-discovery apps only: every output loop and every output `if` listed with line range,
   element count (HTML opening tags, **not** source lines), nesting and `else` presence.
3. Every loop checked with a tag-depth tracker: open-tag stack at loop start → where the container cut falls.
4. Every `if` above 3 elements and every loop read **by hand** to classify it.

**Why elements, not lines:** in HMS the longest in-row `if` was 15 lines, but only 2 elements
(`<a><button>`). The extra lines were 6 `<?php echo $row[...] ?>` interpolations inside one URL.

**Line numbers:** v0.1 mixed two conventions (some ranges start at the keyword line, most at the
opening-brace line, so many are 1 line late). From v0.2, **ranges cite the keyword line** (`if` / `for` /
`while` / `foreach`) to the closing-brace line. Rows not yet re-checked are marked ±1.

**Element counting (used by R-I4 / R-I5):** an `if`'s size = the opening-tag count of its **largest
branch**; void elements (`<br>`, `<input>`, `<img>`) count; markup inside a literal `echo "<b>x</b>"`
counts; an `echo` of a variable counts 0.

**Expression-form exits (v0.15).** `X or die(m)`, `X || die(m)`, `X or exit(m)` are, in PHP, exactly
`if (!X) { die(m); }` (short-circuit). They are normalised to that `if` before the rules run, so R-I8 / R-I8c
decide them (e.g. attendance `connect.php` 3–4 `mysql_connect(...) or die('Cannot connect to server')` →
R-I8c, flagged at every include). This is input normalisation like echo parts, not a new boundary rule.

**Echo strings with markup (v0.9).** HMS prints many rows as one `echo "<tr><td>$username</td>…</tr>"`
(e.g. `admin-panel1.php` 286, 339, 399). Such an echo is treated as a **sequence of parts**, in source order:
literal string pieces (PHP-decoded value) and expressions. Literal pieces go through the tag tracker exactly
like inline HTML; each expression is one read. This is not a boundary rule — it only lets the existing rules
see the markup. An expression part that falls **inside a tag name or attribute name** (e.g.
`"<$tag>"`, `"<td $attr>"`) cannot be generated → abstain `markup_from_expression`. The parts come from the
timeline (harness extension `parts`, Member 01 Q16); without `parts`, the echo stays one opaque node as now.

**Tool limits:** the counting scripts are regex-based approximations. They mis-handle (a) whole pages
inside `echo '...'` strings and (b) a `?` inside a `<?= ... ?>` string. Every reported anomaly was
checked against the source by hand. The real Stage 2 runs on Component 1/core output (PHP lexer,
byte-exact `raw`), which does not have these limits.

---

## 2. Loop rules

| Rule | Text | Evidence |
|---|---|---|
| **R-L1** | A loop with output → **Item** component (loop body) + **List** component (container) | HMS 10/10, WackoPicko 8/9 (the 9th is the chunked loop → R-L7) |
| **R-L2a** | List container = nearest HTML tag still open at loop start, **walking past** `tbody`, `thead`, `tr` | Tight cut: HMS 10/10, WackoPicko 6/9 |
| **R-L2b** | If the loop item has **multiple root elements and no wrapper tag**, the List component has **no wrapper** (renders a Fragment); do not take the enclosing section | The 2 WackoPicko loops where R-L2a cut too broad (`guestbook.php` 35–41, `pictures/view.php` 51–59) |
| **R-L3** | Loop body that emits **one element with no child elements** (e.g. `<option>`) → keep `.map()` inline, no Item component. Otherwise → Item component. **Scope (v0.7):** an `if` inside that one element is classified by §3 and §4a exactly like any other `if` (incl. abstain `cuts_across_business_logic`); R-L3 changes only whether an Item component is created, never how its `if`s are judged. An `if` that wraps the whole element is **not** R-L3 (see §8, filter-in-loop) | HMS `<option>` loops in `newfunc.php`, `func1.php`, `func3.php` (none has an `if`; all are in functions, so no mock has R-L3 until Member 01 Q4) |
| **R-L3b** Text-only loop (v0.9) | Loop body that emits **text and expressions but no element** → inline `.map()` returning a keyed Fragment, no Item component | HMS `include/header.php` 38 (Phase 1 pattern log) |
| **R-L4** | Nested loops → resolve innermost first; the inner List becomes one node of the outer Item | Design only until v0.12 (not observed in HMS / WackoPicko) (seen only in out-of-scope osCommerce / vBulletin) · **v0.13, first real cases** (phpBlog `admin/dashboard.php`, `gallery.php`): the inner Item receives the outer loop's row as a prop `outer` when the inner loop's iterable or reads use it; any other outer variable → `unresolved` **Reads (v0.14):** a read whose `source.fetchNodeId` is the **outer** loop's fetch is attributed to the outer collection (Stage 3/4) and generated as `outer.<field>`, not as a gap of the inner List |
| **R-L5** | Loop with no output → not a UI boundary; ignore | HMS 3/3 (incl. one inside an HTML comment) |
| **R-L6** | Tag tracker **ignores unmatched closing tags** and flags them for Stage 5 (JSX will not compile them). Tag balance is checked **per branch**, not only on the linear sequence | HMS `doctor-panel.php` 246 (`</tr></a>`); WackoPicko `<form>` inside `<table>`, `<p>` inside `<ul>` |
| **R-L7** Chunked loop | A loop body that **closes and reopens its own container** (e.g. rows of four) → **abstain and flag** `chunked_container`. Not a rule yet: seen once. If it recurs, candidate rule = chunk the list, then map each chunk | WackoPicko `include/html_functions.php` 97–129 (lines 109–117) |

Loop kinds observed: `while`, `foreach`, C-style `for`. Item shapes observed: table rows (`<tr>`), cards (`<div>`), list items (`<li>`), options (`<option>`).

---

## 3. `if` rules (structural — replaces the old size-based Rule 3)

| Rule | Text | Evidence |
|---|---|---|
| **R-I1** Page guard | An `if` that wraps the **whole page output** (incl. layout calls) → **not a component**. The page renders normally; the condition is form/redirect logic owned by Component 1. Single-branch only: a page-level `if/else` is not covered yet and falls through to the other rules | WackoPicko 4/4 (`users/login.php` 30–49, `users/register.php` 39–64, `pictures/upload.php` 74–97, `admin/login.php` 22–32) |
| **R-I2** List guard | An `if` whose body contains an output loop **and whose own concern is not `business_logic`** → the List component's **empty-state condition**. Any heading inside the guard belongs to the List. The non-loop branch → Empty component. A `business_logic` guard around a loop is an authorization check, not an empty-state check → **abstain** (`cuts_across_business_logic`, §5) | WackoPicko 8/8 (all `if ($list)` emptiness checks) |
| **R-I3** Attribute conditional | An `if` inside an HTML attribute value → JSX attribute expression. **Never** a boundary | WackoPicko 5/5 (menu `class="current"`) |
| **R-I3b** Attribute-presence conditional (v0.13) | An `if` **inside a start tag, between attributes** (not inside a value), whose every arm prints only **complete literal attributes** (`selected`, `checked`, `class="nav-item"`) → each attribute becomes a conditional prop (`attr={cond ? "v" : undefined}`; boolean → `cond`). If the same attribute already appears **earlier** in the tag, the conditional one has no effect in a browser (first wins) → dropped, flag `duplicate_attribute`. If it appears **later** (v0.14), the conditional one wins when its condition holds → `attr={cond ? "conditional value" : "later value"}`. An arm printing anything else (a variable, half an attribute) → abstain `partial_attribute_output` **for that tag's attributes only** (spec §5b fallback) | phpBlog: 59 `if`s in 9 units (`admin/comments.php` 67 `<option … <?php if(...) echo 'selected'; ?>>`, `admin/header.php` 258 `<li <?php if(...) echo 'class="nav-item"'; ?>>`). 0 in HMS / WackoPicko |
| **R-I2b** Multi-loop list guard (v0.13) | An R-I2 guard whose body holds **several** output loops → one conditional wrapper holding all their Lists; the guard's other arm → Empty. Same label condition as R-I2 | phpBlog `index.php` 14 (`if ($count > 0)` around the carousel indicators loop and the carousel items loop) |
| **R-I4** Small content `if` | Content `if` with **≤ 3 elements** (with or without `else`) → inline conditional / ternary | WackoPicko 11/11, HMS 12/12 |
| **R-I5** Fallback | Content `if` with **≥ N = 4 elements** (i.e. above the R-I4 limit — no gap) that matches none of R-I1–R-I3 → own Conditional component | v0.8: "never triggered" (regex scan of `if`s). **v0.11: classified.** All 10 hits are in HMS and all are **request-handling** conditionals, not display conditionals: 2 × `admin-panel.php` 18/40 (POST booking handler; only output = `<script>alert()</script>` → now R-I7) and 4 search pages × 2 (`appsearch` 13/19, `doctorsearch` 12/18, `messearch` 11/17, `patientsearch` 11/17): an outer `if (isset($_POST['…_submit']))` that wraps the whole `<body>` content, and an inner found / not-found `if/else` (alert vs result table, one fetched row). Labels would not change this (R-I1/R-I2 do not read labels to fire). Condition → `needs_request_context` (Stage 5) |
| **R-I6** Sibling chain | Consecutive sibling `if`s that output into the same parent element with mutually exclusive conditions → **one** conditional value. Exclusivity cannot be checked without condition text, so status is **`review`**, reason `exclusivity_not_verified`, until Member 01 Q2 is answered | HMS: 3 chains × 3 `if`s (status cell in `admin-panel.php`, `admin-panel1.php`, `doctor-panel.php`) |

| **R-I8** Exit-terminated arm (v0.13) | An `if` arm that **always** ends with `exit` / `die` (a statement in the same `stmts` list, read from the AST) makes the rest of the enclosing block its implicit `else`. (1) If the arm's only output is a **redirect** (`<meta http-equiv="refresh">`, a `<script>` setting `location`) → **redirect guard**: not a component; the rest renders; flag `redirect_guard` (review — the redirect belongs to C1, like R-I1). (2) Otherwise → a conditional: `cond ? <arm output> : <rest>`. `die("msg")` / `exit("msg")` print their message: that message is the arm's output | phpBlog 45 (`admin/header.php` 12 `echo '<meta http-equiv="refresh" …>'; exit;`, `category.php` 16); WackoPicko `guestbook.php` 17 (`die()` with a message) **Scope (v0.15, corrects v0.14):** case (1) — a **redirect guard** — applies at **any depth** in a page (the page renders as if not redirected; the redirect is C1's, flagged — the same simplification as at top level). Case (2) applies only to a `then` arm at a page's own top level whose rest closes nothing opened before the `if`; any other non-redirect exit arm (nested, `else` arms) → abstain `exit_arm_not_covered`. (v0.14 wrongly restricted case (1) too, which made 24 nested phpBlog redirect guards abstain.) Case (2) has **no real instance** yet (tests only); WackoPicko's `die()` is nested three `if`s deep → abstains. |
| **R-I8c** Exit arm in an included file or function (v0.14) | An exit-terminated arm inside an **included file** or a **called function** (e.g. phpBlog `config.tpl` 10–13 `printf("Database connection failed…"); exit();`, WackoPicko `Users::check_login` `die(mysql_error())`) is **not generated**; for everything else that file / function counts as non-printing (so its include or call is not a gap). Flag `exit_output_not_generated` (review) **at every include / call site**, with the arm's file:line. Such arms are error stops; with C1 labels they are expected to be excluded as `data_access`, which C2 may not assume (hard rule 3) — hence the flag | phpBlog `config.tpl` (8 includes, 22 helper calls), WackoPicko `include/users.php` 108 |
| **R-I7** No output left (v0.11) | An `if` (or loop) whose **only** output is inline JS (`<script>`, dropped by spec §5) has nothing to render → **not a UI boundary** (same as R-L5). The JS is still flagged `inline_js_dropped` | HMS `admin-panel.php` 18 / 40 (POST handler alerts); HMS uses `echo "<script>alert(…)</script>"` in every POST handler |

**Statement for the report (rewritten in v0.13):** *Boundary decisions are structural (page guard, list
guard, attribute, attribute presence, early exit, inline). A size threshold N = 4 is the fallback for content
conditionals no structural rule covers. In HMS it fired only on request-handling conditionals; in phpBlog it
also fired on 30 display conditionals (role checks, login state, settings switches), each becoming its own
Conditional component.* (v0.11's "no display conditional above 3 elements" was true for HMS and WackoPicko
only.)

---

## 4. Function and layout rules

| Rule | Text | Evidence |
|---|---|---|
Inputs: the function's own timeline, and the **call entries** in the page timeline
(`{kind: "call", callee, outputsInside}`, Member 01 Q4 — proposed) plus the harness `includes` list.
Without them, R-F1/R-F2 do not fire.

**Call resolution (harness, v0.11):** a call whose name is defined in several files resolves only through
the page's own **literal** includes (`include('newfunc.php')`), followed transitively; if exactly one
reachable file defines it → that function; otherwise `callee: null`. (HMS `admin-panel.php:359`
`display_docs()` → `newfunc.php`.) Method calls on objects stay unresolved (Q11), **except** (v0.15, the built-in subset of Member 01's
proposed Q11 v1): a method of PHP's own `mysqli` / `mysqli_result` / `mysqli_stmt` classes, on a receiver whose
only definition in the app is `new mysqli(...)` / `mysqli_connect(...)`, or which is assigned in the same block
from such a receiver's `query()` / `prepare()` / `mysqli_query(...)` — these are documented PHP built-ins and
never print → no call entry (like built-in functions). Everything else stays `unresolved_call`.

| Call entry | Generated at the call site |
|---|---|
| `callee` resolved, `outputsInside: true` | the R-F1 component |
| `outputsInside: false` | nothing |
| `callee: null` or `outputsInside: "unknown"` | `<C2Todo reason="unresolved_call" />` (only where output is possible) |

| Rule | Text | Evidence |
|---|---|---|
| **R-F1** Function component | A user function with output, called from ≥ 1 page, whose markup is **tag-balanced on its own** → one component (`PascalCase(function)`) in `components/`, placed at **each call site** at the call's position (inside whatever element is open there, e.g. `<select>`). Parameters → props: a parameter whose argument is a **literal at every call site** (or the default value) becomes a typed prop (`string` / `number` from the literal); any other parameter is not a prop, and reads of it → `unresolved`. Recursive calls → abstain `recursive_function` | WackoPicko `thumbnail_pic_list` (4 callers); HMS `display_docs()` / `display_specs()` into `<select>` (`admin-panel.php` 359, 331) |
| **R-F1b** Caller-opened container (v0.11) | A loop inside a function whose List container is **not** open in the function (the caller opens it, e.g. `<select>` around `display_docs()`) → List without wrapper (Fragment, as R-L2b) inside the function component; the container stays in the caller's markup around the call site. Replaces `container_undetermined` for these loops | Phase 2a pattern log: 4 loops (HMS `newfunc.php` / `func*.php` `<option>` loops) |
| **R-F2** Shell pair | A header unit H and a footer unit F (functions, or included files) such that: (1) H leaves open-tag stack S; (2) F's unmatched closing tags close exactly S, in reverse; (3) on every page that calls H, F is called later, both at **top level** (not inside a loop/branch), with all other page output between them. Then: `<html>`, `<head>`, `<body>` from H/F → root `app/layout.tsx` (literal parts only; anything dynamic there → flag `document_shell`); the rest → a **shell component** `components/<H>Shell.tsx` with `{children}` at the gap and H's/F's literal-argument parameters as props; each page wraps its content in `<…Shell …props>`. Page output before H or after F → abstain `output_outside_shell`. A page that calls H without F, or tags that cross a function boundary without forming such a pair → abstain `tag_crosses_function` | WackoPicko `our_header()` (26 files) / `our_footer()` (25 files): H opens `<html><body><div class="container">`, F closes `</div></body></html>` |

**R-F2 condition (2), refined in v0.13 (phpBlog admin shell), made exact in v0.14:** "F closes exactly S" is
checked **after the HTML5 implicit closes** every browser applies: (a) `<head>` is closed by the first body
content or `<body>` tag; (b) an end tag of the HTML5 **block group** (`address, article, aside, blockquote,
center, details, dialog, dir, div, dl, fieldset, figcaption, figure, footer, header, hgroup, listing, main,
menu, nav, ol, pre, section, summary, ul`) closes the nearest open element of its name **if it is in scope**
and every element open inside it (so `</div>` closes an open `<main>` inside that `<div>`); (c) elements
still open at `</body>`, `</html>` or end of file are closed there. Other end tags (`</span>`, formatting
elements like `</a>`, `</b>`, `</form>`, `</p>`) do **not** close what is open inside them — they follow
their own HTML5 rules (ignored, adoption agency, …) → if pairing needs them, abstain `tag_crosses_function`. phpBlog's
`admin/header.php` leaves `<head>` and `<main>` open; `admin/footer.php` closes the rest and ends with
`</body></html>` — the browser builds the same tree as an explicit pair. Anything else that F leaves
unclosed → still `tag_crosses_function`.

**Why a shell component and not `layout.tsx` for the menu part:** Next.js layouts receive no per-page props,
but `our_header("guestbook")` passes the page name that sets the menu's `class="current"`. A shell component
keeps that argument; `layout.tsx` keeps only what every page shares.

**Route groups (v0.11).** Next.js applies the root layout to **every** route, so a shell's `<html>/<head>`
must not leak onto pages that do not use it. Pages using shell H go under `app/(<h>)/` with their own root
`layout.tsx` (H's literal `<html>/<head>/<body>`); every other page goes under `app/(plain)/` with a minimal
`<html><body>{children}</body></html>` layout and keeps `document_shell`. (Next.js "multiple root layouts".)

**HMS has no shell pair:** each HMS page carries its own full `<html>…</html>`; `header.php` (included once,
by `index1.php`) is a complete mini-document. HMS pages keep the `document_shell` flag. Extracting a common
layout from repeated per-page markup would be a similarity guess → not done.

---

## 4a. Status rules

| Situation | Status |
|---|---|
| Stage 1 `review` on an **output** node | Carries through to the component that contains it |
| Loop labelled `mixed` (any reason — decisions use `concern` only, never the label's `reason`) | Information only. The List stays `ok`; the loop's concern is recorded on the List as `loopConcern` |
| `if` or loop that is itself `undecided` or unlabelled | `review` carries to the node built from it (CONTEXT rules 3–4) |
| R-I6 sibling chain | `review` (`exclusivity_not_verified`) |
| Any abstain case (§5) | `abstain` |

## 5. Abstain (flag, do not guess)

- No rule matches the structure.
- A **content** boundary (R-I4 / R-I5) would cut across a node labelled `business_logic`, `data_access` or `mixed`. **Exception:** an R-I2 list guard labelled `data_access` or `mixed` is allowed, because emptiness checks usually touch data (`if (mysqli_num_rows($r) > 0)`, `if ($result = mysqli_query(...))`). A `business_logic` guard is never a list guard.
- Container cannot be determined (unbalanced tags that R-L6 cannot resolve).
- A tag opens in one branch and closes outside it (unbalanced per branch).
- Loop body closes and reopens its own container (R-L7).
- Node enclosed by `switch_case` or `try_catch` (no rule yet).
- Output comes from an **included file** that prints and is not half of a shell pair → abstain `cross_file_output` (v0.13 name). (Function calls are handled by R-F1 and the call-entry table in §4.)
- An exit-terminated arm that R-I8 does not cover (nested, `else` arm, rest closes earlier tags) → abstain `exit_arm_not_covered` (v0.14).
- The unit is **not an HTML response** (`header('Content-Type: …xml…')`, RSS, sitemap) → abstain `non_html_response` for the whole unit; it is an API route (Next.js Route Handler), owned by C1, not a page (v0.13; phpBlog `rss.php`, `sitemap.php`).
- A content `if` labelled `business_logic` (authorization guard), whether or not it holds a loop → abstain `cuts_across_business_logic`. Candidate rule, **not adopted** (one hand-written mock only): render the content normally and delegate the condition to C1 (server-side 403 / no data). Decide after C1 confirms how authorization appears in the contract.

---

## 6. Evidence tables

### 6.1 HMS loops (`777fda4`)

| File | Loop | Open stack at loop | Item | Container |
|---|---|---|---|---|
| admin-panel.php | 474–518 | div>div>div>table>tbody | `<tr>` | `<table>` 452–520 |
| admin-panel.php | 556–582 | same | `<tr>` | `<table>` 528–585 |
| admin-panel1.php | 279–293 | same | `<tr>` (single `echo`, 5 cells) | `<table>` 263–297 |
| admin-panel1.php | 330–348 | same | `<tr>` | `<table>` 312–352 |
| admin-panel1.php | 386–411 | same | `<tr>` | `<table>` 365–415 |
| admin-panel1.php | 459–489 | same | `<tr>` | `<table>` 434–491 |
| admin-panel1.php | 569–582 | same | `<tr>` | `<table>` 552–584 |
| doctor-panel.php | 187–247 | same | `<tr>` | `<table>` 162–249 |
| doctor-panel.php | 286–301 | same | `<tr>` | `<table>` 256–304 |
| doctor-panel.php | 333–350 | same | `<tr>` | `<table>` 312–352 |

No-output loops (R-L5): admin-panel.php 146, 315 (inside `<!-- -->`). String-building loop (`$output .=`, PDF): admin-panel.php 87 — out of scope, flagged.

### 6.2 HMS `if`s inside loops

| Kind | Count | Elements |
|---|---|---|
| Status text (`echo "Active"` etc.), 3-way sibling chains | 9 | 0 (text only) |
| Cancel / Prescribe link + `else` text | 3 | 2 (`<a><button>`) |

### 6.3 WackoPicko loops (`view_flymake.php` excluded — identical copy of `view.php`; ranges ±1 except the corrected row)

| File | Loop | Item | Container | Result |
|---|---|---|---|---|
| cart/confirm.php | 36–39 | `<tr>` | `<table>` | Tight |
| cart/review.php | 32–36 | `<tr>` | `<table>` | Tight |
| cart/review.php | 43–47 | `<tr>` | `<table>` | Tight |
| pictures/view.php | 101–106 | `<div>` card | `<div id="related">` | Tight |
| pictures/view.php | 115–119 | `<div>` card | `<div id="same-upload">` | Tight |
| users/similar.php | 22–26 | `<li>` | `<ul>` | Tight |
| include/html_functions.php | 97–129 (C-style `for`) | `<li>`, but every 4th iteration emits `</ul></div><div><ul>` | `<ul>` opened inside `if ($pictures)` (95), closed after the `else` (137) | **Abstain** → R-L7; `<ul>` crosses the if/else → R-L6 per-branch check |
| guestbook.php | 35–41 | 2 × `<p>`, no wrapper | page column (incl. `<h2>`, `<h4>`) | Too broad → R-L2b |
| pictures/view.php | 51–59 | 2 × `<div>`, no wrapper | comments section (incl. form) | Too broad → R-L2b |

### 6.4 WackoPicko output `if`s (28)

| Type | Count | Elements |
|---|---|---|
| Page guard (R-I1) | 4 | 7–30 |
| List guard (R-I2) | 8 | 2–26 |
| Attribute conditional (R-I3) | 5 | 0 |
| Small content (R-I4) | 11 | 1–3 |

---

### 6.5 Where R-F1 / R-F2 would apply (not implemented — blocked on Member 01 Q4)

| Rule | Function | Called from |
|---|---|---|
| R-F2 | `our_header()`, `our_footer()` | `guestbook.php` 25 / 60, `pictures/view.php` 40 / 127, `users/login.php` 31 / 48 (+ other pages) |
| R-F1 | `thumbnail_pic_list($pictures, $high_quality)` | 4 pages (mock `wp_thumbnails`) |
| R-F1 | `error_message()` | `guestbook.php` 29, `users/login.php` 37 |

## 7. Corpus split (approved before deep reading)

| Group | Apps |
|---|---|
| Rule discovery (done) | HMS, WackoPicko |
| Rule discovery (queue, in this order) | 1. `codeplay-code/phpBlog-blogScript` @ `4ffe3c9` (GPL-3.0, blog) · 2. `ihumaunkabir/online-attendance-management-system-1.0` @ `3178104` (MIT, attendance, `mysql_*`) · reserve, only if saturation is not reached: `siamon123/warehouse-inventory-system` @ `69fdd1c` (MIT, inventory; DB wrapper class — borderline) |
| Evaluation (**locked**) | crud-php-mysqli, PHP-MySQL-CRUD-Application (procedural), `anirbandutta9/College-Notes-Gallery` @ `8c1cf3d` (MIT), `m45rud/ams-native` @ `3c4d475` (MIT), `omd01/School-Management-System` @ `1e5426d` (MIT) — 5 apps |
| Out of scope | osCommerce 2 (2.4-dev, namespaced OOP core, DB wrapper), vBulletin 3.8.11 (DB-stored templates via `eval`; commercial licence), PHPMailer, Monolog (libraries). Borderline (OO mysqli / wrapper): AliDev-0/PHP-E-commerce-Platform, buddhirangana/alumni-management-system, psyclone20/Library. Licence doubt: naemazam/Methuen-Online-Food-Ordering-System |

**How the 5 new apps were split (12 Oct 2026):** a shallow metadata scan (file counts, grep counts, licence; no code read) found 5 clean, licensed, procedural candidates. They were split **at random** before anyone read their code: `random.Random(20261012).sample(sorted(apps), 3)` → evaluation; the other 2 → discovery queue, ordered by `random.Random(20261013).shuffle`. Reproducible, and not chosen by looking at the code.

---

## 8. Pattern checklist (for saturation)

**What counts as a new pattern (v0.12, fixed before Phase 3 opens a new app).** A *pattern* is one row of
this table: a class of structural situation. An app **adds a new pattern** if, after the whole-app run, any
construct (a) needs a rule or abstain reason that §2–§5 do not have, or (b) is handled by an existing rule
**wrongly** (a generated result that does not match the legacy page). More instances of an existing row,
more abstains for an existing reason, an implementation gap for a rule that is already written (e.g. R-F2 for
included files), and unresolved reads (the DFG's job) are **not** new patterns. Saturation: two consecutive
discovery apps add no new pattern → the rules are frozen.

**Saturation counter: 1 / 2** (14 Oct 2026). **Attendance (app 4) added no new pattern.** Judged candidates:
(1) `try/catch` around output (6) — §5 already abstains; `try` moves from "not yet seen" to seen; not new.
(2) `X or die(m)` in included `connect.php` (8) — **judgement call, recorded**: semantically an exit arm
(PHP short-circuit), so it is normalised into R-I8c (§1), not a new rule. Before the fix it was dropped
**silently**; the fix makes it a flagged R-I8c case. A stricter reading would count it as new and set the
counter to 0 / 2 — the report states both readings. (3) `ob_start()` (10) — output identical (buffer
flushed at exit); not a structural pattern. (4) filter-in-loop / first-iteration-only `if ($i <= 1)`
(student / teacher `report.php`) — now seen, inside loops that already abstain (unclosed `<tbody>` opened in
a branch); the narrowed fallback abstains; a rule would need loop-counter semantics (a guess) → not new.
Also seen: auth redirects by `header('location: …')` **without** `exit` (attendance, 8 pages) — no output, so
not a boundary; now flagged `header_redirect` (info) for C1 (spec v0.10).
Next: the reserve app (app 5). If it adds no new pattern → **freeze**. If it does → counter 0 / 2 and more
discovery apps are needed.

**Earlier — saturation counter 0 / 2** (13 Oct 2026). phpBlog (app 3) added 4 new patterns: attribute-presence
conditional (R-I3b), multi-loop list guard (R-I2b), exit-terminated arm (R-I8), non-HTML response
(`non_html_response`). **Not counted as new** (by the definition above): markup kept in a variable and
echoed later (that is echo parts + data flow — Member 01's DFG; until then `unresolved`), nested loops (R-L4
already written; first real cases), header/footer includes that pair only after implicit closes (R-F2
refined, not a new rule), includes of printing files (existing §5 bullet), R-I5 on display conditionals (the
rule fired as written). Next: attendance (app 4); if it adds a new pattern, the reserve app follows. (Step 2 corrections on 14 Oct — R-I8c, R-F2 end-tag scope, R-I3b later attribute, R-L4 reads — come from implementing phpBlog's rules on the three opened apps, not from a new app; they do not change the counter.)

| Pattern | Seen in |
|---|---|
| Loop → table rows | HMS, WackoPicko |
| Chunked loop (closes and reopens its container) | WackoPicko (abstain) |
| C-style `for` loop | WackoPicko |
| Loop → cards / list items | WackoPicko |
| Small status `if`, sibling chain | HMS |
| Rows printed by one echo string (`echo "<tr>…$x…</tr>"`), concatenated / multi-argument echo with markup | HMS (Phase 1: 3 + 7 + 5 + 6) |
| Text-only loop | HMS `include/header.php` 38 |
| Attribute-presence conditional (`<option <?php if(...) echo 'selected'; ?>>`) | phpBlog (59) → R-I3b |
| List guard with several loops | phpBlog `index.php` 14 → R-I2b |
| Arm ending in `exit` / `die` (redirect guards, error stops) | phpBlog (45), WackoPicko (1) → R-I8 |
| Non-HTML response (RSS, sitemap) | phpBlog `rss.php`, `sitemap.php` → `non_html_response` |
| Markup or attribute text kept in a variable, echoed later (`$image = '<img …>'`, `$active1 = 'class="active"'`) | phpBlog (49), HMS (1) — data flow, not a boundary rule; `unresolved` / `markup_from_expression` until the DFG |
| Nested loops | phpBlog `admin/dashboard.php`, `gallery.php` (4) → R-L4 |
| Shell via `include header.php` / `footer.php` with implicit closes | phpBlog admin (24 pages) → R-F2 refined |
| Request-handling `if` (`isset($_POST['…_submit'])`) wrapping a page's body, with an inner found / not-found check | HMS: 4 search pages (copies of one pattern) + `admin-panel.php` handler. **Candidate, not a rule:** *record guard* — a found / not-found check on one fetched row → single-record component + Empty. Seen in one app only; **phpBlog does not use it** (its not-found checks redirect and `exit` → R-I8). Stays a candidate |
| Invalid nesting the browser repairs (`<p>` closed by a block element, rows without `<tbody>`, `<form>` / `<input>` inside `<table>`) | WackoPicko 9 (Phase 2a nesting check) |
| Page output inside a List's container outside the loop (`if(!$result) echo mysqli_error(...)`) | HMS `admin-panel.php`, `doctor-panel.php` — likely excluded once C1 labels exist (data_access); until then List-level fallback |
| Element with no React type (`<frameset>`) | WackoPicko `test.php` → fallback |
| `die()` / `exit()` with a message | WackoPicko `guestbook.php` 17 — abstain (1 case) |
| Layout via `include header.php` | HMS `index1.php` 2 — but `header.php` is a complete mini-document, not a shell |
| Page guard, list guard, attribute conditional | WackoPicko |
| Function = component (`thumbnail_pic_list`) | WackoPicko |
| Layout via functions | WackoPicko |
| Malformed HTML | HMS, WackoPicko |
| Cross-file output (function echoes `<option>` into a page's `<select>`) | HMS |
| Escape wrapper on output (`h()` — 89 of 116 `<?= ?>` tags) | WackoPicko |
| `try / catch` around output | attendance (6) — abstain (§5) |
| `X or die(m)` error stop in an included file | attendance `connect.php` ×4 (8) → normalised into R-I8c |
| Auth redirect by `header('location: …')` without `exit` | attendance (8 pages) → `header_redirect` (info) |
| First-iteration-only `if ($i <= 1)` inside a loop | attendance `report.php` ×2 — abstains (fallback) |
| **Not yet seen:** switch; **filter-in-loop** (`if` wrapping the whole loop body, e.g. `while(...) { if ($row['n'] > 0) { echo '<option>…'; } }` — synthetic test only; until seen in a real app it abstains via the narrowed fallback) | — |

---

## 9. Open questions

**Member 01's answers (12 Oct 2026)** — full table in CONTEXT.md §3:
Q2 **answered** (condExpr = cond node source, trimmed, none on `else`). Q13–Q15 **answered** (1-based lines,
0-based byte columns, exclusive end, plus start/end byte; loops carry loc). Q4 **proposed**: `call` entry at
the call site (`callee`, `outputsInside`), no inlining — R-F1/R-F2 will read call entries. Q1 **proposed**:
`SELECT *` ambiguous unless schema in repo + one table + no JOIN (`basis: schema-expanded`). Q11
**proposed**, not built. Q3, Q5–Q10, Q12, Q16, Q17 open. Timeline, labels, contract: not built yet.

**For Member 01 (core / timeline)**
1. `SELECT *`: always `ambiguous`, even for a single table?
2. Condition text for JSX: read from `ast/<fileId>.json` via `condNodeId`, or a new `condExpr` field? _(Until answered, C2 mocks carry `condExpr` on branch enclosures as an agent extension; Stage 5 needs it. Form: the byte-exact source between the `if`/`elseif` parentheses, with the whitespace just inside them trimmed; no `condExpr` on `else`. Is this form acceptable, or will the core emit something else?)_
3. `$username = $_SESSION['username']` — will the DFG trace back to the session key?
4. Function output: does the page timeline include output of called functions (e.g. `display_specs()`, `thumbnail_pic_list()`) at the call site?
5. Escape wrappers: `<?= h($row['x']) ?>` → `computed` with `derivedFrom` → `db_row_field`?
6. Output inside an HTML attribute (`class="<?php if(...){echo 'current';} ?>"`) — how is it represented?
7. Literal output (`echo "Active"`): `sourceKind: "literal"` with `var`/`path` null?
8. Which endpoint serves which page/timeline (for Stage 4)?
9. What fields do `switch_case` and `try_catch` enclosures carry?
10. For a C-style `for` loop (`for ($i=0; $i<count($p); $i++) { $pic = $p[$i]; … }`), what are `iterExpr`, `valueVar`, `keyVar`?
11. Does the DFG follow values through class methods in other files (e.g. `Guestbook::get_all_guestbooks()`)? If not, every WackoPicko read stays `unresolved`.
12. How is a static property read (`Users::$VIEW_URL`) represented?
13. Can timeline sequence entries carry `loc` (same shape as the AST node envelope: `startLine`, `endLine`, `startCol`, `endCol`)? Flag reports need file and line (NFR4).
14. `loc` conventions: are lines and columns 0- or 1-based, counted in bytes or characters, and is the end inclusive or exclusive? (nikic's own node attributes are `startLine`/`endLine` (1-based) and `startFilePos`/`endFilePos` (0-based byte offsets); columns would be derived from those. The C2 mocks currently use 1-based, bytes, inclusive end.)
15. Can loop enclosures (and branch enclosures) also carry `loc`? A List's collection need has no line today.
16. Can an echo entry carry its **parts** in order — literal pieces (PHP-decoded) and expressions (each linked to its read)? HMS prints whole table rows as one interpolated `echo "<tr>…$x…</tr>"`; without parts that markup is invisible to C2. The harness emits `parts` as an extension.
17. Copy propagation: will a read of `$username` after `$username = $row['username'];` (same block, no reassignment) resolve to `$row['username']`? And for interpolated SQL (`… where fname='$fname'`) — is the table/column resolved when interpolation appears only in value positions? (40 row reads in HMS + WackoPicko depend on these.)
18. How are `fileId`s assigned in a whole-app run (manifest order? alphabetical? excluded folders)? Your AST has `admin-panel1.php` = `f001`; the harness numbers HMS files differently (`f002`). The id is the join key, so the file numbering must match too.
19. Calls and parameters: will a `call` entry carry the call node's AST id (so arguments come from the AST), and will the DFG mark a function parameter that is never reassigned in the body? R-F1 props need both; the harness emits `args` and `params[].readOnly` as extensions.
20. Will the timeline mark `exit` / `die` after output? Until then C2 reads them from the AST (an `Expr_Exit` statement in the same `stmts` list). Empty `if` arms are also read from the AST (`elseifs`, `else`), so arm structure needs no new field.

**For Component 1:** HMS never includes `include/checklogin.php`, so it has no working login gate.
**For Component 4:** WackoPicko has intentionally unescaped output (`<?= $comment['text'] ?>`, `<?= $guest["comment"] ?>`). React escapes by default, so this output will differ — an expected, intended difference. WackoPicko uses `mysql_*` → needs PHP 5.x to run for runtime capture.

---

## 10. Decision log entries (for Appendix E)

| Date | Decision | Reason |
|---|---|---|
| 6 Oct 2026 | Count `if` size in HTML elements, not lines | HMS's longest in-row `if` was inflated by URL interpolations |
| 6 Oct 2026 | Replace size-based Rule 3 with structural rules R-I1–R-I6; keep N = 4 as fallback only | All large `if`s in 2 apps were page guards or list guards; largest plain content `if` = 3 elements |
| 6 Oct 2026 | Corpus split: HMS + WackoPicko for rules; 2 CRUD apps locked for evaluation | Keep evaluation apps unseen during rule design |
| 6 Oct 2026 | Stop adding rule-discovery apps at saturation (2 consecutive apps, no new pattern) | Defensible stopping point |
| 6 Oct 2026 | Chunked loop = abstain (R-L7), not a rule | Observed once; a rule from one case would be over-fitting |
| 6 Oct 2026 | Tag balance checked per branch | WackoPicko `<ul>` opens inside `if`, closes after `else` |
| 7 Oct 2026 | R-I5 fires at ≥ 4 elements (no gap after R-I4's ≤ 3) | A 4-element `if` matching no rule would otherwise abstain for an arbitrary reason |
| 7 Oct 2026 | R-I2 only for guards not labelled `business_logic` | Without condition text, the label is the only signal separating an emptiness check from an authorization check |
| 7 Oct 2026 | R-I6 chains → status `review` | Collapsing non-exclusive `if`s would change behaviour; an unverified assumption must stay visible |
| 7 Oct 2026 | A DB loop labelled `mixed` does **not** make its List `review` | Every DB-driven loop fetches in its header; flagging all of them would inflate the flag rate with no action for the developer |
| 7 Oct 2026 | Authorization guard stays abstain; delegation rule recorded as candidate only | One hand-written case; needs C1's contract decision |
| 7 Oct 2026 | R-I2 allows `data_access` / `mixed` guards; only `business_logic` abstains | Procedural PHP emptiness checks usually touch data; authorization is the only case that is not an empty state |
| 7 Oct 2026 | Mixed loops are information regardless of label reason | Decisions may use `concern` only (CONTEXT hard rule 2), so the "because" cannot be checked |
| 12 Oct 2026 | 5 new licensed procedural apps split at random: 3 evaluation, 2 discovery | Avoids picking easy evaluation apps; seed recorded so the split is reproducible |
| 12 Oct 2026 | Saturation is judged on **whole apps** run through the harness, not on excerpts | Excerpts were chosen by us and hide patterns |
| 13 Oct 2026 | phpBlog adds 4 new patterns → R-I3b, R-I2b, R-I8, `non_html_response`; counter 0 / 2 | Judged by the §8 definition fixed before the app was opened |
| 14 Oct 2026 | Attendance: no new pattern → counter 1 / 2 | Judged by the §8 definition; the `or die` normalisation is recorded as a judgement call with both readings |
| 14 Oct 2026 | R-I8 redirect guards at any depth (corrects v0.14) | Nesting does not change what a redirect guard means; restricting it made 24 sound cases abstain |
| 14 Oct 2026 | Built-in mysqli methods are not calls | PHP built-ins never print; same treatment as built-in functions; matches Member 01's proposed Q11 v1 |
| 13 Oct 2026 | R-F2 pairing checked after HTML5 implicit closes | phpBlog's admin shell leaves `<head>`/`<main>` to the browser; the tree is the same as an explicit pair |
| 13 Oct 2026 | Markup in variables is not a boundary rule | It is data flow (Member 01's DFG); a C2 rule would guess values across branches |
