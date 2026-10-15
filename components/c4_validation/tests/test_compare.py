"""Comparison rules, checked on small oracle records written for the purpose."""

import pytest

from c4.compare import FAIL, INCONCLUSIVE, PASS, compare, normalise
from c4.replay import Replay


def oracle(*records, delta=None, excluded=(), outcome="success"):
    return {
        "scenario_id": "s",
        "legacy_response": {"status_code": 200, "outcome": outcome, "records": list(records)},
        "db_state": {"baseline_fingerprint": "sha256:x", "delta": delta or {}},
        "excluded_fields": [
            {"scope": scope, "field": field, "reason": "declared"} for scope, field in excluded
        ],
    }


def migrated(*records, delta=None, status=200, outcome="success", problem=None):
    return Replay(status, outcome, None if problem else tuple(records), problem, delta or {})


def inserted(table, *rows):
    return {table: {"inserted": list(rows), "updated": [], "deleted": []}}


RAM = {"First Name": "Ram", "Last Name": "Kumar", "Fee": "550"}
ALIA = {"First Name": "Alia", "Last Name": "Bhatt", "Fee": "700"}
ram = {"fname": "Ram", "lname": "Kumar", "fee": 550}
alia = {"fname": "Alia", "lname": "Bhatt", "fee": 700}


# ------------------------------------------------------------------------ values


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("550", 550), ("550", 550.0), ("550.00", 550), ("550.50", 550.5), ("-3", -3), ("0", 0.0),
        ("", None), ("Viki", " Viki"), ("Hey Admin", "Hey   Admin\n"), ("1", True), ("0", False),
    ],
)
def test_the_same_content_in_two_representations_is_equal(a, b):
    assert normalise(a) == normalise(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("0771234567", 771234567),  # a leading zero is content, not formatting
        ("Ram", "ram"),
        ("550", "551"),
        ("2020-02-14", "2020-02-15"),
        ("1e3", 1000),
    ],
)
def test_different_content_stays_different(a, b):
    assert normalise(a) != normalise(b)


# ---------------------------------------------------------------------- response


def test_same_data_under_different_field_names_passes():
    assert compare(oracle(RAM, ALIA), migrated(ram, alia)).verdict == PASS


def test_record_order_is_ignored():
    assert compare(oracle(RAM, ALIA), migrated(alia, ram)).verdict == PASS


def test_extra_migrated_fields_are_not_a_difference():
    assert compare(oracle(RAM), migrated({**ram, "pid": 1, "gender": "Male"})).verdict == PASS


def test_values_nested_inside_a_migrated_record_count():
    nested = {"id": 1, "name": {"first": "Ram", "last": "Kumar"}, "fees": [550]}

    assert compare(oracle(RAM), migrated(nested)).verdict == PASS


def test_an_empty_legacy_cell_matches_null_empty_or_absent():
    legacy = {"Name": "Ram", "Allergy": ""}

    assert compare(oracle(legacy), migrated({"name": "Ram", "allergy": None})).verdict == PASS
    assert compare(oracle(legacy), migrated({"name": "Ram", "allergy": ""})).verdict == PASS
    assert compare(oracle(legacy), migrated({"name": "Ram"})).verdict == PASS


def test_an_altered_value_fails_and_names_the_field():
    result = compare(oracle(RAM, ALIA), migrated({**ram, "lname": "Kumarx"}, alia))

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["kind"] == "record_not_reproduced"
    assert difference["values_not_found"] == {"Last Name": "Kumar"}
    assert difference["closest_migrated_record"] == {**ram, "lname": "Kumarx"}


def test_an_altered_record_is_reported_once_not_also_as_unexpected():
    result = compare(oracle(RAM), migrated({**ram, "fee": 999}))

    assert [d["kind"] for d in result.differences] == ["record_not_reproduced"]


def test_a_missing_field_fails_and_names_the_field():
    without_fee = {"fname": "Ram", "lname": "Kumar"}

    (difference,) = compare(oracle(RAM), migrated(without_fee)).differences

    assert difference["values_not_found"] == {"Fee": "550"}


def test_a_missing_record_fails():
    result = compare(oracle(RAM, ALIA), migrated(ram))

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["legacy_record"] == ALIA
    assert difference["closest_migrated_record"] is None


def test_an_unexpected_record_fails():
    result = compare(oracle(RAM), migrated(ram, alia))

    assert result.verdict == FAIL
    assert result.differences == ({"where": "response", "kind": "unexpected_record", "migrated_record": alia},)


def test_repeated_legacy_records_each_need_a_migrated_record_of_their_own():
    assert compare(oracle(RAM, RAM), migrated(ram, ram)).verdict == PASS
    assert compare(oracle(RAM, RAM), migrated(ram)).verdict == FAIL


def test_pairing_does_not_depend_on_which_record_is_tried_first():
    # The first legacy record fits either migrated record; the second fits only one.
    loose, strict = {"City": "Kandy"}, {"City": "Kandy", "Name": "Ram"}
    both, only_city = {"city": "Kandy", "name": "Ram"}, {"city": "Kandy"}

    assert compare(oracle(loose, strict), migrated(both, only_city)).verdict == PASS
    assert compare(oracle(strict, loose), migrated(only_city, both)).verdict == PASS


def test_many_identical_records_are_paired():
    count = 1500

    assert compare(oracle(*[RAM] * count), migrated(*[ram] * count)).verdict == PASS


def test_no_records_on_both_sides_passes_and_on_one_side_fails():
    assert compare(oracle(), migrated()).verdict == PASS
    assert compare(oracle(), migrated(ram)).verdict == FAIL
    assert compare(oracle(RAM), migrated()).verdict == FAIL


def test_a_different_outcome_fails():
    result = compare(oracle(RAM), migrated(ram, status=500, outcome="error"))

    assert result.verdict == FAIL
    assert result.differences[0] == {
        "where": "response", "kind": "outcome_differs",
        "legacy": "success", "migrated": "error", "migrated_status_code": 500,
    }


def test_different_status_codes_of_the_same_outcome_pass():
    assert compare(oracle(RAM), migrated(ram, status=201)).verdict == PASS


def test_a_response_without_readable_records_fails():
    result = compare(oracle(RAM), migrated(problem="the response body is not JSON"))

    assert result.verdict == FAIL
    assert result.differences == (
        {"where": "response", "kind": "unreadable", "detail": "the response body is not JSON"},
    )


# --------------------------------------------------------------- excluded fields


SEEN = {"Name": "Ram", "Seen At": "10:00:01"}


def test_a_difference_confined_to_an_excluded_field_is_inconclusive():
    record = oracle(SEEN, excluded=[("response", "Seen At")])

    result = compare(record, migrated({"name": "Ram", "seen_at": "10:07:44"}))

    assert result.verdict == INCONCLUSIVE
    assert result.differences == ()
    (abstention,) = result.abstentions
    assert abstention["kind"] == "excluded_field_differs" and abstention["field"] == "Seen At"


def test_an_excluded_field_that_agrees_passes():
    record = oracle(SEEN, excluded=[("response", "Seen At")])

    assert compare(record, migrated({"name": "Ram", "seen_at": "10:00:01"})).verdict == PASS


def test_the_same_difference_fails_when_the_field_is_not_excluded():
    assert compare(oracle(SEEN), migrated({"name": "Ram", "seen_at": "10:07:44"})).verdict == FAIL


def test_a_real_difference_fails_even_if_an_excluded_field_also_differs():
    record = oracle(SEEN, excluded=[("response", "Seen At")])

    assert compare(record, migrated({"name": "Rama", "seen_at": "10:07:44"})).verdict == FAIL


def test_records_are_still_paired_when_an_excluded_field_differs():
    record = oracle(
        {"Name": "Ram", "Seen At": "1"}, {"Name": "Alia", "Seen At": "2"},
        excluded=[("response", "Seen At")],
    )

    result = compare(record, migrated({"name": "Alia", "seen": "9"}, {"name": "Ram", "seen": "8"}))

    assert result.verdict == INCONCLUSIVE and len(result.abstentions) == 2


def test_exclusions_that_leave_nothing_to_compare_are_inconclusive():
    record = oracle({"Token": "abc"}, excluded=[("response", "Token")])

    result = compare(record, migrated({"token": "abc"}))

    assert result.verdict == INCONCLUSIVE
    assert {"where": "response", "kind": "nothing_to_compare"} in result.abstentions


# ---------------------------------------------------------------------- database


def test_the_same_database_change_on_both_sides_passes():
    legacy = inserted("contact", {"name": "Anu", "fee": "550"})
    replayed = inserted("contact", {"name": "Anu", "fee": 550})

    assert compare(oracle(delta=legacy), migrated(delta=replayed)).verdict == PASS


def test_a_suppressed_database_row_fails():
    legacy = inserted("contact", {"name": "Anu", "message": "hello"})

    result = compare(oracle(delta=legacy), migrated(delta={}))

    assert result.verdict == FAIL
    assert result.differences[0]["where"] == "database"
    assert "contact" in str(result.differences[0]["detail"])


def test_a_wrong_written_value_fails_and_points_at_the_column():
    legacy = inserted("contact", {"name": "Anu", "message": "hello"})
    replayed = inserted("contact", {"name": "Anu", "message": "goodbye"})

    (difference,) = compare(oracle(delta=legacy), migrated(delta=replayed)).differences

    assert difference["kind"] == "delta_differs"
    assert "['contact']['inserted'][0]['message']" in str(difference["detail"])


def test_a_write_to_another_table_fails():
    legacy = inserted("contact", {"name": "Anu"})
    replayed = {**legacy, **inserted("audit", {"event": "contact created"})}

    assert compare(oracle(delta=legacy), migrated(delta=replayed)).verdict == FAIL


def test_a_row_written_twice_fails():
    once = inserted("contact", {"name": "Anu", "message": "hello"})
    twice = inserted("contact", {"name": "Anu", "message": "hello"}, {"name": "Anu", "message": "hello"})

    assert compare(oracle(delta=once), migrated(delta=twice)).verdict == FAIL
    assert compare(oracle(delta=twice), migrated(delta=once)).verdict == FAIL
    assert compare(oracle(delta=twice), migrated(delta=twice)).verdict == PASS


def test_identical_rows_among_others_are_counted():
    legacy = inserted("contact", {"name": "Anu"}, {"name": "Ben"}, {"name": "Ben"})
    fewer = inserted("contact", {"name": "Ben"}, {"name": "Anu"})
    same = inserted("contact", {"name": "Ben"}, {"name": "Anu"}, {"name": "Ben"})

    assert compare(oracle(delta=legacy), migrated(delta=fewer)).verdict == FAIL
    assert compare(oracle(delta=legacy), migrated(delta=same)).verdict == PASS


def test_the_wording_of_a_legacy_pop_up_message_is_not_compared():
    record = oracle(delta=inserted("contact", {"name": "Anu"}))
    record["legacy_response"]["messages"] = ["Message sent successfully!"]

    assert compare(record, migrated(status=201, delta=inserted("contact", {"name": "Anu"}))).verdict == PASS


def test_the_order_of_written_rows_is_ignored():
    legacy = inserted("contact", {"name": "Anu"}, {"name": "Ben"})
    replayed = inserted("contact", {"name": "Ben"}, {"name": "Anu"})

    assert compare(oracle(delta=legacy), migrated(delta=replayed)).verdict == PASS


def test_an_update_must_change_the_same_row_to_the_same_value():
    def updated(after):
        return {"appointments": {"inserted": [], "deleted": [], "updated": [
            {"key": {"id": 3}, "changes": {"status": {"before": "1", "after": after}}}
        ]}}

    assert compare(oracle(delta=updated("0")), migrated(delta=updated(0))).verdict == PASS
    assert compare(oracle(delta=updated("0")), migrated(delta=updated("2"))).verdict == FAIL


def test_a_database_difference_confined_to_an_excluded_column_is_inconclusive():
    legacy = inserted("contact", {"name": "Anu", "created_at": "2026-10-12 10:00:01"})
    replayed = inserted("contact", {"name": "Anu", "created_at": "2026-10-12 10:07:44"})
    record = oracle(delta=legacy, excluded=[("database", "contact.created_at")])

    result = compare(record, migrated(delta=replayed))

    assert result.verdict == INCONCLUSIVE
    assert result.abstentions[0]["where"] == "database"


def test_the_same_database_difference_fails_when_the_column_is_not_excluded():
    legacy = inserted("contact", {"name": "Anu", "created_at": "2026-10-12 10:00:01"})
    replayed = inserted("contact", {"name": "Anu", "created_at": "2026-10-12 10:07:44"})

    assert compare(oracle(delta=legacy), migrated(delta=replayed)).verdict == FAIL


def test_a_matching_response_does_not_excuse_a_wrong_database_change():
    legacy = inserted("contact", {"name": "Anu"})
    replayed = inserted("contact", {"name": "Ben"})

    result = compare(oracle(RAM, delta=legacy), migrated(ram, delta=replayed))

    assert result.verdict == FAIL
    assert [d["where"] for d in result.differences] == ["database"]


def test_a_matching_database_change_does_not_excuse_a_wrong_response():
    delta = inserted("contact", {"name": "Anu"})

    result = compare(oracle(RAM, delta=delta), migrated({**ram, "fee": 1}, delta=delta))

    assert result.verdict == FAIL
    assert [d["where"] for d in result.differences] == ["response"]


# ------------------------------------------------- a legacy request with no effect


def nothing(*records, delta=None):
    return oracle(*records, delta=delta, outcome="no_effect")


@pytest.mark.parametrize(("status", "outcome"), [(200, "success"), (404, "rejection"), (422, "rejection")])
def test_doing_nothing_agrees_with_any_answer_that_also_does_nothing(status, outcome):
    assert compare(nothing(), migrated(status=status, outcome=outcome)).verdict == PASS


def test_a_rejection_without_readable_records_agrees_with_doing_nothing():
    replayed = migrated(status=404, outcome="rejection", problem="the response has nothing at 'things'")

    assert compare(nothing(), replayed).verdict == PASS


def test_a_server_error_does_not_agree_with_doing_nothing():
    result = compare(nothing(), migrated(status=500, outcome="error"))

    assert result.verdict == FAIL
    assert result.differences[0]["kind"] == "outcome_differs"


def test_returning_records_where_the_legacy_system_showed_none_fails():
    assert compare(nothing(), migrated(ram)).verdict == FAIL


def test_writing_where_the_legacy_system_wrote_nothing_fails_and_says_so():
    result = compare(nothing(), migrated(status=201, delta=inserted("contact", {"name": "O'Brien"})))

    assert result.verdict == FAIL
    (difference,) = result.differences
    assert difference["where"] == "database"
    assert difference["note"] == "the legacy system changed nothing for this input; the migrated system did"


def test_a_rejection_of_a_request_the_legacy_system_completed_fails():
    record = oracle(delta=inserted("contact", {"name": "Anu"}))

    result = compare(record, migrated(status=422, outcome="rejection", problem="no records"))

    assert result.verdict == FAIL
    assert [d["kind"] for d in result.differences] == ["outcome_differs", "delta_differs"]
