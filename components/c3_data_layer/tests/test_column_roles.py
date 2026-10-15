"""S11: column roles, table attribution, SELECT * expansion and template queries.

Fixtures are written for this test only (they reproduce the shapes listed
in the audit, not benchmark files).
"""

from c3.analysis.column_analyzer import analyze_converted_template, analyze_sql_columns

SCHEMA = {"tables": [
    {"name": "users", "columns": [{"name": "id"}, {"name": "name"}, {"name": "status"}, {"name": "team_id"}]},
    {"name": "teams", "columns": [{"name": "id"}, {"name": "name"}]},
    {"name": "accounts", "columns": [{"name": "team_id"}, {"name": "balance"}]},
    {"name": "archive", "columns": [{"name": "id"}, {"name": "name"}]},
]}


def usage(sql, schema=SCHEMA):
    return analyze_sql_columns(query_id="T", sql=sql, schema=schema)


def test_join_order_by_and_table_attribution():
    u = usage("SELECT u.name AS n, t.name FROM users u JOIN teams t ON t.id = u.team_id "
              "WHERE u.status = 'a' ORDER BY t.name")

    assert u.qualified_columns["read"] == ["users.name", "teams.name"]
    assert u.join_columns == ["id", "team_id"]
    assert u.order_by_columns == ["name"]
    assert u.qualified_columns["order_by"] == ["teams.name"]
    assert set(u.filter_columns) == {"status", "id", "team_id"}  # WHERE + JOIN ON


def test_group_by_having_and_select_alias():
    u = usage("SELECT team_id, SUM(balance) AS total FROM accounts GROUP BY team_id HAVING total > 5")

    assert u.group_by_columns == ["team_id"]
    assert u.having_columns == ["balance"]  # alias resolved to its column
    assert "balance" in u.filter_columns


def test_select_star_is_expanded_from_the_schema_only():
    u = usage("SELECT * FROM users")

    assert u.read_columns == ["*"]
    assert u.expanded_read_columns == ["id", "name", "status", "team_id"]
    assert usage("SELECT * FROM unknown_table").expanded_read_columns is None
    assert usage("SELECT t.* FROM users u JOIN teams t ON t.id = u.team_id").expanded_read_columns == ["id", "name"]


def test_update_reads_assignment_values():
    increment = usage("UPDATE accounts SET balance = balance + :delta WHERE team_id = :id")
    join_update = usage("UPDATE users u JOIN teams t ON t.id = u.team_id SET u.name = t.name WHERE u.id = :id")

    assert increment.read_columns == ["balance"] and increment.write_columns == ["balance"]
    assert join_update.qualified_columns["read"] == ["teams.name"]
    assert join_update.qualified_columns["write"] == ["users.name"]


def test_insert_select_attributes_writes_to_the_target():
    u = usage("INSERT INTO archive (id, name) SELECT id, name FROM users WHERE status = 'old'")

    assert u.qualified_columns["write"] == ["archive.id", "archive.name"]
    assert u.qualified_columns["read"] == ["users.id", "users.name"]
    assert u.filter_columns == ["status"]


def test_ambiguous_column_is_not_guessed():
    u = usage("SELECT name FROM users u JOIN teams t ON t.id = u.team_id")

    assert u.qualified_columns["read"] == ["?.name"]


def test_template_conversions_get_column_use():
    query = {"query_id": "T1", "source_file": "x.php", "source_line": 1}

    in_list = analyze_converted_template(query, {"query_shape": "EXPANDING_IN_SELECT",
                                                 "parameterized_sql": "SELECT id, name FROM users WHERE id IN :ids"}, SCHEMA)
    order = analyze_converted_template(query, {"query_shape": "ORDER_BY_SELECT", "base_sql": "SELECT id FROM users",
                                               "allowed_columns": ["id", "name"]}, SCHEMA)
    table = analyze_converted_template(query, {"query_shape": "TABLE_IDENTIFIER_SELECT", "input_key": "t",
                                               "sql_template": "SELECT id FROM {table} WHERE id = 1",
                                               "allowed_tables": ["users", "teams"]}, SCHEMA)

    assert in_list.filter_columns == ["id"] and in_list.analysis_source == "converted_template"
    assert order.dynamic_identifiers == [{"role": "order_by", "input_key": "sort", "allowed": ["id", "name"]}]
    assert table.tables == ["users", "teams"] and table.qualified_columns["read"] == ["?.id"]
    assert analyze_converted_template(query, {"query_shape": "UNKNOWN"}, SCHEMA) is None
