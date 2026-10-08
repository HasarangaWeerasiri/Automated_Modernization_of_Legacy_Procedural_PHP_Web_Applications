"""Integration adapter for the C3 Data Layer Migration component."""

from pathlib import Path

from c3.pipeline import run_pipeline


ARTIFACT_NAMES = (
    "models.py",
    "data_access.py",
    "queries.json",
    "converted_queries.json",
    "schema.json",
    "column_usage.json",
    "unresolved_queries.json",
    "summary.json",
)


def migrate_data_layer(
    php_path: str | Path,
    schema_file: str | Path,
    output_dir: str | Path,
    php_executable: str = "php",
) -> dict:
    """
    Run C3 and return a structured integration result.

    The generated files are returned as paths, not executed.
    """

    php_path = Path(php_path).resolve()
    schema_file = Path(schema_file).resolve()
    output_dir = Path(output_dir).resolve()

    if not php_path.exists():
        raise FileNotFoundError(
            f"Legacy PHP source not found: {php_path}"
        )

    if not schema_file.is_file():
        raise FileNotFoundError(
            f"Database schema not found: {schema_file}"
        )

    summary = run_pipeline(
        php_path=php_path,
        schema_file=schema_file,
        output_dir=output_dir,
        php_executable=php_executable,
    )

    artifacts = {
        filename: str(output_dir / filename)
        for filename in ARTIFACT_NAMES
    }

    missing = [
        name
        for name, path in artifacts.items()
        if not Path(path).is_file()
    ]

    if missing:
        raise RuntimeError(
            "C3 pipeline did not produce expected artifacts: "
            + ", ".join(missing)
        )

    return {
        "component": "C3",
        "status": "completed",
        "summary": summary,
        "artifacts": artifacts,
    }
