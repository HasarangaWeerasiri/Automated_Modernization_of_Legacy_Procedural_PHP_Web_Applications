"""The Clinic equivalence tests load a freshly generated data_access.py.

Runs without MySQL, so a clean clone checks the generation path that the
MySQL-dependent equivalence tests rely on.
"""

import importlib.util
import json

from clinic_generated_output import (
    generated_clinic_output,
    generated_data_access_file,
)


def test_generated_output_is_fresh_and_complete():
    output_dir = generated_clinic_output()

    assert "output" not in output_dir.parts[-2:]  # temp folder, not output/

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_queries"] == 22
    assert summary["converted_queries"] == 21


def test_generated_data_access_module_loads():
    spec = importlib.util.spec_from_file_location(
        "clinic_generated_check",
        generated_data_access_file(),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(module.select_patients_q001)
    assert callable(module.update_appointment_q017)
