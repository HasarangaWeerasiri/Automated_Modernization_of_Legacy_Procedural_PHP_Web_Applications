# ------------------------------------------------------------------------------
# Everything below is the suite's own code. Everything above it is copied from the
# validation engine when the suite is generated, so both apply the same rules.
# This part is appended to that copy and is not meant to be imported on its own.
# ------------------------------------------------------------------------------


# ------------------------------------------------------------------ the database


class StructureChanged(AssertionError):
    """Tables or columns differ between two snapshots, so rows cannot be compared."""


def snapshot(engine) -> dict[str, dict[str, Any]]:
    """Every row of every table, with values reduced to JSON types."""
    metadata = MetaData()
    with engine.connect() as connection:
        metadata.reflect(bind=connection)
        return {
            name: {
                "columns": [column.name for column in table.columns],
                "primary_key": [column.name for column in table.primary_key.columns],
                "rows": [
                    {column: _plain(value) for column, value in row.items()}
                    for row in connection.execute(select(table)).mappings()
                ],
            }
            for name, table in sorted(metadata.tables.items())
        }


def start(engine):
    """The database as it is just before the request under test, or None without one."""
    return snapshot(engine) if engine is not None else None


_CLOCK_QUERIES = {
    "mysql": "SELECT NOW(), UTC_TIMESTAMP()",
    "mariadb": "SELECT NOW(), UTC_TIMESTAMP()",
}


def clock(engine) -> dict[str, str] | None:
    """The database's time now, by its own time zone and in UTC; None when it cannot be read."""
    query = _CLOCK_QUERIES.get(engine.dialect.name) if engine is not None else None
    if query is None:
        return None
    with engine.connect() as connection:
        local, utc = connection.execute(text(query)).one()
    return {"local": _plain(local), "utc": _plain(utc)}


def _key(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, ensure_ascii=False)


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, list]]:
    """What changed between two snapshots, in the form the oracle records use.

    Rows are matched by primary key where the table has one. Where it has none, rows
    are counted by content, so an update appears as one deletion plus one insertion.
    """
    shape_before = {name: (t["columns"], t["primary_key"]) for name, t in before.items()}
    shape_after = {name: (t["columns"], t["primary_key"]) for name, t in after.items()}
    if shape_before != shape_after:
        raise StructureChanged("Tables or columns changed during the request.")

    changed: dict[str, dict[str, list]] = {}
    for name, table in before.items():
        old_rows, new_rows = table["rows"], after[name]["rows"]
        keys = table["primary_key"]
        inserted: list = []
        updated: list = []
        deleted: list = []
        if keys:
            old = {_key({k: row[k] for k in keys}): row for row in old_rows}
            new = {_key({k: row[k] for k in keys}): row for row in new_rows}
            inserted = [new[k] for k in sorted(set(new) - set(old))]
            deleted = [old[k] for k in sorted(set(old) - set(new))]
            for k in sorted(set(old) & set(new)):
                if old[k] != new[k]:
                    updated.append({
                        "key": {column: old[k][column] for column in keys},
                        "changes": {
                            column: {"before": old[k][column], "after": new[k][column]}
                            for column in table["columns"] if old[k][column] != new[k][column]
                        },
                    })
        else:
            old_count = Counter(_key(row) for row in old_rows)
            new_count = Counter(_key(row) for row in new_rows)
            inserted = [json.loads(k) for k in sorted((new_count - old_count).elements())]
            deleted = [json.loads(k) for k in sorted((old_count - new_count).elements())]
        if inserted or updated or deleted:
            changed[name] = {"inserted": inserted, "updated": updated, "deleted": deleted}
    return changed


def reset(engine, baseline: dict[str, Any]) -> None:
    """Put the database back to the baseline the expected values were captured from."""
    metadata = MetaData()
    metadata.reflect(bind=engine)
    mysql = engine.dialect.name in ("mysql", "mariadb")
    with engine.begin() as connection:
        if mysql:
            # Load the rows the way legacy seed dumps load them. They routinely hold
            # values a strict session refuses, such as the zero date 0000-00-00.
            connection.execute(text("SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'"))
            connection.execute(text("SET SESSION FOREIGN_KEY_CHECKS = 0"))
        for table in reversed(metadata.sorted_tables):
            connection.execute(table.delete())
        for table in metadata.sorted_tables:
            rows = baseline["tables"].get(table.name, [])
            if rows:
                connection.execute(table.insert(), rows)
    counters = baseline.get("auto_increment", {})
    if counters and mysql:
        quote = engine.dialect.identifier_preparer.quote
        with engine.begin() as connection:
            for name, value in counters.items():
                connection.execute(text(f"ALTER TABLE {quote(name)} AUTO_INCREMENT = {int(value)}"))
    elif counters:
        pytest.skip(
            f"Database checks reset auto-increment counters on MySQL only, not {engine.dialect.name}."
        )


# ------------------------------------------------------------------ the response


def records_from(response, at: str):
    """The list of records at `at` in a JSON response, or None if there is none."""
    try:
        found: Any = response.json()
    except ValueError:
        return None
    for key in filter(None, at.split(".")):
        if not isinstance(found, dict) or key not in found:
            return None
        found = found[key]
    if isinstance(found, dict):
        found = [found]
    if not isinstance(found, list) or not all(isinstance(item, dict) for item in found):
        return None
    return found


def expect_outcome(response, legacy_outcome: str) -> None:
    """The response's outcome agrees with what the legacy page did."""
    migrated = outcome_of(response.status_code)
    if legacy_outcome == "no_effect":
        # The legacy page did nothing; any answer that also does nothing agrees.
        agrees = migrated != "error"
    else:
        agrees = migrated == legacy_outcome
    assert agrees, (
        f"The legacy page's outcome was {legacy_outcome}; "
        f"this answer's is {migrated} (status {response.status_code})."
    )


def expect_records(
    response, at, records: list[dict[str, Any]], excluded: tuple[str, ...] = (),
    columns: dict[str, str] | None = None, fields: dict[str, str] | None = None,
) -> None:
    """Each record the legacy page showed is reproduced by a record of its own.

    Fields are matched by value: the page labels its columns with header text, the API
    with JSON keys. Extra values in an API record are not a difference. Where both the
    page's field (`columns`) and the API's field (`fields`) are known to hold the same
    database column, the value must also sit under that column.
    """
    if at is None:
        return  # this endpoint returns no records
    found = records_from(response, at)
    if found is None:
        assert outcome_of(response.status_code) != "success", (
            f"No list of records at '{at}' in a successful response."
        )
        found = []

    needed = [_values(value for label, value in item.items() if label not in excluded) for item in records]
    offered = [_values(_leaves(item)) for item in found]
    columns = {label: column for label, column in (columns or {}).items() if label not in excluded}
    fields = fields or {}
    compared = set(columns.values()) & set(fields.values())
    wanted = [_by_column(item, columns, compared) for item in records]
    held = [_by_column(_flat(item), fields, compared) for item in found]
    pairs = _match(needed, offered, wanted, held)
    missing = [records[i] for i in range(len(records)) if i not in pairs]
    unexpected = [found[m] for m in range(len(found)) if m not in pairs.values()]
    assert not missing and not unexpected, "\n".join([
        "The response does not reproduce the records the legacy page showed.",
        *[f"  not reproduced: {item}" for item in missing],
        *[f"  not shown by the legacy page: {item}" for item in unexpected],
    ])

    for index, migrated in pairs.items():
        spare = offered[migrated] - needed[index]
        for label in excluded:
            value = normalise(records[index].get(label))
            if value and value not in spare:
                pytest.skip(f"Only the excluded field '{label}' differs, which cannot be judged.")


def expect_database_change(
    engine, before, expected: dict[str, Any], excluded: tuple[str, ...] = (),
    clocked: list[dict[str, Any]] = (), sent: dict[str, str] | None = None,
) -> None:
    """The request changed the database exactly as the legacy page did.

    A column in `clocked` was written from the clock: it must hold the time, or the
    date, of this request, read from `sent` (the clock just before it) to now.
    """
    if engine is None:
        return  # no database given: database checks are off
    ended = clock(engine)
    actual = delta(before, snapshot(engine))
    fields = {entry["field"]: entry for entry in clocked}
    window = {name: [sent[name], ended[name]] for name in sent} if fields and sent and ended else None
    if fields and window is None:
        excluded, fields = (*excluded, *fields), {}  # the clock could not be read: not judged
    expected = _on_the_clock(expected, fields, None, oracle=True)
    actual = _on_the_clock(actual, fields, window)
    kept = DeepDiff(
        _plain_delta(expected, set(excluded)), _plain_delta(actual, set(excluded)),
        ignore_order=True, report_repetition=True,
    )
    note = " The legacy page changed nothing for this input." if not expected and actual else ""
    assert not kept, f"The database change differs from the legacy page's.{note}\n{kept.pretty()}"
    if DeepDiff(_plain_delta(expected, set()), _plain_delta(actual, set()), ignore_order=True, report_repetition=True):
        pytest.skip("Only excluded columns differ, which cannot be judged.")


def expect_safe_handling(response, engine, before, probe: str, at, placed: bool = True) -> None:
    """An injection probe was treated as plain text: no leak, no alteration, no crash.

    This is not an equivalence check. A legacy page that leaked data on this input is
    not something to reproduce. `placed` is False when it is not known where the value
    is stored, so a row written without the probe text is no evidence.
    """
    assert response.status_code < 500, (
        f"The probe {probe!r} caused a server error (status {response.status_code})."
    )
    found = records_from(response, at) if at is not None else None
    changes = delta(before, snapshot(engine)) if engine is not None else {}
    effects = injection_effects(probe, found or [], changes, placed=placed)
    assert not effects, f"The probe {probe!r} changed what the query did: {effects}"
