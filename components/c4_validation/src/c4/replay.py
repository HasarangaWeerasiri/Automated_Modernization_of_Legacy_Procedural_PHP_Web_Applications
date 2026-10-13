"""Stage 4 — reset and replay (LOOP 2).

Reset → snapshot → drive the MIGRATED system over HTTP → snapshot.

A scenario is replayed from the same database baseline its oracle record was
captured from. That is checked, not assumed: the record carries the fingerprint of
its baseline, and replay refuses to run against a migrated database that does not
match it.

This is the walking-skeleton version. The request to send to the migrated endpoint
is read from the scenario file, which stands in for the endpoint map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from c4 import dbstate
from c4.capture import outcome_of
from c4.config import Config


class ReplayError(RuntimeError):
    """A scenario could not be replayed under the conditions its oracle requires."""


@dataclass(frozen=True)
class Replay:
    """What one run of a scenario against the migrated system showed."""

    status_code: int
    outcome: str
    # None when the response held no readable records; `problem` then says why.
    records: tuple[dict[str, Any], ...] | None
    problem: str | None
    delta: dict[str, dict[str, list]]


def _records(response: requests.Response, records_at: str) -> tuple[tuple | None, str | None]:
    try:
        found: Any = response.json()
    except ValueError:
        return None, "the response body is not JSON"
    for key in filter(None, records_at.split(".")):
        if not isinstance(found, dict) or key not in found:
            return None, f"the response has nothing at '{records_at}'"
        found = found[key]
    if isinstance(found, dict):
        found = [found]
    if not isinstance(found, list) or not all(isinstance(item, dict) for item in found):
        return None, f"the value at '{records_at or 'the top level'}' is not a list of records"
    return tuple(found), None


def replay(
    config: Config,
    scenario: dict[str, Any],
    record: dict[str, Any],
    baselines: dict[str, dbstate.Snapshot],
) -> Replay:
    """Run one scenario against the migrated system from its oracle's baseline."""
    dbstate.ensure_baseline(config.legacy, baselines["legacy"])
    before = dbstate.ensure_baseline(config.migrated, baselines["migrated"])
    if before.data_fingerprint != record["db_state"]["baseline_fingerprint"]:
        raise ReplayError(
            f"{record['scenario_id']}: the migrated database does not start from the "
            "baseline this oracle record was captured from."
        )

    request = scenario["migrated_request"]
    session = requests.Session()  # a fresh session: no cookies carried between runs
    session.trust_env = False
    try:
        response = session.request(
            request["method"],
            config.migrated.base_url + request["path"],
            params=request.get("params") or None,
            json=request.get("body"),
            timeout=config.request_timeout,
            allow_redirects=False,
        )
    finally:
        session.close()

    after = dbstate.snapshot(config.migrated)
    records_at = request.get("records_at", "")
    if records_at is None:
        # The endpoint returns no records, as a write that only reports success does.
        records, problem = (), None
    else:
        records, problem = _records(response, records_at)
    return Replay(
        status_code=response.status_code,
        outcome=outcome_of(response.status_code),
        records=records,
        problem=problem,
        delta=dbstate.delta(before, after),
    )
