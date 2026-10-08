from c3.conversion.query_converter import _parameterize_sql

def test_like_wildcard_parameterization():
    sql = "SELECT id, name FROM users WHERE name LIKE '%{{q}}%'"

    result = _parameterize_sql(sql)

    assert result == "SELECT id, name FROM users WHERE name LIKE :q"
