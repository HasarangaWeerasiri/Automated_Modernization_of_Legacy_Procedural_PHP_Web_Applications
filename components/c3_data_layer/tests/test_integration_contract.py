import json
from pathlib import Path

from c3.pipeline import run_pipeline


def test_c3_integration_contract(tmp_path):
    root = Path(__file__).resolve().parents[3]

    contract_file = root / "contracts" / "c3_data_layer_contract.json"
    assert contract_file.exists(), "C3 contract file is missing"

    contract = json.loads(
        contract_file.read_text(encoding="utf-8-sig")
    )

    assert contract["component_id"] == "C3"

    benchmark = root / "benchmarks" / "apps" / "simple_crud"

    output = tmp_path / "c3_output"

    summary = run_pipeline(
        php_path=benchmark / "legacy",
        schema_file=benchmark / "schema.sql",
        output_dir=output,
    )

    for filename in contract["outputs"]:
        assert (output / filename).is_file(), (
            f"Missing promised output: {filename}"
        )

    assert summary["total_queries"] == 4
    assert summary["recovered_queries"] == 4
    assert summary["converted_queries"] == 4
    assert summary["unresolved_queries"] == 0
