"""
Security Report (FR7)
Component 3 - Data Layer Migration

For every SQL execution point, trace which user-controlled request data
($_GET, $_POST, $_REQUEST, $_COOKIE) reaches the SQL text in the legacy
code, which sanitizers were applied on the way, and how the generated code
handles each value (bound parameter, schema-allowlisted identifier, or not
converted). Also scans the generated data_access.py for SQL built from
variables.

Scope of the taint analysis (stated in the report):
- intra-file and order-based: a variable is traced to its last assignment
  before the use in the same file, following variables transitively;
- function parameters and values from other files are not traced, except
  for cross-file bindings that recovery has already validated;
- $_SESSION is reported as an indirect source and is not counted as
  direct user input.
"""

import ast
import json
import re
from pathlib import Path
from typing import Any

from c3.mapping.sql_recovery import (
    _find_relevant_assignments,
    _select_assignment_chain,
)

USER_INPUT_SOURCES = ("_GET", "_POST", "_REQUEST", "_COOKIE")
INDIRECT_SOURCES = ("_SESSION",)

SUPERGLOBAL_PATTERN = re.compile(
    r"\$(_GET|_POST|_REQUEST|_COOKIE|_SESSION)\b"
    r"(?:\s*\[\s*(['\"])([^'\"]*)\2\s*\])?"
)
VARIABLE_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

NUMERIC_CASTS = re.compile(
    r"\(\s*(?:int|integer|float|double)\s*\)|\b(?:intval|floatval)\s*\(",
    re.IGNORECASE,
)
STRING_ESCAPES = re.compile(
    r"\b(?:mysqli_real_escape_string|mysql_real_escape_string|"
    r"mysql_escape_string|addslashes)\s*\(|->real_escape_string\s*\(",
    re.IGNORECASE,
)

MAX_TRACE_DEPTH = 6


def _last_assignment(assignments, source_file, variable, before_line):
    candidates = [
        a for a in assignments
        if a.get("source_file") == source_file
        and a.get("variable") == f"${variable}"
        and (a.get("source_line") or 0) < before_line
        and a.get("expression") is not None
    ]
    return max(candidates, key=lambda a: a["source_line"]) if candidates else None


def _trace(expression, assignments, source_file, line, depth=0, seen=None):
    """
    Return (sources, sanitizers) reaching an expression.

    sources: list of "_POST.name" style strings (direct and transitive).
    sanitizers: names of numeric casts / escapes seen on the path.
    """

    seen = set() if seen is None else seen
    sources, sanitizers = [], []

    if NUMERIC_CASTS.search(expression):
        sanitizers.append("numeric_cast")
    if STRING_ESCAPES.search(expression):
        sanitizers.append("string_escape")

    for match in SUPERGLOBAL_PATTERN.finditer(expression):
        key = match.group(3)
        sources.append(f"{match.group(1)}.{key}" if key is not None else match.group(1))

    if depth >= MAX_TRACE_DEPTH:
        return sources, sanitizers

    for name in VARIABLE_PATTERN.findall(expression):
        if name.startswith("_") or name in seen or name == "this":
            continue

        definition = _last_assignment(assignments, source_file, name, line)

        if definition is None:
            continue

        seen.add(name)
        more_sources, more_sanitizers = _trace(
            definition["expression"], assignments, source_file,
            definition["source_line"], depth + 1, seen,
        )
        sources += more_sources
        sanitizers += more_sanitizers

    return sorted(set(sources)), sorted(set(sanitizers))


def _bound_names(conversion: dict) -> tuple[set[str], set[str]]:
    """Names handled by the generated code: (bound values, allowlisted identifiers)."""

    bound = set(conversion.get("parameters") or [])

    if isinstance(conversion.get("parameter"), str):
        bound.add(conversion["parameter"])

    for key in ("condition_key",):
        if isinstance(conversion.get(key), str):
            bound.add(conversion[key])

    for binding in conversion.get("binding_plan") or []:
        bound.update(
            value for value in (binding.get("parameter"), binding.get("key"))
            if isinstance(value, str)
        )

    allowlisted = set()

    if conversion.get("allowed_columns") or conversion.get("allowed_tables"):
        if isinstance(conversion.get("input_key"), str):
            allowlisted.add(conversion["input_key"])
    elif isinstance(conversion.get("input_key"), str):
        bound.add(conversion["input_key"])

    return bound, allowlisted


def _sql_expressions(sql_call_like: dict, assignments: list[dict]) -> list[dict]:
    """Verbatim PHP expressions that build the SQL at an execution point."""

    argument = (sql_call_like.get("sql_argument") or "").strip()

    if not re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", argument):
        return [{"expression": argument, "source_line": sql_call_like.get("source_line")}]

    chain = _select_assignment_chain(_find_relevant_assignments(sql_call_like, assignments))
    return [
        {"expression": a.get("expression") or "", "source_line": a.get("source_line")}
        for a in chain
    ]


def _inside_single_quotes(template: str, name: str) -> bool:
    """
    True when every occurrence of the placeholder for name lies inside a
    single-quoted SQL string literal (e.g. '{{x}}' or LIKE '%{{x}}%').
    """

    positions = [
        m.start()
        for m in re.finditer(
            r"\{\{" + re.escape(name) + r"\}\}"
            r"|\{\{UNRESOLVED:[^}]*\b" + re.escape(name) + r"\b[^}]*\}\}",
            template,
        )
    ]

    if not positions:
        return False

    def quote_count(end: int) -> int:
        # Count single quotes before the position, skipping '' and ' escapes.
        prefix = template[:end].replace("\\'", "").replace("''", "")
        return prefix.count("'")

    return all(quote_count(position) % 2 == 1 for position in positions)


def _legacy_risk(sanitizers: list[str], quoted: bool) -> str:
    if "numeric_cast" in sanitizers:
        return "mitigated_numeric_cast"
    if "string_escape" in sanitizers and quoted:
        return "mitigated_escaped_and_quoted"
    if "string_escape" in sanitizers:
        return "injectable_escaped_but_unquoted"
    return "injectable_unsanitized"


def build_query_records(
    queries: list[dict],
    converted: list[dict],
    assignments: list[dict],
) -> list[dict[str, Any]]:
    conversion_by_id = {c.get("query_id"): c for c in converted}
    records = []

    for query in queries:
        conversion = conversion_by_id.get(query.get("query_id"), {})
        is_converted = conversion.get("status") == "CONVERTED"
        bound, allowlisted = _bound_names(conversion) if is_converted else (set(), set())

        template = query.get("recovered_sql") or ""
        inputs = []

        for part in _sql_expressions(query, assignments):
            expression = part["expression"]
            line = part["source_line"] or query.get("source_line") or 0

            # Each variable or direct superglobal read in the SQL-building
            # expression is one candidate value reaching the SQL.
            candidates = [(name, f"${name}") for name in VARIABLE_PATTERN.findall(expression)
                          if not name.startswith("_") and name != "this"]
            candidates += [
                (m.group(3) or m.group(1), m.group(0))
                for m in SUPERGLOBAL_PATTERN.finditer(expression)
            ]

            for name, text in candidates:
                sources, sanitizers = _trace(text, assignments, query.get("source_file"), line + 1)
                direct = [s for s in sources if s.split(".")[0] in USER_INPUT_SOURCES]

                if not sources:
                    continue

                quoted = _inside_single_quotes(template, name)

                if not is_converted:
                    handling = "not_converted"
                elif name in allowlisted:
                    handling = "schema_allowlisted_identifier"
                elif name in bound:
                    handling = "bound_parameter"
                else:
                    handling = "UNHANDLED_IN_CONVERTED_CODE"

                inputs.append({
                    "variable": name,
                    "sources": sources,
                    "user_controlled": bool(direct),
                    "sanitizers": sanitizers,
                    "legacy_risk": _legacy_risk(sanitizers, quoted),
                    "generated_handling": handling,
                })

        # One entry per variable name (the same value may appear twice).
        unique = {}
        for item in inputs:
            unique.setdefault(item["variable"], item)
        inputs = list(unique.values())

        user_inputs = [i for i in inputs if i["user_controlled"]]
        legacy_injectable = any(i["legacy_risk"].startswith("injectable") for i in user_inputs)

        records.append({
            "query_id": query.get("query_id"),
            "source_file": query.get("source_file"),
            "source_line": query.get("source_line"),
            "recovery_status": query.get("status"),
            "conversion_status": conversion.get("status"),
            "conversion_rule": conversion.get("conversion_rule"),
            "placeholders": {
                "value_positions": sorted(bound & {i["variable"] for i in inputs}) if is_converted else [],
                "structural_positions": sorted(allowlisted) if is_converted else (
                    PLACEHOLDER_PATTERN.findall(template) if query.get("reason_code") == "STRUCTURAL" else []
                ),
            },
            "user_inputs": inputs,
            "legacy_injection_site": legacy_injectable,
            "remediated": legacy_injectable and is_converted and all(
                i["generated_handling"] in ("bound_parameter", "schema_allowlisted_identifier")
                for i in user_inputs
            ),
        })

    return records


def scan_generated_code(data_access_source: str, converted: list[dict]) -> list[dict]:
    """
    Find text(...) calls whose SQL is not a string literal.

    Allowed only where the dynamic part is a schema-allowlisted identifier
    (f-string in an identifier template) or a choice between literal SQL
    strings (conditional template). Everything else is a violation.
    """

    shape_by_suffix = {
        f"_{str(c.get('query_id', '')).lower()}": c.get("query_shape")
        for c in converted if c.get("status") == "CONVERTED"
    }
    findings = []
    tree = ast.parse(data_access_source)

    for function in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        shape = next(
            (s for suffix, s in shape_by_suffix.items() if function.name.endswith(suffix)),
            None,
        )
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "text"):
                continue
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                continue

            kind = type(argument).__name__
            if kind == "JoinedStr" and shape in ("ORDER_BY_SELECT", "TABLE_IDENTIFIER_SELECT"):
                verdict = "allowed_schema_allowlisted_identifier"
            elif kind == "Name" and shape == "CONDITIONAL_SELECT":
                verdict = "allowed_literal_sql_selection"
            else:
                verdict = "VIOLATION_sql_built_from_variables"

            findings.append({
                "function": function.name,
                "line": node.lineno,
                "sql_argument_kind": kind,
                "verdict": verdict,
            })

    return findings


def build_security_report(
    queries: list[dict],
    converted: list[dict],
    assignments: list[dict],
    data_access_source: str | None = None,
) -> dict[str, Any]:
    records = build_query_records(queries, converted, assignments)
    scan = scan_generated_code(data_access_source, converted) if data_access_source else []

    converted_user_values = [
        i for r in records if r["conversion_status"] == "CONVERTED"
        for i in r["user_inputs"] if i["user_controlled"]
    ]
    handled = [
        i for i in converted_user_values
        if i["generated_handling"] in ("bound_parameter", "schema_allowlisted_identifier")
    ]
    legacy_sites = [r for r in records if r["legacy_injection_site"]]

    return {
        "summary": {
            "total_queries": len(records),
            "queries_with_user_input": sum(any(i["user_controlled"] for i in r["user_inputs"]) for r in records),
            "legacy_injection_sites": len(legacy_sites),
            "legacy_injection_sites_remediated": sum(r["remediated"] for r in legacy_sites),
            "legacy_injection_sites_not_converted": sum(r["conversion_status"] != "CONVERTED" for r in legacy_sites),
            "user_controlled_values_in_converted_queries": len(converted_user_values),
            "bound_or_allowlisted": len(handled),
            "injection_safety_coverage_percent": (
                round(100 * len(handled) / len(converted_user_values), 2)
                if converted_user_values else None
            ),
            "generated_code_text_calls_not_literal": len(scan),
            "generated_code_violations": sum(f["verdict"].startswith("VIOLATION") for f in scan),
        },
        "scope": {
            "user_input_sources": ["$" + s for s in USER_INPUT_SOURCES],
            "indirect_sources_reported_not_counted": ["$" + s for s in INDIRECT_SOURCES],
            "taint_analysis": (
                "intra-file, order-based (last assignment before use), transitive over "
                f"variables up to depth {MAX_TRACE_DEPTH}; function parameters and other "
                "files not traced except validated cross-file bindings"
            ),
            "coverage_definition": (
                "bound_or_allowlisted / user-controlled values in converted queries; "
                "unresolved queries are excluded and listed as not converted"
            ),
        },
        "queries": records,
        "generated_code_scan": scan,
    }


def generate_security_report(
    queries: list[dict],
    converted: list[dict],
    assignments: list[dict],
    data_access_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    source = Path(data_access_file).read_text(encoding="utf-8") if Path(data_access_file).exists() else None
    report = build_security_report(queries, converted, assignments, source)
    Path(output_file).write_text(json.dumps(report, indent=4, ensure_ascii=False), encoding="utf-8")
    return report
