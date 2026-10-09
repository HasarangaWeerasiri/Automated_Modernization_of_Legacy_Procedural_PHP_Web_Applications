# Pinned benchmark applications

The app folders under `benchmarks/` are git-ignored clones. This file records the exact
commit each one must be checked out at. Benchmark source and seed dumps are used under
their own terms and are not redistributed from this repository.

A benchmark is never edited. Environment differences are handled in `docker/legacy/`, and
the source is mounted read-only into the container.

| App | Folder | Repository | Commit | Commit date | Licence | Role |
|---|---|---|---|---|---|---|
| HMS | `benchmarks/hms/app` | `kishan0725/Hospital-Management-System` | `777fda46b77a820977a5ba616283dbfbc40bf7e1` | 2024-10-07 | None declared | Development |

**Role.** A *development* app is one the engine is built against. Evaluation results that
support a generality claim must come from apps the engine was not developed against.

**HMS licence.** The repository has no licence file and GitHub reports no licence
(checked 2026-10-09). Only its bundled third-party libraries carry licences. Redistribution
of its source or seed data is therefore not granted.

## Seed dumps

| App | Dump | SHA-256 |
|---|---|---|
| HMS | `benchmarks/hms/app/myhmsdb.sql` | `5ccd3134675c99fb95267898f4b18117c9a77443a53e124f4f00377e42778450` |

## Restoring a clone

```
git clone --config core.autocrlf=false https://github.com/<repository>.git benchmarks/<folder>/app
git -C benchmarks/<folder>/app checkout <commit>
```

`core.autocrlf=false` keeps the working files byte-identical to upstream on Windows.
`git -C benchmarks/<folder>/app status --short` must print nothing.
