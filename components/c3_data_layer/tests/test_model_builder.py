import importlib.util
from pathlib import Path

from c3.conversion.model_builder import (
    generate_models,
    load_schema,
    table_to_class_name,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]

SCHEMA_FILE = (
    PROJECT_ROOT
    / "output"
    / "simple_crud"
    / "schema.json"
)


def load_generated_module(model_file: Path):
    """
    Dynamically import a generated models.py file.
    """

    spec = importlib.util.spec_from_file_location(
        "generated_test_models",
        model_file,
    )

    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    return module


def test_table_name_to_class_name():
    assert table_to_class_name("users") == "User"

    assert (
        table_to_class_name("appointments")
        == "Appointment"
    )

    assert (
        table_to_class_name("user_accounts")
        == "UserAccount"
    )


def test_schema_can_be_loaded():
    schema = load_schema(SCHEMA_FILE)

    table_names = [
        table["name"]
        for table in schema["tables"]
    ]

    assert table_names == [
        "users",
        "appointments",
    ]


def test_models_are_generated(tmp_path):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    assert output_file.exists()

    source = output_file.read_text(
        encoding="utf-8"
    )

    assert "class User(Base):" in source
    assert "class Appointment(Base):" in source


def test_generated_models_can_be_imported(
    tmp_path,
):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    assert set(
        module.Base.metadata.tables.keys()
    ) == {
        "users",
        "appointments",
    }


def test_user_schema_fidelity(tmp_path):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    users = (
        module.Base.metadata.tables["users"]
    )

    assert users.c.id.primary_key is True
    assert users.c.id.nullable is False

    assert str(users.c.name.type) == "VARCHAR(100)"
    assert users.c.name.nullable is False

    assert str(users.c.email.type) == "VARCHAR(150)"
    assert users.c.email.nullable is False

    assert str(users.c.status.type) == "VARCHAR(20)"
    assert users.c.status.nullable is False


def test_appointment_schema_fidelity(
    tmp_path,
):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    appointments = (
        module.Base.metadata.tables[
            "appointments"
        ]
    )

    assert appointments.c.id.primary_key is True
    assert appointments.c.id.nullable is False

    assert str(
        appointments.c.user_id.type
    ) == "INTEGER"

    assert (
        appointments.c.user_id.nullable
        is False
    )

    assert str(
        appointments.c.appointment_date.type
    ) == "DATE"

    assert (
        appointments.c.appointment_date.nullable
        is False
    )

    assert str(
        appointments.c.status.type
    ) == "VARCHAR(20)"

    assert appointments.c.status.nullable is True


def test_foreign_key_is_preserved(
    tmp_path,
):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    user_id = (
        module.Base
        .metadata
        .tables["appointments"]
        .c
        .user_id
    )

    targets = [
        foreign_key.target_fullname
        for foreign_key
        in user_id.foreign_keys
    ]

    assert targets == ["users.id"]

def test_parent_relationship_is_generated(
    tmp_path,
):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    relationship_names = list(
        module.User.__mapper__.relationships.keys()
    )

    assert "appointments" in relationship_names

    relationship = (
        module.User
        .__mapper__
        .relationships["appointments"]
    )

    assert (
        relationship.mapper.class_.__name__
        == "Appointment"
    )


def test_child_relationship_is_generated(
    tmp_path,
):
    output_file = tmp_path / "models.py"

    generate_models(
        SCHEMA_FILE,
        output_file,
    )

    module = load_generated_module(
        output_file
    )

    relationship_names = list(
        module.Appointment
        .__mapper__
        .relationships.keys()
    )

    assert "user" in relationship_names

    relationship = (
        module.Appointment
        .__mapper__
        .relationships["user"]
    )

    assert (
        relationship.mapper.class_.__name__
        == "User"
    )

    assert (
        relationship.back_populates
        == "appointments"
    )