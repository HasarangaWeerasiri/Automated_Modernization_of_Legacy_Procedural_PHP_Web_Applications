"""Labelled comparison: a value held to its database column on both sides."""

import pytest

from c4.capture import _field_columns
from c4.compare import FAIL, INCONCLUSIVE, PASS, compare
from c4.replay import Replay
from c4.scenarios import EndpointMapError, load_endpoint_map
from c4.verdict import _describe, differential_report

RAM = {"First Name": "Ram", "Last Name": "Kumar", "Email": "ram@example.test"}
COLUMNS = {"First Name": "patreg.fname", "Last Name": "patreg.lname", "Email": "patreg.email"}
FIELDS = {"fname": "patreg.fname", "lname": "patreg.lname", "email": "patreg.email"}


def oracle(*records, columns=COLUMNS, excluded=()):
    return {
        "scenario_id": "s", "endpoint_id": "GET /api/patients",
        "legacy_response": {"status_code": 200, "outcome": "success", "records": list(records), "columns": columns},
        "db_state": {"delta": {}},
        "excluded_fields": [{"scope": "response", "field": f, "reason": "computed_on_page"} for f in excluded],
    }


def migrated(*records, fields=FIELDS):
    return Replay(200, "success", tuple(records), None, {}, fields)


# ------------------------------------------------------------------- comparison


def test_swapped_fields_fail_when_both_sides_link_them_to_columns():
    result = compare(oracle(RAM), migrated({"fname": "Kumar", "lname": "Ram", "email": "ram@example.test"}))

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["values_under_another_column"]["First Name"] == {
        "column": "patreg.fname", "legacy": "Ram", "migrated": "Kumar",
    }
    assert "'First Name' (patreg.fname): legacy 'Ram', migrated 'Kumar'" in _describe(difference)


def test_the_same_values_under_the_right_columns_pass_and_say_how_they_were_compared():
    result = compare(oracle(RAM), migrated({"fname": "Ram", "lname": "Kumar", "email": "ram@example.test", "pid": 1}))

    assert result.verdict == PASS
    assert result.by_label == ("First Name", "Last Name", "Email")
    assert result.by_value == ()


def test_a_field_only_one_side_links_is_compared_by_value_alone():
    swapped = {"fname": "Kumar", "lname": "Ram", "email": "ram@example.test"}

    result = compare(oracle(RAM), migrated(swapped, fields={"email": "patreg.email"}))

    assert result.verdict == PASS  # the swap cannot be seen without both links
    assert result.by_label == ("Email",)
    assert result.by_value == ("First Name", "Last Name")


def test_an_excluded_field_is_never_held_to_its_column():
    result = compare(oracle(RAM, excluded=("Last Name",)), migrated({"fname": "Ram", "lname": "x", "email": "ram@example.test"}))

    assert result.verdict == INCONCLUSIVE  # the excluded field differs, and only it
    assert "Last Name" not in result.by_label


def test_a_nested_field_is_linked_by_its_path():
    record = {"First Name": "Ram", "Login": "ram"}
    columns = {"First Name": "users.fname", "Login": "users.login"}
    fields = {"user.fname": "users.fname", "user.login": "users.login"}

    good = compare(oracle(record, columns=columns), migrated({"user": {"fname": "Ram", "login": "ram"}}, fields=fields))
    bad = compare(oracle(record, columns=columns), migrated({"user": {"fname": "ram", "login": "Ram"}}, fields=fields))

    assert (good.verdict, bad.verdict) == (PASS, FAIL)


def test_records_are_still_paired_by_their_columns_whatever_their_order():
    alia = {"First Name": "Alia", "Last Name": "Bhatt", "Email": "alia@example.test"}

    result = compare(oracle(RAM, alia), migrated(
        {"fname": "Alia", "lname": "Bhatt", "email": "alia@example.test"},
        {"fname": "Ram", "lname": "Kumar", "email": "ram@example.test"},
    ))

    assert result.verdict == PASS


# ------------------------------------------------------- linking legacy fields

PATIENTS = {"patreg": [
    {"pid": 1, "fname": "Ram", "lname": "Kumar", "email": "ram@example.test"},
    {"pid": 2, "fname": "Alia", "lname": "Bhatt", "email": "alia@example.test"},
]}


def test_a_field_is_linked_to_the_column_of_the_row_each_record_shows():
    records = ({"No": "1", "Name": "Ram", "Mail": "ram@example.test"}, {"No": "2", "Name": "Alia", "Mail": "alia@example.test"})

    assert _field_columns(records, PATIENTS, []) == {"No": "patreg.pid", "Name": "patreg.fname", "Mail": "patreg.email"}


def test_a_field_two_columns_could_hold_is_left_unlinked():
    twins = {"people": [{"id": 1, "first": "Anu", "nick": "Anu", "city": "Kandy"}]}

    assert _field_columns(({"Name": "Anu", "City": "Kandy"},), twins, []) == {"City": "people.city"}


def test_the_records_own_table_comes_before_a_table_joined_in():
    tables = {
        **PATIENTS,
        "appointments": [{"id": 9, "fname": "Ram", "doctor": "Ganesh"}],
    }
    record = {"Name": "Ram", "Surname": "Kumar", "Mail": "ram@example.test"}

    assert _field_columns((record,), tables, [])["Name"] == "patreg.fname"


def test_a_record_that_shows_no_row_links_nothing():
    records = ({"Name": "Ram", "Mail": "ram@example.test"}, {"Name": "Nobody", "Mail": "none@example.test"})

    assert _field_columns(records, PATIENTS, []) == {}


def test_without_database_rows_nothing_is_linked():
    assert _field_columns(({"Name": "Ram"},), None, []) == {}


def test_skipped_fields_are_not_linked():
    records = ({"No": "1", "Name": "Ram", "Mail": "ram@example.test"},)

    assert "Name" not in _field_columns(records, PATIENTS, ["Name"])


# ---------------------------------------------------------------- the endpoint map


def test_an_endpoint_map_names_the_column_of_each_response_field(tmp_path):
    document = {"format": "c4-stand-in/1", "endpoints": [{
        "endpoint_id": "GET /api/p", "method": "GET", "path": "/api/p", "records_at": "p",
        "fields": {"fname": "patreg.fname", "user.login": "users.login"},
        "sources": [{"source_id": "s", "legacy": {"method": "GET", "path": "/p.php", "records": "only"}, "parameters": []}],
    }]}
    path = tmp_path / "map.json"
    path.write_text(__import__("json").dumps(document))

    assert load_endpoint_map(path)["GET /api/p"].fields == {"fname": "patreg.fname", "user.login": "users.login"}

    document["endpoints"][0]["fields"] = {"fname": "fname"}
    path.write_text(__import__("json").dumps(document))
    with pytest.raises(EndpointMapError, match="table.column"):
        load_endpoint_map(path)


# --------------------------------------------------------------------- reports


def test_the_readable_report_says_how_fields_were_compared():
    report = {"benchmark": "demo", "endpoints": {"GET /api/p": {
        "verdict": PASS, "fields_compared": {"by_label": ["First Name"], "by_value": ["Status"]}, "scenarios": [],
    }}}

    text = differential_report(report, {"probes": []})

    assert "- `GET /api/p`: by column: First Name; by value only: Status" in text
