"""Recovery of SQL built and executed inside the same conditional block (S13).

The SQL value is deterministic at the call when every conditional block
(if/elseif/else/catch body) around a chain assignment also encloses the
call. Fixtures are written for this test only, not taken from any
benchmark application.
"""

from pathlib import Path

import pytest

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast


def _recover_one(tmp_path: Path, php: str) -> dict:
    php_file = tmp_path / "fixture.php"
    php_file.write_text(php, encoding="utf-8")
    rows = recover_queries_from_ast(analyze_php_ast(php_file))
    assert len(rows) == 1
    return rows[0]


MUST_RECOVER = {
    "insert_inside_post_block": (
        """<?php
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name = mysqli_real_escape_string($conn, $_POST['name']);
    $sql = "INSERT INTO items (name) VALUES ('$name')";
    mysqli_query($conn, $sql);
}
""",
        "INSERT INTO items (name) VALUES ('{{name}}')",
    ),
    "update_inside_else_block": (
        """<?php
if ($error) {
    echo 'invalid';
} else {
    $sql = "UPDATE items SET qty = $qty WHERE id = $id";
    if (mysqli_query($conn, $sql)) { header('Location: /'); exit; }
}
""",
        "UPDATE items SET qty = {{qty}} WHERE id = {{id}}",
    ),
    "select_inside_elseif_block": (
        """<?php
if ($mode === 'a') {
    echo 'a';
} elseif ($mode === 'b') {
    $sql = "SELECT id FROM items WHERE kind = 'b'";
    mysqli_query($conn, $sql);
}
""",
        "SELECT id FROM items WHERE kind = 'b'",
    ),
    "nested_blocks": (
        """<?php
if (isset($_POST['save'])) {
    if ($valid) {
        $sql = "DELETE FROM items WHERE id = $id";
        mysqli_query($conn, $sql);
    }
}
""",
        "DELETE FROM items WHERE id = {{id}}",
    ),
    "base_outside_append_and_call_inside": (
        """<?php
$sql = "SELECT id FROM items";
if ($filter) {
    $sql .= " WHERE kind = '$kind'";
    mysqli_query($conn, $sql);
}
""",
        "SELECT id FROM items WHERE kind = '{{kind}}'",
    ),
    "assignment_and_call_inside_catch": (
        """<?php
try {
    risky();
} catch (Exception $e) {
    $sql = "INSERT INTO errors (msg) VALUES ('$msg')";
    mysqli_query($conn, $sql);
}
""",
        "INSERT INTO errors (msg) VALUES ('{{msg}}')",
    ),
}


@pytest.mark.parametrize("name", sorted(MUST_RECOVER))
def test_sql_built_and_executed_in_same_block_is_recovered(tmp_path, name):
    php, expected = MUST_RECOVER[name]

    row = _recover_one(tmp_path, php)

    assert row["status"] == "RECOVERED", row
    assert row["recovered_sql"] == expected


MUST_ABSTAIN = {
    "assign_in_if_call_after": """<?php
$sql = "SELECT id FROM items";
if ($all) {
    $sql = "SELECT id FROM items WHERE 1=1";
}
mysqli_query($conn, $sql);
""",
    "assign_in_if_call_in_else": """<?php
if ($a) {
    $sql = "SELECT id FROM items";
} else {
    mysqli_query($conn, $sql);
}
""",
    "append_in_if_call_after": """<?php
$sql = "SELECT id FROM items WHERE 1=1";
if ($kind) {
    $sql .= " AND kind = '$kind'";
}
mysqli_query($conn, $sql);
""",
    "if_else_assign_call_after": """<?php
if ($a) {
    $sql = "SELECT id FROM items";
} else {
    $sql = "SELECT id FROM orders";
}
mysqli_query($conn, $sql);
""",
    "assign_in_catch_call_after": """<?php
$sql = "SELECT id FROM items";
try {
    risky();
} catch (Exception $e) {
    $sql = "SELECT id FROM backup_items";
}
mysqli_query($conn, $sql);
""",
    "assign_in_elseif_call_in_else": """<?php
if ($a) {
    echo 'a';
} elseif ($b) {
    $sql = "SELECT id FROM items";
} else {
    mysqli_query($conn, $sql);
}
""",
    "sibling_append_in_other_branch": """<?php
$sql = "SELECT id FROM items WHERE 1=1";
if ($x) {
    $sql .= " AND a = 1";
} else {
    $sql .= " AND b = 1";
    mysqli_query($conn, $sql);
}
""",
}


@pytest.mark.parametrize("name", sorted(MUST_ABSTAIN))
def test_branch_dependent_sql_still_abstains(tmp_path, name):
    row = _recover_one(tmp_path, MUST_ABSTAIN[name])

    assert row["status"] == "UNRESOLVED"
    assert row["reason_code"] == "BRANCHING"
    assert row["reason"]


def test_else_body_is_not_inside_then_body(tmp_path):
    php_file = tmp_path / "fixture.php"
    php_file.write_text(MUST_ABSTAIN["assign_in_if_call_in_else"], encoding="utf-8")

    ast_data = analyze_php_ast(php_file)
    assignment = next(a for a in ast_data["assignments"] if a["variable"] == "$sql")
    call = ast_data["sql_calls"][0]

    assert [c.split("@")[0] for c in assignment["branch_context"]] == ["IF"]
    assert [c.split("@")[0] for c in call["branch_context"]] == ["ELSE"]
    assert not set(assignment["branch_context"]) & set(call["branch_context"])
