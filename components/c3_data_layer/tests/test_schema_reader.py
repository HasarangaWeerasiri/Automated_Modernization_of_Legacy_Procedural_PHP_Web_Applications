from pathlib import Path

from c3.mapping.schema_reader import read_schema


PROJECT_ROOT = Path(__file__).resolve().parents[3]

SCHEMA_FILE = (
    PROJECT_ROOT
    / "benchmarks"
    / "apps"
    / "simple_crud"
    / "schema.sql"
)


def get_table(schema, table_name):
    return next(
        table
        for table in schema.tables
        if table.name == table_name
    )


def get_column(table, column_name):
    return next(
        column
        for column in table.columns
        if column.name == column_name
    )


def test_schema_contains_expected_tables():
    schema = read_schema(SCHEMA_FILE)

    table_names = [
        table.name
        for table in schema.tables
    ]

    assert "users" in table_names
    assert "appointments" in table_names


def test_users_table_columns():
    schema = read_schema(SCHEMA_FILE)

    users = get_table(schema, "users")

    column_names = [
        column.name
        for column in users.columns
    ]

    assert column_names == [
        "id",
        "name",
        "email",
        "status",
    ]


def test_users_primary_key():
    schema = read_schema(SCHEMA_FILE)

    users = get_table(schema, "users")
    id_column = get_column(users, "id")

    assert id_column.data_type == "INT"
    assert id_column.primary_key is True
    assert id_column.auto_increment is True
    assert id_column.nullable is False


def test_not_null_columns():
    schema = read_schema(SCHEMA_FILE)

    appointments = get_table(
        schema,
        "appointments",
    )

    user_id = get_column(
        appointments,
        "user_id",
    )

    appointment_date = get_column(
        appointments,
        "appointment_date",
    )

    assert user_id.data_type == "INT"
    assert user_id.nullable is False

    assert appointment_date.data_type == "DATE"
    assert appointment_date.nullable is False


def test_nullable_column():
    schema = read_schema(SCHEMA_FILE)

    appointments = get_table(
        schema,
        "appointments",
    )

    status = get_column(
        appointments,
        "status",
    )

    assert status.data_type == "VARCHAR(20)"
    assert status.nullable is True


def test_foreign_key_relationship():
    schema = read_schema(SCHEMA_FILE)

    appointments = get_table(
        schema,
        "appointments",
    )

    assert len(appointments.foreign_keys) == 1

    foreign_key = appointments.foreign_keys[0]

    assert foreign_key.column == "user_id"
    assert foreign_key.referenced_table == "users"
    assert foreign_key.referenced_column == "id"