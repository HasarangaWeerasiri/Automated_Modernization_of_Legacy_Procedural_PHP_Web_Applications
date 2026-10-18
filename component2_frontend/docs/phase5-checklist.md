# Component 2 — Phase 5 Readiness Checklist (the real evaluation run)

| Item | Value |
|---|---|
| Date | 18 October 2026 (written after the HMS rehearsals 5R / 5R2, before any evaluation run) |
| Rules | v1.0 = `27c380e` (freeze), v1.1-final = `535e2e6`. **No rule, spec or pipeline change during Phase 5** (eval-mapping §4.3) |
| Scoring | `docs/eval-mapping.md` **v1.2**, `docs/gold-standard-guide.md` **v1.1**, `tools/eval/score.py` as committed with them |
| Apps | the 5 locked evaluation apps of CONTEXT §5, from the owner's archive `c2-eval-apps.zip` (sha256 manifest per app) |

Every command runs from `component2_frontend/` in PowerShell with `$env:PYTHONUTF8=1` (or Git Bash with
`PYTHONUTF8=1`). `<app>` below is the app's short name (also its gold file name), `<dir>` its unpacked folder.

## 0. Before anything (once)

- [ ] Everything of the rehearsals is committed (5R, 5R2): `git status` shows nothing under `src/`, `config/`,
      `tools/`, `gold/`. `gold/practice/` stays committed (the §4.1 check covers all of `gold/`).
- [ ] `venv/Scripts/python.exe -m pytest -q` passes; `venv/Scripts/python.exe tools/rules_guard.py mocks --rules v1.0`
      prints "byte-identical".
- [ ] Unpack each app from `c2-eval-apps.zip` into its own folder **outside the repository** (or under a git-ignored
      folder), and check every file against the app's sha256 manifest. The tree is never edited afterwards (the run
      records its sha256 as `sourceDigest`; the scorer refuses a changed tree).

## 1. The gold standard (owner, by hand, before the tool sees an app)

- [ ] **Sample** each app: `venv/Scripts/python.exe tools/sample_eval_pages.py <dir>`; append its output (one path
      per line, prefixed by the app name) to `gold/sample.txt`.
- [ ] **Annotate** `gold/<app>.csv` (columns as `gold/template.csv`) by guide v1.1, from the PHP source only:
      `read` rows on the **print** line; `function` rows on the **call** line in the calling file (a never-called
      function: its definition line, `not_ui`); an `if` that only sets a variable printed later: label it by what a
      correct migration shows, on the `if` line; `unsure` in `notes` when unsure.
- [ ] **Second annotator** on 20 % of the sampled pages (`random.Random(20261016)`), without seeing your labels:
      `gold/<app>.second.csv` (agreement: % and Cohen's κ on `if` decisions).
- [ ] **Run settings**, fixed before the run and recorded below (eval-mapping §4.2: no other setting changes): the
      document root (`--docroot`, the folder the URLs start from; empty for the app root) and the library folders
      to leave out (`--exclude`, comma-separated prefixes; default `vendor/`).

      | App | `--name` | `--docroot` | `--exclude` |
      |---|---|---|---|
      | crud-php-mysqli | | | |
      | PHP-MySQL-CRUD-Application | | | |
      | College-Notes-Gallery | | | |
      | ams-native | | | |
      | School-Management-System | | | |

- [ ] **Commit** `gold/sample.txt`, every `gold/<app>.csv` / `.second.csv` and this table, with the date. Check:
      `git status --short gold/` prints nothing.

## 2. The run (per app, both rules versions in one command)

```
venv/Scripts/python.exe tools/eval/run_eval.py <dir> --name <app> --docroot <docroot> --exclude <prefixes>
```

- [ ] It refuses (exit 3) if `gold/` has any uncommitted change or `gold/<app>.csv` is not committed: fix that, never
      bypass it.
- [ ] It prints, per version, the units, crashes and units not compiling. Check `output/eval/runs/<app>/run.json`:
      `goldCommitted` < `firstRun`, `codeChanges: []`, `codeCommit` = the commit of step 0, `rules: ["1.0", "1.1"]`.
- [ ] Run it **once** per app. A second run keeps `firstRun`; never delete `output/eval/runs/<app>/` to start over.
- [ ] **No routing or assets layer**: never run `tools/routing/routes.py`, `tools/assets/legacy_assets.py` or
      `generated_app.py --routes / --assets` on an evaluation app (eval-mapping §4 item 2, no other setting;
      `docs/routing.md` §4, `docs/assets-layer.md` §4). They are deployment aids, not measured.

## 3. Scoring and tables

```
venv/Scripts/python.exe tools/eval/score.py <app>
venv/Scripts/python.exe tools/results_table.py phase5 --out docs/phase5-tables.md
```

- [ ] `score.py` writes `output/eval/<app>.json` and `docs/eval-results.md` (all scored apps, v1.0 and v1.1 side by
      side, every row). It refuses if the gold changed or was committed after `firstRun`.
- [ ] **Targets / checks:** `unmapped` 0 and `not_in_mapping` 0 (any such row: stop and report it — never add a
      mapping row during the run); every `wrong` row listed with file:line and the rule; unsound decisions (strict
      and flag-adjusted) per app and in total.
- [ ] Report numbers only from `docs/eval-results.md` / `docs/phase5-tables.md` (generated, never copied by hand).
- [ ] Runtime render match (where an app runs): the Phase 4 method (`tools/runtime/`), only if the owner decides it;
      `tools/results_table.py phase4` for its tables.

## 4. If something goes wrong

| Situation | What to do |
|---|---|
| A **scorer bug** (score.py does not do what eval-mapping v1.2 says) | Do not edit the gold or the mapping. Log the bug (date, row, mapping clause) in the results, fix `tools/eval/` **after** scoring, re-score, and report the scores **before and after** the fix (eval-mapping §5) |
| A **gold mistake** found after the run | Never edit `gold/<app>.csv`. Write it in `gold/corrections.md` (date, row, reason); report the results both ways (guide §1.4) |
| A **mapping gap** (`unmapped` / `not_in_mapping` row) | Stop; report the rows. The owner decides; any mapping change after the run is a new version with its date and both results |
| A **tool finding** (a wrong decision of the pipeline) | Record it as a v1.1 limitation (rules are closed); no pipeline change in Phase 5 |
| A crash or a unit that does not compile | Record it (compile rate); no fix during Phase 5 |
| The scorer refuses (§4.1) | Read the reason; commit what is missing **only if** it is gold that was ready before the run; otherwise the run is invalid and is reported as such |

## 5. What the HMS rehearsals showed (5R, 5R2; a discovery app)

- The whole chain works: gold committed → run → score → tables; §4.1 refuses a dummy file in `gold/` (tested live).
- Under mapping v1.2 and guide v1.1 (practice key v2): v1.0 / v1.1-final precision 0.982 / 1.000, recall 0.798 /
  0.836, unsound (strict) 2 / 0, binding accuracy 0.865, compile rate 1.000; 0 unmapped, 0 not_in_mapping, 3 misses
  (ifs that only set a variable: a fair miss, guide v1.1). Details: `docs/phase5r-rehearsal.md`.
