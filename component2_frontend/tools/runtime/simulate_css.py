"""Phase 4b, SIMULATION ONLY: the generated pages with the legacy page's own stylesheets, as a developer would have
them after completing the flagged `document_shell` gap (HMS keeps its own <head>, so the generated pages load no
CSS). Never written into generated code: the capture tool adds the legacy page's `<link rel="stylesheet">`s (as
absolute URLs on the legacy server) to the generated HTML while the browser receives it, and takes a screenshot.

For every page capture.py captured, it reads the stylesheet links of the legacy capture
(output/runtime/<app>/legacy/<page>.html), loads the generated page with JavaScript off, inserts the same links
before </head>, and saves output/runtime/<app>/generated-with-legacy-css/<page>.png plus an index of what was
injected. Only `<link rel="stylesheet">` is injected (as the brief says): inline <style> blocks are not.

Usage, from component2_frontend/ (both servers running, after tools/runtime/capture.py <app>):
    venv/Scripts/python.exe tools/runtime/simulate_css.py hms
"""

import html
import json
import sys
import urllib.parse
from pathlib import Path

import html5lib
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parents[1] / "output" / "runtime"
FOLDER = "generated-with-legacy-css"


def stylesheets(legacy_html: str, legacy_url: str) -> list[str]:
    """Absolute URLs of the legacy page's stylesheet links, in document order (each once)."""
    document = html5lib.parse(legacy_html, treebuilder="etree", namespaceHTMLElements=False)
    found = []
    for link in document.iter("link"):
        if "stylesheet" in (link.get("rel") or "").lower().split() and link.get("href"):
            url = urllib.parse.urljoin(legacy_url, link.get("href"))
            if url not in found:
                found.append(url)
    return found


def injector(tags: str, target: str):
    """A route handler: the generated page's own HTML gets `tags` before </head>; every other request passes."""
    def inject(route, request):
        if request.url.rstrip("/") != target.rstrip("/"):
            route.continue_()
            return
        response = route.fetch()
        route.fulfill(response=response, body=response.text().replace("</head>", tags + "</head>", 1))
    return inject


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    app = argv[0]
    out = OUTPUT / app
    capture = json.loads((out / "capture.json").read_text(encoding="utf-8"))
    (out / FOLDER).mkdir(parents=True, exist_ok=True)
    injected = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(java_script_enabled=False, viewport=capture["viewport"])
        for row in capture["pages"]:
            if not row["captured"] or any(k.endswith(("Refresh", "FinalUrl")) for k in row):
                continue  # (a page that redirects is not compared: capture.py)
            legacy_url = capture["legacy"] + row["legacyPath"]
            links = stylesheets((out / "legacy" / f"{row['page']}.html").read_text(encoding="utf-8"), legacy_url)
            injected[row["page"]] = links
            tags = "".join(f'<link rel="stylesheet" href="{html.escape(url)}">' for url in links)
            page = context.new_page()
            target = capture["generated"] + row["generatedPath"]

            page.route("**/*", injector(tags, target))
            page.goto(target, wait_until="load")
            page.screenshot(path=str(out / FOLDER / f"{row['page']}.png"), full_page=True)
            page.close()
        browser.close()
    (out / FOLDER / "injected.json").write_text(json.dumps({
        "simulation": "the legacy page's own <link rel=stylesheet> added to the generated HTML in transit; generated "
                      "code unchanged", "pages": injected}, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{app}: {len(injected)} generated pages screenshotted with the legacy stylesheets "
          f"({sum(bool(v) for v in injected.values())} had any) -> {out / FOLDER}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
