"""Migration Impact data for the C3 Migration Studio, read from the output folder of one C3 run.

Before: each SQL site as C3 found it in the PHP (queries.json: file:line, executor, the PHP call line, the recovered
SQL and the PHP values interpolated into it) and the legacy tables (schema.json). After: the generated function for
that site (data_access.py: exact code, bind parameters, PHP-to-parameter mapping, return shape), linked through
traceability_report.json, and the generated model per table (models.py: ORM class or Core Table, relationships).

The generated Python is parsed with the ast module, never imported or executed. Every count is read or counted from
a named file of the run; nothing is estimated, and there are no speed, effort or complexity figures.

Used by scripts/summarize_evidence.py (Evidence tab; SQL, source and table detail only for apps whose source may be
published) and by server.py (a live Try It run, shown only on the user's own computer).
"""

import ast
import json
import re
from collections import Counter
from pathlib import Path

NOTE = "These are measured properties of the generated code, not performance claims."
COVERAGE_NOTE = "Conversion coverage is measured over detected sites and does not establish complete application migration accuracy."
SITE = re.compile(r"Migrated from (.+?):(\d+)\.")
QUERY_ID = re.compile(r"Original query ID: (Q\d+)\.")
PARAMETER_LINE = re.compile(r"^\s*(\w+): (.+)$")
PLACEHOLDER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")  # SQLAlchemy named bind parameter in SQL text

# What each unresolved reason code means (from the C3 recovery/conversion code) and work that is NOT implemented.
REASON_INFO = {
    "BRANCHING": ("Conditional / branch-dependent SQL",
                  "The SQL text is built differently in if/else branches before the call; C3 does not choose a branch.",
                  "Path-sensitive reconstruction of each branch's SQL as separate functions (only the Clinic templates "
                  "Q014 and Q016 do this today, for those exact files)."),
    "LOOP_OR_SWITCH_CONTROL_FLOW": ("SQL built in a loop or switch",
                                    "The SQL text depends on loop iterations or on which switch case ran.",
                                    "Bounded unrolling of simple loops and per-case functions for switch statements."),
    "STRUCTURAL": ("Dynamic SQL structure (identifier or fragment)",
                   "A PHP value supplies a table name, ORDER BY column, IN-list or SQL fragment, which cannot be a bound "
                   "parameter.",
                   "General schema-derived allowlists for identifiers and expanding parameters for IN-lists (only the "
                   "Clinic templates Q011-Q013 do this today, for those exact files)."),
    "UNKNOWN_EXPR": ("Unsupported PHP expression",
                     "Part of the SQL comes from a PHP expression C3 cannot evaluate statically.",
                     "Support for more PHP expression forms (function results, array and object access)."),
    "UNSUPPORTED_SHAPE": ("Not a recognised CRUD statement",
                          "The recovered text is not a SELECT, INSERT, UPDATE or DELETE that C3 converts.",
                          "Recognising further statement types."),
    "CONVERSION_UNSUPPORTED": ("Unsupported query transformation",
                               "The SQL was recovered, but its shape (for example LEFT JOIN or an implicit comma join) "
                               "is outside the converter's supported forms.",
                               "Conversion rules for LEFT JOIN and implicit joins."),
    "UNSUPPORTED_EXECUTOR": ("Unsupported database API",
                             "The call uses a database API C3 does not target.", "Further executor support."),
    "MISSING_SQL_ARGUMENT": ("SQL argument not found", "The SQL argument of the call could not be identified.",
                             "Wider call-argument resolution."),
    "MULTI_STATEMENT_EXECUTOR": ("Multi-statement execution",
                                 "Several statements run in one call; they cannot become one parameterised statement.",
                                 "Splitting multi-statement calls."),
}
USER_INPUT_DEFINITION = ("The security report counts a value as user-controlled only when it traces it, within the same "
                         "file and in order, to $_GET, $_POST, $_REQUEST or $_COOKIE. Function parameters and values "
                         "from other files are not traced (its stated scope).")


def _relative(path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError, TypeError):
        return Path(str(path)).name


def return_shape(expressions: list[str]) -> str:
    """Plain-language return shape of a generated function, from its return expressions."""
    if not expressions:
        return "nothing (raises NotImplementedError: flagged for manual review)"
    if all(e.endswith(".mappings().all()") for e in expressions):
        return "list of row mappings"
    if expressions == ["result"]:
        return "SQLAlchemy result (rowcount for writes)"
    if expressions == ["result.rowcount"]:
        return "number of affected rows"
    return "returns " + " / ".join(expressions)


def parse_functions(source: str, with_code: bool = False) -> dict:
    """Top-level functions of a generated data_access.py: parameters, bind keys, mapping, return shape, cited site."""
    functions = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        doc = ast.get_docstring(node) or ""
        site, query_id = SITE.search(doc), QUERY_ID.search(doc)
        # Bind parameters = the :name placeholders in the function's SQL string constants (not the docstring,
        # and not other dicts such as template metadata).
        docstring_node = node.body[0].value if node.body and isinstance(node.body[0], ast.Expr) else None
        bind_keys = []
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and inner is not docstring_node:
                bind_keys += [k for k in PLACEHOLDER.findall(inner.value) if k not in bind_keys]
        mapping, in_block = [], False
        for line in doc.splitlines():
            if line.startswith("Parameters (PHP value at the call)"):
                in_block = True
            elif in_block and PARAMETER_LINE.match(line):
                name, php = PARAMETER_LINE.match(line).groups()
                mapping.append({"parameter": name, "php_value": php})
            elif in_block and line.strip():
                in_block = False
        returns = sorted({ast.unparse(r.value) for r in ast.walk(node) if isinstance(r, ast.Return) and r.value is not None})
        functions[node.name] = {
            "name": node.name,
            "parameters": [a.arg for a in node.args.args if a.arg != "session"],
            "keyword_only": [a.arg for a in node.args.kwonlyargs],
            "bind_keys": bind_keys,
            "parameter_mapping": mapping,
            "returns": return_shape(returns),
            "raises_not_implemented": any(isinstance(r, ast.Raise) and "NotImplementedError" in ast.unparse(r) for r in ast.walk(node)),
            "cited_site": f"{site.group(1)}:{site.group(2)}" if site else None,
            "cited_query_id": query_id.group(1) if query_id else None,
            **({"code": ast.get_source_segment(source, node)} if with_code else {}),
        }
    return functions


def parse_models(source: str) -> dict:
    """Generated models.py: table name -> ORM class or Core Table, with its relationship() attributes."""
    models = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            table, relationships = None, []
            for item in node.body:
                if not isinstance(item, ast.Assign) or len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                    continue
                name, value = item.targets[0].id, item.value
                if name == "__tablename__" and isinstance(value, ast.Constant):
                    table = value.value
                elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "relationship" and value.args \
                        and isinstance(value.args[0], ast.Constant):
                    relationships.append({"name": name, "target": value.args[0].value})
            if table:
                models[table.lower()] = {"name": node.name, "kind": "ORM class", "relationships": relationships}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "Table" and node.value.args and isinstance(node.value.args[0], ast.Constant):
            target = node.targets[0].id if isinstance(node.targets[0], ast.Name) else node.value.args[0].value
            models[node.value.args[0].value.lower()] = {"name": target, "kind": "Core Table (no primary key)", "relationships": []}
    return models


def _foreign_keys(table: dict) -> list[dict]:
    keys = []
    for fk in table.get("foreign_keys", []):
        keys.append({"columns": fk.get("columns") or [fk["column"]], "referenced_table": fk["referenced_table"],
                     "referenced_columns": fk.get("referenced_columns") or [fk["referenced_column"]]})
    return keys


def _item(key, label, value, of, file, field=None, derivation=None, note=None):
    return {"key": key, "label": label, "value": value, "of": of,
            "source": {"file": file, **({"field": field} if field else {}), **({"derivation": derivation} if derivation else {})},
            **({"note": note} if note else {})}


def _php_line(path, line) -> str | None:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[int(line) - 1].strip() if 0 < int(line) <= len(lines) else None
    except (OSError, ValueError, TypeError):
        return None


def build(out_dir, php_root, *, detail: bool, source_prefix: str = "", e4: dict | None = None,
          include_reason_text: bool = True) -> dict:
    """Migration Impact of one run. detail=False returns counts only (no SQL, source line, file, table or column names)."""
    out = Path(out_dir)
    load = lambda name: json.loads((out / name).read_text(encoding="utf-8"))
    summary, queries, converted = load("summary.json"), load("queries.json"), load("converted_queries.json")
    unresolved, trace, security, schema = (load("unresolved_queries.json"), load("traceability_report.json"),
                                           load("security_report.json"), load("schema.json"))
    access_source = (out / "data_access.py").read_text(encoding="utf-8")
    functions = parse_functions(access_source, with_code=detail)
    models = parse_models((out / "models.py").read_text(encoding="utf-8"))
    src = lambda name: f"{source_prefix}{name}"

    if summary["converted_queries"] + summary["unresolved_total"] != summary["total_queries"]:
        raise ValueError("summary.json: converted + unresolved != detected; the coverage bar would not add up")
    converted_by_id = {c["query_id"]: c for c in converted}
    unresolved_by_id = {u["query_id"]: u for u in unresolved}
    trace_by_id = {t["query_id"]: t for t in trace}
    security_by_id = {r["query_id"]: r for r in security["queries"]}
    data_access = [t["generated_function"] for t in trace
                   if t.get("generated_function_kind") == "data_access" and t.get("generated_function") in functions]
    converted_ok = [c for c in converted if c.get("status") == "CONVERTED"]
    sec = security["summary"]
    fk_count = sum(len(_foreign_keys(t)) for t in schema["tables"])
    relationships = sum(len(m["relationships"]) for m in models.values())
    citing = sum(1 for name in data_access if functions[name]["cited_site"])
    with_binds = [name for name in data_access if functions[name]["bind_keys"]]
    orm = sum(m["kind"] == "ORM class" for m in models.values())
    core = sum(m["kind"].startswith("Core") for m in models.values())

    reasons = {}
    for u in unresolved:
        code = u.get("reason_code") or "UNKNOWN"
        entry = reasons.setdefault(code, {"code": code, "count": 0, "stages": Counter(), "reasons": []})
        entry["count"] += 1
        entry["stages"][u.get("stage") or "recovery"] += 1
        if include_reason_text and u.get("reason") and u["reason"] not in entry["reasons"]:
            entry["reasons"].append(u["reason"])
    limitations = []
    for code, entry in sorted(reasons.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        category, why, future = REASON_INFO.get(code, (code, "See the unresolved-query report.", "Not defined."))
        limitations.append({"code": code, "count": entry["count"], "stages": dict(entry["stages"]), "category": category,
                            "why": why, "reasons": entry["reasons"], "future_work": future,
                            "future_work_status": "Proposed future work, not implemented"})

    facts = {
        "detected": summary["total_queries"], "recovered": summary["recovered_queries"],
        "converted": summary["converted_queries"], "general_rules": summary["converted_by_general_rules"],
        "templates": summary["converted_by_query_specific_template"], "unresolved": summary["unresolved_total"],
        "unresolved_recovery": summary["unresolved_recovery_stage"], "unresolved_conversion": summary["unresolved_conversion_stage"],
        "php_files": len({q["source_file"] for q in queries}),
        "dynamic_value_sites": sum(1 for q in queries if q.get("dynamic_variables")),
        "data_access_functions": len(data_access), "functions_with_bind_parameters": len(with_binds),
        "bind_parameters": sum(len(functions[name]["bind_keys"]) for name in with_binds),
        "legacy_injection_sites": sec["legacy_injection_sites"], "legacy_injection_sites_remediated": sec["legacy_injection_sites_remediated"],
        "user_controlled_values": sec["user_controlled_values_in_converted_queries"], "user_values_bound": sec["bound_or_allowlisted"],
        "generated_code_violations": sec["generated_code_violations"],
        "tables": len(schema["tables"]), "columns": sum(len(t["columns"]) for t in schema["tables"]),
        "tables_with_primary_key": sum(1 for t in schema["tables"] if any(c.get("primary_key") for c in t["columns"])),
        "foreign_keys": fk_count, "orm_classes": orm, "core_tables": core, "relationships": relationships,
        "traceable_sites": sum(1 for t in trace if t.get("source_file") and t.get("source_line")), "trace_entries": len(trace),
        "functions_citing_site": citing,
        "sources": {
            "detected / recovered / converted / unresolved": src("summary.json"),
            "php_files, dynamic_value_sites": src("queries.json") + " (distinct source_file; dynamic_variables not empty)",
            "functions, bind parameters, docstring sites": src("data_access.py") + " (ast) + " + src("traceability_report.json"),
            "injection sites, user-controlled values, violations": src("security_report.json") + " summary",
            "tables, columns, keys": src("schema.json"), "ORM / Core / relationships": src("models.py") + " (ast)",
        },
    }

    explanations = [USER_INPUT_DEFINITION]
    if with_binds and not sec["user_controlled_values_in_converted_queries"]:
        explanations.append(
            f"{len(with_binds)} generated function(s) take bound runtime parameters, but the security report traced none of "
            "those values to a request superglobal, so it reports 0 of 0 user-controlled values. The two counts have "
            "different definitions; 0 of 0 does not show that the application has no user input.")

    benefits = [
        _item("functions_generated", "Data-access functions generated", summary["converted_queries"], summary["total_queries"],
              src("summary.json"), field="converted_queries (of total_queries)",
              note=f"general rules {summary['converted_by_general_rules']}, query-specific templates "
                   f"{summary['converted_by_query_specific_template']}"),
        _item("queries_with_bound_parameters", "Converted queries that take bound parameters",
              sum(1 for c in converted_ok if c.get("parameters")), len(converted_ok), src("converted_queries.json"),
              derivation="status == CONVERTED and parameters not empty (of all CONVERTED)",
              note="the other converted queries take no runtime value"),
        _item("user_values_bound", "User-controlled values bound or allow-listed", sec["bound_or_allowlisted"],
              sec["user_controlled_values_in_converted_queries"], src("security_report.json"),
              field="summary.bound_or_allowlisted (of summary.user_controlled_values_in_converted_queries)"),
        _item("injection_sites_remediated", "Legacy injection sites converted to bound parameters",
              sec["legacy_injection_sites_remediated"], sec["legacy_injection_sites"], src("security_report.json"),
              field="summary.legacy_injection_sites_remediated (of summary.legacy_injection_sites)"),
        _item("generated_code_violations", "Generated-code scan violations", sec["generated_code_violations"], None,
              src("security_report.json"), field="summary.generated_code_violations"),
        e4 or _item("e4", "E4 injection check (functions SAFE)", None, None, None,
                    note="Not measured for this run: E4 is a separate evaluation, not run on uploads."),
        _item("orm_classes", "ORM model classes generated", orm, len(schema["tables"]), src("models.py"),
              derivation="classes with __tablename__ (of tables in schema.json)"),
        _item("core_tables", "Core Tables generated (tables without a primary key)", core, len(schema["tables"]), src("models.py"),
              derivation="Table(...) assignments (of tables in schema.json)"),
        _item("relationships", "Relationships generated", relationships, None, src("models.py"),
              derivation="relationship(...) attributes", note=f"schema.json declares {fk_count} foreign key(s)"),
        _item("traceable_sites", "Detected SQL sites with their PHP file:line",
              sum(1 for t in trace if t.get("source_file") and t.get("source_line")), len(trace), src("traceability_report.json"),
              derivation="entries with source_file and source_line"),
        _item("functions_citing_site", "Generated functions that cite their PHP file:line in the docstring", citing,
              len(data_access), src("data_access.py"),
              derivation="docstring 'Migrated from <file>:<line>' (of data-access functions in traceability_report.json)",
              note=None if citing == len(data_access) else "the other functions carry their PHP site only in "
                   "traceability_report.json (their docstring does not cite it)"),
        _item("flagged_for_review", "Sites flagged for manual review (no code generated)", summary["unresolved_total"],
              summary["total_queries"], src("summary.json"), field="unresolved_total (of total_queries)"),
    ]
    result = {"note": NOTE, "coverage_note": COVERAGE_NOTE, "detail": detail, "facts": facts, "limitations": limitations,
              "explanations": explanations, "benefits": benefits}
    if not detail:
        return result

    model_by_table = {t["name"].lower(): models.get(t["name"].lower()) for t in schema["tables"]}
    rows = []
    for q in queries:
        qid = q["query_id"]
        conv, unres, tr, sq = converted_by_id.get(qid, {}), unresolved_by_id.get(qid), trace_by_id.get(qid, {}), security_by_id.get(qid, {})
        function = functions.get(tr.get("generated_function"))
        table = (conv.get("table") or "").lower() or None
        rows.append({
            "query_id": qid, "site": f"{_relative(q['source_file'], php_root)}:{q['source_line']}",
            "executor": q.get("executor"), "php_argument": q.get("sql_argument"), "operation": q.get("operation"),
            "php_line": _php_line(q["source_file"], q["source_line"]),
            "legacy_sql": q.get("recovered_sql"), "dynamic_values": q.get("dynamic_variables") or [],
            "recovery_status": q.get("status"),
            "status": "converted" if conv.get("status") == "CONVERTED" else "flagged",
            "conversion_rule": conv.get("conversion_rule"),
            "generated_sql": conv.get("parameterized_sql") or conv.get("base_sql"),
            "flag": {"stage": unres["stage"], "reason_code": unres.get("reason_code"), "reason": unres.get("reason"),
                     "action": unres.get("action")} if unres else None,
            "function": function, "function_kind": tr.get("generated_function_kind"),
            "table": table, "model": model_by_table.get(table) if table else None,
            "security": {"legacy_injection_site": sq.get("legacy_injection_site", False), "remediated": sq.get("remediated", False),
                         "values": [{k: i.get(k) for k in ("variable", "sources", "user_controlled", "legacy_risk", "generated_handling")}
                                    for i in sq.get("user_inputs", [])]},
        })
    tables = []
    for t in schema["tables"]:
        model = models.get(t["name"].lower())
        primary_key = [c["name"] for c in t["columns"] if c.get("primary_key")]
        tables.append({
            "table": t["name"],
            "columns": [{"name": c["name"], "type": c["data_type"], "nullable": c["nullable"], "primary_key": c.get("primary_key", False),
                         "auto_increment": c.get("auto_increment", False), "unique": c.get("unique", False)} for c in t["columns"]],
            "primary_key": primary_key, "foreign_keys": _foreign_keys(t),
            "model": model, "no_primary_key": not primary_key,
        })
    result.update(queries=rows, tables=tables)
    return result
