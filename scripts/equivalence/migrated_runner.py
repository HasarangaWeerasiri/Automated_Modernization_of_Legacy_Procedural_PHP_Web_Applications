"""E1 generated side: call one function of the generated data_access.py.

Each generated parameter is bound to the value of the PHP variable of the
same name at the legacy call, computed by legacy_runner.php in "raw" mode
(SQL escaping functions are the identity there, because escaping is replaced
by binding; intval, trim, htmlspecialchars, md5 ... are kept). A parameter
with no PHP variable of that name is reported, never guessed.
"""

import importlib.util
import inspect
from pathlib import Path

from sqlalchemy.orm import Session


def load_module(data_access_file: Path):
    spec = importlib.util.spec_from_file_location(f"e1_data_access_{abs(hash(str(data_access_file)))}", data_access_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function_for(module, query_id: str):
    suffix = "_" + query_id.lower()
    matches = [
        getattr(module, name) for name in dir(module)
        if name.endswith(suffix) and callable(getattr(module, name)) and not name.startswith("unresolved_query_")
    ]
    return matches[0] if len(matches) == 1 else None


def parameters(function) -> list[str]:
    return [
        name for name, p in inspect.signature(function).parameters.items()
        if name != "session" and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
    ]


def bind(function, raw_variables: dict) -> tuple[dict, list[str]]:
    names = parameters(function)
    missing = [name for name in names if name not in raw_variables]
    return {name: raw_variables[name] for name in names if name in raw_variables}, missing


def call(function, engine, raw_variables: dict) -> dict:
    arguments, missing = bind(function, raw_variables)
    if missing:
        return {"status": "unbound_parameter", "missing": missing}

    with Session(engine) as session:
        try:
            result = function(session, **arguments)
        except Exception as error:  # recorded as a FAIL by compare.py
            session.rollback()
            return {"status": "error", "error": f"{type(error).__name__}: {error}"[:500], "arguments": arguments}

        if isinstance(result, list):
            columns = list(result[0].keys()) if result else []
            return {"status": "ok", "columns": columns, "rows": [list(row.values()) for row in result], "arguments": arguments}
        return {
            "status": "ok",
            "affected_rows": getattr(result, "rowcount", None),
            "insert_id": getattr(result, "lastrowid", None),
            "arguments": arguments,
        }
