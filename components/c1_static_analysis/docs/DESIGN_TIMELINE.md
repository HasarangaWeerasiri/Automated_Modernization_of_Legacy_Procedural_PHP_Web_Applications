# Output timeline for C2 (schema 1.0)

`python -m c1.timeline analysis-out --src <app>` → `timeline/<fileId>.json`, `timeline/index.json`

The format is C2's (`component2_frontend/CONTEXT.md`, "Timeline"), using the conventions agreed on 12–13 Oct 2026. The full field list is in the docstring of `src/c1/timeline.py`.

## Document contents

There is one document per file:
- `sequence`: the top-level output in source order;
- `functions`: per function/method that outputs;
- `queries`: the SQL shapes the reads point at;
- `isPage`: true when the file is an entry page.

## Entry types

- **Output entries:** `Stmt_Echo`, `Stmt_InlineHTML`, `Expr_Print`, each with `raw`, `loc` (the AST's six numbers), `enclosedBy` and `reads`. Echo `raw` is extra (C2's mock leaves it out).
- **`call`:** the agreed Q4 answer. No inlining; `outputsInside` is `true`, or `"unknown"` for calls we cannot resolve.
- **`include`:** the included file's output stays in that file's own timeline.
- **`parts`** (Q16): on echoes of interpolated strings.

## Reads

Each read uses the DFG origins, so values are traced through copies, includes, functions and static methods.

- **`db_row_field`:** carries `{fetchNodeId, queryNodeId, table, column}`.
- **SELECT \*:** a read from a `SELECT *` query is `resolved` with `basis: "schema-expanded"` only when the schema has the column and the query has one table and no JOIN (Q1). Otherwise it is `ambiguous`.
- **`mayBeAbsent: true`:** a helper returns a row or `false` (no row). The value is still that column, but it may be missing.
- **`computed`:** has `derivedFrom` with the reads inside, e.g. `h($pic['title'])`.

## Check against C2's hand-built mock

C2 built `mocks/timeline_list_while.json` by hand for HMS `admin-panel1.php` (the appointment table). Our output for the same page:
- all 29 mock entries are present with the same ids;
- `enclosedBy` and `reads` are **identical** for all 29: the same query, table, column, fetch node and `schema-expanded` basis;
- the only difference is that our echo entries also carry `raw`.

## Results (leaf reads)

| | HMS | WackoPicko |
|---|---|---|
| Output entries | 330 | 558 |
| Leaf reads traced to a DB column | 122 / 189 (116 resolved) | 69 / 133 (60 resolved) |
| C2's mock for WackoPicko `view.php` | — | 27 / 27 were `unresolved` (no DFG through classes yet) |
