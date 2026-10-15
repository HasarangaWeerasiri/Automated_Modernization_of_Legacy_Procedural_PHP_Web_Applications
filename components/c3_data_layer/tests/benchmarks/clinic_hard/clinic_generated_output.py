"""Generate the Clinic pipeline output once per test session, in a temp folder.

The equivalence tests must exercise the code under test, not whatever happens
to sit in output/clinic_inventory_legacy_hard (an untracked, possibly stale
folder). A fresh run also makes the tests work on a clean clone.
"""

import tempfile
from functools import lru_cache
from pathlib import Path

from c3.pipeline import run_pipeline

COMPONENT_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = COMPONENT_DIR.parents[1]
CLINIC_DIR = REPO_ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard"


@lru_cache(maxsize=1)
def generated_clinic_output() -> Path:
    """Run C3 on the Clinic benchmark into a new temp folder and return it."""

    output_dir = Path(tempfile.mkdtemp(prefix="c3_clinic_output_"))

    run_pipeline(
        php_path=CLINIC_DIR / "legacy",
        schema_file=CLINIC_DIR / "database" / "schema.sql",
        output_dir=output_dir,
    )

    return output_dir


def generated_data_access_file() -> Path:
    return generated_clinic_output() / "data_access.py"
