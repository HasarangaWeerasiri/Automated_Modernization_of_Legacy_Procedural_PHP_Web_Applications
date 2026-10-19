# Group UI — run a component on a PHP app and see what it did

A local web UI for the research project: press **Migrate**, watch the component run live, then look at its results.
Component 2 (frontend, PHP → Next.js) is connected; Components 1, 3 and 4 connect the same way (below).

It is a **demonstration tool, not part of any component's pipeline and not a measurement**: every number it shows is
read from a component's own output files.

## Stack (pinned)

| Part | What | Version |
|---|---|---|
| Server | Python + FastAPI + Uvicorn (`ui/server/`) | Python 3.12, fastapi 0.115.6, uvicorn 0.32.1 |
| Live log | Server-Sent Events (`/api/runs/{id}/events`) | — |
| Run history | SQLite (`ui/runs/runs.db`) + one folder per run (`ui/runs/<id>/`: `log.txt`, `out/`) | — |
| Web | React + TypeScript + Vite (`ui/web/`), plain CSS with design tokens | react 18.3.1, typescript 5.6.3, vite 5.4.11 |

A component is run as a **separate process** (its own command and its own environment), so it may be written in any
language and nobody imports anyone else's code.

## Start it (Windows, from the repository root)

```powershell
ui\start.ps1
```

then open <http://127.0.0.1:8000>. The first time, the script creates `ui\.venv`, installs the server's requirements,
runs `npm ci` and builds the web app. Only `127.0.0.1` is used: do not expose it to a network.

While working on the web screens: `cd ui\web; npm run dev` (port 5173, `/api` goes to the server on 8000).

## The contract: how a component connects

Every component provides **one command**:

```
<command> --app <app> --out <folder>
```

| Rule | Meaning |
|---|---|
| exit code | `0` = ok, anything else = failed |
| stdout | log lines, shown live; a line `::step <name>` starts a step (e.g. `::step harness`, `::step build`) |
| `<folder>/summary.json` | what the UI shows: at least `{"component": "...", "app": "...", "ok": true/false}`, plus the component's own counts and file lists. Write it to a temporary name and rename it, so the UI never reads half a file |
| other files | anything else under `<folder>/`; the UI reads them through `GET /api/runs/{id}/file?path=...` |

Then add the component to `ui/config.json`:

```json
"c3": {
  "name": "Component 3 · Data layer",
  "connected": true,
  "cwd": "components/c3_data_layer",
  "command": ["components/c3_data_layer/.venv/Scripts/python.exe", "-m", "c3.ui", "--app", "{app}", "--out", "{out}"],
  "options": [{"id": "strict", "flag": "--strict", "default": false, "label": "Strict mode", "help": "…"}],
  "apps": ["hms"],
  "sourceRoot": "path/to/{app}"
}
```

`{app}` and `{out}` are filled in by the server; a checked option adds its `flag`. `sourceRoot` (optional) lets the UI
show the app's source files (`GET /api/runs/{id}/source?path=...`). `serve` (optional) starts what the component
produced (Component 2 starts the generated Next.js app). The component's result screens go in `ui/web/src/<id>/`
(Component 2's are in `ui/web/src/c2/`) and are added to the sidebar in `ui/web/src/App.tsx`.

## Rules for every member

1. **Only listed apps run.** `ui/config.json` lists the apps; the server refuses any other. Until the evaluation's
   gold standard is committed, list **discovery apps only**: never put an evaluation app there.
2. **Never measure through the UI.** Evaluation results come from each component's own evaluation scripts. The UI
   shows a run; it is not a result.
3. **No invented numbers.** A screen shows what the component wrote. If a value does not exist, the screen says so.
4. **Do not change your component for the UI.** Add a small command that calls it (Component 2:
   `component2_frontend/tools/ui_cli.py`) and leave the pipeline as it is.

## Component 2 in the UI

- **Migrate**: runs `component2_frontend/tools/ui_cli.py`: the C2 test harness (a stand-in for Member 01's core),
  Stages 1–5, the Next.js assembly, `next build` (`component2_frontend/output/test-app/c2-build/ui-<app>/`) and,
  optionally, the replay data (a Phase 4 test fixture standing in for Component 1) and the opt-in deployment layers.
  HMS takes about 30 seconds.
- **Overview**: units, components, C2Todos, crashes, compile result, and the todo / review / info counts.
- **Component Explorer**: the PHP source, the Stage 2 component tree with each node's rule (R-L1, R-I4, …), status
  and reason, the lines the component reads, its Stage 4 data result, and the generated React code.
- **Flags & C2Todos**: every C2Todo and flag with its generated-file line and PHP line.
- **Migrated App**: starts the generated app (and the replay API) and shows it.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/config` | apps and components |
| GET / POST | `/api/runs` | list runs / start one: `{"component": "c2", "app": "hms", "options": {...}}` (one run at a time) |
| GET | `/api/runs/{id}` | a run, with its `summary.json` |
| GET | `/api/runs/{id}/events` | the live log (Server-Sent Events), then `event: end` |
| GET | `/api/runs/{id}/file?path=` / `/source?path=` | an output file / a source file (inside the run's folder / the `sourceRoot` only) |
| POST / GET | `/api/runs/{id}/serve`, `/api/serve`, `/api/serve/stop` | start / check / stop what a run serves |
