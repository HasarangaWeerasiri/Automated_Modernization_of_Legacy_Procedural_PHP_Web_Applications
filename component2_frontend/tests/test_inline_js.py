"""The opt-in inline JS layer (tools/assets/inline_js.py, `legacy_assets.py --inline-js`): the legacy page's inline
scripts and event handlers, run verbatim; nothing converted; skipped (never guessed) when conditional, built by PHP,
ambiguous or position-dependent. A deployment aid, not measured, never reached from the pipeline.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "assets"))
import inline_js  # noqa: E402
import legacy_assets  # noqa: E402

PHP = legacy_assets.find_php()
needs_php = pytest.mark.skipif(PHP is None, reason="PHP not found")

PAGE = """<?php
if (isset($_POST['go'])) {
    echo "<script>alert('Saved');</script>";
}
if ($x): ?>
<script>var onlyWhenX = 1;</script>
<?php endif; ?>
<script>function clickDiv(id) { document.querySelector(id).click(); }</script>
<a class="item" id="home-list" href="#home" data-toggle="list">Home</a>
<a href="#home" onclick="clickDiv('#home-list')">Open home</a>
<?php foreach ($rows as $r) { ?>
<button class="pay" type="submit" onclick="return confirm('Sure?');">Pay</button>
<?php } ?>
<input name="a" onkeyup="check();"><input name="a" onkeyup="other();">
<button onclick="go()">bare</button>
<a href="x.php?id=<?= $id ?>" onclick="del(1)">Delete</a>
<?php echo '<span onclick="f(' . $id . ')">x</span>'; ?>
<script>document.write('<p>here</p>');</script>
<script>window.onload = function () { document.getElementById('home-list').innerHTML = 'Home'; };</script>
<script type="text/template"><b>not JS</b></script>
<script src="https://cdn.example/jquery.js"></script>
<!-- <script>commented()</script> -->
"""


def table(tmp_path: Path) -> dict:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "page.php").write_text(PAGE, encoding="utf-8")
    index = {"app": "demo", "docroot": "", "shells": [], "functions": {}, "pages": [
        {"key": "page.php", "path": "page.php", "role": "page", "route": "/page", "group": "(plain)",
         "entrypoint": root.as_posix() + "/page.php", "crash": None}]}
    return legacy_assets.asset_table(index, PHP, inline=True)["pages"][0]["inline"]


@needs_php
def test_scripts_run_in_source_order_files_and_blocks_together(tmp_path):
    steps = table(tmp_path)["steps"]
    assert [s.get("code", s.get("src", {}).get("src")).strip()[:20] for s in steps] == [
        "function clickDiv(id", "window.onload = func", "https://cdn.example/"]


@needs_php
def test_conditional_built_by_php_and_document_write_are_skipped_never_guessed(tmp_path):
    skipped = {(s["skip"], s["line"]) for s in table(tmp_path)["skipped"]}
    assert skipped == {("conditional", 3), ("conditional", 6),  # if { echo } and if: endif
                       ("ambiguous", 14), ("no_selector", 15),
                       ("handler_built_by_php", 16), ("handler_built_by_php", 17), ("document_write", 18)}


@needs_php
def test_handlers_carry_an_exact_selector_and_a_loop_is_fine(tmp_path):
    handlers = {h["code"]: h for h in table(tmp_path)["handlers"]}
    assert set(handlers) == {"clickDiv('#home-list')", "return confirm('Sure?');"}
    card = handlers["clickDiv('#home-list')"]
    assert card["selector"] == 'a[href="#home"]' and card["names"] == "href"  # the sidebar link has more names
    assert handlers["return confirm('Sure?');"]["selector"] == 'button[class="pay"][type="submit"]'


@needs_php
def test_risky_code_is_listed_but_kept_as_it_was(tmp_path):
    notes = {(n["note"], n["line"]) for n in table(tmp_path)["notes"]}
    assert notes == {("dom_sink", 19), ("load_event", 19)}


def test_selector_escapes_quotes_and_leaves_out_style():
    assert inline_js.selector("A", {"title": 'say "hi"', "style": "color: red", "onclick": "f()", "hidden": True}) \
        == 'a[hidden][title="say \\"hi\\""]'
    assert inline_js.selector("button", {"onclick": "go()"}) is None


@needs_php
def test_without_inline_js_the_assets_layer_is_unchanged(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "page.php").write_text(PAGE, encoding="utf-8")
    index = {"app": "demo", "docroot": "", "shells": [], "functions": {}, "pages": [
        {"key": "page.php", "path": "page.php", "role": "page", "route": "/page", "group": "(plain)",
         "entrypoint": root.as_posix() + "/page.php", "crash": None}]}
    plain = legacy_assets.asset_table(index, PHP)
    assert "inline" not in plain["pages"][0]
    text = legacy_assets.data_module(plain)
    assert "steps" not in text and "c2-legacy-script-loader" not in text


@needs_php
def test_the_build_gets_the_loader_and_scripts_are_no_longer_server_rendered(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "page.php").write_text(PAGE, encoding="utf-8")
    index = {"app": "demo", "docroot": "", "shells": [], "functions": {}, "pages": [
        {"key": "page.php", "path": "page.php", "role": "page", "route": "/page", "group": "(plain)",
         "entrypoint": root.as_posix() + "/page.php", "crash": None}]}
    build = tmp_path / "build"
    (build / "src" / "app" / "(plain)").mkdir(parents=True)
    (build / "src" / "app" / "(plain)" / "layout.tsx").write_text("<html><body>{children}</body></html>",
                                                                    encoding="utf-8")
    legacy_assets.write(build, legacy_assets.asset_table(index, PHP, inline=True))
    data = (build / "src" / "c2-legacy-assets-data.ts").read_text(encoding="utf-8")
    page = json.loads(data.split("export const PAGES: Record<string, Assets> = ")[1].split(";\n\nexport")[0])
    assert page["page.php"]["scripts"] == [] and len(page["page.php"]["steps"]) == 3
    assert (build / "src" / "c2-legacy-script-loader.tsx").read_text(encoding="utf-8") == inline_js.LOADER
    assert "LegacyScriptLoader" in (build / "src" / "c2-legacy-assets.tsx").read_text(encoding="utf-8")


def test_the_loader_sets_handlers_first_and_runs_steps_in_order_after_hydration():
    loader = inline_js.LOADER
    assert loader.startswith('"use client";') and "useEffect(" in loader
    assert loader.index("element.setAttribute(h.event, h.code)") < loader.index("for (const step of steps) await run(step)")
    assert "script.async = false" in loader and "attributeNames(element) === h.names" in loader


def test_the_pipeline_and_the_evaluation_never_reach_the_inline_layer():
    for folder in ("src", "config", "tools/harness", "tools/eval"):
        for file in (ROOT / folder).rglob("*.py"):
            text = file.read_text(encoding="utf-8")  # (Stage 2's own `only_inline_js` is not the layer)
            assert "import inline_js" not in text and "tools/assets" not in text, file


@needs_php
def test_hms_cards_are_found_and_post_alerts_are_skipped():
    index = ROOT / "output" / "apps" / "hms" / "index.json"
    if not index.exists() or not (ROOT / "legacy-apps" / "hms").is_dir():
        pytest.skip("HMS run output or clone not on this machine")
    rows = legacy_assets.asset_table(json.loads(index.read_text(encoding="utf-8")), PHP, inline=True)["pages"]
    panel = next(r for r in rows if r["path"] == "admin-panel.php")["inline"]
    assert {h["code"] for h in panel["handlers"]} >= {"clickDiv('#list-home-list')", "clickDiv('#list-pat-list')",
                                                     "clickDiv('#list-pres-list')"}
    assert {s["line"] for s in panel["skipped"] if s["skip"] == "conditional" and s["file"] == "admin-panel.php"} \
        == {49, 52, 56, 60, 64, 74}
