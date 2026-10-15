"""Substitute replay API for the Phase 4 runtime check: a TEST FIXTURE standing in for Component 1, never part of
C2's output.

From an app's harness timelines it takes every query the harness resolved with **literal SQL that is a plain
SELECT with no request input** (no interpolated value, nothing but the literal text) whose result columns are
known, and writes, under tools/runtime/replay/build/<app>/ (git-ignored):

  replay.json        what the server (server.py) answers: one endpoint per query, GET /api/c2-replay/<id>
                     (the query node id with "#" written "-"), returning {"rows": [...]}
  contract.json      an OpenAPI 3.1 contract for those endpoints, column types from the app's schema (.sql)
  endpoint_map.json  queryNodeId -> its endpoint (reconciliation-spec v0.3, per-query entries), so Stages 4-5 run
                     with it: a List whose loop reads that query maps to it exactly
  report.json        every query, replayable or not, and why

Queries are taken from the app's pages and function components (spec v0.13: a function component may fetch).
Everything is marked "substitute": "c2-replay". No input is invented: a query with request input stays out, and
the List that reads it keeps whatever Stage 4 decides (no_endpoint).

Usage, from component2_frontend/ (after tools/harness/run_apps.py has written output/apps/<app>/):
    venv/Scripts/python.exe tools/runtime/replay/generate.py hms
"""

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # component2_frontend/
sys.path.insert(0, str(ROOT / "tools" / "harness"))
from app import APPS  # noqa: E402

SUBSTITUTE = "c2-replay"
PREFIX = "/api/c2-replay"
INTEGER = ("int", "integer", "tinyint", "smallint", "mediumint", "bigint", "year")
NUMBER = ("decimal", "numeric", "float", "double", "real")


def column_types(sql: str) -> dict:
    """{table: {column: (OpenAPI type, nullable)}} from the CREATE TABLE statements of a schema dump."""
    tables = {}
    for found in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?(\w+)[`\"]?\s*\((.*?)\)\s*(?:engine|;)",
                             sql, re.I | re.S):
        columns = {}
        for line in found.group(2).splitlines():
            column = re.match(r"\s*`(\w+)`\s+(\w+)(.*)", line)
            if column:
                kind = column.group(2).lower()
                openapi = "integer" if kind in INTEGER else "number" if kind in NUMBER else "string"
                columns[column.group(1)] = (openapi, "not null" not in column.group(3).lower())
        tables[found.group(1)] = columns
    return tables


def sql_text(query: dict) -> str | None:
    """The SQL a query sends. The harness keeps it as written in PHP, i.e. a string literal with its quotes; a
    literal with a backslash escape is not read here (its value would need PHP's decoding)."""
    sql = query["sql"].strip()
    if len(sql) >= 2 and sql[0] == sql[-1] and sql[0] in "'\"":
        sql = sql[1:-1]
        if "\\" in sql:
            return None
    return sql.strip()


def plain_select(query: dict) -> str | None:
    """None when the query can be replayed; else why not."""
    if query.get("valuesInterpolated"):
        return "request_input"  # interpolated values: replaying it would need invented inputs
    sql = sql_text(query)
    if sql is None or ";" in sql.rstrip(";") or "$" in sql:
        return "not_plain_sql"
    if not re.match(r"select\b", sql, re.I):
        return "not_a_select"
    if not query.get("columns"):
        return "columns_unknown"
    return None


def safe(query_id: str) -> str:
    return query_id.replace("#", "-")


def item_schema(query: dict, types: dict) -> dict | None:
    """The row schema of a query, or None when a column's type is not certain (several tables could own it)."""
    properties, required = {}, []
    for column in query["columns"]:
        owners = [types[t][column] for t in query["tables"] if t in types and column in types[t]]
        if len(owners) != 1:
            return None
        kind, nullable = owners[0]
        properties[column] = {"type": [kind, "null"] if nullable else kind}
        if not nullable:
            required.append(column)
    return {"type": "object", "required": required, "properties": properties}


def build(pages: list[dict], types: dict) -> dict:
    """pages: [{entrypoint, queries: {id: query}}] (pages and function components) ->
    {replay, contract, endpoint_map, report}."""
    replay = {"substitute": SUBSTITUTE, "queries": {}}
    paths, endpoints, report = {}, {}, []

    def operation(name: str, schema: dict, summary: str) -> dict:
        return {"get": {"operationId": name, "summary": summary, "x-c2-substitute": SUBSTITUTE,
                        "responses": {"200": {"description": "rows", "content": {"application/json": {
                            "schema": schema}}}}}}

    for page in sorted(pages, key=lambda p: p["entrypoint"]):
        for query_id, query in sorted(page["queries"].items()):
            reason = plain_select(query)
            item = item_schema(query, types) if reason is None else None
            if reason is None and item is None:
                reason = "column_type_unknown"
            report.append({"entrypoint": page["entrypoint"], "queryNodeId": query_id, "sql": query["sql"],
                           "replayable": reason is None, **({"reason": reason} if reason else {})})
            if reason is not None:
                continue
            path = f"{PREFIX}/{safe(query_id)}"
            replay["queries"][path] = {"queryNodeId": query_id, "sql": sql_text(query).rstrip(";").strip(),
                                       "columns": query["columns"]}
            paths[path] = operation(f"replay_{safe(query_id).replace('-', '_')}", {
                "type": "object", "required": ["rows"], "properties": {"rows": {"type": "array", "items": item}}},
                f"rows of {query_id} (substitute: {SUBSTITUTE})")
            endpoints[query_id] = {"method": "get", "path": path, "status": "200", "mediaType": "application/json"}
    contract = {"openapi": "3.1.0", "info": {
        "title": "C2 replay substitute", "version": "1", "x-c2-substitute": SUBSTITUTE,
        "description": "Test fixture for the Phase 4 runtime check, standing in for Component 1. Not C2 output."},
        "paths": dict(sorted(paths.items()))}
    endpoint_map = {"endpoints": dict(sorted(endpoints.items())), "_provenance": {
        "substitute": SUBSTITUTE, "replaces": "Component 1's endpoint map, for the Phase 4 runtime check only"}}
    return {"replay": replay, "contract": contract, "endpoint_map": endpoint_map, "report": report}


def app_pages(app: str) -> list[dict]:
    """The pages and function components of an app's last harness run (output/apps/<app>/) with their queries.
    Shells and functions not in the app are left out (they render no List of their own here)."""
    folder = ROOT / "output" / "apps" / app
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    pages = []
    for entry in index["pages"]:
        if entry["role"] not in ("page", "component") or entry.get("notGenerated"):
            continue
        timeline = json.loads((folder / entry["key"] / "timeline.json").read_text(encoding="utf-8"))
        if timeline.get("queries"):
            pages.append({"entrypoint": timeline["entrypoint"], "queries": timeline["queries"]})
    return pages


def schema_files(app: str) -> list[str]:
    """The text of the app's .sql files at its pinned commit (not the working copy, which may be patched)."""
    config = APPS[app]
    if "manifest" in config:  # an archived app: the folder is the commit (checked against its manifest)
        return [p.read_bytes().decode("utf-8", errors="replace") for p in sorted(config["root"].rglob("*.sql"))]
    names = subprocess.run(["git", "-C", str(config["root"]), "ls-tree", "-r", "--name-only", config["commit"]],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [subprocess.run(["git", "-C", str(config["root"]), "show", f"{config['commit']}:{name}"],
                           capture_output=True, check=True).stdout.decode("utf-8", errors="replace")
            for name in sorted(n for n in names if n.lower().endswith(".sql"))]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in APPS:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    app = argv[0]
    types = {}
    for sql in schema_files(app):
        types |= column_types(sql)
    result = build(app_pages(app), types)
    out = HERE / "build" / app
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in (("replay", result["replay"]), ("contract", result["contract"]),
                      ("endpoint_map", result["endpoint_map"]), ("report", result["report"])):
        (out / f"{name}.json").write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8",
                                          newline="\n")
    replayable = sum(r["replayable"] for r in result["report"])
    print(f"{app}: {len(result['report'])} queries in pages and function components, {replayable} replayable "
          f"(per-query endpoint-map entries) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
