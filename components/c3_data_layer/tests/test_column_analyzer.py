from c3.analysis.column_analyzer import analyze_sql_columns


def test_select_column_usage():
    result = analyze_sql_columns(
        query_id="Q004",
        sql="SELECT id, name, email, status FROM users",
        source_file="users.php",
        source_line=10,
    )

    assert result.status == "ANALYZED"
    assert result.operation == "SELECT"
    assert result.tables == ["users"]

    assert result.read_columns == [
        "id",
        "name",
        "email",
        "status",
    ]

    assert result.write_columns == []
    assert result.filter_columns == []

    assert result.source_file == "users.php"
    assert result.source_line == 10


def test_select_where_column_usage():
    result = analyze_sql_columns(
        query_id="Q005",
        sql=(
            "SELECT id, name, email "
            "FROM users "
            "WHERE status = 'active'"
        ),
    )

    assert result.operation == "SELECT"

    assert result.read_columns == [
        "id",
        "name",
        "email",
    ]

    assert result.filter_columns == ["status"]


def test_insert_column_usage():
    result = analyze_sql_columns(
        query_id="Q001",
        sql=(
            "INSERT INTO users "
            "(name, email, status) "
            "VALUES (:name, :email, 'active')"
        ),
        source_file="create_user.php",
        source_line=12,
    )

    assert result.status == "ANALYZED"
    assert result.operation == "INSERT"
    assert result.tables == ["users"]

    assert result.write_columns == [
        "name",
        "email",
        "status",
    ]

    assert result.read_columns == []
    assert result.filter_columns == []


def test_update_column_usage():
    result = analyze_sql_columns(
        query_id="Q003",
        sql=(
            "UPDATE users "
            "SET name = :name "
            "WHERE id = :id"
        ),
        source_file="update_user.php",
        source_line=15,
    )

    assert result.status == "ANALYZED"
    assert result.operation == "UPDATE"
    assert result.tables == ["users"]

    assert result.write_columns == ["name"]
    assert result.filter_columns == ["id"]


def test_delete_filter_column_usage():
    result = analyze_sql_columns(
        query_id="Q002",
        sql="DELETE FROM users WHERE id = :id",
        source_file="delete_user.php",
        source_line=18,
    )

    assert result.status == "ANALYZED"
    assert result.operation == "DELETE"
    assert result.tables == ["users"]

    assert result.read_columns == []
    assert result.write_columns == []
    assert result.filter_columns == ["id"]


def test_select_star_is_preserved():
    result = analyze_sql_columns(
        query_id="Q006",
        sql="SELECT * FROM users WHERE status = 'active'",
    )

    assert result.operation == "SELECT"
    assert result.read_columns == ["*"]
    assert result.filter_columns == ["status"]


def test_multiple_filter_columns_are_detected():
    result = analyze_sql_columns(
        query_id="Q007",
        sql=(
            "SELECT id, name "
            "FROM users "
            "WHERE status = 'active' "
            "AND email = :email"
        ),
    )

    assert result.read_columns == [
        "id",
        "name",
    ]

    assert set(result.filter_columns) == {
        "status",
        "email",
    }


def test_source_traceability_is_preserved():
    result = analyze_sql_columns(
        query_id="Q008",
        sql="SELECT id FROM users",
        source_file="legacy/admin/users.php",
        source_line=47,
    )

    output = result.to_dict()

    assert output["query_id"] == "Q008"
    assert output["source_file"] == "legacy/admin/users.php"
    assert output["source_line"] == 47
    assert output["status"] == "ANALYZED"


def test_invalid_sql_abstains_instead_of_guessing():
    result = analyze_sql_columns(
        query_id="Q009",
        sql="SELECT FROM WHERE ???",
        source_file="dynamic.php",
        source_line=22,
    )

    assert result.status == "UNRESOLVED"
    assert result.operation == "UNKNOWN"

    assert result.read_columns == []
    assert result.write_columns == []
    assert result.filter_columns == []

    assert result.reason is not None

def test_recovered_query_record_is_analyzed():
    from c3.analysis.column_analyzer import analyze_recovered_query

    query = {
        "query_id": "Q003",
        "source_file": (
            "..\\..\\benchmarks\\apps\\simple_crud\\"
            "legacy\\update_user.php"
        ),
        "source_line": 8,
        "executor": "mysqli_query",
        "sql_argument": "$sql",
        "recovered_sql": (
            "UPDATE users "
            "SET name = '{{name}}' "
            "WHERE id = {{id}}"
        ),
        "operation": "UPDATE",
        "dynamic_variables": [
            "name",
            "id",
        ],
        "status": "RECOVERED",
        "reason": None,
    }

    result = analyze_recovered_query(query)

    assert result.status == "ANALYZED"
    assert result.query_id == "Q003"
    assert result.operation == "UPDATE"
    assert result.tables == ["users"]
    assert result.write_columns == ["name"]
    assert result.filter_columns == ["id"]

    assert result.source_file.endswith(
        "legacy\\update_user.php"
    )

    assert result.source_line == 8


def test_unresolved_recovered_query_abstains():
    from c3.analysis.column_analyzer import analyze_recovered_query

    query = {
        "query_id": "Q006",
        "source_file": (
            "..\\..\\benchmarks\\apps\\mixed_crud\\"
            "legacy\\dynamic.php"
        ),
        "source_line": 20,
        "executor": "mysqli_query",
        "sql_argument": "$sql",
        "recovered_sql": (
            "{{UNRESOLVED:buildQuery($table,$condition)}}"
        ),
        "operation": "UNKNOWN",
        "dynamic_variables": [],
        "status": "UNRESOLVED",
        "reason": "Dynamic function call could not be resolved",
    }

    result = analyze_recovered_query(query)

    assert result.status == "UNRESOLVED"
    assert result.query_id == "Q006"
    assert result.tables == []
    assert result.read_columns == []
    assert result.write_columns == []
    assert result.filter_columns == []
    assert result.reason is not None