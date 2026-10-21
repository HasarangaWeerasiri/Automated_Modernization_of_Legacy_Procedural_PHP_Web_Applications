# Benchmark apps used by C1

| App | Pin | PHP files | In scope? | Run with |
|---|---|---|---|---|
| kishan0725/Hospital-Management-System (HMS) | 777fda4 | 33 analysed (+227 TCPDF excluded) | yes (primary) | `--exclude-dir TCPDF` |
| WackoPicko (Doupé et al.) | team pin (C2 `legacy-apps/PINS.md`) | 49 | yes, with a front controller in `admin/` | — |
| chapagain/crud-php-complete | 78e1c1d (2016-11-22) | 9 | yes | — |
| chetans9/core-php-admin-panel | 90d07ed (2026-01-31) | 21 analysed (+3 in `lib/`) | **no**: data access through the MysqliDb query builder (ORM-like, master reference §2.2) | `--exclude-dir lib` |

## Screening greps (agreed first check)

Counts are from the two grep commands in the decision log; 2026-10-17.

| App | Include lines with `$` | Front-controller files | Reading |
|---|---|---|---|
| HMS | 49 | none | Almost all of the 49 are inside TCPDF. Outside it the resolver finds 13 includes, all static |
| WackoPicko | 4 | `website/admin/index.php` | One front controller (`$_GET['page'] . '.php'`), flagged dynamic |
| crud-php-complete | 0 | none | One file per page |
| core-php-admin-panel | 6 | `helpers.php`, `admin_users.php`, `config.php` | The grep hits are `REQUEST_URI` uses, not routing. Includes resolve 49/52 (3 into the excluded lib) |

## What the pipeline finds

| | HMS | WackoPicko | crud-php-complete | core-php-admin-panel |
|---|---|---|---|---|
| Pages guarded / unguarded with data | 0 / 13 | 17 / 16 | 0 / 7 | 10 guarded (include `auth_validate.php`) |
| Broken guards (redirect, no `exit`) | 1 | 0 | **5** (`add`, `delete`, `edit`, `view`, `login`) | 1 |
| Endpoints | 19 | 25 | 8 | 0: query builder, every call flagged R19 (abstain) |

crud-php-complete is the textbook EAR case (Doupé et al. 2011). Every protected page does `if(!isset($_SESSION['valid'])) { header('Location: login.php'); }` with no `exit`, so `delete.php?id=…` still deletes for a visitor who is not logged in.
