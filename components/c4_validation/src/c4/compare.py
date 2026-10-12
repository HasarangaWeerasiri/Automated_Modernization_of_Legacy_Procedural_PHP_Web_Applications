"""Stage 5 — compare.

Legacy and migrated behaviour are compared as data, never as text. The legacy system
returned an HTML page and the migrated system returns JSON, so nothing about their
format can be expected to match. What must match is the data content of the response
and the change made to the database. Both are compared, and both must agree.

Three results are possible for a scenario:

    FAIL          a compared value or row differs
    INCONCLUSIVE  the only differences are in excluded fields, which cannot be judged,
                  or the exclusions leave nothing to compare
    PASS          everything compared agrees

Response records are matched without using field names, because the two sides name
their fields differently (a page header against a JSON key). Each legacy record must
be reproduced by a migrated record of its own: every value the legacy record shows
must appear in that migrated record. Two consequences follow, and both are accepted
limits of comparing without labels:

  - a migrated record may carry extra values the page did not show;
  - two fields whose values are swapped still match.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from deepdiff import DeepDiff

from c4.replay import Replay

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"

# A number written the way a number is written. Text such as "0771234567" is not:
# its leading zero carries meaning, so it stays text.
_NUMBER = re.compile(r"-?(0|[1-9]\d*)(\.\d+)?")


@dataclass(frozen=True)
class Comparison:
    verdict: str
    # Disagreements in compared data: any one of these makes the scenario FAIL.
    differences: tuple[dict[str, Any], ...]
    # Disagreements the engine cannot judge: these make it INCONCLUSIVE.
    abstentions: tuple[dict[str, Any], ...]


def normalise(value: Any) -> str:
    """Reduce a value to its data content, independent of representation.

    550, 550.0, "550" and "550.00" are one value; so are null and an empty cell;
    so are true and 1. Whitespace is compared the way a page displays it.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    text = " ".join(str(value).split())
    if _NUMBER.fullmatch(text):
        number = format(Decimal(text).normalize(), "f")
        return "0" if number == "-0" else number
    return text


def _leaves(value: Any) -> Iterable[Any]:
    """Every plain value inside a JSON value, however it is nested."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


def _values(values: Iterable[Any]) -> Counter:
    # An empty value asks for nothing: a missing field, a null and an empty string
    # are the same content.
    return Counter(text for text in map(normalise, values) if text)


def _match(needed: list[Counter], offered: list[Counter]) -> dict[int, int]:
    """Pair each legacy record with a migrated record of its own that reproduces it.

    Returns legacy index -> migrated index for the largest set of pairs possible,
    so the result does not depend on the order either side lists its records in.
    """
    candidates = [
        [m for m, have in enumerate(offered) if not need - have] for need in needed
    ]
    taken: dict[int, int] = {}  # migrated index -> legacy index

    def place(legacy: int, seen: set[int]) -> bool:
        # A free record is taken before any other pairing is disturbed, which keeps
        # the search shallow when many records look alike.
        for migrated in candidates[legacy]:
            if migrated not in taken:
                taken[migrated] = legacy
                return True
        for migrated in candidates[legacy]:
            if migrated in seen:
                continue
            seen.add(migrated)
            if place(taken[migrated], seen):
                taken[migrated] = legacy
                return True
        return False

    for legacy in range(len(needed)):
        place(legacy, set())
    return {legacy: migrated for migrated, legacy in taken.items()}


def _compare_response(record: dict[str, Any], replayed: Replay) -> tuple[list, list]:
    differences: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    legacy = record["legacy_response"]

    if replayed.outcome != legacy["outcome"]:
        differences.append({
            "where": "response", "kind": "outcome_differs",
            "legacy": legacy["outcome"], "migrated": replayed.outcome,
            "migrated_status_code": replayed.status_code,
        })
    if replayed.records is None:
        differences.append({"where": "response", "kind": "unreadable", "detail": replayed.problem})
        return differences, abstentions

    excluded = {
        entry["field"] for entry in record["excluded_fields"] if entry["scope"] == "response"
    }
    legacy_records = legacy["records"]
    migrated_records = list(replayed.records)
    needed = [
        _values(value for label, value in item.items() if label not in excluded)
        for item in legacy_records
    ]
    offered = [_values(_leaves(item)) for item in migrated_records]
    pairs = _match(needed, offered)
    unused = [m for m in range(len(migrated_records)) if m not in pairs.values()]

    if legacy_records and not any(needed):
        abstentions.append({"where": "response", "kind": "nothing_to_compare"})

    for index, item in enumerate(legacy_records):
        if index in pairs:
            # Reproduced. An excluded value is still looked for among the values the
            # match did not use: if it is absent, that is a difference, but one
            # confined to a field that cannot be judged.
            spare = offered[pairs[index]] - needed[index]
            for label in item:
                if label in excluded and normalise(item[label]) and normalise(item[label]) not in spare:
                    abstentions.append({
                        "where": "response", "kind": "excluded_field_differs", "field": label,
                        "legacy_value": item[label],
                        "migrated_record": migrated_records[pairs[index]],
                    })
            continue
        # Not reproduced. Point at the unpaired migrated record that shares the most
        # values with it, if any shares one, and at the values that record lacks.
        def shared(m: int) -> int:
            return sum((needed[index] & offered[m]).values())

        closest = max(unused, key=shared, default=None)
        if closest is not None and not shared(closest):
            closest = None
        if closest is not None:
            unused.remove(closest)  # reported here, as the altered form of this record
        lacking = needed[index] - (offered[closest] if closest is not None else Counter())
        differences.append({
            "where": "response", "kind": "record_not_reproduced",
            "legacy_record": item,
            "values_not_found": {
                label: value for label, value in item.items()
                if label not in excluded and normalise(value) in lacking
            },
            "closest_migrated_record": migrated_records[closest] if closest is not None else None,
        })

    for m in unused:
        differences.append({
            "where": "response", "kind": "unexpected_record",
            "migrated_record": migrated_records[m],
        })
    return differences, abstentions


def _plain_delta(delta: dict[str, Any], excluded: set[str]) -> dict[str, Any]:
    """A delta with values normalised and excluded columns removed."""
    def row(table: str, values: dict[str, Any]) -> dict[str, str]:
        return {
            column: normalise(value) for column, value in values.items()
            if f"{table}.{column}" not in excluded
        }

    return {
        table: {
            "inserted": [row(table, item) for item in changes["inserted"]],
            "deleted": [row(table, item) for item in changes["deleted"]],
            "updated": [
                {
                    "key": row(table, item["key"]),
                    "changes": {
                        column: {"before": normalise(change["before"]), "after": normalise(change["after"])}
                        for column, change in item["changes"].items()
                        if f"{table}.{column}" not in excluded
                    },
                }
                for item in changes["updated"]
            ],
        }
        for table, changes in delta.items()
    }


def _compare_database(record: dict[str, Any], replayed: Replay) -> tuple[list, list]:
    excluded = {
        entry["field"] for entry in record["excluded_fields"] if entry["scope"] == "database"
    }
    legacy, migrated = record["db_state"]["delta"], replayed.delta

    kept = _plain_delta(legacy, excluded)
    compared = DeepDiff(kept, _plain_delta(migrated, excluded), ignore_order=True)
    if compared:
        return [{"where": "database", "kind": "delta_differs", "detail": json.loads(compared.to_json())}], []
    rows = [row for changes in kept.values() for row in (*changes["inserted"], *changes["deleted"])]
    if rows and not any(rows) and not any(changes["updated"] for changes in kept.values()):
        return [], [{"where": "database", "kind": "nothing_to_compare"}]
    everything = DeepDiff(_plain_delta(legacy, set()), _plain_delta(migrated, set()), ignore_order=True)
    if everything:
        return [], [{
            "where": "database", "kind": "excluded_field_differs",
            "detail": json.loads(everything.to_json()),
        }]
    return [], []


def compare(record: dict[str, Any], replayed: Replay) -> Comparison:
    """Compare what the migrated system did against what the oracle record expects."""
    response_differences, response_abstentions = _compare_response(record, replayed)
    database_differences, database_abstentions = _compare_database(record, replayed)
    differences = (*response_differences, *database_differences)
    abstentions = (*response_abstentions, *database_abstentions)

    if differences:
        verdict = FAIL
    elif abstentions:
        verdict = INCONCLUSIVE
    else:
        verdict = PASS
    return Comparison(verdict, differences, abstentions)
