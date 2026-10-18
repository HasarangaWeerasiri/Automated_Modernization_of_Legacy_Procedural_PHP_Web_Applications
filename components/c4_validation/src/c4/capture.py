"""Stage 2 — capture (LOOP 1).

Reset → snapshot → drive the LEGACY system over HTTP → snapshot → extract values →
assemble one oracle record.

The record is the expectation the migrated system will be held to. Nothing in it is
authored: the response values and the database delta are what the legacy system was
observed to do. That is what makes the oracle derived.

Every scenario is captured more than once from the same baseline. A value that
differs between captures cannot be expected of any system, so it is listed as
excluded instead of being compared: a value the page shows, or a database column
the scenario writes, such as the current time stored with a new row. The captures
are sent at least CAPTURE_GAP seconds apart, so a value taken from a clock that
counts in seconds cannot come out the same twice by chance. A value that changes
more slowly, such as today's date, is not caught this way.

A column whose values occur nowhere in the database was computed by the page, as
"Cancelled by Doctor" is computed from two status flags. A migrated API may well
return the inputs instead and leave the wording to its frontend, so such a column is
listed as excluded too: reproduced, it passes; not reproduced, it cannot be judged.

    python -m c4.capture [scenario_id ...]

A scenario arrives already resolved against the endpoint map (see scenarios.py), so
it carries the legacy request to send. Its `legacy_table` says where the endpoint's
records are on the page: left out, the page must hold exactly one table; a number
picks one table of several; "none" means the endpoint shows no records, as a form
submission that only writes to the database does.

The outcome of a legacy request is judged by its effect, not by its status code:
procedural PHP answers 200 to almost everything, success or failure.

    success    the page showed records, or the database changed
    no_effect  neither: the page showed only a message, or nothing at all
    rejection  status 4xx
    error      status 5xx

A page expected to show records that shows a message instead, such as "No entries
found", showed no data. A page that shows neither a table nor a message is refused:
the engine cannot tell "no data" from data it is unable to read.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from c4 import containers, dbstate, scenarios
from c4.config import Config, ConfigError, load_config
from c4.extract import extract_messages, extract_tables

CAPTURE_RUNS = 2
# Seconds between the requests of two captures: more than one tick of a clock that
# counts in seconds, with room for a request that takes longer to arrive than the other.
CAPTURE_GAP = 2.0


class CaptureError(RuntimeError):
    """A scenario could not be turned into a trustworthy oracle record."""


@dataclass(frozen=True)
class Observation:
    """What one run of a scenario against the legacy system showed."""

    status_code: int
    records: tuple[dict[str, str], ...]
    delta: dict[str, dict[str, list]]
    messages: tuple[str, ...] = ()
    # Whether the page showed the endpoint's records, even an empty list of them.
    shown: bool = True
    # When the request was sent, on a clock that only moves forward.
    sent_at: float = 0.0


def _observe(
    config: Config, scenario: dict[str, Any], baselines: dict[str, dbstate.Snapshot],
    not_before: float = 0.0,
) -> Observation:
    # Both databases are brought to the baseline before every scenario, although
    # only the legacy system is driven here.
    dbstate.ensure_baseline(config.migrated, baselines["migrated"])
    before = dbstate.ensure_baseline(config.legacy, baselines["legacy"])

    request = scenario["legacy_request"]
    session = requests.Session()  # a fresh session: no cookies carried between runs
    session.trust_env = False
    try:
        if scenario.get("legacy_login"):
            _log_in(config, session, scenario["scenario_id"], scenario["login"], scenario["legacy_login"])
            # Logging in may itself write, a last-login time for instance. The
            # scenario's change is measured from after it.
            before = dbstate.snapshot(config.legacy)
        time.sleep(max(0.0, not_before - time.monotonic()))
        sent_at = time.monotonic()
        response = session.request(
            request["method"],
            config.legacy.base_url + request["path"],
            params=request.get("params") or None,
            data=request.get("body") or None,
            timeout=config.request_timeout,
            allow_redirects=False,
        )
    finally:
        session.close()

    after = dbstate.snapshot(config.legacy)

    tables = extract_tables(response.content)
    messages = tuple(extract_messages(response.content))
    chosen = scenario.get("legacy_table", "only")
    shown = chosen != "none"
    if chosen == "none":
        records: tuple[dict[str, str], ...] = ()
    elif not tables:
        if not messages:
            raise CaptureError(
                f"{scenario['scenario_id']}: the page shows neither a table nor a message, "
                "so it cannot be told apart from a page whose data could not be read."
            )
        records, shown = (), False  # a message in place of the records: no data shown
    elif chosen == "only":
        if len(tables) != 1:
            # Sound-or-abstain: with no table, or several and no pointer to the
            # right one, any choice made here would be a guess.
            raise CaptureError(
                f"{scenario['scenario_id']}: the page shows {len(tables)} tables and the "
                "scenario does not say which one holds this endpoint's data."
            )
        records = tables[0].records
    elif chosen in range(len(tables)):
        records = tables[chosen].records
    else:
        raise CaptureError(
            f"{scenario['scenario_id']}: the scenario points at table {chosen!r}, "
            f"but the page shows {len(tables)} tables."
        )

    return Observation(
        status_code=response.status_code,
        records=records,
        delta=dbstate.delta(before, after),
        messages=messages,
        shown=shown,
        sent_at=sent_at,
    )


def _log_in(config: Config, session: requests.Session, scenario_id: str, name: str, login: dict[str, Any]) -> None:
    """Log in on the legacy system as the seed user a page needs."""
    response = session.request(
        login["method"],
        config.legacy.base_url + login["path"],
        params=login.get("params") or None,
        data=login.get("body") or None,
        timeout=config.request_timeout,
        allow_redirects=False,
    )
    expected = login.get("expect_redirect")
    redirected = response.headers.get("Location", "")
    if response.status_code >= 400 or (expected and expected not in redirected):
        raise CaptureError(
            f"{scenario_id}: logging in as '{name}' on the legacy system did not succeed "
            f"(status {response.status_code}{', redirected to ' + redirected if redirected else ''})."
        )


def _differing_columns(first: dict[str, Any], other: dict[str, Any]) -> set[str] | None:
    """The columns, "table.column", whose values differ between two deltas of one shape.

    Two deltas have one shape when they change the same tables, insert, delete and
    update the same number of rows, and update the same rows and columns. None when
    they do not: then more than single values differ.
    """
    if first.keys() != other.keys():
        return None
    columns: set[str] = set()
    for table, changes in first.items():
        others = other[table]
        for kind in ("inserted", "deleted"):
            if len(changes[kind]) != len(others[kind]):
                return None
            for a, b in zip(changes[kind], others[kind]):
                if a.keys() != b.keys():
                    return None
                columns.update(f"{table}.{column}" for column in a if a[column] != b[column])
        if len(changes["updated"]) != len(others["updated"]):
            return None
        for a, b in zip(changes["updated"], others["updated"]):
            if a["key"] != b["key"] or a["changes"].keys() != b["changes"].keys():
                return None
            columns.update(
                f"{table}.{column}" for column, change in a["changes"].items() if change != b["changes"][column]
            )
    return columns


def _volatile(scenario_id: str, observations: list[Observation]) -> tuple[list[str], list[str]]:
    """Labels and database columns whose value changed between captures of the same scenario."""
    first, *others = observations
    labels: set[str] = set()
    columns: set[str] = set()
    for other in others:
        same_shape = len(other.records) == len(first.records) and all(
            a.keys() == b.keys() for a, b in zip(first.records, other.records)
        )
        changed = _differing_columns(first.delta, other.delta)
        if (
            other.status_code != first.status_code
            or not same_shape
            or changed is None
            or other.messages != first.messages
            or other.shown != first.shown
        ):
            # More than single values differ, so there is no stable behaviour to record.
            raise CaptureError(
                f"{scenario_id}: the legacy system did not behave the same way in "
                f"{len(observations)} captures from the same baseline."
            )
        columns |= changed
        for a, b in zip(first.records, other.records):
            labels.update(label for label in a if a[label] != b[label])
    order = first.records[0].keys() if first.records else ()
    return [label for label in order if label in labels], sorted(columns)


def _computed_labels(
    observed: Observation, baseline: dbstate.Snapshot, skip: list[str]
) -> list[str]:
    """Labels whose shown values occur nowhere in the database: computed by the page."""
    from c4.compare import normalise  # here, not at the top: compare imports this module's users

    tables = getattr(baseline, "tables", None)
    if not tables:
        return []  # without database facts nothing can be called computed
    stored = {
        normalise(value)
        for table in tables.values() for row in table.rows for value in row.values()
    }
    for changes in observed.delta.values():
        for row in (*changes["inserted"], *changes["deleted"]):
            stored.update(normalise(value) for value in row.values())
        for item in changes["updated"]:
            for change in item["changes"].values():
                stored.update((normalise(change["before"]), normalise(change["after"])))

    labels = observed.records[0].keys() if observed.records else ()
    computed = []
    for label in labels:
        shown = {normalise(record.get(label)) for record in observed.records} - {""}
        if label not in skip and shown and not shown & stored:
            computed.append(label)
    return computed


def outcome_of(status_code: int) -> str:
    """The class of result a status code stands for.

    Used as it is for the migrated system, whose status codes are deliberate.
    Outcomes are compared, not status codes: a legacy 200 or 302 and a migrated 201
    are the same outcome.
    """
    if status_code < 400:
        return "success"
    return "rejection" if status_code < 500 else "error"


def legacy_outcome(observation: Observation) -> str:
    """The outcome of a legacy request, judged by what it did."""
    by_status = outcome_of(observation.status_code)
    if by_status != "success":
        return by_status
    return "success" if observation.shown or observation.delta else "no_effect"


def capture(
    config: Config, scenario: dict[str, Any], baselines: dict[str, dbstate.Snapshot]
) -> dict[str, Any]:
    """Observe one scenario on the legacy system and return its oracle record."""
    observations: list[Observation] = []
    for _ in range(CAPTURE_RUNS):
        not_before = observations[-1].sent_at + CAPTURE_GAP if observations else 0.0
        observations.append(_observe(config, scenario, baselines, not_before=not_before))
    volatile, volatile_columns = _volatile(scenario["scenario_id"], observations)
    observed = observations[0]
    computed = _computed_labels(observed, baselines["legacy"], volatile)
    request = scenario["legacy_request"]
    return {
        "endpoint_id": scenario["endpoint_id"],
        "legacy_url": request["path"],
        "scenario_id": scenario["scenario_id"],
        "scenario_type": scenario["scenario_type"],
        "input": {
            "method": request["method"],
            "params": request.get("params") or {},
            "body": request.get("body"),
        },
        "legacy_response": {
            "status_code": observed.status_code,
            "outcome": legacy_outcome(observed),
            "records": list(observed.records),
            # What the page told the user in a pop-up. Kept for the reader; its
            # wording is presentation and is not compared.
            "messages": list(observed.messages),
        },
        "db_state": {
            # The content fingerprint, so replay can confirm that the migrated
            # database starts from the same logical baseline.
            "baseline_fingerprint": baselines["legacy"].data_fingerprint,
            "delta": observed.delta,
        },
        "excluded_fields": [
            {"scope": "response", "field": label, "reason": "observed_nondeterministic"}
            for label in volatile
        ] + [
            {"scope": "response", "field": label, "reason": "computed_on_page"}
            for label in computed
        ] + [
            {"scope": "database", "field": column, "reason": "observed_nondeterministic"}
            for column in volatile_columns
        ],
        "capture_runs": CAPTURE_RUNS,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_record(config: Config, record: dict[str, Any]) -> Path:
    """Store an oracle record, keeping the stored one if nothing observed has changed.

    Capturing again and seeing the same behaviour must not rewrite the evidence, so
    `captured_at` is the time the stored behaviour was first observed.
    """
    config.oracle_dir.mkdir(parents=True, exist_ok=True)
    path = config.oracle_dir / f"{record['scenario_id']}.json"
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if {**stored, "captured_at": None} == {**record, "captured_at": None}:
            return path
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4.capture", description="Capture oracle records from the legacy system.")
    parser.add_argument("scenario_id", nargs="*", help="scenarios to capture; default: all")
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.benchmark)
        selected = [
            scenario for scenario in scenarios.load_all(config)
            if not args.scenario_id or scenario["scenario_id"] in args.scenario_id
        ]
        if not selected:
            raise CaptureError(f"No matching scenario in {config.scenario_dir}.")
        containers.wait_until_ready(config, timeout=0)
        baselines = {system.name: dbstate.reset(system) for system in config.systems}

        print(f"benchmark: {config.benchmark}")
        for scenario in selected:
            record = capture(config, scenario, baselines)
            path = write_record(config, record)
            changed = ", ".join(record["db_state"]["delta"]) or "none"
            print(
                f"  {record['scenario_id']}: {len(record['legacy_response']['records'])} record(s), "
                f"database change: {changed} -> {path.relative_to(config.root).as_posix()}"
            )
    except (
        CaptureError, ConfigError, scenarios.EndpointMapError, scenarios.ScenarioError,
        containers.DockerUnavailableError, containers.NotReadyError,
        dbstate.BaselineError, dbstate.ResetError, dbstate.StructureChangedError,
        requests.RequestException,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
