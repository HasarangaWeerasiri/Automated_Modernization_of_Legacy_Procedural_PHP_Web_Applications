"""Positive and negative tests for the Q014 source-shape gate."""

from pathlib import Path

import pytest

from c3.mapping.branch_source_gate import validate_q014_source_shape


Q014_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "clinic_inventory_legacy_hard"
    / "legacy"
    / "14_branch_query.php"
)


@pytest.mark.parametrize(
    "name,addition",
    [
        ("unset_sql", "unset($sql);"),
        ("reference_assignment", "$sql =& $otherSql;"),
        ("function_side_effect", "modifySql($sql);"),
        ("dynamic_variable", "${$name} = 'other';"),
        ("extra_sql_assignment", '$sql = "SELECT id FROM doctors";'),
        ("increment", "$sql++;"),
        ("unknown_include", "include 'unknown.php';"),
        ("early_return", "return;"),
    ],
)
def test_unsafe_source_modification_rejected(tmp_path, name, addition):
    original = Q014_SOURCE.read_text(encoding="utf-8")

    modified = original.replace(
        "$result = mysqli_query",
        addition + "\n$result = mysqli_query",
        1,
    )

    assert modified != original

    source = tmp_path / f"{name}.php"
    source.write_text(modified, encoding="utf-8")

    assert validate_q014_source_shape(source) is False


def test_original_q014_source_accepted():
    assert validate_q014_source_shape(Q014_SOURCE) is True


def test_missing_source_fails_closed(tmp_path):
    assert validate_q014_source_shape(tmp_path / "missing.php") is False


def test_unexpected_comment_rejected(tmp_path):
    original = Q014_SOURCE.read_text(encoding="utf-8")

    modified = original.replace(
        "// S14 multiple assignments across branches: conservative abstention",
        "// unexpected comment",
        1,
    )

    source = tmp_path / "unexpected_comment.php"
    source.write_text(modified, encoding="utf-8")

    assert validate_q014_source_shape(source) is False
