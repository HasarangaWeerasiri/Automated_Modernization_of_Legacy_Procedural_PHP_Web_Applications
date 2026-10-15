"""Prepare the legacy side of the Phase 4 runtime check: HMS exactly as committed at its pinned commit.

Writes, under tools/runtime/hms/build/ (git-ignored):
  htdocs/                    a `git archive` of legacy-apps/hms at 777fda4 (NOT the patched working copy)
  initdb/01-myhmsdb.sql      HMS's own myhmsdb.sql from that commit, byte for byte
and prints the sha256 of both, so the README / report can record them. No HMS file is edited.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/runtime/hms/prepare.py
then:  docker compose -f tools/runtime/hms/docker-compose.yml up -d
"""

import hashlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # component2_frontend/
HMS = ROOT / "legacy-apps" / "hms"
COMMIT = "777fda46b77a820977a5ba616283dbfbc40bf7e1"
BUILD = HERE / "build"


def main() -> int:
    head = subprocess.run(["git", "-C", str(HMS), "cat-file", "-t", COMMIT], capture_output=True, text=True)
    if head.stdout.strip() != "commit":
        print(f"legacy-apps/hms does not have commit {COMMIT} (legacy-apps/PINS.md)", file=sys.stderr)
        return 1
    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "initdb").mkdir(parents=True)
    archive = BUILD / "hms.tar"
    subprocess.run(["git", "-C", str(HMS), "archive", "--format=tar", "-o", str(archive), COMMIT], check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(BUILD / "htdocs", filter="data")
    tar_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.unlink()
    schema = subprocess.run(["git", "-C", str(HMS), "show", f"{COMMIT}:myhmsdb.sql"], capture_output=True,
                            check=True).stdout
    (BUILD / "initdb" / "01-myhmsdb.sql").write_bytes(schema)
    print(f"htdocs: git archive of {COMMIT} (tar sha256 {tar_sha})")
    print(f"initdb/01-myhmsdb.sql: {len(schema)} bytes, sha256 {hashlib.sha256(schema).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
