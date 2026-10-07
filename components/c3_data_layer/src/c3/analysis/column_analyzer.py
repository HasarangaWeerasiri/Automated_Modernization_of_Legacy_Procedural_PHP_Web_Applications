from dataclasses import asdict, dataclass
from typing import Any

from sqlglot import exp, parse_one


@dataclass
class ColumnUsage:
    """
    Describes how one recovered SQL query uses database columns.

    read_columns:
        Columns whose values are read/returned.

    write_columns:
        Columns whose values are inserted or updated.

    filter_columns:
        Columns used to restrict rows, for example in WHERE clauses.
    """

    query_id: str
    operation: str
    tables: list[str]
    read_columns: list[str]
    write_columns: list[str]
    filter_columns: list[str]
    source_file: str | None = None
    source_line: int | None = None
    status: str = "ANALYZED"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unique(values: list[str]) -> list[str]:
    """
    Remove duplicates while preserving discovery order.
    """
    return list(dict.fromkeys(values))


def _column_names(expression: exp.Expression | None) -> list[str]:
    """
    Extract column names from a SQLGlot expression.
    """

    if expression is None:
        return []

    return _unique(
        [
            column.name
            for column in expression.find_all(exp.Column)
            if column.name
        ]
    )


def _table_names(expression: exp.Expression) -> list[str]:
    """
    Extract referenced table names.
    """

    return _unique(
        [
            table.name
            for table in expression.find_all(exp.Table)
            if table.name
        ]
    )


def _where_columns(expression: exp.Expression) -> list[str]:
    """
    Extract columns used in a WHERE condition.
    """

    where = expression.args.get("where")

    if where is None:
        return []

    return _column_names(where)


def _select_columns(expression: exp.Select) -> list[str]:
    """
    Extract columns returned by SELECT.

    SELECT * is represented using "*".
    """

    columns: list[str] = []

    for selected in expression.expressions:
        if isinstance(selected, exp.Star):
            columns.append("*")
            continue

        columns.extend(_column_names(selected))

    return _unique(columns)


def _insert_columns(expression: exp.Insert) -> list[str]:
    """
    Extract target columns from INSERT.
    """

    target = expression.this

    if isinstance(target, exp.Schema):
        columns: list[str] = []

        for item in target.expressions:
            if isinstance(item, exp.Identifier):
                columns.append(item.name)
            elif isinstance(item, exp.Column):
                columns.append(item.name)

        return _unique(columns)

    return []


def _update_columns(expression: exp.Update) -> list[str]:
    """
    Extract columns modified by UPDATE SET assignments.
    """

    columns: list[str] = []

    for assignment in expression.expressions:
        if isinstance(assignment, exp.EQ):
            target = assignment.this

            if isinstance(target, exp.Column):
                columns.append(target.name)
            elif isinstance(target, exp.Identifier):
                columns.append(target.name)

    return _unique(columns)


def analyze_sql_columns(
    *,
    query_id: str,
    sql: str,
    source_file: str | None = None,
    source_line: int | None = None,
) -> ColumnUsage:
    """
    Analyze one SQL statement and classify its column usage.

    The analysis is deterministic. If SQLGlot cannot safely parse
    the statement, the query is marked UNRESOLVED instead of guessing.
    """

    try:
        expression = parse_one(
            sql,
            read="mysql",
        )
    except Exception as exc:
        return ColumnUsage(
            query_id=query_id,
            operation="UNKNOWN",
            tables=[],
            read_columns=[],
            write_columns=[],
            filter_columns=[],
            source_file=source_file,
            source_line=source_line,
            status="UNRESOLVED",
            reason=f"SQL parse failed: {exc}",
        )

    tables = _table_names(expression)
    filter_columns = _where_columns(expression)

    if isinstance(expression, exp.Select):
        return ColumnUsage(
            query_id=query_id,
            operation="SELECT",
            tables=tables,
            read_columns=_select_columns(expression),
            write_columns=[],
            filter_columns=filter_columns,
            source_file=source_file,
            source_line=source_line,
        )

    if isinstance(expression, exp.Insert):
        return ColumnUsage(
            query_id=query_id,
            operation="INSERT",
            tables=tables,
            read_columns=[],
            write_columns=_insert_columns(expression),
            filter_columns=filter_columns,
            source_file=source_file,
            source_line=source_line,
        )

    if isinstance(expression, exp.Update):
        return ColumnUsage(
            query_id=query_id,
            operation="UPDATE",
            tables=tables,
            read_columns=[],
            write_columns=_update_columns(expression),
            filter_columns=filter_columns,
            source_file=source_file,
            source_line=source_line,
        )

    if isinstance(expression, exp.Delete):
        return ColumnUsage(
            query_id=query_id,
            operation="DELETE",
            tables=tables,
            read_columns=[],
            write_columns=[],
            filter_columns=filter_columns,
            source_file=source_file,
            source_line=source_line,
        )

    return ColumnUsage(
        query_id=query_id,
        operation=expression.key.upper(),
        tables=tables,
        read_columns=[],
        write_columns=[],
        filter_columns=filter_columns,
        source_file=source_file,
        source_line=source_line,
        status="UNSUPPORTED",
        reason=f"Unsupported SQL operation: {expression.key}",
    )
def analyze_recovered_query(
    query: dict[str, Any],
) -> ColumnUsage:
    """
    Analyze one query record produced by the SQL recovery stage.

    Only queries with status RECOVERED and a usable recovered_sql
    value are analyzed. Unresolved queries are conservatively
    propagated as UNRESOLVED instead of being guessed.
    """

    query_id = query.get("query_id", "UNKNOWN")
    source_file = query.get("source_file")
    source_line = query.get("source_line")
    status = query.get("status")
    recovered_sql = query.get("recovered_sql")

    if status != "RECOVERED":
        return ColumnUsage(
            query_id=query_id,
            operation=query.get("operation") or "UNKNOWN",
            tables=[],
            read_columns=[],
            write_columns=[],
            filter_columns=[],
            source_file=source_file,
            source_line=source_line,
            status="UNRESOLVED",
            reason=query.get("reason")
            or f"Recovery status is {status}",
        )

    if not recovered_sql:
        return ColumnUsage(
            query_id=query_id,
            operation=query.get("operation") or "UNKNOWN",
            tables=[],
            read_columns=[],
            write_columns=[],
            filter_columns=[],
            source_file=source_file,
            source_line=source_line,
            status="UNRESOLVED",
            reason="Recovered SQL is missing",
        )

    # Convert PHP-recovery placeholders into SQL bind parameters
    # so SQLGlot can parse the recovered statement.
    import re

    parseable_sql = re.sub(
        r"'{{([A-Za-z_][A-Za-z0-9_]*)}}'",
        r":\1",
        recovered_sql,
    )

    parseable_sql = re.sub(
        r"{{([A-Za-z_][A-Za-z0-9_]*)}}",
        r":\1",
        parseable_sql,
    )

    # Any remaining recovery marker means static recovery was
    # incomplete. Do not attempt to infer its meaning.
    if "{{UNRESOLVED:" in parseable_sql:
        return ColumnUsage(
            query_id=query_id,
            operation=query.get("operation") or "UNKNOWN",
            tables=[],
            read_columns=[],
            write_columns=[],
            filter_columns=[],
            source_file=source_file,
            source_line=source_line,
            status="UNRESOLVED",
            reason=query.get("reason")
            or "Recovered SQL contains an unresolved expression",
        )

    return analyze_sql_columns(
        query_id=query_id,
        sql=parseable_sql,
        source_file=source_file,
        source_line=source_line,
    )


def analyze_query_records(
    queries: list[dict[str, Any]],
) -> list[ColumnUsage]:
    """
    Analyze all records from queries.json.
    """

    return [
        analyze_recovered_query(query)
        for query in queries
    ]