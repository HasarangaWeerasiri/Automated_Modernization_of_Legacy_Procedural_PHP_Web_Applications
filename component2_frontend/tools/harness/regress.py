"""Regression check: the harness against the existing generated mocks (roadmap Phase 1, brief section 2).

For each mock made from a real source file, the harness runs on that file with the mock's file id,
and every sequence entry of the mock is compared with the harness entry of the same id: kind, raw,
loc, enclosedBy (incl. condExpr and loc) and reads. Both sides use Member 01's AST node ids. list_foreach, admin and edge_cases are rewrites or
hand-written, so they are not compared.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/harness/regress.py
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import APPS, ROOT, SourceFile, commit_schema, parse_sources  # noqa: E402
from harness import FileHarness  # noqa: E402

MOCKS = {  # mock -> (app, file, function, the mock's file id)
    "list_while": ("hms", "admin-panel1.php", None, "f001"),
    "detail": ("hms", "admin-panel.php", None, "f004"),
    "wp_guestbook": ("wackopicko", "website/guestbook.php", None, "f006"),
    "wp_view": ("wackopicko", "website/pictures/view.php", None, "f007"),
    "wp_login": ("wackopicko", "website/users/login.php", None, "f008"),
    "wp_header": ("wackopicko", "website/include/html_functions.php", "our_header", "f009"),
    "wp_thumbnails": ("wackopicko", "website/include/html_functions.php", "thumbnail_pic_list", "f009"),
}
FIELDS = ("kind", "raw", "loc", "enclosedBy", "reads")
# Harness extensions on enclosures that the hand-made mocks predate (R-I8 `exits`, R-L4 `fetchNodeId`)
ENCLOSURE_EXTENSIONS = ("exits", "fetchNodeId")


def source_bytes(app: str, path: str) -> bytes:
    config = APPS[app]
    return subprocess.run(["git", "-C", str(config["root"]), "show", f"{config['commit']}:{path}"],
                          capture_output=True, check=True).stdout


def harness_timeline(mock: str, work: Path) -> dict:
    app, path, function, file_id = MOCKS[mock]
    source = work / f"{mock}.php"
    source.write_bytes(source_bytes(app, path))
    entrypoint = f"{APPS[app]['prefix']}/{path}"
    (ast,), _ = parse_sources([SourceFile(source, file_id, entrypoint)])
    harness = FileHarness(source.read_bytes(), ast, entrypoint, commit_schema(app))
    scope = harness.top if function is None else harness.functions[function]
    return harness.timeline(scope, {"generator": "c2-harness"})


def compare(mock_doc: dict, harness_doc: dict) -> list[dict]:
    """Every difference between the mock's entries and the harness entries of the same id.

    Call entries are left out: the mocks do not have them."""
    ours = {entry["id"]: entry for entry in harness_doc["sequence"]}
    differences = []
    for entry in mock_doc["sequence"]:
        other = ours.get(entry["id"])
        if other is None:
            differences.append({"id": entry["id"], "field": "(entry)", "mock": entry["kind"], "harness": "missing"})
            continue
        for name in FIELDS:
            value = other.get(name)
            if name == "enclosedBy" and value is not None:
                value = [{k: v for k, v in e.items() if k not in ENCLOSURE_EXTENSIONS} for e in value]
            if entry.get(name) != value:
                differences.append({"id": entry["id"], "field": name, "mock": entry.get(name),
                                    "harness": other.get(name)})
    lines = [entry["loc"]["startLine"] for entry in mock_doc["sequence"]]
    known = {entry["id"] for entry in mock_doc["sequence"]}
    for entry in harness_doc["sequence"]:
        if entry["kind"] != "call" and entry["id"] not in known \
                and min(lines) <= entry["loc"]["startLine"] <= max(lines):
            differences.append({"id": entry["id"], "field": "(entry)", "mock": "missing", "harness": entry["kind"]})
    return differences


def run(work: Path) -> dict[str, list[dict]]:
    out = {}
    for mock in MOCKS:
        mock_doc = json.loads((ROOT / "mocks" / f"timeline_{mock}.json").read_text(encoding="utf-8"))
        out[mock] = compare(mock_doc, harness_timeline(mock, work))
    return out


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        results = run(Path(work))
    for mock, differences in results.items():
        print(f"{mock}: {len(differences)} differences")
        for d in differences:
            print(f"  {d['id']} {d['field']}\n      mock:    {json.dumps(d['mock'])[:300]}\n"
                  f"      harness: {json.dumps(d['harness'])[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
