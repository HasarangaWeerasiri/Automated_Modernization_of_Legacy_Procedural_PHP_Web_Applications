import re
from dataclasses import asdict, dataclass, field
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
        Columns used to restrict rows: WHERE, JOIN ... ON and HAVING.

    join_columns / group_by_columns / order_by_columns / having_columns:
        The individual roles (S11). read_columns also holds the columns an
        UPDATE assignment reads (fee = fee + :delta).

    qualified_columns:
        The same roles as "table.column", resolved through aliases and the
        schema; "?.column" when the table cannot be determined.

    expanded_read_columns:
        SELECT * expanded from the schema (None when there is no * or the
        table is not in the schema).

    dynamic_identifiers:
        Identifiers chosen at run time from an allowlist (template
        conversions), e.g. {"role": "order_by", "allowed": [...]}.
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
    join_columns: list[str] = field(default_factory=list)
    group_by_columns: list[str] = field(default_factory=list)
    order_by_columns: list[str] = field(default_factory=list)
    having_columns: list[str] = field(default_factory=list)
    qualified_columns: dict[str, list[str]] = field(default_factory=dict)
    expanded_read_columns: list[str] | None = None
    dynamic_identifiers: list[dict] = field(default_factory=list)
    analysis_source: str = "recovered_sql"

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


# --------------------------------------------------------------------------
# S11: column roles, table attribution, SELECT * expansion, templates.
# --------------------------------------------------------------------------

ROLES = ("read", "write", "filter", "join", "group_by", "order_by", "having")


def _alias_map(expression: exp.Expression) -> dict[str, str]:
    """Table alias (or name) -> table name for every table in the statement."""

    aliases = {}
    for table in expression.find_all(exp.Table):
        if table.name:
            aliases[(table.alias_or_name or table.name).lower()] = table.name
            aliases[table.name.lower()] = table.name
    return aliases


def _schema_columns(schema: dict | None) -> dict[str, list[str]]:
    if not schema:
        return {}
    return {
        table["name"].lower(): [column["name"] for column in table.get("columns", [])]
        for table in schema.get("tables", [])
    }


class _Resolver:
    """Attributes columns to tables using aliases and, when given, the schema."""

    def __init__(self, expression: exp.Expression, schema: dict | None):
        self.aliases = _alias_map(expression)
        self.tables = _table_names(expression)
        self.schema = _schema_columns(schema)

    def qualify(self, column: exp.Column) -> str:
        name = "*" if isinstance(column.this, exp.Star) else column.name
        if column.table:
            table = self.aliases.get(column.table.lower(), column.table)
            return f"{table}.{name}"
        if len(self.tables) == 1:
            return f"{self.tables[0]}.{name}"
        owners = [
            t for t in self.tables
            if name.lower() in (c.lower() for c in self.schema.get(t.lower(), []))
        ]
        return f"{owners[0]}.{name}" if len(owners) == 1 else f"?.{name}"


def _columns_in(node: exp.Expression | None) -> list[exp.Column]:
    if node is None:
        return []
    return [c for c in node.find_all(exp.Column) if c.name or isinstance(c.this, exp.Star)]


def _bare(column: exp.Column) -> str:
    return "*" if isinstance(column.this, exp.Star) else column.name


def _select_alias_targets(select: exp.Select) -> dict[str, list[exp.Column]]:
    targets = {}
    for projection in select.expressions:
        if isinstance(projection, exp.Alias) and projection.alias:
            targets[projection.alias.lower()] = _columns_in(projection.this)
    return targets


def _resolve_select_aliases(columns, select, resolver) -> list[exp.Column]:
    """GROUP BY / ORDER BY / HAVING may name a SELECT alias: use its columns."""

    if not isinstance(select, exp.Select):
        return columns
    targets = _select_alias_targets(select)
    known = {c.lower() for cols in resolver.schema.values() for c in cols}
    resolved = []
    for column in columns:
        alias_target = targets.get(column.name.lower()) if not column.table else None
        if alias_target is not None and column.name.lower() not in known:
            resolved.extend(alias_target)
        else:
            resolved.append(column)
    return resolved


def column_roles(expression: exp.Expression, schema: dict | None = None) -> dict[str, list[exp.Column]]:
    """Columns per role for one parsed statement."""

    roles: dict[str, list[exp.Column]] = {role: [] for role in ROLES}
    resolver = _Resolver(expression, schema)

    roles["filter"] = _columns_in(expression.args.get("where"))
    for join in expression.find_all(exp.Join):
        roles["join"].extend(_columns_in(join.args.get("on")))
        for identifier in join.args.get("using") or []:
            roles["join"].append(exp.column(identifier.name))

    if isinstance(expression, exp.Select):
        for projection in expression.expressions:
            if isinstance(projection, exp.Star):
                roles["read"].append(exp.Column(this=exp.Star()))
            else:
                roles["read"].extend(_columns_in(projection))
        group = expression.args.get("group")
        roles["group_by"] = _resolve_select_aliases(_columns_in(group), expression, resolver)
        roles["order_by"] = _resolve_select_aliases(_columns_in(expression.args.get("order")), expression, resolver)
        roles["having"] = _resolve_select_aliases(_columns_in(expression.args.get("having")), expression, resolver)

    elif isinstance(expression, exp.Insert):
        target = expression.this
        if isinstance(target, exp.Schema):
            for item in target.expressions:
                if isinstance(item, (exp.Identifier, exp.Column)):
                    # INSERT targets belong to the INSERT table, whatever the SELECT reads.
                    roles["write"].append(exp.column(item.name, table=target.this.name))
        source = expression.expression
        if isinstance(source, exp.Select):
            nested = column_roles(source, schema)
            nested_resolver = _Resolver(source, schema)
            for role in ("read", "filter", "join", "group_by", "order_by", "having"):
                for column in nested[role]:
                    # Attribute within the SELECT's own FROM, not the INSERT target.
                    table = nested_resolver.qualify(column).rpartition(".")[0]
                    if not column.table and table != "?" and not isinstance(column.this, exp.Star):
                        column = exp.column(column.name, table=table)
                    roles[role].append(column)
        conflict = expression.args.get("conflict")
        if conflict is not None:
            for assignment in conflict.find_all(exp.EQ):
                if isinstance(assignment.this, exp.Column):
                    roles["write"].append(assignment.this)
                    roles["read"].extend(_columns_in(assignment.expression))

    elif isinstance(expression, exp.Update):
        for assignment in expression.expressions:
            if isinstance(assignment, exp.EQ):
                target = assignment.this
                if isinstance(target, exp.Column):
                    roles["write"].append(target)
                elif isinstance(target, exp.Identifier):
                    roles["write"].append(exp.column(target.name))
                # Values read by the assignment: fee = fee + :delta, a.fee = d.rate
                roles["read"].extend(_columns_in(assignment.expression))

    roles["filter"] = roles["filter"] + roles["join"] + roles["having"]
    return roles


def _role_lists(roles, resolver) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    bare = {role: _unique([_bare(c) for c in columns]) for role, columns in roles.items()}
    qualified = {role: _unique([resolver.qualify(c) for c in columns]) for role, columns in roles.items()}
    return bare, qualified


def _expand_star(read_qualified: list[str], resolver: _Resolver) -> list[str] | None:
    if not any(item.endswith(".*") for item in read_qualified):
        return None
    expanded = []
    for item in read_qualified:
        table, _, name = item.rpartition(".")
        if name != "*":
            expanded.append(name)
            continue
        tables = resolver.tables if table in ("", "?") else [table]
        for t in tables:
            columns = resolver.schema.get(t.lower())
            if columns is None:
                return None  # table not in the schema: do not guess
            expanded.extend(columns)
    return _unique(expanded)


def analyze_sql_columns(
    *,
    query_id: str,
    sql: str,
    source_file: str | None = None,
    source_line: int | None = None,
    schema: dict | None = None,
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

    operations = {
        exp.Select: "SELECT",
        exp.Insert: "INSERT",
        exp.Update: "UPDATE",
        exp.Delete: "DELETE",
    }
    operation = next(
        (name for kind, name in operations.items() if isinstance(expression, kind)),
        None,
    )

    resolver = _Resolver(expression, schema)
    bare, qualified = _role_lists(column_roles(expression, schema), resolver)

    usage = ColumnUsage(
        query_id=query_id,
        operation=operation or expression.key.upper(),
        tables=resolver.tables,
        read_columns=bare["read"],
        write_columns=bare["write"],
        filter_columns=bare["filter"],
        source_file=source_file,
        source_line=source_line,
        join_columns=bare["join"],
        group_by_columns=bare["group_by"],
        order_by_columns=bare["order_by"],
        having_columns=bare["having"],
        qualified_columns=qualified,
        expanded_read_columns=_expand_star(qualified["read"], resolver),
    )

    if operation is None:
        usage.status = "UNSUPPORTED"
        usage.reason = f"Unsupported SQL operation: {expression.key}"
        usage.read_columns = usage.write_columns = []

    return usage


def analyze_recovered_query(
    query: dict[str, Any],
    schema: dict | None = None,
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
        schema=schema,
    )


def _merge(usages: list[ColumnUsage], base: ColumnUsage) -> ColumnUsage:
    """Union of the roles of several possible statements (branches)."""

    for usage in usages:
        for name in ("tables", "read_columns", "write_columns", "filter_columns", "join_columns",
                     "group_by_columns", "order_by_columns", "having_columns"):
            setattr(base, name, _unique(getattr(base, name) + getattr(usage, name)))
        for role, items in usage.qualified_columns.items():
            base.qualified_columns[role] = _unique(base.qualified_columns.get(role, []) + items)
    return base


def analyze_converted_template(query: dict[str, Any], converted: dict[str, Any], schema: dict | None = None) -> ColumnUsage | None:
    """Column use of a query that only a guarded template converted.

    The template's own SQL (base statement, branches, allowlisted
    identifiers) is analysed; identifiers chosen at run time are listed in
    dynamic_identifiers instead of being guessed.
    """

    shape = converted.get("query_shape")
    common = {"query_id": query.get("query_id", "UNKNOWN"), "source_file": query.get("source_file"),
              "source_line": query.get("source_line"), "schema": schema}
    dynamic = []

    if shape == "EXPANDING_IN_SELECT" and converted.get("parameterized_sql"):
        sql = re.sub(r"\bIN\s+:(\w+)", r"IN (:\1)", converted["parameterized_sql"])
        usage = analyze_sql_columns(sql=sql, **common)
    elif shape == "ORDER_BY_SELECT" and converted.get("base_sql"):
        usage = analyze_sql_columns(sql=converted["base_sql"], **common)
        dynamic.append({"role": "order_by", "input_key": "sort", "allowed": converted.get("allowed_columns", [])})
    elif shape == "TABLE_IDENTIFIER_SELECT" and converted.get("sql_template"):
        allowed = converted.get("allowed_tables", [])
        usage = analyze_sql_columns(sql=converted["sql_template"].replace("{table}", "c3_dynamic_table"), **common)
        usage.tables = list(allowed)
        usage.qualified_columns = {
            role: [item.replace("c3_dynamic_table.", "?.") for item in items]
            for role, items in usage.qualified_columns.items()
        }
        dynamic.append({"role": "table", "input_key": converted.get("input_key"), "allowed": allowed})
    elif shape == "CONDITIONAL_SELECT" and converted.get("branches"):
        branches = [analyze_sql_columns(sql=sql, **common) for sql in converted["branches"].values()]
        usage = _merge(branches[1:], branches[0])
        dynamic.append({"role": "branch", "branches": sorted(converted["branches"])})
    elif shape == "CONDITIONAL_CONCAT_SELECT" and converted.get("filtered_sql"):
        usage = analyze_sql_columns(sql=converted["filtered_sql"], **common)
        dynamic.append({"role": "optional_filter", "input_key": converted.get("condition_key")})
    else:
        return None

    if usage.status != "ANALYZED":
        return None
    usage.dynamic_identifiers = dynamic
    usage.analysis_source = "converted_template"
    return usage


def analyze_query_records(
    queries: list[dict[str, Any]],
    converted: list[dict[str, Any]] | None = None,
    schema: dict | None = None,
) -> list[ColumnUsage]:
    """
    Analyze all records from queries.json.

    With the conversion results, a query that recovery left UNRESOLVED but
    a guarded template CONVERTED is analysed from the template's SQL.
    """

    conversions = {item.get("query_id"): item for item in (converted or [])}
    results = []

    for query in queries:
        usage = analyze_recovered_query(query, schema)
        conversion = conversions.get(query.get("query_id"), {})
        if usage.status != "ANALYZED" and conversion.get("status") == "CONVERTED":
            usage = analyze_converted_template(query, conversion, schema) or usage
        results.append(usage)

    return results
