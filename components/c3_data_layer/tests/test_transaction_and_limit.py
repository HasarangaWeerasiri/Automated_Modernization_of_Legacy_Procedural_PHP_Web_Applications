"""S8: transaction policy of generated writes and LIMIT/OFFSET coercion (SQLite)."""

import importlib.util

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from c3.conversion.limit_binding import to_row_count
from c3.pipeline import run_pipeline

SCHEMA = """
CREATE TABLE items (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(40) NOT NULL
);
"""

PAGES = {
    "add.php": '<?php\n$name = $_POST[\'name\'];\nmysqli_query($conn, "INSERT INTO items (name) VALUES (\'$name\')");\n',
    "rename.php": '<?php\n$id = $_POST[\'id\'];\n$newname = $_POST[\'name\'];\n'
                  'mysqli_query($conn, "UPDATE items SET name = \'$newname\' WHERE id = \'$id\'");\n',
    "page.php": '<?php\n$limit = $_GET[\'limit\'];\n$offset = $_GET[\'offset\'];\n'
                'mysqli_query($conn, "SELECT id, name FROM items ORDER BY id LIMIT $limit OFFSET $offset");\n',
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("s8")
    php = root / "php"
    php.mkdir()
    for name, source in PAGES.items():
        (php / name).write_text(source, encoding="utf-8")
    (root / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    run_pipeline(php, root / "schema.sql", root / "out")

    spec = importlib.util.spec_from_file_location("s8_data_access", root / "out" / "data_access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    functions = {name.split("_")[0]: getattr(module, name) for name in dir(module) if name[-4:-3] == "q" and name[-3:].isdigit()}
    return functions


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's8.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(40) NOT NULL)"))
        connection.execute(text("INSERT INTO items (name) VALUES ('a'), ('b'), ('c')"))
    yield engine
    engine.dispose()


def count(engine):
    with engine.connect() as connection:
        return connection.execute(text("SELECT COUNT(*) FROM items")).scalar_one()


def test_all_three_sites_are_generated(generated):
    assert set(generated) == {"insert", "update", "select"}


def test_write_commits_by_default(generated, engine):
    with Session(engine) as session:
        generated["insert"](session, name="d")

    assert count(engine) == 4


def test_commit_false_leaves_the_transaction_to_the_caller(generated, engine):
    with Session(engine) as session:
        generated["insert"](session, name="d", commit=False)
        generated["update"](session, id="1", newname="z", commit=False)
        session.rollback()

    assert count(engine) == 3
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM items WHERE id = 1")).scalar_one() == "a"

    with Session(engine) as session:
        generated["insert"](session, name="d", commit=False)
        generated["update"](session, id="1", newname="z", commit=False)
        session.commit()

    assert count(engine) == 4


def test_limit_offset_accept_integer_strings(generated, engine):
    with Session(engine) as session:
        rows = generated["select"](session, limit="2", offset="1")
        assert [row["name"] for row in rows] == ["b", "c"]
        assert len(generated["select"](session, limit=1, offset=0)) == 1


@pytest.mark.parametrize("bad", ["abc", "-1", "1; DROP TABLE items", "1.5", "", True, None])
def test_limit_offset_reject_non_integers(generated, engine, bad):
    with Session(engine) as session:
        with pytest.raises(ValueError):
            generated["select"](session, limit=bad, offset="0")

    assert count(engine) == 3


def test_to_row_count_bounds():
    assert to_row_count(" 10 ") == 10
    assert to_row_count(18446744073709551615) == 18446744073709551615
    with pytest.raises(ValueError):
        to_row_count(18446744073709551616)
