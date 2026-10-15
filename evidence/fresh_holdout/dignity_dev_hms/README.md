# Fresh holdout (current rules, post-S13): Dignity-Dev Hospital-Management-System

**This is the first and only run of this app; nothing was tuned on it.** It ran on the current converter (`src/` identical to `17b3e51`, i.e. after S1–S6 and S13) before any further code change. Unlike Helpdesk and the three earlier unseen apps, it was not inspected before the rules were written, so it is the only genuinely independent generalisation result so far.

- **Source:** https://github.com/Dignity-Dev/Hospital-Management-System @ `b9e939d64e06ae5adedb51b91329784a645b74e9`, MIT (Copyright (c) 2022 DATA - Dignity Technology). Not committed; see the manifest.
- **Input:** 18 app PHP files (`app_only_file_list.json`, nothing excluded) and schema `127_0_0_1.sql`.
- **Command:** see `run_log.txt` (exit 0, all 6 stages, 0.63 s).

## Result

| Total sites | Recovered | Converted | Not converted | Detection (independent grep) | Models |
|---|---|---|---|---|---|
| 14 | 7 | **4** | 10 | 14/14 | `configure_mappers()` OK (2 mapped classes, 4 key-less tables as Core `Table`) |

| Query | Outcome | Manual check |
|---|---|---|
| Q001, Q002, Q013, Q014 | UNRESOLVED `STRUCTURAL` (`FROM $table` from a function parameter) | correct abstention: the table name is caller-controlled |
| Q003 | UNRESOLVED `LOOP_OR_SWITCH_CONTROL_FLOW` | correct: `switch ($table)` picks one of three INSERTs (S1 rule on real code) |
| Q006 | UNRESOLVED `BRANCHING` (`SET $column_name = …`) | correct: dynamic column |
| Q010 | UNRESOLVED `UNSUPPORTED_SHAPE` | correct: the SQL is `''` inside a switch case |
| Q007–Q009 | recovered, conversion-unresolved | implicit comma joins (`FROM a, b`) are not supported by the converter (limitation) |
| Q004, Q005 | CONVERTED (INSERT) | sound: assignment and call in the same function body, consecutive |
| Q011 | CONVERTED (SELECT, bound `appointment_no`) | sound |
| Q012 | CONVERTED (static SELECT) | sound. The legacy SQL `WHERE appointment_no=appointment_no` is a legacy bug (always true) and is preserved as is |

## Security report finding (limitation shown)

`security_report.json` reports 0 user-controlled values. That is not true of the app: `$_POST` data reaches `library.php` functions **as function parameters**, which the S6 taint analysis does not trace across calls (stated in the report's `scope`). This is a documented limitation of the static analysis. The converted queries still bind every dynamic value.
