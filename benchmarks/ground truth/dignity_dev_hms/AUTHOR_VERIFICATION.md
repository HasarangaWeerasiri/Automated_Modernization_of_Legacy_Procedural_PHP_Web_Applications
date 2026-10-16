# Dignity-Dev ground truth: author verification

- **Date:** 16 October 2026
- **Verifier:** the author (single human verifier)
- **File verified:** `benchmarks/ground truth/dignity_dev_hms/queries.json`

## 1. Scope

The author checked the original PHP source against `queries.json` by hand.

**SQL execution calls.** There are 14, all in `library.php`, at lines 31, 53, 96, 158, 178, 203, 219, 226, 233, 249, 292, 299, 313 and 320.

**Live sites.** 13 are live SQL sites. One is excluded:
- **G010**, line 249: `$sql = ''` before the call, so it is an empty-SQL site, not a SQL statement.

**Draft labels on the 13 live sites:** 7 recoverable, 6 non-recoverable.

**Whole-project search.**
- Searched all `.php` files of the project.
- Patterns used:
  - `mysqli_query`
  - `->query`
  - `->prepare`
  - `->execute`
  - `get_table(`
- Result: only `library.php` matched, with the 14 calls above plus the `get_table` definition at line 236. There were no other execution calls.

**Extended search.**
- Searched all `.php` files under `benchmarks/apps/dignity_dev_hms`.
- Patterns used:
  - `multi_query`
  - `real_query`
  - `mysqli_prepare`
  - `mysqli_stmt`
  - `->exec`
  - `new PDO`
  - `PDO::`
  - `->prepare`
  - `->execute`
- Result: **0 matches**.

**Schema note.** `127_0_0_1.sql` defines six tables:
- `admin`
- `appointments`
- `clerks`
- `doctors`
- `patient_info`
- `users`

Both `patient_info` and `users` are present, so this check establishes no missing-table mismatch. Column-level compatibility remains to be verified.

**Changes made to `queries.json`:** none.

## 2. Labelling criterion

The author used a conservative full-site criterion. A site is **recoverable** only when its code produces a single fixed statement, with values coming only from parameters.

If C3 converts a site labelled non-recoverable, that is reported as an **extra recovery**, separately from precision and recall.

## 3. FR2 justification for G003 and G006

**FR2 (verbatim):**

> "SQL queries constructed using variables, string concatenation, or conditional statements must be reconstructed whenever they can be safely resolved."

G003 and G006 have their SQL selected by branches (`switch` and `if`/`else`). Their complete behaviour, however, includes dynamic table or column identifiers or unsupported paths:
- **G003** (line 96): `$table` is used in `INSERT INTO $table`, and the `default` branch leaves `$sql` unset.
- **G006** (line 203): one branch uses `SET $column_name`.

Under the conservative criterion both stay **non-recoverable**. This is a classification boundary and an evaluation limitation. It is not proof that conditional SQL recovery is impossible.

## 4. Limitations

- **How the ground truth was made:** it was drafted blind by an LLM and verified by a single human.
- **Same model family:** the drafting model is from the same model family as the assistant used during development, so they may share blind spots.
- **Fresh holdout:** Dignity-Dev is the fresh holdout, and no pipeline rules are changed for it.
