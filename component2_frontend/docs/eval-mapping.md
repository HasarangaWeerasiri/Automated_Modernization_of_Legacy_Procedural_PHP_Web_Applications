# Component 2 — Evaluation Mapping and Scoring (v1.0)

| Item | Value |
|---|---|
| Date | 16 October 2026 — **fixed before the tool runs on any evaluation app** |
| Used by | `tools/eval/score.py` (Phase 5) |
| Inputs | `gold/<app>.csv` (owner's gold standard, gold-standard-guide.md) and the tool's Stage 2 / 3 / 5 outputs for the same app, under rules **v1.0** and **v1.1** |

## 1. Matching a tool node to a gold row

Same app, same file, same construct, same **start line** (gold `line` = tool node `loc.startLine`; ±0). One gold
row matches at most one tool node. Unmatched gold rows are **misses**; unmatched tool decisions on constructs the
gold does not list are reported separately (`extra`) and not scored.

## 2. Decision mapping

| Gold decision | Tool result counted **correct** | Tool result counted **abstain** (a miss, not wrong) | Tool result counted **wrong** |
|---|---|---|---|
| loop `list` | List (R-L1 / R-L2a / R-L2b / R-L4), **and** container tag + line and Item root match | any abstain on the loop | inline map; container or Item root differs; not a boundary |
| loop `inline_map` | R-L3 / R-L3b | abstain | List with a different item shape; not a boundary |
| loop `not_ui` | R-L5, R-I7, or no node | — | a List or inline map that renders output |
| if `page_guard` | R-I1 | v1.1 conditional with `unresolvedCondition` (content hidden, flagged) | content rendered under a different, translated condition |
| if `list_guard` | R-I2 / R-I2b | abstain; v1.1 conditional | R-I4 / R-I5 rendering the loop unguarded |
| if `attribute` | R-I3 | abstain, attribute omitted | anything that renders the attribute unconditionally |
| if `attribute_presence` | R-I3b | abstain, attribute omitted | attribute always present / always absent without a flag |
| if `inline` | R-I4, R-I6 | abstain | R-I5 is **correct too** if the gold note says the block is large; otherwise wrong only if the condition is translated wrongly |
| if `own_component` | R-I5 | abstain | R-I4 is correct too (size is a style choice); wrong only if rendered unconditionally |
| if `redirect_guard` | R-I8 redirect guard, `header_redirect` flag | abstain | content of the redirect arm rendered |
| if `auth_check` | abstain `cuts_across_business_logic`; any conditional whose condition is `unresolvedCondition(needs_auth_context)` | — | content rendered **unconditionally** |
| if `request_handler` | any conditional whose condition is `unresolvedCondition(needs_request_context)` or a translated request condition; R-I7 | abstain | handler output rendered unconditionally (e.g. v1.0 R-I1 on a POST-only page) |
| if `not_ui` | R-I7, or no node | — | a rendered conditional |
| function `component` | R-F1 | `unresolved_call`, abstain | not a component and its output lost **without** a flag |
| function / include `shell` | R-F2 | `tag_crosses_function`, `output_outside_shell`, `cross_file_output` | — |
| include `not_ui` | no unit, `error_output_only`, whitespace-only | — | rendered as content |
| unit `page` | a generated page | — | not generated |
| unit `not_ui` | `notGenerated` (`non_html_response`, `ajax_endpoint`, `error_output_only`) or no unit | — | generated as a page that renders content |
| read `table.column` | tool read resolves to the same `table.column` | tool `unresolved` / `ambiguous` | tool resolves to a **different** `table.column` |
| read `unknown` | tool `unresolved` / `ambiguous` | — | tool resolves to any `table.column` |
| read `session` / `request` / `literal` | same `sourceKind` | `unresolved` | a different `sourceKind` |
| any row `unsure` | not scored; listed | | |

## 3. Metrics

| Metric | Definition |
|---|---|
| Boundary precision | correct / (correct + wrong), over loop, if, function, include, unit rows |
| Boundary recall | correct / all scored gold rows (abstains count as not correct) |
| **Unsound decisions (strict)** | count of **wrong**. Target 0. Every one is listed with file:line and the rule that produced it |
| Unsound decisions (flag-adjusted) | wrong decisions whose node carries **no** specific review reason. `review: unlabelled` does **not** count as specific (it is on every node, because evaluation apps have no C1 labels) |
| Abstain rate | abstain / all scored gold rows |
| Binding accuracy | correct reads / scored read rows; wrong bindings listed separately |
| Compile rate, flag rate | from the tool's own outputs (0 strict tsc errors per app; todo + review per page) |
| Agreement | owner vs second annotator on the 20 % sample: percentage agreement and Cohen's κ on `if` decisions |

Every metric is reported **per app and in total**, for **v1.0 and v1.1**, side by side.

## 4. Rules for the run

1. The gold standard is committed (with its date) **before** the first tool run on an evaluation app. The scorer
   refuses to run if `gold/` has uncommitted changes or a later commit than the first tool output.
2. The tool runs with `--rules v1.0` and `--rules v1.1`, no other setting changed, no contract / endpoint map.
3. No rule, spec or code change between the two runs. A bug found during Phase 5 is logged, fixed after scoring,
   and the scores are reported **before and after** the fix.
