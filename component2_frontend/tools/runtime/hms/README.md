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
query the harness resolved with literal SQL that is a plain `SELECT` with no request input, and one per page
holding those queries; everything is marked `"substitute": "c2-replay"`. Which array a List reads is Stage 4's
decision (reconciliation-spec §2.2, frozen): a page with several Lists gets `list_not_mapped_to_array`.

## Capture and compare

```
venv/Scripts/python.exe tools/runtime/capture.py hms           # Chromium, JavaScript off, plain GET only
venv/Scripts/python.exe tools/runtime/compare.py hms           # raw DOM comparison (no classification)
venv/Scripts/python.exe tools/runtime/side_by_side.py hms admin-panel1.php
```

Tool versions: `tools/runtime/requirements.txt` (Playwright 1.62.0, which pins Chromium build 1234, the build
already installed; PyMySQL 1.2.3). The pipeline's own tests do not need them.
