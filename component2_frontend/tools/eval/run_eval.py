"""Phase 5: run one evaluation app through the harness and Stages 1-5, with `--rules v1.0` and with `--rules v1.1`,
into separate folders (docs/eval-mapping.md section 4.2: no other setting changed, no contract / endpoint map).

Refuses to run unless the app's gold standard (`gold/<app>.csv`, or `<gold-dir>/<key>.csv` with `--key`) is committed
and nothing under the gold directory is uncommitted (section 4.1: the gold is committed before the tool runs).
`run.json` also records the code the tool ran from (Phase 5R): `codeCommit` (HEAD) and `codeChanges`, the uncommitted
changes to the pipeline (src/, config/, tools/harness/) at run time, which must be empty for a Phase 5 run. Writes `output/eval/runs/<app>/v1.0/<app>/` and `v1.1/<app>/` (as
tools/harness/run_apps.py writes an app) and `output/eval/runs/<app>/run.json`, whose `firstRun` (epoch seconds) is
set on the first run and never changed: tools/eval/score.py refuses gold committed after it.

Usage, from component2_frontend/ (the app unpacked from the owner's archive and checked against its manifest):
    venv/Scripts/python.exe tools/eval/run_eval.py <app_dir> [--name <app>] [--docroot <dir>] [--exclude vendor/,lib/]
        [--out output/eval/runs] [--gold-dir gold] [--key <name>] [--no-tsc]
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # component2_frontend/
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

from app import dir_timelines  # noqa: E402
from run_apps import run_app  # noqa: E402
from score import VERSIONS, Refused, git, gold_commit_time, source_digest  # noqa: E402

PIPELINE = ("src", "config", "tools/harness")  # the code whose version a run depends on (rules, stages, harness)


def code_state() -> dict:
    """The commit the tool runs from and the uncommitted changes to its pipeline code (Phase 5R)."""
    head = git(ROOT, "rev-parse", "HEAD").strip()
    changes = git(ROOT, "status", "--porcelain", "--untracked-files=all", "--", *PIPELINE).splitlines()
    return {"codeCommit": head, "codeChanges": [c for c in changes if c.strip()]}


def run_eval(app_dir: Path, name: str, out: Path, gold_dir: Path, docroot: str = "",
             exclude: tuple = ("vendor/",), tsc: bool = True, key: str | None = None) -> dict:
    committed = gold_commit_time(gold_dir, key or name)  # raises Refused when the gold is not committed and clean
    started = int(time.time())
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    run_file = folder / "run.json"
    previous = json.loads(run_file.read_text(encoding="utf-8")) if run_file.exists() else {}
    indexes = {}
    for version in VERSIONS:
        timelines = dir_timelines(app_dir, name, docroot, exclude, rules=version)
        indexes[version] = run_app(timelines, folder / f"v{version}", tsc=tsc, build=False, rules=version)
    run = {"app": name, "source": app_dir.as_posix(), "sourceDigest": source_digest(app_dir),
           "goldCommitted": committed, "firstRun": previous.get("firstRun", started), "lastRun": started,
           "rules": list(VERSIONS), "docroot": docroot, "exclude": list(exclude), "tsc": tsc,
           **({"key": key} if key else {}), **code_state()}
    run_file.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"run": run, "indexes": indexes}


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("--"):
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    option = lambda name, default: argv[argv.index(name) + 1] if name in argv else default  # noqa: E731
    app_dir = Path(argv[0])
    name = option("--name", app_dir.name)
    exclude = tuple(x for x in option("--exclude", "vendor/").split(",") if x)
    try:
        result = run_eval(app_dir, name, Path(option("--out", ROOT / "output" / "eval" / "runs")),
                          Path(option("--gold-dir", ROOT / "gold")), option("--docroot", ""), exclude,
                          tsc="--no-tsc" not in argv, key=option("--key", None))
    except Refused as refusal:
        print(f"refused (docs/eval-mapping.md section 4.1): {refusal}", file=sys.stderr)
        return 3
    for version, index in result["indexes"].items():
        units = [p for p in index["pages"] if p["role"] != "shell_part"]
        print(f"{name} v{version}: {len(units)} units, {sum(bool(p['crash']) for p in units)} crashes, "
              f"{sum(bool(p['tsc']) for p in units)} not compiling")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
