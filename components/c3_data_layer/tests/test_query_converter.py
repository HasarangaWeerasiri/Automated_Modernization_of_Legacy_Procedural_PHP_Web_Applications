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

def test_static_inner_join_is_converted():
    query = {
        "query_id": "Q008",
        "source_file": "08_join_appointments.php",
        "source_line": 7,
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT a.id, p.full_name, "
            "d.full_name AS doctor_name "
            "FROM appointments a "
            "JOIN patients p ON p.id = a.patient_id "
            "JOIN doctors d ON d.id = a.doctor_id "
            "WHERE p.id = {{patient}}"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "CONVERTED"
    assert result["operation"] == "SELECT"
    assert result["table"] == "appointments"
    assert result["parameters"] == ["patient"]
    assert "JOIN patients p" in result["parameterized_sql"]
    assert "JOIN doctors d" in result["parameterized_sql"]
    assert "WHERE p.id = :patient" in result["parameterized_sql"]


def test_subquery_remains_unresolved():
    query = {
        "query_id": "Q010",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id FROM patients "
            "WHERE id IN "
            "(SELECT patient_id FROM appointments)"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "UNRESOLVED"


def test_multi_table_update_remains_unresolved():
    query = {
        "query_id": "Q020",
        "operation": "UPDATE",
        "status": "RECOVERED",
        "recovered_sql": (
            "UPDATE patients p "
            "JOIN appointments a ON a.patient_id = p.id "
            "SET p.phone = '0700000000'"
        ),
    }

    result = convert_query(query)

    assert result["status"] == "UNRESOLVED"

def test_dynamic_select_column_is_unresolved():
    result = convert_query({
        "query_id": "dynamic_column",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT {{column}} FROM patients"
        ),
    })

    assert result["status"] == "UNRESOLVED"


def test_dynamic_where_column_is_unresolved():
    result = convert_query({
        "query_id": "dynamic_condition_column",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id FROM patients "
            "WHERE {{column}} = 1"
        ),
    })

    assert result["status"] == "UNRESOLVED"


def test_dynamic_order_direction_is_unresolved():
    result = convert_query({
        "query_id": "dynamic_order_direction",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id FROM patients "
            "ORDER BY id {{direction}}"
        ),
    })

    assert result["status"] == "UNRESOLVED"

def test_dynamic_condition_is_unresolved():
    result = convert_query({
        "query_id": "dynamic_condition",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT id FROM patients WHERE {{condition}}"
        ),
    })

    assert result["status"] == "UNRESOLVED"


def test_dynamic_group_by_column_is_unresolved():
    result = convert_query({
        "query_id": "dynamic_group_by",
        "operation": "SELECT",
        "status": "RECOVERED",
        "recovered_sql": (
            "SELECT COUNT(*) FROM patients "
            "GROUP BY {{column}}"
        ),
    })

    assert result["status"] == "UNRESOLVED"

def test_arithmetic_update_is_converted():
    result = convert_query({
        "query_id": "Q017",
        "operation": "UPDATE",
        "status": "RECOVERED",
        "recovered_sql": (
            "UPDATE appointments "
            "SET fee = fee + {{delta}} "
            "WHERE id = {{id}}"
        ),
    })

    assert result["status"] == "CONVERTED"
    assert result["parameterized_sql"] == (
        "UPDATE appointments "
        "SET fee = fee + :delta "
        "WHERE id = :id"
    )
    assert set(result["parameters"]) == {"delta", "id"}


def test_structural_placeholder_safety_cases():
    cases = [
        (
            "safe_update_value",
            "UPDATE patients SET phone = {{phone}} WHERE id = {{id}}",
            "CONVERTED",
        ),
        (
            "unsafe_update_column",
            "UPDATE patients SET {{column}} = '123' WHERE id = 1",
            "UNRESOLVED",
        ),
        (
            "unsafe_where_expression",
            "SELECT id FROM patients WHERE {{condition}}",
            "UNRESOLVED",
        ),
        (
            "unsafe_join_condition",
            "SELECT p.id FROM patients p "
            "INNER JOIN doctors d ON {{condition}}",
            "UNRESOLVED",
        ),
        (
            "safe_join_value",
            "SELECT p.id FROM patients p "
            "INNER JOIN doctors d ON d.id = p.id "
            "WHERE p.id = {{id}}",
            "CONVERTED",
        ),
    ]

    for query_id, sql, expected_status in cases:
        result = convert_query({
            "query_id": query_id,
            "operation": sql.split()[0],
            "status": "RECOVERED",
            "recovered_sql": sql,
        })

        assert result["status"] == expected_status, (
            f"{query_id}: {result.get('reason')}"
        )
