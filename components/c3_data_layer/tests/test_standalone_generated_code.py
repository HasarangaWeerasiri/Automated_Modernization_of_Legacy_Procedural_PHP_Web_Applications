"""S7: generated code runs without C3 and carries no machine-specific paths."""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from c3.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[3]
CLINIC = ROOT / "benchmarks" / "apps" / "clinic_inventory_legacy_hard"

CHILD = r"""
import importlib.util, json, sys
sys.modules["c3"] = None  # any import of C3 now fails
from sqlalchemy.dialects import mysql

class Session:
    def __init__(self): self.sql = []
    def execute(self, statement, params=None):
        self.sql.append(str(statement.compile(dialect=mysql.dialect())))
        class R:
            rowcount = 0; lastrowid = 0
            def mappings(self): return self
            def all(self): return []
        return R()
    def commit(self): pass

results = {}
for name in ("models", "data_access"):
    spec = importlib.util.spec_from_file_location(name, sys.argv[1] + "/" + name + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results[name] = module

da = results["data_access"]
session = Session()
da.select_patients_q012(session, {"sort": "email"})
da.select_patients_q011(session, {"ids": "3,4"})
da.select_doctors_q014(session, {"active": "1"})
rejected = []
for call in (lambda: da.select_patients_q012(Session(), {"sort": "email; DROP TABLE x"}),
             lambda: da.select_q013(Session(), {"table": "patients WHERE 1=1"}),
             lambda: da.update_appointment_q020(Session(), {"id": "1 OR 1=1"})):
    try:
        call()
    except ValueError:
        rejected.append(True)
print(json.dumps({"sql": session.sql, "rejected": len(rejected), "tables": len(results["models"].Base.metadata.tables)}))
"""


@pytest.fixture(scope="module")
def clinic_output(tmp_path_factory):
    out = tmp_path_factory.mktemp("s7") / "clinic"
    run_pipeline(CLINIC / "legacy", CLINIC / "database" / "schema.sql", out)
    return out


@pytest.mark.parametrize("name", ["data_access.py", "models.py"])
def test_generated_files_import_only_sqlalchemy_and_stdlib(clinic_output, name):
    tree = ast.parse((clinic_output / name).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])

    assert modules <= {"sqlalchemy", "re", "typing", "datetime", "decimal", "__future__"}, modules


def test_generated_code_has_no_absolute_paths(clinic_output):
    source = (clinic_output / "data_access.py").read_text(encoding="utf-8")

    assert str(ROOT).replace("\\", "/") not in source
    assert ":/" not in source and "Users" not in source
    assert "Migrated from 01_list_patients.php:4." in source


def test_generated_code_runs_with_c3_blocked(clinic_output):
    completed = subprocess.run(
        [sys.executable, "-c", CHILD, str(clinic_output)],
        capture_output=True, text=True, cwd=clinic_output, env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    assert result["sql"][0] == "SELECT id, full_name FROM patients ORDER BY email"
    assert "IN (__[POSTCOMPILE_ids])" in result["sql"][1]
    assert result["sql"][2] == "SELECT id, full_name FROM doctors WHERE active = 1"
    assert result["rejected"] == 3
    assert result["tables"] == 5


def test_inlined_helpers_do_not_collide():
    from c3.generation.runtime_inliner import inline_helpers

    imports, source = inline_helpers([
        ("c3.conversion.join_update_binding", "resolve_q020_id"),
        ("c3.conversion.conditional_concat_binding", "resolve_q016_department"),
        ("c3.conversion.in_list_binding", "resolve_q011_ids"),
    ])
    namespace = {}
    exec("\n".join(imports) + "\n" + source, namespace)

    # Each module keeps its own MAX_ID.
    assert namespace["_JOIN_UPDATE_BINDING_MAX_ID"] == 2**31 - 1
    assert namespace["_CONDITIONAL_CONCAT_BINDING_MAX_ID"] == 2**63 - 1
    with pytest.raises(ValueError):
        namespace["resolve_q020_id"]({"id": str(2**31)})
