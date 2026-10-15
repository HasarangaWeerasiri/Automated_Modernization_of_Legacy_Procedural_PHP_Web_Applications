"""Soundness of SQL recovery around loops and switch statements.

Fixtures are written for this test only (not taken from any benchmark).
SQL that can depend on loop iterations or on which switch case ran must
be UNRESOLVED with file, line and reason. Sound straight-line uses inside
a loop or a single case must still be recovered.
"""

from pathlib import Path

import pytest

from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


LOOP_REASON_CODE = "LOOP_OR_SWITCH_CONTROL_FLOW"


def _recover(tmp_path: Path, php: str) -> list[dict]:
    php_file = tmp_path / "fixture.php"
    php_file.write_text(php, encoding="utf-8")
    return recover_queries_from_ast(analyze_php_ast(php_file))


MUST_ABSTAIN = {
    "foreach_append_value": """<?php
$sql = "SELECT id FROM items WHERE 1=0";
foreach ($_GET['names'] as $n) {
    $sql .= " OR name = '$n'";
}
mysqli_query($conn, $sql);
""",
    "while_append_delete": """<?php
$sql = "DELETE FROM items WHERE 1=0";
$i = 0;
while ($i < 3) {
    $sql .= " OR id = $i";
    $i++;
}
mysqli_query($conn, $sql);
""",
    "for_append": """<?php
$sql = "SELECT id FROM items WHERE 1=0";
for ($i = 0; $i < 3; $i++) {
    $sql .= " OR id = $i";
}
mysqli_query($conn, $sql);
""",
    "do_while_append": """<?php
$sql = "UPDATE items SET qty = 0 WHERE 1=0";
do {
    $sql .= " OR id = $id";
} while (false);
mysqli_query($conn, $sql);
""",
    "switch_assign_then_call": """<?php
switch ($_GET['mode']) {
    case 'a':
        $sql = "SELECT id FROM items";
        break;
    default:
        $sql = "SELECT id FROM orders";
}
mysqli_query($conn, $sql);
""",
    "switch_overrides_base": """<?php
$sql = "SELECT id FROM items";
switch ($_GET['mode']) {
    case 'all':
        $sql = "SELECT id FROM orders";
        break;
}
mysqli_query($conn, $sql);
""",
    "loop_assign_then_call_after_loop": """<?php
foreach ($_GET['ids'] as $id) {
    $sql = "DELETE FROM items WHERE id = $id";
}
mysqli_query($conn, $sql);
""",
    "back_edge_reassign_after_call": """<?php
$sql = "SELECT id FROM items";
while ($row = next_row()) {
    mysqli_query($conn, $sql);
    $sql = "DELETE FROM items WHERE id = " . $row['id'];
}
""",
    "accumulate_inside_loop_with_call": """<?php
$sql = "SELECT id FROM items WHERE 1=0";
foreach ($_GET['names'] as $n) {
    $sql .= " OR name = '$n'";
    mysqli_query($conn, $sql);
}
""",
}


@pytest.mark.parametrize("name", sorted(MUST_ABSTAIN))
def test_loop_or_switch_dependent_sql_abstains(tmp_path, name):
    recovered = _recover(tmp_path, MUST_ABSTAIN[name])

    assert len(recovered) == 1
    query = recovered[0]

    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == LOOP_REASON_CODE
    assert query["reason"]
    assert query["source_file"].endswith("fixture.php")
    assert isinstance(query["source_line"], int)

    converted = convert_queries(recovered)
    assert converted[0]["status"] == "UNRESOLVED"


MUST_RECOVER = {
    "straight_line": (
        """<?php
$sql = "SELECT id FROM items";
$sql .= " WHERE id = " . $id;
mysqli_query($conn, $sql);
""",
        "SELECT id FROM items WHERE id = {{id}}",
    ),
    "fresh_assignment_and_call_in_same_loop": (
        """<?php
foreach ($_GET['ids'] as $id) {
    $sql = "DELETE FROM items WHERE id = $id";
    mysqli_query($conn, $sql);
}
""",
        "DELETE FROM items WHERE id = {{id}}",
    ),
    "base_before_loop_call_inside_loop": (
        """<?php
$sql = "SELECT id FROM items";
while ($row = next_row()) {
    mysqli_query($conn, $sql);
}
""",
        "SELECT id FROM items",
    ),
    "base_and_append_inside_same_loop": (
        """<?php
foreach ($_GET['ids'] as $id) {
    $sql = "SELECT id FROM items";
    $sql .= " WHERE id = $id";
    mysqli_query($conn, $sql);
}
""",
        "SELECT id FROM items WHERE id = {{id}}",
    ),
    "assignment_and_call_in_same_case": (
        """<?php
switch ($_GET['mode']) {
    case 'a':
        $sql = "SELECT id FROM items";
        mysqli_query($conn, $sql);
        break;
}
""",
        "SELECT id FROM items",
    ),
    "direct_sql_call_inside_loop": (
        """<?php
foreach ($_GET['ids'] as $id) {
    mysqli_query($conn, "DELETE FROM items WHERE id = $id");
}
""",
        "DELETE FROM items WHERE id = {{id}}",
    ),
}


@pytest.mark.parametrize("name", sorted(MUST_RECOVER))
def test_sound_loop_and_switch_uses_still_recover(tmp_path, name):
    php, expected_sql = MUST_RECOVER[name]

    recovered = _recover(tmp_path, php)

    assert len(recovered) == 1
    assert recovered[0]["status"] == "RECOVERED", recovered[0]
    assert recovered[0]["recovered_sql"] == expected_sql


def test_extractor_reports_loop_context(tmp_path):
    php_file = tmp_path / "fixture.php"
    php_file.write_text(
        MUST_ABSTAIN["foreach_append_value"],
        encoding="utf-8",
    )

    ast_data = analyze_php_ast(php_file)

    contexts = {
        assignment["source_line"]: assignment["loop_context"]
        for assignment in ast_data["assignments"]
        if assignment["variable"] == "$sql"
    }

    assert contexts[2] == []
    assert len(contexts[4]) == 1
    assert contexts[4][0].startswith("FOREACH@")
    assert ast_data["sql_calls"][0]["loop_context"] == []
