"""Reset, snapshot and delta against the running databases of both systems.

These tests name no table or column of any particular benchmark: they work on
whatever application the environment is currently running.
"""

import pytest
from sqlalchemy import MetaData, and_, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from c4 import dbstate

pytestmark = pytest.mark.environment


@pytest.fixture(params=["legacy", "migrated"])
def system(request, at_baseline):
    return getattr(at_baseline, request.param)


@pytest.fixture
def baseline(system, baselines):
    return baselines[system.name]


@pytest.fixture
def backend(system):
    return make_url(system.db_url).get_backend_name()


@pytest.fixture
def db(system):
    engine = create_engine(system.db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    yield engine, metadata
    engine.dispose()


@pytest.fixture
def reloads(monkeypatch, backend):
    """Record each time the seed dump is actually reloaded."""
    calls = []
    original = dbstate._RELOADERS[backend]

    def counting(target):
        calls.append(target.name)
        original(target)

    monkeypatch.setitem(dbstate._RELOADERS, backend, counting)
    return calls


def _first_table(metadata, *, keyed):
    for name in sorted(metadata.tables):
        table = metadata.tables[name]
        if bool(table.primary_key.columns) == keyed:
            return table
    pytest.skip(f"this benchmark has no table {'with' if keyed else 'without'} a primary key")


def _raw_row(connection, table, plain_row):
    """The database's own values for a row known by its snapshot form."""
    for row in connection.execute(select(table)).mappings():
        if {name: dbstate._plain(value) for name, value in row.items()} == plain_row:
            return dict(row)
    raise AssertionError("snapshot row not found in the table")


def _is_row(table, raw_row):
    return and_(*(
        column.is_(None) if raw_row[column.name] is None else column == raw_row[column.name]
        for column in table.columns
    ))


def _empty_every_table(engine, metadata):
    with engine.begin() as connection:
        for table in reversed(metadata.sorted_tables):
            connection.execute(table.delete())


# ----------------------------------------------------------------------- snapshot


def test_snapshot_holds_every_row_of_every_table(baseline, db):
    engine, metadata = db

    assert set(baseline.tables) == set(metadata.tables)
    assert sum(len(table.rows) for table in baseline.tables.values()) > 0
    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            count = connection.execute(select(func.count()).select_from(table)).scalar()
            assert len(baseline.tables[name].rows) == count, name


def test_snapshot_is_repeatable(system, baseline):
    assert dbstate.snapshot(system).state_fingerprint == baseline.state_fingerprint


def test_snapshot_values_are_json_types(baseline):
    for table in baseline.tables.values():
        for row in table.rows:
            for value in row.values():
                assert value is None or type(value) in (bool, int, float, str)


# -------------------------------------------------------------------------- delta


def test_deleting_a_keyed_row_is_reported_exactly(system, baseline, db):
    engine, metadata = db
    table = _first_table(metadata, keyed=True)
    victim = baseline.tables[table.name].rows[0]

    with engine.begin() as connection:
        connection.execute(table.delete().where(
            *(column == victim[column.name] for column in table.primary_key.columns)
        ))

    assert dbstate.delta(baseline, dbstate.snapshot(system)) == {
        table.name: {"inserted": [], "updated": [], "deleted": [victim]}
    }


def test_updating_a_keyed_row_is_reported_exactly(system, baseline, db):
    engine, metadata = db
    table = _first_table(metadata, keyed=True)
    rows = baseline.tables[table.name].rows
    target = rows[0]
    keys = [column.name for column in table.primary_key.columns]

    # Give the target row another row's value: valid for the column whatever its type.
    for column in table.columns:
        donor = next((row for row in rows if row[column.name] != target[column.name]), None)
        if column.name in keys or donor is None:
            continue
        try:
            with engine.begin() as connection:
                value = _raw_row(connection, table, donor)[column.name]
                connection.execute(
                    table.update()
                    .where(*(table.c[key] == target[key] for key in keys))
                    .values({column.name: value})
                )
        except DBAPIError:
            continue  # for example a unique column; try the next one

        assert dbstate.delta(baseline, dbstate.snapshot(system)) == {
            table.name: {
                "inserted": [],
                "updated": [{
                    "key": {key: target[key] for key in keys},
                    "changes": {
                        column.name: {"before": target[column.name], "after": donor[column.name]}
                    },
                }],
                "deleted": [],
            }
        }
        return
    pytest.skip("no column of the keyed table could be updated")


def test_deleting_from_a_table_without_a_key_is_reported_exactly(system, baseline, db):
    engine, metadata = db
    table = _first_table(metadata, keyed=False)
    rows = baseline.tables[table.name].rows
    if not rows:
        pytest.skip("the table without a key is empty")
    victim = rows[0]
    copies = sum(1 for row in rows if row == victim)

    with engine.begin() as connection:
        connection.execute(table.delete().where(_is_row(table, _raw_row(connection, table, victim))))

    assert dbstate.delta(baseline, dbstate.snapshot(system)) == {
        table.name: {"inserted": [], "updated": [], "deleted": [victim] * copies}
    }


# -------------------------------------------------------------------------- reset


def test_reset_restores_the_baseline_after_every_row_is_deleted(system, baseline, db):
    _empty_every_table(*db)
    assert dbstate.snapshot(system).state_fingerprint != baseline.state_fingerprint

    assert dbstate.reset(system).state_fingerprint == baseline.state_fingerprint


def test_reset_removes_objects_the_dump_does_not_know_about(system, baseline, db):
    engine, _ = db
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE c4_stray (id INT)"))
    assert "c4_stray" in dbstate.snapshot(system).tables

    assert dbstate.reset(system).state_fingerprint == baseline.state_fingerprint


def test_a_clean_database_is_not_reloaded(system, baseline, reloads):
    restored = dbstate.ensure_baseline(system, baseline)

    assert reloads == []
    assert restored.state_fingerprint == baseline.state_fingerprint


def test_a_dirty_database_is_reloaded_once_and_verified(system, baseline, db, reloads):
    _empty_every_table(*db)

    restored = dbstate.ensure_baseline(system, baseline)

    assert reloads == [system.name]
    assert restored.state_fingerprint == baseline.state_fingerprint


def test_a_consumed_counter_makes_the_database_dirty(system, baseline, db, backend, reloads):
    # What a failed insert leaves behind: the rows untouched, the next id used up.
    if backend not in ("mysql", "mariadb"):
        pytest.skip("the counter is moved with MySQL syntax")
    engine, _ = db
    name = next((n for n, table in baseline.tables.items() if table.auto_increment), None)
    if name is None:
        pytest.skip("this benchmark has no auto-increment table")
    moved = baseline.tables[name].auto_increment + 5
    with engine.begin() as connection:
        quoted = engine.dialect.identifier_preparer.quote(name)
        connection.execute(text(f"ALTER TABLE {quoted} AUTO_INCREMENT = {moved}"))

    dirty = dbstate.snapshot(system)
    assert dirty.tables[name].auto_increment == moved
    assert dirty.data_fingerprint == baseline.data_fingerprint
    assert dirty.state_fingerprint != baseline.state_fingerprint

    assert dbstate.ensure_baseline(system, baseline).state_fingerprint == baseline.state_fingerprint
    assert reloads == [system.name]


def test_a_reload_that_does_not_restore_the_baseline_is_an_error(
    system, baseline, db, backend, monkeypatch
):
    engine, _ = db
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE c4_stray (id INT)"))
    monkeypatch.setitem(dbstate._RELOADERS, backend, lambda target: None)  # reload does nothing

    with pytest.raises(dbstate.BaselineError, match="c4_stray"):
        dbstate.ensure_baseline(system, baseline)
