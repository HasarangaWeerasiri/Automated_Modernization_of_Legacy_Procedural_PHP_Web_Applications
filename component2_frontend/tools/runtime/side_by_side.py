"""One image with a page's two captures next to each other (legacy left, generated right), for the report.

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
</div>"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    app, page = argv
    out = OUTPUT / app
    route = page.removesuffix(".php")
    sheet = out / f"{page}.side-by-side.html"
    sheet.write_text(PAGE.format(page=page, legacy=f"/{page}", generated=f"/{route}"), encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        view = browser.new_page(viewport={"width": 1344, "height": 800})
        view.goto(sheet.as_uri(), wait_until="load")
        view.screenshot(path=str(out / f"{page}.side-by-side.png"), full_page=True)
        browser.close()
    sheet.unlink()
    print(out / f"{page}.side-by-side.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
