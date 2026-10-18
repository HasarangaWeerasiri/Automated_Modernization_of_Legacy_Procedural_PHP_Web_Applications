"""Roadmap Phase 4f: post-freeze change 4 (generation spec v0.16 section 6, "an untranslatable condition never
selects an arm") and phpBlog's recorded data fixture.

Every construct of the section 6 table, with an untranslatable condition (the whole conditional renders nothing:
one C2Todo, its arms in an unused `Arms_<nodeId>()` function; a two-valued attribute is omitted) and with a
translated one (unchanged); `then`-only conditionals unchanged; rules v1.0 unchanged (the freeze guard itself is
tests/test_phase4c.py). Then the fixture file and the classifier's `data` scope of reviewed findings.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
sys.path.insert(0, str(ROOT / "tools" / "runtime"))
import classify  # noqa: E402

from tests.test_harness import pytestmark  # noqa: E402, F401
from tests.test_phase2a import stages  # noqa: E402
from tests.test_phase3 import flat, run  # noqa: E402

PHPBLOG_RUNTIME = ROOT / "tools" / "runtime" / "phpblog"
NO_ARM = "untranslatable condition: no arm is chosen (spec v0.16)"


def gaps(result: dict) -> list:
    """(node id, reason, function) of the change-4 C2Todos."""
    return [(t["nodeId"], t["reason"], t["function"]) for t in result["stage5"]["c2todos"]
            if t["kind"] == "C2Todo" and t["detail"].startswith(NO_ARM)]


def function(text: str, name: str) -> str:
    """The source of one top-level function of a generated file (up to the next one)."""
    start = text.index(f"function {name}(")
    following = text.find("\nfunction ", start + 1)
    return text[start:following if following >= 0 else len(text)]


def page_of(result: dict) -> str:
    return result["files"]["app/page/page.tsx"]


# ---------------------------------------------------------------- if / else, if / elseif / else

IF_ELSE = "<div><?php if ($a == 1) { ?><p>One</p><?php } else { ?><p>Two</p><?php } ?></div>"
ELSEIF = ("<div><?php if ($a == 1) { ?><p>One</p><?php } elseif ($a == 2) { ?><p>Two</p><?php } else { ?>"
          "<p>Three</p><?php } ?></div>")


@pytest.mark.parametrize("php", [IF_ELSE, ELSEIF], ids=["if-else", "if-elseif-else"])
def test_an_if_else_on_an_untranslatable_condition_renders_nothing(tmp_path, php):
    result = stages(tmp_path, php)
    (gap,) = gaps(result)
    if_id, reason, where = gap
    assert (reason, where) == ("condition_not_translatable", "Page")
    name = f"Arms_{if_id.replace('#', '_')}"
    page = page_of(result)
    rendered = function(page, "Page")
    assert f'<div><C2Todo id="{if_id}" reason="condition_not_translatable" /></div>' in flat(rendered)
    assert "<p>" not in rendered and "unresolvedCondition(" not in rendered  # no arm is rendered
    arms = flat(function(page, name))  # every arm is kept, unused
    assert "<p>One</p>" in arms and "<p>Two</p>" in arms and f"{name}(" not in rendered
    assert ("<p>Three</p>" in arms) == (php == ELSEIF)
    todo = next(t for t in result["stage5"]["c2todos"] if t["detail"].startswith(NO_ARM))
    assert todo["detail"].endswith(f"the arms are in {name}()") and todo["severity"] == "todo"


def test_rules_v10_still_renders_the_else_arm_by_default(tmp_path):
    """The frozen behaviour (spec v0.13 section 6), kept under --rules v1.0."""
    result = stages(tmp_path, IF_ELSE, rules="v1.0")
    assert not gaps(result) and "Arms_" not in page_of(result)
    assert ') ? (<p>One</p>) : (<p>Two</p>)}' in flat(page_of(result))


def test_an_if_else_on_a_translated_condition_is_unchanged(tmp_path):
    files = {"lib.php": "<?php function item($page) { if ($page == 'home') { ?><b>Home</b><?php } else { ?>"
                        "<i>Other</i><?php } }",
             "page.php": "<?php include 'lib.php'; ?><ul><?php item('home'); ?></ul>"}
    item = run(tmp_path, files)["file"]("components/Item.tsx")
    assert '{page == "home" ? (<b>Home</b>) : (<i>Other</i>)}' in flat(item) and "Arms_" not in item


def test_a_then_only_conditional_is_unchanged(tmp_path):
    result = stages(tmp_path, "<div><?php if ($a == 1) { ?><p>One</p><?php } ?></div>")
    assert not gaps(result) and "Arms_" not in page_of(result)
    assert '"condition_not_translatable") && (<p>One</p>)}' in flat(page_of(result))


def test_an_else_after_a_silent_then_renders_nothing(tmp_path):
    """The `then` arm prints nothing (its condition is not in the tree: condition_unavailable); the else arm would
    be chosen by it."""
    result = stages(tmp_path, "<div><?php if ($a) { $x = 1; } else { ?><p>B</p><?php } ?></div>")
    ((if_id, reason, _),) = gaps(result)
    assert reason == "condition_unavailable" and "<p>B</p>" not in function(page_of(result), "Page")
    assert "<p>B</p>" in function(page_of(result), f"Arms_{if_id.replace('#', '_')}")


# ---------------------------------------------------------------- R-I8 case 2: cond ? arm : rest


def test_an_exit_arm_with_a_rest_renders_neither(tmp_path):
    result = stages(tmp_path, "<?php if ($error) { echo '<b>Stop</b>'; exit; } ?><h1>Page</h1><p>rest</p>")
    ((if_id, reason, _),) = gaps(result)
    rendered, arms = function(page_of(result), "Page"), flat(function(page_of(result), f"Arms_{if_id.replace('#', '_')}"))
    assert "<b>Stop</b>" not in rendered and "<h1>Page</h1>" not in rendered
    assert ') ? (<b>Stop</b>) : (<><h1>Page</h1><p>rest</p></>)}' in arms


def test_rules_v10_keeps_the_exit_arm_ternary(tmp_path):
    """(A translated R-I8 case 2 does not occur: a page's conditions have no translatable name, an exit arm in a
    function is R-I8c and one in a loop abstains, exit_arm_not_covered. Its decision is the one tested above.)"""
    result = stages(tmp_path, "<?php if ($error) { echo '<b>Stop</b>'; exit; } ?><h1>Page</h1><p>rest</p>",
                    rules="v1.0")
    assert not gaps(result) and ') ? (<b>Stop</b>) : (<><h1>Page</h1><p>rest</p></>)}' in flat(page_of(result))


# ---------------------------------------------------------------- R-I2 / R-I2b with an Empty arm

ONE_LOOP = ("<div><?php if (%s) { ?><ul><?php foreach ($a as $x) { ?><li><b>a</b></li><?php } ?></ul><?php } "
            "else { ?><p>None</p><?php } ?></div>")
TWO_LOOPS = ("<div><?php if ($count > 0) { ?><ul><?php foreach ($a as $x) { ?><li><b>a</b></li><?php } ?></ul>"
             "<ol><?php foreach ($b as $y) { ?><li><i>b</i></li><?php } ?></ol><?php } %s ?></div>")


def test_a_list_guard_with_an_empty_on_an_untranslatable_condition_renders_neither(tmp_path):
    result = stages(tmp_path, ONE_LOOP % "$count > 0")
    ((if_id, reason, where),) = gaps(result)
    assert (reason, where) == ("condition_not_translatable", "PageList1")
    listing = result["files"]["components/PageList1.tsx"]
    assert "items.map" not in function(listing, "PageList1") and "<PageList1Empty />" not in function(listing,
                                                                                                    "PageList1")
    arms = flat(function(listing, f"Arms_{if_id.replace('#', '_')}"))
    assert arms.startswith(f"function Arms_{if_id.replace('#', '_')}({{ items }}: ")  # the List's own props
    assert "items.map((row, i) => (<PageList1Item key={i} row={row} />))" in arms and "<PageList1Empty />" in arms


def test_a_list_guard_on_its_own_collection_is_translated_and_unchanged(tmp_path):
    result = stages(tmp_path, ONE_LOOP % "$a")
    assert not gaps(result)
    assert "{items.length > 0 ? (" in result["files"]["components/PageList1.tsx"]


def test_a_guard_around_several_lists_with_an_empty_renders_neither(tmp_path):
    result = stages(tmp_path, TWO_LOOPS % "else { ?><p>Nothing yet</p><?php }")
    ((if_id, _, _),) = gaps(result)
    rendered = function(page_of(result), "Page")
    assert "<PageList1" not in rendered and "<PageList1Empty" not in rendered
    assert ') ? (<><PageList1 items={[]} /><PageList2 items={[]} /></>) : (<PageList1Empty />)}' in flat(
        function(page_of(result), f"Arms_{if_id.replace('#', '_')}"))


def test_a_guard_around_several_lists_without_an_empty_is_unchanged(tmp_path):
    result = stages(tmp_path, TWO_LOOPS % "")
    assert not gaps(result) and ') && (<><PageList1 items={[]} /><PageList2 items={[]} /></>)}' in flat(
        page_of(result))


# ---------------------------------------------------------------- R-I3b: an attribute with two values


def attribute_gaps(result: dict) -> list:
    return [(f["reason"], f["detail"]) for f in result["stage5"]["flags"]
            if f["flag"] == "attribute_omitted" and f["detail"].endswith("no value is chosen (spec v0.16)")]


@pytest.mark.parametrize("php", [
    "<a href=\"/x\" <?php if ($on) { echo 'class=\"on\"'; } else { echo 'class=\"off\"'; } ?>>x</a>",  # else
    "<a <?php if ($on) { echo 'class=\"on\"'; } ?> class=\"off\" href=\"/x\">x</a>",  # the later attribute
], ids=["else", "later"])
def test_a_two_valued_attribute_on_an_untranslatable_condition_is_omitted(tmp_path, php):
    result = stages(tmp_path, php)
    assert attribute_gaps(result) == [("condition_not_translatable", "class on <a>: no value is chosen (spec v0.16)")]
    assert '<a href="/x">x</a>' in flat(page_of(result)) and "unresolvedCondition(" not in page_of(result)


@pytest.mark.parametrize("php", [
    '<input type="text" name="u" <?php if ($error == 1) { echo \'autofocus\'; } ?> required>',  # present or not
    "<a href=\"/x\" <?php if ($on) { echo 'class=\"on\"'; } ?>>x</a>",  # then-only: a value or none
    "<a href=\"/x\" <?php if ($on) { echo 'class=\"on\"'; } else { echo 'class=\"on\"'; } ?>>x</a>",  # one value
], ids=["boolean", "then-only", "same-value"])
def test_an_attribute_with_at_most_one_value_is_unchanged(tmp_path, php):
    result = stages(tmp_path, php)
    assert not attribute_gaps(result) and "unresolvedCondition(" in page_of(result)


def test_a_two_valued_attribute_on_a_translated_condition_is_unchanged(tmp_path):
    files = {"lib.php": "<?php function item($page) { ?><li <?php if ($page == 'home') { echo 'class=\"current\"'; }"
                        " else { echo 'class=\"other\"'; } ?>>Home</li><?php }",
             "page.php": "<?php include 'lib.php'; ?><ul><?php item('home'); ?></ul>"}
    item = run(tmp_path, files)["file"]("components/Item.tsx")
    assert '<li className={page == "home" ? "current" : "other"}>Home</li>' in flat(item)


def test_rules_v10_keeps_the_two_valued_attribute(tmp_path):
    php = "<a href=\"/x\" <?php if ($on) { echo 'class=\"on\"'; } else { echo 'class=\"off\"'; } ?>>x</a>"
    result = stages(tmp_path, php, rules="v1.0")
    assert not attribute_gaps(result) and ') ? "on" : "off"}' in page_of(result)


# ---------------------------------------------------------------- phpBlog's recorded fixture (data only)


def inserts(sql: str) -> dict:
    """{table: [row tuples as text]} of the INSERT statements."""
    out = {}
    for table, values in re.findall(r"INSERT INTO `(\w+)` \([^)]*\) VALUES\s*(.*?);\s*$", sql, re.S | re.M):
        out[table] = re.findall(r"^\((.*)\),?$", values, re.M)
    return out


def test_the_fixture_is_data_only_and_holds_what_the_owner_asked_for():
    sql = (PHPBLOG_RUNTIME / "initdb" / "03-fixture.sql").read_text(encoding="utf-8")
    statements = [s.strip() for s in re.split(r";\s*$", re.sub(r"--[^\n]*", "", sql), flags=re.M) if s.strip()]
    assert statements and all(s.startswith("INSERT INTO `") for s in statements)  # no DDL, UPDATE or DELETE
    rows = inserts(sql)
    assert set(rows) == {"categories", "posts", "pages", "comments", "menu"}  # no users: nobody logs in
    assert len(rows["posts"]) == 3 and all("'Yes', " in r for r in rows["posts"])  # published
    assert {r.split(", ")[1] for r in rows["posts"]} == {"1", "2"}  # 2 categories: the seed's Site News + 1 new
    assert len(rows["categories"]) == 1 and rows["categories"][0].startswith("2, ")
    assert len(rows["pages"]) == 1 and len(rows["menu"]) == 1 and "'page?name=terms'" in rows["menu"][0]
    assert len(rows["comments"]) == 2 and all(r.endswith("'Yes', 'Yes'") for r in rows["comments"])  # approved


def test_the_fixture_is_loaded_after_the_seed_only_by_its_override():
    base = (PHPBLOG_RUNTIME / "docker-compose.yml").read_text(encoding="utf-8")
    override = (PHPBLOG_RUNTIME / "docker-compose.fixture.yml").read_text(encoding="utf-8")
    assert "03-fixture" not in base  # the shipped-seed run does not load it
    mounts = re.findall(r"docker-entrypoint-initdb\.d/(\S+?):ro", base + override)
    assert mounts == ["01-database.sql", "02-replay-user.sql", "03-fixture.sql"]  # run in name order: seed first
    assert "./initdb/03-fixture.sql:/docker-entrypoint-initdb.d/03-fixture.sql:ro" in override


# ---------------------------------------------------------------- the classifier: reviewed findings by data


def test_a_reviewed_finding_applies_only_to_the_data_it_was_reviewed_on(tmp_path):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"findings": [
        {"page": "a.php", "path": "body>div[0]", "rules": "1.1", "class": "flag:x", "rootCause": "seed default"},
        {"page": "a.php", "path": "body>div[1]", "rules": ["1.0", "1.1"], "data": "fixture", "class": "flag:x",
         "rootCause": "fixture only"},
        {"page": "b.php", "path": "*", "rules": ["1.0", "1.1"], "data": ["seed", "fixture"], "class": "env:x",
         "rootCause": "both"}]}), encoding="utf-8")
    assert set(classify.reviewed_notes(path, "1.1", "seed")) == {("a.php", "body>div[0]"), ("b.php", "*")}
    assert set(classify.reviewed_notes(path, "1.1", "fixture")) == {("a.php", "body>div[1]"), ("b.php", "*")}
    assert set(classify.reviewed_notes(path, "1.0", "seed")) == {("b.php", "*")}
    assert classify.reviewed_notes(tmp_path / "none.json", "1.1", "seed") == {}


def test_the_phpblog_findings_name_their_data():
    notes = {d: classify.reviewed_notes(PHPBLOG_RUNTIME / "findings.json", "1.1", d) for d in ("seed", "fixture")}
    # the Recent Posts row: div[0] on the seed, div[1] with the fixture's carousel before it
    assert ("index.php", "body>div[1]>div[0]>div[0]>div[0]") in notes["seed"]
    assert ("index.php", "body>div[1]>div[0]>div[0]>div[1]") in notes["fixture"]
    assert ("index.php", "body>div[1]>div[0]>div[0]>div[0]") not in notes["fixture"]
    assert ("install__upgrade-v2.2.php", "*") in notes["seed"] and ("install__upgrade-v2.2.php", "*") in notes[
        "fixture"]
