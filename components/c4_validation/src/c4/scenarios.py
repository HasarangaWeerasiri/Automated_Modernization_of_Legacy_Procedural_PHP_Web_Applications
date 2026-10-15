"""Stage 1 — scenarios.

A scenario is one use of one endpoint with concrete input values. This module turns
it into the two requests that carry those values: one for the legacy page the
endpoint was recovered from, one for the migrated endpoint itself. Which page, which
form fields and which API parameters is knowledge held by the endpoint map (FR3).

`load_endpoint_map` is the only place that reads an endpoint map file. Everything
else works on the classes below, so a map in another format needs another reader
here and nothing more. The format read today is a stand-in written for C4 while the
real map does not exist yet.

Scenario values are generated, never written by hand (FR4). For every source page of
every endpoint:

    typical    3 sets of normal values: values present in the seed data for pages
               that read, new realistic values for pages that write; plus one value
               absent from the seed data for pages that search
    boundary   per parameter, one at a time: empty, the column's longest value, one
               character over, and left out (other column types get their own edges)
    injection  per parameter, a fixed list of probes that depends on no application

Everything is derived from the endpoint map, the database schema and the seed data,
and nothing random reaches a scenario: choices are made by position in sorted data,
and new values come from Faker seeded with the scenario's own name. The scenarios are
generated once and written to files, so every later run uses the same inputs.

    python -m c4.scenarios
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from faker import Faker
from sqlalchemy import MetaData, create_engine
from sqlalchemy import types as sqltypes
from sqlalchemy.pool import NullPool

from c4.config import Config, ConfigError, System, load_config

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


# --------------------------------------------------------------------- generation

TYPICAL_COUNT = 3
# Classic probes that break, widen or cut off an SQL string built by concatenation.
# None of them deletes or alters data on its own.
INJECTION_PROBES = ("'", "' OR '1'='1", "' OR 1=1 -- ")
# The largest value of a standard signed INT column.
_INT_MAX = 2147483647
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class ColumnFacts:
    """What the schema and the seed data say about one column."""

    kind: str  # text, integer, number, date, datetime, time or other
    max_length: int | None
    # Distinct non-empty values in the seed data, in a fixed order.
    seed_values: tuple[Any, ...]


@dataclass(frozen=True)
class SeedFacts:
    columns: Mapping[str, ColumnFacts]  # "table.column" -> facts
    rows: Mapping[str, tuple[dict[str, Any], ...]]  # table -> seed rows, in a fixed order


def _kind(column_type: sqltypes.TypeEngine) -> str:
    for kind, types in (
        ("integer", sqltypes.Integer),
        ("number", (sqltypes.Numeric, sqltypes.Float)),
        ("datetime", sqltypes.DateTime),
        ("date", sqltypes.Date),
        ("time", sqltypes.Time),
        ("text", sqltypes.String),
    ):
        if isinstance(column_type, types):
            return kind
    return "other"


def seed_facts(system: System) -> SeedFacts:
    """Read column types from the schema and values from the seed data of a database.

    The database must be at its baseline, so the values are the seed's own.
    """
    from c4 import dbstate  # imported here: dbstate needs Docker, the rest of this module does not

    snapshot = dbstate.snapshot(system)
    engine = create_engine(system.db_url, poolclass=NullPool)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    engine.dispose()

    columns: dict[str, ColumnFacts] = {}
    for name, table in metadata.tables.items():
        rows = snapshot.tables[name].rows
        for column in table.columns:
            values = {row[column.name] for row in rows if row[column.name] not in (None, "")}
            columns[f"{name}.{column.name}"] = ColumnFacts(
                kind=_kind(column.type),
                max_length=getattr(column.type, "length", None),
                seed_values=tuple(sorted(values, key=lambda value: (str(type(value)), str(value)))),
            )
    return SeedFacts(columns=columns, rows={name: table.rows for name, table in snapshot.tables.items()})


def _spread(items: list, count: int) -> list:
    """`count` items spread evenly from first to last, without repeats."""
    if len(items) <= count:
        return list(items)
    positions = sorted({round(i * (len(items) - 1) / (count - 1)) for i in range(count)})
    return [items[position] for position in positions]


def _digits(facts: ColumnFacts | None) -> bool:
    texts = [value for value in facts.seed_values if isinstance(value, str)] if facts else []
    return bool(texts) and all(text.isdigit() for text in texts)


def _fill(facts: ColumnFacts | None, length: int) -> str:
    """A value of exactly `length` characters, in the shape of the column's values."""
    return ("9" if _digits(facts) else "x") * length


def _typical_reads(source: Source, seed: SeedFacts) -> list[dict[str, Any]]:
    """Values the seed data holds, so that a page that reads finds something."""
    if not source.parameters:
        return [{}]
    known = [p for p in source.parameters if p.column in seed.columns]
    if not known:
        return []
    tables = {p.column.split(".")[0] for p in known}
    if len(tables) == 1:
        # All parameters filter one table: take whole rows, so the values belong together.
        rows = [
            row for row in seed.rows[tables.pop()]
            if all(row[p.column.split(".")[1]] not in (None, "") for p in known)
        ]
        return [{p.name: row[p.column.split(".")[1]] for p in known} for row in _spread(rows, TYPICAL_COUNT)]
    chosen = {p.name: _spread(list(seed.columns[p.column].seed_values), TYPICAL_COUNT) for p in known}
    count = min(len(values) for values in chosen.values())
    return [{name: values[i] for name, values in chosen.items()} for i in range(count)]


def _absent(facts: ColumnFacts) -> Any:
    """A value of the column's shape that the seed data does not hold."""
    if facts.kind in ("integer", "number"):
        numbers = [value for value in facts.seed_values if isinstance(value, (int, float))]
        return (max(numbers) if numbers else 0) + 1000
    length = facts.max_length or 12
    for digit in "0123456789":
        candidate = digit * length if _digits(facts) else f"absent{digit}"[:length]
        if candidate not in facts.seed_values:
            return candidate
    raise ScenarioError("Every candidate absent value is present in the seed data.")


def _new_value(fake: Faker, facts: ColumnFacts | None) -> Any:
    """A realistic value the seed data does not necessarily hold, shaped like its values."""
    if facts is None:
        return fake.word()
    numbers = [value for value in facts.seed_values if isinstance(value, (int, float))]
    low, high = (min(numbers), max(numbers)) if numbers else (1, 1000)
    if facts.kind == "integer":
        return fake.random_int(int(low), int(high))
    if facts.kind == "number":
        return round(fake.pyfloat(min_value=low, max_value=high + 1), 2)
    if facts.kind == "date":
        return fake.date_between(date(2015, 1, 1), date(2025, 12, 31)).isoformat()
    if facts.kind == "datetime":
        return fake.date_time_between(date(2015, 1, 1), date(2025, 12, 31)).isoformat(sep=" ")
    if facts.kind == "time":
        return fake.time()

    texts = [value for value in facts.seed_values if isinstance(value, str)]
    if texts and all(_EMAIL.fullmatch(text) for text in texts):
        value = fake.email()
    elif _digits(facts):
        value = fake.numerify("#" * round(statistics.median(len(text) for text in texts)))
    else:
        words = round(statistics.median(len(text.split()) for text in texts)) if texts else 2
        value = fake.name() if words <= 3 else fake.sentence(nb_words=words).rstrip(".")
    return value[: facts.max_length] if facts.max_length else value


def _typical_writes(source: Source, seed: SeedFacts) -> list[dict[str, Any]]:
    """New values for a page that writes, so each scenario stores something new."""
    scenarios = []
    for number in range(1, TYPICAL_COUNT + 1):
        fake = Faker("en_US")
        fake.seed_instance(f"{source.source_id}:typical:{number}")
        scenarios.append({p.name: _new_value(fake, seed.columns.get(p.column)) for p in source.parameters})
    return scenarios


_MISSING = object()


def _boundaries(facts: ColumnFacts | None) -> list[tuple[str, Any]]:
    """The edges of one parameter's range, from its column's type and length."""
    if facts is None:
        return [("empty", ""), ("missing", _MISSING)]
    if facts.kind == "text" and facts.max_length:
        return [
            ("empty", ""),
            ("longest", _fill(facts, facts.max_length)),
            ("too_long", _fill(facts, facts.max_length + 1)),
            ("missing", _MISSING),
        ]
    if facts.kind == "text":
        return [("empty", ""), ("very_long", _fill(facts, 1000)), ("missing", _MISSING)]
    if facts.kind == "integer":
        return [("zero", 0), ("negative", -1), ("largest", _INT_MAX), ("missing", _MISSING)]
    if facts.kind == "number":
        return [("zero", 0), ("negative", -1), ("missing", _MISSING)]
    if facts.kind in ("date", "datetime"):
        return [("empty", ""), ("impossible_date", "2020-02-30"), ("missing", _MISSING)]
    return [("empty", ""), ("missing", _MISSING)]


def generate(endpoint_map: EndpointMap, seed: SeedFacts) -> list[dict[str, Any]]:
    """Every scenario for every source page of every endpoint, in a fixed order."""
    scenarios: list[dict[str, Any]] = []

    def add(source: Source, endpoint: Endpoint, scenario_id: str, kind: str, case: str,
            values: dict[str, Any], parameter: str | None = None) -> None:
        scenarios.append({
            "scenario_id": scenario_id,
            "scenario_type": kind,
            "case": case,
            "parameter": parameter,
            "endpoint_id": endpoint.endpoint_id,
            "source_id": source.source_id,
            "values": {name: value for name, value in values.items() if value is not _MISSING},
        })

    for endpoint in endpoint_map.values():
        for source in endpoint.sources.values():
            writes = source.records == "none"
            typical = _typical_writes(source, seed) if writes else _typical_reads(source, seed)
            for number, values in enumerate(typical, start=1):
                add(source, endpoint, f"{source.source_id}_typical_{number:02d}", "typical", "typical", values)

            # The other cases vary one parameter at a time, from the first typical values.
            base = typical[0] if typical else {}
            first_known = next((p for p in source.parameters if p.column in seed.columns), None)
            if not writes and first_known is not None:
                add(source, endpoint, f"{source.source_id}_not_found_01", "typical", "not_found",
                    {**base, first_known.name: _absent(seed.columns[first_known.column])},
                    first_known.name)

            for parameter in source.parameters:
                for case, value in _boundaries(seed.columns.get(parameter.column)):
                    add(source, endpoint, f"{source.source_id}_boundary_{parameter.name}_{case}",
                        "boundary", case, {**base, parameter.name: value}, parameter.name)
            for parameter in source.parameters:
                for number, probe in enumerate(INJECTION_PROBES, start=1):
                    add(source, endpoint, f"{source.source_id}_injection_{parameter.name}_{number:02d}",
                        "injection", "probe", {**base, parameter.name: probe}, parameter.name)
    return scenarios


def write_scenarios(config: Config, scenarios: list[dict[str, Any]]) -> None:
    """Replace the benchmark's scenario files with the generated ones."""
    config.scenario_dir.mkdir(parents=True, exist_ok=True)
    for stale in config.scenario_dir.glob("*.json"):
        stale.unlink()
    for scenario in scenarios:
        text = json.dumps(scenario, indent=2, ensure_ascii=False) + "\n"
        (config.scenario_dir / f"{scenario['scenario_id']}.json").write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m c4.scenarios", description="Generate the scenarios of a benchmark.")
    parser.add_argument("--benchmark", help="benchmark name; defaults to C4_BENCHMARK")
    args = parser.parse_args(argv)

    from c4 import containers, dbstate

    try:
        config = load_config(args.benchmark)
        endpoint_map = load_endpoint_map(config.endpoint_map_file)
        containers.wait_until_ready(config, timeout=0)
        dbstate.reset(config.legacy)
        scenarios = generate(endpoint_map, seed_facts(config.legacy))
        write_scenarios(config, scenarios)
    except (
        ConfigError, EndpointMapError, ScenarioError,
        containers.DockerUnavailableError, containers.NotReadyError,
        dbstate.BaselineError, dbstate.ResetError,
    ) as error:
        print(error, file=sys.stderr)
        return 1

    print(f"benchmark: {config.benchmark}")
    for endpoint in endpoint_map.values():
        for source_id in endpoint.sources:
            kinds = [s["scenario_type"] for s in scenarios if s["source_id"] == source_id]
            counts = ", ".join(f"{kinds.count(kind)} {kind}" for kind in ("typical", "boundary", "injection") if kinds.count(kind))
            print(f"  {source_id}: {counts}")
    print(f"{len(scenarios)} scenarios -> {config.scenario_dir.relative_to(config.root).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
