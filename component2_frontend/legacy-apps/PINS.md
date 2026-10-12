# Pinned legacy apps

The app folders under `legacy-apps/` are git-ignored clones. This file records the exact
commit each one must be checked out at; mock timelines are byte-exact against these commits.

| App | Folder | Repository | Commit | Notes |
|---|---|---|---|---|
| HMS | `legacy-apps/hms` | `kishan0725/Hospital-Management-System` | `777fda46b77a820977a5ba616283dbfbc40bf7e1` | Rule-discovery app |
| WackoPicko | `legacy-apps/wackopicko` | `adamdoupe/WackoPicko` | `cabc1b3a06beda7df98e839fa2d84db9a4bdfee7` | Rule-discovery app. MIT. PHP under `website/` |

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

Clone WackoPicko with `--config core.autocrlf=false` so the working files stay byte-identical
to upstream. The HMS working copy has its database connection strings edited for Docker; read
the upstream bytes with `git -C legacy-apps/hms show HEAD:<file>`.
