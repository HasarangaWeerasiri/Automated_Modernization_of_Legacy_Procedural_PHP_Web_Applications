# Component 2 — Phase 5R: Dress Rehearsal of the Phase 5 Run (HMS)

| Item | Value |
|---|---|
| Date | 18 October 2026 |
| App | HMS (`kishan0725/Hospital-Management-System` @ `777fda4`), a **rule-discovery** app. No evaluation app was opened |
| Key | `gold/practice/hms-practice-gold.csv` (written by Claude, the research chat), committed by the owner as `0e0c61d` **before** the run. The owner's key (`hms-practice-owner.csv`) was not attached: no agreement measure |
| Rules | v1.0 = the freeze `27c380e`; v1.1-final = `535e2e6` (changes 1–5). The run came from `0e0c61d` (= `535e2e6` + the key), pipeline code clean (`run.json` `codeChanges: []`) |
| Tools | `tools/eval/run_eval.py`, `tools/eval/score.py` (fixed in this phase, §4), `tools/results_table.py phase5` |

## 1. The run, as Phase 5 will do it

1. HMS unpacked unmodified from `git archive 777fda4` into `output/eval/src/hms/` (the working copy carries 11
   locally patched files; not used).
2. `run_eval.py output/eval/src/hms --name hms --gold-dir gold/practice --key hms-practice-gold --exclude TCPDF/,vendor/`
   (mapping §4.2: v1.0 and v1.1, no contract, no endpoint map; HMS's own pinned excludes). Both versions: 33 units,
   0 crashes, 0 tsc errors. `firstRun` (1792325957) is after the key's commit (1792325928).
3. `score.py hms --key hms-practice-gold --gold-dir gold/practice --out output/eval/practice
   --doc output/eval/practice/eval-results-practice.md` (the real `docs/eval-results.md` is not touched).
4. `results_table.py phase5 output/eval/practice/hms-practice-gold.json` (tables below, from code).

**Mapping §4.1, rule 1, tested live:** with an uncommitted `gold/dummy.txt`, `score.py` and `run_eval.py` both refused
(exit 3, "component2_frontend/gold/ has uncommitted changes: ?? …/gold/dummy.txt") and wrote nothing; after the file was
removed (`git status` clean), scoring ran again.

## 2. Metrics (mapping §3), v1.0 and v1.1-final side by side

After the scorer fixes of §4 (the scores before them are in §4, as mapping §5 asks):

| Metric | v1.0 | v1.1-final |
|---|---|---|
| Scored gold rows | 253 | 253 |
| Boundary rows / read rows | 127 / 126 | 127 / 126 |
| Boundary precision | 0.963 | 0.982 |
| Boundary recall | 0.811 | 0.850 |
| **Unsound decisions (strict)** | **4** | **2** |
| Unsound decisions (flag-adjusted) | 2 | **0** |
| Abstain rate | 0.051 | 0.051 |
| Binding accuracy | 0.746 | 0.746 |
| Wrong bindings | 0 | 0 |
| Compile rate | 1.000 (26 pages) | 1.000 (26 pages) |
| Flag rate per page | 20.00 | 16.88 |
| Rows correct / abstain / wrong / miss / unmapped | 197 / 13 / 4 / 28 / 11 | 202 / 13 / 2 / 28 / 8 |
| Not scored: `unsure` / `not_in_mapping` | 4 / 7 | 4 / 7 |

Agreement (owner vs second key): **not measured**, only one key was attached.

## 3. Every wrong, unmapped, miss and not_in_mapping row, with a verdict

Verdict = which is wrong: the **tool**, the practice **key**, or the **mapping** (a case the mapping does not decide;
for the owner). No key was changed.

### 3.1 Wrong

| Row | Gold | Tool (version) | Verdict |
|---|---|---|---|
| `search.php:4` if | request_handler | R-I1: the POST-only results page rendered on every GET (v1.0) | **Tool** (v1.0): the unsound decision of Phase 4b, fixed by post-freeze change 1; v1.1 correct |
| `include/config.php:1` unit | not_ui | a page that renders "Failed to connect to MySQL: …" unconditionally (v1.0 R-I1) | **Tool** (v1.0), same cause; v1.1: the error is a hidden conditional (unmapped, §3.2) |
| `admin-panel.php:551` if | not_ui | R-I4 conditional, `unresolvedCondition(condition_not_translatable)`: `mysqli_error` output hidden and flagged (both versions; †) | **Mapping**: "wrong: a rendered conditional" — is a conditional whose content can never render "rendered"? The key could also argue: the if prints error output, the guide does not say how to label error-only output. Flag-adjusted: not counted (specific review) |
| `doctor-panel.php:281` if | not_ui | the same (both versions; †) | **Mapping**, as above |

### 3.2 Unmapped (counted like a miss, listed)

| Rows | Gold | Tool | Verdict |
|---|---|---|---|
| `contact.php:1`, `func.php:1`, `func1.php:1`, `func3.php:1`, `header.php:1` unit (both versions); `include/config.php:1` (v1.1) | not_ui | a generated page that renders nothing (only the `no_endpoint` C2Todo, or a hidden conditional) | **Mapping**: the table names `notGenerated` / no unit as correct and "a page that renders content" as wrong; an empty page is neither. The harness makes every file a page unit (POST endpoints, an include that holds only `<head>` content) |
| `contact.php:3`, `func.php:4`, `func1.php:4`, `func3.php:4` if (v1.0) | request_handler | R-I1 on a handler whose only output is inline JS (dropped): the page renders nothing | **Mapping**: not "handler output rendered" (wrong), not a hidden / R-I7 result (correct). v1.1: R-I7, correct |
| `func1.php:44`, `func3.php:37` loop (both versions) | not_ui | an inline-map List inside `display_docs()`, a function nothing calls (generated as a "function not in the app") | **Mapping**: not "a List that renders output"; the key's reason (dead code) is right |

### 3.3 Misses (28, both versions)

| Rows | Gold | What the tool has | Verdict |
|---|---|---|---|
| `newfunc.php:42`, `:53` function | component | R-F1 components `DisplaySpecs` / `DisplayDocs` at their **call sites** (`admin-panel.php` 331 / 359) | **Key**: the guide (§2, `line`) puts a function row on the call / include line; the key used the definition line (its notes name the calls) |
| `appsearch.php:44–51`, `doctorsearch.php:38–41`, `messearch.php:37–40`, `patientsearch.php:38–42` read (21) | `table.column` | the reads where the values are **printed** (`<td>$fname</td>`, lines 66–73 / 43–46 / 42–45 / 44–48) | **Key** (and a **guide gap**): the key put these rows on the copy lines (`$fname = $row['fname']`), while for `admin-panel1.php` it used the print lines (which matched). At the print lines 13 of the 21 resolve to exactly the key's binding (doctor/mes/patient search); the 8 of `appsearch.php` stay `unresolved` (abstain): the strict copy propagation stops at the three `if`s between the copy and the print (tool limitation, Phase 2a's documented choice) |
| `appsearch.php:52`, `:56`, `:61` if | inline | no node: the ifs print nothing themselves, they set `$appstatus`, printed at 74 (an unresolved read) | **Tool limitation by design** (a value chosen by ifs is data flow, boundary-rules §10, "markup in variables") and a **guide gap** (how to label an if that only sets a variable printed later) |
| `func1.php:46`, `func3.php:39` read | unknown | the read at the print line (48 / 41), `unresolved` — correct for `unknown` | **Key**: copy line, as above |

### 3.4 Not in mapping (7, not scored)

`admin-panel.php:82`, `:142`, `func.php:66`, `func1.php:39`, `:62`, `func2.php:70`, `func3.php:32` — function `not_ui`.
**Mapping gap**: the guide lists `function not_ui` as a decision, mapping §2 has no row for it.

`unsure` (4): `include/header.php`, `footer.php`, `setting.php`, `sidebar.php` units — listed, not scored. Tool
decisions on constructs the key does not list (`extra`): `include/header.php:36` loop (List R-L3b), `include/config.php:8`
if (R-I4).

### 3.5 Abstains (13, both versions; misses, not wrong)

Four includes the key calls not_ui and the tool abstains on (`cross_file_output`): three of files whose only output
is a JS alert in a POST handler (`admin-panel.php:3`, `doctor-panel.php:3`, `prescribe.php:3`) and `index1.php:2` (a
`header.php` holding only `<head>` content); 9 reads the tool leaves `unresolved`
(`newfunc.php:49` `select distinct(spec)`; session / request values copied before a branch, `prescribe.php` 115–143,
`admin-panel.php:225`). Conservative, as intended.

## 4. tools/eval fixes (logged; scores before and after)

| # | Fix | Why |
|---|---|---|
| 1 | §4.1 refusal covers **everything under `gold/`** (new, changed, deleted, untracked; `gold/corrections.md` exempt, guide §1.4), also when the key is in a subfolder (`gold/practice/`) | Mapping §4.1 says "`gold/` has uncommitted changes"; only the app's own files were checked, so a dummy file in `gold/` passed |
| 2 | `--key <name>` in `score.py` and `run_eval.py` | A practice key keeps its own name and folder (`gold/practice/`, never `gold/`); report `output/eval/<key>.json` |
| 3 | `run.json` records `codeCommit` and `codeChanges` (uncommitted changes to `src/`, `config/`, `tools/harness/`) | The run must name the commits it ran from ("the commits above"); a Phase 5 run needs `codeChanges: []` |
| 4 | The results page says v1.1 = changes 1–5 (`535e2e6`) | It said "changes 1–3" |
| 5 | "Wrong" for `unit not_ui`, `if request_handler` (R-I1) and `loop not_ui` needs **rendered** output, as the mapping's wrong column says ("a page that renders content", "handler output rendered", "a List … that renders output"); otherwise `unmapped` (listed). An `if not_ui` whose only effect is a `header_redirect` flag is "no node" (correct) | The scorer counted any generated page, any R-I1, any List as wrong, and a header-redirect-only if as unmapped |

Not changed (the owner's decision): `if not_ui` against a conditional whose content is hidden stays **wrong** — the
mapping's "a rendered conditional" may mean the conditional itself (the synthetic test of Phase 4d scores it so).

Effect of fix 5 (fixes 1–4 change no score):

| | v1.0 before → after | v1.1-final before → after |
|---|---|---|
| Boundary precision | 0.864 → 0.963 | 0.906 → 0.982 |
| Boundary recall | 0.748 → 0.811 | 0.764 → 0.850 |
| Unsound (strict) | 15 → 4 | 10 → 2 |
| Unsound (flag-adjusted) | 13 → 2 | 8 → 0 |
| Rows correct / wrong / unmapped | 189 / 15 / 8 → 197 / 4 / 11 | 191 / 10 / 11 → 202 / 2 / 8 |

Row by row: 11 (v1.0) and 8 (v1.1) `unit` / `loop` / R-I1 rows wrong → unmapped; 8 (v1.0) and 11 (v1.1) header-redirect
ifs unmapped → correct. Tables: `output/eval/practice/phase5-tables-before-fix.md`, `phase5-tables.md`.

## 5. What would block or bias the real Phase 5 run

1. **Mapping gaps to decide before the first evaluation run** (still allowed: no evaluation run has happened): function
   `not_ui` (no mapping row); an empty generated page for a `unit not_ui`; an R-I1 whose page renders nothing; a List in
   a never-called function; a hidden conditional against `if not_ui` (§3.1). Each is now `unmapped` / `wrong` / not
   scored and listed — visible, but the owner should fix the reading in mapping v1.2 first.
2. **Annotation conventions** (guide): a `read` row's line should be stated (the line where the value is **printed**,
   as the tool reads it — the key mixed print and copy lines and lost 21 rows); `function` rows go on the **call** line
   (the key used definition lines: 2 misses); how to label an `if` that only sets a variable printed later (3 misses).
   These cost recall without saying anything about the tool.
3. **The scorer fixes of §4** are uncommitted tools code: they must be committed (and accepted by the owner) before the
   real run, so that `run.json` names a commit that contains them.
4. **Excludes per app.** HMS needed `--exclude TCPDF/,vendor/`; for each evaluation app the excludes (libraries) must be
   fixed and recorded before the run, or library code is generated and enters the flag rate.
5. **Every file is a page unit.** POST endpoints and include-only files are generated as (empty) pages; with the mapping
   as it is, a key that labels them `unit not_ui` gets `unmapped` rows (item 1).
6. **Binding accuracy depends on copy propagation** (same block only, stops at any control statement between copy and
   print): patterns like `appsearch.php` stay `unresolved` (abstain, not wrong). A documented limit, it will show in the
   evaluation apps too.
7. `gold/practice/` lives inside `gold/`: it must stay committed, or every real run is refused (fix 1, by design).

## 6. Phase 5R2: mapping v1.2, guide v1.1, two practice keys

Mapping v1.2 (owner, 18 Oct, before any evaluation run) states the principle **wrong needs output that renders; a
non-gold result that renders nothing is abstain**, and fills the five gaps of §5 with it; guide v1.1 fixes the line
conventions. `tools/eval/score.py` implements v1.2 exactly (rows in `tests/test_phase5r2.py`, one test per new or
changed row): `if not_ui` / `request_handler`, `loop not_ui`, `unit not_ui` results that render nothing → abstain;
`function not_ui` (new row) — on a call line as any function row, on a definition line (a never-called function) by
the function defined there, read from the app's source tree that run.json names (refused if its sha256 is no longer
`sourceDigest`): no component → correct, a component nothing calls → abstain, a component rendered → wrong.

**Keys.** `gold/practice/hms-practice-gold.csv` (5R, `0e0c61d`, unchanged) and `hms-practice-gold-v2.csv` (owner,
`b72f1e3`: the same key with only guide v1.1 line conventions applied — 23 read rows on print lines, the 2
`newfunc` function rows on their call lines; 25 rows differ). The v2 key was committed after the 5R run, so §4.1
refused it against that run (as it must); it was scored against a new run (`output/eval/runs-v2/`, from `b72f1e3`,
`codeChanges: []`), whose 786 output files are byte-identical to the 5R run's (only the folder name differs inside
some files).

| Metric | 5R key 1 v1.0 | 5R key 1 v1.1 | v1.2 key 1 v1.0 | v1.2 key 1 v1.1 | v1.2 key 2 v1.0 | v1.2 key 2 v1.1 |
|---|---|---|---|---|---|---|
| Boundary precision | 0.963 | 0.982 | 0.981 | **1.000** | 0.982 | **1.000** |
| Boundary recall | 0.811 | 0.850 | 0.784 | 0.821 | 0.798 | 0.836 |
| Unsound (strict) | 4 | 2 | 2 | **0** | 2 | **0** |
| Unsound (flag-adjusted) | 2 | 0 | 2 | 0 | 2 | 0 |
| Abstain rate | 0.051 | 0.051 | 0.119 | 0.108 | 0.150 | 0.139 |
| Binding accuracy | 0.746 | 0.746 | 0.746 | 0.746 | 0.865 | 0.865 |
| Wrong bindings | 0 | 0 | 0 | 0 | 0 | 0 |
| Compile rate | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Flag rate per page | 20.00 | 16.88 | 20.00 | 16.88 | 20.00 | 16.88 |
| Scored rows | 253 | 253 | 260 | 260 | 260 | 260 |
| Correct / abstain / wrong / miss / unmapped | 197 / 13 / 4 / 28 / 11 | 202 / 13 / 2 / 28 / 8 | 199 / 31 / 2 / 28 / 0 | 204 / 28 / 0 / 28 / 0 | 216 / 39 / 2 / 3 / 0 | 221 / 36 / 0 / 3 / 0 |
| Not scored: not_in_mapping / unsure | 7 / 4 | 7 / 4 | 0 / 4 | 0 / 4 | 0 / 4 | 0 / 4 |

Recall moves down from 5R to v1.2 key 1 because the 7 `function not_ui` rows are now scored (2 correct, 5 abstain:
`display_admin_panel()` / `display_docs()` components nothing calls); precision moves up because the 2 hidden
`mysqli_error` conditionals and v1.0's 11 nothing-rendering results are abstain, not wrong.

**Targets met:** unmapped 0 and not_in_mapping 0, both keys, both versions.

**Every remaining non-correct row (v2 key):**

| Row | Gold | v1.0 | v1.1-final | Verdict |
|---|---|---|---|---|
| `search.php:4` if | request_handler | wrong: R-I1 renders the POST-only results on GET | correct (conditional, `needs_request_context`) | **Tool** (v1.0), fixed by post-freeze change 1 |
| `include/config.php:1` unit | not_ui | wrong: R-I1 renders the connect-error text | abstain (page renders nothing) | **Tool** (v1.0), same cause |
| `appsearch.php:52`, `:56`, `:61` if | inline | miss | miss | **Tool limitation by design**: the ifs only set `$appstatus` (data flow, no node); guide v1.1 calls it a fair miss |

Key 1 has the same rows plus its 25 convention misses (§3.3). The abstains (36 under v1.1, v2 key) are: 13 of 5R
(§3.5), 8 `appsearch.php` reads now on their print lines (copy propagation stops at the ifs between copy and print),
and the v1.2 nothing-rendering results (6 empty pages, 2 Lists and 5 components in never-called functions, 2 hidden
`mysqli_error` conditionals).

**The 28 misses of 5R:**

| Change | Misses removed | Rows |
|---|---|---|
| Guide v1.1 line conventions (key v2) | **25** | 23 read rows moved to print lines (13 doctor / mes / patient search → correct, 8 appsearch → abstain, 2 func1 / func3 `unknown` → correct), 2 function rows moved to `admin-panel.php` 331 / 359 → correct (R-F1) |
| Mapping v1.2 | 0 | it changes results the tool did produce (unmapped, not_in_mapping, wrong → abstain / correct), not rows the tool has no construct for |
| Remaining | 3 | `appsearch.php` 52 / 56 / 61 (above) |

**Scorer change log (Phase 5R2, before any evaluation run):** mapping v1.2 implemented (the 5R "wrong needs rendered
output" fix is its first application: its `unmapped` results are now `abstain`, as v1.2 says); `function not_ui`
rows matched on call lines and on definition lines; `source_digest` shared by run_eval and score (score refuses a
source tree that is no longer the run's). Synthetic test pins changed (`tests/test_phase4d.py`): `index.php` 31 / 32
`if not_ui` against a hidden conditional wrong → abstain; v1.0 counts correct / wrong / abstain 25 / 4 / 3 → 25 / 2 / 5,
unsound strict / flag-adjusted 6 / 4 → 4 / 4.
