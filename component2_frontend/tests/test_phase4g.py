"""Roadmap Phase 4g: post-freeze change 5 (generation-spec v0.17 section 6, the last): a row count of the List's own
query is a translatable list-guard condition, only when (a)-(d) all hold; and the parameterised GETs of the phpBlog
runtime check.

The harness writes the facts (extension `rowCounts`, rules v1.1 only), the loader reads them, Stage 5 decides.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
sys.path.insert(0, str(ROOT / "tools" / "runtime" / "replay"))

from src.mapping.conditions import Scope, translate  # noqa: E402
from src.mapping.loader import load_timeline  # noqa: E402
from tests.test_harness import page, pytestmark  # noqa: E402, F401
from tests.test_phase3 import flat  # noqa: E402
from tests.test_phase4b import run_with  # noqa: E402
from tests.test_phase4c import run_tiny  # noqa: E402

OPS = ["==", "!=", "===", "!==", "<", ">", "<=", ">="]


def guard_page(cond="$count <= 0", count="$count = mysqli_num_rows($run);", between="", loop_on="$run",
               in_loop="", swap=False) -> str:
    """A List guard with an Empty arm, as phpBlog index.php 87-147: the count, the guard, the loop."""
    message, loop = ("echo '<p>None</p>';",
                     f"while ($row = mysqli_fetch_assoc({loop_on})) {{ {in_loop} "
                     "echo '<div class=\"card\">' . $row['name'] . '</div>'; }")
    first, second = (loop, message) if swap else (message, loop)
    return ("<?php $c = mysqli_connect('h', 'u', '', 'd');\n"
            "$run = mysqli_query($c, 'select id, name from users');\n"
            "$other = mysqli_query($c, 'select name, fee from doctors');\n"
            f"{count}\n{between} ?>\n"
            f'<div class="row"><h5>Recent</h5><?php\nif ({cond}) {{\n    {first}\n}} else {{\n    {second}\n}}\n'
            "?></div>")


def generate(tmp_path, php: str, mapping: str = "query") -> dict:
    result = run_with(tmp_path, {"page.php": php}, mapping)
    flags = [f["detail"] for f in result["stage"]("page.php", 5)["flags"] if f["flag"] == "row_count_guard"]
    return {"list": flat(result["file"]("components/PageList1.tsx")), "flags": flags, "result": result}


# ---------------------------------------------------------------- the translation (direct and variable forms)


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("literal", ["0", "1"])
@pytest.mark.parametrize("form, text", [("variable", "$count"), ("direct", "mysqli_num_rows($run)")])
def test_every_operator_with_0_and_1_translates(op, literal, form, text):
    scope = Scope(row_counts=((form, "$count" if form == "variable" else "$run", "items"),))
    assert translate(f"{text} {op} {literal}", scope, {}).code == f"items.length {op} {literal}"
    assert translate(f"{literal} {op} {text}", scope, {}).code == f"{literal} {op} items.length"


@pytest.mark.parametrize("cond", ["$count > 2", "$count == $count", "($count) >= 10", "$count == '0'",
                                  "mysqli_num_rows($other) > 0", "$n > 0", "count($rows) > 0"])
def test_any_other_use_of_a_count_is_not_translated(cond):
    scope = Scope(row_counts=(("variable", "$count", "items"), ("direct", "$run", "items")))
    assert translate(cond, scope, {}).code is None


def test_a_count_used_bare_is_items_length_above_0():
    scope = Scope(row_counts=(("variable", "$count", "items"),))
    assert translate("$count", scope, {}).code == "items.length > 0"
    assert translate("!$count", scope, {}).code == "!(items.length > 0)"


def test_without_the_scope_entry_a_count_is_not_translated():
    assert translate("$count <= 0", Scope(), {}).code is None  # Stage 5 adds it only when (a)-(d) hold
    assert translate("mysqli_num_rows($run) > 0", Scope(), {}).code is None


# ---------------------------------------------------------------- end to end: (a)-(d) all hold


@pytest.mark.parametrize("cond, count, swap, code", [
    ("$count <= 0", "$count = mysqli_num_rows($run);", False, "{items.length <= 0 ? (<PageList1Empty />) : (<>"),
    ("mysqli_num_rows($run) == 0", "", False, "{items.length == 0 ? (<PageList1Empty />) : (<>"),
    ("$count", "$count = mysqli_num_rows($run);", True, "{items.length > 0 ? (<>"),
    ("mysqli_num_rows($run) >= 1", "", True, "{items.length >= 1 ? (<>"),
], ids=["variable", "direct", "variable-bare", "direct-1"])
def test_a_count_of_the_lists_own_query_translates(tmp_path, cond, count, swap, code):
    out = generate(tmp_path, guard_page(cond, count, swap=swap))
    assert code in out["list"] and "unresolvedCondition" not in out["list"] and "Arms_" not in out["list"]
    assert len(out["flags"]) == 1 and out["flags"][0].startswith(f"{count.split(' =')[0] or 'mysqli_num_rows($run)'}: "
                                                                 "translated: items.length")


# ---------------------------------------------------------------- end to end: each condition failing on its own


@pytest.mark.parametrize("php, mapping, failed", [
    (guard_page(loop_on="$other"), "query", "(a) no guarded List iterates $run"),
    (guard_page(between="$first = mysqli_fetch_assoc($run);"), "query", "(b) mysqli_fetch_assoc at line 5"),
    (guard_page(between="mysqli_data_seek($run, 1);"), "query", "(b) mysqli_data_seek at line 5"),
    (guard_page(between="$run = mysqli_query($c, 'select id, name from users');"), "query",
     "(b) assignment to $run at line 5"),
    (guard_page(in_loop="if ($row['id'] == 2) { break; }"), "query", "(c) break / continue at line"),
    (guard_page(in_loop="if ($row['id'] == 2) { continue; }"), "query", "(c) break / continue at line"),
    (guard_page(), "none", "(d) the List's items have no data source"),
    # (assigned again after the count, it is also (b))
    (guard_page(count="if ($x) { $count = mysqli_num_rows($run); }"), "query",
     "$count: the count assignment does not certainly reach the condition"),
], ids=["a", "b-fetch", "b-seek", "b-reassign", "c-break", "c-continue", "d", "not-reaching"])
def test_each_failing_condition_alone_leaves_the_guard_unresolved(tmp_path, php, mapping, failed):
    out = generate(tmp_path, php, mapping)
    assert "items.length" not in out["list"].split("function Arms_")[0]
    (detail,) = out["flags"]
    assert "not translated: " in detail and failed in detail
    others = [x for x in "abcd" if x != failed[1]] if failed.startswith("(") else list("abcd")
    assert not [x for x in others if f"({x})" in detail]  # only that condition failed


def test_a_guard_around_several_lists_uses_the_list_that_iterates_the_counted_result(tmp_path):
    """R-I2b (phpBlog index.php 14: the carousel): the condition is in the page, the List's items come from its
    own fetch there."""
    php = ("<?php $c = mysqli_connect('h', 'u', '', 'd');\n"
           "$run = mysqli_query($c, 'select id, name from users');\n$count = mysqli_num_rows($run);\n"
           "if ($count > 0) { ?><ul><?php while ($row = mysqli_fetch_assoc($run)) { ?><li><b><?php echo $row['name']; ?>"
           "</b></li><?php } ?></ul><ol><?php $run2 = mysqli_query($c, 'select name, fee from doctors');\n"
           "while ($d = mysqli_fetch_assoc($run2)) { ?><li><i><?php echo $d['name']; ?></i></li><?php } ?></ol>"
           "<?php } ?>")
    result = run_with(tmp_path, {"page.php": php}, "query")
    page_tsx = flat(result["file"]("app/(plain)/page/page.tsx"))
    assert ("{apiC2ReplayT00100017.rows.length > 0 && (<><PageList1 items={apiC2ReplayT00100017.rows} />"
            "<PageList2 items={apiC2ReplayT00100052.rows} /></>)}") in page_tsx


def test_a_count_in_a_condition_that_is_not_a_list_guard_is_not_translated(tmp_path):
    php = guard_page() + "<?php if ($count > 0) { echo '<p>There are posts</p>'; } ?>"
    out = generate(tmp_path, php)
    page_tsx = out["result"]["file"]("app/(plain)/page/page.tsx")
    assert 'unresolvedCondition("' in page_tsx and "There are posts" in page_tsx  # R-I4, then-only: unchanged


# ---------------------------------------------------------------- the harness facts, the loader, rules v1.0


def test_the_harness_records_the_count_facts(tmp_path):
    timeline = page(tmp_path, guard_page(in_loop="if ($row['id'] == 2) { break; }"))
    (count,) = timeline["rowCounts"]
    assert (count["form"], count["count"], count["result"], count["reaches"], count["definitions"]) == (
        "variable", "$count", "$run", True, 1)
    (loop,) = count["loops"]
    assert loop["between"] == [] and len(loop["breaks"]) == 1


def test_rules_v10_writes_no_count_facts_and_translates_nothing(tmp_path):
    result = run_tiny(tmp_path, {"page.php": guard_page()}, rules="1.0")
    assert "rowCounts" not in result["timeline"]("page.php")
    assert not [f for f in result["stage"]("page.php", 5)["flags"] if f["flag"] == "row_count_guard"]


def test_the_loader_rejects_a_malformed_count_record(tmp_path):
    doc = json.loads((ROOT / "mocks" / "timeline_list_while.json").read_text(encoding="utf-8"))
    doc["rowCounts"] = [{"ifNodeId": "f001#00001", "form": "direct"}]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="rowCounts"):
        load_timeline(path)


# ---------------------------------------------------------------- phpBlog: parameterised GETs


def test_parameterised_gets_follow_their_unit_and_keep_the_bare_url():
    capture = pytest.importorskip("capture")
    found = [{"page": f"{name}.php", "file": f"{name}.php", "legacyPath": f"/{name}.php", "generatedPath": f"/{name}",
              "getOnly": True} for name in ("post", "category", "page")]
    found.append({"page": "index.php", "file": "index.php", "legacyPath": "/index.php", "generatedPath": "/",
                  "getOnly": False})
    rows = capture.with_gets(found, ROOT / "tools" / "runtime" / "phpblog" / "gets.json")
    assert rows[0]["page"] == "post.php" and "unit" not in rows[0]  # the bare URL stays
    assert rows[1] == found[0] | {"page": "post.php@name=welcome-to-the-blog", "unit": "post.php",
                                  "legacyPath": "/post.php?name=welcome-to-the-blog",
                                  "generatedPath": "/post?name=welcome-to-the-blog"}
    assert [r["page"] for r in rows].count("index.php") == 1 and len(rows) == 4 + 6
    with pytest.raises(SystemExit):
        capture.with_gets(found[3:], ROOT / "tools" / "runtime" / "phpblog" / "gets.json")  # a unit not generated


def test_the_gets_are_the_fixtures_own_slugs():
    gets = json.loads((ROOT / "tools" / "runtime" / "phpblog" / "gets.json").read_text(encoding="utf-8"))["gets"]
    fixture = (ROOT / "tools" / "runtime" / "phpblog" / "initdb" / "03-fixture.sql").read_text(encoding="utf-8")
    assert sorted(g["unit"] for g in gets) == ["category.php"] * 2 + ["page.php"] + ["post.php"] * 3
    for get in gets:
        slug = get["query"].removeprefix("name=")
        assert f"'{slug}'" in fixture or slug == "site-news"  # site-news: the seed's category (install/database.sql)


def test_a_count_variable_reused_in_one_block_is_read_at_its_reaching_definition(tmp_path):
    """phpBlog index.php 13 / 89: `$count` counts the featured posts, then the recent posts. At the second guard its
    value is certainly the second count (Phase 2a copy propagation); a reassignment between that count and the loop
    would be (b)."""
    out = generate(tmp_path, guard_page(count="$count = mysqli_num_rows($other);\n$count = mysqli_num_rows($run);"))
    assert "{items.length <= 0 ? (<PageList1Empty />)" in out["list"]
    (tmp_path / "t").mkdir()
    timeline = page(tmp_path / "t", guard_page(count="$count = mysqli_num_rows($other);\n$count = mysqli_num_rows($run);"))
    assert [(c["result"], c["reaches"], c["definitions"]) for c in timeline["rowCounts"]] == [("$run", True, 2)]


def test_a_count_compared_with_a_quoted_string_is_not_translated(tmp_path):
    """phpBlog core.php 861 / admin/dashboard.php 177 `$cmnts == "0"`: (a)-(d) hold, but a number compared with a
    string is not on the whitelist (spec section 6: string vs number), so the guard stays a gap (change 4)."""
    out = generate(tmp_path, guard_page('$count == "0"'))
    (detail,) = out["flags"]
    assert detail == "$count: (a)-(d) hold; not translated: the condition is not on the whitelist (condition_not_translatable)"
    assert "items.length" not in out["list"].split("function Arms_")[0]


def test_a_list_guard_generated_in_the_page_uses_the_lists_items_there(tmp_path):
    """phpBlog blog.php 29: the else arm holds the List and its pagination, so the guard is generated in the page;
    the count stands for the items the page passes to the List."""
    php = ("<?php $c = mysqli_connect('h', 'u', '', 'd');\n"
           "$run = mysqli_query($c, 'select id, name from users');\n$count = mysqli_num_rows($run); ?>\n"
           '<div class="card-body"><?php if (!is_numeric($p)) { echo \'<meta http-equiv="refresh" content="0; url=b">\';'
           " exit(); }\n"  # blog.php 21: output in the List's container besides the loop
           "if ($count <= 0) { echo '<p>None</p>'; } else {\n"
           "while ($row = mysqli_fetch_assoc($run)) { echo '<div class=\"card\">' . $row['name'] . '</div>'; }\n"
           "echo '<center>pages</center>'; } ?></div>")
    result = run_with(tmp_path, {"page.php": php}, "query")
    page_tsx = flat(result["file"]("app/(plain)/page/page.tsx"))
    assert ("{apiC2ReplayT00100017.rows.length <= 0 ? (<p>None</p>) : (<><PageList1 items={apiC2ReplayT00100017.rows} />"
            "<center>pages</center></>)}") in page_tsx
    (detail,) = [f["detail"] for f in result["stage"]("page.php", 5)["flags"] if f["flag"] == "row_count_guard"]
    assert detail == "$count: translated: apiC2ReplayT00100017.rows.length <= 0 (spec v0.17)"
