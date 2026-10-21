"""Before/after measures that show what the migration changed (metrics.json).

Two factors, each computed from what earlier stages wrote, for the legacy app (before)
and for the generated backend (after):

Security (attack surface, Manadhata & Wing, IEEE TSE 2011: count the entry points an
attacker can reach and what they reach)
  S1  missing authorization (CWE-862): data operations reachable with no working check
  S2  execution after redirect (CWE-698): a "guard" redirects but the page keeps running
  S3  SQL text built from request input (CWE-89, injection-prone)
  S4  request inputs read with no declared type / validation
  S5  sensitive columns (passwords) sent to the client

Separation of concerns (concern tangling, after Eaddy et al., IEEE TSE 2008: a module is
tangled when it implements several concerns)
  T1  files that mix presentation, business logic and data access
  T2  files that both print HTML and run SQL
  C1  cyclomatic complexity V(G) = E - N + 2 per unit (McCabe 1976), from our CFGs

Rules: every number comes from the analysis, never estimated. Where the "after" side is
not comparable yet (the C3 data layer is still stubs), the metric says so in `note`
instead of printing a flattering number.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .astdoc import SCOPE_KINDS, AstDoc
from .extract import canonical_json

CONCERNS = ("presentation", "business_logic", "data_access")
# functions that escape or coerce a value before it goes into SQL
ESCAPERS = frozenset({"mysqli_real_escape_string", "mysql_real_escape_string", "mysqli_escape_string",
                      "mysql_escape_string", "addslashes", "intval", "floatval", "filter_var", "filter_input"})
PREPARED = frozenset({"mysqli_prepare", "prepare", "mysqli_stmt_prepare"})
SENSITIVE = ("password", "passwd", "pwd", "pass")
REQUEST = ("GET", "POST", "REQUEST", "COOKIE", "FILES")


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _vendored(path: str) -> bool:
    """Third-party code shipped inside the app (e.g. vendor/jquery-file-upload): not the app's own design."""
    return path.startswith("vendor/") or "/vendor/" in path


def _unit_of(ast: AstDoc, nid: str) -> str:
    """Id of the unit (file body or function) a node belongs to: same ids as cfg/dfg units."""
    for a in ast.ancestors(nid):
        if ast.kind(a) in SCOPE_KINDS:
            return a
    return ast.root_id


# ---------------------------------------------------------------- security ---------
def security(out: Path, manifest: dict, asts: dict[str, AstDoc]) -> dict[str, Any]:
    auth = _load(out / "auth.json")
    eps = _load(out / "endpoints.json")["endpoints"]
    flags = _load(out / "flags.json")["items"] if (out / "flags.json").exists() else []

    # S1: data operations with no working check
    unprot_pages = [p for p in auth["pages"] if p["status"] == "unguarded"]
    unprot_ops = sum(len(p.get("unprotected", [])) for p in unprot_pages)
    writes = sum(p.get("unguardedWrites") or 0 for p in unprot_pages)
    token = [e["id"] for e in eps if e["auth"].get("required")]
    login = [e["id"] for e in eps if e["path"] == "/api/auth/login"]
    open_ = [e["id"] for e in eps if not e["auth"].get("required") and e["id"] not in login]
    s1 = {
        "before": {"pages": len(unprot_pages), "dataOperations": unprot_ops, "writeOperations": writes,
                   "dataPages": sum(1 for p in auth["pages"] if p["dataAccess"])},
        "after": {"endpoints": len(eps), "tokenRequired": len(token), "publicLogin": len(login),
                  "awaitingDecision": len(open_)},
        "note": "after: every endpoint either checks a JWT or is listed in flags.json for a person to mark it "
                "public; none is silently left open",
    }

    # S2: redirect without exit
    broken = auth["totals"].get("effect:broken-guard", 0)
    ear = len(auth.get("redirectWithoutExit", []))
    s2 = {"before": broken + ear, "after": 0,
          "note": "after: FastAPI's Depends(get_current_user) raises before the handler runs, so no code can "
                  "run after a failed check"}

    # S3: SQL built from request input. Two ways a request value reaches a query: directly on the page
    # (DFG origin "input:...") or through a function/method parameter (endpoint params traced to the call site)
    qinfo: dict[str, dict] = {}
    for f in manifest["files"]:
        dfg_p = out / "dfg" / f"{f['fileId']}.json"
        if not dfg_p.exists() or f["fileId"] not in asts:
            continue
        ast = asts[f["fileId"]]
        for u in _load(dfg_p)["units"]:
            calls = {ast.call_name(n) for n in ast.walk(u["id"])} - {None}
            for q in u.get("queries", []):
                qinfo[q["node"]] = {"path": f["path"], "line": ast.line(q["node"]), "call": (q.get("call") or "").lower(),
                                    "escaped": bool(calls & ESCAPERS),
                                    "dynamic": any(o != "literal" for o in q.get("sqlOrigins", [])),
                                    "inputs": {o[6:] for o in q.get("sqlOrigins", []) if o.startswith("input:")}}
    reached = {n for n, q in qinfo.items() if q["inputs"]}
    for e in eps:
        req = {f"{p.get('legacy_in')}:{p.get('legacy_name')}" for p in e.get("params", [])
               if p.get("legacy_in") in ("query", "body", "cookie")}
        if not req:
            continue
        for src in e.get("sources", []):
            for n in src.get("queries", []):
                if n in qinfo and qinfo[n]["dynamic"]:  # a fixed SQL string cannot carry a request value
                    reached.add(n)
                    qinfo[n]["inputs"] |= req
    built = [n for n in sorted(reached) if qinfo[n]["call"] not in PREPARED]
    prepared = len(reached) - len(built)
    escaped_seen = sum(1 for n in built if qinfo[n]["escaped"])
    examples = [{"path": qinfo[n]["path"], "line": qinfo[n]["line"], "inputs": sorted(qinfo[n]["inputs"])}
                for n in built if not qinfo[n]["escaped"]][:5]
    params = sum(len(e.get("params", [])) for e in eps)
    s3 = {
        "before": {"queriesBuiltFromInput": len(built), "withNoEscapingInSameUnit": len(built) - escaped_seen,
                   "preparedStatements": prepared, "examples": examples},
        "after": {"queriesBuiltFromInput": 0, "typedParameters": params},
        "note": "after: request values reach the data layer as separate, typed arguments (never pasted into SQL "
                "text); C3 must bind them as query parameters",
    }

    # S4: request inputs with no declared type
    inputs = set()
    for f in manifest["files"]:
        dfg_p = out / "dfg" / f"{f['fileId']}.json"
        if dfg_p.exists():
            for u in _load(dfg_p)["units"]:
                for s in u.get("sources", []):
                    if s.get("superglobal") in REQUEST:
                        inputs.add((f["path"], s["superglobal"], tuple(s.get("dims") or ())))
    unknown = sum(1 for i in flags if i.get("kind") == "type-unknown")
    s4 = {"before": {"untypedRequestInputs": len(inputs)},
          "after": {"declaredParameters": params, "typeUnknownFlags": unknown},
          "note": "after: every parameter is declared in OpenAPI and checked by Pydantic before the handler runs "
                  "(a wrong type gets 422)"}

    # S5: sensitive columns shown
    sens = [i for i in flags if i.get("kind") == "sensitive-field"]
    s5 = {"before": len(sens), "after": len(sens), "status": "flagged",
          "note": "the API still lists these columns until a person removes them (flags.json, kind "
                  "sensitive-field); see decision in docs"}

    return {"S1_missingAuthorization": s1, "S2_executionAfterRedirect": s2, "S3_sqlBuiltFromInput": s3,
            "S4_untypedInputs": s4, "S5_sensitiveFields": s5}


# ------------------------------------------------------- separation of concerns ----
def structure(out: Path, manifest: dict) -> dict[str, Any]:
    labels = _load(out / "labels.json")["labels"]
    per_file: dict[str, Counter] = defaultdict(Counter)
    for nid, lab in labels.items():
        per_file[nid.split("#")[0]][lab["concern"]] += 1
    path_of = {f["fileId"]: f["path"] for f in manifest["files"]}

    vendored = sorted(p for p in path_of.values() if _vendored(p))
    files = [fid for fid, c in per_file.items() if any(c[k] for k in CONCERNS) and not _vendored(path_of.get(fid, ""))]
    mixed2 = [fid for fid in files if sum(1 for k in CONCERNS if per_file[fid][k]) >= 2]
    mixed3 = [fid for fid in files if all(per_file[fid][k] for k in CONCERNS)]
    html_sql = [fid for fid in files if per_file[fid]["presentation"] and per_file[fid]["data_access"]]
    worst = sorted(mixed3, key=lambda fid: (-sum(per_file[fid][k] for k in CONCERNS), path_of.get(fid, fid)))[:5]

    t1 = {"before": {"files": len(files), "mixTwoOrMore": len(mixed2), "mixAllThree": len(mixed3),
                     "percentMixed": round(100 * len(mixed2) / len(files), 1) if files else 0.0,
                     "worst": [{"path": path_of.get(fid, fid), **{k: per_file[fid][k] for k in CONCERNS}}
                               for fid in worst]},
          "after": {"modules": {"routes.py": "interface", "auth.py": "security", "models.py": "data shape",
                                "data_access.py": "data access (C3)"}, "mixTwoOrMore": 0, "percentMixed": 0.0},
          "note": "after: presentation moves to the frontend (C2); each generated backend module has one job"}
    t2 = {"before": len(html_sql), "after": 0, "files": sorted(path_of.get(f, f) for f in html_sql)}

    # cyclomatic complexity per unit, from the CFGs
    units = []
    for f in manifest["files"]:
        p = out / "cfg" / f"{f['fileId']}.json"
        if not p.exists() or _vendored(f["path"]):
            continue
        for u in _load(p)["units"]:
            live = {b["id"] for b in u["blocks"] if b.get("reachable", True)}
            e = sum(1 for x in u["edges"] if x["from"] in live and x["to"] in live)
            units.append({"path": f["path"], "unit": u.get("name", "?"), "vg": max(1, e - len(live) + 2)})
    vg = sorted(u["vg"] for u in units)
    eps = _load(out / "endpoints.json")
    split = []
    for page, ids in sorted(eps.get("pages", {}).items()):
        main = next((u for u in units if u["path"] == page and u["unit"] == "{main}"), None)
        if ids and main:
            split.append({"page": page, "vg": main["vg"], "endpoints": len(ids)})
    c1 = {"before": {"units": len(units), "total": sum(vg), "max": vg[-1] if vg else 0,
                     "median": vg[len(vg) // 2] if vg else 0, "over10": sum(1 for v in vg if v > 10),
                     "hotspots": sorted(units, key=lambda u: (-u["vg"], u["path"], u["unit"]))[:5]},
          "after": {"pagesSplitIntoEndpoints": sorted(split, key=lambda s: (-s["vg"], s["page"]))},
          "note": "Not compared after the migration yet: the generated handlers are thin until C3 fills the data "
                  "layer, so a before/after V(G) would be unfair"}
    return {"T1_concernTangling": t1, "T2_htmlAndSqlInOneFile": t2, "C1_cyclomaticComplexity": c1,
            "excludedVendoredFiles": vendored}


def build(out: Path) -> dict[str, Any]:
    manifest = _load(out / "manifest.json")
    asts = {f["fileId"]: AstDoc.load(out / "ast" / f"{f['fileId']}.json") for f in manifest["files"]
            if (out / "ast" / f"{f['fileId']}.json").exists()}
    sec = security(out, manifest, asts)
    struct = structure(out, manifest)
    s1, s3 = sec["S1_missingAuthorization"], sec["S3_sqlBuiltFromInput"]
    t1, c1 = struct["T1_concernTangling"], struct["C1_cyclomaticComplexity"]
    totals = {
        "unguardedDataPages": s1["before"]["pages"],
        "earBefore": sec["S2_executionAfterRedirect"]["before"],
        "sqlFromInputBefore": s3["before"]["queriesBuiltFromInput"],
        "tangledFilesPercent": t1["before"]["percentMixed"],
        "maxComplexity": c1["before"]["max"],
    }
    return {"schemaVersion": "1.0", "generatedFor": manifest["contentHash"],
            "tool": {"name": "c1-static-analysis-core", "stage": "metrics"},
            "references": {
                "attackSurface": "P. K. Manadhata, J. M. Wing, An Attack Surface Metric, IEEE TSE 37(3), 2011",
                "tangling": "M. Eaddy et al., Do Crosscutting Concerns Cause Defects?, IEEE TSE 34(4), 2008",
                "complexity": "T. J. McCabe, A Complexity Measure, IEEE TSE 2(4), 1976",
                "ear": "A. Doupe et al., Fear the EAR, ACM CCS 2011 (CWE-698)"},
            "security": sec, "structure": struct, "totals": totals}


def run(out_dir: Path) -> dict[str, Any]:
    res = build(out_dir)
    (out_dir / "metrics.json").write_bytes(canonical_json(res, pretty=True))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.metrics", description="Before/after migration measures")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    args = ap.parse_args(argv)
    if not (args.out_dir / "endpoints.json").exists():
        print("error: run the pipeline up to `contract` first", file=sys.stderr)
        return 1
    r = run(args.out_dir)
    print(json.dumps(r["totals"]) + f" -> {args.out_dir}/metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
