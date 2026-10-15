"""Copy C3 runtime helpers into generated code (S7: standalone output).

Generated data_access.py must run without C3 installed. Helpers such as
resolve_q012_sort() are therefore copied into the generated file, together
with every module-level function and constant they use (followed through
`from c3... import` lines). Constants are renamed with a module prefix,
because different helper modules use the same names (e.g. MAX_ID) with
different values. Only standard-library imports may remain.
"""

import ast
import importlib
import inspect
import re


def _module_parts(module_name: str):
    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    tree = ast.parse(source)
    definitions, constants, imports = {}, {}, {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            definitions[node.name] = node
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            for target in node.targets:
                constants[target.id] = node
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = (alias.name, None)
    return source.splitlines(), definitions, constants, imports


def _used_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def inline_helpers(helpers: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Return (import lines, source block) for the given (module, function) helpers."""

    emitted: dict[tuple[str, str], str | None] = {}
    order: list[tuple[str, str]] = []
    import_lines: set[str] = set()
    renames: dict[str, dict[str, str]] = {}

    def visit(module_name: str, name: str) -> None:
        key = (module_name, name)
        if key in emitted:
            return
        emitted[key] = None
        lines, definitions, constants, imports = _module_parts(module_name)
        node = definitions.get(name) or constants.get(name)
        if node is None:
            raise ValueError(f"{module_name}.{name} not found")

        for used in sorted(_used_names(node) - {name}):
            if used in definitions or used in constants:
                visit(module_name, used)
            elif used in imports:
                source_module, attribute = imports[used]
                if source_module.startswith("c3."):
                    visit(source_module, attribute)
                elif attribute is None:
                    import_lines.add(f"import {source_module}")
                else:
                    import_lines.add(f"from {source_module} import {attribute}")

        if name in constants:
            prefix = module_name.rsplit(".", 1)[-1]
            renames.setdefault(module_name, {})[name] = f"_{prefix}_{name}".upper()

        emitted[key] = "\n".join(lines[node.lineno - 1: node.end_lineno])
        order.append(key)

    for module_name, name in helpers:
        visit(module_name, name)

    functions = [name for _, name in order if not name.isupper()]
    if len(functions) != len(set(functions)):
        raise ValueError(f"helper function name collision: {functions}")

    blocks = []
    for module_name, name in order:
        text = emitted[(module_name, name)]
        for old, new in renames.get(module_name, {}).items():
            text = re.sub(rf"\b{old}\b", new, text)
        blocks.append(text)

    return sorted(import_lines), "\n\n\n".join(blocks)
