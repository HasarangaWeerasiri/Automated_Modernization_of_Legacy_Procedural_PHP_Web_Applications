
"""One-command Data Layer Migration pipeline."""
from c3.mapping.schema_reader import read_schema, write_schema_json
import json
from dataclasses import asdict
from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.analysis.security_report import generate_security_report
from c3.conversion.model_builder import generate_models
from c3.conversion.query_converter import (
    convert_queries,
    write_converted_queries,
)
from c3.generation.emitter import generate_data_access
from c3.analysis.column_usage_generator import generate_column_usage
from c3.analysis.unresolved_report import generate_unresolved_report


# The proposal defines three primary outputs; every other file is a
# supporting artifact (reports and metadata that explain or justify them).
PRIMARY_OUTPUTS = (
    "models.py",
    "data_access.py",
    "unresolved_queries.json",
)


def build_summary(
    queries: list[dict],
    converted: list[dict],
) -> dict:
    """
    Count queries per stage without ambiguity.

    total = recovered + recovery_unresolved
    total = converted + not_converted
    converted = converted_by_general_rules + converted_by_query_specific_template

    A query can be recovery-unresolved and still converted, but only by a
    guarded query-specific template (that is how converted can exceed
    recovered). unresolved_queries and conversion_unresolved are kept with
    their original meaning for backward compatibility.
    """

    recovered_ids = {
        q.get("query_id") for q in queries if q.get("status") == "RECOVERED"
    }
    converted_rows = [q for q in converted if q.get("status") == "CONVERTED"]

    total = len(queries)
    recovered = len(recovered_ids)
    converted_count = len(converted_rows)

    return {
        "total_queries": total,
        "recovered_queries": recovered,
        "recovery_unresolved": total - recovered,
        "converted_queries": converted_count,
        "converted_by_general_rules": sum(
            q.get("conversion_rule") == "general" for q in converted_rows
        ),
        "converted_by_query_specific_template": sum(
            q.get("conversion_rule") == "query_specific_template"
            for q in converted_rows
        ),
        "recovered_not_converted": sum(
            q.get("query_id") in recovered_ids
            and q.get("status") != "CONVERTED"
            for q in converted
        ),
        "not_converted": total - converted_count,
        # Backward-compatible keys (original meaning).
        "unresolved_queries": total - recovered,
        "conversion_unresolved": total - converted_count,
    }


def run_pipeline(
    php_path: str | Path,
    schema_file: str | Path,
    output_dir: str | Path,
    php_executable: str = "php",
) -> dict:
    """Run the supported migration stages and write their outputs."""

    php_path = Path(php_path).resolve()
    schema_file = Path(schema_file).resolve()
    output_dir = Path(output_dir).resolve()

    if not php_path.exists():
        raise FileNotFoundError(f"PHP path not found: {php_path}")

    if not schema_file.is_file():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    output_dir.mkdir(parents=True, exist_ok=True)

    queries_file = output_dir / "queries.json"
    converted_file = output_dir / "converted_queries.json"
    models_file = output_dir / "models.py"
    data_access_file = output_dir / "data_access.py"
    column_usage_file = output_dir / "column_usage.json"
    unresolved_file = output_dir / "unresolved_queries.json"

    print("[1/6] Recovering legacy SQL...")
    ast_data = analyze_php_ast(
        php_path,
        php_executable=php_executable,
    )
    queries = recover_queries_from_ast(ast_data)
    queries_file.write_text(
        json.dumps(queries, indent=4, default=str),
        encoding="utf-8",
    )

    print("[2/6] Converting recovered queries...")
    # The schema is parsed before conversion so that identifier allowlists
    # are derived from it (stage 3 writes schema.json and the models).
    schema = read_schema(schema_file)
    converted = convert_queries(queries, schema=asdict(schema))
    write_converted_queries(converted, converted_file)


    print("[3/6] Parsing schema and generating SQLAlchemy models...")

    schema_json_file = output_dir / "schema.json"

    write_schema_json(schema, schema_json_file)

    generate_models(
        schema_json_file,
        models_file,
        notes_file=output_dir / "migration_notes.md",
    )

    print("[4/6] Generating data-access functions...")
    generate_data_access(converted_file, data_access_file)

    print("[5/6] Generating column-use report...")
    generate_column_usage(queries_file, column_usage_file)

    print("[6/6] Generating unresolved-query and security reports...")
    generate_unresolved_report(
        queries_file,
        unresolved_file,
        converted_file=converted_file,
    )
    generate_security_report(
        queries,
        converted,
        ast_data.get("assignments", []),
        data_access_file,
        output_dir / "security_report.json",
    )

    summary = build_summary(queries, converted)
    summary["primary_outputs"] = list(PRIMARY_OUTPUTS)
    summary["supporting_artifacts"] = sorted(
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and path.name not in PRIMARY_OUTPUTS
    ) + ["summary.json"]
    summary["output_directory"] = str(output_dir)

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=4),
        encoding="utf-8",
    )

    return summary
