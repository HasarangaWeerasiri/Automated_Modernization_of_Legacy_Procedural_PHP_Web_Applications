# Community Service Desk — Independent Legacy PHP Holdout

A separate **framework-free procedural PHP/MySQL** application for testing generalization of C3 data-layer migration. This is **NOT** the clinic benchmark and uses new domain tables (`teams`, `agents`, `tickets`).

## Features
Ticket queue, ticket detail, create, edit status/priority, delete, search, agents directory and team count report. PHP pages use `mysqli_query` with constructed SQL, including deliberately legacy query patterns. `legacy/db.php` contains the reusable connection and display helpers.

## Prerequisites and setup (local only)
PHP 8.x with mysqli and MySQL 8.x. Create a disposable database by importing `database/schema.sql` using a test-only MySQL account authorized to create it. Example from PowerShell, after selecting a MySQL account with suitable permissions:

```powershell
Get-Content .\database\schema.sql -Raw | mysql -u root -p
$env:HOLDOUT_DB_USER = 'root' # or restricted local test user
$env:HOLDOUT_DB_NAME = 'c3_helpdesk_holdout'
# supply HOLDOUT_DB_PASSWORD securely via Read-Host; do not paste passwords
php -S 127.0.0.1:8097 -t .\legacy
```

Browse http://127.0.0.1:8097/index.php . Import **only into your disposable local test environment**. Do not run this legacy fixture against real data or expose it to the internet: it intentionally retains string-built SQL and lacks authentication / CSRF protection to serve as migration research input.

## Held-out C3 evaluation
Copy the `helpdesk_legacy_holdout` folder under your research repository's `benchmarks/apps/`. Run the evaluation from `components/c3_data_layer`:

Use the provided `evaluate_c3_holdout.py` at this folder's root:

```powershell
python ..\..\benchmarks\apps\helpdesk_legacy_holdout\evaluate_c3_holdout.py
```

It calls the currently installed C3 mapping, recovery, conversion and generation pipeline on this new application, prints every recovered query's conversion status and records JSON and generated Python under `evaluation_output/`. It **does not** assert 100% conversion or run generated DML. Unresolved/abstained cases are valid research findings. It does not change any database.

## Test matrix
| Case | File | Intended query pattern |
|---|---|---|
| H01 | index.php | 3-table JOIN + ORDER BY |
| H02 | view.php | interpolated ID SELECT |
| H03 | agents.php | 2-table JOIN |
| H04 | create.php | POST INSERT with escaped strings |
| H05 | edit.php | POST UPDATE |
| H06 | edit.php | GET/POST SELECT |
| H07 | delete.php | POST DELETE |
| H08 | search.php | LIKE/wildcard |
| H09 | report.php | LEFT JOIN with aggregate GROUP BY |

## Research validity
A 21/21 result on the clinic benchmark does not prove universal migration. Do not use this app to tune rules and then describe it as an untouched holdout. Record initial performance, any source-specific adaptions, and before/after metrics explicitly. Conversion status alone does not verify behavioral equivalence; that requires isolated test databases and transactions.
