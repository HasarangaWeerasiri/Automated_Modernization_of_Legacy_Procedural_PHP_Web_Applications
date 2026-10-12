"""Whole-app side of the Component 2 test harness: every PHP file of an app -> timelines.

For each app (rule-discovery corpus only: HMS and WackoPicko) this parses every PHP file that is
not a library, and returns one timeline per page (a file's top-level output) and per function that
prints. Calls are marked with whether the callee prints, directly or through a function it calls.
"""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness import FileHarness

ROOT = Path(__file__).resolve().parents[2]
PARSE_PHP = Path(__file__).with_name("parse.php")
VENDOR = Path(__file__).with_name("vendor")

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


def find_php() -> str | None:
    for candidate in (os.environ.get("PHP_BIN"), shutil.which("php"), r"C:\xampp\php\php.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def unavailable() -> str | None:
    """Why the harness cannot run here, or None when it can."""
    if find_php() is None:
        return "PHP not found: set PHP_BIN or put php on PATH"
    if not (VENDOR / "autoload.php").exists():
        return "tools/harness/vendor is missing: run `composer install` in tools/harness"
    return None


def parse_files(sources: list[Path]) -> list[dict]:
    """parse.php's output for each source file, in order (one PHP process for all of them)."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        manifest = [{"source": str(src), "out": str(work / f"{index}.json")} for index, src in enumerate(sources)]
        (work / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        subprocess.run([find_php(), str(PARSE_PHP), str(work / "manifest.json")], check=True, capture_output=True)
        return [json.loads(Path(entry["out"]).read_text(encoding="utf-8")) for entry in manifest]


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


def page_key(path: str, function: str | None) -> str:
    return path.replace("/", "__") + (f"#{function}" if function else "")


def safe_function_name(name: str) -> str:
    """Class::method -> Class.method: the name ends up in an entrypoint and in folder names."""
    return name.replace("::", ".")


def build_timelines(file_harnesses: dict[str, FileHarness], provenance) -> tuple[list[Page], list[str]]:
    """Timelines for a set of files that form one app (so calls between them can be marked)."""
    prints: dict[str, bool] = {}  # lower-case function name -> prints directly
    calls: dict[str, set] = {}  # lower-case function name -> names it calls
    for harness in file_harnesses.values():
        for name, scope in harness.functions.items():
            lower = name.lower()
            prints[lower] = prints.get(lower, False) or bool(scope.outputs)
            calls.setdefault(lower, set()).update(c["callee"] for c in scope.calls)
    changed = True
    while changed:  # a function prints if a function it calls prints
        changed = False
        for name, callees in calls.items():
            if not prints[name] and any(prints.get(callee) for callee in callees):
                prints[name] = changed = True

    pages, silent = [], []
    for path in sorted(file_harnesses):
        harness = file_harnesses[path]
        for scope in harness.scopes():
            original = scope.name
            if original is not None:
                scope.name = safe_function_name(original)
            timeline = harness.timeline(scope, provenance(path), prints.get)
            if timeline is None:
                if original is None:
                    silent.append(path)
                continue
            pages.append(Page(page_key(path, scope.name), path, scope.name, timeline))
    return pages, silent


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
        parsed = parse_files([tree / f for f in files])
        for index, (path, doc) in enumerate(zip(files, parsed), start=1):
            if doc.get("errors"):
                errors[path] = doc["errors"]
            harnesses[path] = FileHarness((tree / path).read_bytes(), doc, f"f{index:03d}",
                                          f"{config['prefix']}/{path}")

    def provenance(path: str) -> dict:
        doc = parsed[files.index(path)]
        return {"generator": "c2-harness", "parser": doc["parser"], "parserSyntax": doc["syntax"],
                "php": doc["php"], "source": f"{config['prefix']}/{path}", "sourceCommit": commit,
                "labels": "none: no labels file exists for whole apps, so every node is unlabelled",
                "note": "Test harness output, not Member 01's core (docs/c2-roadmap.md Phase 1)."}

    pages, silent = build_timelines(harnesses, provenance)
    return AppTimelines(app, tuple(pages), tuple(silent), errors, len(files))


def dump_timeline(timeline: dict) -> str:
    return json.dumps(timeline, indent=2, ensure_ascii=False) + "\n"
