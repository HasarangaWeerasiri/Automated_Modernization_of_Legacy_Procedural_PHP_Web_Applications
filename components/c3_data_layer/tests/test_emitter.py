import importlib.util

from c3.generation.emitter import (
    generate_data_access_source,
)


def _sample_queries():
    return [
        {
            "query_id": "Q001",
            "source_file": "create_user.php",
            "source_line": 9,
            "operation": "INSERT",
            "table": "users",
            "parameters": [
                "name",
                "email",
            ],
            "parameterized_sql": (
                "INSERT INTO users "
                "(name, email, status) "
                "VALUES (:name, :email, 'active')"
            ),
            "status": "CONVERTED",
            "reason": None,
        },
        {
            "query_id": "Q002",
            "source_file": "delete_user.php",
            "source_line": 8,
            "operation": "DELETE",
            "table": "users",
            "parameters": ["id"],
            "parameterized_sql": (
                "DELETE FROM users WHERE id = :id"
            ),
            "status": "CONVERTED",
            "reason": None,
        },
        {
            "query_id": "Q003",
            "source_file": "update_user.php",
            "source_line": 8,
            "operation": "UPDATE",
            "table": "users",
            "parameters": [
                "name",
                "id",
            ],
            "parameterized_sql": (
                "UPDATE users SET name = :name "
                "WHERE id = :id"
            ),
            "status": "CONVERTED",
            "reason": None,
        },
        {
            "query_id": "Q004",
            "source_file": "users.php",
            "source_line": 4,
            "operation": "SELECT",
            "table": "users",
            "parameters": [],
            "parameterized_sql": (
                "SELECT id, name, email, status "
                "FROM users"
            ),
            "status": "CONVERTED",
            "reason": None,
        },
    ]


def test_insert_function_is_generated():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert (
        "def insert_user_q001"
        in source
    )

    assert (
        ":name"
        in source
    )

    assert (
        ":email"
        in source
    )


def test_update_function_is_generated():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert (
        "def update_user_q003"
        in source
    )

    assert (
        '"name": name'
        in source
    )

    assert (
        '"id": id'
        in source
    )


def test_delete_function_is_generated():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert (
        "def delete_user_q002"
        in source
    )


def test_select_function_is_generated():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert (
        "def select_users_q004"
        in source
    )

    assert (
        "return result.mappings().all()"
        in source
    )


def test_write_operations_commit():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert source.count(
        "session.commit()"
    ) == 3


def test_select_does_not_commit():
    source = generate_data_access_source(
        [
            _sample_queries()[3]
        ]
    )

    assert (
        "session.commit()"
        not in source
    )


def test_unresolved_query_is_not_generated():
    queries = _sample_queries()

    queries.append(
        {
            "query_id": "Q005",
            "source_file": "dynamic.php",
            "source_line": 10,
            "operation": None,
            "status": "UNRESOLVED",
            "reason": (
                "Unsupported dynamic expression."
            ),
        }
    )

    source = generate_data_access_source(
        queries
    )

    assert "Q005" not in source


def test_traceability_is_preserved():
    source = generate_data_access_source(
        _sample_queries()
    )

    assert "create_user.php:9" in source
    assert "Original query ID: Q001" in source


def test_generated_source_is_valid_python(
    tmp_path,
):
    source = generate_data_access_source(
        _sample_queries()
    )

    output_file = (
        tmp_path
        / "data_access.py"
    )

    output_file.write_text(
        source,
        encoding="utf-8",
    )

    spec = (
        importlib.util
        .spec_from_file_location(
            "generated_data_access",
            output_file,
        )
    )

    module = (
        importlib.util
        .module_from_spec(spec)
    )

    spec.loader.exec_module(module)

    assert hasattr(
        module,
        "insert_user_q001",
    )

    assert hasattr(
        module,
        "delete_user_q002",
    )

    assert hasattr(
        module,
        "update_user_q003",
    )

    assert hasattr(
        module,
        "select_users_q004",
    )
def test_windows_source_path_is_normalized():
    queries = _sample_queries()

    queries[0]["source_file"] = (
        r"..\..\benchmarks\apps\simple_crud"
        r"\legacy\create_user.php"
    )

    source = generate_data_access_source(
        queries
    )

    assert (
        "../../benchmarks/apps/simple_crud/"
        "legacy/create_user.php:9"
        in source
    )

    assert (
        r"..\..\benchmarks"
        not in source
    )