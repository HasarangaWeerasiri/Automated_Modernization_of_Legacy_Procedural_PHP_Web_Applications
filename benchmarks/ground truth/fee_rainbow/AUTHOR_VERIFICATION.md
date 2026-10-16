# Fee-Rainbow ground truth: author verification

- **Date:** 16 October 2026
- **Verifier:** the author (single human verifier)
- **File verified:** `benchmarks/ground truth/fee_rainbow/queries.json`

## 1. Scope

The author reviewed the PHP source by hand against all 58 ground-truth entries (G001–G058).

The author also reviewed both `query()` helper implementations:
- `admin/config/helpers.php`
- `config/helpers.php`

Both contain `mysqli_query($conn, $query)` inside `query($query)`. These two execution sites are counted as **G021** and **G022**. The helper *declarations* (the `function query(...)` lines) are not entries.

**Label changes made to `queries.json`:** none (0).

## 2. Counts

| Measure | Count |
|---|---|
| Total entries | 58 |
| Live SQL sites | 54 |
| Commented-out calls | 4 (G033, G042, G053, G054) |
| Recoverable (live) | 40 |
| Non-recoverable (live) | 12 |
| Ambiguous (live, excluded from precision and recall) | 2 (G007, G011) |
| Direct execution sites (`mysqli_query`) | 22, including G021 and G022 |
| `query()` wrapper calls | 36, including the 4 commented-out calls |

**Live sites by kind:**
- **Direct execution sites (22):** 18 recoverable, 2 non-recoverable (G021, G022), 2 ambiguous (G007, G011).
- **Live wrapper calls (32):** 22 recoverable, 10 non-recoverable.

**Non-recoverable sites (12):**
- **The two generic executors**, where the SQL is the function parameter:
  - G021 `admin/config/helpers.php:4`
  - G022 `config/helpers.php:4`
- **Ten wrapper calls with an optional WHERE/AND clause** appended through a ternary on `$search`:
  - G023 `admin/app/models/categories.php:7`, G024 `:21`
  - G025 `admin/app/models/occasions.php:7`, G026 `:14`
  - G027 `admin/app/models/products.php:17`, G028 `:42`
  - G029 `admin/app/models/subcategories.php:7`, G030 `:24`
  - G051 `app/models/products.php:17`, G052 `:42`

These stay non-recoverable under the same conservative full-site criterion as in the Dignity-Dev, Helpdesk and NEWS-BUZZ notes: a single fixed statement, with values coming only from parameters. If C3 recovers any of them anyway, that is an **extra recovery**, reported separately from precision and recall.

## 3. Ambiguous cases (excluded from precision and recall)

| Entry | Site | Why ambiguous |
|---|---|---|
| G007 | `admin/app/models/categories.php:100` | The SQL text is static, but the function can never run successfully. Its only caller passes 2 of the 4 required arguments, and `escape($conn, $keyword)` would pass the mysqli object as the string. |
| G011 | `admin/app/models/occasions.php:92` | The SQL text is static, but `searchOccasion()` has no caller, and `escape($conn, $keyword)` would fail first. |

## 4. Tables missing from the supplied schema

The supplied schema (`benchmarks/apps/fee_rainbow/database/db.sql`) defines only `admin`. The ground truth refers to 5 tables: `admin`, `categories`, `occasions`, `products`, `subcategories`.

**Missing from the schema:**
- `categories`
- `occasions`
- `products`
- `subcategories`

Every site that uses these tables is a schema-mismatch site. Its tables and columns were taken from the SQL text, as the per-entry notes in `queries.json` record.

## 5. Search

- **Execution methods:** whole-project searches for other SQL execution methods found nothing beyond the 22 direct `mysqli_query` sites and the 36 `query()` wrapper calls.
- **Extension check:** a search for `.inc`, `.phtml`, `.php5`, `.html`, `.htm` and `.tpl` files found **no files**.

## 6. Limits of the search

Pattern search covers the searched patterns and file types only. It cannot rule out dynamically built SQL calls (e.g. a function or method name assembled at run time), every possible custom database wrapper, or SQL executed through some other mechanism.

## 7. Status of Fee-Rainbow

Fee-Rainbow had a pre-adaptation run before the rule changes (`evidence/preadaptation/fee_rainbow/`). The rule changes since then were informed partly by several apps, so current Fee-Rainbow results are **post-adaptation**. **Dignity-Dev remains the only fresh holdout.**

## 8. Limitations

- **How the ground truth was made:** it was drafted blind by an LLM and verified by a single human.
- **Same model family:** the drafting model is from the same model family as the assistant used during development, so they may share blind spots.
