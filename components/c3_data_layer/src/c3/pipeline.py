
"""One-command Data Layer Migration pipeline."""
from c3.mapping.schema_reader import read_schema, write_schema_json
import json
from pathlib import Path

from c3.mapping.sql_recovery import recover_queries
from c3.conversion.model_builder import generate_models
from c3.conversion.query_converter import (
    convert_queries,
    write_converted_queries,
)
from c3.generation.emitter import generate_data_access
from c3.analysis.column_usage_generator import generate_column_usage
from c3.analysis.unresolved_report import generate_unresolved_report


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
    queries = recover_queries(
        php_path,
        php_executable=php_executable,
    )
    queries_file.write_text(
        json.dumps(queries, indent=4, default=str),
        encoding="utf-8",
    )

    print("[2/6] Converting recovered queries...")
    converted = convert_queries(queries)
    write_converted_queries(converted, converted_file)


    print("[3/6] Parsing schema and generating SQLAlchemy models...")

    schema = read_schema(schema_file)

    schema_json_file = output_dir / "schema.json"

    write_schema_json(schema, schema_json_file)

    generate_models(schema_json_file, models_file)

    print("[4/6] Generating data-access functions...")
    generate_data_access(converted_file, data_access_file)

    print("[5/6] Generating column-use report...")
    generate_column_usage(queries_file, column_usage_file)

    print("[6/6] Generating unresolved-query report...")
    generate_unresolved_report(queries_file, unresolved_file)

    summary = {
        "total_queries": len(queries),
        "recovered_queries": sum(
            q.get("status") == "RECOVERED" for q in queries
        ),
        "converted_queries": sum(
            q.get("status") == "CONVERTED" for q in converted
        ),
        "unresolved_queries": sum(
            q.get("status") != "RECOVERED" for q in queries
        ),
        "conversion_unresolved": sum(
            q.get("status") != "CONVERTED" for q in converted
        ),
        "output_directory": str(output_dir),
    }

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=4),
        encoding="utf-8",
    )

    return summary
