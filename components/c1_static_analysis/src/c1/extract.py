"""AST extraction driver (schema 1.0).

    python -m c1.extract <src-root> --out analysis-out

Walks <src-root>, runs the PHP extractor once per file, and writes:

    analysis-out/manifest.json
    analysis-out/ast/<fileId>.json

Determinism rules (checked by tests/test_determinism.py):
  * files are processed in byte-order of their relative POSIX path
  * file ids are assigned from that order: f000, f001, ...
  * every JSON file is written with sorted keys, no timestamps, no absolute paths
  * the parser version is read from the extractor, not guessed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__

EXTRACTOR = Path(__file__).resolve().parents[2] / "tools" / "php_ast" / "extract_ast.php"
DEFAULT_EXTENSIONS = (".php", ".inc")
DEFAULT_EXCLUDE_DIRS = (".git", "node_modules")
PHP_TIMEOUT_SECONDS = 120


class ExtractError(RuntimeError):
    """The PHP extractor failed on a file. Never swallowed: the run stops."""


def canonical_json(obj: Any, pretty: bool = False) -> bytes:
    """One serialisation used everywhere, so equal data is byte-equal."""
    if pretty:
        text = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1)
    else:
        text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_files(
    root: Path, extensions: tuple[str, ...], exclude_dirs: tuple[str, ...]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (relative POSIX paths of source files sorted by UTF-8 bytes, excluded dirs).

    A skipped directory is never silent: it is returned as {"dir", "files"} and written
    to manifest.json, because later stages must know that calls into it are unanalysed.
    """
    found: list[str] = []
    excluded: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        keep = []
        for d in sorted(dirnames):
            if d in exclude_dirs:
                skipped = Path(dirpath) / d
                count = sum(
                    1
                    for _, _, names in os.walk(skipped, followlinks=False)
                    for n in names
                    if n.lower().endswith(extensions)
                )
                if count:  # a folder that hid no PHP files is not worth recording
                    excluded.append({"dir": skipped.relative_to(root).as_posix(), "files": count})
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            if name.lower().endswith(extensions):
                found.append(full.relative_to(root).as_posix())
    key = lambda p: p.encode("utf-8", "surrogateescape")  # noqa: E731
    found.sort(key=key)
    excluded.sort(key=lambda e: key(e["dir"]))
    return found, excluded


def php_command(php: str | None) -> str:
    return php or os.environ.get("C1_PHP") or "php"


def parser_info(php: str) -> dict[str, str]:
    proc = subprocess.run(
        [php, str(EXTRACTOR), "--version"],
        capture_output=True, timeout=PHP_TIMEOUT_SECONDS, check=False,
    )
    if proc.returncode != 0:
        raise ExtractError(
            "Cannot run the PHP extractor. Is PHP installed and did you run `composer install`?\n"
            + proc.stderr.decode("utf-8", "replace")
        )
    return json.loads(proc.stdout)


def run_extractor(php: str, abs_path: Path, file_id: str) -> dict[str, Any]:
    proc = subprocess.run(
        [php, str(EXTRACTOR), "--file", str(abs_path), "--file-id", file_id],
        capture_output=True, timeout=PHP_TIMEOUT_SECONDS, check=False,
    )
    if proc.returncode != 0:
        raise ExtractError(f"extractor failed on {abs_path.name}: " + proc.stderr.decode("utf-8", "replace").strip())
    return json.loads(proc.stdout)


def extract_tree(
    src_root: Path,
    out_dir: Path,
    *,
    php: str | None = None,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS,
    pretty: bool = False,
) -> dict[str, Any]:
    """Run the extractor over a tree and write analysis-out/. Returns the manifest."""
    src_root = src_root.resolve()
    if not src_root.is_dir():
        raise ExtractError(f"not a directory: {src_root}")
    php_bin = php_command(php)
    parser = parser_info(php_bin)
    rel_paths, excluded = discover_files(src_root, extensions, exclude_dirs)

    ast_dir = out_dir / "ast"
    if ast_dir.exists():
        shutil.rmtree(ast_dir)  # no stale files from an earlier run
    ast_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()

    files: list[dict[str, Any]] = []
    total_nodes = 0
    failures = 0
    for index, rel in enumerate(rel_paths):
        file_id = f"f{index:03d}"
        abs_path = src_root / rel
        raw = abs_path.read_bytes()
        result = run_extractor(php_bin, abs_path, file_id)
        doc = {
            "schemaVersion": SCHEMA_VERSION,
            "fileId": file_id,
            "path": rel,
            "sha256": sha256_hex(raw),
            "bytes": len(raw),
            "parseOk": not result["parseErrors"],
            "parseErrors": result["parseErrors"],
            "rootId": result["rootId"],
            "nodeCount": len(result["nodes"]),
            "nodes": result["nodes"],
        }
        ast_bytes = canonical_json(doc, pretty)
        (ast_dir / f"{file_id}.json").write_bytes(ast_bytes)
        total_nodes += doc["nodeCount"]
        failures += 0 if doc["parseOk"] else 1
        files.append({
            "fileId": file_id,
            "path": rel,
            "sha256": doc["sha256"],
            "bytes": doc["bytes"],
            "nodeCount": doc["nodeCount"],
            "parseOk": doc["parseOk"],
            "parseErrorCount": len(doc["parseErrors"]),
            "ast": f"ast/{file_id}.json",
            "astSha256": sha256_hex(ast_bytes),
        })

    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "tool": {"name": "c1-static-analysis-core", "version": __version__},
        "parser": parser,
        "extensions": sorted(extensions),
        "excluded": excluded,
        "files": files,
        "totals": {"files": len(files), "nodes": total_nodes, "parseFailures": failures},
    }
    # Hash of everything above. labels.json uses it as `generatedFor`.
    manifest["contentHash"] = sha256_hex(canonical_json(manifest))
    manifest_path.write_bytes(canonical_json(manifest, pretty=True))
    return manifest


def warn_if_dominated(manifest: dict[str, Any], share: float = 0.5) -> None:
    """Warn when one folder holds most nodes: usually a vendored library (e.g. TCPDF)."""
    total = manifest["totals"]["nodes"]
    if total == 0:
        return
    per_dir: dict[str, int] = {}
    for f in manifest["files"]:
        top = f["path"].split("/", 1)[0] if "/" in f["path"] else "(root)"
        per_dir[top] = per_dir.get(top, 0) + f["nodeCount"]
    top_dir, n = max(sorted(per_dir.items()), key=lambda kv: kv[1])
    if top_dir != "(root)" and n / total >= share:
        print(
            f"warning: {top_dir}/ holds {n / total:.0%} of all nodes. If it is a vendored library, "
            f"re-run with --exclude-dir {top_dir}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.extract", description=__doc__.split("\n")[0])
    ap.add_argument("src_root", type=Path, help="root folder of the legacy PHP app")
    ap.add_argument("--out", type=Path, default=Path("analysis-out"), help="output folder (default: analysis-out)")
    ap.add_argument("--php", help="php binary (default: $C1_PHP or `php`)")
    ap.add_argument("--ext", action="append", help="file extension to include, repeatable (default: .php .inc)")
    ap.add_argument("--exclude-dir", action="append", help="directory name to skip, repeatable (default: .git node_modules)")
    ap.add_argument("--pretty", action="store_true", help="indent AST files (bigger, easier to read)")
    args = ap.parse_args(argv)

    exts = tuple(e if e.startswith(".") else "." + e for e in args.ext) if args.ext else DEFAULT_EXTENSIONS
    excl = tuple(args.exclude_dir) if args.exclude_dir else DEFAULT_EXCLUDE_DIRS
    try:
        m = extract_tree(args.src_root, args.out, php=args.php, extensions=exts, exclude_dirs=excl, pretty=args.pretty)
    except ExtractError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    t = m["totals"]
    print(f"{t['files']} files, {t['nodes']} nodes, {t['parseFailures']} with parse errors -> {args.out}")
    for e in m["excluded"]:
        print(f"  excluded: {e['dir']}/ ({e['files']} files not analysed)", file=sys.stderr)
    warn_if_dominated(m)
    for f in m["files"]:
        if not f["parseOk"]:
            print(f"  parse errors: {f['path']} ({f['parseErrorCount']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
