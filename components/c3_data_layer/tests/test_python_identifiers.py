"""Names from the legacy app that are not usable Python identifiers (keywords, spaces,
hyphens, leading digits, names the generated code uses itself) must still give valid,
correct generated code. Project-authored fixtures only."""

import importlib.util
import shutil
import sys

import pytest

from c3.conversion.model_builder import generate_models_source
from c3.generation.identifiers import DATA_ACCESS_RESERVED, assign_names, python_name
from c3.pipeline import GeneratedCodeError, check_generated_python, run_pipeline

needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="PHP executable not on PATH")

SCHEMA = """
CREATE TABLE students (
  id INT NOT NULL AUTO_INCREMENT,
  name VARCHAR(50) NOT NULL,
  `class` VARCHAR(20) NOT NULL,
  `pass` VARCHAR(20) NULL,
  `class_` VARCHAR(20) NULL,
  `statement` VARCHAR(20) NULL,
  PRIMARY KEY (id)
);
"""


class RecordingSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))

        class Result:
            def mappings(self):
                return self

            def all(self):
                return []
        return Result()

    def commit(self):
        pass


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- the naming helper ----------

@pytest.mark.parametrize("name,expected", [
    ("class", "class_"), ("from", "from_"), ("first name", "first_name"), ("e-mail", "e_mail"), ("2fa", "_2fa"),
    ("None", "None_"), ("user_id", "user_id"), ("Name", "Name"),
])
def test_python_name(name, expected):
    assert python_name(name) == expected


def test_valid_names_never_change_and_renames_never_collide():
    names = assign_names(["class", "class_", "statement", "session", "name", "2fa", "_2fa"], DATA_ACCESS_RESERVED)
    assert names["class_"] == "class_" and names["name"] == "name" and names["_2fa"] == "_2fa"
    assert names["class"] == "class__"                       # class_ is taken by a real name
    assert names["statement"] == "statement_" and names["session"] == "session_"
    assert names["2fa"] == "_2fa_"
    assert len(set(names.values())) == len(names)


# ---------- models: keyword, space, hyphen, leading digit, reserved attribute ----------

def test_models_with_unusual_column_names_compile_and_keep_database_names(tmp_path):
    schema = {"tables": [{
        "name": "students", "primary_key": ["id"],
        "columns": [{"name": n, "data_type": "VARCHAR(20)", "nullable": n != "id", "primary_key": n == "id"}
                    for n in ["id", "class", "from", "first name", "e-mail", "2fa", "metadata", "class_"]],
        "foreign_keys": [],
    }]}
    source = generate_models_source(schema)
    path = tmp_path / "models.py"
    path.write_text(source, encoding="utf-8")
    check_generated_python(path, "test")
    assert '    class__ = mapped_column("class", ' in source       # class_ is a real column, so class gets class__
    assert '    class_ = mapped_column(' in source and 'mapped_column("class_"' not in source
    assert '    first_name = mapped_column("first name", ' in source
    assert '    e_mail = mapped_column("e-mail", ' in source
    assert '    _2fa = mapped_column("2fa", ' in source
    assert '    metadata_ = mapped_column("metadata", ' in source
    models = load(path, "models_unusual")
    from sqlalchemy.orm import configure_mappers
    configure_mappers()
    columns = {c.name for c in models.Student.__table__.columns}
    assert columns == {"id", "class", "from", "first name", "e-mail", "2fa", "metadata", "class_"}
    assert models.Student.__mapper__.attrs["class__"].columns[0].name == "class"


def test_keyword_table_names_give_valid_class_and_core_table_names(tmp_path):
    schema = {"tables": [
        {"name": "none", "primary_key": ["id"], "foreign_keys": [],
         "columns": [{"name": "id", "data_type": "INT", "nullable": False, "primary_key": True}]},
        {"name": "class", "primary_key": [], "foreign_keys": [],
         "columns": [{"name": "x", "data_type": "INT", "nullable": True}]},
    ]}
    path = tmp_path / "models.py"
    path.write_text(generate_models_source(schema), encoding="utf-8")
    check_generated_python(path, "test")
    source = path.read_text(encoding="utf-8")
    assert "class None_(Base):" in source and "class_table = Table(" in source


def test_relationship_named_after_keyword_fk_column_is_valid(tmp_path):
    schema = {"tables": [
        {"name": "classes", "primary_key": ["id"], "foreign_keys": [],
         "columns": [{"name": "id", "data_type": "INT", "nullable": False, "primary_key": True}]},
        {"name": "pupils", "primary_key": ["id"],
         "columns": [{"name": "id", "data_type": "INT", "nullable": False, "primary_key": True},
                     {"name": "class_id", "data_type": "INT", "nullable": True}],
         "foreign_keys": [{"column": "class_id", "referenced_table": "classes", "referenced_column": "id"}]},
    ]}
    path = tmp_path / "models.py"
    path.write_text(generate_models_source(schema), encoding="utf-8")
    check_generated_python(path, "test")
    assert "    class_ = relationship(" in path.read_text(encoding="utf-8")


# ---------- data access: $class, another keyword, $statement, duplicates after renaming ----------

@pytest.fixture
def app(tmp_path):
    php = tmp_path / "php"
    php.mkdir()
    (php / "add.php").write_text(
        "<?php\n$name = $_POST['name'];\n$class = $_POST['class'];\n$pass = $_POST['pass'];\n"
        "mysqli_query($conn, \"INSERT INTO students (name, `class`, `pass`) VALUES ('$name', '$class', '$pass')\");\n",
        encoding="utf-8")
    (php / "find.php").write_text(
        "<?php\n$statement = $_GET['s'];\n"
        "mysqli_query($conn, \"SELECT id FROM students WHERE `statement` = '$statement'\");\n", encoding="utf-8")
    (php / "pair.php").write_text(
        "<?php\n$class = $_GET['a'];\n$class_ = $_GET['b'];\n"
        "mysqli_query($conn, \"SELECT id FROM students WHERE `class` = '$class' AND `class_` = '$class_'\");\n", encoding="utf-8")
    (tmp_path / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    out = tmp_path / "out"
    run_pipeline(php, tmp_path / "schema.sql", out)
    return out


def _function(module, prefix):
    return next(getattr(module, n) for n in dir(module) if n.startswith(prefix))


@needs_php
def test_keyword_parameters_compile_and_bind_under_the_original_key(app):
    source = (app / "data_access.py").read_text(encoding="utf-8")
    assert "def insert_student_q" in source and ", class_, pass_, *, commit" in source
    assert '"class": class_,' in source and '"pass": pass_,' in source
    assert "class_: $class" in source                         # docstring names the Python argument and the PHP value
    module = load(app / "data_access.py", "da_keywords")
    session = RecordingSession()
    _function(module, "insert_student_q")(session, "Ann", "7B", "Kandy", commit=False)
    statement, params = session.calls[0]
    assert params == {"name": "Ann", "class": "7B", "pass": "Kandy"}
    assert ":class" in str(statement) and ":pass" in str(statement)


@needs_php
def test_statement_variable_binds_the_users_value_not_the_sql_object(app):
    source = (app / "data_access.py").read_text(encoding="utf-8")
    assert "(session, statement_)" in source and '"statement": statement_,' in source
    module = load(app / "data_access.py", "da_statement")
    session = RecordingSession()
    finder = next(getattr(module, n) for n in dir(module) if n.startswith("select_students_q")
                  and "statement_" in getattr(module, n).__code__.co_varnames[:getattr(module, n).__code__.co_argcount])
    finder(session, "users value")
    statement, params = session.calls[0]
    assert params == {"statement": "users value"}
    assert isinstance(params["statement"], str)
    assert "WHERE `statement` = :statement" in str(statement)


@needs_php
def test_duplicate_parameter_after_renaming_is_kept_distinct(app):
    source = (app / "data_access.py").read_text(encoding="utf-8")
    assert "(session, class__, class_)" in source
    assert '"class": class__,' in source and '"class_": class_,' in source
    module = load(app / "data_access.py", "da_pair")
    pair = next(getattr(module, n) for n in dir(module) if n.startswith("select_students_q")
                and "class__" in getattr(module, n).__code__.co_varnames)
    session = RecordingSession()
    pair(session, "A", "B")
    assert session.calls[0][1] == {"class": "A", "class_": "B"}


@needs_php
def test_generated_models_for_the_fixture_compile(app):
    source = (app / "models.py").read_text(encoding="utf-8")
    assert 'class__ = mapped_column("class", ' in source and 'pass_ = mapped_column("pass", ' in source
    assert 'statement = mapped_column(' in source              # only data-access arguments reserve "statement"


# ---------- the compile check ----------

def test_compile_check_names_file_line_and_text(tmp_path):
    bad = tmp_path / "data_access.py"
    bad.write_text("x = 1\ndef f(session, class, *, commit: bool = True):\n    pass\n", encoding="utf-8")
    with pytest.raises(GeneratedCodeError) as caught:
        check_generated_python(bad, "stage 4/7 (data-access functions)")
    message = str(caught.value)
    assert message.startswith("stage 4/7 (data-access functions): generated data_access.py is not valid Python at line 2")
    assert "def f(session, class, *, commit: bool = True):" in message
    assert not list(tmp_path.glob("__pycache__"))


@needs_php
def test_pipeline_stops_at_the_stage_that_wrote_bad_code(tmp_path, monkeypatch, capsys):
    import c3.pipeline as pipeline

    def broken(converted_file, data_access_file, source_root=None):
        data_access_file.write_text("def broken(:\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "generate_data_access", broken)
    php = tmp_path / "php"
    php.mkdir()
    (php / "a.php").write_text("<?php mysqli_query($c, \"SELECT id FROM students\");\n", encoding="utf-8")
    (tmp_path / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    with pytest.raises(GeneratedCodeError, match=r"^stage 4/7 \(data-access functions\): generated data_access.py .* line 1"):
        pipeline.run_pipeline(php, tmp_path / "schema.sql", tmp_path / "out")
    assert "[5/7]" not in capsys.readouterr().out
