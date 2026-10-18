"""The opt-in routing layer (tools/routing/routes.py): a deployment aid, not measured. It reads a run's index.json
only, writes next.config.ts rewrites / redirects into a build project, and is never reached from the pipeline
(src/, config/, tools/harness/) or from an evaluation run.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "routing"))
import routes  # noqa: E402


def page(path, route, role="page", **extra):
    return {"key": path.replace("/", "__"), "path": path, "role": role, "route": route, "crash": None} | extra


INDEX = {"app": "demo", "docroot": "", "pages": [
    page("index.php", "/"),
    page("logout.php", "/logout"),
    page("admin/index.php", "/admin"),
    page("admin/add_post.php", "/admin/add_post"),
    page("rss.php", "/rss", notGenerated={"reason": "non_html_response"}),
    page("broken.php", "/broken", crash={"error": "x", "where": ""}),
    page("func.php#show", None, role="component", component="Show"),
]}


def test_a_php_url_is_rewritten_to_its_route_and_an_index_to_its_folder():
    table = routes.route_table(INDEX)
    assert [(r["source"], r["destination"]) for r in table["rewrites"]] == [
        ("/admin/add_post.php", "/admin/add_post"), ("/admin/index.php", "/admin"), ("/index.php", "/"),
        ("/logout.php", "/logout")]


def test_a_folder_index_redirects_to_its_php_url_so_relative_links_resolve_inside_the_folder():
    table = routes.route_table(INDEX)
    assert [(r["source"], r["destination"], r["permanent"]) for r in table["redirects"]] == [
        ("/admin", "/admin/index.php", False)]  # the root index needs none


def test_a_file_with_no_page_gets_no_rule_and_is_listed_with_its_reason():
    table = routes.route_table(INDEX)
    assert table["noPage"] == [{"path": "broken.php", "url": "/broken.php", "reason": "crash"},
                               {"path": "rss.php", "url": "/rss.php", "reason": "non_html_response"}]
    sources = {r["source"] for r in table["rewrites"] + table["redirects"]}
    assert not {"/rss.php", "/broken.php", "/rss", "/broken"} & sources
    assert all("func.php" not in r["path"] for r in table["rewrites"])  # components are not URLs


def test_urls_start_at_the_document_root():
    index = {"app": "w", "docroot": "website", "pages": [page("website/users/login.php", "/users/login"),
                                                         page("website/index.php", "/"),
                                                         page("tools/seed.php", "/seed")]}
    table = routes.route_table(index)
    assert [(r["source"], r["destination"]) for r in table["rewrites"]] == [("/index.php", "/"),
                                                                            ("/users/login.php", "/users/login")]
    assert table["noPage"] == [{"path": "tools/seed.php", "url": None, "reason": "outside_docroot"}]


def test_a_path_that_is_not_literal_is_refused_not_escaped_by_guess():
    for path in ("a(b).php", "my page.php", "x:y.php"):
        with pytest.raises(routes.RouteError):
            routes.route_table({"app": "x", "docroot": "", "pages": [page(path, "/" + path[:-4])]})


def test_the_config_is_deterministic_and_carries_every_rule():
    text = routes.next_config(routes.route_table(INDEX))
    assert text == routes.next_config(routes.route_table(json.loads(json.dumps(INDEX))))
    assert '{ source: "/logout.php", destination: "/logout" },' in text
    assert '{ source: "/admin", destination: "/admin/index.php", permanent: false },' in text
    assert "async rewrites()" in text and "async redirects()" in text and "export default nextConfig;" in text
    empty = routes.next_config(routes.route_table({"app": "e", "docroot": "", "pages": []}))
    assert "return [];" in empty


def test_write_needs_a_build_project_and_writes_only_its_two_files(tmp_path):
    table = routes.route_table(INDEX)
    with pytest.raises(FileNotFoundError):
        routes.write(tmp_path, table)
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "page.tsx").write_text("x", encoding="utf-8")
    routes.write(tmp_path, table)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        "next.config.ts", "routes.json", "src/app/page.tsx"]
    assert json.loads((tmp_path / "routes.json").read_text(encoding="utf-8")) == table
    assert b"\r\n" not in (tmp_path / "next.config.ts").read_bytes()


def test_the_cli_prints_the_table_without_touching_a_build(tmp_path, capsys):
    index = tmp_path / "index.json"
    index.write_text(json.dumps(INDEX), encoding="utf-8")
    assert routes.main([str(index), "--out", str(tmp_path / "routes.json")]) == 0
    assert "demo: 4 rewrites, 1 redirects, 2 files with no page" in capsys.readouterr().out
    assert json.loads((tmp_path / "routes.json").read_text(encoding="utf-8")) == routes.route_table(INDEX)


# ---------------------------------------------------------------- off by default, never in the pipeline


def test_the_pipeline_and_the_evaluation_never_reach_the_routing_layer():
    for folder in ("src", "config", "tools/harness", "tools/eval"):
        for file in (ROOT / folder).rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            assert "routing" not in text and "import routes" not in text, file


def test_the_plain_build_config_has_no_routing():
    config = ROOT / "output" / "test-app" / "next.config.ts"  # what compile_check.next_build copies
    if not config.exists():
        pytest.skip("test app not set up on this machine")
    text = config.read_text(encoding="utf-8")
    assert "rewrites" not in text and "redirects" not in text


def test_the_runtime_check_adds_it_only_with_routes():
    text = (ROOT / "tools" / "runtime" / "generated_app.py").read_text(encoding="utf-8")
    assert 'if ok and "--routes" in argv:' in text and text.count("routes.apply(") == 1


@pytest.mark.parametrize("app,rewrites,redirects,no_page", [
    ("hms", 26, 0, []), ("phpblog", 43, 2, ["core.php", "install/core.php", "rss.php", "sitemap.php"]),
    ("warehouse", 29, 0, ["ajax.php"]), ("wackopicko", 30, 0, [])])
def test_the_discovery_apps(app, rewrites, redirects, no_page):
    index = ROOT / "output" / "apps" / app / "index.json"
    if not index.exists():
        pytest.skip(f"{index} not on this machine (run tools/harness/run_apps.py)")
    table = routes.route_table(json.loads(index.read_text(encoding="utf-8")))
    assert (len(table["rewrites"]), len(table["redirects"]), [n["path"] for n in table["noPage"]]) == \
        (rewrites, redirects, no_page)
