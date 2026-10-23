# What this file does: reads the rule numbers (R-I4 = 3, R-I5 = 4) from config/boundary_rules.json.
"""Reads config/boundary_rules.json: the thresholds of the boundary rules.

The rules themselves are in docs/php-analysis/boundary-rules.md and are decided
by the component owner; their numbers live in the config file so they can be
changed without touching code.
"""

import json
from pathlib import Path

from src.model import BoundaryConfig

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "boundary_rules.json"
_INT_KEYS = ("small_if_max_elements", "fallback_component_min_elements")
_TAG_LIST_KEYS = ("table_section_tags", "void_tags")
RULES_VERSIONS = ("1.0", "1.1")  # boundary-rules v1.0 (frozen) and v1.1 (post-freeze change 1, with spec v0.14)


def load_boundary_config(path: Path = DEFAULT_PATH, rules: str | None = None) -> BoundaryConfig:
    """`rules` ("1.0" or "1.1", a leading "v" allowed) overrides the file's `rules_version`, e.g. to run the
    frozen v1.0 path (`--rules v1.0`)."""
    with Path(path).open("r", encoding="utf-8") as f:
        doc = json.load(f)
    if set(doc) != set(_INT_KEYS) | set(_TAG_LIST_KEYS) | {"rules_version"}:
        raise ValueError(f"{path}: expected keys {sorted(_INT_KEYS + _TAG_LIST_KEYS + ('rules_version',))}, "
                         f"got {sorted(doc)}")
    version = (rules or doc["rules_version"]).removeprefix("v")
    if version not in RULES_VERSIONS:
        raise ValueError(f"{path}: rules version must be one of {RULES_VERSIONS}, got {rules or doc['rules_version']!r}")
    for key in _INT_KEYS:
        if not isinstance(doc[key], int) or isinstance(doc[key], bool) or doc[key] < 0:
            raise ValueError(f"{path}: {key} must be a non-negative integer")
    for key in _TAG_LIST_KEYS:
        if not isinstance(doc[key], list) or not all(isinstance(tag, str) for tag in doc[key]):
            raise ValueError(f"{path}: {key} must be a list of tag names")
    return BoundaryConfig(
        small_if_max_elements=doc["small_if_max_elements"],
        fallback_component_min_elements=doc["fallback_component_min_elements"],
        table_section_tags=tuple(tag.lower() for tag in doc["table_section_tags"]),
        void_tags=tuple(tag.lower() for tag in doc["void_tags"]),
        rules_version=version,
    )
