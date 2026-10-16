# NEWS-BUZZ ground truth: author verification

- **Date:** 16 October 2026
- **Verifier:** the author (single human verifier)
- **File verified:** `benchmarks/ground truth/news_buzz/queries.json`

## 1. Scope

The author reviewed all 32 live SQL sites by hand against the original PHP.

**Label changes made to `queries.json`:** none (0). Git history shows only the original commit `9886dd6`, and `git status` shows no modifications to `queries.json`.

**Files and lines:** all match. The author also compared the ground truth's file:line list with the search hits (section 3), and the two sets are identical.

## 2. Draft labels

- **30 recoverable.**
- **2 not recoverable:** `admin/editposts.php:17` and `admin/post.php:17`. At each site an if/else picks one of two SELECT statements with different WHERE clauses. Both versions are recorded in the ground truth.

Both sites stay non-recoverable under the same conservative full-site criterion as in the Dignity-Dev and Helpdesk notes. If C3 recovers them anyway, that is an **extra recovery**, reported separately from precision and recall.

**Schema-mismatch sites:** none identified.
- The original SQL schema defines `posts` and `users`.
- The ground truth refers only to `posts` and `users`.
- The E2 evaluation of 15 October found 0 query-schema drift site-columns for NEWS-BUZZ.

## 3. Search

**Searched:** all `.php` files under `benchmarks/apps/news_buzz`.

**Patterns used:**
- `mysqli_query(`
- `mysql_query(`
- `->query(`
- `->prepare(`
- `->execute(`
- `mysqli_prepare(`
- `multi_query(`
- `real_query(`
- `->exec(`

**Result:** 32 `mysqli_query` calls, and no other matches.

**Extension check** for `.inc`, `.phtml`, `.html` and `.htm`: one file was found, a vendored TinyMCE example dialog (`admin/js/tinymce/plugins/example/dialog.html`). A search of it found no SQL or database calls.

## 4. Limits of the search

The search covers the listed patterns in `.php` files only. It does not prove the absence of every custom database wrapper.

## 5. Status of NEWS-BUZZ

NEWS-BUZZ had a pre-adaptation run before the rule changes. The rule changes since then were informed partly by several apps, so current NEWS-BUZZ results are **post-adaptation**. **Dignity-Dev remains the only fresh holdout.**

## 6. Limitations

- **How the ground truth was made:** it was drafted blind by an LLM and verified by a single human.
- **Same model family:** the drafting model is from the same model family as the assistant used during development, so they may share blind spots.
