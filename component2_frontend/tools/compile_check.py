"""Compile check for Stage 5's output (docs/generation-spec.md section 10).

Copies one mock's generated files into the pinned Next.js test app
(output/test-app/c2-check/<mock>/), writes a tsconfig there that extends the
app's own (which must set "strict": true, or the check fails), and runs
`npx tsc --noEmit` on it. Target: 0 errors for every mock.

Usage, from component2_frontend/ (after `npm ci` in output/test-app and a Stage 5 run):
    venv/Scripts/python.exe tools/compile_check.py            # every mock in output/generated
    venv/Scripts/python.exe tools/compile_check.py list_while # one mock

Each run writes output/tsc_<mock>.json (error count), which tools/results_table.py reads.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_APP = ROOT / "output" / "test-app"
CHECK_DIR = TEST_APP / "c2-check"
GENERATED = ROOT / "output" / "generated"


@dataclass(frozen=True)
class CompileResult:
    name: str
    errors: int
    output: str


def unavailable() -> str | None:
    """Why the check cannot run here, or None when it can."""
    if shutil.which("node") is None or shutil.which("npx") is None:
        return "Node.js (node and npx) is not installed"
    if not (TEST_APP / "node_modules" / "typescript").exists():
        return "output/test-app has no node_modules: run `npm ci` in output/test-app"
    return None


def strict_enabled(tsconfig: Path = TEST_APP / "tsconfig.json") -> bool:
    """Spec v0.2 section 10: the test app must compile with "strict": true."""
    try:
        options = json.loads(tsconfig.read_text(encoding="utf-8")).get("compilerOptions", {})
    except (OSError, ValueError):
        return False
    return options.get("strict") is True


def check(name: str, source: Path) -> CompileResult:
    if not strict_enabled():
        return CompileResult(name, 1, 'output/test-app/tsconfig.json does not set "strict": true')
    target = CHECK_DIR / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    config = {"extends": "../../tsconfig.json", "compilerOptions": {"incremental": False, "noEmit": True},
              "include": ["**/*.ts", "**/*.tsx"]}
    (target / "tsconfig.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    run = subprocess.run([shutil.which("npx"), "--no-install", "tsc", "--noEmit", "-p", str(target / "tsconfig.json")],
                         cwd=TEST_APP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (run.stdout + run.stderr).strip()
    errors = len(re.findall(r"error TS\d+", output))
    if run.returncode != 0 and errors == 0:
        errors = 1  # tsc failed without a TS error line (e.g. it could not start): never report a pass
    return CompileResult(name, errors, output)


def check_many(name: str, sources: dict[str, Path]) -> tuple[dict[str, int], str]:
    """One tsc run over many generated pages (each in its own folder): errors per page, and tsc's output.

    Used for whole apps, where a run per page would take minutes. A failure tsc does not attribute to a page
    is counted under the key ""."""
    if not strict_enabled():
        return {key: 1 for key in sources}, 'output/test-app/tsconfig.json does not set "strict": true'
    target = CHECK_DIR / name
    if target.exists():
        shutil.rmtree(target)
    for key, source in sources.items():
        shutil.copytree(source, target / key)
    # The test app's own tsconfig, with no compiler option overridden (spec v0.5 section 8): every generated
    # file is a module, so many pages can be checked in one program without their names colliding.
    config = {"extends": "../../tsconfig.json", "compilerOptions": {"incremental": False, "noEmit": True},
              "include": ["**/*.ts", "**/*.tsx"]}
    target.mkdir(parents=True, exist_ok=True)
    (target / "tsconfig.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    run = subprocess.run([shutil.which("npx"), "--no-install", "tsc", "--noEmit", "-p", str(target / "tsconfig.json")],
                         cwd=TEST_APP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (run.stdout + run.stderr).strip()
    errors = {key: 0 for key in sources}
    prefix = f"c2-check/{name}/"
    attributed = 0
    for line in output.splitlines():
        if not re.search(r"error TS\d+", line):
            continue
        path = line.replace("\\", "/")
        key = next((k for k in sorted(sources, key=len, reverse=True) if path.startswith(prefix + k + "/")), None)
        if key is not None:
            errors[key] += 1
            attributed += 1
        else:
            errors[""] = errors.get("", 0) + 1
    if run.returncode != 0 and attributed == 0 and "" not in errors:
        errors[""] = 1  # tsc failed without an error line: never report a pass
    return errors, output


def main(names: list[str]) -> int:
    reason = unavailable()
    if reason:
        print(f"compile check skipped: {reason}")
        return 0
    names = names or sorted(p.name for p in GENERATED.iterdir() if p.is_dir())
    failed = 0
    for name in names:
        result = check(name, GENERATED / name)
        print(f"{name}: tsc --noEmit {result.errors} errors")
        record = {"check": "tsc --noEmit", "mock": name, "errors": result.errors, "strict": strict_enabled()}
        (ROOT / "output" / f"tsc_{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if result.errors:
            failed += 1
            print(result.output)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


def check_app(name: str, source: Path) -> tuple[dict[str, int], str]:
    """One tsc run over a whole generated Next.js app (app/, components/, lib/ with all its route groups):
    errors per file, relative to the app, and tsc's output. Uses the test app's own tsconfig, nothing overridden.

    A failure tsc does not put in a file of the app is counted under the key ""."""
    if not strict_enabled():
        return {"": 1}, 'output/test-app/tsconfig.json does not set "strict": true'
    target = CHECK_DIR / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    config = {"extends": "../../tsconfig.json", "compilerOptions": {"incremental": False, "noEmit": True},
              "include": ["**/*.ts", "**/*.tsx"]}
    (target / "tsconfig.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    run = subprocess.run([shutil.which("npx"), "--no-install", "tsc", "--noEmit", "-p", str(target / "tsconfig.json")],
                         cwd=TEST_APP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (run.stdout + run.stderr).strip()
    errors: dict[str, int] = {}
    prefix = f"c2-check/{name}/"
    for line in output.splitlines():
        if not re.search(r"error TS\d+", line):
            continue
        path = line.replace("\\", "/")
        found = re.match(re.escape(prefix) + r"(.+?)\(\d+,\d+\)", path)
        key = found.group(1) if found else ""
        errors[key] = errors.get(key, 0) + 1
    if run.returncode != 0 and not errors:
        errors[""] = 1  # tsc failed without an error line: never report a pass
    return errors, output


BUILD_DIR = TEST_APP / "c2-build"  # inside the test app, so its node_modules are found without any link


def tree_hash(folder: Path) -> str:
    """One sha256 over every file of a folder (path and bytes), in sorted order."""
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def build_record(output: Path, app: str) -> Path:
    """Where the result of `next build` of an app is kept: beside the app's folder, not in it, so the app's
    outputs are the same bytes whether or not it was built."""
    return output / f"{app}.next-build.json"


def write_build(output: Path, app: str, app_dir: Path, ok: bool, text: str) -> None:
    with build_record(output, app).open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"app": app, "ok": ok, "appSha256": tree_hash(app_dir), "output": text}, indent=2,
                           ensure_ascii=False) + "\n")


def read_build(output: Path, app: str, app_dir: Path) -> dict | None:
    """The recorded `next build` result, only if it is the build of the app that is there now."""
    path = build_record(output, app)
    if not path.exists() or not app_dir.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if record.get("appSha256") == tree_hash(app_dir) else None
BUILD_FILES = ("package.json", "tsconfig.json", "next.config.ts", "postcss.config.mjs")


def next_build(name: str, source: Path, timeout: int = 900) -> tuple[bool, str]:
    """`next build` of a whole generated app, once: (it built, the end of Next's output).

    The app is put in a project of its own inside the test app, output/test-app/c2-build/<name>/: the test
    app's configuration files and the generated tree as src/. Nothing is installed: Node finds the test app's
    node_modules one level up."""
    target = BUILD_DIR / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for file in BUILD_FILES:
        if (TEST_APP / file).exists():
            shutil.copyfile(TEST_APP / file, target / file)
    shutil.copytree(source, target / "src")
    next_bin = TEST_APP / "node_modules" / "next" / "dist" / "bin" / "next"
    try:
        run = subprocess.run([shutil.which("node"), str(next_bin), "build", str(target)], cwd=TEST_APP,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"next build did not finish in {timeout} s"
    text = (run.stdout + run.stderr).strip()
    return run.returncode == 0, "\n".join(text.splitlines()[-60:])
