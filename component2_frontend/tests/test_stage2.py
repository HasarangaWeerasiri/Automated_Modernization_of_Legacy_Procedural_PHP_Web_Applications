"""Stage 2 (boundary inference).

Every expected outcome here comes from docs/php-analysis/boundary-rules.md (sections 2 to 6)
or from the owner's Stage 2 brief. Nothing is asserted about cases those do not cover.
"""

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.mapping.boundary_config import load_boundary_config
from src.mapping.loader import load_labels, load_timeline
from src.mapping.stage1_isolation import isolate_presentation
from src.mapping.stage2_boundaries import dumps, find, infer_boundaries, leaves, summary, tree_to_dict
from src.model import Concern, Enclosure, Label, OutputNode, Read, Timeline

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
FIXTURES = Path(__file__).parent / "fixtures"
HMS_MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases")
WP_MOCKS = ("wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")
ALL_MOCKS = HMS_MOCKS + WP_MOCKS
CONFIG = load_boundary_config()


def inputs(name, folder=MOCKS):
    return load_timeline(folder / f"timeline_{name}.json"), load_labels(folder / f"labels_{name}.json")


def build(name, config=CONFIG, folder=MOCKS):
    timeline, labels = inputs(name, folder)
    return infer_boundaries(isolate_presentation(timeline, labels), timeline, config)


@pytest.fixture(scope="module")
def trees():
    return {name: build(name) for name in ALL_MOCKS}


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def ifs_labelled(name, concern):
    timeline, labels = inputs(name)
    guards = {e.node_id for node in timeline.sequence for e in node.enclosed_by if e.role == "branch"}
    return {g for g in guards if labels[g].concern is concern}


def raw_of(name, text):
    """The id of the one output node whose raw contains `text`."""
    (node,) = [n for n in inputs(name)[0].sequence if n.raw and text in n.raw]
    return node.id


# ---------------------------------------------------------------- invariants, on every mock


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_every_kept_output_node_is_in_exactly_one_leaf(name, trees):
    timeline, labels = inputs(name)
    kept = [k.node_id for k in isolate_presentation(timeline, labels).kept if k.node_type == "output"]
    in_leaves = [node_id for leaf in leaves(trees[name]) for node_id in leaf.node_ids]
    assert in_leaves == kept, "each kept output node once, in sequence order"


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_a_node_covers_exactly_what_its_children_cover(name, trees):
    for node in walk(trees[name].root):
        if node.children:
            assert node.node_ids == tuple(i for child in node.children for i in child.node_ids)


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_two_runs_give_byte_identical_json(name, trees):
    assert dumps(build(name)) == dumps(trees[name])


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_no_decision_uses_rule_id_or_basis(name, trees):
    timeline, labels = inputs(name)
    scrambled = {k: Label(v.concern, "other-basis", "R99", v.reason) for k, v in labels.items()}
    tree = infer_boundaries(isolate_presentation(timeline, scrambled), timeline, CONFIG)
    assert dumps(tree) == dumps(trees[name])


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_counts_match_the_tree(name, trees):
    tree = trees[name]
    nodes = list(walk(tree.root))
    assert tree.counts.abstain == sum(n.status == "abstain" for n in nodes) == len(find(tree, type="Abstain"))
    assert tree.counts.review == sum(n.status == "review" for n in nodes)
    assert not find(tree, type="Layout"), "R-F2 is not implemented yet"


# ---------------------------------------------------------------- HMS (boundary-rules.md 6.1, 6.2)


@pytest.mark.parametrize("name", ["list_while", "list_foreach", "detail"])
def test_hms_table_loop_is_a_list_in_its_table_with_a_tr_item(name, trees):
    (lst,) = find(trees[name], type="List")
    assert (lst.container.has_wrapper, lst.container.tag) == (True, "table")
    assert lst.rules[:2] == ("R-L1", "R-L2a")
    (item,) = find(trees[name], type="Item")
    assert item.root_tags == ("tr",) and item in lst.children


@pytest.mark.parametrize("name", ["list_while", "list_foreach", "detail"])
def test_hms_status_cell_is_one_inline_conditional(name, trees):
    (chain,) = find(trees[name], rule="R-I6")
    assert chain.type == "InlineConditional" and len(chain.source_ids) == 3
    assert (chain.elements, chain.status, chain.reason) == (0, "review", "exclusivity_not_verified")


def canonical(tree):
    """Tree JSON with node ids renumbered by first use; the entrypoint is dropped."""
    ids = {}

    def walk_json(value, key=None):
        if isinstance(value, dict):
            return {k: walk_json(v, k) for k, v in value.items() if k != "entrypoint"}
        if isinstance(value, list):
            return [walk_json(v, key) for v in value]
        if key in ("nodeIds", "sourceIds", "openedIn") and value is not None:
            return ids.setdefault(value, f"#{len(ids)}")
        return value

    return walk_json(tree_to_dict(tree))


def test_while_and_foreach_give_identical_trees(trees):
    assert canonical(trees["list_while"]) == canonical(trees["list_foreach"])


def test_detail_cancel_link_is_a_small_inline_conditional(trees):
    (cancel,) = find(trees["detail"], rule="R-I4")
    assert (cancel.type, cancel.elements) == ("InlineConditional", 2)
    assert raw_of("detail", "Cancel</button>") in cancel.node_ids


def test_admin_display_guard_is_inline_and_authorization_guard_is_not_a_component(trees):
    tree = trees["admin"]
    (display_guard,) = ifs_labelled("admin", Concern.PRESENTATION)
    (auth_guard,) = ifs_labelled("admin", Concern.BUSINESS_LOGIC)
    (inline,) = find(tree, rule="R-I4")
    assert inline.type == "InlineConditional" and inline.source_ids == (display_guard,)
    built_from_auth = [n for n in walk(tree.root) if auth_guard in n.source_ids]
    assert [(n.type, n.status) for n in built_from_auth] == [("Abstain", "abstain")]


def test_edge_cases_business_logic_guard_around_a_loop_abstains_and_still_shows_the_list(trees):
    """R-I2 (v0.3): a business_logic guard is an authorization check, not an empty-state check."""
    tree = trees["edge_cases"]
    (guard,) = ifs_labelled("edge_cases", Concern.BUSINESS_LOGIC)
    (abstain,) = [n for n in walk(tree.root) if guard in n.source_ids]
    assert (abstain.type, abstain.status, abstain.reason) == ("Abstain", "abstain", "cuts_across_business_logic")
    (lst,) = find(tree, type="List")
    assert lst in abstain.children, "the List inside the guard is still built, under the Abstain"
    assert "R-I2" not in lst.rules and guard not in lst.source_ids
    assert (lst.container.tag, lst.status) == ("table", "ok")
    (item,) = find(tree, type="Item")
    assert item in lst.children and item.root_tags == ("tr",)


def test_edge_cases_undecided_guard_content_carries_review(trees):
    (guard,) = ifs_labelled("edge_cases", Concern.UNDECIDED)
    (node,) = [n for n in walk(trees["edge_cases"].root) if guard in n.source_ids]
    assert (node.status, node.reason) == ("review", "auth_or_display")


@pytest.mark.parametrize("name", ["list_while", "list_foreach", "detail"])
def test_mixed_loop_is_a_list_with_status_ok_and_its_concern_recorded(name, trees):
    """Section 4a: a loop that fetches rows in its header is information, not a review."""
    (lst,) = find(trees[name], type="List")
    assert (lst.status, lst.loop_concern) == ("ok", "mixed")
    assert tree_to_dict(trees[name])["tree"]["children"][1]["loopConcern"] == "mixed"


@pytest.mark.parametrize("name", ["edge_cases", "wp_guestbook", "wp_view"])
def test_loop_concern_is_recorded_on_every_list(name, trees):
    assert {lst.loop_concern for lst in find(trees[name], type="List")} == {"presentation"}
    assert all(n.loop_concern is None for n in walk(trees[name].root) if n.type != "List")


def test_only_reviews_named_by_the_status_rules_reach_the_tree(trees):
    """Section 4a: R-I6 chains, and Stage 1 reviews other than a mixed loop."""
    reviews = {name: sorted((n.type, n.reason) for n in walk(tree.root) if n.status == "review")
               for name, tree in trees.items()}
    chain = ("InlineConditional", "exclusivity_not_verified")
    assert reviews == {
        "list_while": [chain], "list_foreach": [chain], "detail": [chain], "admin": [],
        "edge_cases": [("InlineConditional", "auth_or_display")],
        "wp_guestbook": [], "wp_view": [], "wp_header": [], "wp_thumbnails": [],
        "wp_login": [("Page", "logic_or_display")],
    }


# ---------------------------------------------------------------- WackoPicko (boundary-rules.md 6.3, 6.4)


def test_guestbook_list_has_no_wrapper_and_is_guarded(trees):
    (lst,) = find(trees["wp_guestbook"], type="List")
    assert lst.container.has_wrapper is False and lst.container.tag is None
    assert {"R-L2b", "R-I2"} <= set(lst.rules)
    (item,) = find(trees["wp_guestbook"], type="Item")
    assert item.root_tags == ("p", "p")


def test_view_card_lists_take_their_div_and_own_the_heading(trees):
    tree = trees["wp_view"]
    lists = {lst.container.element_id: lst for lst in find(tree, type="List") if lst.container.has_wrapper}
    assert set(lists) == {"related", "same-upload"}
    for element_id, heading in (("related", 'id="related-title"'), ("same-upload", 'id="same-upload-title"')):
        lst = lists[element_id]
        assert lst.container.tag == "div" and {"R-L2a", "R-I2"} <= set(lst.rules)
        assert raw_of("wp_view", heading) in lst.node_ids, "the heading inside the guard belongs to the List"
        (item,) = [c for c in lst.children if c.type == "Item"]
        assert item.root_tags == ("div",)


def test_view_comments_list_has_no_wrapper_and_an_empty_state(trees):
    (comments,) = [lst for lst in find(trees["wp_view"], type="List") if not lst.container.has_wrapper]
    assert {"R-L2b", "R-I2"} <= set(comments.rules)
    (empty,) = [c for c in comments.children if c.type == "Empty"]
    assert empty.rule == "R-I2" and raw_of("wp_view", "No comments yet") in empty.node_ids


def test_login_page_guard_is_not_a_component(trees):
    tree = trees["wp_login"]
    assert (tree.root.type, tree.root.rule, len(tree.root.source_ids)) == ("Page", "R-I1", 1)
    assert not find(tree, type="ConditionalComponent") and not find(tree, type="InlineConditional")


def test_thumbnail_loop_abstains_as_chunked_and_the_ul_crossing_is_flagged(trees):
    tree = trees["wp_thumbnails"]
    (chunked,) = find(tree, reason="chunked_container")
    assert (chunked.type, chunked.rule, chunked.status) == ("Abstain", "R-L7", "abstain")
    (crossing,) = [n for n in walk(tree.root) if "tag_crosses_branch" in n.flags]
    assert crossing.status == "abstain" and chunked in crossing.children
    assert not find(tree, type="List")


def test_menu_attribute_conditionals_are_expressions_not_boundaries(trees):
    conditionals = find(trees["wp_header"], reason="attribute_conditional")
    assert len(conditionals) == 5, "boundary-rules.md 6.4: five attribute conditionals"
    assert all(n.type == "AttributeExpression" and n.rule == "R-I3" and not n.children for n in conditionals)


@pytest.mark.parametrize("name", ALL_MOCKS)
def test_fallback_rule_never_fires_in_the_rule_discovery_corpus(name, trees):
    assert not find(trees[name], type="ConditionalComponent"), "boundary-rules.md section 3: R-I5 never triggered"


# ---------------------------------------------------------------- thresholds come from config


def test_changing_small_if_max_elements_changes_the_outcome():
    """The Cancel link has 2 elements: inline under the default config, its own component under a tighter one."""
    tighter = dataclasses.replace(CONFIG, small_if_max_elements=1, fallback_component_min_elements=2)
    default_tree, tight_tree = build("detail"), build("detail", tighter)
    cancel = raw_of("detail", "Cancel</button>")
    assert [n.type for n in find(default_tree, rule="R-I4") if cancel in n.node_ids] == ["InlineConditional"]
    assert [n.type for n in find(tight_tree, rule="R-I5") if cancel in n.node_ids] == ["ConditionalComponent"]


def test_table_section_tags_come_from_config():
    no_walk_past = dataclasses.replace(CONFIG, table_section_tags=())
    (lst,) = find(build("list_while", no_walk_past), type="List")
    assert lst.container.tag == "tbody"


def test_a_size_between_the_two_thresholds_has_no_rule():
    gap = dataclasses.replace(CONFIG, small_if_max_elements=0, fallback_component_min_elements=5)
    cancel = raw_of("detail", "Cancel</button>")
    (node,) = [n for n in find(build("detail", gap), reason="no_rule") if cancel in n.node_ids]
    assert node.status == "abstain"


# ---------------------------------------------------------------- rules no mock exercises (tiny synthetic pages)

LOOP = Enclosure(node_id="s001#90001", kind="Stmt_Foreach", role="iteration", iter_expr="$rows",
                 iter_source_kind="unresolved", value_var="$row", key_var=None)
INNER_LOOP = dataclasses.replace(LOOP, node_id="s001#90002", iter_expr="$row['parts']", value_var="$part")
GUARD = Enclosure(node_id="s001#80001", kind="Stmt_If", role="branch", branch="then", cond_node_id="s001#80002")
_serial = iter(range(1, 10_000))


def html(raw, *chain):
    return OutputNode(id=f"s001#{next(_serial):05d}", kind="Stmt_InlineHTML", raw=raw, reads=(), enclosed_by=chain)


def echo(*chain):
    read = Read(expr="$x", var="$x", path=(), source_kind="unresolved", confidence="unresolved", source=None)
    return OutputNode(id=f"s001#{next(_serial):05d}", kind="Stmt_Echo", raw=None, reads=(read,), enclosed_by=chain)


def synthetic(*nodes, concern=Concern.PRESENTATION):
    timeline = Timeline("synthetic", "1.0", tuple(nodes), {}, {})
    ids = {n.id for n in nodes} | {e.node_id for n in nodes for e in n.enclosed_by}
    labels = {i: Label(concern if i == GUARD.node_id else Concern.PRESENTATION, "rule", "R00", "test") for i in ids}
    return infer_boundaries(isolate_presentation(timeline, labels), timeline, CONFIG)


def test_one_element_item_stays_an_inline_map():
    """R-L3: an <option> loop keeps the List but gets no Item component."""
    tree = synthetic(html("<select>"), html("<option>", LOOP), echo(LOOP), html("</option>", LOOP), html("</select>"))
    (lst,) = find(tree, type="List")
    assert lst.container.tag == "select" and "R-L3" in lst.rules
    assert not find(tree, type="Item")
    assert [(c.type, c.rule) for c in lst.children] == [("Static", "R-L3")]


def test_nested_loops_resolve_innermost_first():
    """R-L4: the inner List is one node of the outer Item."""
    tree = synthetic(
        html("<ul>"), html("<li><b>", LOOP), echo(LOOP), html("</b><ol>", LOOP),
        html("<li><i>", LOOP, INNER_LOOP), echo(LOOP, INNER_LOOP), html("</i></li>", LOOP, INNER_LOOP),
        html("</ol></li>", LOOP), html("</ul>"))
    outer, inner = find(tree, type="List")
    assert (outer.container.tag, inner.container.tag) == ("ul", "ol")
    (outer_item,) = [c for c in outer.children if c.type == "Item"]
    assert inner in outer_item.children


def test_loop_without_output_is_not_a_boundary():
    """R-L5."""
    tree = synthetic(html("<p>a</p>"), html("\n  ", LOOP), html("<p>b</p>"))
    assert not find(tree, type="List") and len(find(tree, rule="R-L5")) == 1


def test_large_content_if_falls_back_to_its_own_component():
    """R-I5: more elements than the fallback threshold, and none of R-I1 to R-I3 applies."""
    tree = synthetic(html("<h1>t</h1>"), html("<div><p>a</p><p>b</p><p>c</p></div>", GUARD), html("<hr>"))
    (component,) = find(tree, type="ConditionalComponent")
    assert (component.rule, component.elements) == ("R-I5", 4)


def test_large_content_if_labelled_business_logic_abstains():
    """Section 5: a boundary must not cut across a business_logic node."""
    tree = synthetic(html("<h1>t</h1>"), html("<div><p>a</p><p>b</p><p>c</p></div>", GUARD), html("<hr>"),
                     concern=Concern.BUSINESS_LOGIC)
    assert not find(tree, type="ConditionalComponent")
    assert [n.reason for n in find(tree, type="Abstain")] == ["cuts_across_business_logic"]


@pytest.mark.parametrize("concern", [Concern.DATA_ACCESS, Concern.MIXED, Concern.PRESENTATION])
def test_a_list_guard_may_touch_data(concern):
    """R-I2 and section 5 (v0.4): only a business_logic guard around a loop abstains."""
    # The <h1> outside the guard keeps it from being a page guard (R-I1).
    tree = synthetic(html("<h1>page</h1>"), html("<h2>t</h2>", GUARD), html("<ul>", GUARD),
                     html("<li><b>", GUARD, LOOP), echo(GUARD, LOOP), html("</b></li>", GUARD, LOOP),
                     html("</ul>", GUARD), concern=concern)
    (lst,) = find(tree, type="List")
    assert "R-I2" in lst.rules and GUARD.node_id in lst.source_ids and not find(tree, type="Abstain")


def test_a_business_logic_guard_around_a_loop_abstains_but_keeps_the_list():
    tree = synthetic(html("<h1>page</h1>"), html("<ul>", GUARD), html("<li><b>", GUARD, LOOP), echo(GUARD, LOOP),
                     html("</b></li>", GUARD, LOOP), html("</ul>", GUARD), concern=Concern.BUSINESS_LOGIC)
    (abstain,) = find(tree, type="Abstain")
    (lst,) = find(tree, type="List")
    assert abstain.reason == "cuts_across_business_logic" and lst in abstain.children and "R-I2" not in lst.rules


def test_unmatched_closing_tag_is_ignored_and_flagged():
    """R-L6."""
    tree = synthetic(html("<table><tr><td>x</td></tr></a></table>"))
    (leaf,) = leaves(tree)
    assert leaf.flags == ("unmatched_close",) and leaf.status == "ok"


def test_text_only_loop_has_no_rule():
    tree = synthetic(html("<p>"), echo(LOOP), html("</p>"))
    assert [n.reason for n in find(tree, type="Abstain")] == ["no_rule"]


# ---------------------------------------------------------------- fixtures


def test_switch_case_enclosure_abstains():
    """Section 5: no rule covers a node enclosed by switch_case."""
    tree = build("switch_case", folder=FIXTURES)
    (abstain,) = find(tree, type="Abstain")
    assert abstain.reason == "switch_case_enclosure" and abstain.node_ids == ("t002#00006",)


def test_unlabelled_output_node_carries_review_into_its_leaf():
    tree = build("unlabelled", folder=FIXTURES)
    (leaf,) = [n for n in leaves(tree) if "t001#00005" in n.node_ids]
    assert (leaf.status, leaf.reason) == ("review", "unlabelled")


# ---------------------------------------------------------------- CLI


def test_cli_prints_the_tree_and_writes_json(trees):
    # The printed tree uses box-drawing characters; UTF-8 on both ends keeps this independent of the console code page.
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--timeline", "mocks/timeline_wp_guestbook.json",
         "--labels", "mocks/labels_wp_guestbook.json", "--stage", "2"],
        cwd=ROOT, capture_output=True, encoding="utf-8", check=True, env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert summary(trees["wp_guestbook"]) in proc.stdout and "R-L2b" in proc.stdout
    written = json.loads((ROOT / "output" / "stage2_wp_guestbook.json").read_text(encoding="utf-8"))
    assert written == tree_to_dict(trees["wp_guestbook"])
