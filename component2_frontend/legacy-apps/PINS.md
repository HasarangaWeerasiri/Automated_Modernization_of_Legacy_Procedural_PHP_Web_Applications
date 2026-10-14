# Pinned legacy apps

The app folders under `legacy-apps/` are git-ignored clones. This file records the exact
commit each one must be checked out at; mock timelines are byte-exact against these commits.

| App | Folder | Repository | Commit | Licence | Document root | Notes |
|---|---|---|---|---|---|---|
| HMS | `legacy-apps/hms` | `kishan0725/Hospital-Management-System` | `777fda46b77a820977a5ba616283dbfbc40bf7e1` | — | the repository root | Rule-discovery app. Libraries `TCPDF/`, `vendor/` are not read |
| WackoPicko | `legacy-apps/wackopicko` | `adamdoupe/WackoPicko` | `cabc1b3a06beda7df98e839fa2d84db9a4bdfee7` | MIT | `website/` | Rule-discovery app. All its PHP is under `website/` |
| attendance | `legacy-apps/attendance` | `ihumaunkabir/online-attendance-management-system-1.0` | `31781040c50f2be20514ef18c1b0d4ae7b2bf76e` | MIT (its `LICENSE` file stays with it) | the repository root | Rule-discovery app, opened 14 Oct 2026 (roadmap Phase 3, app 4). PHP 5 (`mysql_*`): parsed only, never run. Schema: `database/attsystem.sql` |
| phpBlog | `legacy-apps/phpblog` | `codeplay-code/phpBlog-blogScript` | `4ffe3c9641a42b99829cf4b8e4185093793a3abc` | GPL-3.0 (its `LICENSE` file stays with it) | the repository root | Rule-discovery app, opened 13 Oct 2026 (roadmap Phase 3, app 1). Schema: `install/database.sql`. `config.php` is not in the repository: the installer (`install/`) writes it from `install/config.tpl`. The installer is parsed like any other page and never run |

## Analysis substitutions (owner decision, 13 Oct 2026)

A file that an app needs but that is **not in its repository** is analysed from another file, only where the
evidence below shows what the missing file holds. The harness reads the list from `APPS[...]["substitutions"]`
in `tools/harness/app.py`; a timeline made from a substituted file says so in `_provenance.analysisSubstitution`.
No other substitution is made without asking the owner.

| App | Missing file | Analysed from | Evidence |
|---|---|---|---|
| phpBlog | `config.php` | `install/config.tpl` | `install/done.php` 49–53: the installer reads the template and writes it as `config.php` after `str_replace` of the `<DB_HOST>`, `<DB_NAME>`, `<DB_USER>`, `<DB_PASSWORD>` placeholders, which stand only inside string literals (`config.tpl` 2–5). Its code is therefore the template's code |
| phpBlog | `config_settings.php` | itself (it is in the repository) | `install/done.php` 22 rewrites it as `'<?php $settings = ' . var_export($settings, true) . '; ?>'`: one assignment, no output. It is a **no-output include** whatever the installer wrote |

Note: `config.tpl` 10–13 prints through `printf("Database connection failed: ...")` and exits when the
connection fails. `printf` is not an output kind of the timeline, so an include of `config.php` *may print*
(`outputsInside: true`) although the file has no timeline of its own.

The **document root** is the folder the web server serves. A generated page's route is its path from
there, without `.php`; `index.php` is its directory (generation-spec v0.7 section 8a). The harness reads
it from `APPS[...]["docroot"]` in `tools/harness/app.py`, which must say the same as this table.

To restore a clone:

```
git clone https://github.com/<repository>.git legacy-apps/<folder>
git -C legacy-apps/<folder> checkout <commit>
```

## Real analysis outputs (from Member 01)

Files under `legacy-apps/real/` are Member 01's own outputs, kept with unchanged bytes. They are
committed (unlike the app clones).

| File | What | Source | Facts |
|---|---|---|---|
| `legacy-apps/real/f001_admin-panel1_php_ast.json` | AST, schema 1.0, from Member 01 (received 12 Oct 2026) | HMS `admin-panel1.php` @ `777fda4` | 602 nodes; 171,166 bytes; the file's own sha256 is `da42837a1892721cf1db626e1102c505eb3e6e741e490f558918744d6faddb67`; the `sha256` in its header is that of the source, `492d63d991bcb083e9f08769f10e08cd53edf3844ec437b8ba9e9a1dc6e8b3e0` (`admin-panel1.php` at that commit, 25,408 bytes) |

`tests/test_real_ast.py` loads it, checks every `loc` against the source bytes and compares the
harness's AST with it node by node and byte by byte; those tests skip if the file is absent.

Clone WackoPicko, phpBlog and attendance with `--config core.autocrlf=false` so the working files stay
byte-identical to upstream. Do not modify an app's files. The HMS working copy has its database connection strings edited for Docker; read
the upstream bytes with `git -C legacy-apps/hms show HEAD:<file>`.
