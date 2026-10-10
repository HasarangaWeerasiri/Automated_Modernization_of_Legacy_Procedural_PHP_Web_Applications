# Component 2 — UI Boundary Rules (DRAFT v0.5)

| Item | Value |
|---|---|
| Status | **Draft** — not frozen. Freeze when 2 consecutive new rule-discovery apps add no new pattern (saturation) |
| Date | 7 October 2026 (v0.2: source spot-check corrections · v0.3: owner decisions after the first Stage 2 run · v0.4: wording fixes after the v0.3 run · v0.5, 10 Oct: Member 01 Q14–Q15 added, Q2 note; no rule changed) |
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
| **R-L3** | Loop body that emits **one element with no child elements** (e.g. `<option>`) → keep `.map()` inline, no Item component. Otherwise → Item component | HMS `<option>` loops in `newfunc.php`, `func1.php`, `func3.php` |
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
| **R-I5** Fallback | Content `if` with **≥ N = 4 elements** (i.e. above the R-I4 limit — no gap) that matches none of R-I1–R-I3 → own Conditional component | **Never triggered** in the rule-discovery corpus. Design default only |
| **R-I6** Sibling chain | Consecutive sibling `if`s that output into the same parent element with mutually exclusive conditions → **one** conditional value. Exclusivity cannot be checked without condition text, so status is **`review`**, reason `exclusivity_not_verified`, until Member 01 Q2 is answered | HMS: 3 chains × 3 `if`s (status cell in `admin-panel.php`, `admin-panel1.php`, `doctor-panel.php`) |

**Statement for the report:** *Boundary decisions are structural (page guard, list guard, attribute,
inline). A size threshold N = 4 exists only as a fallback; it was never triggered in the
rule-discovery corpus, where the largest plain content conditional had 3 elements.*

---

## 4. Function and layout rules

| Rule | Text | Evidence |
|---|---|---|
| **R-F1** | A user-defined function whose body produces output → component; its parameters → props | WackoPicko `thumbnail_pic_list($pictures, $high_quality)` — 4 callers |
| **R-F2** | Layout produced by **functions** (`our_header()`, `our_footer()`) **or** by `include header.php/footer.php` → Shared Layout component | WackoPicko: `our_header()` 25 callers, `our_footer()` 24. `include` form: **not yet observed** |

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
| Rule discovery | HMS, WackoPicko |
| Evaluation (**locked**) | crud-php-mysqli, PHP-MySQL-CRUD-Application (procedural) — **need 3–4 more** (proposal target: 5–8) |
| Out of scope | osCommerce 2 (2.4-dev, namespaced OOP core, DB wrapper), vBulletin 3.8.11 (DB-stored templates via `eval`; commercial licence), PHPMailer, Monolog (libraries) |

---

## 8. Pattern checklist (for saturation)

| Pattern | Seen in |
|---|---|
| Loop → table rows | HMS, WackoPicko |
| Chunked loop (closes and reopens its container) | WackoPicko (abstain) |
| C-style `for` loop | WackoPicko |
| Loop → cards / list items | WackoPicko |
| Small status `if`, sibling chain | HMS |
| Page guard, list guard, attribute conditional | WackoPicko |
| Function = component (`thumbnail_pic_list`) | WackoPicko |
| Layout via functions | WackoPicko |
| Malformed HTML | HMS, WackoPicko |
| Cross-file output (function echoes `<option>` into a page's `<select>`) | HMS |
| Escape wrapper on output (`h()` — 89 of 116 `<?= ?>` tags) | WackoPicko |
| **Not yet seen:** layout via `include header.php`; nested loops; plain content `if` > 3 elements | — |

---

## 9. Open questions

**For Member 01 (core / timeline)**
1. `SELECT *`: always `ambiguous`, even for a single table?
2. Condition text for JSX: read from `ast/<fileId>.json` via `condNodeId`, or a new `condExpr` field? _(Until answered, C2 mocks carry `condExpr` — byte-exact PHP condition source — on branch enclosures as an agent extension; Stage 5 needs it.)_
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
