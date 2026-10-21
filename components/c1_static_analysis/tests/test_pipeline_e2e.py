"""Every stage after extract, end to end, from cached ASTs (runs without PHP)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from c1.__main__ import analyse
from c1.extract import canonical_json
from conftest import php_ast

APP = {
    "index.php": "<?php\nsession_start();\nif (!isset($_SESSION['uid'])) { header('Location: login.php'); exit; }\n"
                 "include 'db.php';\n$r = mysqli_query($con, 'select name from items');\n"
                 "while ($row = mysqli_fetch_array($r)) { echo $row['name']; }\n",
    "db.php": "<?php\n$con = mysqli_connect('h', 'u', 'p', 'd');\n",
    "add.php": "<?php\ninclude 'db.php';\nif (isset($_POST['save'])) {\n  $n = $_POST['n'];\n"
               "  mysqli_query($con, \"insert into items(name) values('$n')\");\n}\n?>\n<form method=post><input name=n></form>\n",
    "schema.sql": "CREATE TABLE `items` (`name` varchar(40) NOT NULL) ENGINE=InnoDB;\n",
}


def make_out(tmp: Path, name: str) -> tuple[Path, Path]:
    src = tmp / f"src_{name}"
    out = tmp / f"out_{name}"
    (out / "ast").mkdir(parents=True)
    src.mkdir()
    files = []
    php = sorted(p for p in APP if p.endswith(".php"))
    for i, path in enumerate(php):
        (src / path).write_text(APP[path], encoding="utf-8")
        doc = php_ast(APP[path], f"f{i:03d}", path)
        doc = {"schemaVersion": "1.0", "sha256": hashlib.sha256(APP[path].encode()).hexdigest(),
               "bytes": len(APP[path].encode()), "parseOk": True, "nodeCount": len(doc["nodes"]), **doc}
        (out / "ast" / f"f{i:03d}.json").write_bytes(canonical_json(doc))
        files.append({"fileId": f"f{i:03d}", "path": path, "ast": f"ast/f{i:03d}.json"})
    (src / "schema.sql").write_text(APP["schema.sql"], encoding="utf-8")
    manifest = {"schemaVersion": "1.0", "files": files, "excluded": [], "totals": {"files": len(files)}}
    manifest["contentHash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    (out / "manifest.json").write_bytes(canonical_json(manifest, pretty=True))
    return src, out


def test_all_stages_run_and_agree(tmp_path):
    src, out = make_out(tmp_path, "a")
    analyse(out, src, "E2E", [])
    for f in ("labels.json", "auth.json", "endpoints.json", "endpoint-map.json", "flags.json",
              "contract/openapi.json", "backend/app/routes.py", "timeline/index.json", "report.html",
              "metrics.json", "architecture.json"):
        assert (out / f).exists(), f
    auth = json.loads((out / "auth.json").read_text(encoding="utf-8"))
    status = {p["page"]: p["status"] for p in auth["pages"]}
    assert status["index.php"] == "guarded" and status["add.php"] == "unguarded"
    oa = json.loads((out / "contract" / "openapi.json").read_text(encoding="utf-8"))
    assert set(oa["paths"]) == {"/api/items"}
    assert oa["paths"]["/api/items"]["get"]["security"] == [{"bearerAuth": []}]
    assert "security" not in oa["paths"]["/api/items"]["post"]
    report = (out / "report.html").read_text(encoding="utf-8")
    assert "add.php" in report and "unguarded" in report


def test_whole_pipeline_is_byte_identical_across_runs_and_folders(tmp_path):
    src_a, out_a = make_out(tmp_path, "a")
    src_b, out_b = make_out(tmp_path, "b")
    analyse(out_a, src_a, "E2E", [])
    analyse(out_b, src_b, "E2E", [])
    files_a = sorted(p.relative_to(out_a) for p in out_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(out_b) for p in out_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), rel
