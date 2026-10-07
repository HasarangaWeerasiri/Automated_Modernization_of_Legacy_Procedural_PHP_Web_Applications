"""The tag tracker (src/mapping/tags.py), on small hand-made node sequences."""

from src.mapping import tags
from src.mapping.boundary_config import load_boundary_config
from src.model import OutputNode, Read

CONFIG = load_boundary_config()


def html(i, raw):
    return OutputNode(id=f"n{i}", kind="Stmt_InlineHTML", raw=raw, reads=(), enclosed_by=())


def echo(i, literal=None):
    kind, expr = ("literal", literal) if literal is not None else ("unresolved", "$x")
    read = Read(expr=expr, var=None if literal is not None else "$x", path=None if literal is not None else (),
                source_kind=kind, confidence="resolved" if literal is not None else "unresolved", source=None)
    return OutputNode(id=f"n{i}", kind="Stmt_Echo", raw=None, reads=(read,), enclosed_by=())


def track(*nodes):
    return tags.track(nodes, CONFIG)


def names(stack):
    return [e.tag for e in stack]


def test_stack_before_each_node():
    t = track(html(1, "<table><tbody>"), html(2, "<tr><td>"), echo(3), html(4, "</td></tr></tbody></table>"))
    assert names(t["n1"].stack_before) == []
    assert names(t["n2"].stack_before) == ["table", "tbody"]
    assert names(t["n3"].stack_before) == ["table", "tbody", "tr", "td"]
    assert names(t["n4"].stack_after) == []


def test_output_inside_an_open_tag_is_in_attribute_position():
    t = track(html(1, '<a href="page.php?id='), echo(2), html(3, '&x=1" class="b">go</a>'), echo(4))
    assert [t[n].in_tag_before for n in ("n1", "n2", "n3", "n4")] == [False, True, True, False]
    assert names(t["n3"].stack_after) == []


def test_a_tag_split_across_nodes_counts_once_in_the_node_where_it_starts():
    t = track(html(1, '<a href="'), echo(2), html(3, '"><button>x</button></a>'))
    assert [tags.element_count([t[n]]) for n in ("n1", "n2", "n3")] == [1, 0, 1]
    assert t["n1"].events[0].element.opened_in == "n1"


def test_greater_than_inside_a_quoted_attribute_does_not_end_the_tag():
    t = track(html(1, """<a onclick="return a > b ? 'x' : 'y'" href="#">"""), html(2, "text"))
    assert names(t["n2"].stack_before) == ["a"] and tags.element_count([t["n1"]]) == 1


def test_script_and_style_bodies_are_not_markup():
    t = track(html(1, "<script>if (a <b && c> d) { x = '<div>'; }</script><style>p > a {}</style><p>"), html(2, "x"))
    assert names(t["n2"].stack_before) == ["p"]
    assert [e.tag for e in t["n1"].events if e.kind == "open"] == ["script", "style", "p"]


def test_comments_and_doctype_are_skipped():
    t = track(html(1, "<!DOCTYPE html><!-- <div><table> --><!--[if IE]><b><![endif]--><p>"), html(2, "x"))
    assert names(t["n2"].stack_before) == ["p"] and tags.element_count([t["n1"]]) == 1


def test_void_and_self_closing_elements_are_counted_but_not_left_open():
    t = track(html(1, '<p><br><img src="a.png"><input type="text" /><x-icon/>'), html(2, "</p>"))
    assert names(t["n2"].stack_before) == ["p"]
    assert tags.element_count([t["n1"]]) == 5


def test_unmatched_closing_tag_is_ignored_and_recorded():
    t = track(html(1, "<tr><td>x</td></tr></a>"), html(2, "y"))
    assert tags.has_unmatched_close([t["n1"]]) and names(t["n2"].stack_before) == []
    assert [e.tag for e in t["n1"].events if e.kind == "unmatched_close"] == ["a"]


def test_closing_tag_also_closes_unclosed_elements_inside_it():
    t = track(html(1, "<ul><li>a<li>b</ul>"), html(2, "x"))
    assert names(t["n2"].stack_before) == [] and not tags.has_unmatched_close([t["n1"]])


def test_tag_names_are_case_insensitive():
    t = track(html(1, "<Span>x</span>"), html(2, "y"))
    assert names(t["n2"].stack_before) == []


def test_static_id_is_kept_and_a_dynamic_id_is_not():
    t = track(html(1, '<div class="c" id="related"><div id="row-'), echo(2), html(3, '">'))
    related, dynamic = t["n3"].stack_after
    assert (related.tag, related.element_id, dynamic.element_id) == ("div", "related", None)


def test_literal_echo_is_read_as_markup_and_other_echoes_are_not():
    t = track(echo(1, '"<b>Active</b>"'), echo(2), html(3, "x"))
    assert tags.element_count([t["n1"]]) == 1 and tags.element_count([t["n2"]]) == 0
    assert names(t["n3"].stack_before) == []


def test_balance_of_a_run_of_nodes():
    t = track(html(1, "<div><ul>"), html(2, "<li>a</li>"), html(3, "</ul></div><div><ul>"), html(4, "</ul></div>"))
    assert tags.is_balanced([t["n2"]])
    assert tags.closes_outer([t["n3"]]) and tags.leaves_open([t["n3"]])
    assert tags.leaves_open([t["n1"]]) and not tags.closes_outer([t["n1"]])
    assert tags.root_tags([t["n2"]]) == ("li",)


def test_root_tags_ignores_nested_elements():
    t = track(html(1, "<div>"), html(2, "<p><b>a</b></p><p>b</p>"), html(3, "</div>"))
    assert tags.root_tags([t["n2"]]) == ("p", "p")
