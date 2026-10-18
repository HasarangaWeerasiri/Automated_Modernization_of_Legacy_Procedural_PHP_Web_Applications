"""The opt-in assets layer (tools/assets/legacy_assets.py): a deployment aid, not measured. It reads a run's index.json
and the legacy PHP source (PHP's tokenizer), loads the legacy page's own stylesheets and script files as they were,
and is never reached from the pipeline (src/, config/, tools/harness/) or from an evaluation run.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "assets"))
import legacy_assets  # noqa: E402

PHP = legacy_assets.find_php()
needs_php = pytest.mark.skipif(PHP is None, reason="PHP not found")

PAGE = """<?php include "parts/head.php"; include $dynamic; ?>
<html><head>
<link rel="stylesheet" href="css/site.css">
<!-- <link rel="stylesheet" href="old.css"> -->
<link rel="icon" href="favicon.png">
<link rel="stylesheet" href="<?= $theme ?>.css">
</head><body>
<?php function helper() { echo '<script src="never.js"></script>'; } ?>
<?php echo '<link href="https://cdn.example/' . $v . '/x.css" rel="stylesheet">'; ?>
<script>alert(1)</script>
<script src="https://cdn.example/jquery.js"></script>
<script src="js/app.js" defer></script>
</body></html>
"""

HEAD = '<link rel="stylesheet" href="parts/head.css" crossorigin="anonymous">\n'

SHELL = """<?php
function top() { ?>
<html><head><link rel="stylesheet" href="shell.css"><link rel="stylesheet" href="layout.css"></head><body>
<?php }
function other() { echo '<script src="other.js"></script>'; }
function bottom() { echo '<script src="shell.js"></script></body></html>'; }
"""


def app(tmp_path: Path) -> tuple[dict, Path]:
    root = tmp_path / "legacy"
    (root / "parts").mkdir(parents=True)
    (root / "css").mkdir()
    (root / "page.php").write_text(PAGE, encoding="utf-8")
    (root / "parts" / "head.php").write_text(HEAD, encoding="utf-8")
    (root / "core.php").write_text(SHELL, encoding="utf-8")
    (root / "blog.php").write_text('<?php include "core.php"; top(); ?><p>x</p><?php bottom(); ?>', encoding="utf-8")
    (root / "css" / "site.css").write_text("body{}", encoding="utf-8")
    prefix = root.as_posix() + "/"
    index = {"app": "demo", "docroot": "", "pages": [
        {"key": "page.php", "path": "page.php", "role": "page", "route": "/page", "group": "(plain)",
         "entrypoint": prefix + "page.php", "crash": None},
        {"key": "blog.php", "path": "blog.php", "role": "page", "route": "/blog", "group": "(top)",
         "entrypoint": prefix + "blog.php", "crash": None},
        {"key": "index.php", "path": "index.php", "role": "page", "route": "/", "group": "(plain)",
         "entrypoint": prefix + "index.php", "crash": None},
        {"key": "rss.php", "path": "rss.php", "role": "page", "route": "/rss", "crash": None,
         "entrypoint": prefix + "rss.php", "notGenerated": {"reason": "non_html_response"}}],
        "shells": [{"name": "TopShell", "group": "(top)", "header": "f1#1", "footer": "f1#2", "props": []}],
        "functions": {"f1#1": {"name": "top", "path": "core.php"}, "f1#2": {"name": "bottom", "path": "core.php"}}}
    (root / "index.php").write_text("<p>home</p>", encoding="utf-8")
    return index, root


def row(table: dict, path: str) -> dict:
    return next(r for r in table["pages"] if r["path"] == path)


# ---------------------------------------------------------------- what is collected


@needs_php
def test_a_page_loads_its_stylesheets_and_script_files_in_source_order(tmp_path):
    index, _ = app(tmp_path)
    page = row(legacy_assets.asset_table(index, PHP), "page.php")
    assert [s["attrs"]["href"] for s in page["styles"]] == ["parts/head.css", "css/site.css"]  # include first
    assert [s["attrs"]["src"] for s in page["scripts"]] == ["https://cdn.example/jquery.js", "js/app.js"]
    assert page["scripts"][1]["attrs"] == {"src": "js/app.js", "defer": True}
    assert page["styles"][0]["attrs"]["crossorigin"] == "anonymous" and page["styles"][0]["file"] == "parts/head.php"


@needs_php
def test_comments_icons_inline_scripts_and_function_bodies_are_left_out(tmp_path):
    index, _ = app(tmp_path)
    page = row(legacy_assets.asset_table(index, PHP), "page.php")
    urls = json.dumps(page)
    assert "old.css" not in urls and "favicon" not in urls and "never.js" not in urls and "alert" not in urls


@needs_php
def test_a_tag_or_include_built_by_php_is_skipped_and_listed_never_guessed(tmp_path):
    index, _ = app(tmp_path)
    page = row(legacy_assets.asset_table(index, PHP), "page.php")
    assert [(s["skip"], s["line"]) for s in page["skipped"]] == [
        ("include_not_literal", 1), ("tag_built_by_php", 6), ("tag_built_by_php", 9)]


@needs_php
def test_a_shell_page_takes_the_header_function_then_the_page_then_the_footer_function(tmp_path):
    index, _ = app(tmp_path)
    blog = row(legacy_assets.asset_table(index, PHP), "blog.php")
    assert [s["attrs"]["href"] for s in blog["styles"]] == ["shell.css", "layout.css"]
    assert [s["attrs"]["src"] for s in blog["scripts"]] == ["shell.js"]  # not other()'s


@needs_php
def test_a_stylesheet_the_shell_layout_already_has_is_not_added_twice(tmp_path):
    index, _ = app(tmp_path)
    build = tmp_path / "build"
    (build / "src" / "app" / "(top)").mkdir(parents=True)
    (build / "src" / "app" / "(top)" / "layout.tsx").write_text(
        '<html><head><link href="layout.css" rel="stylesheet" /></head><body>{children}</body></html>', encoding="utf-8")
    blog = row(legacy_assets.asset_table(index, PHP, build), "blog.php")
    assert [s["attrs"]["href"] for s in blog["styles"]] == ["shell.css"]


@needs_php
def test_pages_with_no_page_are_left_out_and_the_table_is_deterministic(tmp_path):
    index, _ = app(tmp_path)
    table = legacy_assets.asset_table(index, PHP)
    assert [r["path"] for r in table["pages"]] == ["blog.php", "index.php", "page.php"]
    assert row(table, "index.php")["styles"] == [] and row(table, "index.php")["scripts"] == []
    assert legacy_assets.asset_table(index, PHP) == table


# ---------------------------------------------------------------- the build project


def test_the_layout_gets_the_styles_in_head_and_the_scripts_after_the_children():
    plain = ('import type { ReactNode } from "react";\n\nexport default function RootLayout({ children }: '
             '{ children: ReactNode }) {\n  return (\n    <html>\n      <body>{children}</body>\n    </html>\n  );\n}\n')
    edited = legacy_assets.edit_layout(plain)
    assert edited.startswith(legacy_assets.IMPORT)
    assert "<html>\n      <head>\n        <LegacyStyles />\n      </head>" in edited
    assert "{children}\n        <LegacyScripts />" in edited
    assert legacy_assets.edit_layout(edited) == edited  # once
    shell = "<html><head><meta charSet=\"utf-8\" /></head><body>{children}</body></html>"
    assert "<meta charSet=\"utf-8\" />  <LegacyStyles />\n      </head>" in legacy_assets.edit_layout(shell)
    with pytest.raises(ValueError):
        legacy_assets.edit_layout("<html><body>{children}{children}</body></html>")


def test_the_data_module_maps_the_route_the_legacy_url_and_a_folder_index():
    table = {"app": "x", "docroot": "", "pages": [
        {"path": "admin/index.php", "route": "/admin", "styles": [], "scripts": [
            {"attrs": {"src": "a.js", "crossorigin": "anonymous"}}]},
        {"path": "index.php", "route": "/", "styles": [], "scripts": []}]}
    text = legacy_assets.data_module(table)
    urls = json.loads(text.split("PAGE_OF_URL: Record<string, string> = ")[1].split(";\n")[0])
    assert urls == {"/": "index.php", "/admin": "admin/index.php", "/admin/index.php": "admin/index.php",
                    "/index.php": "index.php"}
    assert '"crossOrigin": "anonymous"' in text and legacy_assets.data_module(table) == text


@needs_php
def test_write_needs_a_build_project_and_copies_only_static_files(tmp_path):
    index, _ = app(tmp_path)
    table = legacy_assets.asset_table(index, PHP)
    build = tmp_path / "build"
    with pytest.raises(FileNotFoundError):
        legacy_assets.write(build, table)
    (build / "src" / "app" / "(plain)").mkdir(parents=True)
    (build / "src" / "app" / "(plain)" / "page.tsx").write_text("x", encoding="utf-8")
    (build / "src" / "app" / "(plain)" / "layout.tsx").write_text("<html><body>{children}</body></html>",
                                                                    encoding="utf-8")
    assert legacy_assets.write(build, table) == 1
    files = sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file())
    assert files == ["assets.json", "public/css/site.css", "src/app/(plain)/layout.tsx", "src/app/(plain)/page.tsx",
                     "src/c2-legacy-assets-data.ts", "src/c2-legacy-assets.tsx", "src/proxy.ts"]
    assert (build / "src" / "app" / "(plain)" / "page.tsx").read_text(encoding="utf-8") == "x"  # pages untouched
    assert b"\r\n" not in (build / "src" / "proxy.ts").read_bytes()


# ---------------------------------------------------------------- off by default, never in the pipeline


def test_the_pipeline_and_the_evaluation_never_reach_the_assets_layer():
    for folder in ("src", "config", "tools/harness", "tools/eval"):
        for file in (ROOT / folder).rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            assert "legacy_assets" not in text and "tools/assets" not in text, file


def test_the_runtime_check_adds_it_only_with_assets():
    text = (ROOT / "tools" / "runtime" / "generated_app.py").read_text(encoding="utf-8")
    assert 'if ok and "--assets" in argv:' in text and text.count("legacy_assets.apply(") == 1


@needs_php
@pytest.mark.parametrize("app_name,page,styles,scripts", [
    ("hms", "index.php", 5, ["https://code.jquery.com/jquery-3.3.1.slim.min.js",
                             "https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.14.7/umd/popper.min.js",
                             "https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/js/bootstrap.min.js",
                             "https://stackpath.bootstrapcdn.com/bootstrap/3.4.1/js/bootstrap.min.js"]),
    ("attendance", "index.php", 4, ["https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/js/bootstrap.min.js"]),
    ("wackopicko", "website/about.php", 3, [])])
def test_the_discovery_apps(app_name, page, styles, scripts):
    index = ROOT / "output" / "apps" / app_name / "index.json"
    if not index.exists() or not (ROOT / "legacy-apps" / app_name).is_dir():
        pytest.skip(f"{app_name}: run output or clone not on this machine")
    found = row(legacy_assets.asset_table(json.loads(index.read_text(encoding="utf-8")), PHP), page)
    assert len(found["styles"]) == styles and [s["attrs"]["src"] for s in found["scripts"]] == scripts
