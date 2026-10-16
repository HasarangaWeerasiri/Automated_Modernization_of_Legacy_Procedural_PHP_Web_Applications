"""Whole-app side of the Component 2 test harness: every PHP file of an app -> timelines.

For each app (rule-discovery corpus only: HMS, WackoPicko, phpBlog, attendance and warehouse) this produces an AST for every PHP
file that is not a library (ast_producer.py), and returns one timeline per page (a file's top-level
output) and per function that prints (harness.py). Call sites carry the called function's node id
and whether it prints, directly or through a function it calls.
"""

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ast_producer import SourceFile, find_php, parse_sources, unavailable  # noqa: F401 - re-exported
from harness import FileHarness, function_resolver, parse_schema

ROOT = Path(__file__).resolve().parents[2]

# Only the active rule-discovery corpus (CONTEXT.md section 5). Queued and evaluation apps are not listed on
# purpose. `docroot` is the folder the web server serves, relative to the repository root ("" = the root
# itself): routes are paths from it (generation-spec v0.7 section 8a). Recorded in legacy-apps/PINS.md.
APPS = {
    "hms": {"root": ROOT / "legacy-apps" / "hms", "prefix": "legacy-apps/hms",
            "commit": "777fda46b77a820977a5ba616283dbfbc40bf7e1", "docroot": "",
            "exclude": ("TCPDF/", "vendor/")},  # libraries
    "wackopicko": {"root": ROOT / "legacy-apps" / "wackopicko", "prefix": "legacy-apps/wackopicko",
                   "commit": "cabc1b3a06beda7df98e839fa2d84db9a4bdfee7", "docroot": "website", "exclude": ()},
    # `substitutions` (owner decision, CONTEXT v18): a file that is not in the repository is analysed from
    # another file, {missing file: the file read in its place}. Each one is recorded in legacy-apps/PINS.md
    # with its evidence; none is added without asking the owner.
    "phpblog": {"root": ROOT / "legacy-apps" / "phpblog", "prefix": "legacy-apps/phpblog",
                "commit": "4ffe3c9641a42b99829cf4b8e4185093793a3abc", "docroot": "", "exclude": (),
                "substitutions": {"config.php": "install/config.tpl"}},
    # roadmap Phase 3, app 4 (opened 14 Oct 2026): PHP 5 (mysql_*), parsed only, never run
    "attendance": {"root": ROOT / "legacy-apps" / "attendance", "prefix": "legacy-apps/attendance",
                   "commit": "31781040c50f2be20514ef18c1b0d4ae7b2bf76e", "docroot": "", "exclude": ()},
    # roadmap Phase 3, app 5, the reserve app (opened 14 Oct 2026). Supplied by the owner as a `git archive` of the
    # commit and its manifest (the agent had no network): no .git, so the folder is read directly, after every
    # file is checked against `manifest` (CONTEXT v21 section 5)
    "warehouse": {"root": ROOT / "legacy-apps" / "warehouse", "prefix": "legacy-apps/warehouse",
                  "commit": "69fdd1ca1364a37e6dfbc7d90ded693964d87efe", "docroot": "", "exclude": (),
                  "manifest": ROOT / "legacy-apps" / "warehouse-69fdd1c.sha256"},
}


@dataclass(frozen=True)
class Page:
    key: str  # folder-safe name, e.g. "website__include__html_functions.php#our_header"
    path: str  # the file, relative to the app root
    function: str | None
    timeline: dict


@dataclass(frozen=True)
class AppTimelines:
    app: str
    pages: tuple[Page, ...]
    silent: tuple[str, ...]  # files whose top level prints nothing
    parse_errors: dict  # file -> messages
    files: int
    schema_files: tuple[str, ...] = ()  # the .sql files SELECT * was expanded from
    functions: dict = None  # function node id -> {"name", "path"}, for reports
    docroot: str = ""  # the served folder, relative to the app root
    prefix: str = ""  # what an entrypoint starts with before the file's path, e.g. "legacy-apps/hms"
    silent_notes: dict = None  # file -> the harness notes of its top level, for files that print nothing
    silent_redirects: dict = None  # file -> its literal header("Location: ...") calls, for files that print nothing
    js_references: dict = None  # PHP file -> [{file, line}] of the app's .js lines that name it (spec v0.12)


def head_commit(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def verify_manifest(root: Path, manifest: Path) -> int:
    """Every file under `root` against a `sha256  path` manifest: same set of files, same bytes. Raises on the
    first difference (nothing may run on an app that is not exactly the pinned commit); returns the count."""
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, path = line.split(None, 1)
            expected[path.strip().lstrip("*")] = digest.lower()
    found = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    missing, extra = sorted(set(expected) - set(found)), sorted(set(found) - set(expected))
    if missing or extra:
        raise RuntimeError(f"{root.name}: not the archived commit: missing {missing}, not in the manifest {extra}")
    changed = [path for path in found if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected[path]]
    if changed:
        raise RuntimeError(f"{root.name}: files differ from the manifest: {changed}")
    return len(found)


def source_commit(app: str) -> str:
    """The commit the app's folder holds: its git HEAD, or, for an archived app, the recorded commit once every
    file matches the manifest."""
    config = APPS[app]
    if "manifest" in config:
        verify_manifest(config["root"], config["manifest"])
        return config["commit"]
    return head_commit(config["root"])


def export_commit(root: Path, commit: str, destination: Path) -> None:
    """The app's files exactly as committed (the working copy may carry local patches, e.g. the HMS DB fix)."""
    archive = destination.parent / f"{destination.name}.tar"
    subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "-o", str(archive), commit], check=True,
                   capture_output=True)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    archive.unlink()


def app_files(app: str, tree: Path) -> list[str]:
    """The app's own PHP files in `tree`, relative to it, sorted."""
    files = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*.php"))
    return [f for f in files if not any(f.startswith(prefix) for prefix in APPS[app]["exclude"])]


def load_schema(tree: Path) -> tuple[dict, list[str]]:
    """({table: [columns]}, the files they came from) for the schema files the app ships (Member 01 Q1).

    A table that two files declare differently is left out: which one is live is not certain."""
    versions: dict[str, list] = {}
    files = []
    for path in sorted(tree.rglob("*.sql")):
        tables = parse_schema(path.read_bytes().decode("utf-8", errors="replace"))
        if tables:
            files.append(path.relative_to(tree).as_posix())
        for table, columns in tables.items():
            versions.setdefault(table, []).append(columns)
    return {t: v[0] for t, v in sorted(versions.items()) if all(x == v[0] for x in v)}, files


def commit_schema(app: str) -> dict:
    """{table: [columns]} from the .sql files of the app's pinned commit, read without a checkout."""
    config = APPS[app]
    names = subprocess.run(["git", "-C", str(config["root"]), "ls-tree", "-r", "--name-only", config["commit"]],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    versions: dict[str, list] = {}
    for name in sorted(n for n in names if n.lower().endswith(".sql")):
        blob = subprocess.run(["git", "-C", str(config["root"]), "show", f"{config['commit']}:{name}"],
                              capture_output=True, check=True).stdout
        for table, columns in parse_schema(blob.decode("utf-8", errors="replace")).items():
            versions.setdefault(table, []).append(columns)
    return {t: v[0] for t, v in sorted(versions.items()) if all(x == v[0] for x in v)}


def page_key(path: str, function: str | None) -> str:
    return path.replace("/", "__") + (f"#{function}" if function else "")


def safe_function_name(name: str) -> str:
    """Class::method -> Class.method: the name ends up in an entrypoint and in folder names."""
    return name.replace("::", ".")


def js_references(tree: Path, php_files: list, docroot: str = "", exclude: tuple = ()) -> dict:
    """PHP file -> [{file, line}]: the app's own .js lines with a quoted string naming that file (its path from
    the document root, or its file name when no other PHP file has that name). Generation-spec v0.12: the JS that
    calls an AJAX endpoint is named in its notGenerated entry."""
    served = {posixpath.relpath(f, docroot) if docroot else f: f for f in php_files
              if not docroot or f.startswith(docroot + "/")}
    names: dict[str, list] = {}
    for path in served.values():
        names.setdefault(posixpath.basename(path), []).append(path)
    found: dict[str, list] = {}
    scripts = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*.js"))
    for script in scripts:
        if any(script.startswith(prefix) for prefix in exclude):
            continue
        text = (tree / script).read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for quoted in re.findall(r"""['"]([^'"\s]+\.php)(?:\?[^'"]*)?['"]""", line):
                target = served.get(posixpath.normpath(quoted.lstrip("./")))
                if target is None and len(names.get(posixpath.basename(quoted), [])) == 1:
                    target = names[posixpath.basename(quoted)][0]
                if target is not None:
                    found.setdefault(target, []).append({"file": script, "line": number})
    return found


def silent_redirects(harnesses: dict, silent) -> dict:
    """The literal header("Location: ...") calls of files that print nothing (they have no unit, so no Stage 5
    flag): file -> [{line, target, guarded}] (generation-spec v0.10, header_redirect)."""
    return {path: [{"line": r["loc"]["startLine"], "target": r["target"],
                    "guarded": any(e["role"] == "branch" for e in r["enclosedBy"])}
                   for r in harnesses[path].top.redirects]
            for path in silent if harnesses[path].top.redirects}


def build_timelines(file_harnesses: dict[str, FileHarness], provenance) -> tuple[list[Page], list[str]]:
    """Timelines for a set of files that form one app (so calls between them can be resolved)."""
    resolve = function_resolver(file_harnesses)
    pages, silent = [], []
    for path in sorted(file_harnesses):
        harness = file_harnesses[path]
        for scope in harness.scopes():
            original = scope.name
            if original is not None:
                scope.name = safe_function_name(original)
            timeline = harness.timeline(scope, provenance(path), resolve)
            if timeline is None:
                if original is None:
                    silent.append(path)
                continue
            pages.append(Page(page_key(path, scope.name), path, scope.name, timeline))
    return pages, silent


def function_table(file_harnesses: dict[str, FileHarness]) -> dict:
    """function node id -> {"name", "path"} for every function the files define."""
    return {scope.node_id: {"name": name, "path": path}
            for path in sorted(file_harnesses) for name, scope in file_harnesses[path].functions.items()}


def app_timelines(app: str, rules: str = "1.1") -> AppTimelines:
    """`rules` "1.0" leaves out the harness extension that only boundary rules v1.1 read (`topLevelIfs`), so the
    frozen run is the same bytes as at the freeze commit."""
    config = APPS[app]
    root = config["root"]
    commit = source_commit(app)
    if commit != config["commit"]:
        raise RuntimeError(f"legacy-apps/{root.name} is at {commit}, expected {config['commit']}")
    harnesses, errors = {}, {}
    with tempfile.TemporaryDirectory() as work:
        tree = Path(work) / "tree"
        if "manifest" in config:  # verified above: the folder is the commit
            shutil.copytree(root, tree)
        else:
            export_commit(root, commit, tree)
        substitutions = config.get("substitutions", {})
        for missing, source in sorted(substitutions.items()):
            if (tree / missing).exists():
                raise RuntimeError(f"{app}: {missing} exists at {commit}; its substitution is out of date")
            shutil.copyfile(tree / source, tree / missing)
        files = app_files(app, tree)
        scripts = js_references(tree, files, config["docroot"], config["exclude"])
        schema, schema_files = load_schema(tree)
        sources = [SourceFile(tree / path, f"f{index:03d}", f"{config['prefix']}/{path}")
                   for index, path in enumerate(files, start=1)]
        asts, meta = parse_sources(sources)
        for path, source, ast in zip(files, sources, asts):
            if ast.parse_errors:
                errors[path] = list(ast.parse_errors)
            harnesses[path] = FileHarness(source.source.read_bytes(), ast, source.path, schema,
                                          top_ifs=rules.removeprefix("v") != "1.0")
    file_ids = {path: harness.file_id for path, harness in harnesses.items()}

    def provenance(path: str) -> dict:
        return {"generator": "c2-harness", "parser": meta["parser"], "parserSyntax": meta["syntax"][file_ids[path]],
                "php": meta["php"], "source": f"{config['prefix']}/{path}", "sourceCommit": commit,
                **({"analysisSubstitution": f"not in the repository: analysed from {substitutions[path]} "
                                            "(legacy-apps/PINS.md)"} if path in substitutions else {}),
                "astFormat": "Member 01 schema 1.0 (produced by the harness: tools/harness/parse.php)",
                "schemaFiles": schema_files,
                "labels": "none: no labels file exists for whole apps, so every node is unlabelled",
                "note": "Test harness output, not Member 01's core (docs/c2-roadmap.md Phases 1 and 1b)."}

    pages, silent = build_timelines(harnesses, provenance)
    # A file that prints nothing has no timeline, so its notes (e.g. `mysql_connect(...) or die("msg")`) would be
    # lost: they are kept for the pattern log.
    silent_notes = {path: sorted(harnesses[path].top.notes, key=lambda n: (n["line"], n["col"], n["kind"]))
                    for path in silent if harnesses[path].top.notes}
    return AppTimelines(app, tuple(pages), tuple(silent), errors, len(files), tuple(schema_files),
                        function_table(harnesses), config["docroot"], config["prefix"], silent_notes,
                        silent_redirects(harnesses, silent), scripts)


def dump_timeline(timeline: dict) -> str:
    return json.dumps(timeline, indent=2, ensure_ascii=False) + "\n"
