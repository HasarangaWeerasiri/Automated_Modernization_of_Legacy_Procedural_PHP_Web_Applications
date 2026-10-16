# Phase 4a — HMS runtime rendering check

Legacy HMS and the generated HMS app, both running, captured and compared (roadmap Phase 4, SO4). This folder
holds the legacy side; `tools/runtime/` holds the rest. Outputs go to `output/runtime/hms/` (git-ignored).

## Legacy side: HMS unmodified

**No HMS file is edited.** HMS is served from a `git archive` of commit
`777fda46b77a820977a5ba616283dbfbc40bf7e1` (not the working copy in `legacy-apps/hms`, which has 11 locally
patched files), and the database is seeded from HMS's own `myhmsdb.sql` at that commit, byte for byte.

| Choice | Value | Why |
|---|---|---|
| Source | `prepare.py`: `git archive 777fda4` → `build/htdocs/` (read-only mount) | the pinned commit, not the patched working copy |
| PHP | `php:7.4-apache` + `docker-php-ext-install mysqli` (`Dockerfile`) | the brief's version; HMS uses `mysqli_*` only |
| Database | `mariadb:10.1.48` | `myhmsdb.sql` was dumped from MariaDB 10.1.31 (its header): the same major version, so the same defaults (e.g. a non-strict SQL mode) |
| Schema | `build/initdb/01-myhmsdb.sql` = `myhmsdb.sql` at the commit; `MYSQL_DATABASE=myhmsdb` | the dump has no `CREATE DATABASE`; the image's init scripts run in that database |
| Credentials | `MYSQL_ALLOW_EMPTY_PASSWORD=yes` | HMS connects as `root` with an empty password |
| Host `localhost` | the `/run/mysqld` socket directory is a shared volume; `mysqli.default_socket = /run/mysqld/mysqld.sock` (`php.ini`) | `mysqli_connect("localhost", …)` means the Unix socket; this makes it reach the database container with no source change |
| Errors | `display_errors = Off`, `log_errors = On`, `error_log = /var/log/php/errors.log` (`build/logs/`) | PHP notices must not enter the HTML; they are kept in the log |
| Time zone | `date.timezone = UTC` | PHP 7.4 warns without one |
| Ports | web `127.0.0.1:18080`, database `127.0.0.1:13306` | the database port is for the replay API only |
| Replay user | `initdb/02-replay-user.sql`: `c2replay`, `SELECT` on `myhmsdb.*` only | the substitute replay API reads, never writes; HMS does not use it |

`include/config.php` connects to a database named `hms`, which `myhmsdb.sql` does not create: that is HMS's own
state at the commit and is left as it is.

Run:

```
venv/Scripts/python.exe tools/runtime/hms/prepare.py
docker compose -f tools/runtime/hms/docker-compose.yml up -d --build
```

## Generated side

```
venv/Scripts/python.exe tools/harness/run_apps.py hms          # the whole-app run (output/apps/hms)
venv/Scripts/python.exe tools/runtime/replay/generate.py hms   # substitute contract + endpoint map (test fixture)
venv/Scripts/python.exe tools/runtime/replay/server.py hms     # replay API on 127.0.0.1:18090 (keep running)
venv/Scripts/python.exe tools/runtime/generated_app.py hms     # Stages 1-5 with the substitute, next build
API_BASE_URL=http://127.0.0.1:18090 node output/test-app/node_modules/next/dist/bin/next start \
    output/test-app/c2-build/hms-runtime -p 18100 -H 127.0.0.1
```

The replay API is a **test fixture standing in for Component 1**, never part of C2's output: one endpoint per
query (of a page or a function component) the harness resolved with literal SQL that is a plain `SELECT` with no
request input, and an endpoint-map entry per such query, keyed by its `queryNodeId` (reconciliation-spec v0.3,
post-freeze): a List whose loop reads that query maps to it exactly. Everything is marked
`"substitute": "c2-replay"`. Phase 4a used one page-level entry per page instead (commit `2c35b2b`), which Stage 4's
one-List rule could not map.

## Capture, compare, classify

```
venv/Scripts/python.exe tools/runtime/capture.py hms           # Chromium, JavaScript off, plain GET only
venv/Scripts/python.exe tools/runtime/compare.py hms           # DOM comparison
venv/Scripts/python.exe tools/runtime/classify.py hms          # one class per difference -> classified.json
venv/Scripts/python.exe tools/runtime/simulate_css.py hms      # SIMULATION: generated + legacy stylesheets
venv/Scripts/python.exe tools/runtime/side_by_side.py hms admin-panel1.php
```

`classify.py` gives every difference one class: `flag:<kind>` only when the pipeline emitted that flag **at the
node that printed the differing element** (or in the function component called inside it), `env:`, `norm:`, or
`UNEXPLAINED`. Reviewed root causes of UNEXPLAINED items are in `findings.json` (they stay UNEXPLAINED).

The "before data wiring" side (Phase 4a) is kept in `output/runtime/hms-4a/` (its captures), re-compared with the
same `compare.py` and classified with `classify.py hms --folder hms-4a`; its pipeline is reproduced with the 4a
generator of commit `2c35b2b` (page-level entries).

Comparison fixes in Phase 4b (the comparison itself, not the pipeline): `style` values are compared in a canonical
form (React writes `margin-top:50px` where PHP wrote `margin-top: 50px;`), and HTML boolean attributes are valued
`""` (`required="required"` = `required=""`).

## Phase 4c: rules v1.0 and v1.1 side by side

Post-freeze changes 1 (R-I1 scope, boundary rules v1.1) and 2 (spec v0.14) are on by default; `--rules v1.0` runs
the frozen rules. Each run has its own folder under `output/runtime/`, and `lists.json` records its rules version:

```
venv/Scripts/python.exe tools/runtime/generated_app.py hms --folder hms-v1.1               # with replay data
venv/Scripts/python.exe tools/runtime/generated_app.py hms --folder hms-v1.1-nodata --no-data
# serve c2-build/hms-runtime on :18100 and c2-build/hms-nodata-runtime on :18101, then
venv/Scripts/python.exe tools/runtime/capture.py hms --folder hms-v1.1
venv/Scripts/python.exe tools/runtime/capture.py hms --folder hms-v1.1-nodata --generated http://127.0.0.1:18101
venv/Scripts/python.exe tools/runtime/compare.py hms-v1.1
venv/Scripts/python.exe tools/runtime/classify.py hms --folder hms-v1.1 --before hms-v1.1-nodata
```

`hms-v1.0` is the Phase 4b run (rules v1.0), kept as it was and classified again (`--before` defaults to `hms-4a`).
The no-data build is the baseline that tells List rows from static elements; without it, elements v1.1 adds (the
navbar) would be counted as rows.

`classify.py` also has the class `flag-but-wrong:<kind>` (owner, Phase 4c): a flag at the node, but the generated tree
contradicts the browser's. That is either a flag claiming browser equivalence (an HTML5 repair or close, R-L6), or
`<head>` content dropped although the browser's tree has it in `<body>`. It is counted separately from `flag:`.
Findings in `findings.json` carry the rules version they were found under, and apply only to runs of that version. A
difference whose printing node stands inside an if with an `unresolvedCondition` is `flag:unresolvedCondition`.

Tool versions: `tools/runtime/requirements.txt` (Playwright 1.62.0, which pins Chromium build 1234, the build
already installed; PyMySQL 1.2.3). The pipeline's own tests do not need them.
