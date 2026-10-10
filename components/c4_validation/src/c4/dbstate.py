"""Database state: reset, snapshot, delta.

The correctness foundation for SO3, NFR1 and NFR2: every scenario must run from an
identical, verified baseline on both systems.

A snapshot covers every table, because a write to the wrong table is exactly the
defect a narrower snapshot cannot see. Only the delta between two snapshots is ever
stored, so the actual change stays visible.

Nothing here knows which statements an application runs. Whether a database is still
at its baseline is decided by looking at the database, never by assuming that a
request was read-only.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cached_property, lru_cache
from typing import Any, Callable, Iterable, Mapping

import pandas as pd
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.pool import NullPool

from c4.config import SEED_PATH_IN_CONTAINER, System
from c4.containers import docker_client


class ResetError(RuntimeError):
    """The seed dump could not be reloaded."""


class BaselineError(RuntimeError):
    """A database is not at its baseline even after a reload."""


class StructureChangedError(RuntimeError):
    """Tables or columns differ between two snapshots, so rows cannot be compared."""


class UnsupportedDatabaseError(RuntimeError):
    """No implementation exists for this database engine."""


# --------------------------------------------------------------------------- values


def _plain(value: Any) -> Any:
    """Reduce a database value to a JSON type, the same way on every engine."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        # How drivers return a TIME column.
        seconds = int(value.total_seconds())
        sign, seconds = ("-", -seconds) if seconds < 0 else ("", seconds)
        return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    # Guessing a representation here could make two different values look equal.
    raise TypeError(f"No stable representation for a value of type {type(value).__name__}.")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------ snapshots


@dataclass(frozen=True)
class TableState:
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    definition: tuple[str, ...]
    auto_increment: int | None
    rows: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        # Row order from a database carries no meaning; a fixed order makes
        # snapshots comparable and their fingerprints repeatable.
        object.__setattr__(self, "rows", tuple(sorted(self.rows, key=_canonical)))


@dataclass(frozen=True)
class Snapshot:
    tables: Mapping[str, TableState]

    @cached_property
    def data_fingerprint(self) -> str:
        """Identifies the logical content: table names, column names and rows.

        Comparable between the legacy and the migrated database.
        """
        return _digest({
            name: {"columns": table.columns, "rows": table.rows}
            for name, table in self.tables.items()
        })

    @cached_property
    def state_fingerprint(self) -> str:
        """Identifies everything that can influence the next request.

        Besides the rows, this covers column definitions, keys and auto-increment
        counters. A failed insert consumes a counter value without leaving a row,
        so rows alone cannot show that a database has left its baseline.
        Comparable only between snapshots of the same database.
        """
        return _digest({
            name: {
                "definition": table.definition,
                "primary_key": table.primary_key,
                "auto_increment": table.auto_increment,
                "rows": table.rows,
            }
            for name, table in self.tables.items()
        })


@lru_cache
def _engine(url: str) -> Engine:
    # No pooling: a snapshot must never be served through a connection that
    # predates a reload.
    return create_engine(url, poolclass=NullPool)


_AUTO_INCREMENT = re.compile(r"\bAUTO_INCREMENT=(\d+)")


def _mysql_auto_increment(connection: Connection, tables: Iterable[str]) -> dict[str, int | None]:
    # SHOW CREATE TABLE, not information_schema: on MySQL 8 the latter reports a
    # stale counter for a table that has not been opened since the server started.
    quote = connection.dialect.identifier_preparer.quote
    counters: dict[str, int | None] = {}
    for name in tables:
        ddl = connection.execute(text(f"SHOW CREATE TABLE {quote(name)}")).one()[1]
        options = next(line for line in reversed(ddl.splitlines()) if line.startswith(")"))
        match = _AUTO_INCREMENT.search(options)
        counters[name] = int(match.group(1)) if match else None
    return counters


_RELOAD_MYSQL = r"""
export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
mysql -uroot -e "DROP DATABASE IF EXISTS \`$C4_DB\`; CREATE DATABASE \`$C4_DB\`" &&
mysql -uroot "$C4_DB" < "$C4_SEED"
"""


def _mysql_reload(system: System) -> None:
    # The dump is a multi-statement script with client directives, so it is loaded
    # by the mysql client inside the database container, exactly as the image
    # loaded it when the container was first created. Dropping the database first
    # removes every object, including any the dump does not itself drop.
    client = docker_client()
    try:
        result = client.containers.get(system.db_container).exec_run(
            ["sh", "-c", _RELOAD_MYSQL],
            environment={"C4_DB": system.db_name, "C4_SEED": SEED_PATH_IN_CONTAINER},
        )
    finally:
        client.close()
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="replace").strip()
        raise ResetError(f"Reloading the {system.name} database failed:\n{output}")


# Everything that differs between database engines is collected here (NFR4).
_COUNTER_READERS: dict[str, Callable[[Connection, Iterable[str]], dict[str, int | None]]] = {
    "mysql": _mysql_auto_increment,
    "mariadb": _mysql_auto_increment,
}
_RELOADERS: dict[str, Callable[[System], None]] = {
    "mysql": _mysql_reload,
    "mariadb": _mysql_reload,
}


def _for_engine(registry: dict[str, Callable], system: System, purpose: str) -> Callable:
    backend = make_url(system.db_url).get_backend_name()
    try:
        return registry[backend]
    except KeyError:
        raise UnsupportedDatabaseError(
            f"No {purpose} for database engine '{backend}' ({system.name} system)."
        ) from None


def snapshot(system: System) -> Snapshot:
    """Read the complete state of one system's database."""
    read_counters = _for_engine(_COUNTER_READERS, system, "auto-increment reader")
    with _engine(system.db_url).connect() as connection:
        metadata = MetaData()
        metadata.reflect(bind=connection)
        names = sorted(metadata.tables)
        counters = read_counters(connection, names)
        tables = {}
        for name in names:
            table = metadata.tables[name]
            tables[name] = TableState(
                columns=tuple(column.name for column in table.columns),
                primary_key=tuple(column.name for column in table.primary_key.columns),
                definition=tuple(
                    f"{column.name} {column.type.compile(dialect=connection.dialect)} "
                    f"{'NULL' if column.nullable else 'NOT NULL'}"
                    for column in table.columns
                ),
                auto_increment=counters[name],
                rows=tuple(
                    {column: _plain(value) for column, value in row.items()}
                    for row in connection.execute(select(table)).mappings()
                ),
            )
    return Snapshot(tables)


# ---------------------------------------------------------------------------- reset


def reset(system: System) -> Snapshot:
    """Reload the seed dump and return the resulting state."""
    _for_engine(_RELOADERS, system, "reload procedure")(system)
    return snapshot(system)


def ensure_baseline(system: System, baseline: Snapshot) -> Snapshot:
    """Guarantee the database is at its baseline, reloading only if it is not.

    The check is the guarantee: a database whose state fingerprint equals the
    baseline's is at the baseline, whatever ran before. A reload that does not
    restore the fingerprint is an error, never something to continue past.
    """
    current = snapshot(system)
    if current.state_fingerprint == baseline.state_fingerprint:
        return current
    restored = reset(system)
    if restored.state_fingerprint != baseline.state_fingerprint:
        differing = sorted(
            name
            for name in set(baseline.tables) | set(restored.tables)
            if baseline.tables.get(name) != restored.tables.get(name)
        )
        raise BaselineError(
            f"The {system.name} database is not at its baseline after a reload. "
            f"Differing tables: {', '.join(differing)}."
        )
    return restored


# ---------------------------------------------------------------------------- delta


def _frame(table: TableState) -> pd.DataFrame:
    rows = list(table.rows)
    frame = pd.DataFrame({
        "row": pd.Series([_canonical(row) for row in rows], dtype=object),
        "data": pd.Series(rows, dtype=object),
    })
    if table.primary_key:
        frame["key"] = pd.Series(
            [_canonical({column: row[column] for column in table.primary_key}) for row in rows],
            dtype=object,
        )
    else:
        # Without a key a row is identified by its whole content, and identical
        # rows by how many of them there are.
        occurrence = frame.groupby("row").cumcount().astype(str)
        frame["key"] = (frame["row"] + "#" + occurrence).astype(object)
    return frame


def _table_delta(before: TableState, after: TableState) -> dict[str, list]:
    merged = (
        _frame(before)
        .merge(_frame(after), on="key", how="outer", suffixes=("_before", "_after"), indicator=True)
        .sort_values("key")
    )
    side = merged["_merge"]
    changed = merged[(side == "both") & (merged["row_before"] != merged["row_after"])]
    return {
        "inserted": merged.loc[side == "right_only", "data_after"].tolist(),
        "updated": [
            {
                "key": {column: old[column] for column in before.primary_key},
                "changes": {
                    column: {"before": old[column], "after": new[column]}
                    for column in before.columns
                    if old[column] != new[column]
                },
            }
            for old, new in zip(changed["data_before"], changed["data_after"])
        ],
        "deleted": merged.loc[side == "left_only", "data_before"].tolist(),
    }


def delta(before: Snapshot, after: Snapshot) -> dict[str, dict[str, list]]:
    """Describe what changed between two snapshots of the same database.

    Returns one entry per changed table; an unchanged database gives an empty
    result. Rows are matched by primary key where the table has one. Where it has
    none, an update appears as one deleted row plus one inserted row.
    """
    structure_before = {name: (t.definition, t.primary_key) for name, t in before.tables.items()}
    structure_after = {name: (t.definition, t.primary_key) for name, t in after.tables.items()}
    if structure_before != structure_after:
        differing = sorted(
            name
            for name in set(structure_before) | set(structure_after)
            if structure_before.get(name) != structure_after.get(name)
        )
        raise StructureChangedError(f"Structure differs for: {', '.join(differing)}.")

    return {
        name: _table_delta(before.tables[name], after.tables[name])
        for name in sorted(before.tables)
        if before.tables[name].rows != after.tables[name].rows
    }
