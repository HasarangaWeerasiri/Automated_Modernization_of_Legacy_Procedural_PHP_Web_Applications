"""Concern labels (src/c1/concerns.py), format agreed with C2 (CONTEXT.md "Labels")."""

from __future__ import annotations

from pipeline import Run

PAGE = """<?php
$con = mysqli_connect('localhost', 'root', '', 'db');
$id = $_GET['id'];
$query = "select name, price from products where id='$id'";
$result = mysqli_query($con, $query);
while ($row = mysqli_fetch_array($result)) {
    $name = $row['name'];
    echo "<td>$name</td>";
}
if ($_SESSION['role'] == 'admin') {
    echo "<a href='edit.php'>Edit</a>";
}
if ($_SESSION['role'] == 'admin') {
    $stats = mysqli_query($con, "SELECT COUNT(*) FROM products");
}
if (isset($_POST['save'])) {
    header('Location: done.php');
    exit;
}
?>
<p>footer</p>
"""


def test_label_format_matches_the_c2_contract():
    r = Run(PAGE)
    assert r.labels["schemaVersion"] == "1.0" and r.labels["generatedFor"] == "test"
    for lab in r.labels["labels"].values():
        assert lab["concern"] in ("presentation", "business_logic", "data_access", "mixed", "undecided")
        assert lab["basis"] in ("rule", "abstained")
        assert lab["ruleId"] in r.labels["rules"]
        assert (lab["basis"] == "abstained") == (lab["concern"] == "undecided")


def test_statement_rules_on_a_typical_page():
    r = Run(PAGE)
    assert r.label_of("$con = mysqli_connect")["ruleId"] == "R04"
    assert r.label_of("$id = $_GET")["ruleId"] == "R13"
    assert r.label_of("$query = \"select")["ruleId"] == "R05"     # SQL text, found through the DFG
    assert r.label_of("$result = mysqli_query")["ruleId"] == "R02"
    assert r.label_of("$row = mysqli_fetch_array")["ruleId"] == "R03"
    assert r.label_of('echo "<td>')["ruleId"] == "R01"
    assert r.label_of("<p>footer", "Stmt_InlineHTML")["concern"] == "presentation"


def test_value_only_echoed_is_a_display_binding():
    # forward use-def: $name is only ever used by an echo
    r = Run(PAGE)
    assert r.label_of("$name = $row")["ruleId"] == "R17"
    assert r.label_of("$name = $row")["concern"] == "presentation"


def test_master_reference_worked_example():
    # Master reference 3.1 step 7: the same session check is AUTHORIZATION when it gates
    # a query and DISPLAY when it only outputs HTML
    r = Run(PAGE)
    ifs = [lab for nid, lab in sorted(r.labels["labels"].items()) if r.asts[0].kind(nid) == "Stmt_If"]
    assert [x["ruleId"] for x in ifs] == ["R08", "R06", "R06"]
    assert ifs[0]["concern"] == "presentation" and ifs[1]["concern"] == "business_logic"


def test_row_loop_that_only_outputs_is_render_rows():
    r = Run(PAGE)
    assert r.label_of("while ($row", "Stmt_While")["ruleId"] == "R12"


def test_mixed_statement():
    r = Run("<?php\necho mysqli_num_rows(mysqli_query($c, 'select 1'));\n")
    assert r.label_of("echo mysqli_num_rows")["concern"] == "mixed"


def test_branch_mixing_output_and_logic_abstains():
    r = Run("<?php\nif ($x) { $total = $a + $b; echo $total; }\n")
    lab = r.label_of("if ($x)", "Stmt_If")
    assert lab["concern"] == "undecided" and lab["basis"] == "abstained" and lab["ruleId"] == "R10"


def test_function_effects_propagate_to_call_sites():
    src = "<?php\nfunction load() { return mysqli_query($GLOBALS['c'], 'select 1'); }\nfunction show() { echo load(); }\nshow();\nload();\n"
    r = Run(src)
    assert r.label_of("show();")["concern"] == "mixed"
    assert r.label_of("load();")["ruleId"] == "R02"


def test_state_change_and_include_rules():
    r = Run({"p.php": "<?php\ninclude 'h.php';\nsession_start();\nsetcookie('a', 'b');\n", "h.php": "<?php\n"})
    assert r.label_of("include 'h.php'")["ruleId"] == "R18"
    assert r.label_of("session_start")["ruleId"] == "R07"


def test_labels_are_deterministic():
    assert Run(PAGE).labels == Run(PAGE).labels


def test_query_builder_call_abstains_and_is_not_silently_missed():
    # core-php-admin-panel uses MysqliDb ($db->where(...)->get('customers')): no SQL to read,
    # ORMs on the legacy side are out of scope (master reference 2.2), so the label abstains
    r = Run("<?php\n$db = getDbInstance();\n$rows = $db->get('customers');\n$this_is_fine = strlen('x');\n")
    lab = r.label_of("$rows = $db->get")
    assert lab["ruleId"] == "R19" and lab["concern"] == "undecided" and lab["basis"] == "abstained"
    assert r.label_of("$this_is_fine")["ruleId"] == "R09"
