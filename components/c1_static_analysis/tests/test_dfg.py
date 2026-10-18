"""Reaching definitions, use-def chains and origins (src/c1/dfg.py).

The first test is the acceptance criterion from the project master reference (6.2):
the CFG and DFG must trace `$_GET['id']` through variable assignments to `echo`.
"""

from __future__ import annotations

from c1.astdoc import AstDoc
from c1.cfg import build_project_cfgs
from c1.dfg import build_project_dfg
from c1.includes import build_includes
from conftest import php_ast


def analyse(files: dict[str, str] | str):
    if isinstance(files, str):
        files = {"p.php": files}
    paths = sorted(files, key=lambda p: p.encode("utf-8"))
    asts = [AstDoc(php_ast(files[p], f"f{i:03d}", p)) for i, p in enumerate(paths)]
    manifest = {"files": [{"path": p, "fileId": f"f{i:03d}"} for i, p in enumerate(paths)],
                "excluded": [], "contentHash": "t"}
    cfgs, _ = build_project_cfgs(asts)
    inc = build_includes(asts, cfgs, manifest)
    p = build_project_dfg(asts, cfgs, inc)
    return p, {a.path: a for a in asts}


def doc(p, path_or_id="f000"):
    return p.file_doc(path_or_id)


def echo_origins(p, ast, file_id="f000", unit=0):
    """Origins of every variable read inside an echo, in source order."""
    d = p.file_doc(file_id)["units"][unit]
    return [(u["var"], u["origins"]) for u in d["uses"] if ast.kind(u["item"]) == "Stmt_Echo"]


def test_acceptance_get_id_traced_through_assignments_to_echo():
    src = "<?php\n$id = $_GET['id'];\n$x = $id;\n$y = 'Item ' . $x;\necho $y;\n"
    p, asts = analyse(src)
    assert echo_origins(p, asts["p.php"]) == [("y", ["input:GET:id"])]
    # and the use-def chain itself: echo $y -> $y = ... -> $x -> $id
    u = p.file_doc("f000")["units"][0]
    y_use = next(x for x in u["uses"] if x["var"] == "y")
    assert len(y_use["defs"]) == 1


def test_strong_definition_kills_the_earlier_one():
    p, asts = analyse("<?php\n$a = $_GET['a'];\n$a = 'fixed';\necho $a;\n")
    assert echo_origins(p, asts["p.php"]) == [("a", ["literal"])]


def test_branches_merge_both_definitions():
    p, asts = analyse("<?php\nif ($c) { $a = $_GET['a']; } else { $a = $_POST['a']; }\necho $a;\n")
    assert echo_origins(p, asts["p.php"])[0][1] == ["input:GET:a", "input:POST:a"]


def test_variable_defined_on_one_path_only_is_maybe_undefined():
    p, asts = analyse("<?php\nif ($c) { $a = 1; }\necho $a;\n")
    u = p.file_doc("f000")["units"][0]
    a_use = next(x for x in u["uses"] if x["var"] == "a")
    assert len(a_use["defs"]) == 1  # only one definition reaches; the other path has none


def test_loop_converges():
    p, asts = analyse("<?php\n$i = 0;\nwhile ($i < 3) { $i = $i + 1; }\necho $i;\n")
    o = dict(echo_origins(p, asts["p.php"]))["i"]
    assert "literal" in o


def test_array_element_write_is_weak():
    p, asts = analyse("<?php\n$a = [];\n$a['k'] = $_GET['k'];\necho $a['k'];\n")
    assert echo_origins(p, asts["p.php"])[0][1] == ["input:GET:k", "literal"]


def test_query_fetch_column_chain_and_sql_origins():
    src = """<?php
$r = mysqli_query($con, "SELECT name FROM t WHERE id=" . $_GET['id']);
while ($row = mysqli_fetch_assoc($r)) {
    echo $row['name'];
}
"""
    p, asts = analyse(src)
    u = p.file_doc("f000")["units"][0]
    q = u["queries"][0]
    assert q["call"] == "mysqli_query" and "input:GET:id" in q["sqlOrigins"]
    assert echo_origins(p, asts["p.php"]) == [("row", [f"column:{q['node']}:name"])]


def test_or_die_keeps_the_query_flow():
    src = "<?php\n$r = mysqli_query($c, 'SELECT a FROM t') or die('x');\n$row = mysqli_fetch_array($r);\necho $row['a'];\n"
    p, asts = analyse(src)
    q = p.file_doc("f000")["units"][0]["queries"][0]
    assert echo_origins(p, asts["p.php"]) == [("row", [f"column:{q['node']}:a"])]


def test_foreach_over_result_rows():
    src = "<?php\n$res = mysqli_query($c, 'SELECT n FROM t');\nforeach ($res as $row) { echo $row['n']; }\n"
    p, asts = analyse(src)
    q = p.file_doc("f000")["units"][0]["queries"][0]
    assert echo_origins(p, asts["p.php"]) == [("row", [f"column:{q['node']}:n"])]


def test_include_defines_variables_in_the_callers_scope():
    # PHP manual (include): included code inherits the variable scope of the line
    files = {"db.php": "<?php\n$con = mysqli_connect('h', 'u', 'p', 'd');\n",
             "page.php": "<?php\ninclude 'db.php';\n$r = mysqli_query($con, 'SELECT 1');\necho $con;\n"}
    p, asts = analyse(files)
    page = asts["page.php"]
    assert echo_origins(p, page, page.file_id) == [("con", ["call:mysqli_connect"])]


def test_unresolved_include_may_define_anything():
    files = {"page.php": "<?php\ninclude $_GET['p'] . '.php';\necho $z;\n"}
    p, asts = analyse(files)
    o = echo_origins(p, asts["page.php"])[0][1]
    assert o == ["unknown:include-unresolved"]


def test_function_return_summary_substitutes_arguments():
    src = "<?php\nfunction clean($x) { return trim($x); }\necho clean($_GET['q']);\n"
    p, asts = analyse(src)
    d = p.file_doc("f000")["units"][0]
    # the echo has no variable use, so look at the origin of the echoed expression
    a = asts["p.php"]
    echo = next(n for n in a.doc["nodes"] if n["kind"] == "Stmt_Echo")
    o = p.origins.of_expr("f000", a.root_id, echo["fields"]["exprs"][0])
    assert "input:GET:q" in o and "fn:clean" in o


def test_static_method_summary_reaches_the_query_column():
    # WackoPicko style: Users::get_user($id) returns a fetched row
    files = {
        "users.php": """<?php
class Users {
  static function get_user($id) {
    $res = mysqli_query($GLOBALS['db'], "SELECT * FROM users WHERE id = '$id'");
    return mysqli_fetch_array($res);
  }
}
""",
        "view.php": "<?php\nrequire_once('users.php');\n$user = Users::get_user($_GET['userid']);\necho $user['login'];\n",
    }
    p, asts = analyse(files)
    v = asts["view.php"]
    o = echo_origins(p, v, v.file_id)[0][1]
    assert any(x.startswith("column:f000#") and x.endswith(":login") for x in o)
    assert "method:Users::get_user" in o


def test_this_method_call_inside_class():
    src = """<?php
class R {
  function row() { return mysqli_fetch_assoc(mysqli_query($c, 'SELECT a FROM t')); }
  function show() { $r = $this->row(); echo $r['a']; }
}
"""
    p, asts = analyse(src)
    a = asts["p.php"]
    units = p.file_doc("f000")["units"]
    show = next(u for u in units if u["name"] == "R::show")
    use = next(u for u in show["uses"] if u["var"] == "r")
    assert any(o.startswith("column:") and o.endswith(":a") for o in use["origins"])


def test_session_reads_and_writes_are_sources():
    src = "<?php\n$row = mysqli_fetch_assoc($r);\n$_SESSION['uid'] = $row['id'];\nif ($_SESSION['role'] == 'admin') { echo 1; }\n"
    p, asts = analyse(src)
    s = p.file_doc("f000")["units"][0]["sources"]
    write = next(x for x in s if x.get("write"))
    assert write["superglobal"] == "SESSION" and write["dims"] == ["uid"]
    read = next(x for x in s if not x.get("write"))
    assert read["dims"] == ["role"]


def test_byref_builtin_defines_its_argument():
    p, asts = analyse("<?php\npreg_match('/(\\d+)/', $s, $m);\necho $m[1];\n")
    assert echo_origins(p, asts["p.php"]) == [("m", ["call:preg_match"])]


def test_extract_is_flagged_and_havocs():
    p, asts = analyse("<?php\nextract($_POST);\necho $name;\n")
    u = p.file_doc("f000")["units"][0]
    assert any("extract" in f["reason"] for f in u["flags"])
    assert echo_origins(p, asts["p.php"])[0][1] == ["unknown:havoc"]


def test_list_destructuring_defines_each_variable():
    p, asts = analyse("<?php\nlist($a, $b) = mysqli_fetch_row($r);\necho $a . $b;\n")
    vars_ = [v for v, _ in echo_origins(p, asts["p.php"])]
    assert vars_ == ["a", "b"]


def test_parameters_are_definitions_at_entry():
    src = "<?php\nfunction f($x) { echo $x; }\n"
    p, asts = analyse(src)
    f = p.file_doc("f000")["units"][1]
    assert f["uses"][0]["origins"] == ["param:x"]


def test_included_file_reads_from_its_includer_are_context():
    files = {"h.php": "<?php echo $title;\n", "p.php": "<?php $title = 'x';\ninclude 'h.php';\n"}
    p, asts = analyse(files)
    h = p.file_doc("f000")["units"][0]
    assert h["uses"][0]["origins"] == ["context:title"]


def test_dfg_output_is_deterministic():
    src = "<?php\n$a = $_GET['a'];\nif ($a) { $b = $a; }\necho $b;\n"
    p1, _ = analyse(src)
    p2, _ = analyse(src)
    assert p1.file_doc("f000") == p2.file_doc("f000")


def test_escape_string_keeps_the_origin_of_its_value_argument():
    # mysqli_real_escape_string($link, $value): the value is the 2nd argument (crud-php-complete login)
    p, asts = analyse("<?php\n$u = mysqli_real_escape_string($mysqli, $_POST['username']);\necho $u;\n")
    assert "input:POST:username" in echo_origins(p, asts["p.php"])[0][1]
