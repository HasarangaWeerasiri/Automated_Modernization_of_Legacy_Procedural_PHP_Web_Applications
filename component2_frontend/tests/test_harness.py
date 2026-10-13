"""The whole-app test harness (docs/c2-roadmap.md Phases 1 and 1b): tools/harness.

The harness has two halves: an AST producer that writes Member 01's AST format (parse.php,
ast_producer.py) and a timeline builder that reads an AST through the loader (harness.py). Unit
tests parse small PHP snippets and check the AST and the timeline: one test per row of the read
table, one per enclosure kind, call entries and include recording. Then the regression check
against the mocks, and a smoke test that runs every page of HMS and WackoPicko through Stages 1-5.
tests/test_real_ast.py compares the producer with Member 01's real AST.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "harness"))
import app as harness_app  # noqa: E402
import apps_results_table  # noqa: E402
import compile_check  # noqa: E402
import pattern_log  # noqa: E402
import regress  # noqa: E402
import run_apps  # noqa: E402
from ast_producer import SourceFile, parse_sources, write_asts  # noqa: E402
from harness import FileHarness, parse_schema, parse_select  # noqa: E402

from src.mapping.loader import load_ast, load_timeline  # noqa: E402

pytestmark = pytest.mark.skipif(harness_app.unavailable() is not None,
                                reason=f"harness skipped: {harness_app.unavailable()}")
clones = all((config["root"] / ".git").exists() for config in harness_app.APPS.values())
needs_clones = pytest.mark.skipif(not clones, reason="HMS or WackoPicko clone not present")


def app_of(tmp_path, files: dict, schema: dict | None = None) -> dict:
    """{(file name, function name or None for the page): timeline} for PHP files that form one small app."""
    sources = []
    for index, (name, php) in enumerate(sorted(files.items()), start=1):
        (tmp_path / name).write_bytes(php.encode("utf-8"))
        sources.append(SourceFile(tmp_path / name, f"t{index:03d}", f"legacy-apps/tiny/{name}"))
    asts, _ = parse_sources(sources)
    harnesses = {Path(s.source).name: FileHarness(s.source.read_bytes(), ast, s.path, schema)
                 for s, ast in zip(sources, asts)}
    pages, _ = harness_app.build_timelines(harnesses, lambda path: {"generator": "c2-harness"})
    return {(page.path, page.function): page.timeline for page in pages}


def timelines(tmp_path, php: str, schema: dict | None = None) -> dict:
    """{function name or None for the page: timeline} for one PHP source text."""
    return {function: timeline for (_, function), timeline in app_of(tmp_path, {"page.php": php}, schema).items()}


def page(tmp_path, php: str, schema: dict | None = None) -> dict:
    return timelines(tmp_path, php, schema)[None]


def outputs(timeline: dict) -> list[dict]:
    return [entry for entry in timeline["sequence"] if entry["kind"] != "call"]


def calls(timeline: dict) -> list[dict]:
    return [entry for entry in timeline["sequence"] if entry["kind"] == "call"]


def reads(timeline: dict) -> list[dict]:
    return [r for entry in timeline["sequence"] for r in entry.get("reads", [])]


def kinds(timeline: dict) -> list[tuple]:
    return [(r["expr"], r["sourceKind"], r["confidence"]) for r in reads(timeline)]


FETCH = """<?php
$result = mysqli_query($con, %s);
while ($row = mysqli_fetch_array($result)) { echo $row['name']; }
"""
USERS = {"users": ["id", "name"]}


# ---------------------------------------------------------------- the AST producer (Member 01's format)

SMALL = "<p>café</p>\n<?php if ($a) { echo 'y'; } ?>\n"


def small_ast(tmp_path) -> tuple[dict, bytes, Path]:
    source = tmp_path / "small.php"
    source.write_bytes(SMALL.encode("utf-8"))
    (path,), _ = write_asts([SourceFile(source, "t001", "apps/small.php")], tmp_path / "ast")
    return json.loads(path.read_text(encoding="utf-8")), source.read_bytes(), path


def test_ast_file_has_member_01s_header(tmp_path):
    doc, source, _ = small_ast(tmp_path)
    assert list(doc) == ["bytes", "fileId", "nodeCount", "nodes", "parseErrors", "parseOk", "path", "rootId",
                         "schemaVersion", "sha256"]  # keys in sorted order, as in Member 01's files
    assert {k: v for k, v in doc.items() if k != "nodes"} == {
        "schemaVersion": "1.0", "fileId": "t001", "path": "apps/small.php",
        "sha256": hashlib.sha256(source).hexdigest(), "bytes": len(source), "parseOk": True, "parseErrors": [],
        "rootId": "t001#00000", "nodeCount": 6}


def test_ast_nodes_are_numbered_in_pre_order_from_the_root_file(tmp_path):
    doc, _, _ = small_ast(tmp_path)
    assert [(n["id"], n["kind"], n["parent"]) for n in doc["nodes"]] == [
        ("t001#00000", "File", None), ("t001#00001", "Stmt_InlineHTML", "t001#00000"),
        ("t001#00002", "Stmt_If", "t001#00000"), ("t001#00003", "Expr_Variable", "t001#00002"),
        ("t001#00004", "Stmt_Echo", "t001#00002"), ("t001#00005", "Scalar_String", "t001#00004")]
    assert all(list(n) == ["attrs", "children", "fields", "id", "kind", "loc", "parent"] for n in doc["nodes"])
    root, _, branch, _, echo, _ = doc["nodes"]
    assert root["children"] == root["fields"]["stmts"] == ["t001#00001", "t001#00002"]
    assert branch["children"] == ["t001#00003", "t001#00004"]  # children: every child, in order


def test_ast_roles_are_fields_and_literal_values_are_attrs(tmp_path):
    doc, _, _ = small_ast(tmp_path)
    _, html, branch, variable, echo, string = doc["nodes"]
    # no else: the role is still there, with null (as in Member 01's real AST); an empty list stays a list
    assert branch["fields"] == {"cond": "t001#00003", "else": None, "elseifs": [], "stmts": ["t001#00004"]}
    assert branch["attrs"] == {}
    assert echo["fields"] == {"exprs": ["t001#00005"]}
    assert (variable["fields"], variable["attrs"]) == ({}, {"name": "a"})
    assert string["attrs"] == {"_kind": 1, "_rawValue": "'y'", "value": "y"}  # _kind 1: single-quoted
    assert html["attrs"] == {"value": "<p>café</p>\n"}


def test_ast_loc_is_computed_from_bytes_with_exclusive_ends(tmp_path):
    doc, source, _ = small_ast(tmp_path)
    root, html, branch, variable, echo, string = doc["nodes"]
    assert root["loc"] == {"startLine": 1, "endLine": 3, "startCol": 0, "endCol": 0, "startByte": 0,
                           "endByte": len(source)}
    # the html ends with its newline: the exclusive end is the start of line 2. "é" is two bytes.
    assert html["loc"] == {"startLine": 1, "endLine": 2, "startCol": 0, "endCol": 0, "startByte": 0, "endByte": 13}
    assert variable["loc"] == {"startLine": 2, "endLine": 2, "startCol": 10, "endCol": 12, "startByte": 23,
                               "endByte": 25}
    for node in doc["nodes"]:
        loc = node["loc"]
        text = source[loc["startByte"]:loc["endByte"]]
        assert loc["startCol"] == loc["startByte"] - (source.rfind(b"\n", 0, loc["startByte"]) + 1)
        assert loc["startLine"] == source.count(b"\n", 0, loc["startByte"]) + 1
        assert loc["endLine"] == source.count(b"\n", 0, loc["endByte"]) + 1
        if "_rawValue" in node["attrs"]:
            assert text.decode("utf-8") == node["attrs"]["_rawValue"]
    assert source[branch["loc"]["startByte"]:branch["loc"]["endByte"]] == b"if ($a) { echo 'y'; }"
    assert source[echo["loc"]["startByte"]:echo["loc"]["endByte"]] == b"echo 'y';"


def test_ast_producer_is_deterministic_and_a_parse_error_is_recorded(tmp_path):
    good, bad = tmp_path / "good.php", tmp_path / "bad.php"
    good.write_bytes(SMALL.encode("utf-8"))
    bad.write_bytes(b"<?php if ($a { echo 1; }")
    files = [SourceFile(good, "t001", "good.php"), SourceFile(bad, "t002", "bad.php")]
    first, meta = write_asts(files, tmp_path / "a")
    second, _ = write_asts(files, tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    assert meta["parser"].startswith("nikic/php-parser v5.9.0") and set(meta["syntax"]) == {"t001", "t002"}
    broken = load_ast(first[1])
    assert broken.parse_ok is False and broken.parse_errors and "line 1" in broken.parse_errors[0]
    assert broken.root.kind == "File"  # a file that does not parse still gives a root


def test_the_loader_reads_the_ast_into_internal_types(tmp_path):
    _, source, path = small_ast(tmp_path)
    ast = load_ast(path)
    assert (ast.file_id, ast.path, ast.bytes, ast.parse_ok, ast.node_count, ast.root_id) == (
        "t001", "apps/small.php", len(source), True, 6, "t001#00000")
    assert ast.root.kind == "File" and list(ast.nodes) == [f"t001#{i:05d}" for i in range(6)]
    branch = ast.nodes["t001#00002"]
    assert (branch.parent, branch.children) == ("t001#00000", ("t001#00003", "t001#00004"))
    assert branch.fields == {"cond": "t001#00003", "else": None, "elseifs": (), "stmts": ("t001#00004",)}
    assert source[branch.loc.start_byte:branch.loc.end_byte].startswith(b"if ($a)")
    assert (branch.loc.start_line, branch.loc.start_col, branch.loc.column) == (2, 6, 7)


def _drop_loc_key(doc):
    del doc["nodes"][3]["loc"]["endByte"]


@pytest.mark.parametrize("change, message", [
    (lambda d: d.pop("sha256"), "AST keys"),
    (lambda d: d.update(extra=1), "AST keys"),
    (lambda d: d.update(schemaVersion="2.0"), "schemaVersion"),
    (lambda d: d.update(sha256="abc"), "sha256"),
    (lambda d: d.update(nodeCount=7), "nodeCount"),
    (lambda d: d["nodes"][2].update(id="t001#00009"), "pre-order numbering"),
    (lambda d: d["nodes"][2].pop("attrs"), "node 2 keys"),
    (lambda d: d["nodes"][0].update(kind="Program"), "root"),
    (lambda d: d["nodes"][3].update(parent="t001#00000"), "parent / children"),
    (lambda d: d["nodes"][2].update(children=["t001#00004", "t001#00003"]), "parent / children"),
    (lambda d: d["nodes"][2]["fields"].update(cond="t001#00005"), "not one of its children"),
    (lambda d: d["nodes"][2]["fields"].update(cond="t001#00099"), "not a node of this AST"),
    (_drop_loc_key, "loc needs exactly"),
    (lambda d: d["nodes"][3]["loc"].update(endByte=9999, endCol=9999), "ends after the file"),
])
def test_the_loader_rejects_an_ast_that_drifts_from_schema_1_0(tmp_path, change, message):
    doc, _, _ = small_ast(tmp_path)
    change(doc)
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_ast(path)


# ---------------------------------------------------------------- reads, one test per row of the table


def test_row_of_a_single_table_select_is_a_resolved_db_field(tmp_path):
    (read,) = reads(page(tmp_path, FETCH % '"select id, name from users where id = 3"'))
    assert (read["sourceKind"], read["confidence"]) == ("db_row_field", "resolved")
    assert (read["source"]["table"], read["source"]["column"]) == ("users", "name")
    assert "basis" not in read  # the column list is in the query itself
    assert read["source"]["fetchNodeId"].startswith("t001#") and read["source"]["queryNodeId"].startswith("t001#")


def test_select_star_is_ambiguous_without_a_schema(tmp_path):
    """Member 01 Q1 (proposed): `SELECT *` is ambiguous by default, even on one table."""
    timeline = page(tmp_path, FETCH % '"select * from users;"')
    (read,) = reads(timeline)
    assert (read["sourceKind"], read["confidence"]) == ("db_row_field", "ambiguous")
    assert (read["source"]["table"], read["source"]["column"]) == (None, None) and "basis" not in read
    (query,) = timeline["queries"].values()
    assert (query["tables"], query["columns"], query["resultVar"]) == (["users"], None, "$result")


def test_select_star_is_resolved_from_a_schema_the_app_ships(tmp_path):
    timeline = page(tmp_path, FETCH % '"select * from users;"', USERS)
    (read,) = reads(timeline)
    assert (read["confidence"], read["source"]["table"], read["source"]["column"]) == ("resolved", "users", "name")
    assert read["basis"] == "schema-expanded"
    (query,) = timeline["queries"].values()
    assert query["columns"] == ["id", "name"]  # the columns come from the schema


@pytest.mark.parametrize("sql, schema, expected", [
    ('"select * from users"', {"users": ["id", "email"]}, ("unresolved", "unresolved")),  # not a column of it
    ('"select * from users"', {"other": ["name"]}, ("db_row_field", "ambiguous")),  # the schema lacks the table
    ('"select * from users u inner join b on u.id = b.id"', USERS, ("db_row_field", "ambiguous")),  # a JOIN
])
def test_select_star_is_expanded_only_for_one_declared_table(tmp_path, sql, schema, expected):
    (read,) = reads(page(tmp_path, FETCH % sql, schema))
    assert (read["sourceKind"], read["confidence"]) == expected and "basis" not in read


def test_sql_in_a_variable_assigned_a_literal_is_literal_sql(tmp_path):
    php = """<?php
$query = "select name from users";
$result = mysqli_query($con, $query);
$row = mysqli_fetch_assoc($result);
echo $row['name'];
"""
    assert kinds(page(tmp_path, php)) == [("$row['name']", "db_row_field", "resolved")]


def test_mysql_functions_are_traced_too(tmp_path):
    php = """<?php
$res = mysql_query("select name from users");
while ($r = mysql_fetch_assoc($res)) { echo $r['name']; }
"""
    (read,) = reads(page(tmp_path, php))
    assert (read["sourceKind"], read["source"]["table"]) == ("db_row_field", "users")


def test_a_join_is_ambiguous(tmp_path):
    (read,) = reads(page(tmp_path, FETCH % '"select * from a inner join b on a.id = b.id"'))
    assert (read["sourceKind"], read["confidence"]) == ("db_row_field", "ambiguous")
    assert (read["source"]["table"], read["source"]["column"]) == (None, None)


@pytest.mark.parametrize("sql", ['"select * from $table where id = 3"', '"select * from users " . $where', "$sql",
                                 "build_sql()"])
def test_built_sql_is_unresolved(tmp_path, sql):
    """SQL whose structure is not fixed text. (Interpolated *values* are resolved: tests/test_phase2a.py.)"""
    timeline = page(tmp_path, FETCH % sql, USERS)
    assert kinds(timeline) == [("$row['name']", "unresolved", "unresolved")]
    (note,) = timeline["harnessNotes"]
    assert (note["kind"], note["line"], note["col"]) == ("row_read_not_traced", 3, 51)  # col: 1-based


def test_a_column_not_in_the_select_list_is_unresolved(tmp_path):
    assert kinds(page(tmp_path, FETCH % '"select id from users"')) == [("$row['name']", "unresolved", "unresolved")]


def test_superglobals(tmp_path):
    php = "<?php echo $_SESSION['u']; echo $_GET['a']; echo $_POST['b']; echo $_REQUEST['c']; echo $_COOKIE['d']; " \
          "echo $_SERVER['PHP_SELF'];"
    assert [(r["sourceKind"], r["confidence"], r["source"]) for r in reads(page(tmp_path, php))] == [
        ("session", "resolved", None), ("request", "resolved", None), ("request", "resolved", None),
        ("request", "resolved", None), ("request", "resolved", None), ("server", "resolved", None)]


def test_literals(tmp_path):
    assert kinds(page(tmp_path, "<?php echo 'Active'; echo \"Done\"; echo 5; print 'x';")) == [
        ("'Active'", "literal", "resolved"), ('"Done"', "literal", "resolved"), ("5", "literal", "resolved"),
        ("'x'", "literal", "resolved")]


@pytest.mark.parametrize("wrapper", ["h", "htmlspecialchars", "htmlentities"])
def test_escape_wrappers_are_computed_with_derived_from(tmp_path, wrapper):
    (read,) = reads(page(tmp_path, f"<?php echo {wrapper}($_SESSION['u']);"))
    assert (read["sourceKind"], read["confidence"], read["var"], read["path"]) == ("computed", "resolved", None, None)
    assert [(d["expr"], d["sourceKind"]) for d in read["derivedFrom"]] == [("$_SESSION['u']", "session")]


def test_escape_wrapper_around_a_concatenation_keeps_only_the_reads(tmp_path):
    (read,) = reads(page(tmp_path, "<?php echo h(Cart::$URL . '?id=' . $pic['id']);"))
    assert [d["expr"] for d in read["derivedFrom"]] == ["Cart::$URL", "$pic['id']"]
    assert read["confidence"] == "unresolved"


@pytest.mark.parametrize("expr", ["$name", "$user['login']", "date('Y')", "Users::current()", "$db->title",
                                  "$a ? 'x' : 'y'", "$row[$key]", "COUNT"])
def test_anything_else_is_unresolved(tmp_path, expr):
    assert kinds(page(tmp_path, f"<?php echo {expr};")) == [(expr, "unresolved", "unresolved")]


def test_an_interpolated_string_gives_one_read_per_expression_and_is_noted(tmp_path):
    """Phase 2a: no longer one opaque read. Its parts are tested in tests/test_phase2a.py."""
    timeline = page(tmp_path, '<?php echo "<td>$name</td>";')
    assert kinds(timeline) == [("$name", "unresolved", "unresolved")]
    (note,) = timeline["harnessNotes"]
    assert (note["kind"], note["form"], note["markup"]) == ("echo_parts", "interpolated_string", True)


def test_a_heredoc_is_noted_as_one(tmp_path):
    timeline = page(tmp_path, "<?php echo <<<HTML\n<b>$x</b>\nHTML;\n")
    assert [(n["kind"], n["form"]) for n in timeline["harnessNotes"]] == [("echo_parts", "heredoc")]


def test_a_concatenated_echo_gives_one_read_per_piece(tmp_path):
    timeline = page(tmp_path, "<?php echo '<td>' . $_GET['q'] . '</td>', 'x';")
    assert [r["sourceKind"] for r in reads(timeline)] == ["literal", "request", "literal", "literal"]
    assert [(n["kind"], n["form"]) for n in timeline["harnessNotes"]] == [("echo_parts", "concatenation")]


# ---------------------------------------------------------------- "comes from" is decided only when certain


@pytest.mark.parametrize("php", [
    # the query is chosen in a branch the fetch is not in
    "<?php if ($a) { $result = mysqli_query($c, 'select name from users'); }\n"
    "$row = mysqli_fetch_assoc($result); echo $row['name'];",
    # an include between the query and the fetch may have changed $result
    "<?php $result = mysqli_query($c, 'select name from users'); include 'x.php';\n"
    "$row = mysqli_fetch_assoc($result); echo $row['name'];",
    # the row variable is also assigned from something else
    "<?php $result = mysqli_query($c, 'select name from users'); $row = mysqli_fetch_assoc($result);\n"
    "echo $row['name']; $row = other();",
    # the row is read after its loop
    "<?php $result = mysqli_query($c, 'select name from users');\n"
    "while ($row = mysqli_fetch_assoc($result)) { } echo $row['name'];",
    # the SQL variable is overwritten later in the loop both are in
    "<?php while ($x) { $result = mysqli_query($c, $q); $row = mysqli_fetch_assoc($result); echo $row['name'];\n"
    "$q = 'select name from users'; }",
    # inside a function, $result comes from outside
    "<?php function f($result) { $row = mysqli_fetch_assoc($result); echo $row['name']; }",
])
def test_uncertain_data_flow_is_unresolved(tmp_path, php):
    found = timelines(tmp_path, php)
    (timeline,) = found.values()
    assert kinds(timeline) == [("$row['name']", "unresolved", "unresolved")]


def test_foreach_over_fetched_rows(tmp_path):
    php = """<?php
$result = mysqli_query($c, "select name from users");
$rows = mysqli_fetch_all($result, MYSQLI_ASSOC);
foreach ($rows as $i => $row) { echo $row['name']; }
"""
    timeline = page(tmp_path, php)
    assert kinds(timeline) == [("$row['name']", "db_row_field", "resolved")]
    (loop,) = timeline["sequence"][0]["enclosedBy"]
    assert (loop["iterSourceKind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"]) == (
        "db_row_field", "$rows", "$row", "$i")


@pytest.mark.parametrize("sql, table, columns", [
    ("select * from t", "t", None),
    ("SELECT a, t.b, `c`, count(*) as n FROM `t` WHERE x = 1 ORDER BY a;", "t", ["a", "b", "c", "n"]),
    ("select distinct a from t u where u.x = 1", "t", ["a"]),
    ("select * from a, b", None, None),
    ("select * from a left join b on a.x = b.x", None, None),
    ("select a from (select a from t) s", None, None),
    ("update t set a = 1", None, None),
])
def test_parse_select(sql, table, columns):
    shape = parse_select(sql)
    assert (shape.table, shape.columns) == (table, columns)


def test_parse_schema_reads_create_table_statements():
    sql = """-- dump
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(50) DEFAULT 'a, b (c)',
  price decimal(10,2) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `name` (`name`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS logs (msg text);
INSERT INTO `users` VALUES (1, 'x', 2.0);
"""
    assert parse_schema(sql) == {"users": ["id", "name", "price"], "logs": ["msg"]}
    twice = sql + "CREATE TABLE logs (msg text, level int);"
    assert "logs" not in parse_schema(twice)  # declared two ways: which one is live is not certain


# ---------------------------------------------------------------- enclosures, one test per kind


def enclosures(timeline: dict) -> list[dict]:
    return timeline["sequence"][0]["enclosedBy"]


def test_while_enclosure(tmp_path):
    php = "<?php while ($row = mysqli_fetch_array($r)) { echo 'x'; }"
    (loop,) = enclosures(page(tmp_path, php))
    assert loop == {"nodeId": "t001#00001", "kind": "Stmt_While", "role": "iteration",
                    "iterExpr": "$row = mysqli_fetch_array($r)", "iterSourceKind": "db_row_field",
                    "valueVar": "$row", "keyVar": None,
                    "loc": {"startLine": 1, "endLine": 1, "startCol": 6, "endCol": len(php), "startByte": 6,
                            "endByte": len(php)}}  # the loop's own loc (Member 01 Q15)


def test_foreach_enclosure(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php foreach ($items as $k => $v) { echo 'x'; }"))
    assert (loop["kind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"], loop["iterSourceKind"]) == (
        "Stmt_Foreach", "$items", "$v", "$k", "unresolved")


def test_for_enclosure_leaves_the_variables_null(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php for ( $i = 0; $i < 3; $i++ ) { echo 'x'; }"))
    assert (loop["kind"], loop["iterExpr"], loop["valueVar"], loop["keyVar"]) == (
        "Stmt_For", "$i = 0; $i < 3; $i++", None, None)


def test_do_enclosure(tmp_path):
    (loop,) = enclosures(page(tmp_path, "<?php do { echo 'x'; } while ($row = next_row());"))
    assert (loop["kind"], loop["role"], loop["iterExpr"], loop["valueVar"]) == (
        "Stmt_Do", "iteration", "$row = next_row()", "$row")


def test_if_elseif_else_enclosures_carry_condexpr(tmp_path):
    php = "<?php if ( ($a == 1) && $b ) { echo 'x'; } elseif ($c) { echo 'y'; } else { echo 'z'; }"
    then, elseif, otherwise = (entry["enclosedBy"][0] for entry in page(tmp_path, php)["sequence"])
    assert (then["branch"], then["condExpr"]) == ("then", "($a == 1) && $b")
    assert (elseif["branch"], elseif["condExpr"]) == ("elseif", "$c")
    assert otherwise["branch"] == "else" and "condExpr" not in otherwise
    assert then["nodeId"] == elseif["nodeId"] == otherwise["nodeId"]
    assert otherwise["condNodeId"] == then["condNodeId"] != elseif["condNodeId"]
    assert then["loc"] == elseif["loc"] == otherwise["loc"]  # the loc of the node nodeId names: the whole if
    assert (then["loc"]["startCol"], then["loc"]["endByte"]) == (6, len(php))


def test_condexpr_is_the_source_of_the_cond_node(tmp_path):
    """Member 01 Q2: parentheses that only wrap the whole condition are not part of the cond node."""
    php = "<?php if (($a)) { echo 'x'; } while ( ( $r = f() ) ) { echo 'y'; }"
    first, second = (entry["enclosedBy"][0] for entry in page(tmp_path, php)["sequence"])
    assert (first["condExpr"], second["iterExpr"]) == ("$a", "$r = f()")


def test_else_if_is_a_nested_if(tmp_path):
    php = "<?php if ($a) { echo 'x'; } else if ($b) { echo 'y'; }"
    second = page(tmp_path, php)["sequence"][1]["enclosedBy"]
    assert [(e["branch"], e.get("condExpr")) for e in second] == [("else", None), ("then", "$b")]


def test_alternative_syntax_gives_the_same_enclosures(tmp_path):
    php = "<?php if ($a): ?><p>x</p><?php endif; foreach ($l as $v): ?><li></li><?php endforeach; ?>"
    first, second = page(tmp_path, php)["sequence"]
    assert (first["kind"], first["raw"], first["enclosedBy"][0]["condExpr"]) == ("Stmt_InlineHTML", "<p>x</p>", "$a")
    assert second["enclosedBy"][0]["kind"] == "Stmt_Foreach"


def test_switch_and_try_enclosures(tmp_path):
    php = "<?php switch ($x) { case 1: echo 'a'; break; default: echo 'b'; }\n" \
          "try { echo 'c'; } catch (Exception $e) { echo 'd'; } finally { echo 'e'; }"
    sequence = page(tmp_path, php)["sequence"]
    cases = [entry["enclosedBy"][0] for entry in sequence[:2]]
    assert [(c["kind"], c["role"], c["caseExpr"]) for c in cases] == [
        ("Stmt_Switch", "switch_case", "1"), ("Stmt_Switch", "switch_case", None)]
    assert cases[0]["nodeId"] == cases[1]["nodeId"] and cases[0]["caseNodeId"] != cases[1]["caseNodeId"]
    assert [(e["enclosedBy"][0]["role"], e["enclosedBy"][0]["part"]) for e in sequence[2:]] == [
        ("try_catch", "try"), ("try_catch", "catch"), ("try_catch", "finally")]


def test_entries_have_byte_exact_raw_ast_ids_and_the_ast_nodes_loc(tmp_path):
    php = "<p>café</p>\r\n<?php echo $x; ?>\r\n<b><?= $y ?></b>"
    source = php.encode("utf-8")
    first, echo, tag, short, close = page(tmp_path, php)["sequence"]
    assert (first["kind"], first["raw"]) == ("Stmt_InlineHTML", "<p>café</p>\r\n")
    assert (echo["kind"], echo["loc"]) == ("Stmt_Echo", {"startLine": 2, "endLine": 2, "startCol": 6, "endCol": 14,
                                                         "startByte": 20, "endByte": 28})  # "echo $x;"
    # an echo closed by "?>" ends after the tag, as nikic's node (and so Member 01's loc) does
    assert source[short["loc"]["startByte"]:short["loc"]["endByte"]] == b"<?= $y ?>"
    assert (short["kind"], short["loc"]["startCol"], short["loc"]["endCol"]) == ("Stmt_Echo", 3, 12)
    ids = [e["id"] for e in (first, echo, tag, short, close)]
    assert ids == ["t001#00001", "t001#00002", "t001#00004", "t001#00005", "t001#00007"]  # pre-order AST ids


# ---------------------------------------------------------------- calls (Member 01 Q4) and includes: not inlined

FUNCTIONS = """<?php
include 'header.php';
require_once("lib/" . $name);
function row($r) { echo '<tr>'; }
function table($rows) { foreach ($rows as $r) { row($r); } }
function helper() { return 1; }
class View { static function title() { ?><h1>T</h1><?php } }
?>
<div><?php table($rows); helper(); strlen('x'); View::title(); ?></div>
"""


def test_each_printing_function_gets_its_own_timeline(tmp_path):
    found = timelines(tmp_path, FUNCTIONS)
    assert sorted(k for k in found if k) == ["View.title", "row"]  # table() and helper() print nothing themselves
    assert found["row"]["entrypoint"] == "legacy-apps/tiny/page.php#row"
    assert found["View.title"]["sequence"][0]["raw"] == "<h1>T</h1>"
    assert [e["kind"] for e in outputs(found[None])] == ["Stmt_InlineHTML", "Stmt_InlineHTML"]  # not inlined


def test_a_call_site_is_a_sequence_entry_in_source_order(tmp_path):
    found = timelines(tmp_path, FUNCTIONS)
    timeline = found[None]
    assert [e["kind"] for e in timeline["sequence"]] == ["Stmt_InlineHTML", "call", "call", "call", "Stmt_InlineHTML"]
    names = timeline["callNames"]
    assert [(names[c["id"]], c["outputsInside"], c["loc"]["startLine"]) for c in calls(timeline)] == [
        ("table", True, 9), ("helper", False, 9), ("view::title", True, 9)]  # table prints through row()
    assert all(set(c) == {"id", "kind", "callee", "outputsInside", "enclosedBy", "loc", "args"}
               for c in calls(timeline))
    title = calls(timeline)[2]
    assert title["callee"] == found["View.title"]["_provenance"]["functionNodeId"]  # the function's AST node
    assert all(c["enclosedBy"] == [] for c in calls(timeline))  # strlen: PHP's own function, no entry
    (inner,) = calls(timelines(tmp_path, FUNCTIONS)["row"]) or [None]
    assert inner is None  # row() calls nothing


def test_a_call_inside_a_loop_carries_the_loop(tmp_path):
    php = "<?php function cell() { echo 'x'; } ?><tr><?php foreach ($l as $v) { cell(); } ?></tr>"
    (call,) = calls(page(tmp_path, php))
    assert [(e["role"], e["kind"]) for e in call["enclosedBy"]] == [("iteration", "Stmt_Foreach")]


def test_a_call_that_cannot_be_followed_is_unknown(tmp_path):
    php = """<?php
function wrapper($o) { $o->draw(); }
function loader() { include 'part.php'; }
function quiet() { return strlen('x'); }
echo 'x';
$obj->render(); $fn(); Other::make(); call_user_func('a'); wrapper($obj); loader(); quiet();
"""
    timeline = page(tmp_path, php)
    assert [(c["callee"] is not None, c["outputsInside"]) for c in calls(timeline)] == [
        (False, "unknown"), (False, "unknown"), (False, "unknown"), (False, "unknown"),  # not one app function
        (True, "unknown"), (True, "unknown"), (True, False)]  # resolved: a method call inside, an include, nothing


def test_a_function_defined_twice_has_no_single_callee(tmp_path):
    files = {"a.php": "<?php function options() { echo '<option>'; }",
             "b.php": "<?php function options() { echo '<option>'; } function mixed() { }",
             "c.php": "<?php function mixed() { echo 'x'; }",
             "page.php": "<select><?php options(); mixed(); ?></select>"}
    first, second = calls(app_of(tmp_path, files)[("page.php", None)])
    assert (first["callee"], first["outputsInside"]) == (None, True)  # both definitions print
    assert (second["callee"], second["outputsInside"]) == (None, "unknown")  # one prints, one does not


def test_the_loader_keeps_call_entries_apart_from_the_output_nodes(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text(harness_app.dump_timeline(page(tmp_path, FUNCTIONS)), encoding="utf-8")
    timeline = load_timeline(path)
    assert [n.kind for n in timeline.sequence] == ["Stmt_InlineHTML", "Stmt_InlineHTML"]
    assert [(c.outputs_inside, c.callee is not None, c.loc.start_line) for c in timeline.calls] == [
        (True, True, 9), (False, True, 9), (True, True, 9)]


@pytest.mark.parametrize("change, message", [
    (lambda c: c.update(outputsInside="maybe"), "outputsInside"),
    (lambda c: c.update(callee="not an id"), "callee"),
    (lambda c: c.pop("callee"), "call keys"),
    (lambda c: c.update(reads=[]), "call keys"),
])
def test_the_loader_rejects_a_malformed_call_entry(tmp_path, change, message):
    doc = page(tmp_path, FUNCTIONS)
    change(calls(doc)[0])
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_timeline(path)


def test_includes_are_recorded(tmp_path):
    includes = timelines(tmp_path, FUNCTIONS)[None]["includes"]
    assert [(i["kind"], i["expr"], i["literal"], i["line"], i["col"]) for i in includes] == [
        ("include", "'header.php'", "header.php", 2, 1), ("require_once", '"lib/" . $name', None, 3, 1)]


def test_output_the_schema_cannot_express_is_noted(tmp_path):
    notes = page(tmp_path, "<?php echo 'x'; printf('%d', 1); die('stop'); ob_start();")["harnessNotes"]
    assert sorted(n["kind"] for n in notes) == ["exit_with_output", "output_buffering", "output_function"]


def test_provenance_marks_the_harness_and_the_conventions(tmp_path):
    provenance = page(tmp_path, "<?php echo 'x';")["_provenance"]
    assert provenance["generator"] == "c2-harness"
    assert "Q4" in provenance["callsConvention"] and "Q2" in provenance["condExprConvention"]
    assert "Q1" in provenance["selectStarConvention"] and "exclusive" in provenance["locConvention"]


def test_a_file_that_prints_nothing_has_no_page_timeline(tmp_path):
    assert timelines(tmp_path, "<?php $x = 1; function f() { return 2; }") == {}


def test_the_loader_and_all_five_stages_accept_harness_output(tmp_path):
    php = """<table><?php
$result = mysqli_query($con, "select name from users");
while ($row = mysqli_fetch_array($result)) { ?><tr><td><?php echo $row['name']; ?></td></tr><?php }
switch ($x) { case 1: echo 'a'; }
?></table>"""
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(harness_app.dump_timeline(page(tmp_path, php)), encoding="utf-8")
    assert len(load_timeline(timeline_path).sequence) == 6
    folder = tmp_path / "out"
    folder.mkdir()
    from src.mapping.boundary_config import load_boundary_config
    from src.mapping.generation_config import load_generation_config
    from src.mapping.loader import load_contract

    run_apps.run_page(timeline_path, folder, load_boundary_config(), load_generation_config(),
                      load_contract(run_apps.EMPTY_CONTRACT))
    stage2 = json.loads((folder / "stage2.json").read_text(encoding="utf-8"))
    nodes = list(apps_results_table._walk(stage2["tree"]))
    assert [n["reason"] for n in nodes if n["status"] == "abstain"] == ["switch_case_enclosure"]
    assert [n["type"] for n in nodes if n["type"] in ("List", "Item")] == ["List", "Item"]
    assert (folder / "generated" / "lib" / "c2-todo.tsx").exists()
    stage5 = json.loads((folder / "stage5.json").read_text(encoding="utf-8"))
    assert {(t["sourceLine"], t["sourceCol"]) for t in stage5["c2todos"] if t["reason"] == "unresolved_read"} <= {
        (3, 67)}  # file:line:col, the column 1-based


# ---------------------------------------------------------------- regression against the mocks (brief section 2)


@needs_clones
def test_harness_reproduces_the_mocks_except_where_the_read_rules_differ(tmp_path):
    differences = regress.run(tmp_path)
    assert {mock: len(d) for mock, d in differences.items()} == {
        "list_while": 0, "detail": 0, "wp_guestbook": 0, "wp_view": 1, "wp_login": 0, "wp_header": 0,
        "wp_thumbnails": 0}
    # detail: its SQL interpolates values only (where fname ='$fname' and lname='$lname'), so since Phase 2a the
    # harness resolves the same table and columns the mock was given by hand (5 reads; they differed before).
    # wp_view: date(...) inside h() is a function result (unresolved); the mock made it a computed read.
    (view,) = differences["wp_view"]
    assert view["id"] == "f007#00211" and view["harness"][0]["derivedFrom"][0]["sourceKind"] == "unresolved"


# ---------------------------------------------------------------- whole apps (brief section 3)


@pytest.fixture(scope="module")
def apps_output(tmp_path_factory):
    if not clones:
        pytest.skip("HMS or WackoPicko clone not present")
    output = tmp_path_factory.mktemp("apps")
    tsc = compile_check.unavailable() is None
    return output, {app: run_apps.run_app(harness_app.app_timelines(app), output, tsc=tsc)
                    for app in harness_app.APPS}


def test_every_page_of_both_apps_runs_without_a_crash(apps_output):
    _, indexes = apps_output
    def roles(index):
        return tuple(sum(p["role"] == role for p in index["pages"]) for role in (
            "page", "component", "shell", "not_called", "shell_part"))

    # units: pages, function components (R-F1), shells (R-F2), functions not in the app; and the two functions
    # that are one shell (they have no unit of their own)
    assert {app: (index["files"], roles(index)) for app, index in indexes.items()} == {
        "hms": (29, (26, 2, 0, 5, 0)), "wackopicko": (49, (29, 2, 1, 0, 2))}
    for index in indexes.values():
        assert [p["key"] for p in index["pages"] if p["crash"]] == []
        assert index["parseErrors"] == {} and index["collisions"] == []
    assert [s["name"] for s in indexes["wackopicko"]["shells"]] == ["OurHeaderShell"] and not indexes["hms"]["shells"]


def test_every_page_of_both_apps_compiles(apps_output):
    _, indexes = apps_output
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    for index in indexes.values():
        assert index["tscRan"] and index["tscUnattributed"] == 0, index["tscOutput"]
        assert [(p["key"], p["tsc"]) for p in index["pages"] if p["role"] != "shell_part" and p["tsc"] != 0] == []


def test_libraries_are_excluded_and_only_discovery_apps_are_configured(apps_output):
    _, indexes = apps_output
    assert sorted(harness_app.APPS) == ["hms", "wackopicko"]
    assert not any(p["path"].startswith(("TCPDF/", "vendor/")) for p in indexes["hms"]["pages"])


def test_whole_app_totals(apps_output):
    """Nodes, S2 components / abstain / review, S5 todo / review / info. Phase 1 and 1b: hms (269, 59, 8, 180,
    133, 397, 230). Phase 2a: hms (269, 66, 4, 188, 179, 405, 295), wackopicko (383, 58, 4, 278, 164, 276, 113).
    Phase 2b: function components and the shell; in HMS the 4 caller-opened loops are Lists (R-F1b) and the 15
    TCPDF method calls are unresolved_call; in WackoPicko header and footer are one shell unit."""
    output, indexes = apps_output
    totals = {}
    for app, index in indexes.items():
        rows = [apps_results_table.page_row(output / app, entry) for entry in index["pages"]
                if entry["role"] != "shell_part"]
        totals[app] = tuple(sum(r[key] for r in rows) for key in ("nodes", "components", "abstain", "review", "todo",
                                                                    "s5review", "info"))
    assert totals == {"hms": (269, 68, 15, 174, 202, 391, 285), "wackopicko": (383, 57, 5, 307, 156, 299, 120)}
    hms = [apps_results_table.page_row(output / "hms", entry) for entry in indexes["hms"]["pages"]]
    assert (sum(r["review_unlabelled"] for r in hms), sum(r["review_other"] for r in hms)) == (171, 3)
    assert (sum(r["s5review_unlabelled"] for r in hms), sum(r["s5review_other"] for r in hms)) == (171, 220)
    reasons = {}
    for r in hms:
        for reason, n in r["abstains"].items():
            reasons[reason] = reasons.get(reason, 0) + n
    assert reasons == {"unresolved_call": 15}  # the TCPDF method calls; no container_undetermined, no no_rule


def test_hms_select_star_reads_are_expanded_from_its_own_schema(apps_output):
    output, indexes = apps_output
    assert indexes["hms"]["schemaFiles"] == ["myhmsdb.sql"]
    timeline = json.loads((output / "hms" / "admin-panel1.php" / "timeline.json").read_text(encoding="utf-8"))
    direct = [r for r in reads(timeline) if r.get("basis")]
    assert len(direct) == 15 and {r["source"]["table"] for r in direct} == {"appointmenttb", "contact"}
    copied = [r["derivedFrom"][0] for r in reads(timeline) if r["sourceKind"] == "computed"]  # $v = $row['k']
    assert len(copied) == 22 and {r["source"]["table"] for r in copied} == {"doctb", "patreg", "prestb"}
    expanded = direct + copied
    assert {r["basis"] for r in expanded} == {"schema-expanded"}
    assert not [r for r in reads(timeline) if r["confidence"] == "ambiguous"]


def test_call_entries_name_functions_of_the_app(apps_output):
    output, indexes = apps_output
    index = indexes["wackopicko"]
    timeline = json.loads((output / "wackopicko" / "website__guestbook.php" / "timeline.json").read_text(
        encoding="utf-8"))
    by_name = {timeline["callNames"][c["id"]]: c for c in calls(timeline)}
    header = by_name["our_header"]
    assert header["outputsInside"] is True
    assert index["functions"][header["callee"]] == {"name": "our_header", "path": "website/include/html_functions.php"}
    assert sum(p["calls"] for p in index["pages"]) == 231


@needs_clones
def test_two_harness_runs_give_byte_identical_timelines():
    first, second = harness_app.app_timelines("wackopicko"), harness_app.app_timelines("wackopicko")
    assert [(p.key, harness_app.dump_timeline(p.timeline)) for p in first.pages] == \
        [(p.key, harness_app.dump_timeline(p.timeline)) for p in second.pages]


def test_reports_are_deterministic_and_only_read_the_outputs(apps_output, tmp_path):
    output, _ = apps_output
    for module in (apps_results_table, pattern_log):
        first = module.render(output, tmp_path / "a.md")
        assert first == module.render(output, tmp_path / "b.md")
    table = apps_results_table.render(output, tmp_path / "a.md")
    assert "| **hms total** |" in table and "| **wackopicko total** |" in table and "not from Member 01" in table
    log = pattern_log.render(output, tmp_path / "a.md")
    assert "Page output inside the list's container, outside the loop" in log and "§8 mapping" in log
    assert "`hms/admin-panel1.php:286:23`" in log  # file:line:col, the column 1-based


def test_batch_compile_check_attributes_a_planted_error_to_its_page(apps_output, tmp_path):
    output, _ = apps_output
    if compile_check.unavailable():
        pytest.skip(f"compile check skipped: {compile_check.unavailable()}")
    import shutil

    for key in ("index.php", "error.php"):
        shutil.copytree(output / "hms" / key / "generated", tmp_path / key)
    planted = tmp_path / "error.php" / "lib" / "c2-todo.tsx"  # (a no-endpoint page has no lib/api.ts)
    planted.write_text(planted.read_text(encoding="utf-8") + 'export const wrong: number = "text";\n',
                       encoding="utf-8")
    errors, _ = compile_check.check_many("planted_many", {key: tmp_path / key for key in ("index.php", "error.php")})
    assert errors == {"index.php": 0, "error.php": 1}
