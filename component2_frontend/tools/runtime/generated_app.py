"""Generated side of the Phase 4 runtime check: an app's Stages 1-5 with the substitute replay contract and
endpoint map (tools/runtime/replay/), then `next build` of the assembled Next.js app.

Writes output/runtime/<app>/pipeline/ (the stage outputs, as tools/harness/run_apps.py does) and builds the app
as its own project, output/test-app/c2-build/<app>-runtime/ (so the normal whole-app build is untouched).
Prints how many Lists got replay data (Stage 4 `mapped`) and why the others did not.

Usage, from component2_frontend/ (after tools/runtime/replay/generate.py <app>):
    venv/Scripts/python.exe tools/runtime/generated_app.py hms [--rules v1.0] [--folder hms-v1.0]
(`--rules`: the boundary rules version, default the config's (v1.1); it is recorded in lists.json. `--no-data`: no
substitute contract / map, built as output/test-app/c2-build/<app>-nodata-runtime: the same rules without the data
wiring, the baseline classify.py --before takes to tell List rows from static elements.)
then start it:
    API_BASE_URL=http://127.0.0.1:18090 node output/test-app/node_modules/next/dist/bin/next start \\
        output/test-app/c2-build/hms-runtime -p 18100
"""

import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # component2_frontend/
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import compile_check  # noqa: E402
from app import app_timelines  # noqa: E402
from run_apps import NEXT_APP, run_app  # noqa: E402

sys.path.insert(0, str(ROOT))
from src.mapping.boundary_config import load_boundary_config  # noqa: E402

OUTPUT = ROOT / "output" / "runtime"


def lists(folder: Path) -> list[dict]:
    """Every List of every unit: its unit, its Stage 4 collection result and reason."""
    found = []
    for unit in sorted(p for p in folder.iterdir() if (p / "stage4.json").exists()):
        report = json.loads((unit / "stage4.json").read_text(encoding="utf-8"))
        for collection in report["collections"]:
            found.append({"unit": unit.name, "component": collection["componentId"],
                          "queryNodeId": collection["need"]["queryNodeId"], "result": collection["result"],
                          "reason": collection["reason"], "endpoint": (report["endpoint"] or {}).get("path")})
    return found


def main(argv: list[str]) -> int:
    named = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or not argv[i - 1].startswith("--"))]
    if len(named) != 1:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    app = named[0]
    rules = load_boundary_config(rules=argv[argv.index("--rules") + 1] if "--rules" in argv else None).rules_version
    folder = argv[argv.index("--folder") + 1] if "--folder" in argv else app
    replay = HERE / "replay" / "build" / app
    pipeline = OUTPUT / folder / "pipeline"
    data = {} if "--no-data" in argv else {"contract_path": replay / "contract.json",
                                           "endpoint_map_path": replay / "endpoint_map.json"}
    index = run_app(app_timelines(app, rules), pipeline, tsc=True, build=False, rules=rules, **data)
    crashes = [p["key"] for p in index["pages"] if p["crash"]]
    failing = [p["key"] for p in index["pages"] if p["tsc"] not in (0, None)]
    ok, text = compile_check.next_build(f"{app}-{'nodata-' if not data else ''}runtime", pipeline / app / NEXT_APP)
    found = lists(pipeline / app)
    summary = collections.Counter((f["result"], f["reason"]) for f in found)
    report = {"app": app, "rules": rules, "crashes": crashes, "tscFailing": failing, "nextBuild": ok, "lists": found,
              "summary": {f"{r}:{why}": n for (r, why), n in sorted(summary.items(), key=str)}}
    (OUTPUT / folder / "lists.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{app} (rules v{rules}): {len(crashes)} crashes, {len(failing)} units not compiling, next build {'ok' if ok else 'FAILED'}")
    print(f"  Lists: {len(found)} — " + ", ".join(f"{k} {v}" for k, v in report["summary"].items()))
    if not ok:
        print(text)
    return 0 if ok and not crashes else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
