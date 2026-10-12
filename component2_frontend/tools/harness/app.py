"""Whole-app side of the Component 2 test harness: every PHP file of an app -> timelines.

For each app (rule-discovery corpus only: HMS and WackoPicko) this produces an AST for every PHP
file that is not a library (ast_producer.py), and returns one timeline per page (a file's top-level
output) and per function that prints (harness.py). Call sites carry the called function's node id
and whether it prints, directly or through a function it calls.
"""

import json
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ast_producer import SourceFile, find_php, parse_sources, unavailable  # noqa: F401 - re-exported
from harness import FileHarness, function_resolver, parse_schema

ROOT = Path(__file__).resolve().parents[2]

# Only the rule-discovery corpus (CONTEXT.md section 5). Queued and evaluation apps are not listed on purpose.
APPS = {
    "hms": {"root": ROOT / "legacy-apps" / "hms", "prefix": "legacy-apps/hms",
            "commit": "777fda46b77a820977a5ba616283dbfbc40bf7e1",
            "exclude": ("TCPDF/", "vendor/")},  # libraries
    "wackopicko": {"root": ROOT / "legacy-apps" / "wackopicko", "prefix": "legacy-apps/wackopicko",
                   "commit": "cabc1b3a06beda7df98e839fa2d84db9a4bdfee7", "exclude": ()},
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


def head_commit(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


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


def app_timelines(app: str) -> AppTimelines:
    config = APPS[app]
    root = config["root"]
    commit = head_commit(root)
    if commit != config["commit"]:
        raise RuntimeError(f"legacy-apps/{root.name} is at {commit}, expected {config['commit']}")
    harnesses, errors = {}, {}
    with tempfile.TemporaryDirectory() as work:
        tree = Path(work) / "tree"
        export_commit(root, commit, tree)
        files = app_files(app, tree)
        schema, schema_files = load_schema(tree)
        sources = [SourceFile(tree / path, f"f{index:03d}", f"{config['prefix']}/{path}")
                   for index, path in enumerate(files, start=1)]
        asts, meta = parse_sources(sources)
        for path, source, ast in zip(files, sources, asts):
            if ast.parse_errors:
                errors[path] = list(ast.parse_errors)
            harnesses[path] = FileHarness(source.source.read_bytes(), ast, source.path, schema)
    file_ids = {path: harness.file_id for path, harness in harnesses.items()}

    def provenance(path: str) -> dict:
        return {"generator": "c2-harness", "parser": meta["parser"], "parserSyntax": meta["syntax"][file_ids[path]],
                "php": meta["php"], "source": f"{config['prefix']}/{path}", "sourceCommit": commit,
                "astFormat": "Member 01 schema 1.0 (produced by the harness: tools/harness/parse.php)",
                "schemaFiles": schema_files,
                "labels": "none: no labels file exists for whole apps, so every node is unlabelled",
                "note": "Test harness output, not Member 01's core (docs/c2-roadmap.md Phases 1 and 1b)."}

    pages, silent = build_timelines(harnesses, provenance)
    return AppTimelines(app, tuple(pages), tuple(silent), errors, len(files), tuple(schema_files),
                        function_table(harnesses))


def dump_timeline(timeline: dict) -> str:
    return json.dumps(timeline, indent=2, ensure_ascii=False) + "\n"
