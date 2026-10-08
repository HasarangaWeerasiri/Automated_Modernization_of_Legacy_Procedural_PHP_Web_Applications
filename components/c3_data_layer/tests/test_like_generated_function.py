from c3.conversion.query_converter import convert_query
from c3.generation.emitter import generate_data_access_source


def test_generated_like_function_binds_wildcards():
    query = {
        "query_id": "Q001",
        "source_file": "search.php",
        "source_line": 10,
        "status": "RECOVERED",
        "operation": "SELECT",
        "recovered_sql": (
            "SELECT id, name FROM users "
            "WHERE name LIKE '%{{q}}%'"
        ),
    }

    converted = convert_query(query)

    assert converted["status"] == "CONVERTED"
    assert converted["parameterized_sql"] == (
        "SELECT id, name FROM users WHERE name LIKE :q"
    )
    assert converted["like_wildcard_parameters"] == ["q"]

    source = generate_data_access_source([converted])

    assert '"q": f"%{q}%"' in source

    namespace = {}
    exec(source, namespace)

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, statement, params):
            self.sql = str(statement)
            self.params = params
            return FakeResult()

    session = FakeSession()

    namespace["select_users_q001"](session, "Alice")

    assert session.sql == (
        "SELECT id, name FROM users WHERE name LIKE :q"
    )
    assert session.params == {"q": "%Alice%"}
