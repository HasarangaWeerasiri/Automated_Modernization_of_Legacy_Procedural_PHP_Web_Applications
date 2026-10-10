"""Compile check for Stage 5's output (docs/generation-spec.md section 10).

Copies one mock's generated files into the pinned Next.js test app
(output/test-app/c2-check/<mock>/), writes a tsconfig there that extends the
app's own, and runs `npx tsc --noEmit` on it. Target: 0 errors for every mock.

Usage, from component2_frontend/ (after `npm ci` in output/test-app and a Stage 5 run):
    venv/Scripts/python.exe tools/compile_check.py            # every mock in output/generated
    venv/Scripts/python.exe tools/compile_check.py list_while # one mock
"""

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


def check(name: str, source: Path) -> CompileResult:
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
        if result.errors:
            failed += 1
            print(result.output)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
