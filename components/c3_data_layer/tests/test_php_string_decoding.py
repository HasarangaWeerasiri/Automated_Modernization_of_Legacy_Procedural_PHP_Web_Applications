"""PHP string-literal decoding in SQL recovery.

Synthetic cases only (no benchmark app). PHP itself is the oracle: each
literal body is evaluated by `php` and its bytes must equal C3's decoding.
"""

import json
import shutil
import subprocess

import pytest

from c3.conversion.query_converter import convert_queries
from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import LITERAL_DOLLAR, _decode_php_string, recover_queries_from_ast

needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="php not on PATH")

DOUBLE_QUOTED = [
    r"a\tb",                 # tab
    r"line\nnext\rret",      # newline, carriage return
    r"v\vf\fe\e",            # vertical tab, form feed, escape
    r"back\\slash",          # escaped backslash
    r"quote\"inside",        # escaped double quote
    r"dollar\$name",         # escaped dollar: literal, not a variable
    r"oct\101\60\7",         # octal 1-3 digits
    r"oct\400",              # octal above \377 wraps (PHP behaviour)
    r"hex\x41\x4a\xF",       # hex 1-2 digits
    r"utf\xC3\xA9",          # hex bytes forming UTF-8
    r"uni\u{e9}\u{1F600}",   # unicode escapes
    r"unknown\q\d\'",        # unknown escapes keep the backslash
    r"u\u41",                # \u without braces is literal
    r"ends\\",              # escaped backslash at the end
]
SINGLE_QUOTED = [
    r"a\tb",                 # no escape in single quotes
    r"it\'s",                # \' -> '
    r"back\\slash",          # \\ -> \
    r"dollar\$name",         # literal
    r"keep\n\x41",           # literal
]


def php_bytes(literal: str) -> bytes:
    code = f"echo bin2hex({literal});"
    completed = subprocess.run(["php", "-d", "display_errors=stderr", "-r", code], capture_output=True, text=True, check=True)
    return bytes.fromhex(completed.stdout.strip())


@needs_php
@pytest.mark.parametrize("body", DOUBLE_QUOTED)
def test_double_quoted_matches_php(body):
    decoded = _decode_php_string(body, '"').replace(LITERAL_DOLLAR, "$")

    assert decoded.encode("utf-8", errors="surrogateescape") == php_bytes(f'"{body}"')


@needs_php
@pytest.mark.parametrize("body", SINGLE_QUOTED)
def test_single_quoted_matches_php(body):
    decoded = _decode_php_string(body, "'")

    assert decoded.encode("utf-8") == php_bytes(f"'{body}'")


def recover(tmp_path, statement):
    (tmp_path / "page.php").write_text("<?php\n" + statement + "\n", encoding="utf-8")
    queries = recover_queries_from_ast(analyze_php_ast(tmp_path))
    return queries[0], convert_queries(queries)[0]


def test_tab_inside_double_quoted_sql_is_whitespace(tmp_path):
    # A literal TAB in the PHP source is printed as \t by the PHP pretty-printer.
    query, converted = recover(tmp_path, 'mysqli_query($c, "UPDATE t SET a = \'$x\' ,\tb = \'$y\' WHERE id = \'$id\'");')

    assert query["recovered_sql"] == "UPDATE t SET a = '{{x}}' , b = '{{y}}' WHERE id = '{{id}}'"
    assert converted["status"] == "CONVERTED"


def test_escaped_dollar_is_not_a_variable(tmp_path):
    query, _ = recover(tmp_path, 'mysqli_query($c, "SELECT a FROM t WHERE price = \'\\$total\' AND id = $id");')

    assert query["dynamic_variables"] == ["id"]
    assert query["recovered_sql"] == "SELECT a FROM t WHERE price = '$total' AND id = {{id}}"


def test_single_quoted_sql_keeps_backslash_sequences(tmp_path):
    query, _ = recover(tmp_path, "mysqli_query($c, 'SELECT a FROM t WHERE note = \\'x\\\\ny\\'');")

    # PHP value: SELECT a FROM t WHERE note = 'x\ny' (backslash + n, not a newline)
    assert query["recovered_sql"] == "SELECT a FROM t WHERE note = 'x\\ny'"
