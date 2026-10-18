"""One image with a page's captures next to each other (legacy, generated and, when simulate_css.py has run, the
generated page with the legacy stylesheets: a SIMULATION), for the report.

Usage, from component2_frontend/ (after tools/runtime/capture.py <app>):
    venv/Scripts/python.exe tools/runtime/side_by_side.py hms admin-panel1.php
writes output/runtime/<app>/<page>.side-by-side.png
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parents[1] / "output" / "runtime"
PAGE = """<!doctype html><meta charset="utf-8"><style>
body {{ margin: 0; font: 14px sans-serif; background: #fff; }}
.row {{ display: flex; gap: 16px; padding: 16px; align-items: flex-start; }}
figure {{ margin: 0; width: 640px; }} img {{ width: 640px; border: 1px solid #999; }}
figcaption {{ font-weight: bold; margin-bottom: 6px; }}
</style><div class="row">
<figure><figcaption>legacy: {legacy}</figcaption><img src="legacy/{page}.png"></figure>
<figure><figcaption>generated: {generated}</figcaption><img src="generated/{page}.png"></figure>
{third}</div>"""
THIRD = ("<figure><figcaption>SIMULATION: generated + legacy stylesheets</figcaption>"
         '<img src="generated-with-legacy-css/{page}.png"></figure>')


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    app, page = argv
    out = OUTPUT / app
    unit, _, query = page.partition("@")  # Phase 4g: a parameterised GET is captured as <unit>@<query>
    route = unit.removesuffix(".php") + (f"?{query}" if query else "")
    legacy_url = unit + (f"?{query}" if query else "")
    sheet = out / f"{page}.side-by-side.html"
    third = THIRD.format(page=page) if (out / "generated-with-legacy-css" / f"{page}.png").exists() else ""
    sheet.write_text(PAGE.format(page=page, legacy=f"/{legacy_url}", generated=f"/{route}", third=third), encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        view = browser.new_page(viewport={"width": 2000 if third else 1344, "height": 800})
        view.goto(sheet.as_uri(), wait_until="load")
        view.screenshot(path=str(out / f"{page}.side-by-side.png"), full_page=True)
        browser.close()
    sheet.unlink()
    print(out / f"{page}.side-by-side.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
