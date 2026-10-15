# C3 — Limitations and Threats to Validity

This is a living document. Each limitation names the evidence it is based on.

## Benchmark coverage
- **No ENUM/SET and no PDO in any benchmark application.** The 5 original synthetic apps and the 3 unseen third-party apps (HMS, NEWS-BUZZ, Fee-Rainbow; see `benchmarks/benchmark_manifest.json`) use `mysqli_query` (plus one `mysqli_prepare` in Clinic) and no ENUM/SET columns. ENUM/SET handling and PDO `query`/`prepare`/`exec` are therefore evidenced only by independently written fixtures and unit tests, not by real applications.
- **Small N.** Results come from 9 applications: 5 original synthetic and 4 third-party (3 post-adaptation, 1 fresh holdout), all small to mid size. They are not a claim about arbitrary PHP applications.
- **HMS (kishan0725) has no licence.** It was analysed locally only. Its source is not redistributed; only C3's outputs and the commit hash are recorded.
- **Clinic is a development benchmark with query-specific code.** 7 of its 22 sites are converted by guarded, query-ID-specific templates. Clinic results are reported as "14 by general rules + 7 by templates" and are not evidence of generalisation.

## Query identifiers are positional
- Query IDs (`Q001`…) are assigned in extraction order. The 7 Clinic templates are gated on these IDs, so they stop firing (fail-closed: the query becomes UNRESOLVED, never wrongly converted) if a new execution point is found earlier in file order. Adding S22 kept the IDs stable only because `22_prepared_statement.php` sorts last.

## Pre-adaptation findings (to be fixed and re-measured post-adaptation)
- The unchanged pipeline crashed on all 3 unseen apps at stage 3 (`INT(11)` display width), and ALTER-style primary keys were lost (`evidence/preadaptation/README.md`).

## Adaptation (S13)
- The S13 rule (SQL built and executed in the same conditional block) was designed from the Helpdesk holdout class after inspecting the unseen apps' pre-adaptation summaries, which showed the same class dominating. Post-S13 results for Helpdesk and the 3 unseen apps are post-adaptation, not independent generalisation evidence (`evidence/postadaptation/S13/README.md`). The fresh untouched result is Dignity-Dev (14 / 7 / 4; `evidence/fresh_holdout/dignity_dev_hms/`).
- PHP escape sequences in double-quoted strings (e.g. a literal tab printed as `\t`) are not decoded during recovery. The affected query then fails to parse and conversion abstains (NEWS-BUZZ Q003). This is safe, but it costs coverage.

## Security analysis (S6)
- The taint analysis in `security_report.json` is static, intra-file and order-based (last assignment before use, transitive over variables). It does not trace function parameters or other files, except validated cross-file bindings. `$_SESSION` is reported as indirect and not counted as user input. Legacy-risk labels are heuristic: a numeric cast, or an escape inside a quoted literal, counts as mitigated.
- "100% injection-safety coverage" means every user-controlled value in a converted query is a bound parameter or a schema-allowlisted identifier, according to this static analysis. Runtime injection tests against MySQL are E4.

## Fresh holdout findings
- Dignity-Dev builds SQL inside procedural functions from function parameters. The S6 taint analysis does not trace across function calls, so its security report shows 0 user inputs even though `$_POST` data reaches those parameters. Implicit comma joins (`FROM a, b`) are not converted.

## Method
- Behavioural equivalence on MySQL covers selected inputs only. Day-3 behaviour tests used SQLite.
- Generated data access is parameterized SQLAlchemy (`text()` + bind parameters), not ORM query constructs.
- Ground truth for the third-party apps will be single-annotator (the student).
