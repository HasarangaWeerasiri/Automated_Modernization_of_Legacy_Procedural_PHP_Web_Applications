"""Suggest a target architecture for the migrated system (architecture.json).

The tool does not assume three tiers is always right. From what the analysis already
knows (endpoints, which pages use which tables, the login), it scores three candidates
and explains every point it gives or takes away:

  layered-monolith   one backend, split into layers (what Component 1 generates today)
  modular-monolith   one backend, split into modules by business area
  microservices      one service per business area, each owning its tables

How business areas are found (static, deterministic):
  1. Identity tables (used by the login and by most pages) form their own "identity" area.
  2. Two tables belong to the same area when the pages that use them overlap a lot
     (Jaccard similarity of their page sets >= 0.5), merged with union-find.
  3. Each endpoint joins the area of its main table.
  (Tables named after another table, like cart / cart_items, also join its area.)
This follows the "shared entity access" coupling idea of Service Cutter (Gysel et al.,
ESOCC 2016) and Mono2Micro (Kalia et al., ESEC/FSE 2021), which use the same kind of
signal from Java code or traces; here it comes from procedural PHP. The rules prefer the
simpler option on a tie ("monolith first", Fowler 2015) and penalise splits where pages
keep combining data from several areas, which would turn into chatty calls and shared
databases (Taibi & Lenarduzzi, IEEE Software 2018: "shared persistence" smell).

Only layered-monolith can be generated now. The others are suggestions with a module
plan; generating them is planned work.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .extract import canonical_json

CANDIDATES = ("layered-monolith", "modular-monolith", "microservices")
TITLES = {"layered-monolith": "Layered monolith", "modular-monolith": "Modular monolith",
          "microservices": "Microservices"}
SUMMARY = {
    "layered-monolith": "One backend, organised in layers: routes, auth, models, data access.",
    "modular-monolith": "One backend and one database, split into modules by business area with clear boundaries.",
    "microservices": "Separate services per business area, each with its own data, talking over the network.",
}
AVAILABLE = {"layered-monolith": "available", "modular-monolith": "planned", "microservices": "planned"}
JACCARD = 0.5
IDENTITY_SHARE = 0.8


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _tables_of(ep: dict) -> set[str]:
    t = {ep.get("table") or ep.get("resource")} | {w.get("table") for w in ep.get("alsoWrites", [])}
    return {x for x in t if x}


def _module_name(tables: list[str]) -> str:
    # a table the others are named after (comments for comments_preview) names the module; else the most used one
    roots = [t for t in tables if any(o != t and (o.startswith(t + "_") or o.endswith("_" + t)) for o in tables)]
    t = min(roots, key=len) if roots else tables[0]
    for suffix in ("tb", "_tbl", "tbl"):
        if t.endswith(suffix) and len(t) > len(suffix) + 2:
            return t[: -len(suffix)]
    return t


def areas(eps: list[dict], schema_tables: set[str] | None = None) -> dict[str, Any]:
    """Group tables into business areas from how pages use them."""
    page_tables: dict[str, set[str]] = defaultdict(set)
    login_tables: set[str] = set()
    for e in eps:
        tabs = _tables_of(e)
        for s in e.get("sources", []):
            page_tables[s["page"]] |= tabs
        if e["path"] == "/api/auth/login":  # the tables the login reads (its parameters' columns)
            login_tables |= {p["column"].split(".")[0] for p in e.get("params", []) if "." in (p.get("column") or "")}
    pages = sorted(page_tables)
    pages_of: dict[str, set[str]] = defaultdict(set)
    for p, tabs in page_tables.items():
        for t in tabs:
            pages_of[t].add(p)
    pages_of.pop("auth", None)  # the login endpoint's pseudo-resource
    if schema_tables:  # a name the database dump does not define is not a table we can place
        for t in [t for t in pages_of if t not in schema_tables]:
            pages_of.pop(t)
    n = max(1, len(pages))
    # identity: login tables that almost every page reads (the legacy app re-reads the user on each request;
    # after migration that comes from the token, so it is not a cross-area dependency)
    identity = sorted(t for t in pages_of if t in login_tables and len(pages_of[t]) / n >= IDENTITY_SHARE)
    domain = sorted(t for t in pages_of if t not in identity)

    parent = {t: t for t in domain}

    def find(t: str) -> str:
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def same_name(a: str, b: str) -> bool:  # cart / cart_items, comments / comments_preview, pictures / conflict_pictures
        return b.startswith(a + "_") or b.endswith("_" + a) or a.startswith(b + "_") or a.endswith("_" + b)

    for i, a in enumerate(domain):
        for b in domain[i + 1:]:
            inter, union = len(pages_of[a] & pages_of[b]), len(pages_of[a] | pages_of[b])
            if (union and inter / union >= JACCARD) or same_name(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = defaultdict(list)
    for t in domain:
        groups[find(t)].append(t)
    # biggest area first; inside an area, the most used table first (it names the module)
    clusters = sorted((sorted(g, key=lambda t: (-len(pages_of[t]), t)) for g in groups.values()),
                      key=lambda g: (-sum(len(pages_of[t]) for t in g), g[0]))
    area_of = {t: i for i, g in enumerate(clusters) for t in g}

    data_pages = [p for p in pages if {t for t in page_tables[p] if t in area_of}]
    spanning = [p for p in data_pages if len({area_of[t] for t in page_tables[p] if t in area_of}) >= 2]
    return {"pages": pages, "pageTables": {p: sorted(page_tables[p]) for p in pages},
            "identityTables": identity, "loginTables": sorted(login_tables), "clusters": clusters, "areaOf": area_of,
            "dataPages": data_pages, "pagesSpanningAreas": spanning,
            "crossAreaShare": round(len(spanning) / len(data_pages), 2) if data_pages else 0.0}


def score(feat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Transparent rule scores, 0-100. Every rule that fires is listed as a reason."""
    S, T, K, X = feat["endpoints"], feat["domainTables"], feat["areas"], feat["crossAreaShare"]
    out: dict[str, dict[str, Any]] = {}

    def rules(base: int, items: list[tuple[bool, int, str]]) -> dict[str, Any]:
        reasons = [{"points": pts, "why": why} for cond, pts, why in items if cond]
        return {"score": max(0, min(100, base + sum(r["points"] for r in reasons))), "reasons": reasons}

    if S == 0:
        none = [{"points": 0, "why": "no endpoints were recovered (the app is out of scope or flagged): "
                                    "there is no evidence to suggest a split"}]
        return {c: {"score": 0, "reasons": none} for c in ("layered-monolith", "modular-monolith", "microservices")}
    out["layered-monolith"] = rules(60, [
        (S <= 30, +15, f"small API ({S} endpoints): one deployable stays easy to run and test"),
        (T <= 8, +10, f"few tables ({T}): one data layer is enough"),
        (K <= 2, +10, f"{K} business area(s): little to gain from splitting"),
        (X > 0.4, +5, f"{X:.0%} of data pages combine several areas: one process keeps those joins local"),
        (K >= 4, -15, f"{K} business areas: one flat layer mixes them and grows hard to change"),
        (S > 60, -10, f"large API ({S} endpoints)"),
    ])
    out["modular-monolith"] = rules(50, [
        (K >= 2, +20, f"{K} business areas found: they map to modules with clear boundaries"),
        (X <= 0.3, +10, f"pages mostly stay in one area (only {X:.0%} span several): module boundaries hold"),
        (S >= 15, +10, f"{S} endpoints: enough to benefit from grouping by area"),
        (X > 0.5, -10, f"{X:.0%} of data pages combine several areas: modules would call each other a lot"),
        (K <= 1, -25, "only one business area: modules would not separate anything"),
    ])
    out["microservices"] = rules(20, [
        (K >= 3, +20, f"{K} business areas could become services"),
        (X <= 0.2, +20, f"pages rarely combine areas ({X:.0%}): services would seldom need each other"),
        (S >= 50, +15, f"large API ({S} endpoints)"),
        (S < 30, -15, f"small app ({S} endpoints): network calls, separate databases and deployment cost more "
                      "than they save (monolith first)"),
        (X > 0.4, -20, f"{X:.0%} of data pages combine several areas: they would need distributed joins or a "
                       "shared database (shared-persistence smell)"),
        (feat["identityShared"], -5, "every area depends on the same login data: needs a separate auth service"),
    ])
    return out


def build(out: Path, selected: str | None = None) -> dict[str, Any]:
    manifest = _load(out / "manifest.json")
    ej = _load(out / "endpoints.json")
    eps = ej["endpoints"]
    a = areas(eps, set(ej.get("schema") or {}))
    feat = {"endpoints": len(eps), "domainTables": sum(len(c) for c in a["clusters"]), "areas": len(a["clusters"]),
            "identityTables": a["identityTables"], "loginTables": a["loginTables"],
            "identityShared": bool(a["identityTables"]),
            "crossAreaShare": a["crossAreaShare"], "dataPages": len(a["dataPages"]),
            "pagesSpanningAreas": a["pagesSpanningAreas"]}
    scores = score(feat)
    ranked = sorted(CANDIDATES, key=lambda c: (-scores[c]["score"], CANDIDATES.index(c)))  # tie -> simpler

    modules = []
    for i, tabs in enumerate(a["clusters"]):
        ids = sorted(e["id"] for e in eps if (e.get("table") or e.get("resource")) in tabs)
        modules.append({"name": _module_name(tabs), "tables": tabs, "endpoints": ids})
    ident_eps = sorted(e["id"] for e in eps if e["path"] == "/api/auth/login"
                       or (e.get("table") or e.get("resource")) in a["identityTables"])
    if ident_eps or a["identityTables"]:
        modules.append({"name": "identity", "tables": a["identityTables"], "endpoints": ident_eps,
                        "reads": [t for t in a["loginTables"] if t not in a["identityTables"]]})
    placed = {i for m in modules for i in m["endpoints"]}
    rest = sorted(e["id"] for e in eps if e["id"] not in placed)
    if rest:
        modules.append({"name": "unplaced", "tables": [], "endpoints": rest,
                        "note": "their table is not in the database dump; a person places them"})

    options = [{"id": c, "title": TITLES[c], "summary": SUMMARY[c], "generation": AVAILABLE[c],
                "score": scores[c]["score"], "reasons": scores[c]["reasons"],
                "modules": [] if c == "layered-monolith" else modules} for c in CANDIDATES]
    sel = selected or "layered-monolith"
    return {"schemaVersion": "1.0", "generatedFor": manifest["contentHash"],
            "tool": {"name": "c1-static-analysis-core", "stage": "architecture"},
            "recommended": ranked[0], "ranking": ranked, "selected": sel,
            "generated": "layered-monolith",
            "note": ("only the layered monolith is generated today; the other options are suggestions with a "
                     "module plan" if sel != "layered-monolith" else "the layered monolith is generated"),
            "features": feat, "pageTables": a["pageTables"], "options": options,
            "method": {"areas": f"tables grouped when the pages using them overlap (Jaccard >= {JACCARD}); "
                                "identity tables kept apart",
                       "references": ["Gysel et al., Service Cutter, ESOCC 2016",
                                      "Kalia et al., Mono2Micro, ESEC/FSE 2021",
                                      "Fowler, MonolithFirst, 2015",
                                      "Taibi & Lenarduzzi, On the Definition of Microservice Bad Smells, IEEE Software 2018"]},
            "totals": {"recommended": ranked[0], "score": scores[ranked[0]]["score"], "areas": feat["areas"],
                       "selected": sel}}


def run(out_dir: Path, selected: str | None = None) -> dict[str, Any]:
    res = build(out_dir, selected)
    (out_dir / "architecture.json").write_bytes(canonical_json(res, pretty=True))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.architecture", description="Suggest a target architecture")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--architecture", choices=CANDIDATES)
    args = ap.parse_args(argv)
    if not (args.out_dir / "endpoints.json").exists():
        print("error: run the pipeline up to `endpoints` first", file=sys.stderr)
        return 1
    r = run(args.out_dir, args.architecture)
    for o in r["options"]:
        print(f"{o['score']:>3}  {o['title']:<18} {o['generation']}")
    print(f"recommended: {r['recommended']} -> {args.out_dir}/architecture.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
