"""Run Stages 1-5 on the rule-discovery apps, each as one whole Next.js app (roadmap Phases 1 and 2b).

An app's timelines are not independent: a page calls functions, and a header / footer pair wraps
pages (boundary-rules section 4). So an app is run as a set of units:

  page         a file's top-level output -> app/(<group>)/<route>/page.tsx; the route is the file's path
               from the app's document root, without ".php", and index.php is its directory
  component    a function that is called and is tag-balanced (R-F1) -> components/<Name>.tsx
  shell        a header / footer pair, functions or included files (R-F2)
               -> components/<H>Shell.tsx + app/(<h>)/layout.tsx
  not called   a function with output that no page calls (or that is not a component): run on its
               own so it is still checked, but it is not part of the app

For each app this writes, under output/apps/<app>/:

  <unit>/timeline.json, stage1.json ... stage5.json, generated/   one folder per unit
  next-app/                                                       every unit's files as one app
  index.json                                                      every unit, any crash, its tsc errors

`next build` (--next-build) writes its result beside the app's folder, output/apps/<app>.next-build.json,
with a hash of the app it built: index.json and the app's folder are the same bytes whether or not it ran.

No labels file exists for whole apps, so every node is unlabelled (CONTEXT hard rule 3); no contract
or endpoint map exists, so every page is no_endpoint. A unit that makes a stage raise is recorded
as a crash, with the error, and the run goes on: a crash is a bug to fix, not a reason to stop.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/harness/run_apps.py            # every app of the active corpus
    venv/Scripts/python.exe tools/harness/run_apps.py hms --no-tsc
    venv/Scripts/python.exe tools/harness/run_apps.py --next-build   # also `next build` each app once
    venv/Scripts/python.exe tools/harness/run_apps.py --rules v1.0   # the frozen rules (default: v1.1)

`--rules v1.0` runs boundary rules v1.0 with spec v0.13, byte for byte the outputs of the freeze commit (Phase 5
reports v1.0 and v1.1; docs/post-freeze-log.md).
"""

import json
import re
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


def run_stages(timeline, folder: Path, config, generation_config, contract, units=None, layout=None,
               endpoint_map=None):
    """Stages 1-5 for one loaded timeline, each stage's JSON written into `folder`. Raises on a stage failure.
    `endpoint_map` (default: none, every page is no_endpoint) is only given for a substitute run, e.g. the
    Phase 4 replay check (tools/runtime/replay/).

    `layout` may be a function of the Stage 2 tree: a page's route group is known only once Stage 2 has said
    whether the page stands inside a shell."""
    result1 = stage1.isolate_presentation(timeline, {})  # no labels: every node is unlabelled
    stage1.write_result(result1, folder / "stage1.json")
    tree = stage2.infer_boundaries(result1, timeline, config, units)
    stage2.write_tree(tree, folder / "stage2.json")
    requirements = stage3.recover_requirements(tree, timeline)
    stage3.write_requirements(requirements, folder / "stage3.json")
    report = stage4.reconcile(requirements, contract, endpoint_map or {})  # none: every page is no_endpoint
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


def route_of(path: str, docroot: str = "") -> tuple[str, str]:
    """(route, name stem) of a page: its path from the document root without ".php"; index.php is its directory
    (generation-spec v0.7 section 8a). The stem names the page's components and keeps `index`."""
    relative = path[len(docroot) + 1:] if docroot and path.startswith(docroot + "/") else path
    relative = relative.removesuffix(".php")
    route = "" if relative == "index" else relative.removesuffix("/index")
    return route, app_units.pascal(relative)


def _label(document: dict) -> str:
    """A function's name, or an included file's base name."""
    return document["function"]["name"] if "function" in document else document["entrypoint"].rsplit("/", 1)[-1]


def _shell_document(header: dict, footer: dict) -> dict:
    """The header's and the footer's harness timelines as one document, for the reports."""
    return {
        "entrypoint": f"{header['entrypoint']}+{_label(footer)}",
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


LIB_HEADS = {"lib/api-types.ts": re.compile(r"export type (\w+)"),
             "lib/api.ts": re.compile(r"(?:// .*\n)*export (?:async )?function (\w+)")}
API_IMPORT = re.compile(r'import type \{ ([\w, ]+) \} from "\./api-types";')


def merge_lib(relative: str, first: str, second: str) -> tuple[str | None, list]:
    """Two units' lib/api-types.ts or lib/api.ts as one file (each unit writes only what its own endpoint
    needs): the union of their declarations, in order of first appearance, with one import line. Returns
    (merged text or None when the file is not one of these, [names declared differently by the two])."""
    head = LIB_HEADS.get(relative)
    if head is None:
        return None, []
    order, blocks, names, preamble, clash = [], {}, set(), [], []
    for text in (first, second):
        found = API_IMPORT.search(text)
        if found:
            names |= {n.strip() for n in found.group(1).split(",")}
        for block in text.strip("\n").split("\n\n"):
            match = head.match(block)
            if match is None:
                # (a unit with no endpoint writes only a stub ending in `export {};`: nothing to keep)
                if not API_IMPORT.fullmatch(block) and not block.endswith("export {};") and block not in preamble:
                    preamble.append(block)  # the header comment of api-types.ts
                continue
            name = match.group(1)
            if name not in blocks:
                order.append(name)
                blocks[name] = block
            elif blocks[name] != block:
                clash.append(name)
    if not order:  # neither unit has an endpoint
        return first, clash
    parts = ([f'import type {{ {", ".join(sorted(names))} }} from "./api-types";'] if names else []) + preamble
    return "\n\n".join(parts + [blocks[name] for name in order]) + "\n", clash


def run_app(timelines: AppTimelines, output: Path = OUTPUT, tsc: bool = True, build: bool = False,
            contract_path: Path = EMPTY_CONTRACT, endpoint_map_path: Path | None = None,
            rules: str | None = None) -> dict:
    """Run every unit of an app and assemble one Next.js app; returns (and writes) the app's index.

    By default there is no contract and no endpoint map (none exists for whole apps). A substitute contract and
    map are given only by a test fixture, e.g. the Phase 4 replay check; they are named in the index. `rules`
    ("1.0" / "1.1") overrides config/boundary_rules.json; give the same to app_timelines."""
    folder = output / timelines.app
    if folder.exists():
        shutil.rmtree(folder)
    config, generation_config = load_boundary_config(rules=rules), load_generation_config()
    contract = loader.load_contract(contract_path)
    endpoint_map = loader.load_endpoint_map(endpoint_map_path) if endpoint_map_path else None

    loaded, raw = {}, {}
    for page in timelines.pages:
        page_folder = folder / page.key
        page_folder.mkdir(parents=True)
        with (page_folder / "timeline.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(dump_timeline(page.timeline))
        loaded[page.key] = loader.load_timeline(page_folder / "timeline.json")
        raw[page.key] = page.timeline
    prefix = timelines.prefix + "/" if timelines.prefix else ""
    file_names = {prefix + page.path: route_of(page.path, timelines.docroot)[0] or "index"
                  for page in timelines.pages if page.function is None}
    units = app_units.analyse(list(loaded.values()), config, file_names)
    key_of = {t.function.node_id if t.function is not None else t.entrypoint: key for key, t in loaded.items()}

    # ---- the units to run: (key, path, function label, role, timeline, layout, units or None)
    plan = []
    for page in timelines.pages:
        timeline = loaded[page.key]
        base = {"key": page.key, "path": page.path, "function": page.function,
                "entrypoint": page.timeline["entrypoint"]}
        if timeline.function is None and timeline.entrypoint not in units.shell_functions:
            route, stem = route_of(page.path, timelines.docroot)

            def layout(tree, route=route, stem=stem):
                """Where a page goes: under its shell's route group, or (plain), at its route."""
                group = units.shells[tree.root.shell.header].group if tree.root.shell else PLAIN_GROUP
                return stage5.UnitLayout(mode="page", route=f"{group}/{route}" if route else group, stem=stem)

            plan.append((base | {"role": "page", "route": "/" + route}, timeline, layout, units))
            continue
        unit = units.functions[timeline.function.node_id if timeline.function is not None else timeline.entrypoint]
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
        label = f"{_label(raw[header_key])}+{_label(raw[footer_key])}"
        key = page_key(header_page.path, label)
        (folder / key).mkdir(parents=True)
        with (folder / key / "timeline.json").open("w", encoding="utf-8", newline="\n") as f:
            f.write(dump_timeline(_shell_document(raw[header_key], raw[footer_key])))
        layout = stage5.UnitLayout(mode="shell", name=shell.name, stem=shell.name, group=shell.group,
                                   props=tuple((name, kind) for name, kind, _ in shell.props))
        plan.append(({"key": key, "path": header_page.path, "function": label, "role": "shell",
                      "component": shell.name, "group": shell.group,
                      "entrypoint": f"{raw[header_key]['entrypoint']}+{_label(raw[footer_key])}"},
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
        entry["includes"] = sum(c.kind == "include" for c in timeline.calls)
        try:
            tree, result = run_stages(timeline, folder / entry["key"], config, generation_config, contract, unit_facts,
                                      layout, endpoint_map)
            if result.not_generated is not None:  # spec v0.8 section 6a: not a page (e.g. non_html_response)
                entry["notGenerated"] = result.not_generated
                if result.not_generated.get("subtype") == "ajax_endpoint":  # spec v0.12: the JS that calls it
                    entry["notGenerated"] = result.not_generated | {
                        "calledFrom": (timelines.js_references or {}).get(entry["path"], [])}
                continue
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
                    merged, clash = merge_lib(relative, target.read_text(encoding="utf-8"),
                                              source.read_text(encoding="utf-8"))
                    if merged is not None:  # each unit's fetch functions and response types, together
                        target.write_bytes(merged.encode("utf-8"))
                    for name in clash if merged is not None else [None]:
                        collisions.append(f"{relative}: written by {owners[relative]} and, differently, by {key}"
                                          + (f" ({name} differs)" if name else ""))
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
    if build and app_dir.exists() and compile_check.unavailable() is None:
        # Kept out of index.json and of the app's folder: a run is the same bytes whether or not it built.
        ok, text = compile_check.next_build(timelines.app, app_dir)
        compile_check.write_build(output, timelines.app, app_dir, ok, text)

    index = {
        "app": timelines.app, "sourceCommit": APPS.get(timelines.app, {}).get("commit", ""),
        "docroot": timelines.docroot, "files": timelines.files,
        **({"substitute": {"contract": contract_path.name, "endpointMap": endpoint_map_path.name}}
           if endpoint_map_path else {}),
        "pages": pages, "silentFiles": list(timelines.silent), "silentFileNotes": timelines.silent_notes or {},
        "silentFileRedirects": timelines.silent_redirects or {},
        "parseErrors": timelines.parse_errors,
        "schemaFiles": list(timelines.schema_files), "functions": timelines.functions or {},
        "shells": [{"name": s.name, "group": s.group, "header": s.header, "footer": s.footer,
                    "props": [list(p) for p in s.props]} for s in units.shells.values()],
        "notGenerated": [{"key": p["key"], "path": p["path"], **p["notGenerated"]} for p in pages
                         if p.get("notGenerated")],
        "appFiles": sorted(owners), "collisions": collisions,
        "tscRan": tsc_output is not None, "tscUnattributed": unattributed, "tscOutput": tsc_output or "",
    }
    with (folder / "index.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return index


def main(argv: list[str]) -> int:
    """Command-line entry: run the given apps (all by default) through Stages 1-5 and report crashes and compile errors."""
    reason = unavailable()
    if reason:
        print(f"harness cannot run: {reason}")
        return 1
    tsc, build = "--no-tsc" not in argv, "--next-build" in argv
    rules = argv[argv.index("--rules") + 1] if "--rules" in argv else None
    rules = load_boundary_config(rules=rules).rules_version  # checked; the file's version when not given
    named = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--rules")]
    apps = named or list(APPS)
    failed = 0
    for app in apps:
        index = run_app(app_timelines(app, rules), tsc=tsc, build=build, rules=rules)
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
        built = compile_check.read_build(OUTPUT, app, OUTPUT / app / NEXT_APP) if build else None
        if built is not None:
            print(f"  next build: {'ok' if built['ok'] else 'FAILED'}")
        failed += len(crashes) + len(index["collisions"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
