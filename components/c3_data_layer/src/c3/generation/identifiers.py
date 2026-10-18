"""Valid, collision-free Python identifiers for names taken from the legacy app.

Table, column and PHP variable names are legal in MySQL and PHP but may not be
legal Python names: keywords (class, from, global), spaces or hyphens, or a
leading digit. Generated code must never contain them as identifiers. The real
name is always kept where it matters (SQL text, bind keys, database column
names); only the Python-side name changes.
"""

import keyword
import re

# Names the generated data-access functions use for themselves.
DATA_ACCESS_RESERVED = frozenset({"session", "commit", "statement", "params", "result", "text", "to_row_count"})

# Attribute names SQLAlchemy's declarative base reserves on a mapped class.
ORM_RESERVED = frozenset({"metadata", "registry", "__tablename__", "__table__", "__table_args__", "__mapper__",
                          "__mapper_args__", "__abstract__"})


def python_name(name: str, taken=frozenset()) -> str:
    """
    Return name unchanged when it is a valid, non-keyword Python identifier
    that is not in taken. Otherwise return a safe one: characters that are not
    allowed become "_", a leading digit gets a "_" prefix, and a trailing "_"
    is added until the name is neither a keyword nor already taken.

        class -> class_     first name -> first_name     2fa -> _2fa
    """

    if name.isidentifier() and not keyword.iskeyword(name) and name not in taken:
        return name

    candidate = re.sub(r"\W", "_", name, flags=re.ASCII) or "_"
    if candidate[0].isdigit():
        candidate = "_" + candidate
    while keyword.iskeyword(candidate) or candidate in taken or not candidate.isidentifier():
        candidate += "_"
    return candidate


def assign_names(names, reserved=frozenset()) -> dict[str, str]:
    """
    Original name -> Python name for a group that shares one namespace (the
    arguments of one function, the attributes of one class). Names that are
    already valid and not reserved never change, and a renamed name never takes
    a name that another member of the group already uses.
    """

    names = list(dict.fromkeys(names))
    keep = {n for n in names if n.isidentifier() and not keyword.iskeyword(n) and n not in reserved}
    taken = set(reserved) | keep
    result = {}
    for name in names:
        if name in keep:
            result[name] = name
        else:
            result[name] = python_name(name, taken)
            taken.add(result[name])
    return result
