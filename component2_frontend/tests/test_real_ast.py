"""Member 01's first real AST (roadmap Phase 1b): legacy-apps/real/f001_admin-panel1_php_ast.json.

HMS `admin-panel1.php` at `777fda4`, 602 nodes, schema 1.0 (legacy-apps/PINS.md). The loader must
read it, and the harness's AST producer must reproduce it exactly: if they differ, Member 01's
file is right and the harness changes. The timeline builder must give the same timeline from
either AST.

The file's own sha256 starts `da42837a`; the `sha256` in its header is that of the PHP source.
Every test here skips, with the reason, if the file is not in the repo.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import app as harness_app  # noqa: E402
from ast_producer import SourceFile, write_asts  # noqa: E402
from harness import FileHarness  # noqa: E402

from src.mapping.loader import load_ast  # noqa: E402

REAL_AST = ROOT / "legacy-apps" / "real" / "f001_admin-panel1_php_ast.json"
SOURCE_SHA256 = "492d63d991bcb083e9f08769f10e08cd53edf3844ec437b8ba9e9a1dc6e8b3e0"
HMS = harness_app.APPS["hms"]

pytestmark = [
    pytest.mark.skipif(not REAL_AST.exists(),
                       reason=f"Member 01's real AST is not in the repo: put it at {REAL_AST.relative_to(ROOT)}"),
    pytest.mark.skipif(not (HMS["root"] / ".git").exists(), reason="HMS clone not present"),
]


@pytest.fixture(scope="module")
def source() -> bytes:
    """admin-panel1.php exactly as committed at the pinned HMS commit."""
    return subprocess.run(["git", "-C", str(HMS["root"]), "show", f"{HMS['commit']}:admin-panel1.php"],
                          capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def real() -> dict:
    return json.loads(REAL_AST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ours_path(real, source, tmp_path_factory) -> Path:
    """The harness's AST file for the same bytes, given the real file's own fileId and path."""
    if harness_app.unavailable():
        pytest.skip(f"harness skipped: {harness_app.unavailable()}")
    work = tmp_path_factory.mktemp("ours")
    (work / "admin-panel1.php").write_bytes(source)
    (path,), _ = write_asts([SourceFile(work / "admin-panel1.php", real["fileId"], real["path"])], work / "ast")
    return path


@pytest.fixture(scope="module")
def ours(ours_path) -> dict:
    return json.loads(ours_path.read_text(encoding="utf-8"))


def test_the_loader_reads_the_real_ast(source):
    ast = load_ast(REAL_AST)
    assert (ast.schema_version, ast.parse_ok, ast.node_count, len(ast.nodes)) == ("1.0", True, 602, 602)
    assert ast.sha256 == SOURCE_SHA256 == hashlib.sha256(source).hexdigest() and ast.bytes == len(source)
    assert ast.root.kind == "File" and ast.root.id == f"{ast.file_id}#00000"


def test_every_loc_of_the_real_ast_slices_the_right_bytes(source):
    ast = load_ast(REAL_AST)
    for node in ast.nodes.values():
        loc = node.loc
        text = source[loc.start_byte:loc.end_byte]
        assert loc.start_line == source.count(b"\n", 0, loc.start_byte) + 1, node.id
        assert loc.start_col == loc.start_byte - (source.rfind(b"\n", 0, loc.start_byte) + 1), node.id
        assert loc.end_line == source.count(b"\n", 0, loc.end_byte) + 1, node.id
        assert loc.end_col == loc.end_byte - (source.rfind(b"\n", 0, loc.end_byte) + 1), node.id
        if node.kind == "Stmt_InlineHTML":
            assert text.decode("utf-8") == node.attrs["value"], node.id
        if node.kind == "Expr_Variable" and isinstance(node.attrs.get("name"), str):
            assert text.decode("utf-8") == "$" + node.attrs["name"], node.id
        if "_rawValue" in node.attrs:
            assert text.decode("utf-8") == node.attrs["_rawValue"], node.id
        for child in node.children:  # a child lies inside its parent
            inner = ast.nodes[child].loc
            assert loc.start_byte <= inner.start_byte <= inner.end_byte <= loc.end_byte, child


def test_the_harness_reproduces_the_real_ast_exactly(real, ours):
    """The acceptance test of Phase 1b: all 602 nodes and the file header, compared field by field."""
    header = [key for key in real if key != "nodes"]
    assert {key: ours.get(key) for key in header} == {key: real[key] for key in header}
    assert list(ours) == list(real)  # the same keys, in the same order
    assert len(ours["nodes"]) == len(real["nodes"]) == 602
    differences = []
    for mine, theirs in zip(ours["nodes"], real["nodes"]):
        for key in ("id", "kind", "parent", "children", "fields", "attrs", "loc"):
            if mine.get(key) != theirs.get(key):
                differences.append((theirs["id"], theirs["kind"], key, theirs.get(key), mine.get(key)))
        if set(mine) != set(theirs):
            differences.append((theirs["id"], theirs["kind"], "keys", sorted(theirs), sorted(mine)))
    kinds = sorted({(kind, key) for _, kind, key, _, _ in differences})
    assert not differences, f"{len(differences)} differences in {kinds}; first: {differences[:5]}"


def test_the_harness_writes_the_same_bytes_as_the_real_file(ours_path):
    """Stronger than the node comparison: the same key order, spacing and escaping, so the files are identical."""
    assert ours_path.read_bytes() == REAL_AST.read_bytes()
    assert hashlib.sha256(REAL_AST.read_bytes()).hexdigest().startswith("da42837a")


def test_what_the_real_file_shows_about_the_format(real):
    """The facts the harness's format was corrected to (roadmap Phase 1b)."""
    nodes = real["nodes"]
    assert list(real) == sorted(real) and all(list(n) == sorted(n) for n in nodes)  # keys in sorted order
    assert all(set(n) == {"attrs", "children", "fields", "id", "kind", "loc", "parent"} for n in nodes)
    absent = [(n["kind"], role) for n in nodes for role, value in n["fields"].items() if value is None]
    assert sorted(set(absent)) == [("Arg", "name"), ("Stmt_If", "else")] and len(absent) == 49  # null is a role
    assert not [n["id"] for n in nodes if None in n["attrs"].values()]
    assert sum("_kind" in n["attrs"] for n in nodes) == 98  # nikic's `kind` attribute, next to `_rawValue`
    assert {key for n in nodes for key in n["attrs"]} == {"_kind", "_rawValue", "name", "value", "byRef", "unpack",
                                                           "type"}
    assert real["parseErrors"] == []  # so the shape of a parse error item is still not known
    echoes = [n for n in nodes if n["kind"] == "Stmt_Echo"]
    assert len(echoes) == 24  # every one ends with ";": this file cannot show an echo closed by "?>"


def test_the_timeline_builder_gives_the_same_timeline_from_either_ast(real, ours, source, tmp_path):
    def timeline(doc: dict, name: str) -> dict:
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        harness = FileHarness(source, load_ast(path), "legacy-apps/hms/admin-panel1.php",
                              harness_app.commit_schema("hms"))
        return harness.timeline(harness.top, {"generator": "c2-harness"})

    from_real, from_ours = timeline(real, "real.json"), timeline(ours, "ours.json")
    assert from_real == from_ours
    # 49 output entries, and since Phase 3 its one include (`include('newfunc.php')`) as an entry of its own
    assert [e["kind"] for e in from_real["sequence"]].count("include") == 1
    assert len(from_real["sequence"]) == 50 and from_real["sequence"][0]["id"] == f"{real['fileId']}#00001"


def test_the_list_while_mock_uses_the_real_asts_ids(real):
    """The mock is an excerpt of this file: every id in it must be a node of Member 01's AST, of the same kind."""
    nodes = {node["id"]: node for node in real["nodes"]}
    mock = json.loads((ROOT / "mocks" / "timeline_list_while.json").read_text(encoding="utf-8"))
    for entry in mock["sequence"]:
        assert nodes[entry["id"]]["kind"] == entry["kind"], entry["id"]
        assert nodes[entry["id"]]["loc"] == entry["loc"], entry["id"]
        for enclosure in entry["enclosedBy"]:
            assert nodes[enclosure["nodeId"]]["kind"] == enclosure["kind"], enclosure["nodeId"]
