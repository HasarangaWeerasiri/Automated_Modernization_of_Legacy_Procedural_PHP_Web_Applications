# Component 2 — Evidence pack for the report (Phase 6E)

Evidence only: numbers, tables, figures and appendices, each traceable to a committed file. The report prose is the
owner's.

| File | What | Made by |
|---|---|---|
| `facts.md` | Every number the report may quote, with its source file, the commit that last changed it, and the local result file it is cross-checked against | `venv/Scripts/python.exe tools/results_table.py report --out docs/report/facts.md` (rules: `tools/report/facts.py`) |
| `appendix-E-decisions.md` | The decision logs of boundary-rules, generation-spec, reconciliation-spec, eval-mapping, gold-standard-guide and the post-freeze log, in date order, copied | `venv/Scripts/python.exe tools/report/build.py` |
| `appendix-F-ai-use.md` | The factual AI-use record: the working method, every commit with an `AI-assisted-by:` line, the full commit record, AI-drafted artefacts | `tools/report/build.py` (commits from `git log`) |
| `limits.md` | Every known limitation already recorded in the repository, each with its source | written by hand, quoting its sources |
| `fig/pipeline.svg` | The C2 pipeline, Stages 1–5 | `tools/report/build.py` |
| `fig/render-match.svg` | Render match per rules version, HMS and phpBlog (Phase 4) | `tools/report/build.py` (values from `facts.md`'s rules) |
| `fig/saturation.svg` | New patterns per discovery app, with the 2 / 2 stop | `tools/report/build.py` |
| `fig/unsound.svg` | Unsound decisions per rules version (Phase 4 and the HMS rehearsal) | `tools/report/build.py` |

**Check:** `venv/Scripts/python.exe tools/report/check_facts.py` re-derives every number of `facts.md` from its source
(and from its result file, where that file is on the machine) and exits 1 on any mismatch.

**After committing:** the Commit column of `facts.md` names the commit that last changed each source, so regenerate
`facts.md` (and run the check) after each commit that touches a source; the values must not change.

The HMS rehearsal numbers (Phase 5R / 5R2) are a **rehearsal on a discovery app, not an evaluation result**.
