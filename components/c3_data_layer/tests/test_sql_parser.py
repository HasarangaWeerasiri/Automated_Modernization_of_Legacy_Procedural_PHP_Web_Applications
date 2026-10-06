from pathlib import Path

from c3.mapping.sql_recovery import recover_queries
from c3.mapping.sql_parser import analyze_column_usage


BENCHMARK_PATH = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "apps"
    / "mixed_crud"
    / "legacy"
)


def get_usage():
    queries = recover_queries(BENCHMARK_PATH)

    return analyze_column_usage(queries)


def test_parser_preserves_query_count():
    usage = get_usage()

    assert len(usage) == 6


def test_five_queries_are_parsed():
    usage = get_usage()

    parsed = [
        item
        for item in usage
        if item["status"] == "PARSED"
    ]

    assert len(parsed) == 5


def test_select_table_and_columns():
    usage = get_usage()

    assert usage[0]["operation"] == "SELECT"
    assert usage[0]["tables"] == ["products"]

    assert usage[0]["columns"] == [
        "id",
        "title",
        "price",
    ]


def test_delete_column_usage():
    usage = get_usage()

    assert usage[2]["operation"] == "DELETE"
    assert usage[2]["tables"] == ["products"]
    assert usage[2]["columns"] == ["id"]


def test_update_column_usage():
    usage = get_usage()

    assert usage[4]["operation"] == "UPDATE"
    assert usage[4]["tables"] == ["products"]

    assert usage[4]["columns"] == [
        "title",
        "price",
        "id",
    ]


def test_unresolved_query_remains_unresolved():
    usage = get_usage()

    query = usage[5]

    assert query["status"] == "UNRESOLVED"
    assert query["tables"] == []
    assert query["columns"] == []

    assert (
        "unsupported dynamic expression"
        in query["reason"].lower()
    )