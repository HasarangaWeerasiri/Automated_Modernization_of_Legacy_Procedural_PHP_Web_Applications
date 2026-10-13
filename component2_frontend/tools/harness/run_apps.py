"""Run Stages 1-5 on the rule-discovery apps, each as one whole Next.js app (roadmap Phases 1 and 2b).

An app's timelines are not independent: a page calls functions, and a header / footer pair wraps
pages (boundary-rules section 4). So an app is run as a set of units:

  page         a file's top-level output -> app/(<group>)/<path>/page.tsx
  component    a function that is called and is tag-balanced (R-F1) -> components/<Name>.tsx
  shell        a header / footer pair (R-F2) -> components/<H>Shell.tsx + app/(<h>)/layout.tsx
  not called   a function with output that no page calls (or that is not a component): run on its
               own so it is still checked, but it is not part of the app

For each app this writes, under output/apps/<app>/:

  <unit>/timeline.json, stage1.json ... stage5.json, generated/   one folder per unit
  next-app/                                                       every unit's files as one app
  index.json                                                      every unit, any crash, its tsc errors

No labels file exists for whole apps, so every node is unlabelled (CONTEXT hard rule 3); no contract
or endpoint map exists, so every page is no_endpoint. A unit that makes a stage raise is recorded
as a crash, with the error, and the run goes on: a crash is a bug to fix, not a reason to stop.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/harness/run_apps.py            # hms and wackopicko
    venv/Scripts/python.exe tools/harness/run_apps.py hms --no-tsc
    venv/Scripts/python.exe tools/harness/run_apps.py --next-build   # also `next build` each app once
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
from app import APPS, AppTimelines, app_timelines, dump_timeline, page_key, unavailable  # noqa: E402
from src.mapping import app_units, loader  # noqa: E402
from src.mapping import stage1_isolation as stage1  # noqa: E402
from src.mapping import stage2_boundaries as stage2  # noqa: E402
from src.mapping import stage3_requirements as stage3  # noqa: E402
from src.mapping import stage4_reconcile as stage4  # noqa: E402
from src.mapping import stage5_generate as stage5  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.generation_config import load_generation_config  # noqa: E402

OUTPUT = ROOT / "output" / "apps"
EMPTY_CONTRACT = HERE / "empty_contract.json"
PLAIN_GROUP = "(plain)"
NEXT_APP = "next-app"


def run_stages(timeline, folder: Path, config, generation_config, contract, units=None, layout=None):
    """Stages 1-5 for one loaded timeline, each stage's JSON written into `folder`. Raises on a stage failure.

    `layout` may be a function of the Stage 2 tree: a page's route group is known only once Stage 2 has said
    whether the page stands inside a shell."""
    result1 = stage1.isolate_presentation(timeline, {})  # no labels: every node is unlabelled
    stage1.write_result(result1, folder / "stage1.json")
    tree = stage2.infer_boundaries(result1, timeline, config, units)
    stage2.write_tree(tree, folder / "stage2.json")
    requirements = stage3.recover_requirements(tree, timeline)
    stage3.write_requirements(requirements, folder / "stage3.json")
    report = stage4.reconcile(requirements, contract, {})  # no endpoint map: every page is no_endpoint
    stage4.write_report(report, folder / "stage4.json")
    if callable(layout):
        layout = layout(tree)
    result = stage5.generate(tree, requirements, report, contract, timeline, config, generation_config, units, layout)
    generated = folder / "generated"
    inside = generated.resolve().is_relative_to(ROOT)  # an output folder outside the repo is named in full
    relative = generated.resolve().relative_to(ROOT).as_posix() if inside else generated.as_posix()
    stage5.write_report(result, folder / "stage5.json", relative)
    stage5.write_files(result, generated)
    return tree, result


def run_page(timeline_path: Path, folder: Path, config, generation_config, contract) -> dict:
    """Stages 1-5 for one timeline file on its own (no app around it), as for a mock."""
    timeline = loader.load_timeline(timeline_path)
    run_stages(timeline, folder, config, generation_config, contract)
    return {"nodes": len(timeline.sequence)}


def _shell_document(header: dict, footer: dict) -> dict:
    """The header's and the footer's harness timelines as one document, for the reports."""
    return {
        "entrypoint": f"{header['entrypoint']}+{footer['function']['name']}",
        "schemaVersion": header["schemaVersion"],
        "sequence": header["sequence"] + footer["sequence"],
        "queries": {**header["queries"], **footer["queries"]},
        "callNames": {**header["callNames"], **footer["callNames"]},
        "includes": header["includes"] + footer["includes"],
        "harnessNotes": header["harnessNotes"] + footer["harnessNotes"],
        "_provenance": header["_provenance"] | {"function": None, "shell": {
            "header": header["entrypoint"], "footer": footer["entrypoint"],
            "note": "R-F2: one unit; the page stands between the two sequences"}},
    }


def run_app(timelines: AppTimelines, output: Path = OUTPUT, tsc: bool = True, build: bool = False) -> dict:
    """Run every unit of an app and assemble one Next.js app; returns (and writes) the app's index."""
    folder = output / timelines.app
    if folder.exists():
        shutil.rmtree(folder)
    config, generation_config = load_boundary_config(), load_generation_config()
    contract = loader.load_contract(EMPTY_CONTRACT)

    loaded, raw = {}, {}
    for page in timelines.pages:
        page_folder = folder / page.key
        page_folder.mkdir(parents=True)
        with (page_folder / "timeline.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(dump_timeline(page.timeline))
        loaded[page.key] = loader.load_timeline(page_folder / "timeline.json")
        raw[page.key] = page.timeline
    units = app_units.analyse(list(loaded.values()), config)
    key_of = {t.function.node_id: key for key, t in loaded.items() if t.function is not None}

    # ---- the units to run: (key, path, function label, role, timeline, layout, units or None)
    plan = []
    for page in timelines.pages:
        timeline = loaded[page.key]
        base = {"key": page.key, "path": page.path, "function": page.function,
                "entrypoint": page.timeline["entrypoint"]}
        if timeline.function is None:
            route = page.path.removesuffix(".php")

            def layout(tree, route=route):
                group = units.shells[tree.root.shell.header].group if tree.root.shell else PLAIN_GROUP
                return stage5.UnitLayout(mode="page", route=f"{group}/{route}", stem=app_units.pascal(route))

            plan.append((base | {"role": "page"}, timeline, layout, units))
            continue
        unit = units.functions[timeline.function.node_id]
        if unit.node_id in units.shell_functions:
            shell = units.shells.get(unit.node_id) or units.shell_of_footer(unit.node_id)
            plan.append((base | {"role": "shell_part", "partOf": shell.name}, None, None, None))
        elif unit.component is not None:
            layout = stage5.UnitLayout(mode="component", name=unit.component, stem=unit.component, props=unit.props)
            plan.append((base | {"role": "component", "component": unit.component}, timeline, layout, units))
        else:  # never called, or not a component: still run, on its own, but it is not in the app
            plan.append((base | {"role": "not_called"}, timeline, None, None))
    for shell in units.shells.values():
        header_key, footer_key = key_of[shell.header], key_of[shell.footer]
        header_page = next(p for p in timelines.pages if p.key == header_key)
        label = f"{raw[header_key]['function']['name']}+{raw[footer_key]['function']['name']}"
        key = page_key(header_page.path, label)
        (folder / key).mkdir(parents=True)
        with (folder / key / "timeline.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(dump_timeline(_shell_document(raw[header_key], raw[footer_key])))
        layout = stage5.UnitLayout(mode="shell", name=shell.name, stem=shell.name, group=shell.group,
                                   props=tuple((name, kind) for name, kind, _ in shell.props))
        plan.append(({"key": key, "path": header_page.path, "function": label, "role": "shell",
                      "component": shell.name, "group": shell.group,
                      "entrypoint": f"{raw[header_key]['entrypoint']}+{raw[footer_key]['function']['name']}"},
                     app_units.shell_timeline(shell, units), layout, units))

    # ---- run them
    pages, in_app, alone = [], {}, {}
    for entry, timeline, layout, unit_facts in plan:
        entry |= {"nodes": 0, "calls": 0, "crash": None, "tsc": None}
        pages.append(entry)
        if timeline is None:
            continue
        entry["nodes"] = len(timeline.sequence)
        entry["calls"] = sum(c.kind == "call" for c in timeline.calls)
        try:
            tree, _ = run_stages(timeline, folder / entry["key"], config, generation_config, contract, unit_facts,
                                 layout)
            if entry["role"] == "page":
                entry["group"] = units.shells[tree.root.shell.header].group if tree.root.shell else PLAIN_GROUP
            (alone if entry["role"] == "not_called" else in_app)[entry["key"]] = folder / entry["key"] / "generated"
        except Exception as error:  # noqa: BLE001 - a crash on any unit is recorded, then reported as a bug
            lines = traceback.format_exc().strip().splitlines()
            where = next((ln.strip() for ln in reversed(lines) if ln.strip().startswith("File ")), "")
            entry["crash"] = {"error": f"{type(error).__name__}: {error}", "where": where}

    # ---- one Next.js app: every unit's files together
    app_dir, owners, collisions = folder / NEXT_APP, {}, []
    for key in sorted(in_app):
        for source in sorted(p for p in in_app[key].rglob("*") if p.is_file()):
            relative = source.relative_to(in_app[key]).as_posix()
            target = app_dir / relative
            if target.exists():
                if target.read_bytes() != source.read_bytes():
                    collisions.append(f"{relative}: written by {owners[relative]} and, differently, by {key}")
                owners[relative] = ""  # shared by several units (lib/)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            owners[relative] = key
    if any(entry.get("group") == PLAIN_GROUP for entry in pages):
        target = app_dir / "app" / PLAIN_GROUP / "layout.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as f:
            f.write(stage5.PLAIN_LAYOUT)
        owners[f"app/{PLAIN_GROUP}/layout.tsx"] = ""

    # ---- the whole app in one tsc program (so route and name collisions show), the rest on their own
    tsc_output, unattributed = None, 0
    if tsc and compile_check.unavailable() is None:
        tsc_output = ""
        if app_dir.exists():
            by_file, tsc_output = compile_check.check_app(f"apps_{timelines.app}", app_dir)
            counts = dict.fromkeys(in_app, 0)
            for relative, count in by_file.items():
                owner = owners.get(relative, "")
                if owner:
                    counts[owner] += count
                else:
                    unattributed += count
            for entry in pages:
                if entry["key"] in counts:
                    entry["tsc"] = counts[entry["key"]]
        if alone:
            errors, text = compile_check.check_many(f"apps_{timelines.app}_alone", alone)
            tsc_output += ("\n" if tsc_output and text else "") + text
            for entry in pages:
                if entry["key"] in errors:
                    entry["tsc"] = errors[entry["key"]]
            unattributed += errors.get("", 0)
    built = None
    if build and app_dir.exists() and compile_check.unavailable() is None:
        ok, text = compile_check.next_build(timelines.app, app_dir)
        built = {"ok": ok, "output": text}

    index = {
        "app": timelines.app, "sourceCommit": APPS.get(timelines.app, {}).get("commit", ""),
        "files": timelines.files,
        "pages": pages, "silentFiles": list(timelines.silent), "parseErrors": timelines.parse_errors,
        "schemaFiles": list(timelines.schema_files), "functions": timelines.functions or {},
        "shells": [{"name": s.name, "group": s.group, "header": s.header, "footer": s.footer,
                    "props": [list(p) for p in s.props]} for s in units.shells.values()],
        "appFiles": sorted(owners), "collisions": collisions,
        "tscRan": tsc_output is not None, "tscUnattributed": unattributed, "tscOutput": tsc_output or "",
        "nextBuild": built,
    }
    with (folder / "index.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return index


def main(argv: list[str]) -> int:
    reason = unavailable()
    if reason:
        print(f"harness cannot run: {reason}")
        return 1
    tsc, build = "--no-tsc" not in argv, "--next-build" in argv
    apps = [a for a in argv if not a.startswith("--")] or list(APPS)
    failed = 0
    for app in apps:
        index = run_app(app_timelines(app), tsc=tsc, build=build)
        units = [p for p in index["pages"] if p["role"] != "shell_part"]
        crashes = [p for p in units if p["crash"]]
        not_compiling = [p for p in units if p["tsc"]]
        roles = {role: sum(p["role"] == role for p in index["pages"]) for role in (
            "page", "component", "shell", "not_called")}
        print(f"{app}: {index['files']} files, {len(units)} units ({roles['page']} pages, {roles['component']} "
              f"function components, {roles['shell']} shells, {roles['not_called']} functions not in the app), "
              f"{len(index['parseErrors'])} parse errors, {len(crashes)} crashes, {len(not_compiling)} not compiling"
              + (f", {index['tscUnattributed']} tsc errors in shared files" if index["tscUnattributed"] else "")
              + ("" if index["tscRan"] else " (tsc not run)")
              + (f", {len(index['collisions'])} file collisions" if index["collisions"] else ""))
        for page in crashes:
            print(f"  CRASH {page['key']}: {page['crash']['error']}\n        {page['crash']['where']}")
        for page in not_compiling:
            print(f"  TSC   {page['key']}: {page['tsc']} errors")
        for line in index["collisions"]:
            print(f"  COLLISION {line}")
        if index["nextBuild"] is not None:
            print(f"  next build: {'ok' if index['nextBuild']['ok'] else 'FAILED'}")
        failed += len(crashes) + len(index["collisions"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
