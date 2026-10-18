"""Include resolver.

    python -m c1.includes analysis-out      # needs ast/ and cfg/; writes index/includes.json

Method (Hills, Klint & Vinju, ASE 2014, "Static, Lightweight Includes Resolution for
PHP"): fold the include expression into a path; when parts stay unknown, match the
known parts against the app's real file list and accept a *unique* match.

PHP path rules (PHP manual, `include`):
* a path starting with `/` (or a drive letter), `./` or `../` ignores include_path;
  `./` and `../` are relative to the current working directory;
* any other relative path is tried against include_path (default ".", the current
  working directory), then "the calling script's own directory and the current
  working directory".
* Under a web server the working directory is the directory of the requested page,
  so the same include can resolve differently for different entry pages. We
  therefore resolve per entry page (`entries`), and also once with the including
  file's own directory (`sites[].self`).

Sound-or-abstain: every site gets a status. `resolved` needs a single target.
`ambiguous` lists candidates, and `dynamic` / `unresolved` / `missing` / `excluded`
carry a reason. Nothing is guessed.
"""

from __future__ import annotations

import argparse
import base64
import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .astdoc import AstDoc
from .extract import canonical_json

INCLUDE_TYPES = {1: "include", 2: "include_once", 3: "require", 4: "require_once"}
SUPERGLOBALS = frozenset({"_GET", "_POST", "_REQUEST", "_COOKIE", "_FILES", "_SERVER", "_SESSION"})
USER_INPUT = frozenset({"_GET", "_POST", "_REQUEST", "_COOKIE", "_FILES"})

WILD = object()  # an unknown part of a path


def _str_value(v: Any) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and "__b64" in v:
        return base64.b64decode(v["__b64"]).decode("latin-1")
    return None


class PathFolder:
    """Folds an include expression into a list of parts: str literals and WILD."""

    def __init__(self, ast: AstDoc, constants: dict[str, str], unit: str | None = None):
        self.ast = ast
        self.constants = constants
        self.unit = unit or ast.root_id  # variables are only folded inside this unit
        self.notes: list[str] = []
        self.user_input = False
        self._assign_cache: dict[str, list[str]] | None = None

    def fold(self, nid: str, depth: int = 0) -> list[Any]:
        a = self.ast
        k = a.kind(nid)
        if depth > 20:
            return [WILD]
        if k == "Scalar_String":
            v = _str_value(a.attr(nid, "value"))
            return [v] if v is not None else [WILD]
        if k == "Scalar_InterpolatedString":
            out: list[Any] = []
            for p in a.field_list(nid, "parts"):
                if a.kind(p) == "InterpolatedStringPart":
                    out.append(_str_value(a.attr(p, "value")) or "")
                else:
                    out += self.fold(p, depth + 1)
            return out
        if k == "Expr_BinaryOp_Concat":
            return self.fold(a.field(nid, "left"), depth + 1) + self.fold(a.field(nid, "right"), depth + 1)
        if k == "Scalar_MagicConst_Dir":
            self.notes.append("__DIR__")
            return ["/" + posixpath.dirname(a.path)]  # "/" marks the app root
        if k == "Scalar_MagicConst_File":
            return ["/" + a.path]
        if k == "Expr_FuncCall" and a.call_name(nid) == "dirname":
            args = a.field_list(nid, "args")
            inner = self.fold(a.field(args[0], "value"), depth + 1) if args else [WILD]
            levels = 1
            if len(args) > 1:
                lv = a.field(args[1], "value")
                levels = a.attr(lv, "value") if a.kind(lv) == "Scalar_Int" else None
            if any(p is WILD for p in inner) or not isinstance(levels, int):
                return [WILD]
            path = "".join(inner)
            for _ in range(levels):
                path = posixpath.dirname(path)
            return [path]
        if k == "Expr_ConstFetch":
            name = a.name_of(a.field(nid, "name"))
            if name and name in self.constants:
                self.notes.append(f"const:{name}")
                return [self.constants[name]]
            return [WILD]
        if k == "Expr_ArrayDimFetch":
            var = a.field(nid, "var")
            if a.kind(var) == "Expr_Variable" and a.attr(var, "name") in SUPERGLOBALS:
                sg = a.attr(var, "name")
                dim = a.field(nid, "dim")
                if sg == "_SERVER" and dim and _str_value(a.attr(dim, "value")) == "DOCUMENT_ROOT":
                    self.notes.append("assumed:DOCUMENT_ROOT=app-root")
                    return ["/"]
                if sg in USER_INPUT:
                    self.user_input = True
            return [WILD]
        if k == "Expr_Variable":
            name = a.attr(nid, "name")
            if not isinstance(name, str):
                return [WILD]
            defs = self.assignments().get(name, [])
            if len(defs) == 1:
                self.notes.append(f"single-assignment:${name}")
                return self.fold(defs[0], depth + 1)
            for d in defs:  # do not resolve, but remember if user input flows in
                self.fold(d, depth + 1)
            return [WILD]
        return [WILD]

    def assignments(self) -> dict[str, list[str]]:
        """Plain `$x = <expr>` assignments in the include's own unit, by variable name.

        Only used to fold include paths (Hills et al. fold simple variable values too).
        A variable also written any other way (compound assignment, by reference,
        foreach, parameter, list(), global) is marked unfoldable.
        """
        if self._assign_cache is not None:
            return self._assign_cache
        a = self.ast
        plain: dict[str, list[str]] = {}
        unfoldable: set[str] = set()

        def var_name(v: str | None) -> str | None:
            if v and a.kind(v) == "Expr_Variable" and isinstance(a.attr(v, "name"), str):
                return a.attr(v, "name")
            return None

        for nid in a.walk(self.unit):
            n = a.nodes[nid]
            k = n["kind"]
            f = n["fields"]
            if k == "Expr_Assign":
                name = var_name(f.get("var"))
                if name:
                    plain.setdefault(name, []).append(f["expr"])
                else:  # list()/[...] destructuring writes several variables
                    for x in a.walk(f.get("var")) if f.get("var") else ():
                        if var_name(x):
                            unfoldable.add(var_name(x))
            elif k.startswith("Expr_AssignOp_") or k == "Expr_AssignRef":
                if var_name(f.get("var")):
                    unfoldable.add(var_name(f.get("var")))
            elif k == "Stmt_Foreach":
                for key in ("keyVar", "valueVar"):
                    if var_name(f.get(key)):
                        unfoldable.add(var_name(f.get(key)))
            elif k in ("Param", "Stmt_Global", "Stmt_Static"):
                for x in a.walk(nid):
                    if var_name(x):
                        unfoldable.add(var_name(x))
        out = {name: defs for name, defs in plain.items() if name not in unfoldable}
        for name in unfoldable:
            out[name] = []  # known to be written in a way we do not fold
        self._assign_cache = out
        return out


def collect_constants(asts: list[AstDoc]) -> dict[str, str]:
    """define('NAME', '<literal>') and `const NAME = '<literal>'` with one value app-wide."""
    seen: dict[str, set[str]] = {}
    for a in asts:
        for n in a.doc["nodes"]:
            if n["kind"] == "Expr_FuncCall" and a.call_name(n["id"]) == "define":
                args = n["fields"]["args"]
                if len(args) >= 2:
                    name_n, val_n = a.field(args[0], "value"), a.field(args[1], "value")
                    name = _str_value(a.attr(name_n, "value")) if a.kind(name_n) == "Scalar_String" else None
                    if name is None:
                        continue
                    folder = PathFolder(a, {})
                    parts = folder.fold(val_n)
                    val = None if any(p is WILD for p in parts) else "".join(parts)
                    seen.setdefault(name, set()).add(val if val is not None else "\0dynamic")
            elif n["kind"] == "Const":
                parent = a.parent(n["id"])
                if a.kind(parent) != "Stmt_Const":
                    continue
                name = a.name_of(n["fields"].get("name"))
                parts = PathFolder(a, {}).fold(n["fields"]["value"])
                if name:
                    val = None if any(p is WILD for p in parts) else "".join(parts)
                    seen.setdefault(name, set()).add(val if val is not None else "\0dynamic")
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1 and "\0dynamic" not in v}


def norm(path: str) -> str | None:
    """Normalise an app-relative path. None when it escapes the app root."""
    parts: list[str] = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(p)
    return "/".join(parts)


class Resolver:
    def __init__(self, files: list[str], excluded_dirs: list[str]):
        self.files = set(files)
        self.lower = {}
        for f in files:
            self.lower.setdefault(f.lower(), []).append(f)
        self.excluded = [d.rstrip("/") + "/" for d in excluded_dirs]

    def lookup(self, rel: str | None) -> tuple[str, str] | None:
        """(status, path) for a fully known app-relative path, or None if absent."""
        if rel is None:
            return None
        if rel in self.files:
            return ("resolved", rel)
        if any(rel.startswith(d) for d in self.excluded):
            return ("excluded", rel)
        alts = self.lower.get(rel.lower(), [])
        if len(alts) == 1:
            return ("resolved-case-insensitive", alts[0])
        return None

    def literal(self, path: str, cwd: str, caller_dir: str) -> dict[str, Any]:
        """Resolve a fully known path string, following the PHP manual's order."""
        if path.startswith("/"):  # app-root anchored (from __DIR__ / DOCUMENT_ROOT) or absolute
            hit = self.lookup(norm(path))
            return self._result(hit, "absolute", path)
        if path.startswith("./") or path.startswith("../") or path in (".", ".."):
            hit = self.lookup(norm(posixpath.join(cwd, path)))
            return self._result(hit, "cwd-relative", path)
        # include_path "." (= cwd), then the calling script's directory, then cwd
        for base, how in ((cwd, "include_path"), (caller_dir, "caller-dir")):
            hit = self.lookup(norm(posixpath.join(base, path)))
            if hit:
                return self._result(hit, how, path)
        return self._result(None, "relative", path)

    def _result(self, hit, how, path) -> dict[str, Any]:
        if hit is None:
            return {"status": "missing", "basis": how, "path": path}
        status, target = hit
        r = {"status": "resolved" if status != "excluded" else "excluded", "basis": how, "target": target}
        if status == "resolved-case-insensitive":
            r["basis"] = how + "+case-insensitive"
        return r

    def pattern(self, parts: list[Any]) -> dict[str, Any]:
        """Hills et al.: match the known fragments against the real files; accept one match."""
        rx = ""
        for p in parts:
            rx += ".*" if p is WILD else re.escape(p)
        literal_chars = sum(len(p) for p in parts if p is not WILD)
        shown = "".join("*" if p is WILD else p for p in parts)
        if literal_chars == 0:
            return {"status": "dynamic", "basis": "no-literal-part", "pattern": shown}
        # a leading literal like "../include/" is relative; match on the path tail
        rx = re.sub(r"^(?:\\\.\\\./|\\\./)+", "", rx).lstrip("/")
        rx_full = re.compile(r"(?:.*/)?" + rx + r"\Z", re.S)
        cands = sorted(f for f in self.files if rx_full.match(f))
        if len(cands) == 1:
            return {"status": "resolved", "basis": "pattern-unique", "target": cands[0], "pattern": shown}
        if cands:
            return {"status": "ambiguous", "basis": "pattern", "candidates": cands, "pattern": shown}
        return {"status": "unresolved", "basis": "pattern-no-match", "pattern": shown}


def resolve_site(resolver: Resolver, folder_parts: list[Any], cwd: str, caller_dir: str) -> dict[str, Any]:
    if all(p is not WILD for p in folder_parts):
        return resolver.literal("".join(folder_parts), cwd, caller_dir)
    return resolver.pattern(folder_parts)


def build_includes(asts: list[AstDoc], cfgs: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    files = [f["path"] for f in manifest["files"]]
    by_path = {f["path"]: f["fileId"] for f in manifest["files"]}
    resolver = Resolver(files, [e["dir"] for e in manifest.get("excluded", [])])
    constants = collect_constants(asts)

    # include sites from the CFG (so each site also knows its unit and block)
    site_block: dict[str, tuple[str, str]] = {}
    for cfg in cfgs:
        for u in cfg["units"]:
            for s in u["includeSites"]:
                site_block[s["node"]] = (u["id"], s["block"])

    sites: list[dict[str, Any]] = []
    folded: dict[str, tuple[list[Any], PathFolder]] = {}
    for a in asts:
        for n in a.doc["nodes"]:
            if n["kind"] != "Expr_Include":
                continue
            unit, block = site_block.get(n["id"], (None, None))
            folder = PathFolder(a, constants, unit)
            parts = folder.fold(n["fields"]["expr"])
            folded[n["id"]] = (parts, folder)
            caller_dir = posixpath.dirname(a.path)
            res = resolve_site(resolver, parts, caller_dir, caller_dir)
            if folder.user_input:
                res = {"status": "dynamic", "basis": "user-input-in-path",
                       "pattern": "".join("*" if p is WILD else p for p in parts),
                       "reason": "include path depends on request input (front-controller style): out of scope, flagged"}
            site = {
                "site": n["id"], "fileId": a.file_id, "path": a.path, "line": a.line(n["id"]),
                "type": INCLUDE_TYPES.get(a.attr(n["id"], "type"), "include"),
                "unit": unit, "block": block, "self": res,
            }
            if folder.notes:
                site["notes"] = sorted(set(folder.notes))
            if "target" in res:
                site["self"]["targetFileId"] = by_path.get(res["target"])
            sites.append(site)

    # per-entry closure: the working directory is the entry page's directory
    included_by: dict[str, set[str]] = {}
    for s in sites:
        if s["self"]["status"] == "resolved":
            included_by.setdefault(s["self"]["target"], set()).add(s["path"])
    sites_by_file: dict[str, list[dict[str, Any]]] = {}
    for s in sites:
        sites_by_file.setdefault(s["path"], []).append(s)

    entries: dict[str, Any] = {}
    for a in asts:
        if a.path in included_by:
            continue  # included somewhere: not treated as its own page
        cwd = posixpath.dirname(a.path)
        order = [a.path]
        edges = []
        problems = []
        queue = [a.path]
        while queue:
            cur = queue.pop(0)
            for s in sites_by_file.get(cur, []):
                parts, _ = folded[s["site"]]
                res = resolve_site(resolver, parts, cwd, posixpath.dirname(cur)) \
                    if s["self"]["status"] != "dynamic" else s["self"]
                if res["status"] == "resolved":
                    edges.append({"site": s["site"], "from": cur, "to": res["target"]})
                    if res["target"] not in order:
                        order.append(res["target"])
                        queue.append(res["target"])
                else:
                    problems.append({"site": s["site"], "from": cur, "status": res["status"],
                                     **{k: v for k, v in res.items() if k in ("pattern", "path", "target", "candidates", "reason")}})
        entries[a.file_id] = {
            "path": a.path, "cwd": cwd or ".",
            "closure": [by_path[p] for p in order],
            "edges": edges,
            "problems": problems,
        }

    counts: dict[str, int] = {}
    for s in sites:
        counts[s["self"]["status"]] = counts.get(s["self"]["status"], 0) + 1
    return {
        "schemaVersion": "1.0",
        "tool": {"name": "c1-static-analysis-core", "version": __version__, "stage": "includes"},
        "generatedFor": manifest["contentHash"],
        "constants": dict(sorted(constants.items())),
        "sites": sites,
        "includedBy": {by_path[k]: sorted(by_path[p] for p in v) for k, v in sorted(included_by.items()) if k in by_path},
        "entries": dict(sorted(entries.items())),
        "totals": {"sites": len(sites), **dict(sorted(counts.items())), "entryPages": len(entries)},
    }


def run(out_dir: Path) -> dict[str, Any]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    asts = [AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]]
    cfgs = [json.loads((out_dir / "cfg" / f"{f['fileId']}.json").read_text(encoding="utf-8"))
            for f in manifest["files"]]
    result = build_includes(asts, cfgs, manifest)
    (out_dir / "index").mkdir(parents=True, exist_ok=True)
    (out_dir / "index" / "includes.json").write_bytes(canonical_json(result, pretty=True))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.includes", description="Resolve include/require sites")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    args = ap.parse_args(argv)
    if not (args.out_dir / "cfg" / "index.json").exists():
        print("error: run `python -m c1.cfg` first", file=sys.stderr)
        return 1
    r = run(args.out_dir)
    t = r["totals"]
    print(", ".join(f"{k} {v}" for k, v in t.items()) + f" -> {args.out_dir}/index/includes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
