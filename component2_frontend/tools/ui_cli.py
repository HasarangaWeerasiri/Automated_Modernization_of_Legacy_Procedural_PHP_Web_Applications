"""Component 2's command for the group UI (ui/ at the repository root): migrate one app's PHP frontend to Next.js.

The UI contract every component follows:
    <cli> --app <app> --out <folder>
      exit 0 = ok, 1 = failed; stdout = log lines (the UI shows them live; a line "::step <name>" starts a step);
      <folder>/summary.json = what the UI shows.

What it runs is the pipeline as it is (the harness, Stages 1-5, the Next.js assembly), then `next build`, then,
with --layers, the opt-in deployment layers (routing, assets, inline JS). It never changes the pipeline: progress
lines come from a wrapper around run_apps.run_stages at run time, not from an edit of it. A deployment aid, not
measured; only the discovery apps (tools/harness/app.py APPS) can be run; never an evaluation app.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/ui_cli.py --app hms --out <folder> [--rules v1.0|v1.1] [--data] [--layers] [--no-tsc]
(`--data`: the Phase 4 substitute replay contract and endpoint map (a test fixture standing in for Component 1),
where tools/runtime/replay/build/<app>/ exists. Build project: output/test-app/c2-build/ui-<app>/.)
"""

import collections
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # component2_frontend/
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "routing"))
sys.path.insert(0, str(ROOT / "tools" / "assets"))
sys.path.insert(0, str(ROOT))
import compile_check  # noqa: E402
import legacy_assets  # noqa: E402
import routes  # noqa: E402
import run_apps  # noqa: E402
from app import APPS, app_timelines  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402

REPLAY = ROOT / "tools" / "runtime" / "replay" / "build"


def say(line: str) -> None:
    print(line, flush=True)


def step(name: str) -> None:
    say(f"::step {name}")


def arg(argv: list[str], name: str, default: str | None = None) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else default


def page_summary(folder: Path, entry: dict) -> dict:
    """One unit's numbers from its own stage files."""
    unit = folder / entry["key"]
    row = {k: entry.get(k) for k in ("key", "path", "role", "route", "group", "component", "function")}
    row |= {"crash": entry.get("crash"), "tsc": entry.get("tsc"), "notGenerated": entry.get("notGenerated")}
    if (unit / "stage5.json").exists():
        s5 = json.loads((unit / "stage5.json").read_text(encoding="utf-8"))
        s2 = json.loads((unit / "stage2.json").read_text(encoding="utf-8"))
        row |= {"components": [c["name"] if isinstance(c, dict) else c for c in s5["components"]],
                "c2todos": len(s5["c2todos"]), "severity": s5["counts"].get("severity", {}),
                "stage2": s2["counts"], "files": s5["files"]}
    return row


def main(argv: list[str]) -> int:
    app, out = arg(argv, "--app"), arg(argv, "--out")
    if not app or not out:
        print(next(line.strip() for line in __doc__.splitlines() if "ui_cli.py --app" in line), file=sys.stderr)
        return 2
    if app not in APPS:
        say(f"{app}: not a discovery app (allowed: {', '.join(sorted(APPS))})")
        return 2
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rules = load_boundary_config(rules=arg(argv, "--rules")).rules_version
    data = "--data" in argv and (REPLAY / app / "contract.json").exists()
    build_name = f"ui-{app}"
    started = time.time()
    summary = {"component": "c2", "app": app, "rules": rules, "ok": False, "data": data,
               "layers": "--layers" in argv, "input": "C2 test harness (stand-in for Member 01's core)"}

    def write() -> None:
        summary["seconds"] = round(time.time() - started, 1)
        partial = out / "summary.json.part"  # then a rename: a reader never sees half a file
        partial.write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
        partial.replace(out / "summary.json")

    step("harness")
    say(f"reading {APPS[app]['prefix']} (rules v{rules})")
    timelines = app_timelines(app, rules)
    total = len(timelines.pages)
    say(f"{total} timelines ({len([p for p in timelines.pages if p.function is None])} files, "
        f"{len([p for p in timelines.pages if p.function is not None])} functions)")

    step("stages")
    done = collections.Counter()
    original = run_apps.run_stages

    def traced(timeline, folder, *args, **kwargs):
        done["n"] += 1
        say(f"[{done['n']}] Stages 1-5: {folder.name}")
        return original(timeline, folder, *args, **kwargs)

    run_apps.run_stages = traced
    try:
        extra = ({"contract_path": REPLAY / app / "contract.json",
                  "endpoint_map_path": REPLAY / app / "endpoint_map.json"} if data else {})
        index = run_apps.run_app(timelines, out / "pipeline", tsc="--no-tsc" not in argv, build=False, rules=rules,
                                 **extra)
    finally:
        run_apps.run_stages = original
    folder = out / "pipeline" / app
    pages = [page_summary(folder, p) for p in index["pages"]]
    crashes = [p["key"] for p in pages if p["crash"]]
    failing = [p["key"] for p in pages if p["tsc"] not in (0, None)]
    say(f"{len(pages)} units: {len(crashes)} crashes, {len(failing)} not compiling")
    severity = collections.Counter()
    for p in pages:
        severity.update(p.get("severity", {}))
    summary |= {"pipeline": folder.relative_to(out).as_posix(), "units": len(pages), "crashes": crashes,
                "notCompiling": failing, "tscRan": "--no-tsc" not in argv,
                "pages": pages, "severity": dict(sorted(severity.items())),
                "notGenerated": index["notGenerated"], "shells": index["shells"],
                "components": sum(len(p.get("components", [])) for p in pages if p["role"] in ("page", "component", "shell")),
                "c2todos": sum(p.get("c2todos", 0) for p in pages)}
    write()

    step("build")
    say(f"next build into output/test-app/c2-build/{build_name}/ (a few minutes)")
    ok, text = compile_check.next_build(build_name, folder / run_apps.NEXT_APP)
    summary["nextBuild"] = ok
    summary["build"] = f"output/test-app/c2-build/{build_name}"
    if not ok:
        say(text)
        write()
        return 1
    say("next build ok")

    if "--layers" in argv:
        step("layers")
        table, ok, text = routes.apply(build_name, folder / "index.json", build=False)
        say(f"routing: {len(table['rewrites'])} rewrites, {len(table['redirects'])} redirects")
        table, copied, ok, text = legacy_assets.apply(build_name, folder / "index.json", inline=True)
        say(f"assets + inline JS: {legacy_assets.summary(table)}; {copied} static files")
        layers = out / "layers"  # the layers' tables, beside the run's other outputs (the UI reads only those)
        layers.mkdir(exist_ok=True)
        for name in ("routes.json", "assets.json"):
            shutil.copyfile(compile_check.BUILD_DIR / build_name / name, layers / name)
        summary["layerTables"] = {"routes": "layers/routes.json", "assets": "layers/assets.json"}
        summary["staticFiles"] = copied
        summary["nextBuild"] = ok
        if not ok:
            say(text)
            write()
            return 1

    summary["ok"] = not crashes
    write()
    step("done")
    say(f"done in {summary['seconds']} s")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
