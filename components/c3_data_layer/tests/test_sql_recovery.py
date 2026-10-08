from pathlib import Path

from c3.mapping.sql_recovery import recover_queries
from c3.mapping.sql_recovery import recover_queries_from_ast

BENCHMARK_PATH = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "mixed_crud"
    / "legacy"
)


def get_queries():
    return recover_queries(BENCHMARK_PATH)


def test_recovery_returns_six_queries():
    queries = get_queries()

    assert len(queries) == 6


def test_supported_queries_are_recovered():
    queries = get_queries()

    recovered = [
        query
        for query in queries
        if query["status"] == "RECOVERED"
    ]

    assert len(recovered) == 5


def test_interpolated_select_is_recovered():
    queries = get_queries()

    assert queries[1]["recovered_sql"] == (
        "SELECT id, title, price FROM products "
        "WHERE id = {{id}}"
    )

    assert queries[1]["dynamic_variables"] == ["id"]


def test_concat_delete_is_recovered():
    queries = get_queries()

    assert queries[2]["recovered_sql"] == (
        "DELETE FROM products WHERE id = {{id}}"
    )


def test_latest_assignment_is_used():
    queries = get_queries()

    assert queries[3]["recovered_sql"] == (
        "SELECT id, title, price FROM products"
    )

    assert "old_products" not in queries[3]["recovered_sql"]


def test_multiline_update_is_recovered():
    queries = get_queries()

    assert queries[4]["operation"] == "UPDATE"

    assert queries[4]["recovered_sql"] == (
        "UPDATE products SET title = '{{title}}', "
        "price = {{price}} WHERE id = {{id}}"
    )

    assert set(
        queries[4]["dynamic_variables"]
    ) == {"title", "price", "id"}


def test_unsupported_dynamic_query_abstains():
    queries = get_queries()

    query = queries[5]

    assert query["status"] == "UNRESOLVED"
    assert query["operation"] is None

    assert (
        "unsupported dynamic expression"
        in query["reason"].lower()
    )

HARD_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "hard_crud"
    / "legacy"
)


def get_hard_queries():
    return recover_queries(HARD_BENCHMARK_PATH)


def test_dynamic_order_by_abstains_as_structural():
    queries = get_hard_queries()

    query = queries[1]

    assert query["query_id"] == "Q002"
    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == "STRUCTURAL"
    assert query["operation"] is None

def test_dynamic_in_list_abstains_as_structural():
    queries = get_hard_queries()

    query = queries[2]

    assert query["query_id"] == "Q003"
    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == "STRUCTURAL"
    assert query["operation"] is None

def test_conditional_sql_append_abstains_as_branching():
    queries = get_hard_queries()

    query = queries[3]

    assert query["query_id"] == "Q004"
    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == "BRANCHING"
    assert query["operation"] is None


def test_direct_sql_literal_is_recovered():
    ast_data = {
        "sql_calls": [
            {
                "source_file": "patients.php",
                "source_line": 4,
                "executor": "mysqli_query",
                "sql_argument": '"SELECT id, full_name FROM patients"',
            }
        ],
        "assignments": [],
    }

    result = recover_queries_from_ast(ast_data)

    assert len(result) == 1
    assert result[0]["status"] == "RECOVERED"
    assert result[0]["operation"] == "SELECT"
    assert result[0]["recovered_sql"] == (
        "SELECT id, full_name FROM patients"
    )


def test_direct_interpolated_sql_is_recovered():
    ast_data = {
        "sql_calls": [
            {
                "source_file": "patients.php",
                "source_line": 5,
                "executor": "mysqli_query",
                "sql_argument": (
                    '"SELECT id FROM patients WHERE id = \'{$id}\'"'
                ),
            }
        ],
        "assignments": [],
    }

    result = recover_queries_from_ast(ast_data)

    assert len(result) == 1
    assert result[0]["status"] == "RECOVERED"
    assert result[0]["operation"] == "SELECT"
    assert "{{id}}" in result[0]["recovered_sql"]


def test_q014_preserves_both_branch_sql_variants():
    from pathlib import Path
    from c3.mapping.sql_locator import analyze_php_ast
    from c3.mapping.sql_recovery import recover_queries_from_ast

    php_file = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
        / "14_branch_query.php"
    )

    queries = recover_queries_from_ast(
        analyze_php_ast(php_file)
    )

    assert len(queries) == 1

    query = queries[0]

    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == "BRANCHING"
    assert query["operation"] is None

    variants = query["sql_variants"]

    assert len(variants) == 2

    assert variants[0]["source_line"] == 6
    assert variants[0]["control_flow"] == ["IF"]
    assert variants[0]["sql"] == (
        "SELECT id, full_name FROM doctors WHERE active = 1"
    )

    assert variants[1]["source_line"] == 8
    assert variants[1]["control_flow"] == ["ELSE", "IF"]
    assert variants[1]["sql"] == (
        "SELECT id, full_name FROM doctors"
    )


def test_q016_preserves_conditional_append_variants():
    from pathlib import Path
    from c3.mapping.sql_locator import analyze_php_ast
    from c3.mapping.sql_recovery import recover_queries_from_ast

    php_file = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "apps"
        / "clinic_inventory_legacy_hard"
        / "legacy"
        / "16_conditional_concat.php"
    )

    queries = recover_queries_from_ast(
        analyze_php_ast(php_file)
    )

    assert len(queries) == 1

    query = queries[0]

    assert query["status"] == "UNRESOLVED"
    assert query["reason_code"] == "BRANCHING"
    assert query["operation"] is None

    variants = query["sql_variants"]

    assert len(variants) == 2

    assert variants[0] == {
        "source_line": 4,
        "control_flow": [],
        "sql": (
            "SELECT id, full_name FROM patients WHERE 1=1"
        ),
        "fully_supported": True,
    }

    assert variants[1] == {
        "source_line": 6,
        "control_flow": ["IF"],
        "sql": (
            "SELECT id, full_name FROM patients WHERE 1=1 "
            "AND department_id = "
            "{{UNRESOLVED:$_GET['department_id']}}"
        ),
        "fully_supported": False,
    }
