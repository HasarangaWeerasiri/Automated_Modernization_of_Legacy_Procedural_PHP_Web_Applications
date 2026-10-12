"""Run Stages 1-5 on every page and function timeline of the rule-discovery apps (roadmap Phase 1).

For each app this writes, under output/apps/<app>/:

  <page>/timeline.json, stage1.json ... stage5.json, generated/   one folder per page or function
  index.json                                                      every page with its node count, any
                                                                  crash, and its tsc error count

No labels file exists for whole apps, so every node is unlabelled (CONTEXT hard rule 3); no contract
or endpoint map exists, so every page is no_endpoint. A page that makes a stage raise is recorded
as a crash, with the error, and the run goes on: a crash is a bug to fix, not a reason to stop.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/harness/run_apps.py            # hms and wackopicko
    venv/Scripts/python.exe tools/harness/run_apps.py hms --no-tsc
"""

import json
import shutil
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import compile_check  # noqa: E402
from app import APPS, AppTimelines, app_timelines, dump_timeline, unavailable  # noqa: E402
from src.mapping import loader  # noqa: E402
from src.mapping import stage1_isolation as stage1  # noqa: E402
from src.mapping import stage2_boundaries as stage2  # noqa: E402
from src.mapping import stage3_requirements as stage3  # noqa: E402
from src.mapping import stage4_reconcile as stage4  # noqa: E402
from src.mapping import stage5_generate as stage5  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.generation_config import load_generation_config  # noqa: E402

OUTPUT = ROOT / "output" / "apps"
EMPTY_CONTRACT = HERE / "empty_contract.json"


def run_page(timeline_path: Path, folder: Path, config, generation_config, contract) -> dict:
    """Stages 1-5 for one timeline file, each stage's JSON written into `folder`. Raises on a stage failure."""
    timeline = loader.load_timeline(timeline_path)
    result1 = stage1.isolate_presentation(timeline, {})  # no labels: every node is unlabelled
    stage1.write_result(result1, folder / "stage1.json")
    tree = stage2.infer_boundaries(result1, timeline, config)
    stage2.write_tree(tree, folder / "stage2.json")
    requirements = stage3.recover_requirements(tree, timeline)
    stage3.write_requirements(requirements, folder / "stage3.json")
    report = stage4.reconcile(requirements, contract, {})  # no endpoint map: every page is no_endpoint
    stage4.write_report(report, folder / "stage4.json")
    result = stage5.generate(tree, requirements, report, contract, timeline, config, generation_config)
    generated = folder / "generated"
    inside = generated.resolve().is_relative_to(ROOT)  # an output folder outside the repo is named in full
    relative = generated.resolve().relative_to(ROOT).as_posix() if inside else generated.as_posix()
    stage5.write_report(result, folder / "stage5.json", relative)
    stage5.write_files(result, folder / "generated")
    return {"nodes": len(timeline.sequence)}


def run_app(timelines: AppTimelines, output: Path = OUTPUT, tsc: bool = True) -> dict:
    """Run every page of an app; returns (and writes) the app's index."""
    folder = output / timelines.app
    if folder.exists():
        shutil.rmtree(folder)
    config, generation_config = load_boundary_config(), load_generation_config()
    contract = loader.load_contract(EMPTY_CONTRACT)
    pages, generated = [], {}
    for page in timelines.pages:
        page_folder = folder / page.key
        page_folder.mkdir(parents=True)
        with (page_folder / "timeline.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(dump_timeline(page.timeline))
        entry = {"key": page.key, "path": page.path, "function": page.function,
                 "entrypoint": page.timeline["entrypoint"], "nodes": len(page.timeline["sequence"]), "crash": None,
                 "tsc": None}
        try:
            run_page(page_folder / "timeline.json", page_folder, config, generation_config, contract)
            generated[page.key] = page_folder / "generated"
        except Exception as error:  # noqa: BLE001 - a crash on any page is recorded, then reported as a bug
            lines = traceback.format_exc().strip().splitlines()
            where = next((ln.strip() for ln in reversed(lines) if ln.strip().startswith("File ")), "")
            entry["crash"] = {"error": f"{type(error).__name__}: {error}", "where": where}
        pages.append(entry)
    tsc_output = None
    if tsc and generated and compile_check.unavailable() is None:
        errors, tsc_output = compile_check.check_many(f"apps_{timelines.app}", generated)
        for entry in pages:
            if entry["key"] in errors:
                entry["tsc"] = errors[entry["key"]]
        if errors.get(""):
            tsc_output = f"{errors['']} error(s) not attributed to a page\n{tsc_output}"
    index = {
        "app": timelines.app, "sourceCommit": APPS[timelines.app]["commit"], "files": timelines.files,
        "pages": pages, "silentFiles": list(timelines.silent), "parseErrors": timelines.parse_errors,
        "tscRan": tsc_output is not None, "tscOutput": tsc_output or "",
    }
    with (folder / "index.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return index


def main(argv: list[str]) -> int:
    reason = unavailable()
    if reason:
        print(f"harness cannot run: {reason}")
        return 1
    tsc = "--no-tsc" not in argv
    apps = [a for a in argv if not a.startswith("--")] or list(APPS)
    failed = 0
    for app in apps:
        index = run_app(app_timelines(app), tsc=tsc)
        crashes = [p for p in index["pages"] if p["crash"]]
        not_compiling = [p for p in index["pages"] if p["tsc"]]
        print(f"{app}: {index['files']} files, {len(index['pages'])} timelines, {len(index['silentFiles'])} files "
              f"with no top-level output, {len(index['parseErrors'])} parse errors, {len(crashes)} crashes, "
              f"{len(not_compiling)} not compiling" + ("" if index["tscRan"] else " (tsc not run)"))
        for page in crashes:
            print(f"  CRASH {page['key']}: {page['crash']['error']}\n        {page['crash']['where']}")
        for page in not_compiling:
            print(f"  TSC   {page['key']}: {page['tsc']} errors")
        failed += len(crashes)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
