# Benchmark runnability test

A candidate application is admitted as a benchmark only if it passes every criterion below
**without any change to its source**. An application that fails is discarded and another is
selected; it is never repaired.

## Criteria

| ID | Criterion |
|---|---|
| R1 | Source is obtainable at a pinned commit, and its licence status is recorded. |
| R2 | It matches the input profile: procedural PHP, no framework, `mysqli` with inline SQL, session-based login, 3–20 tables. |
| R3 | It ships an SQL dump with schema and seed data that loads into the legacy database without error. |
| R4 | It starts unmodified in the legacy container; its pages return HTTP 200 with no fatal PHP error. |
| R5 | At least one page renders seed data read from the database, including a page reached after logging in as a seed identity. |
| R6 | At least one write request changes the database, and the change is observable by query. |
| R7 | Its own pages are server-rendered: no AJAX call is needed to obtain data. |

Logging in under R5 is a precondition for reaching pages. It is not a test of access control,
which is out of scope.

## Result: HMS — PASS (2026-10-09)

Commit `777fda46b77a820977a5ba616283dbfbc40bf7e1`. Environment: Docker 29.1.5 on Windows 11,
`php:7.4-apache` (PHP 7.4.33) and `mysql:5.7` (5.7.44,
`sha256:4bc6bc963e6d8443453676cae56536f4b8156d78bae03c0145cbe47c2aad73bb`).

| ID | Result | Evidence |
|---|---|---|
| R1 | Pass | Pinned in `PINS.md`. No licence is declared. |
| R2 | Pass | 23 root PHP scripts, 3,367 lines. No framework. 23 hardcoded `mysqli_connect("localhost","root","","myhmsdb")` calls. Inline SQL built by string interpolation. `$_SESSION` used in 9 scripts. 6 tables. |
| R3 | Pass | `myhmsdb.sql` loaded with no error. Row counts equal the dump: `admintb` 1, `appointmenttb` 12, `contact` 9, `doctb` 8, `patreg` 11, `prestb` 4. |
| R4 | Pass | `index.php`, `index1.php`, `contact.html`, `services.html`, `admin-panel.php`, `admin-panel1.php`, `doctor-panel.php`, `patientsearch.php` all returned 200. No fatal or parse error logged. |
| R5 | Pass | Login as seed patient `ram@gmail.com` through `func.php` returned 302 to `admin-panel.php`, which then rendered "Welcome Ram Kumar". `patientsearch.php` returned the seed row for contact `9876543210`. `admin-panel1.php` contained all 11 seed patient emails and all 8 seed doctor emails. |
| R6 | Pass | POST to `contact.php` raised the `contact` row count from 9 to 10, and the inserted row was read back by query. |
| R7 | Pass | No `$.ajax`, `XMLHttpRequest` or `fetch` in the app's own PHP or HTML. |

After the test the database was recreated from the dump and the six row counts re-verified.
The clone was confirmed unmodified.

## Observations that shape the engine

- **Output buffering.** With the base image's empty PHP configuration, login failed with
  `session_start(): Cannot start session when headers already sent`, because scripts emit
  HTML before starting the session. Activating PHP's shipped `php.ini-production`, which
  turns output buffering on as XAMPP does, resolved it. This is environment configuration
  that applies to every benchmark, not a change to the application.
- **Database host.** The hardcoded `localhost` is satisfied by sharing the database
  container's Unix socket with the web container, so no connection string is edited.
- **Primary keys.** Only `appointmenttb` and `patreg` have one. Row identity for the other
  four tables must be whole-row content.
- **Write responses carry little data.** `contact.php` answers a successful insert with a
  JavaScript `alert` and a client-side redirect, with status 200. The database delta is the
  main evidence for write endpoints.
- **Unguarded pages.** `admin-panel1.php` rendered the full patient and doctor lists without
  a session. This is recorded as observed legacy behaviour, not judged.
- **Strict SQL mode.** MySQL 5.7 runs with `STRICT_TRANS_TABLES`; the dump was produced on
  MariaDB 10.1. Writes that omit a `NOT NULL` column may behave differently from the
  authors' machine. Whatever this environment does is the behaviour the oracle records.

## Result: WackoPicko — PASS (2026-10-18)

Commit `cabc1b3a06beda7df98e839fa2d84db9a4bdfee7`. Environment: Docker 29.1.5 on Windows 11,
`php:5.6-apache` (PHP 5.6.40, extensions `mysql` and `mysqli`) and `mysql:5.7` (5.7.44); migrated
side `mysql:8.0`.

| ID | Result | Evidence |
|---|---|---|
| R1 | Pass | Pinned in `PINS.md`. MIT licence. |
| R2 | Pass | 49 PHP files, no framework, 84 calls to the `mysql_*` functions with SQL built by string interpolation. Session-based login. 13 tables. The SQL sits in a few shared include files rather than in each page. |
| R3 | Pass | `current.sql` loaded with no error once its account statements were separated (see below). 13 tables; `pictures` 9, `users` 10, `comments` 4, `guestbook` 1, and so on. Both databases hold identical data, and repeated resets restore an identical state on both sides. |
| R4 | Pass | `/`, `users/login.php`, `users/register.php`, `guestbook.php`, `pictures/recent.php`, `calendar.php`, `admin/index.php?page=login`, `users/sample.php?userid=1` all returned 200. No fatal or parse error logged. `admin/index.php` without its `page` parameter answers 500 by design. |
| R5 | Pass | Login as seed user `bryce` through `users/login.php` returned 303 to `users/home.php`, which shows the user's name. |
| R6 | Pass | A POST to `guestbook.php` inserted one row into `guestbook`, read back by query. |
| R7 | Pass | No `XMLHttpRequest`, `$.ajax` or `fetch` in the application's own pages or scripts. |

The clone was confirmed unmodified after the test.

## Observations from WackoPicko that shape the engine

- **The PHP version is a property of the benchmark.** The `mysql_*` functions were removed in
  PHP 7.0, so the application runs only on PHP 5. The legacy image takes its PHP version and
  extensions from the benchmark descriptor; this is configuration, not repair.
- **The seed dump sets up a database user.** Its first statements are `CREATE USER` and two
  `GRANT`s. MySQL 8 rejects that `GRANT` form, so the migrated database could not load the dump,
  and the statements fail when a reset reloads them. The engine therefore splits account
  statements from data: the legacy database runs them once, when it is created; every load and
  reset uses the data alone. HMS's dump has no such statements and is loaded byte for byte as
  before.
- **Logging in writes to the database.** It sets the user's `last_login_on` to the current time.
  The login therefore happens before the "before" snapshot of a scenario.
- **Writes store the current time.** The guestbook stores `created_on = NOW()`, so a database
  delta differs between two captures of the same scenario. Capture currently refuses such a
  scenario.
- **Most data is outside tables.** The guestbook, the recent pictures and a user's page show
  their data without any HTML table. The only tables found lay out the login and registration
  forms.
