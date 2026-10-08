from pathlib import Path

import pytest

from c3.integration import migrate_data_layer


def test_integration_adapter_success(tmp_path):
    root = Path(__file__).resolve().parents[3]
    benchmark = root / "benchmarks" / "apps" / "simple_crud"

    result = migrate_data_layer(
        php_path=benchmark / "legacy",
        schema_file=benchmark / "schema.sql",
        output_dir=tmp_path / "migration_output",
    )

    assert result["component"] == "C3"
    assert result["status"] == "completed"

    summary = result["summary"]
    assert summary["total_queries"] == 4
    assert summary["recovered_queries"] == 4
    assert summary["converted_queries"] == 4
    assert summary["unresolved_queries"] == 0

    assert len(result["artifacts"]) == 8

    for artifact_path in result["artifacts"].values():
        assert Path(artifact_path).is_file()


def test_integration_adapter_missing_php(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_data_layer(
            php_path=tmp_path / "missing_php",
            schema_file=tmp_path / "schema.sql",
            output_dir=tmp_path / "output",
        )


def test_integration_adapter_missing_schema(tmp_path):
    php_dir = tmp_path / "legacy"
    php_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        migrate_data_layer(
            php_path=php_dir,
            schema_file=tmp_path / "missing_schema.sql",
            output_dir=tmp_path / "output",
        )
