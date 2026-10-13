"""Stage 1 — scenarios.

A scenario is one use of one endpoint with concrete input values. This module turns
it into the two requests that carry those values: one for the legacy page the
endpoint was recovered from, one for the migrated endpoint itself. Which page, which
form fields and which API parameters is knowledge held by the endpoint map (FR3).

`load_endpoint_map` is the only place that reads an endpoint map file. Everything
else works on the classes below, so a map in another format needs another reader
here and nothing more. The format read today is a stand-in written for C4 while the
real map does not exist yet.

Scenario values are still written by hand, one small file per scenario. Generating
them is the next addition to this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from c4.config import Config

STAND_IN_FORMAT = "c4-stand-in/1"

_LOCATIONS = ("query", "body", "path")
_LEGACY_LOCATIONS = ("query", "body")


class EndpointMapError(ValueError):
    """The endpoint map cannot be read or does not hold what the engine needs."""


class ScenarioError(ValueError):
    """A scenario does not fit the endpoint map."""


@dataclass(frozen=True)
class Parameter:
    """One input of an endpoint, under its name on each side."""

    name: str
    location: str
    legacy_name: str
    legacy_location: str
    # The database column the value is compared with or written to, "table.column".
    column: str | None


@dataclass(frozen=True)
class Source:
    """One legacy page an endpoint was recovered from.

    `records` says where the endpoint's records are on that page: "only" when the
    page holds exactly one table, a number to pick one table of several, "none"
    when the endpoint shows no records.
    """

    source_id: str
    legacy_method: str
    legacy_path: str
    fixed: Mapping[str, str]
    records: str | int
    parameters: tuple[Parameter, ...]


@dataclass(frozen=True)
class Endpoint:
    """One migrated endpoint. Several legacy pages can stand behind it."""

    endpoint_id: str
    method: str
    path: str
    # Where the list of records sits in the JSON response; None for no records.
    records_at: str | None
    sources: Mapping[str, Source]


EndpointMap = Mapping[str, Endpoint]


def _require(entry: Mapping[str, Any], keys: tuple[str, ...], what: str) -> None:
    missing = [key for key in keys if key not in entry]
    if missing:
        raise EndpointMapError(f"{what} does not set: {', '.join(missing)}.")


def _read_stand_in(document: Mapping[str, Any]) -> dict[str, Endpoint]:
    endpoints: dict[str, Endpoint] = {}
    for entry in document.get("endpoints", []):
        _require(entry, ("endpoint_id", "method", "path", "records_at", "sources"), "An endpoint")
        endpoint_id = entry["endpoint_id"]
        if endpoint_id in endpoints:
            raise EndpointMapError(f"Endpoint '{endpoint_id}' is listed twice.")

        sources: dict[str, Source] = {}
        for item in entry["sources"]:
            where = f"A source of '{endpoint_id}'"
            _require(item, ("source_id", "legacy", "parameters"), where)
            _require(item["legacy"], ("method", "path", "records"), where)
            if item["source_id"] in sources:
                raise EndpointMapError(f"{where} is listed twice: '{item['source_id']}'.")

            records = item["legacy"]["records"]
            if isinstance(records, dict):
                records = records.get("table")
            if records not in ("only", "none") and type(records) is not int:
                raise EndpointMapError(
                    f"{where} must say where its records are: \"only\", \"none\" or a table number."
                )

            parameters = []
            for parameter in item["parameters"]:
                _require(parameter, ("name", "in", "legacy_name", "legacy_in"), f"A parameter of '{endpoint_id}'")
                if parameter["in"] not in _LOCATIONS or parameter["legacy_in"] not in _LEGACY_LOCATIONS:
                    raise EndpointMapError(
                        f"Parameter '{parameter['name']}' of '{endpoint_id}' has an unknown location."
                    )
                parameters.append(Parameter(
                    name=parameter["name"],
                    location=parameter["in"],
                    legacy_name=parameter["legacy_name"],
                    legacy_location=parameter["legacy_in"],
                    column=parameter.get("column"),
                ))

            sources[item["source_id"]] = Source(
                source_id=item["source_id"],
                legacy_method=item["legacy"]["method"],
                legacy_path=item["legacy"]["path"],
                fixed=dict(item["legacy"].get("fixed", {})),
                records=records,
                parameters=tuple(parameters),
            )

        endpoints[endpoint_id] = Endpoint(
            endpoint_id=endpoint_id,
            method=entry["method"],
            path=entry["path"],
            records_at=entry["records_at"],
            sources=sources,
        )
    return endpoints


# One reader per endpoint map format. The map C1 produces gets its own entry here.
_READERS = {STAND_IN_FORMAT: _read_stand_in}


def load_endpoint_map(path: Path) -> EndpointMap:
    """Read an endpoint map file into the form the engine works on."""
    if not path.is_file():
        raise EndpointMapError(f"No endpoint map at {path}.")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise EndpointMapError(f"{path.name} is not valid JSON: {error}") from None
    try:
        reader = _READERS[document.get("format")]
    except (KeyError, AttributeError, TypeError):
        raise EndpointMapError(
            f"{path.name} has no known format. Known: {', '.join(_READERS)}."
        ) from None
    return reader(document)


def resolve(endpoint_map: EndpointMap, scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a scenario's values into the request for each system.

    A parameter the scenario gives no value for is left out of both requests.
    """
    missing = [key for key in ("scenario_id", "scenario_type", "endpoint_id", "source_id") if key not in scenario]
    if missing:
        raise ScenarioError(f"A scenario does not set: {', '.join(missing)}.")
    scenario_id = scenario["scenario_id"]
    try:
        endpoint = endpoint_map[scenario["endpoint_id"]]
        source = endpoint.sources[scenario["source_id"]]
    except KeyError as unknown:
        raise ScenarioError(f"{scenario_id}: the endpoint map has no {unknown}.") from None

    values = dict(scenario.get("values", {}))
    known = {parameter.name for parameter in source.parameters}
    if unknown := sorted(set(values) - known):
        raise ScenarioError(f"{scenario_id}: no such parameter: {', '.join(unknown)}.")

    # A legacy page reads its fixed fields from where its method carries data.
    fixed_location = "body" if source.legacy_method.upper() == "POST" else "query"
    legacy = {"query": {}, "body": {}}
    legacy[fixed_location].update(source.fixed)
    migrated = {"query": {}, "body": {}}
    path = endpoint.path
    for parameter in source.parameters:
        if parameter.name not in values:
            continue
        value = values[parameter.name]
        legacy[parameter.legacy_location][parameter.legacy_name] = value
        if parameter.location == "path":
            path = path.replace("{" + parameter.name + "}", str(value))
        else:
            migrated[parameter.location][parameter.name] = value

    resolved: dict[str, Any] = {
        "scenario_id": scenario_id,
        "scenario_type": scenario["scenario_type"],
        "endpoint_id": endpoint.endpoint_id,
        "legacy_request": {
            "method": source.legacy_method,
            "path": source.legacy_path,
            "params": legacy["query"],
            "body": legacy["body"] or None,
        },
        "migrated_request": {
            "method": endpoint.method,
            "path": path,
            "params": migrated["query"],
            "body": migrated["body"] or None,
            "records_at": endpoint.records_at,
        },
    }
    if source.records != "only":
        resolved["legacy_table"] = source.records
    return resolved


def load_all(config: Config) -> list[dict[str, Any]]:
    """Every scenario of the benchmark, resolved against its endpoint map."""
    endpoint_map = load_endpoint_map(config.endpoint_map_file)
    resolved = []
    for file in sorted(config.scenario_dir.glob("*.json")):
        try:
            scenario = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ScenarioError(f"{file.name} is not valid JSON: {error}") from None
        resolved.append(resolve(endpoint_map, scenario))
    return resolved
