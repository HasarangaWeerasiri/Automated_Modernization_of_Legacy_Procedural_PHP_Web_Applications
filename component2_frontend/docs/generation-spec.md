# Component 2 — Stage 5 Generation Specification (v0.7)

| Item | Value |
|---|---|
| Status | Draft v0.4 — implemented on the rule-discovery mocks (`aead7db` v0.1, `ee21ff8` v0.2, v0.3 uncommitted). v0.4 corrects the abstained-code counting and narrows the last-resort fallback. v0.5 adds what the Phase 1 whole-app run needed (§5a, §8, §8a). v0.6: browser-equivalent markup (§5b), per-part ids, route groups. v0.7: corrections after Phase 2b (§5 form state, §5b stray end tags and loop rows, §8a routes and calls). Evaluation apps stay locked |
| Date | 10 October 2026 |
| Owner | Jayawardhana R D L L (IT23213876) |
| Input | Stage 2 tree, Stage 3 needs, Stage 4 report, contract, endpoint map, timeline |
| Output | Next.js (App Router) TypeScript files + `output/stage5_<name>.json` (file list + flags) |
| Novelty | **None claimed.** Generation is commodity (proposal §3.3). What matters here: it is deterministic, it never hides a gap, and the result compiles |

---

## 1. Core rule — never guess, always compile

Any part Stage 5 cannot generate faithfully is replaced by `<C2Todo id="<nodeId>" reason="<reason>" />`:
a component (in `lib/c2-todo.tsx`) that **renders nothing**. Every C2Todo is listed in the Stage 5 flag
output with file, line and reason. So:

- the generated app **always compiles and runs**;
- every gap is **visible** in the flag file (and C4 will see the behaviour difference);
- nothing is silently invented.

No LLM in v0.1. Parts the rules cannot convert become C2Todo. (Using a pinned local LLM for malformed HTML
is a later owner decision; it must not break determinism.)

## 2. Output layout (per mock)

```
output/generated/<name>/
  app/<route>/page.tsx        route = entrypoint file name without .php (admin-panel1.php -> admin-panel1)
  components/<Component>.tsx  one file per List, Item, Empty, ConditionalComponent
  lib/api-types.ts            TypeScript types for the response schemas this page uses
  lib/api.ts                  one typed fetch function per mapped endpoint
  lib/c2-todo.tsx             the C2Todo component
```

**Everything is a Server Component.** No file may contain `'use client'`. (SO4, rendering preservation.)

## 3. Names (deterministic, no guessing)

| Component | Name |
|---|---|
| List | PascalCase of the response array property + `List` (e.g. `appointments` → `AppointmentsList`). If the collection is not mapped: `<Route>List<n>` |
| Item | Same stem + `Item` (`AppointmentsItem`) |
| Empty | Same stem + `Empty` |
| ConditionalComponent | `<Route>Section<n>` |
| Page | default export `Page` |

`<n>` = order of first appearance in the tree. Clashes get a numeric suffix.

## 4. Data and props

| Situation | Generated |
|---|---|
| Page with a mapped endpoint | `async` Page calls the fetch function from `lib/api.ts` and passes data down |
| Page with no endpoint (`no_endpoint`) | Page passes `[]` / nothing and renders `<C2Todo reason="no_endpoint">` once |
| List | Prop = the array (typed from the contract). Renders container markup + `{items.map((row, i) => <XItem key={i} row={row} />)}` |
| Item | Prop `row` typed as the array item type |
| Collection not mapped | List/Item still generated; `row` typed `Record<string, unknown>`; every field access → C2Todo |
| `key` | Always the index `i` (server-rendered, no reordering; choosing a primary key would be a guess) |
| Field need `matched` | `{row.<column>}` |
| Field need `missing` / `cannot_reconcile` | `<C2Todo reason="missing_in_contract" />` (or its reason) |
| Context need (`session`, `request`, `server`) | `<C2Todo reason="needs_auth_context" />` / `needs_request_context` — wiring belongs to C1 |
| `literal` | The literal text |

**Wrappers on computed reads** (from config `generation.json`):

| Wrapper | Generated |
|---|---|
| `h`, `htmlspecialchars`, `htmlentities` | Dropped — React escapes by default |
| Any other (e.g. `number_format`) | Base value + `/* TODO(C2): <wrapper> */` comment; status `review`, reason `unsupported_wrapper` |
| Output that was **not** escaped in PHP (`<?= $comment['text'] ?>`, and every plain HMS `echo $row[...]`) | Generated normally (React escapes it). Flag `escaping_changed` (info for C4 — an expected difference). Only non-literal output; the CLI prints one line per page with the count |

## 5. Markup → JSX

| HTML | JSX |
|---|---|
| Tag and attribute names | Lower-cased first (HTML is case-insensitive; JSX treats `<Span>` as a component and fails to compile — WackoPicko's header has `<Span>`) |
| `class` / `for` | `className` / `htmlFor` |
| Other attributes | Kept; names follow React's attribute names |
| Void elements | Self-closed (`<br />`) |
| Numeric-only React props (`size`, `colSpan`, `rowSpan`, `tabIndex`, `maxLength`, `minLength`, `cols`, `rows`, `span`, `start`) | Integer value → `{15}`; non-integer → attribute omitted, flag `attribute_dropped` |
| Form state: `value` on an **editable** `<input>` (text, password, email, number, search, tel, url, date…, or no `type`), `checked`, `<textarea>` content | `defaultValue` / `defaultChecked` (no `onChange` in a Server Component). **v0.7:** `value` on `type` = `submit`, `button`, `reset`, `image`, `hidden`, `checkbox`, `radio` stays **`value`** — it is a label / fixed value, not form state, and React accepts it without `onChange`. `selected` on `<option>` → omitted, flag `review` `option_selected_not_converted` |
| Attribute value mixing text and PHP (`href="view.php?id=<?= $row['id'] ?>"`) | Template literal if every PHP part is a `matched` field or a dropped escape wrapper. Any other part → **attribute omitted**, flag with that part's reason (never an empty or guessed value) |
| `<html>`, `<body>` | Tags dropped, children kept. `<head>` and everything in it dropped. Flag `document_shell` (`review`): it belongs in `app/layout.tsx`, which waits on R-F2 / Member 01 Q4 |
| HTML comments (incl. `<!--[if IE]>…<![endif]-->`) | Dropped (they render nothing). Flag `html_comment_dropped` (info) |
| `style="a:b; c:d"` | Style object if every declaration parses; else C2Todo `unsupported_style` on that attribute |
| Entities (`&nbsp;`, `&nbsp` without `;`, `&#8377;`) | Decoded with HTML5 rules (Python `html.unescape`), then written as a JSX string so the rendered character is identical |
| Text with `{`, `}`, `<`, `>` | Wrapped as `{"..."}` |
| Attributes React's types do not declare (`name` on `<option>`, `tooltip` on `<a>`) | Omitted, flag `attribute_dropped` (`review`). Hyphenated attributes (`data-*`, `aria-*`, `tooltip-placement`) kept. The accepted list is `config/react_dom.json`, extracted from the pinned `@types/react` (record that version in the file) |
| Elements still open at the end of an excerpt | Closed at the end, flag `unclosed_at_end` (info) |
| Whitespace | Text with content: runs collapse to one space (as a browser does). Whitespace-only between two inline nodes: `{" "}`. Whitespace-only between block elements: dropped. Inside `<pre>`, `<textarea>`: kept exactly, as a string literal |
| Unmatched closing tag (R-L6 flag) | Dropped; flag kept |
| Event attributes (`onclick`, `onsubmit`, …), `<script>` | **Dropped** (inline JS is out of scope; Server Components cannot have handlers). Flag `inline_js_dropped`, status `review` |
| Links / form actions to `.php` pages | Kept as literal strings. Flag `legacy_link` (info; routing is not C2's job) |

## 5a. Echo strings with parts (v0.5; boundary-rules §1)

| Part | JSX |
|---|---|
| Literal piece | Parsed as HTML with the rest of the markup (same rules as §5) |
| Expression in text position | `{row.x}` / C2Todo, exactly as a read in §4 |
| Expression inside an attribute value | Template-literal attribute (§5 "attribute value mixing text and PHP") |
| Expression in a tag-name or attribute-name position | Not generated; Stage 2 already abstained (`markup_from_expression`) |

R-L3b text-only loop → `{items.map((row, i) => <Fragment key={i}>…</Fragment>)}` (import `Fragment` from `react`).

Each C2Todo / flag for an expression part uses **that part's AST node id**, not the echo's id (v0.6), so
every gap points to its exact source position.

## 5b. Browser-equivalent markup (v0.6)

Legacy pages were rendered by browsers, which **repair** some invalid HTML by fixed HTML5 parsing rules.
React does not repair; it renders the tree as written (and warns / mismatches on hydration). So, inside each
component's static markup, Stage 5 applies the two repairs that are fully determined by the HTML5 spec:

| Repair | Rule | Flag |
|---|---|---|
| Implicit `<tbody>` | `<tr>` directly inside `<table>` → wrapped in `<tbody>` (what every browser builds). **v0.7:** also when the table's row children are a List whose Item root is statically `<tr>` (the `.map()` goes inside the `<tbody>`), with or without static rows | `implicit_tbody` (info) |
| Stray `</p>` / `</br>` (v0.7) | An end tag `</p>` with no open `<p>` → an **empty `<p></p>`**; `</br>` → `<br />` (HTML5 parser rules — every browser does this; an empty `<p>` has visible margins). All other unmatched end tags are ignored by browsers, which is what R-L6 already does | `stray_end_tag_repaired` (info) |
| Implicit `</p>` | An open `<p>` is closed before a block start tag (`address, article, aside, blockquote, details, div, dl, fieldset, figcaption, figure, footer, form, h1–h6, header, hgroup, hr, main, menu, nav, ol, p, pre, section, table, ul`) — HTML5 "close a p element" | `implicit_p_close` (info) |
| Duplicate attribute | Keep the **first** occurrence (browsers ignore later ones); JSX cannot have duplicate props | `duplicate_attribute` (info) |

Only within one component's static markup. A repair that would cross a loop, branch, call or expression
boundary is **not** applied (its result depends on runtime output) → `html_nesting_changed` (review), as now.
**Foster parenting** (`<form>`, `<input>` or text directly inside `<table>`) is never repaired — it moves
content elsewhere in the tree → stays `html_nesting_changed` (review).

## 6. Conditions (InlineConditional, ConditionalComponent, AttributeExpression, Empty)

The condition text is not in schema 1.0 (Member 01 Q2). Until it is, generated mocks may carry
`condExpr` on branch enclosures: the **byte-exact PHP condition source**, marked as an agent extension
(same treatment as `loc`). The loader treats it as optional.

A condition is translated **only** if every part is on this whitelist:

| PHP | TypeScript |
|---|---|
| `$row['x']`, `$row["x"]` (the Item's row) | `row.x` — only if `x` is in the item type; else not translatable |
| `==`, `!=`, `===`, `!==`, `<`, `>`, `<=`, `>=` | same operator; `==` / `!=` become `==` / `!=` (loose, like PHP) |
| `&&`, `||`, `!`, `and`, `or`, `( )` | `&&`, `||`, `!` |
| integer, float, quoted string literals | same |
| `isset($row['x'])` | `row.x !== undefined && row.x !== null` |
| a variable holding a list (`if ($comments)`) used as an R-I2 list guard | `items.length > 0` |

Anything else (`$_SESSION[...]`, function calls, other variables, `mysqli_*`) → the whole condition is
`unresolvedCondition("<nodeId>", "<reason>")` (returns `false`, defined in `lib/c2-todo.tsx`), status
`review`, reason `condition_not_translatable` or `needs_auth_context`. No `condExpr` → reason
`condition_unavailable`.

**PHP vs JS comparison (corrected in v0.2):**

| Case | Generated |
|---|---|
| number vs number | Translated |
| string vs number | **Not translated** — TypeScript rejects it (TS2367) and PHP's loose rules differ. `unresolvedCondition`, reason `loose_compare_semantics` (`review`) |
| string vs string with `==` / `!=` | Translated, flag `numeric_string_compare` (info): PHP compares two numeric strings as numbers (`"10" == "1e1"` is true) |
| string field used alone as a truth value | Not translated (PHP treats `"0"` as false, JS as true) → `condition_not_translatable` |

**R-I6 chains are generated as separate conditionals, in source order** — one `{cond && …}` per `if`,
never an `else`/ternary chain. So the generated code makes **no exclusivity assumption** and behaves
like the PHP even if two conditions are true at once. Stage 2's `review` (`exclusivity_not_verified`)
still carries through to the flag file (the boundary grouping is still unverified), but it no longer
risks a behaviour change. Real `if / elseif / else` (one `Stmt_If`) → nested ternary.

## 7. Abstain nodes (Stage 2)

An Abstain node becomes `<C2Todo reason="<Stage 2 reason>" />` in place. Its children are still generated,
into a separate **unused** function in the same file (`function Abstained_<nodeId>()`), so the developer
has the code — but it is not rendered.

## 8. Typed fetch layer (commodity)

- `lib/api-types.ts`: one exported type per response schema used. OpenAPI → TypeScript: `integer`/`number`
  → `number`, `string` → `string`, `boolean` → `boolean`, `nullable` → `| null`, `array` → `T[]`. Property
  names verbatim. Fields not in the contract do **not** appear (that is why a missing field becomes C2Todo,
  not `row.contact`).
- `lib/api.ts`: `export async function get<Name>(): Promise<Type>` using
  `fetch(\`${process.env.API_BASE_URL}<path>\`, { cache: "no-store", headers: authHeaders() })`.
  `authHeaders()` is a stub returning `{}` with a TODO: token forwarding is C1's design.
- **v0.5:** every generated `.ts` / `.tsx` file is an ES module (has at least one `export`). A page with no
  endpoint gets **no** `lib/api.ts` (nothing to fetch); `authHeaders` is exported. A non-module file is a
  global script: in a real Next.js app (`isolatedModules`) it fails, and two of them collide. The compile check
  must use the test app's own `tsconfig.json` unchanged (no `moduleDetection` override).

## 8a. Functions and shell (v0.5; boundary-rules R-F1, R-F2 — Phase 2b)

| Unit | Generated |
|---|---|
| R-F1 function | `components/<Name>.tsx`, `export function <Name>(props)`; props typed from literal arguments; `<Name … />` at each call site |
| R-F2 shell | `app/(<h>)/layout.tsx` — a root layout per shell (literal `<html>/<head>/<body>`, `{children}`); `components/<H>Shell.tsx` with `{children}`; each page using it is generated under `app/(<h>)/<route>/page.tsx`, its content wrapped in `<…Shell>`; `document_shell` removed for those pages. Every other page → `app/(plain)/<route>/page.tsx` with a minimal root layout (boundary-rules R-F2 "Route groups") |
| Unresolved call / unknown output at a call site | `<C2Todo reason="unresolved_call" />` (todo). **v0.7:** consecutive unresolved calls in one straight-line block with no output between them → **one** C2Todo listing all their ids (one gap, counted once) |
| Routes (v0.7) | Path **relative to the app's document root** (WackoPicko `website/`, HMS repo root — recorded per app in `legacy-apps/PINS.md`), `.php` removed, `index.php` → its directory. E.g. `website/users/login.php` → `app/(our_header)/users/login/page.tsx` |
| R-F1/R-F2 parameter compared to a literal (`$selected == "home"`) | Translatable (whitelist row added): `selected == "home"` when the parameter is a typed prop |

## 9. Formatting and determinism

Own fixed formatter (2-space indent, double quotes, trailing newline). No Prettier (version drift breaks
determinism). Two runs → byte-identical files.

## 10. Checks

| Check | How |
|---|---|
| Compiles | Copy each mock's output into the Next.js test app (`output/test-app`), run `npx tsc --noEmit`: **0 errors** for every mock. Test skips with a clear message if Node is missing |
| Server-only | No `'use client'` anywhere; no event handler props |
| Determinism | Two runs byte-identical |
| No hidden gaps | Every Stage 4 `missing` / `cannot_reconcile` need and every Stage 2 abstain appears as a C2Todo — or, for a gap inside an attribute, as an `attribute_omitted:<reason>` flag carrying the node id (an attribute has nowhere to hold a C2Todo) |
| HTML nesting (v0.2) | Serialise each generated element tree to HTML (data expressions as placeholder text), parse it with an HTML5-compliant parser (Python `html5lib`, pinned), compare the element tree. Any difference (e.g. `<form>` directly in `<table>`, which the browser re-parents but React will not) → flag `html_nesting_changed` (`review`) with the element path. `tsc` cannot see this |
| Strict types | The test app's `tsconfig.json` has `"strict": true`; the compile check fails if it does not |

## 11. Expected results on the current mocks

| Mock | Expected |
|---|---|
| list_while | `AppointmentsList` (`<table>`), `AppointmentsItem` (`<tr>`, 11 data cells + status cell); `contact` → C2Todo `missing_in_contract`. Status cell: three separate conditionals from `condExpr` (`row.userStatus == 1 && row.doctorStatus == 1` → `"Active"`, etc.) **if** both fields are in the item type — else `condition_not_translatable`; still `review` (R-I6). Report which happened |
| list_foreach | Components byte-identical to list_while **after normalising node ids** (C2Todo ids are node ids, which differ between the two timelines) |
| admin | Authorization guard → C2Todo `cuts_across_business_logic` + `Abstained_…` function; session reads → `needs_auth_context` |
| detail | Compiles; no `missing` C2Todo |
| edge_cases | `ambiguous_needs_schema` / `unresolved_read` C2Todos; nested `foreach`-in-`if` → C2Todo from Stage 2 abstain |
| wp_* | Structure generated; every data expression → C2Todo (all reads `unresolved`); `wp_thumbnails` chunked loop → C2Todo; `wp_view` and `wp_guestbook` unescaped comments → `escaping_changed`; `wp_header`: `<Span>` lower-cased, `<html>/<head>/<body>` → `document_shell`, `<!--[if IE]>` dropped, menu `class` conditionals (`$selected == "home"`, a function parameter) → attribute omitted, `condition_not_translatable`; `size="15"` → `{15}`; search box `value` → omitted (its PHP part, `$search_terms`, is unresolved) |
| every mock | `tsc --noEmit` 0 errors; no `'use client'` |

## 12. Metrics

| Metric | From |
|---|---|
| Rendering preservation (SO4) | Server-only check + compile check + nesting check |
| Components generated without C2Todo | count / total components |
| Flag rate | C2Todos + `review` flags only. `info` flags (`escaping_changed`, `legacy_link`, `html_comment_dropped`, `unclosed_at_end`, `numeric_string_compare`) are reported separately and **not** counted — they are expected differences, not developer work |

Every flag in `stage5_<name>.json` carries `severity`: `todo` (a C2Todo, an `unresolvedCondition`, an omitted attribute, `output_dropped`), `review`, or `info`.

**Abstained code is counted once (corrected in v0.4).** Every flag found only in abstained (never-rendered) code — `review` **and** `todo` (C2Todo, `unresolvedCondition`, `attribute_omitted`, `output_dropped`) — is marked `inAbstained: true`, keeps its original severity in a field `severityIfRendered`, and is counted as `info`. The Abstain node's own C2Todo is the single `todo` for the whole region. Every inner flag stays listed (no hidden gaps); it is just not counted twice.

**Last-resort fallback (v0.4).** When a structure cannot be generated (`markup_not_generatable`), the C2Todo replaces the **smallest enclosing component** (the List, Item or ConditionalComponent), never the whole page — unless the page itself is the smallest. The flag names the component.

## 13. Decision log (Appendix E)

| Date | Decision | Reason |
|---|---|---|
| 10 Oct 2026 | Gaps become a no-render `C2Todo` + flag, never a guess | Keeps the app compiling while every gap stays visible (sound-or-abstain) |
| 10 Oct 2026 | No LLM in Stage 5 v0.1 | Determinism; the rules cover the mocks; revisit with evidence |
| 10 Oct 2026 | Index as React `key` | Choosing a primary key without C3's schema would be a guess |
| 10 Oct 2026 | Condition translation by whitelist only | PHP and JS semantics differ; anything outside the whitelist is flagged |
| 10 Oct 2026 | Inline JS dropped and flagged | Out of scope (§2.2 of the proposal); Server Components cannot hold handlers |
| 10 Oct 2026 | R-I6 chains generated as separate conditionals | Faithful to PHP whatever the exclusivity; removes the behaviour risk R-I6 was flagged for |
| 10 Oct 2026 | Unresolvable attribute → omitted, not emptied | An empty `href` or `class` is a silent guess; omission plus a flag is visible |
| 10 Oct 2026 | Document shell (`html/head/body`) not generated in pages | Next.js puts it in `layout.tsx`; layouts wait on R-F2 |
| 10 Oct 2026 | v0.2: gap inside an attribute is shown as `attribute_omitted:<reason>` | No place for a C2Todo in an attribute; the flag carries the node id |
| 10 Oct 2026 | v0.2: string-vs-number comparisons are not translated | TS2367 and PHP's loose rules; flagging but translating was impossible |
| 10 Oct 2026 | v0.2: `escaping_changed` on every non-literal unescaped echo, severity info, outside the flag rate | True for HMS too (React escapes `<` and `&`); C4 needs it; counting it would inflate the flag rate with no developer action |
| 10 Oct 2026 | v0.2: HTML nesting check added | Invalid nesting compiles but renders differently from the browser's re-parented legacy page |
| 10 Oct 2026 | v0.3: review flags inside abstained code are `info` | Never rendered; the Abstain is already counted as `todo` |
| 11 Oct 2026 | v0.4: **todo** flags inside abstained code are `info` too (owner's v0.3 expected totals were wrong) | v0.3 only removed the double count for review flags; a C2Todo inside an abstained region is the same double count. Rule now: one abstained region = one todo |
| 12 Oct 2026 | v0.5: echo strings decomposed into parts | HMS prints whole rows as one interpolated echo; without parts these rows abstain (`no_rule`) |
| 12 Oct 2026 | v0.5: every generated file is a module; no `lib/api.ts` without an endpoint | A global script fails under Next.js `isolatedModules`; Phase 1 hid this with a checker override, which is removed |
| 13 Oct 2026 | v0.7: `value` kept on non-editable input types | `defaultValue` on a submit button rendered the same HTML but is the wrong prop; React needs no `onChange` for those types |
| 13 Oct 2026 | v0.7: stray `</p>` / `</br>` repaired | Fixed HTML5 parser rules; WackoPicko `tos.php` renders an empty paragraph in every browser |
| 13 Oct 2026 | v0.7: routes from the document root | `website/` is not part of WackoPicko's URLs; Phase 4 compares pages by URL |
| 13 Oct 2026 | v0.6: browser-equivalent markup (implicit `tbody`, implicit `</p>`, first duplicate attribute) | These are fixed HTML5 parser rules: the legacy page was rendered this way, so generating it is faithful, not a guess. Foster parenting is not repaired |
| 13 Oct 2026 | v0.6: one id per expression part | A shared echo id made 5 gaps in one row indistinguishable |
| 13 Oct 2026 | v0.6: route groups with one root layout each | Next.js applies the root layout to every route; a shell's `<head>` must not leak onto other pages |
| 12 Oct 2026 | v0.5: shell component for per-page header arguments | Next.js layouts get no page props; `our_header("guestbook")` needs one |
| 11 Oct 2026 | v0.4: last-resort fallback replaces the smallest enclosing component | A synthetic R-L3 case replaced a whole page for one small structure |
| 10 Oct 2026 | v0.3: `unresolvedCondition` is severity `todo` | The guarded content never renders — the same as a C2Todo |
| 10 Oct 2026 | v0.2: route clash for function files recorded, not fixed | `wp_header` and `wp_thumbnails` are functions in `html_functions.php`; they are components, not pages. Waits on R-F1/R-F2 (Member 01 Q4) |
