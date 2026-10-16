"""Prepare the legacy side of the Phase 4e runtime check: phpBlog exactly as committed at its pinned commit.

Writes, under tools/runtime/phpblog/build/ (git-ignored):
  htdocs/                     a `git archive` of legacy-apps/phpblog at 4ffe3c9 (no file edited)
  initdb/01-database.sql      phpBlog's own install/database.sql from that commit, byte for byte
  config/config.php           \\ the two files phpBlog's installer writes, made by make_config.php exactly as
  config/config_settings.php  /  install/done.php makes them (22 and 49-53), from INPUTS below
and prints the sha256 of each. The config files are mounted into the web container; the source tree is not touched.

Usage, from component2_frontend/ (Docker running):
    venv/Scripts/python.exe tools/runtime/phpblog/prepare.py
then:  docker compose -f tools/runtime/phpblog/docker-compose.yml up -d --build
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # component2_frontend/
PHPBLOG = ROOT / "legacy-apps" / "phpblog"
COMMIT = "4ffe3c9641a42b99829cf4b8e4185093793a3abc"
BUILD = HERE / "build"
PHP_IMAGE = "php:8.2-apache"  # the web container's PHP (Dockerfile)
# What the installer's form and request would have given install/done.php (6-9, 11, 19): the database the
# docker-compose `db` service provides, the site served at http://127.0.0.1:18081/, and the repository's own
# default e-mail (config_settings.php). The admin password (done.php 12, 56) is not used: no user row is inserted.
INPUTS = {"database_host": "db", "database_name": "phpblog", "database_username": "root",
          "database_password": "phpblog", "email": "admin@mail.com",
          "http_host": "127.0.0.1:18081", "request_uri": "/install/done.php"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    kind = subprocess.run(["git", "-C", str(PHPBLOG), "cat-file", "-t", COMMIT], capture_output=True, text=True)
    if kind.stdout.strip() != "commit":
        print(f"legacy-apps/phpblog does not have commit {COMMIT} (legacy-apps/PINS.md)", file=sys.stderr)
        return 1
    if BUILD.exists():
        shutil.rmtree(BUILD)
    for folder in ("initdb", "config", "logs"):
        (BUILD / folder).mkdir(parents=True)
    archive = BUILD / "phpblog.tar"
    subprocess.run(["git", "-C", str(PHPBLOG), "archive", "--format=tar", "-o", str(archive), COMMIT], check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(BUILD / "htdocs", filter="data")
    print(f"htdocs: git archive of {COMMIT} (tar sha256 {sha(archive.read_bytes())})")
    archive.unlink()
    (BUILD / "htdocs" / "config.php").touch()  # the mount point of config/config.php (the file is mounted over it)
    schema = subprocess.run(["git", "-C", str(PHPBLOG), "show", f"{COMMIT}:install/database.sql"],
                            capture_output=True, check=True).stdout
    (BUILD / "initdb" / "01-database.sql").write_bytes(schema)
    print(f"initdb/01-database.sql: {len(schema)} bytes, sha256 {sha(schema)}")
    (BUILD / "config" / "inputs.json").write_text(json.dumps(INPUTS, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["docker", "run", "--rm", "-v", f"{BUILD.as_posix()}:/build", "-v",
                    f"{HERE.as_posix()}/make_config.php:/make_config.php:ro", PHP_IMAGE, "php", "/make_config.php",
                    "/build/htdocs", "/build/config", "/build/config/inputs.json"], check=True)
    for name in ("config.php", "config_settings.php"):
        data = (BUILD / "config" / name).read_bytes()
        print(f"config/{name}: {len(data)} bytes, sha256 {sha(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
