# Component 2 — Gold Standard Guide (v1.0)

| Item | Value |
|---|---|
| Date | 15 October 2026 |
| Annotator | Jayawardhana R D L L (IT23213876); second annotator for the agreement check: one team member (name it in §6) |
| Purpose | The answer key for the Phase 5 evaluation: what a **correct** migration of each sampled page would do, decided **by hand, from the PHP source only** |
| When | After the freeze commit (boundary-rules v1.0), **before** the tool is run on any evaluation app |

## 1. Rules for the annotator (these make the evaluation independent)

1. Annotate **from the PHP source only.** Never look at the tool's output for an evaluation app, and never run
   the tool on one, until the gold standard is committed.
2. Decide what a careful developer migrating to Next.js Server Components **should** do — not what the tool's
   rules say. If you disagree with a rule, the gold standard follows *your* judgement (that is the point).
3. If you are not sure, write `unsure` and a note. Do not guess to look complete.
4. Commit the finished files with the date. **After the tool runs, the gold standard is never edited.** A mistake
   you find later goes in `gold/corrections.md` with the date and reason, and results are reported both ways.

## 2. Which pages (sampling, fixed now)

For each of the 5 evaluation apps: list every page-like `.php` file (outside `vendor/`, libraries, `install/`),
sort the paths, and take **all of them if there are ≤ 10**, otherwise a random 10 with
`random.Random(20261015).sample(sorted_paths, 10)`. The script `tools/sample_eval_pages.py` does exactly this and
prints the list; commit its output as `gold/sample.txt` before annotating. Included files and functions that the
sampled pages use are annotated too (as `function` / `include` rows).

## 3. What to record (one row per construct, in `gold/<app>.csv`)

| Column | What to write |
|---|---|
| `app`, `file`, `line` | Where the construct starts (the keyword line for `if` / loops; the call / include line) |
| `construct` | `loop`, `if`, `function`, `include`, `read`, `unit` |
| `decision` | From the table below |
| `container` | For a loop: the tag that should be the List's container and its line (e.g. `table@34`), or `fragment` |
| `item_root` | For a loop: the Item's root tag (`tr`, `li`, `div`, …) or `text` |
| `binding` | For a `read`: `table.column`, or `session`, `request`, `literal`, or `unknown` (when the source alone cannot tell you) |
| `notes` | Anything that explains a hard case; `unsure` goes here |

**Decisions**

| construct | decision values |
|---|---|
| `unit` | `page`, `not_ui` (JSON / XML / API endpoint, error-only file), `layout_part` (header/footer shared by pages) |
| `loop` | `list` (List + Item), `inline_map` (one simple element or text per row), `not_ui` (no output), `cannot_migrate` (explain) |
| `if` | `page_guard` (wraps the whole page), `list_guard` (empty state of a list), `attribute` (inside an attribute value), `attribute_presence` (adds / removes an attribute), `inline` (small display choice), `own_component` (large display block), `redirect_guard`, `auth_check` (authorization), `request_handler` (form / POST / GET handling), `not_ui` |
| `function` | `component` (prints markup reused at call sites), `shell` (header/footer pair), `not_ui` |
| `include` | `component`, `shell`, `not_ui` (prints nothing that renders) |
| `read` | the `binding` column is the answer |

## 4. How it will be scored (fixed now, as in the roadmap)

| Metric | Rule |
|---|---|
| Boundary precision / recall | A tool component matches a gold row if same file, same construct start line (±0), and the decision maps to it (List ↔ `list`, R-I4 ↔ `inline`, …; mapping table in `docs/eval-mapping.md`, written before the run). Container and Item root must match for loops |
| **Unsound decisions** | Tool made a decision (not abstain) that disagrees with gold. Target **0**. Every one is listed and explained |
| Abstain rate | Tool abstained / gold rows the tool should have decided |
| Binding accuracy | Tool `table.column` = gold; tool `unresolved` where gold is `unknown` counts as correct |
| Compile rate, flag rate | From the tool's own outputs |

## 5. Hard cases (decide them the same way every time)

- A loop that prints `<option>`s → `inline_map`. A loop that prints `<tr>` rows → `list` with container `table`.
- `if (mysqli_num_rows($r) > 0) { …loop… } else { No records }` → `list_guard`.
- `if (!isset($_SESSION['user'])) header('Location: login.php');` → `redirect_guard`.
- `if ($_SESSION['role'] == 'admin') { …admin menu… }` → `auth_check`.
- `if (isset($_POST['save'])) { …update… echo "Saved"; }` → `request_handler`.
- A page printing `json_encode(...)` and no markup → `unit` = `not_ui`.

## 6. Agreement check

A second annotator labels **20 %** of the sampled pages (seeded: `random.Random(20261016)`), without seeing your
labels. Report percentage agreement and Cohen's κ on the `if` decisions. Disagreements are discussed and the
**original** gold stays; the discussion is recorded.
