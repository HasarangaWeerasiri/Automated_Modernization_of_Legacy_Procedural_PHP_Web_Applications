"""The snapshot, fingerprint and delta logic, checked without a database."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from c4.dbstate import Snapshot, StructureChangedError, TableState, _plain, delta


def keyed(*rows, auto_increment=None, columns=("id", "name", "fee")):
    return TableState(
        columns=columns,
        primary_key=("id",),
        definition=tuple(f"{column} X NOT NULL" for column in columns),
        auto_increment=auto_increment,
        rows=tuple(dict(zip(columns, row)) for row in rows),
    )


def unkeyed(*rows, columns=("name", "message")):
    return TableState(
        columns=columns,
        primary_key=(),
        definition=tuple(f"{column} X NULL" for column in columns),
        auto_increment=None,
        rows=tuple(dict(zip(columns, row)) for row in rows),
    )


def db(**tables):
    return Snapshot(tables)


# ------------------------------------------------------------------- keyed tables


def test_identical_snapshots_have_no_delta():
    before = db(doctors=keyed((1, "Ganesh", "500")), contact=unkeyed(("Anu", "hello")))
    after = db(doctors=keyed((1, "Ganesh", "500")), contact=unkeyed(("Anu", "hello")))

    assert delta(before, after) == {}


def test_row_order_is_not_a_change():
    before = db(doctors=keyed((1, "Ganesh", "500"), (2, "Dinesh", "700")))
    after = db(doctors=keyed((2, "Dinesh", "700"), (1, "Ganesh", "500")))

    assert delta(before, after) == {}
    assert before.state_fingerprint == after.state_fingerprint


def test_inserted_row_is_reported_in_full():
    before = db(doctors=keyed((1, "Ganesh", "500")))
    after = db(doctors=keyed((1, "Ganesh", "500"), (2, "Dinesh", "700")))

    assert delta(before, after) == {
        "doctors": {
            "inserted": [{"id": 2, "name": "Dinesh", "fee": "700"}],
            "updated": [],
            "deleted": [],
        }
    }


def test_deleted_row_is_reported_in_full():
    before = db(doctors=keyed((1, "Ganesh", "500"), (2, "Dinesh", "700")))
    after = db(doctors=keyed((2, "Dinesh", "700")))

    assert delta(before, after)["doctors"] == {
        "inserted": [],
        "updated": [],
        "deleted": [{"id": 1, "name": "Ganesh", "fee": "500"}],
    }


def test_update_names_the_key_and_only_the_changed_columns():
    before = db(doctors=keyed((1, "Ganesh", "500"), (2, "Dinesh", "700")))
    after = db(doctors=keyed((1, "Ganesh", "550"), (2, "Dinesh", "700")))

    assert delta(before, after)["doctors"] == {
        "inserted": [],
        "updated": [{"key": {"id": 1}, "changes": {"fee": {"before": "500", "after": "550"}}}],
        "deleted": [],
    }


def test_insert_update_and_delete_in_one_table():
    before = db(doctors=keyed((1, "Ganesh", "500"), (2, "Dinesh", "700")))
    after = db(doctors=keyed((2, "Dinesh", "750"), (3, "Kumar", "900")))

    changes = delta(before, after)["doctors"]

    assert changes["inserted"] == [{"id": 3, "name": "Kumar", "fee": "900"}]
    assert changes["updated"] == [{"key": {"id": 2}, "changes": {"fee": {"before": "700", "after": "750"}}}]
    assert changes["deleted"] == [{"id": 1, "name": "Ganesh", "fee": "500"}]


def test_null_is_a_value_like_any_other():
    before = db(doctors=keyed((1, "Ganesh", None), (2, "Dinesh", None)))
    after = db(doctors=keyed((1, "Ganesh", None), (2, "Dinesh", "700")))

    assert delta(before, after)["doctors"]["updated"] == [
        {"key": {"id": 2}, "changes": {"fee": {"before": None, "after": "700"}}}
    ]


def test_a_number_and_its_text_form_are_different_values():
    before = db(doctors=keyed((1, "Ganesh", 500)))
    after = db(doctors=keyed((1, "Ganesh", "500")))

    assert delta(before, after)["doctors"]["updated"] == [
        {"key": {"id": 1}, "changes": {"fee": {"before": 500, "after": "500"}}}
    ]


def test_only_changed_tables_appear():
    before = db(doctors=keyed((1, "Ganesh", "500")), contact=unkeyed(("Anu", "hello")))
    after = db(doctors=keyed((1, "Ganesh", "500")), contact=unkeyed(("Anu", "hello"), ("Ben", "hi")))

    assert list(delta(before, after)) == ["contact"]


# ----------------------------------------------------------------- unkeyed tables


def test_unkeyed_insert_and_delete():
    before = db(contact=unkeyed(("Anu", "hello"), ("Ben", "hi")))
    after = db(contact=unkeyed(("Ben", "hi"), ("Cara", "hey")))

    assert delta(before, after)["contact"] == {
        "inserted": [{"name": "Cara", "message": "hey"}],
        "updated": [],
        "deleted": [{"name": "Anu", "message": "hello"}],
    }


def test_unkeyed_update_appears_as_delete_plus_insert():
    before = db(contact=unkeyed(("Anu", "hello")))
    after = db(contact=unkeyed(("Anu", "goodbye")))

    assert delta(before, after)["contact"] == {
        "inserted": [{"name": "Anu", "message": "goodbye"}],
        "updated": [],
        "deleted": [{"name": "Anu", "message": "hello"}],
    }


def test_identical_unkeyed_rows_are_counted():
    row = ("Anu", "hello")
    before = db(contact=unkeyed(row, row))
    one_more = db(contact=unkeyed(row, row, row))
    one_less = db(contact=unkeyed(row))

    assert delta(before, one_more)["contact"]["inserted"] == [{"name": "Anu", "message": "hello"}]
    assert delta(before, one_more)["contact"]["deleted"] == []
    assert delta(before, one_less)["contact"]["deleted"] == [{"name": "Anu", "message": "hello"}]
    assert delta(before, one_less)["contact"]["inserted"] == []


def test_unkeyed_rows_with_nulls():
    before = db(contact=unkeyed(("Anu", None), (None, None)))
    after = db(contact=unkeyed(("Anu", None)))

    assert delta(before, after)["contact"]["deleted"] == [{"name": None, "message": None}]


def test_empty_table_gaining_and_losing_rows():
    empty, full = db(contact=unkeyed()), db(contact=unkeyed(("Anu", "hello")))

    assert delta(empty, full)["contact"]["inserted"] == [{"name": "Anu", "message": "hello"}]
    assert delta(full, empty)["contact"]["deleted"] == [{"name": "Anu", "message": "hello"}]
    assert delta(empty, db(contact=unkeyed())) == {}


def test_delta_is_the_same_whatever_order_rows_arrive_in():
    before = db(contact=unkeyed(("Anu", "1"), ("Ben", "2"), ("Cara", "3")))
    after_a = db(contact=unkeyed(("Dev", "4"), ("Eli", "5"), ("Anu", "1")))
    after_b = db(contact=unkeyed(("Anu", "1"), ("Eli", "5"), ("Dev", "4")))

    assert delta(before, after_a) == delta(before, after_b)


# ---------------------------------------------------------------------- structure


def test_a_dropped_table_is_a_structure_change():
    before = db(doctors=keyed((1, "Ganesh", "500")), contact=unkeyed())
    after = db(doctors=keyed((1, "Ganesh", "500")))

    with pytest.raises(StructureChangedError, match="contact"):
        delta(before, after)


def test_a_changed_column_is_a_structure_change():
    before = db(contact=unkeyed(("Anu", "hello")))
    after = db(contact=unkeyed(("Anu", "hello"), columns=("name", "body")))

    with pytest.raises(StructureChangedError, match="contact"):
        delta(before, after)


# ------------------------------------------------------------------- fingerprints


def test_a_changed_row_changes_both_fingerprints():
    before = db(doctors=keyed((1, "Ganesh", "500")))
    after = db(doctors=keyed((1, "Ganesh", "550")))

    assert before.data_fingerprint != after.data_fingerprint
    assert before.state_fingerprint != after.state_fingerprint


def test_a_consumed_counter_changes_only_the_state_fingerprint():
    # A failed insert uses up an id and leaves no row behind.
    before = db(doctors=keyed((1, "Ganesh", "500"), auto_increment=2))
    after = db(doctors=keyed((1, "Ganesh", "500"), auto_increment=3))

    assert delta(before, after) == {}
    assert before.data_fingerprint == after.data_fingerprint
    assert before.state_fingerprint != after.state_fingerprint


def test_a_changed_definition_changes_only_the_state_fingerprint():
    rows = dict(columns=("name", "message"), primary_key=(), auto_increment=None,
                rows=({"name": "Anu", "message": "hello"},))
    before = db(contact=TableState(definition=("name VARCHAR(30) NULL", "message TEXT NULL"), **rows))
    after = db(contact=TableState(definition=("name VARCHAR(90) NULL", "message TEXT NULL"), **rows))

    assert before.data_fingerprint == after.data_fingerprint
    assert before.state_fingerprint != after.state_fingerprint


def test_fingerprint_format():
    fingerprint = db(contact=unkeyed()).state_fingerprint

    assert fingerprint.startswith("sha256:") and len(fingerprint) == len("sha256:") + 64


# ------------------------------------------------------------------------- values


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        (1.5, 1.5),
        ("text", "text"),
        (Decimal("500.00"), "500.00"),
        (date(2020, 2, 14), "2020-02-14"),
        (datetime(2020, 2, 14, 9, 30, 5), "2020-02-14 09:30:05"),
        (time(9, 30, 5), "09:30:05"),
        (timedelta(hours=10), "10:00:00"),
        (timedelta(hours=9, minutes=5, seconds=7), "09:05:07"),
        (timedelta(hours=-1, minutes=-30), "-01:30:00"),
        (b"\x00\xff", "0x00ff"),
    ],
)
def test_database_values_reduce_to_stable_json_types(value, expected):
    assert _plain(value) == expected


def test_an_unknown_value_type_is_refused_not_guessed():
    with pytest.raises(TypeError, match="object"):
        _plain(object())
