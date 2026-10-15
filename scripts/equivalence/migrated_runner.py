"""E1 generated side: call one function of the generated data_access.py.

Each generated parameter is bound to the value of the PHP variable of the
same name at the legacy call, computed by legacy_runner.php in "raw" mode
(SQL escaping functions are the identity there, because escaping is replaced
by binding; intval, trim, htmlspecialchars, md5 ... are kept). A parameter
with no PHP variable of that name is reported, never guessed.
"""

import importlib.util
import inspect
import re
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
        if name != "session" and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL, p.KEYWORD_ONLY)
    ]


ELEMENT = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\[\s*(?:'([^']*)'|\"([^\"]*)\"|([0-9]+))\s*\]$")


def bind(function, raw_variables: dict, sources: dict | None = None, arrays: dict | None = None) -> tuple[dict, list[str]]:
    """Bind parameters by name; array-element parameters (GET_ID) through
    C3's dynamic_variable_sources ($_GET['ID']) to the PHP array value."""

    arguments, missing = {}, []
    for name in parameters(function):
        if name in raw_variables:
            arguments[name] = raw_variables[name]
            continue
        match = ELEMENT.match((sources or {}).get(name, ""))
        if match:
            container = (arrays or {}).get(match.group(1))
            key = match.group(2) if match.group(2) is not None else match.group(3) if match.group(3) is not None else match.group(4)
            if isinstance(container, dict) and str(key) in {str(k) for k in container}:
                arguments[name] = next(v for k, v in container.items() if str(k) == str(key))
                continue
            if isinstance(container, list) and key.isdigit() and int(key) < len(container):
                arguments[name] = container[int(key)]
                continue
        missing.append(name)
    return arguments, missing


def call(function, engine, raw_variables: dict, sources: dict | None = None, arrays: dict | None = None) -> dict:
    arguments, missing = bind(function, raw_variables, sources, arrays)
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
