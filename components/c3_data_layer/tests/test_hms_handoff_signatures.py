"""H4: the HMS data_access signatures stay as frozen in output/hms_kishan0725/signatures.json.

Regenerates HMS from the (gitignored, unlicensed) local source; skipped when
the source is not present.
"""

import importlib.util
import inspect
import json
from pathlib import Path

import pytest

from c3.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[3]
PHP = ROOT / "benchmarks" / "apps" / "_app_only" / "hms_kishan0725"
SCHEMA = ROOT / "benchmarks" / "apps" / "hms_kishan0725" / "myhmsdb.sql"
FROZEN = ROOT / "output" / "hms_kishan0725" / "signatures.json"


@pytest.mark.skipif(not PHP.is_dir() or not SCHEMA.is_file(), reason="HMS source not available locally (no licence)")
def test_hms_signatures_match_the_frozen_handoff(tmp_path):
    run_pipeline(PHP, SCHEMA, tmp_path)
    spec = importlib.util.spec_from_file_location("hms_signature_check", tmp_path / "data_access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    current = {
        name: f"{name}{inspect.signature(function)}"
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__ and name.rsplit("_", 1)[-1][1:].isdigit()
        and not name.startswith("unresolved_query_")
    }
    frozen = {name: entry["signature"] for name, entry in json.loads(FROZEN.read_text(encoding="utf-8")).items()}

    assert current == frozen
