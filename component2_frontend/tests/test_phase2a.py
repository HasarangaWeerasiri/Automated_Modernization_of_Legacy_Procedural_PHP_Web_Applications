"""Roadmap Phase 2a: echo-string parts, copy propagation, interpolated-SQL values, R-L3b, the module rule.

Harness side (tools/harness/harness.py): `parts` on echo entries, reads through a copied variable,
and queries whose SQL interpolates values only - each with its "stays unresolved" cases. Pipeline
side: parts through the loader, the tag tracker, Stage 2 and Stage 5; `markup_from_expression`;
R-L3b; every generated file is a module. boundary-rules v0.10 sections 1-2, generation-spec v0.5
sections 5a and 8.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import results_table  # noqa: E402
import run_apps  # noqa: E402

from src.mapping.boundary_config import load_boundary_config  # noqa: E402
from src.mapping.generation_config import load_generation_config  # noqa: E402
from src.mapping.loader import load_contract, load_timeline  # noqa: E402
from tests.test_harness import FETCH, USERS, harness_app, kinds, page, pytestmark, reads  # noqa: E402, F401

MOCKS = ROOT / "mocks"


def echo(tmp_path, php: str, schema=None) -> dict:
    """The one echo / print entry of a page."""
    (entry,) = [e for e in page(tmp_path, php, schema)["sequence"] if e["kind"] in ("Stmt_Echo", "Expr_Print")]
    return entry


def shape(entry: dict) -> list[tuple]:
    """The parts of an entry as (kind, text or source, read index)."""
    return [(p["kind"], p.get("value", p.get("expr")), p.get("read")) for p in entry["parts"]]


def stages(tmp_path, php: str, schema=None) -> dict:
    """Stages 1-5 on one PHP page: its timeline, the Stage 2 nodes, the Stage 5 report and the generated files."""
    timeline = page(tmp_path, php, schema)
    path = tmp_path / "timeline.json"
    path.write_text(harness_app.dump_timeline(timeline), encoding="utf-8")
    folder = tmp_path / "out"
    folder.mkdir()
    run_apps.run_page(path, folder, load_boundary_config(), load_generation_config(),
                      load_contract(run_apps.EMPTY_CONTRACT))
    stage2 = json.loads((folder / "stage2.json").read_text(encoding="utf-8"))
    files = {p.relative_to(folder / "generated").as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((folder / "generated").rglob("*")) if p.is_file()}
    return {"timeline": timeline, "nodes": list(apps_results_table._walk(stage2["tree"])), "files": files,
            "stage5": json.loads((folder / "stage5.json").read_text(encoding="utf-8")), "path": path}


# ---------------------------------------------------------------- (a) parts on echo entries


def test_an_interpolated_string_is_split_into_literal_and_expression_parts(tmp_path):
    entry = echo(tmp_path, '<?php echo "<td>$name</td>";')
    assert shape(entry) == [("literal", "<td>", None), ("expr", "$name", 0), ("literal", "</td>", None)]
    assert [(r["expr"], r["sourceKind"]) for r in entry["reads"]] == [("$name", "unresolved")]  # its own read
    ids = [p["nodeId"] for p in entry["parts"]]
    assert ids == sorted(set(ids)) and all(i.startswith("t001#") and i > entry["id"] for i in ids)  # AST nodes


def test_part_node_ids_are_the_ast_nodes_of_the_pieces(tmp_path):
    from ast_producer import SourceFile, parse_sources

    php = '<?php echo "<td>$name</td>", $x . \'!\';'
    entry = echo(tmp_path, php)
    (ast,), _ = parse_sources([SourceFile(tmp_path / "page.php", "t001", "page.php")])
    assert [ast.nodes[p["nodeId"]].kind for p in entry["parts"]] == [
        "InterpolatedStringPart", "Expr_Variable", "InterpolatedStringPart", "Expr_Variable", "Scalar_String"]
    for part in entry["parts"]:
        loc = ast.nodes[part["nodeId"]].loc
        source = php.encode()[loc.start_byte:loc.end_byte].decode()
        assert source == (part["expr"] if part["kind"] == "expr" else source)
    assert entry["parts"][0]["value"] == ast.nodes[entry["parts"][0]["nodeId"]].attrs["value"]


def test_a_concatenation_and_several_arguments_keep_their_reads_and_gain_parts(tmp_path):
    entry = echo(tmp_path, "<?php echo '<td>' . $_GET['q'] . '</td>', 'x';")
    assert shape(entry) == [("literal", "<td>", None), ("expr", "$_GET['q']", 1), ("literal", "</td>", None),
                            ("literal", "x", None)]
    assert [r["sourceKind"] for r in entry["reads"]] == ["literal", "request", "literal", "literal"]  # as before


def test_a_heredoc_and_a_print_are_split_too(tmp_path):
    assert shape(echo(tmp_path, "<?php echo <<<HTML\n<b>$x</b>\nHTML;\n")) == [
        ("literal", "<b>", None), ("expr", "$x", 0), ("literal", "</b>", None)]
    assert shape(echo(tmp_path, '<?php print "<i>$y</i>";')) == [
        ("literal", "<i>", None), ("expr", "$y", 0), ("literal", "</i>", None)]


def test_braced_and_unquoted_array_reads_are_expression_parts(tmp_path):
    entry = echo(tmp_path, '<?php echo "{$row[\'a\']} and $row[b]";')
    assert shape(entry) == [("expr", "$row['a']", 0), ("literal", " and ", None), ("expr", "$row[b]", 1)]
    assert [(r["var"], r["path"]) for r in entry["reads"]] == [("$row", ["a"]), ("$row", ["b"])]


def test_a_literal_part_is_the_php_decoded_text(tmp_path):
    entry = echo(tmp_path, '<?php echo "a\\tb\\x41 \\$5 \\"q\\"";')
    assert shape(entry) == [("literal", 'a\tbA $5 "q"', None)]
    assert shape(echo(tmp_path, "<?php echo 'it\\'s', 7;")) == [("literal", "it's", None), ("literal", "7", None)]


@pytest.mark.parametrize("php", ["<?php echo $x;", "<?php echo h($x);", "<?php echo 'x' . 1.5;",
                                 "<?php echo $a ? 'x' : 'y';"])
def test_an_echo_that_is_one_expression_or_cannot_be_split_exactly_has_no_parts(tmp_path, php):
    assert "parts" not in echo(tmp_path, php)


def test_an_echo_without_parts_is_noted_as_before(tmp_path):
    timeline = page(tmp_path, "<?php echo '<td>' . $x . '</td>' . 1.5;")  # the float cannot be split exactly
    assert [n["kind"] for n in timeline["harnessNotes"]] == ["markup_in_concatenated_echo"]


def test_the_loader_reads_parts_into_the_model(tmp_path):
    result = stages(tmp_path, '<?php echo "<td>$name</td>";')
    (node,) = load_timeline(result["path"]).sequence
    assert [(p.kind, p.value, p.expr, p.read) for p in node.parts] == [
        ("literal", "<td>", None, None), ("expr", None, "$name", 0), ("literal", "</td>", None, None)]
    assert all(p.node_id.startswith("t001#") for p in node.parts)


@pytest.mark.parametrize("change, message", [
    (lambda e: e["parts"][1].update(read=5), "names its own read by index"),
    (lambda e: e["parts"][1].pop("read"), "is not a literal"),
    (lambda e: e["parts"][0].update(kind="text"), "is not a literal"),
    (lambda e: e["parts"][0].update(value=3), "a literal part's value is its text"),
    (lambda e: e["parts"][0].update(nodeId="nope"), "node id"),
    (lambda e: e.update(parts="<td>"), "parts must be a list"),
])
def test_the_loader_rejects_malformed_parts(tmp_path, change, message):
    timeline = page(tmp_path, '<?php echo "<td>$name</td>";')
    change(timeline["sequence"][0])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)


def test_inline_html_cannot_carry_parts(tmp_path):
    timeline = page(tmp_path, "<p>x</p>")
    timeline["sequence"][0]["parts"] = []
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    with pytest.raises(ValueError, match="Stmt_InlineHTML keys"):
        load_timeline(path)


# ---------------------------------------------------------------- (b) copy propagation

COPY = """<?php
$result = mysqli_query($con, "select id, name from users");
while ($row = mysqli_fetch_array($result)) {
  $name = $row['name'];
  %s
}
"""


def test_a_copied_row_field_is_read_through_the_copy(tmp_path):
    (read,) = reads(page(tmp_path, COPY % "echo $name;"))
    assert (read["expr"], read["sourceKind"], read["confidence"], read["var"], read["path"]) == (
        "$name", "computed", "resolved", None, None)
    (origin,) = read["derivedFrom"]
    assert (origin["expr"], origin["sourceKind"], origin["source"]["table"], origin["source"]["column"]) == (
        "$row['name']", "db_row_field", "users", "name")


def test_a_copy_is_followed_inside_an_echo_string(tmp_path):
    entry = echo(tmp_path, COPY % 'echo "<td>$name</td>";')
    assert shape(entry)[1] == ("expr", "$name", 0)
    assert entry["reads"][0]["derivedFrom"][0]["source"]["column"] == "name"


@pytest.mark.parametrize("body", [
    "if ($x) { $y = 1; } echo $name;",  # a branch in between
    "while ($z) { $z--; } echo $name;",  # a loop in between
    "$name = strtoupper($name); echo $name;",  # reassigned: no longer the copy
    "$row = other(); echo $name;",  # the row variable is assigned in between
    "trim($name); echo $name;",  # a call is given the variable (it could take it by reference)
    "sort($row); echo $name;",  # a call is given the row
    "include 'x.php'; echo $name;",  # an include may change anything
    "if ($x) { echo $name; }",  # the use is in another block
])
def test_a_copy_is_not_followed_when_something_is_in_between(tmp_path, body):
    assert [k for k in kinds(page(tmp_path, COPY % body)) if k[0] == "$name"] == [
        ("$name", "unresolved", "unresolved")]


@pytest.mark.parametrize("php", [
    "<?php $u = $_SESSION['user']; echo $u;",  # Member 01 Q3: not part of Q17
    "<?php $n = $row['name']; echo $n;",  # the row itself is not traced to a query
    "<?php $n = $other; echo $n;",  # not a row field
])
def test_only_a_traced_row_field_is_propagated(tmp_path, php):
    (read,) = reads(page(tmp_path, php))
    assert (read["sourceKind"], read["confidence"]) == ("unresolved", "unresolved")


# ---------------------------------------------------------------- (c) interpolated SQL values


@pytest.mark.parametrize("sql", [
    '"select id, name from users where id = \'$id\'"',  # inside a quoted value
    '"select id, name from users where id = $id"',  # a whole value after a comparison
    '"select id, name from users where id>=$id and name = \'$n\' order by id;"',
    '"select id, name from users where name like \'%$q%\'"',  # still inside the quotes
    '"select id, name from users where id=" . $_GET[\'id\']',  # the same, written as a concatenation
    '"select id, name from users where id=\'" . $id . "\'"',
    '"select id, name from users where id = {$row2[\'id\']} "',
])
def test_sql_with_interpolated_values_only_is_resolved_and_marked(tmp_path, sql):
    timeline = page(tmp_path, FETCH % sql)
    (read,) = reads(timeline)
    assert (read["sourceKind"], read["confidence"], read["source"]["table"], read["source"]["column"]) == (
        "db_row_field", "resolved", "users", "name")
    (query,) = timeline["queries"].values()
    assert query["valuesInterpolated"] is True and query["columns"] == ["id", "name"]
    (note,) = [n for n in timeline["harnessNotes"] if n["kind"] == "sql_interpolated_values"]
    assert note["line"] == 2
    assert "injection" in timeline["_provenance"]["sqlValuesExtension"]


@pytest.mark.parametrize("sql", [
    '"select id, name from $table where id = 1"',  # a table name
    '"select $cols from users"',  # the column list
    '"select id, name from users order by $col"',
    '"select id, name from users limit $n"',
    '"select id, name from users where id in ($ids)"',  # IN (...) is not included
    '"select id, name from users where $clause"',  # a whole clause
    '"select id, name from users where name like $q"',  # not a comparison operator
    '"select id, name from users where id = $a$b"',  # the value runs on into another interpolation
    '"select id, name from users where id = $a-1"',  # ... or into more SQL
    '"select id, name from users where `$col` = 1"',  # an identifier
    '"select id, name from users where id = " . $id . " or 1"  . $rest',
])
def test_an_interpolation_that_is_not_a_value_stays_unresolved(tmp_path, sql):
    timeline = page(tmp_path, FETCH % sql)
    assert kinds(timeline) == [("$row['name']", "unresolved", "unresolved")]
    assert timeline["queries"] == {} and [n["kind"] for n in timeline["harnessNotes"]] == ["row_read_not_traced"]


def test_interpolated_values_do_not_change_the_select_star_rule(tmp_path):
    sql = '"select * from users where id = \'$id\'"'
    (without,) = reads(page(tmp_path, FETCH % sql))
    assert (without["sourceKind"], without["confidence"]) == ("db_row_field", "ambiguous")  # no schema: Q1
    (with_schema,) = reads(page(tmp_path, FETCH % sql, USERS))
    assert (with_schema["confidence"], with_schema["basis"]) == ("resolved", "schema-expanded")


def test_literal_sql_is_not_marked(tmp_path):
    timeline = page(tmp_path, FETCH % '"select name from users"')
    (query,) = timeline["queries"].values()
    assert "valuesInterpolated" not in query


def test_stage_5_flags_a_query_with_interpolated_values_as_info(tmp_path):
    php = "<ul>" + (FETCH % '"select name from users where id = \'$id\'"').replace(
        "echo $row['name'];", "?><li><?php echo $row['name']; ?></li><?php") + "?></ul>"
    result = stages(tmp_path, php)
    (flag,) = [f for f in result["stage5"]["flags"] if f["flag"] == "sql_interpolated_values"]
    assert (flag["severity"], flag["sourceLine"]) == ("info", 2) and "select name from users" in flag["detail"]
    timeline = load_timeline(result["path"])
    assert [q.values_interpolated for q in timeline.queries.values()] == [True]


def test_the_loader_checks_the_interpolated_mark(tmp_path):
    timeline = page(tmp_path, FETCH % '"select name from users where id = $id"')
    next(iter(timeline["queries"].values()))["valuesInterpolated"] = "yes"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    with pytest.raises(ValueError, match="valuesInterpolated"):
        load_timeline(path)


# ---------------------------------------------------------------- parts through Stage 2 and Stage 5

ROWS = """<table><tbody><?php
$result = mysqli_query($con, "select * from users where active = '$flag'");
while ($row = mysqli_fetch_array($result)) {
  $name = $row['name'];
  $id = $row['id'];
  echo "<tr>
          <td>$id</td>
          <td class='n'>$name</td>
          <td><a href='user.php?id=$id'>open</a></td>
        </tr>";
}
?></tbody></table>"""


def test_a_row_printed_by_one_echo_string_becomes_a_list_and_an_item(tmp_path):
    """The HMS admin-panel1.php case (boundary-rules section 1): before parts this loop abstained (no_rule)."""
    result = stages(tmp_path, ROWS, USERS)
    nodes = result["nodes"]
    assert not [n for n in nodes if n["status"] == "abstain"]
    (lst,) = [n for n in nodes if n["type"] == "List"]
    (item,) = [n for n in nodes if n["type"] == "Item"]
    assert lst["rules"] == ["R-L1", "R-L2a"] and lst["container"]["tag"] == "table" and item["rootTags"] == ["tr"]
    item_file = result["files"]["components/PageList1Item.tsx"]
    assert item_file.count("<td") == 3 and "<tr>" in item_file and 'className="n"' in item_file
    assert item_file.count('reason="no_endpoint"') == 2  # each cell is its own gap: $id, $name
    todos = [t for t in result["stage5"]["c2todos"] if t["file"] == "components/PageList1Item.tsx"]
    assert sorted(t["detail"] for t in todos) == ["part $id", "part $name"]  # which piece of the echo
    assert {(t["sourceLine"], t["sourceCol"]) for t in todos} == {(7, 15), (8, 25)}  # each part's own position


def test_an_expression_inside_an_attribute_value_of_an_echo_string(tmp_path):
    result = stages(tmp_path, ROWS, USERS)
    omitted = [f for f in result["stage5"]["flags"] if f["flag"] == "attribute_omitted"]
    assert [(f["reason"], f["detail"]) for f in omitted] == [("no_endpoint", "href on <a>")]
    assert "<a>open</a>" in result["files"]["components/PageList1Item.tsx"]  # never an empty or guessed href
    escaping = [f for f in result["stage5"]["flags"] if f["flag"] == "escaping_changed"]
    assert len(escaping) == 3  # every unescaped expression part


def test_echo_strings_count_their_elements_like_inline_html(tmp_path):
    php = '<div><?php if ($ok) { echo "<p>a</p><p>$b</p><p>c</p><hr>"; } ?></div>'
    (cond,) = [n for n in stages(tmp_path, php)["nodes"] if n["type"] == "ConditionalComponent"]
    assert (cond["rule"], cond["elements"]) == ("R-I5", 4)


@pytest.mark.parametrize("string", ['"<$tag>x</$tag>"', '"<td $attr>x</td>"', "'<td ' . $attr . '>x</td>'"])
def test_an_expression_in_a_tag_or_attribute_name_abstains(tmp_path, string):
    result = stages(tmp_path, f"<div><?php echo {string}; ?></div>")
    (abstain,) = [n for n in result["nodes"] if n["status"] == "abstain"]
    assert (abstain["type"], abstain["reason"]) == ("Abstain", "markup_from_expression")
    assert abstain["nodeIds"] == abstain["sourceIds"] == [result["timeline"]["sequence"][1]["id"]]
    (todo,) = [t for t in result["stage5"]["c2todos"] if t["reason"] == "markup_from_expression"]
    assert todo["severity"] == "todo" and 'reason="markup_from_expression"' in result["files"]["app/page/page.tsx"]


@pytest.mark.parametrize("string", ['"<td class=\'$c\'>x</td>"', '"<td class=$c>x</td>"', '"<td>$c</td>"',
                                    '"<td class=\\"a $c\\">x</td>"'])
def test_an_expression_in_a_value_or_in_text_does_not_abstain(tmp_path, string):
    result = stages(tmp_path, f"<table><tr><?php echo {string}; ?></tr></table>")
    assert not [n for n in result["nodes"] if n["status"] == "abstain"]


# ---------------------------------------------------------------- R-L3b: a text-only loop


def test_a_text_only_loop_is_an_inline_map_of_fragments(tmp_path):
    """boundary-rules R-L3b, generation-spec 5a (HMS include/header.php line 38)."""
    php = '<span class="username"><?php $q = mysqli_query($con, "select fullName from users");\n' \
          "while ($row = mysqli_fetch_array($q)) { echo $row['fullName']; } ?></span>"
    result = stages(tmp_path, php)
    (lst,) = [n for n in result["nodes"] if n["type"] == "List"]
    assert lst["rules"] == ["R-L1", "R-L2a", "R-L3b"] and lst["container"]["tag"] == "span"
    # the body is one leaf (its status is review here only because whole apps have no labels)
    assert [(c["type"], c["rule"], c["status"]) for c in lst["children"]] == [("Static", "R-L3b", "review")]
    assert not [n for n in result["nodes"] if n["type"] in ("Item", "Abstain")]
    component = result["files"]["components/PageList1.tsx"]
    assert 'import { Fragment } from "react";' in component
    assert "{items.map((row, i) => (" in component and "<Fragment key={i}>" in component


def test_a_text_only_loop_keeps_its_literal_text(tmp_path):
    php = '<p><?php foreach ($names as $n) { echo "Hello $n, "; } ?></p>'
    component = stages(tmp_path, php)["files"]["components/PageList1.tsx"]
    assert "Hello" in component and 'reason="unresolved_read"' in component and "<Fragment key={i}>" in component


def test_a_text_only_loop_with_no_container_still_abstains(tmp_path):
    result = stages(tmp_path, "<?php foreach ($names as $n) { echo $n; }")
    assert [n["reason"] for n in result["nodes"] if n["status"] == "abstain"] == ["container_undetermined"]


# ---------------------------------------------------------------- the module rule (spec v0.5 section 8)


def test_a_page_with_no_endpoint_gets_no_api_module_and_every_file_is_a_module(tmp_path):
    files = stages(tmp_path, ROWS, USERS)["files"]
    assert "lib/api.ts" not in files and {"lib/api-types.ts", "lib/c2-todo.tsx"} <= set(files)
    assert all("export " in text for text in files.values())  # a file with no export is a global script


def test_a_page_with_an_endpoint_exports_auth_headers():
    from src.main import load_stage_inputs
    from src.mapping import loader
    from src.mapping import stage1_isolation as s1
    from src.mapping import stage2_boundaries as s2
    from src.mapping import stage3_requirements as s3
    from src.mapping import stage4_reconcile as s4
    from src.mapping import stage5_generate as s5

    def files(name: str) -> dict:
        timeline, labels = load_stage_inputs(MOCKS / f"timeline_{name}.json", MOCKS / f"labels_{name}.json")
        config = load_boundary_config()
        contract = loader.load_contract(MOCKS / "sample_contract.json")
        tree = s2.infer_boundaries(s1.isolate_presentation(timeline, labels), timeline, config)
        requirements = s3.recover_requirements(tree, timeline)
        report = s4.reconcile(requirements, contract, loader.load_endpoint_map(MOCKS / "endpoint_map.json"))
        result = s5.generate(tree, requirements, report, contract, timeline, config, load_generation_config())
        return {f.path: f.text for f in result.files}

    api = files("list_while")["lib/api.ts"]
    assert "export function authHeaders(): Record<string, string> {" in api
    assert "export async function getApiAppointments()" in api and "headers: authHeaders()," in api
    assert "lib/api.ts" not in files("wp_guestbook")  # no endpoint is mapped for it


def test_the_compile_check_overrides_no_compiler_option(tmp_path):
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    pages = {}
    for name in ("a", "b"):  # two no-endpoint pages in one program: nothing collides without an override
        folder = tmp_path / name
        folder.mkdir()
        files = stages(folder, ROWS, USERS)["files"]
        for path, text in files.items():
            (folder / "generated" / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / "generated" / path).write_text(text, encoding="utf-8")
        pages[name] = folder / "generated"
    errors, output = compile_check.check_many("phase2a_modules", pages)
    assert errors == {"a": 0, "b": 0}, output
    written = json.loads((compile_check.CHECK_DIR / "phase2a_modules" / "tsconfig.json").read_text(encoding="utf-8"))
    assert written == {"extends": "../../tsconfig.json", "compilerOptions": {"incremental": False, "noEmit": True},
                       "include": ["**/*.ts", "**/*.tsx"]}  # the test app's own tsconfig; nothing is loosened


# ---------------------------------------------------------------- review split in the tables


def test_review_is_split_into_unlabelled_and_other():
    assert results_table.only_unlabelled("unlabelled") and not results_table.only_unlabelled(
        "unlabelled,exclusivity_not_verified")
    stage2 = {"tree": {"status": "review", "reason": "unlabelled", "children": [
        {"status": "review", "reason": "unlabelled,exclusivity_not_verified", "children": []},
        {"status": "ok", "reason": "static", "children": []}]}}
    stage5 = {"c2todos": [], "flags": [
        {"flag": "stage2_review", "reason": "unlabelled", "severity": "review"},
        {"flag": "stage2_review", "reason": "unlabelled", "severity": "info"},  # in abstained code: not a review
        {"flag": "document_shell", "reason": "document_shell", "severity": "review"}]}
    stage1 = {"kept": [{"status": "review", "reason": "fetch_in_loop_header"}, {"status": "ok", "reason": "x"}]}
    assert results_table.review_split(stage1, stage2, stage5) == {"s1": (0, 1), "s2": (1, 1), "s5": (1, 1)}


def test_whole_app_rows_carry_the_split(tmp_path):
    result = stages(tmp_path, ROWS, USERS)
    folder = tmp_path / "apps" / "tiny" / "page.php"
    folder.mkdir(parents=True)
    for name in ("stage2.json", "stage5.json"):
        (folder / name).write_bytes((tmp_path / "out" / name).read_bytes())
    row = apps_results_table.page_row(tmp_path / "apps" / "tiny", {
        "key": "page.php", "path": "page.php", "function": None, "nodes": 3, "crash": None, "tsc": 0})
    # no labels file: every review here has the one reason "unlabelled", in Stage 2 and in Stage 5
    assert (row["review_unlabelled"], row["review_other"]) == (row["review"], 0) and row["review"] > 0
    assert (row["s5review_unlabelled"], row["s5review_other"]) == (row["s5review"], 0)
    assert result["stage5"]["counts"]["severity"]["review"] == row["s5review"] > 0
