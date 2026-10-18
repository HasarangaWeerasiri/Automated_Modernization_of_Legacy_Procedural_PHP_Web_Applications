# Phase 4e — phpBlog runtime rendering check

The same check as HMS (`tools/runtime/hms/README.md`, `docs/phase4-results.md`) on a second rule-discovery app, phpBlog
(`codeplay-code/phpBlog-blogScript` @ `4ffe3c9`, GPL-3.0), under boundary rules **v1.1 final**. This folder holds the
legacy side; outputs go to `output/runtime/phpblog/` (git-ignored).

## Legacy side: phpBlog unmodified

**No phpBlog file is edited.** `prepare.py` serves a `git archive` of commit `4ffe3c9641a42b99829cf4b8e4185093793a3abc`
(tar sha256 `be9531fb…2b09`), mounted read-only, and seeds the database from phpBlog's own `install/database.sql` at
that commit, byte for byte (10,270 bytes, sha256 `11edc078…9b9d`).

| Choice | Value | Why |
|---|---|---|
| PHP | `php:8.2-apache` + `docker-php-ext-install mysqli`, `a2enmod rewrite`, `AllowOverride All` (`Dockerfile`) | phpBlog uses `mysqli` only; its own `.htaccess` maps URLs without `.php` (mod_rewrite) |
| Database | `mariadb:10.6`, database `phpblog`, root password `phpblog` | `database.sql` has no `CREATE DATABASE`; the image's init scripts run in this one |
| Errors | `display_errors = Off`, log to `build/logs/errors.log` (`php.ini`) | notices must not enter the HTML |
| Ports | web `127.0.0.1:18081`, database `127.0.0.1:13307` | the database port is for the replay API only |
| Replay user | `initdb/02-replay-user.sql`: `c2replay`, `SELECT` on `phpblog.*` | the replay API reads, never writes |

### The two configuration files (made as the installer makes them)

phpBlog has no `config.php` in its repository; its installer writes it, and rewrites `config_settings.php`. Both are
made by `make_config.php`, run with the web container's PHP (`php:8.2-apache`), which repeats the installer's code
**line for line** on the inputs below, and are mounted into the container over the read-only tree. The installer's
web pages are not run.

| File | Made as | Inputs (what the installer form / request would give it) |
|---|---|---|
| `config.php` (479 bytes, sha256 `a3942d15…0c8c`) | `install/done.php` 49–53: `file_get_contents("config.tpl")` (`CONFIG_FILE_TEMPLATE`, `install/core.php` 18), then `str_replace` of `<DB_HOST>`, `<DB_NAME>`, `<DB_USER>`, `<DB_PASSWORD>`; written as is (59–60) | `database_host` = `db` (the compose service), `database_name` = `phpblog`, `database_username` = `root`, `database_password` = `phpblog` (done.php 6–9) |
| `config_settings.php` (606 bytes, sha256 `0ce325b4…032e`) | `install/done.php` 3 (`include "../config_settings.php"`: the repository's own settings), 14–20 (`site_url` = the request URL up to `/install`), 21 (`email`), 22 (`'<?php $settings = ' . var_export($settings, true) . '; ?>'`) | `HTTP_HOST` = `127.0.0.1:18081`, `REQUEST_URI` = `/install/done.php` → `site_url` = `http://127.0.0.1:18081`; `email` = `admin@mail.com` (the repository's default, done.php 11) |

Not done (on purpose): `install/done.php` 24–46 imports `database.sql` line by line through `mysqli` — here the database
image imports the same file (plain SQL, no difference); 56 inserts an **admin user** from the form — **no row is added**
(owner, Phase 4e), so nobody can log in. `prepare.py` leaves an empty `htdocs/config.php` as the mount point of the
real file (Docker cannot create a mount point inside a read-only mount); it is not a phpBlog file.

**Seed:** `database.sql` creates 12 tables and fills `albums` (1), `categories` (1: Site News), `menu` (5), `pages` (1) and
`widgets` (1). It has **no posts, no comments, no users** (reported to the owner: every post list compares only its
empty state).

Run:

```
venv/Scripts/python.exe tools/runtime/phpblog/prepare.py
docker compose -f tools/runtime/phpblog/docker-compose.yml up -d --build
```

### Seed + fixture (Phase 4f)

`initdb/03-fixture.sql` is a small, fixed, **data-only** fixture (owner, Phase 4f), loaded after `database.sql` (01) and
the replay user (02) only through the override `docker-compose.fixture.yml`. No phpBlog file and no generated code
changes. It adds 3 published posts in 2 categories (post 1 in Site News, featured; posts 2–3 in a new category
Tutorials), 1 static page (Terms), 2 approved guest comments, and 1 menu item (Terms → `page?name=terms`); **no users**,
so nobody logs in. Values are written as phpBlog's admin pages write them: dates `d.m.Y`, times `H:i`, content
HTML-escaped. Recreate the database to switch seeds (the init scripts run only on an empty volume):

```
docker compose -f tools/runtime/phpblog/docker-compose.yml -f tools/runtime/phpblog/docker-compose.fixture.yml down -v
docker compose -f tools/runtime/phpblog/docker-compose.yml -f tools/runtime/phpblog/docker-compose.fixture.yml up -d --build
```

and classify a fixture run with `--data fixture` (reviewed findings name the data they were reviewed on: a difference
path depends on it). Back to the shipped seed: the same two commands with the base file only.

## Generated side, capture, compare, classify

```
venv/Scripts/python.exe tools/runtime/replay/generate.py phpblog          # 48 queries, 29 replayable
venv/Scripts/python.exe tools/runtime/replay/server.py phpblog --port 18091 --db-port 13307 --database phpblog
venv/Scripts/python.exe tools/runtime/generated_app.py phpblog --folder phpblog
venv/Scripts/python.exe tools/runtime/generated_app.py phpblog --folder phpblog-nodata --no-data
# serve c2-build/phpblog-runtime on :18102 (API_BASE_URL=http://127.0.0.1:18091), phpblog-nodata-runtime on :18103
venv/Scripts/python.exe tools/runtime/capture.py phpblog --legacy http://127.0.0.1:18081 --generated http://127.0.0.1:18102 --folder phpblog
venv/Scripts/python.exe tools/runtime/capture.py phpblog --legacy http://127.0.0.1:18081 --generated http://127.0.0.1:18103 --folder phpblog-nodata
venv/Scripts/python.exe tools/runtime/compare.py phpblog
venv/Scripts/python.exe tools/runtime/classify.py phpblog --before phpblog-nodata
venv/Scripts/python.exe tools/runtime/simulate_css.py phpblog
venv/Scripts/python.exe tools/runtime/side_by_side.py phpblog index.php
```

Phase 4f runs (folders under `output/runtime/`; each copies its build's `pipeline/` and `lists.json` first):
`phpblog-v1.0`, `phpblog` (v1.1 before change 4), `phpblog-c4` (v1.1 final) on the shipped seed, and
`phpblog-v1.0-fx`, `phpblog-pre4-fx`, `phpblog-c4-fx` on seed + fixture, each with its own no-data build as `--before`.

Phase 4g: v1.1 final (change 5) is `generated_app.py phpblog --folder phpblog-final --build phpblog-final-runtime`
(and `phpblog-final-nodata`). Runs on seed + fixture add the parameterised GETs of `gets.json`
(`capture.py ... --gets tools/runtime/phpblog/gets.json`): `post`, `category` and `page` with the fixture's own slugs,
captured as `<unit>@<query>`. **Recreate the database before each run** (`down -v`, `up -d`): `post.php` 24 counts
views on every GET and the sidebar orders popular posts by views, so the legacy pages change from run to run. Folders:
`phpblog-v1.0-fx2`, `phpblog-pre4-fx2`, `phpblog-c4-fx2`, `phpblog-final-fx` (seed + fixture, 13 pages) and
`phpblog-final-seed` (shipped seed).

Not generated, so not compared: `rss.php`, `sitemap.php` (`non_html_response`), `core.php`, `install/core.php`
(`error_output_only`). A page that **redirects** an anonymous GET (a `<meta http-equiv="refresh">`, which a browser
follows with JavaScript off) is recorded with its target and left out of the element comparison (`compare.json`
`redirected`): 36 of the 43 pages. Results: `docs/phase4-results.md` §6.

**Warning for anyone re-running it:** `install/upgrade-v2.2.php` rewrites `../config.php` on a plain GET (line 41). The
read-only mount stops it here.
