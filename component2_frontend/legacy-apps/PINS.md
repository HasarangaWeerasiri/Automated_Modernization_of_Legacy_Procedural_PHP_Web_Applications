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

Clone WackoPicko with `--config core.autocrlf=false` so the working files stay byte-identical
to upstream. The HMS working copy has its database connection strings edited for Docker; read
the upstream bytes with `git -C legacy-apps/hms show HEAD:<file>`.
