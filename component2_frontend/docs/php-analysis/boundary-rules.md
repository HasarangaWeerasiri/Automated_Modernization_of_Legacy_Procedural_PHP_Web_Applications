# Component 2 — UI Boundary Rules (DRAFT v0.10)

| Item | Value |
|---|---|
| Status | **Draft** — not frozen. Freeze when 2 consecutive new rule-discovery apps add no new pattern (saturation) |
| Date | 7 October 2026 (v0.2: source spot-check corrections · v0.3: owner decisions after the first Stage 2 run · v0.4: wording fixes after the v0.3 run · v0.5, 10 Oct: Member 01 Q14–Q15 added, Q2 note · v0.6, 10 Oct: Q2 note gives the exact condExpr form · v0.7, 11 Oct: R-L3 scope clarified; filter-in-loop added to the checklist as not yet seen · v0.8, 12 Oct: corpus extended — 2 discovery apps queued, 3 evaluation apps added by random split · v0.9, 12 Oct: after the Phase 1 whole-app run — echo-string parts (§1), R-L3b, R-F1/R-F2 written in full, R-I5 evidence re-opened, Q16 · v0.10, 12 Oct: Member 01's answers recorded in §9; no rule changed) |
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
| **R-L4** | Nested loops → resolve innermost first; the inner List becomes one node of the outer Item | **Design only — not observed** in any in-scope app (seen only in out-of-scope osCommerce / vBulletin) |
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
| **R-I4** Small content `if` | Content `if` with **≤ 3 elements** (with or without `else`) → inline conditional / ternary | WackoPicko 11/11, HMS 12/12 |
| **R-I5** Fallback | Content `if` with **≥ N = 4 elements** (i.e. above the R-I4 limit — no gap) that matches none of R-I1–R-I3 → own Conditional component | v0.8: "never triggered" (regex scan of `if`s). **v0.9: re-opened** — the Phase 1 whole-app run hit it 10 times. Many may be guards that R-I1/R-I2 could not confirm because whole-app runs have **no labels**. Each of the 10 to be classified by hand (file:line) before the report statement below is kept or changed |
| **R-I6** Sibling chain | Consecutive sibling `if`s that output into the same parent element with mutually exclusive conditions → **one** conditional value. Exclusivity cannot be checked without condition text, so status is **`review`**, reason `exclusivity_not_verified`, until Member 01 Q2 is answered | HMS: 3 chains × 3 `if`s (status cell in `admin-panel.php`, `admin-panel1.php`, `doctor-panel.php`) |

**Statement for the report (on hold since v0.9 — see R-I5):** *Boundary decisions are structural (page
guard, list guard, attribute, inline). A size threshold N = 4 exists only as a fallback; it was never
triggered in the rule-discovery corpus, where the largest plain content conditional had 3 elements.*

---

## 4. Function and layout rules

| Rule | Text | Evidence |
|---|---|---|
Inputs: the function's own timeline, and the call sites / includes recorded in the timeline (harness
extensions `calls`, `includes`, pending Member 01 Q4). Without them, R-F1/R-F2 do not fire.

| Rule | Text | Evidence |
|---|---|---|
| **R-F1** Function component | A user function with output, called from ≥ 1 page, whose markup is **tag-balanced on its own** → one component (`PascalCase(function)`) in `components/`, placed at **each call site** at the call's position (inside whatever element is open there, e.g. `<select>`). Parameters → props: a parameter whose argument is a **literal at every call site** (or the default value) becomes a typed prop (`string` / `number` from the literal); any other parameter is not a prop, and reads of it → `unresolved`. Recursive calls → abstain `recursive_function` | WackoPicko `thumbnail_pic_list` (4 callers); HMS `display_docs()` / `display_specs()` into `<select>` (`admin-panel.php` 359, 331) |
| **R-F2** Shell pair | A header unit H and a footer unit F (functions, or included files) such that: (1) H leaves open-tag stack S; (2) F's unmatched closing tags close exactly S, in reverse; (3) on every page that calls H, F is called later, both at **top level** (not inside a loop/branch), with all other page output between them. Then: `<html>`, `<head>`, `<body>` from H/F → root `app/layout.tsx` (literal parts only; anything dynamic there → flag `document_shell`); the rest → a **shell component** `components/<H>Shell.tsx` with `{children}` at the gap and H's/F's literal-argument parameters as props; each page wraps its content in `<…Shell …props>`. Page output before H or after F → abstain `output_outside_shell`. A page that calls H without F, or tags that cross a function boundary without forming such a pair → abstain `tag_crosses_function` | WackoPicko `our_header()` (26 files) / `our_footer()` (25 files): H opens `<html><body><div class="container">`, F closes `</div></body></html>` |

**Why a shell component and not `layout.tsx` for the menu part:** Next.js layouts receive no per-page props,
but `our_header("guestbook")` passes the page name that sets the menu's `class="current"`. A shell component
keeps that argument; `layout.tsx` keeps only what every page shares.

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
- Output comes from a called function whose output is not in the current timeline (cross-file output). **Not detectable yet**: the timeline does not record calls (Member 01 Q4). Call sites are listed by hand in §6.5.
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

| Pattern | Seen in |
|---|---|
| Loop → table rows | HMS, WackoPicko |
| Chunked loop (closes and reopens its container) | WackoPicko (abstain) |
| C-style `for` loop | WackoPicko |
| Loop → cards / list items | WackoPicko |
| Small status `if`, sibling chain | HMS |
| Rows printed by one echo string (`echo "<tr>…$x…</tr>"`), concatenated / multi-argument echo with markup | HMS (Phase 1: 3 + 7 + 5 + 6) |
| Text-only loop | HMS `include/header.php` 38 |
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
| **Not yet seen:** nested loops; switch; try; **filter-in-loop** (`if` wrapping the whole loop body, e.g. `while(...) { if ($row['n'] > 0) { echo '<option>…'; } }` — synthetic test only; until seen in a real app it abstains via the narrowed fallback) | — |

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
