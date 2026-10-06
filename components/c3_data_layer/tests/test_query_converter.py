from c3.conversion.query_converter import (
    _parameterize_sql,
    convert_query,
    convert_queries,
)


def test_unquoted_placeholder_is_parameterized():
    sql = (
        "DELETE FROM users "
        "WHERE id = {{id}}"
    )

    result = _parameterize_sql(sql)

    assert result == (
        "DELETE FROM users "
        "WHERE id = :id"
    )


def test_quoted_placeholder_loses_legacy_quotes():
    sql = (
        "UPDATE users "
        "SET name = '{{name}}' "
        "WHERE id = {{id}}"
    )

    result = _parameterize_sql(sql)

    assert result == (
        "UPDATE users "
        "SET name = :name "
        "WHERE id = :id"
    )


def test_static_literal_remains_quoted():
    sql = (
        "INSERT INTO users "
        "(name, status) "
        "VALUES ('{{name}}', 'active')"
    )

    result = _parameterize_sql(sql)

    assert result == (
        "INSERT INTO users "
        "(name, status) "
        "VALUES (:name, 'active')"
    )


def test_insert_is_converted():
    query = {
        "query_id": "Q001",
        "source_file": "create_user.php",
        "source_line": 10,
        "operation": "INSERT",
        "status": "RECOVERED",
        "recovered_sql": (
            "INSERT INTO users "
            "(name, email, status) "
            "VALUES "
            "('{{name}}', '{{email}}', 'active')"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert result["operation"] == "INSERT"
    assert result["table"] == "users"

    assert result["parameters"] == [
        "name",
        "email",
    ]

    assert result["parameterized_sql"] == (
        "INSERT INTO users "
        "(name, email, status) "
        "VALUES (:name, :email, 'active')"
    )


def test_update_is_converted():
    query = {
        "query_id": "Q002",
        "source_file": "update_user.php",
        "source_line": 10,
        "operation": "UPDATE",
        "status": "RECOVERED",
        "recovered_sql": (
            "UPDATE users "
            "SET name = '{{name}}' "
            "WHERE id = {{id}}"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"

    assert result["parameters"] == [
        "name",
        "id",
    ]

    assert result["parameterized_sql"] == (
        "UPDATE users "
        "SET name = :name "
        "WHERE id = :id"
    )


def test_delete_is_converted():
    query = {
        "query_id": "Q003",
        "source_file": "delete_user.php",
        "source_line": 10,
        "operation": "DELETE",
        "status": "RECOVERED",
        "recovered_sql": (
            "DELETE FROM users "
            "WHERE id = {{id}}"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert result["table"] == "users"
    assert result["parameters"] == ["id"]

    assert result["parameterized_sql"] == (
        "DELETE FROM users "
        "WHERE id = :id"
    )


def test_select_without_parameters_is_converted():
    query = {
        "query_id": "Q004",
        "source_file": "users.php",
        "source_line": 4,
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id, name, email, status "
            "FROM users"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert result["operation"] == "SELECT"
    assert result["parameters"] == []


def test_unresolved_query_remains_unresolved():
    query = {
        "query_id": "Q005",
        "source_file": "dynamic.php",
        "source_line": 8,
        "operation": None,
        "status": "UNRESOLVED",
        "reason": (
            "Query contains unsupported "
            "dynamic expression."
        ),
    }

    result = convert_query(query)

    assert result["status"] == "UNRESOLVED"

    assert (
        "unsupported dynamic expression"
        in result["reason"].lower()
    )


def test_multiple_queries_are_converted():
    queries = [
        {
            "query_id": "Q001",
            "operation": "SELECT",
            "status": "RECOVERED",
            "recovered_sql": (
                "SELECT id FROM users"
            ),
        },
        {
            "query_id": "Q002",
            "operation": "DELETE",
            "status": "RECOVERED",
            "recovered_sql": (
                "DELETE FROM users "
                "WHERE id = {{id}}"
            ),
        },
    ]

    result = convert_queries(queries)

    assert len(result) == 2

    assert (
        result[0]["status"]
        == "CONVERTED"
    )

    assert (
        result[1]["status"]
        == "CONVERTED"
    )