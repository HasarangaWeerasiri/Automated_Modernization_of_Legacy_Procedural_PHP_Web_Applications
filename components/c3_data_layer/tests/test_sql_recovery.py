from pathlib import Path

from c3.mapping.sql_recovery import recover_queries


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