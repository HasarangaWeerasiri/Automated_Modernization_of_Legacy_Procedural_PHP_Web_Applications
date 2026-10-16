"""Phase 4 runtime check: capture both sides of every page (legacy app in Docker, generated Next.js app).

For every page unit of the generated app (output/runtime/<app>/pipeline/<app>/index.json), it loads the legacy
URL `/<file>.php` and the generated URL (the page's route) in Chromium **with JavaScript disabled** (inline JS is
out of scope, and Server Components need none), waits for `load`, and saves `document.documentElement.outerHTML`
and a full-page screenshot under output/runtime/<app>/{legacy,generated}/<page>.{html,png}, plus index.json.

A page is captured only when both sides answer a plain GET with 200; anything else is listed with its status.
A page that **redirects** (an HTTP redirect the browser follows, or a `<meta http-equiv="refresh">`, which a browser
follows even with JavaScript off) is recorded with where it goes (`<side>FinalUrl` / `<side>Refresh`); a side whose
served HTML holds a meta refresh is not loaded in the browser (it would leave at once).
compare.py leaves such a page out of the element comparison (Phase 4e: phpBlog's admin pages, anonymous GET).
Nothing is posted and nobody logs in: a page whose output depends on a request or a session (its timeline reads
$_POST / $_GET / $_REQUEST / $_SESSION / $_COOKIE in an output or a condition) is captured as an anonymous GET and
marked `get_only`.

Usage, from component2_frontend/ (both servers running, see tools/runtime/hms/README.md):
    venv/Scripts/python.exe tools/runtime/capture.py hms [--legacy http://127.0.0.1:18080]
        [--generated http://127.0.0.1:18100] [--folder hms-v1.1]
(`--folder`: the run's folder under output/runtime/, default the app's name; generated_app.py takes the same.)
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # component2_frontend/
OUTPUT = ROOT / "output" / "runtime"
VIEWPORT = {"width": 1280, "height": 800}
REQUEST_INPUT = re.compile(r"\$_(POST|GET|REQUEST|SESSION|COOKIE)\b")
REFRESH_TAG = re.compile(r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh[^>]*>", re.I)
REFRESH_URL = re.compile(r"url\s*=\s*([^\"'>\s;]+)", re.I)


def status(url: str) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError):
        return 0


def get_only(timeline: dict) -> bool:
    """Whether what the page prints depends on a request or a session (then a GET shows the anonymous view)."""
    for entry in timeline["sequence"]:
        texts = [r["expr"] for r in entry.get("reads", [])]
        texts += [e.get("condExpr") or "" for e in entry["enclosedBy"]]
        if any(REQUEST_INPUT.search(text) for text in texts):
            return True
    return False


def pages(app: str, run: str | None = None) -> list[dict]:
    """Every generated page of the app: its key, file, route and get_only."""
    folder = OUTPUT / (run or app) / "pipeline" / app
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    found = []
    for entry in index["pages"]:
        if entry["role"] != "page" or entry.get("notGenerated") or entry["crash"]:
            continue
        timeline = json.loads((folder / entry["key"] / "timeline.json").read_text(encoding="utf-8"))
        route = entry["path"].removesuffix(".php")
        route = "" if route == "index" else route.removesuffix("/index")
        found.append({"page": entry["key"], "file": entry["path"], "legacyPath": "/" + entry["path"],
                      "generatedPath": "/" + route, "getOnly": get_only(timeline)})
    return found


def meta_refresh(url: str) -> str | None:
    """The target of a `<meta http-equiv="refresh">` in the page as served (a browser follows it, JavaScript or not),
    or None."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=60) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None
    for tag in REFRESH_TAG.findall(text):
        found = REFRESH_URL.search(tag)
        return found.group(1) if found else ""
    return None


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("--"):
        print(__doc__.strip().splitlines()[-4], file=sys.stderr)
        return 2
    app = argv[0]
    legacy = argv[argv.index("--legacy") + 1] if "--legacy" in argv else "http://127.0.0.1:18080"
    generated = argv[argv.index("--generated") + 1] if "--generated" in argv else "http://127.0.0.1:18100"
    run = argv[argv.index("--folder") + 1] if "--folder" in argv else app
    out = OUTPUT / run
    for side in ("legacy", "generated"):
        (out / side).mkdir(parents=True, exist_ok=True)
    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()  # the Chromium build Playwright 1.62.0 pins (tools/runtime/requirements.txt)
        context = browser.new_context(java_script_enabled=False, viewport=VIEWPORT)
        for page_info in pages(app, run):
            urls = {"legacy": legacy + page_info["legacyPath"], "generated": generated + page_info["generatedPath"]}
            codes = {side: status(url) for side, url in urls.items()}
            row = {**page_info, "legacyStatus": codes["legacy"], "generatedStatus": codes["generated"]}
            if codes["legacy"] != 200 or codes["generated"] != 200:
                rows.append(row | {"captured": False})
                continue
            for side, url in urls.items():
                refresh = meta_refresh(url)
                if refresh is not None:  # it sends the browser elsewhere: recorded, not captured
                    row[f"{side}Refresh"] = refresh
                    continue
                page = context.new_page()
                page.goto(url, wait_until="load")
                if page.url != url:  # an HTTP redirect (e.g. an admin page without a login): where it ended
                    row[f"{side}FinalUrl"] = page.url
                html = page.evaluate("document.documentElement.outerHTML")
                (out / side / f"{page_info['page']}.html").write_text(html, encoding="utf-8", newline="\n")
                page.screenshot(path=str(out / side / f"{page_info['page']}.png"), full_page=True)
                page.close()
            rows.append(row | {"captured": True})
        browser.close()
    index = {"app": app, "legacy": legacy, "generated": generated, "javascript": False, "viewport": VIEWPORT,
             "pages": rows}
    (out / "capture.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n")
    captured = sum(r["captured"] for r in rows)
    print(f"{app}: {captured} of {len(rows)} pages captured on both sides "
          f"({sum(r['getOnly'] for r in rows if r['captured'])} get_only)")
    for row in rows:
        if not row["captured"]:
            print(f"  not captured: {row['page']} (legacy {row['legacyStatus']}, generated {row['generatedStatus']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
