"""The freeze guard: with `--rules v1.0`, every output is byte-identical to the freeze commit 27c380e.

Two digests, the ones recorded in docs/post-freeze-log.md (Phase 4b, run from a worktree of the freeze commit):

  apps    the five discovery apps through tools/harness/run_apps.py (with tsc), written to output/apps/ (the path
          is part of each stage5.json, so it must be that folder): sha256 of the JSON map relative path -> sha256
          of every file, `*.next-build.json` left out (sort_keys, default separators)
  mocks   the ten mocks through Stages 1-5 with mocks/sample_contract.json and mocks/endpoint_map.json: every stage
          JSON and generated file in one JSON document (indent 1), CRLF line ends as the Phase 4b run wrote it

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/rules_guard.py mocks [--rules v1.0]
    venv/Scripts/python.exe tools/rules_guard.py apps [--rules v1.0]     # several minutes; rewrites output/apps
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # component2_frontend/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))

from src.mapping import loader  # noqa: E402
from src.mapping import stage1_isolation as stage1  # noqa: E402
from src.mapping import stage2_boundaries as stage2  # noqa: E402
from src.mapping import stage3_requirements as stage3  # noqa: E402
from src.mapping import stage4_reconcile as stage4  # noqa: E402
from src.mapping import stage5_generate as stage5  # noqa: E402
from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.generation_config import load_generation_config  # noqa: E402

FREEZE_COMMIT = "27c380e"
FREEZE_APPS = "ba8e4ac78db8ce08dadc2646337b66f38026cf7c5de837576fa750a4adae25d9"  # 2,079 files
FREEZE_MOCKS = "71b746eb26e657a0c0f729a69705563bd3b611b327a48d30aed6223f578c0cf6"
MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases",
         "wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
APPS_OUTPUT = ROOT / "output" / "apps"


def mocks_digest(rules: str | None = None) -> str:
    config, generation = load_boundary_config(rules=rules), load_generation_config()
    contract = loader.load_contract(ROOT / "mocks" / "sample_contract.json")
    endpoint_map = loader.load_endpoint_map(ROOT / "mocks" / "endpoint_map.json")
    out = {}
    for name in MOCKS:
        timeline = loader.load_timeline(ROOT / "mocks" / f"timeline_{name}.json")
        result1 = stage1.isolate_presentation(timeline, loader.load_labels(ROOT / "mocks" / f"labels_{name}.json"))
        tree = stage2.infer_boundaries(result1, timeline, config)
        requirements = stage3.recover_requirements(tree, timeline)
        report = stage4.reconcile(requirements, contract, endpoint_map)
        result = stage5.generate(tree, requirements, report, contract, timeline, config, generation)
        out[name] = {"1": stage1.dumps(result1), "2": stage2.dumps(tree), "3": stage3.dumps(requirements),
                     "4": stage4.dumps(report), "5": stage5.dumps(result, "x"),
                     "files": {f.path: f.text for f in result.files}}
    return hashlib.sha256(json.dumps(out, indent=1).replace("\n", "\r\n").encode("utf-8")).hexdigest()


def tree_digest(folder: Path = APPS_OUTPUT) -> tuple[str, dict]:
    """(digest, relative path -> sha256) of every output file of the apps, *.next-build.json left out."""
    hashes = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(folder.rglob("*")) if p.is_file() and not p.name.endswith(".next-build.json")}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), hashes


def apps_digest(rules: str | None = None) -> tuple[str, dict]:
    from app import APPS, app_timelines
    from run_apps import run_app

    version = load_boundary_config(rules=rules).rules_version
    for app in APPS:
        run_app(app_timelines(app, version), APPS_OUTPUT, tsc=True, build=False, rules=version)
    return tree_digest()


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("mocks", "apps"):
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    rules = argv[argv.index("--rules") + 1] if "--rules" in argv else None
    version = load_boundary_config(rules=rules).rules_version
    if argv[0] == "mocks":
        digest, expected = mocks_digest(rules), FREEZE_MOCKS
        print(f"mocks (10, Stages 1-5 + generated files), rules v{version}: sha256 {digest}")
    else:
        digest, hashes = apps_digest(rules)
        expected = FREEZE_APPS
        print(f"five apps, rules v{version}: {len(hashes)} files, sha256 {digest}")
    same = digest == expected
    print(f"freeze {FREEZE_COMMIT}: sha256 {expected} -> {'byte-identical' if same else 'DIFFERENT'}")
    return 0 if same or version != "1.0" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
