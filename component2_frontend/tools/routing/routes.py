"""Routing layer (opt-in deployment aid, not measured): the legacy URLs of a generated app, served by its routes.

A generated page keeps its links as they were in the PHP (`href="logout.php"`, flag `legacy_link`), and its route is
its path from the document root without ".php" (generation-spec v0.7 section 8a). This tool reads the run's
index.json (nothing else: no stage, rule or timeline) and writes a route table, then the matching `rewrites()` /
`redirects()` into the `next.config.ts` of a build project (output/test-app/c2-build/<name>/), so that a click on a
legacy link reaches the generated page. The page code and every Stage 1-5 output stay as they are.

- `/<path>.php` is rewritten to the page's route (the URL in the browser stays the legacy one, so relative links
  resolve as they did on the PHP server).
- A folder's index page (`admin/index.php`, route `/admin`): `/admin/index.php` is rewritten to `/admin`, and `/admin`
  (and `/admin/`, which Next first redirects to `/admin`) is redirected to `/admin/index.php`, so a relative link
  on it resolves inside the folder as it did on the PHP server. The root index needs no redirect.
- A file that is not a generated page gets no rule and is listed under `noPage` with the reason: not generated
  (spec v0.8 section 6a, e.g. `non_html_response`), crashed, or outside the document root.

It is never part of an evaluation run (eval-mapping section 4.2: links are not scored; keep it off in Phase 5).

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/routing/routes.py <index.json> [--out routes.json] [--build <name> [--no-build]]
(`--build <name>`: write next.config.ts and routes.json into output/test-app/c2-build/<name>/, a project
tools/runtime/generated_app.py or compile_check.next_build made, then `next build` it again in place; `--no-build`
only writes the files.)
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # component2_frontend/
TEST_APP = ROOT / "output" / "test-app"
BUILD_DIR = TEST_APP / "c2-build"
SPECIAL = re.compile(r"[():*+?{}\\\s#%]")  # path-to-regexp syntax or URL-encoded: never escaped by guess


class RouteError(ValueError):
    """A path the table cannot write as a literal rule."""


def _url(path: str, docroot: str) -> str | None:
    """A file's URL path from the document root, or None when it is outside it."""
    if not docroot:
        return "/" + path
    return "/" + path[len(docroot) + 1:] if path.startswith(docroot + "/") else None


def route_table(index: dict) -> dict:
    """The routing table of a run's index.json: rewrites, redirects and the files that have no page."""
    docroot = index.get("docroot") or ""
    rewrites, redirects, no_page = [], [], []
    for page in index["pages"]:
        if page["role"] != "page":
            continue
        url = _url(page["path"], docroot)
        reason = ("outside_docroot" if url is None else "crash" if page.get("crash")
                  else page["notGenerated"]["reason"] if page.get("notGenerated") else None)
        if reason is not None:
            no_page.append({"path": page["path"], "url": url, "reason": reason})
            continue
        if SPECIAL.search(url):
            raise RouteError(f"{page['path']}: {url} is not a literal path")
        route = page["route"]
        rewrites.append({"source": url, "destination": route, "path": page["path"]})
        if url.endswith("/index.php") and route != "/":
            redirects.append({"source": route, "destination": url, "permanent": False, "path": page["path"]})
    return {"app": index["app"], "docroot": docroot,
            "rewrites": sorted(rewrites, key=lambda r: r["source"]),
            "redirects": sorted(redirects, key=lambda r: r["source"]),
            "noPage": sorted(no_page, key=lambda r: r["path"])}


def _rules(entries: list[dict], keys: tuple[str, ...]) -> str:
    if not entries:
        return "[]"
    lines = ["{ " + ", ".join(f"{k}: {json.dumps(e[k])}" for k in keys) + " }," for e in entries]
    return "[\n" + "\n".join("      " + line for line in lines) + "\n    ]"


def next_config(table: dict) -> str:
    """The build project's next.config.ts with the table's rules (deterministic: sorted, no date)."""
    return (
        'import type { NextConfig } from "next";\n\n'
        f"// Routing layer for {table['app']} (tools/routing/routes.py): legacy .php URLs served by the generated\n"
        "// routes. A deployment aid, not part of the pipeline and not measured.\n"
        "const nextConfig: NextConfig = {\n"
        "  async redirects() {\n"
        f"    return {_rules(table['redirects'], ('source', 'destination', 'permanent'))};\n"
        "  },\n"
        "  async rewrites() {\n"
        f"    return {_rules(table['rewrites'], ('source', 'destination'))};\n"
        "  },\n"
        "};\n\n"
        "export default nextConfig;\n"
    )


def write(target: Path, table: dict) -> None:
    """Write next.config.ts and routes.json into a build project."""
    if not (target / "src").is_dir():
        raise FileNotFoundError(f"{target} is not a build project (no src/): build it first")
    (target / "next.config.ts").write_bytes(next_config(table).encode("utf-8"))
    (target / "routes.json").write_bytes((json.dumps(table, indent=2) + "\n").encode("utf-8"))


def rebuild(target: Path, timeout: int = 900) -> tuple[bool, str]:
    """`next build` of a build project in place (Node finds the test app's node_modules one level up)."""
    next_bin = TEST_APP / "node_modules" / "next" / "dist" / "bin" / "next"
    try:
        run = subprocess.run([shutil.which("node"), str(next_bin), "build", str(target)], cwd=TEST_APP,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"next build did not finish in {timeout} s"
    text = (run.stdout + run.stderr).strip()
    return run.returncode == 0, "\n".join(text.splitlines()[-60:])


def apply(name: str, index_path: Path, build: bool = True) -> tuple[dict, bool, str]:
    """Write the routing layer into output/test-app/c2-build/<name>/ and (by default) rebuild it."""
    table = route_table(json.loads(index_path.read_text(encoding="utf-8")))
    target = BUILD_DIR / name
    write(target, table)
    ok, text = rebuild(target) if build else (True, "")
    return table, ok, text


def main(argv: list[str]) -> int:
    named = [a for i, a in enumerate(argv) if not a.startswith("--")
             and (i == 0 or argv[i - 1] not in ("--out", "--build"))]
    if len(named) != 1:
        print(__doc__.strip().splitlines()[-4].strip(), file=sys.stderr)
        return 2
    index_path = Path(named[0])
    try:
        if "--build" in argv:
            table, ok, text = apply(argv[argv.index("--build") + 1], index_path, build="--no-build" not in argv)
        else:
            table, ok, text = route_table(json.loads(index_path.read_text(encoding="utf-8"))), True, ""
    except (RouteError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    if "--out" in argv:
        Path(argv[argv.index("--out") + 1]).write_bytes((json.dumps(table, indent=2) + "\n").encode("utf-8"))
    print(f"{table['app']}: {len(table['rewrites'])} rewrites, {len(table['redirects'])} redirects, "
          f"{len(table['noPage'])} files with no page"
          + (f"; next build {'ok' if ok else 'FAILED'}" if "--build" in argv and "--no-build" not in argv else ""))
    for entry in table["noPage"]:
        print(f"  no page: {entry['path']} ({entry['reason']})")
    if not ok:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
