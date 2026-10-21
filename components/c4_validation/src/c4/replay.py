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

from dataclasses import dataclass, field
from typing import Any, Mapping

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
    # The database column each response field holds, by field path, as the endpoint
    # map gives it; empty when the map says nothing.
    columns: Mapping[str, str] = field(default_factory=dict)
    # The migrated database's clock just before and just after the request (see
    # dbstate.window); None when it was not read.
    window: Mapping[str, list[str]] | None = None


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


def _token(config: Config, session: requests.Session, scenario: dict[str, Any], login: dict[str, Any]) -> str:
    """Log in on the migrated system as the same seed user, and return the header to send."""
    response = session.request(
        login["method"],
        config.migrated.base_url + login["path"],
        params=login.get("params") or None,
        json=login.get("body"),
        timeout=config.request_timeout,
        allow_redirects=False,
    )
    try:
        found: Any = response.json()
        for key in filter(None, login["token_at"].split(".")):
            found = found[key]
    except (ValueError, KeyError, TypeError):
        found = None
    if response.status_code >= 400 or not isinstance(found, str) or not found:
        raise ReplayError(
            f"{scenario['scenario_id']}: logging in as '{scenario['login']}' on the migrated system "
            f"did not give a token (status {response.status_code})."
        )
    return f"Bearer {found}"


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
        if scenario.get("migrated_login"):
            session.headers["Authorization"] = _token(config, session, scenario, scenario["migrated_login"])
            # Measured from after the login, as on the legacy side.
            before = dbstate.snapshot(config.migrated)
        started = dbstate.clock(config.migrated)
        response = session.request(
            request["method"],
            config.migrated.base_url + request["path"],
            params=request.get("params") or None,
            json=request.get("body"),
            timeout=config.request_timeout,
            allow_redirects=False,
        )
        ended = dbstate.clock(config.migrated)
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
        columns=request.get("fields") or {},
        window=dbstate.window(started, ended),
    )
