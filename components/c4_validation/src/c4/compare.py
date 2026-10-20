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

Response records are matched by value, because the two sides name their fields
differently (a page header against a JSON key). Each legacy record must be reproduced
by a migrated record of its own: every value the legacy record shows must appear in
that migrated record. A migrated record may carry extra values the page did not show.

Where both sides say which database column a field holds, the value must also sit
under that column (labelled comparison). The legacy side learns it at capture, from
the database row each record shows (`legacy_response.columns`); the migrated side
takes it from the endpoint map (`fields`). This is what tells two swapped fields
apart. A field that only one side, or neither, links to a column is compared by value
alone, as before: it can never cause a difference on its own, and the comparison says
which fields were compared each way.
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
    # The legacy fields compared under their column, and those compared by value alone.
    by_label: tuple[str, ...] = ()
    by_value: tuple[str, ...] = ()


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


def _flat(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """A record's plain values by path: "user.login" for {"user": {"login": ...}}."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict):
            flat.update(_flat(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _by_column(values: dict[str, Any], columns: dict[str, str], compared: set) -> dict[str, str]:
    """The values of fields linked to a compared column, keyed by the column."""
    return {
        columns[field]: normalise(value)
        for field, value in values.items()
        if columns.get(field) in compared and normalise(value)
    }


def _match(
    needed: list[Counter], offered: list[Counter],
    wanted: list[dict[str, str]] | None = None, held: list[dict[str, str]] | None = None,
) -> dict[int, int]:
    """Pair each legacy record with a migrated record of its own that reproduces it.

    `wanted` and `held` give, per record, the values of the fields linked to a column
    on both sides: a pair must then agree on every one of them as well.

    Returns legacy index -> migrated index for the largest set of pairs possible,
    so the result does not depend on the order either side lists its records in.
    """
    candidates = [
        [
            m for m, have in enumerate(offered)
            if not need - have and (wanted is None or all(held[m].get(c) == v for c, v in wanted[i].items()))
        ]
        for i, need in enumerate(needed)
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

    if legacy["outcome"] == "no_effect":
        # The legacy system did nothing. Any migrated answer that also does nothing
        # agrees, whether 2xx or a deliberate 4xx rejection; a server error does not.
        agrees = replayed.outcome != "error"
    else:
        agrees = replayed.outcome == legacy["outcome"]
    if not agrees:
        differences.append({
            "where": "response", "kind": "outcome_differs",
            "legacy": legacy["outcome"], "migrated": replayed.outcome,
            "migrated_status_code": replayed.status_code,
        })
    if replayed.records is None:
        if replayed.outcome == "success":
            differences.append({"where": "response", "kind": "unreadable", "detail": replayed.problem})
            return differences, abstentions, (), ()
        # A rejection or error carries no records; whether that outcome is right was
        # decided above.
        replayed = Replay(replayed.status_code, replayed.outcome, (), None, replayed.delta, replayed.columns)

    excluded = {
        entry["field"]: entry["reason"] for entry in record["excluded_fields"] if entry["scope"] == "response"
    }
    legacy_records = legacy["records"]
    migrated_records = list(replayed.records)
    # Labelled comparison where both sides link a field to the same column.
    columns = {label: column for label, column in legacy.get("columns", {}).items() if label not in excluded}
    migrated_columns = dict(replayed.columns)
    compared = set(columns.values()) & set(migrated_columns.values())
    wanted = [_by_column(item, columns, compared) for item in legacy_records]
    held = [_by_column(_flat(item), migrated_columns, compared) for item in migrated_records]
    labels = list(dict.fromkeys(label for item in legacy_records for label in item if label not in excluded))
    by_label = tuple(label for label in labels if columns.get(label) in compared)
    by_value = tuple(label for label in labels if columns.get(label) not in compared)

    needed = [
        _values(value for label, value in item.items() if label not in excluded)
        for item in legacy_records
    ]
    offered = [_values(_leaves(item)) for item in migrated_records]
    pairs = _match(needed, offered, wanted, held)
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
                        "reason": excluded[label],
                        "legacy_value": item[label],
                        # A value that differs on every capture differs on every run too.
                        "migrated_record": VARIES if excluded[label] == "observed_nondeterministic"
                        else migrated_records[pairs[index]],
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
        difference = {
            "where": "response", "kind": "record_not_reproduced",
            "legacy_record": item,
            "values_not_found": {
                label: value for label, value in item.items()
                if label not in excluded and normalise(value) in lacking
            },
            "closest_migrated_record": migrated_records[closest] if closest is not None else None,
        }
        if closest is not None:
            # A value present in the closest record but under another column.
            at_column = {migrated_columns[path]: value for path, value in _flat(migrated_records[closest]).items()
                         if path in migrated_columns}
            misplaced = {
                label: {"column": columns[label], "legacy": item[label], "migrated": at_column.get(columns[label])}
                for label in by_label
                if label in item and normalise(item[label]) and wanted[index].get(columns[label]) != held[closest].get(columns[label])
            }
            if misplaced:
                difference["values_under_another_column"] = misplaced
        differences.append(difference)

    for m in unused:
        differences.append({
            "where": "response", "kind": "unexpected_record",
            "migrated_record": migrated_records[m],
        })
    return differences, abstentions, by_label, by_value


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


# A value a system writes differently on every run, as the current time: it is not
# evidence, and printing it would make identical runs give different reports.
VARIES = "different on every run"
_PATH_KEY = re.compile(r"\['([^']*)'\]|\[(\d+)\]")


def _column_at(path: str) -> str | None:
    """The "table.column" a DeepDiff path into a delta points at, if it points at one."""
    keys = [name if name else int(index) for name, index in _PATH_KEY.findall(path)]
    if len(keys) >= 4 and keys[1] in ("inserted", "deleted"):
        return f"{keys[0]}.{keys[3]}"
    if len(keys) >= 5 and keys[1] == "updated" and keys[3] == "changes":
        return f"{keys[0]}.{keys[4]}"
    return None


def _steady(detail: dict[str, Any], varying: set[str]) -> dict[str, Any]:
    """A delta difference with the migrated values of varying columns left out.

    The legacy value stays: it is the oracle's, and the same on every run.
    """
    if not varying:
        return detail

    def masked(table: str, row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        return {column: VARIES if f"{table}.{column}" in varying else value for column, value in row.items()}

    steady: dict[str, Any] = {}
    for change, places in detail.items():
        if not isinstance(places, dict):
            steady[change] = places
            continue
        steady[change] = {}
        for path, info in places.items():
            table = next(iter(_PATH_KEY.findall(path)), ("", ""))[0]
            if change == "values_changed" and _column_at(path) in varying:
                info = {"old_value": info["old_value"], "new_value": VARIES}
            elif change in ("iterable_item_added", "dictionary_item_added"):
                info = masked(table, info)  # rows only the migrated side wrote
            elif change == "repetition_change" and isinstance(info, dict):
                info = {**info, "value": masked(table, info.get("value"))}
            steady[change][path] = info
    return steady


def _compare_database(record: dict[str, Any], replayed: Replay) -> tuple[list, list]:
    excluded = {
        entry["field"] for entry in record["excluded_fields"] if entry["scope"] == "database"
    }
    varying = {
        entry["field"] for entry in record["excluded_fields"]
        if entry["scope"] == "database" and entry["reason"] in ("observed_nondeterministic", "current_date")
    }
    legacy, migrated = record["db_state"]["delta"], replayed.delta

    def diff(expected: dict[str, Any], actual: dict[str, Any]) -> DeepDiff:
        # Row order carries no meaning, but how many times a row was written does:
        # without report_repetition a row written twice would equal one written once.
        return DeepDiff(expected, actual, ignore_order=True, report_repetition=True)

    kept = _plain_delta(legacy, excluded)
    compared = diff(kept, _plain_delta(migrated, excluded))
    if compared:
        difference = {"where": "database", "kind": "delta_differs", "detail": json.loads(compared.to_json())}
        if not legacy:
            difference["note"] = "the legacy system changed nothing for this input; the migrated system did"
        return [difference], []
    rows = [row for changes in kept.values() for row in (*changes["inserted"], *changes["deleted"])]
    if rows and not any(rows) and not any(changes["updated"] for changes in kept.values()):
        return [], [{"where": "database", "kind": "nothing_to_compare"}]
    everything = diff(_plain_delta(legacy, set()), _plain_delta(migrated, set()))
    if everything:
        return [], [{
            "where": "database", "kind": "excluded_field_differs",
            "detail": _steady(json.loads(everything.to_json()), varying),
        }]
    return [], []


def compare(record: dict[str, Any], replayed: Replay) -> Comparison:
    """Compare what the migrated system did against what the oracle record expects."""
    response_differences, response_abstentions, by_label, by_value = _compare_response(record, replayed)
    database_differences, database_abstentions = _compare_database(record, replayed)
    differences = (*response_differences, *database_differences)
    abstentions = (*response_abstentions, *database_abstentions)

    if differences:
        verdict = FAIL
    elif abstentions:
        verdict = INCONCLUSIVE
    else:
        verdict = PASS
    return Comparison(verdict, differences, abstentions, by_label, by_value)


# ------------------------------------------------------- conflicting expectations


def _as_replay(record: dict[str, Any]) -> Replay:
    """What the legacy system did, presented as if a migrated system had done it."""
    response = record["legacy_response"]
    outcome = "success" if response["outcome"] == "no_effect" else response["outcome"]
    return Replay(
        response["status_code"], outcome, tuple(response["records"]), None, record["db_state"]["delta"],
        response.get("columns", {}),
    )


def _compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether one answer can satisfy both oracle records.

    If what one legacy page did would pass against the other's record, a migrated
    system that does the same satisfies both.
    """
    return compare(a, _as_replay(b)).verdict != FAIL or compare(b, _as_replay(a)).verdict != FAIL


def conflicts(cases: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, list[str]]:
    """Scenarios whose identical request is expected to give incompatible results.

    One endpoint can replace several legacy pages. Two of its scenarios may then send
    exactly the same request to the migrated system while the pages they came from
    did different things. No migrated system can satisfy both, so neither scenario
    can be judged. Returns, per scenario, the scenarios it conflicts with.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for scenario, record in cases:
        request = json.dumps([scenario["endpoint_id"], scenario["migrated_request"]], sort_keys=True)
        groups.setdefault(request, []).append(record)
    found: dict[str, list[str]] = {}
    for records in groups.values():
        for a in records:
            for b in records:
                if a is not b and not _compatible(a, b):
                    found.setdefault(a["scenario_id"], []).append(b["scenario_id"])
    return found


# ------------------------------------------------------------- injection probes
#
# Injection probes are not judged by equivalence (FR13). A migrated system that
# reproduced a legacy injection hole would be equivalent and dangerous, and one that
# closed it would differ. Each probe is judged instead by what it made each system do.
#
# A probe's text is never a value in the seed data. A system that treats it as plain
# text can therefore only find nothing (or records that really contain the text),
# store the text exactly as sent, or reject the request. Anything else means the input
# changed what the query did:
#
#     leak        records shown that do not contain the probe text
#     alteration  a row written or changed without the probe text, or a row deleted
#
# Limit: a probe that changes several rows to its own literal text, as a widened
# UPDATE would, is not detected; only single changes that lose the text are.
#
# A probe is not judged at all when what it did cannot be told apart from what the
# same request does without it (see `not_judged`): the legacy page must have acted on
# that request, and a row written without the probe text is evidence only when the
# map says where the value is stored. A page may store a value changed, as a password
# is stored hashed.

IMPROVEMENT = "security improvement"
SAFE_AS_BEFORE = "safe, as before"
VULNERABILITY_KEPT = "vulnerability kept"
VULNERABILITY_INTRODUCED = "vulnerability introduced"
UNHANDLED = "unhandled input"
NOT_JUDGED = "not judged"


def not_judged(record: dict[str, Any], scenario: dict[str, Any], base: dict[str, Any] | None) -> str | None:
    """Why a probe cannot be judged, or None when it can.

    `base` is the oracle record of the same request without the probe.
    """
    if base is None:
        return "the same request without the probe was not captured"
    if base["legacy_response"]["outcome"] != "success":
        return (
            "the same request without the probe had no effect on the legacy system either, "
            "so what the probe did cannot be told apart from that"
        )
    if _unplaced(scenario):
        probe = scenario["values"][scenario["parameter"]]
        if injection_effects(probe, (), record["db_state"]["delta"]) != injection_effects(
            probe, (), record["db_state"]["delta"], placed=False
        ):
            return (
                f"the map does not say where '{scenario['parameter']}' is stored, so a row "
                "written without the probe text is no evidence either way"
            )
    return None


def _unplaced(scenario: dict[str, Any]) -> bool:
    # A scenario resolved against a map always says which column its parameter
    # relates to, None when the map names none.
    return "column" in scenario and scenario["column"] is None


def injection_effects(
    probe: Any, records: Iterable[dict[str, Any]], delta: dict[str, Any], placed: bool = True
) -> list[dict[str, Any]]:
    """What a probe made a system do beyond treating it as plain text.

    `placed` is False when it is not known where the value is stored: a row written
    without the probe text is then no evidence, and only deleted rows count.
    """
    text = normalise(probe)
    effects: list[dict[str, Any]] = []
    for item in records:
        if text not in {normalise(value) for value in _leaves(item)}:
            effects.append({"kind": "leak", "record": item})
    for table, changes in delta.items():
        for row in changes["inserted"] if placed else ():
            if text not in {normalise(value) for value in row.values()}:
                effects.append({"kind": "alteration", "table": table, "inserted": row})
        for item in changes["updated"] if placed else ():
            if text not in {normalise(change["after"]) for change in item["changes"].values()}:
                effects.append({"kind": "alteration", "table": table, "updated": item})
        for row in changes["deleted"]:
            effects.append({"kind": "alteration", "table": table, "deleted": row})
    return effects


def legacy_injection_behaviour(record: dict[str, Any], scenario: dict[str, Any]) -> tuple[str, list]:
    """What a probe did to the legacy system: vulnerable, broken or no sign of injection."""
    probe = scenario["values"][scenario["parameter"]]
    legacy = record["legacy_response"]
    effects = injection_effects(probe, legacy["records"], record["db_state"]["delta"], placed=not _unplaced(scenario))
    if effects:
        return "vulnerable", effects
    if scenario.get("legacy_table") == "none" and legacy["outcome"] == "no_effect":
        # A page that should store what it is sent stored nothing: the input broke it.
        return "broken", effects
    # Nothing visible. For a page that reads, a query broken by the input and a
    # literal search that finds nothing look the same from outside.
    return "no sign of injection", effects


def judge_probe(record: dict[str, Any], scenario: dict[str, Any], replayed: Replay) -> dict[str, Any]:
    """Label one injection probe from what the legacy and the migrated system did."""
    probe = scenario["values"][scenario["parameter"]]
    legacy = record["legacy_response"]
    legacy_label, legacy_effects = legacy_injection_behaviour(record, scenario)

    migrated_effects = injection_effects(probe, replayed.records or (), replayed.delta, placed=not _unplaced(scenario))
    if replayed.outcome == "error":
        migrated_label, label = "server error", UNHANDLED
    elif migrated_effects:
        migrated_label = "vulnerable"
        label = VULNERABILITY_INTRODUCED if legacy_label == "no sign of injection" else VULNERABILITY_KEPT
    else:
        migrated_label = "safe"
        label = SAFE_AS_BEFORE if legacy_label == "no sign of injection" else IMPROVEMENT

    return {
        "scenario_id": record["scenario_id"],
        "endpoint_id": record["endpoint_id"],
        "parameter": scenario["parameter"],
        "probe": probe,
        "label": label,
        "legacy": {"behaviour": legacy_label, "outcome": legacy["outcome"], "effects": legacy_effects},
        "migrated": {
            "behaviour": migrated_label,
            "status_code": replayed.status_code,
            "effects": migrated_effects,
        },
    }
