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


def load_boundary_config(path: Path = DEFAULT_PATH) -> BoundaryConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        doc = json.load(f)
    if set(doc) != set(_INT_KEYS) | set(_TAG_LIST_KEYS):
        raise ValueError(f"{path}: expected keys {sorted(_INT_KEYS + _TAG_LIST_KEYS)}, got {sorted(doc)}")
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
    )
