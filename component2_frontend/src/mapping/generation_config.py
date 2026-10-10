"""Reads Stage 5's settings: config/generation.json and config/react_dom.json.

generation.json holds the owner-changeable settings of docs/generation-spec.md
(the escape-wrapper list, context reasons, tag lists). react_dom.json is what
React's TypeScript types accept, extracted from the pinned test app by
tools/gen_react_dom.py; it is data, not a setting.
"""

import json
from pathlib import Path

from src.model import GenerationConfig, ReactDom

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_PATH = CONFIG_DIR / "generation.json"
REACT_DOM_PATH = CONFIG_DIR / "react_dom.json"

_LISTS = ("escape_wrappers", "document_shell_tags", "preserve_whitespace_tags", "inline_tags",
          "legacy_link_attributes")
_MAPS = ("context_reasons", "condition_context_reasons", "attribute_renames", "content_as_prop", "dropped_tags")
_NESTED = ("form_state", "omitted_attributes")
_STRINGS = ("legacy_link_pattern", "api_base_url_env")


def load_react_dom(path: Path = REACT_DOM_PATH) -> ReactDom:
    with Path(path).open("r", encoding="utf-8") as f:
        doc = json.load(f)
    if not {"elements", "extends", "props", "css"} <= set(doc):
        raise ValueError(f"{path}: expected elements, extends, props and css (run tools/gen_react_dom.py)")
    return ReactDom(elements=doc["elements"], extends={k: tuple(v) for k, v in doc["extends"].items()},
                    props=doc["props"], css=doc["css"])


def load_generation_config(path: Path = DEFAULT_PATH, react_dom_path: Path = REACT_DOM_PATH) -> GenerationConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        doc = json.load(f)
    expected = set(_LISTS + _MAPS + _NESTED + _STRINGS)
    if set(doc) != expected:
        raise ValueError(f"{path}: expected keys {sorted(expected)}, got {sorted(doc)}")
    for key in _LISTS:
        if not isinstance(doc[key], list) or not all(isinstance(x, str) for x in doc[key]):
            raise ValueError(f"{path}: {key} must be a list of strings")
    for key in _MAPS:
        if not isinstance(doc[key], dict) or not all(isinstance(v, str) for v in doc[key].values()):
            raise ValueError(f"{path}: {key} must map strings to strings")
    for key in _NESTED:
        if not isinstance(doc[key], dict) or not all(
                isinstance(v, dict) and all(isinstance(x, str) for x in v.values()) for v in doc[key].values()):
            raise ValueError(f"{path}: {key} must map tags to {{attribute: string}}")
    for key in _STRINGS:
        if not isinstance(doc[key], str) or not doc[key]:
            raise ValueError(f"{path}: {key} must be a non-empty string")
    return GenerationConfig(
        escape_wrappers=tuple(doc["escape_wrappers"]),
        context_reasons=dict(doc["context_reasons"]),
        condition_context_reasons=dict(doc["condition_context_reasons"]),
        attribute_renames=dict(doc["attribute_renames"]),
        form_state={tag: dict(v) for tag, v in doc["form_state"].items()},
        content_as_prop=dict(doc["content_as_prop"]),
        omitted_attributes={tag: dict(v) for tag, v in doc["omitted_attributes"].items()},
        document_shell_tags=tuple(t.lower() for t in doc["document_shell_tags"]),
        dropped_tags={t.lower(): flag for t, flag in doc["dropped_tags"].items()},
        preserve_whitespace_tags=tuple(t.lower() for t in doc["preserve_whitespace_tags"]),
        inline_tags=tuple(t.lower() for t in doc["inline_tags"]),
        legacy_link_attributes=tuple(doc["legacy_link_attributes"]),
        legacy_link_pattern=doc["legacy_link_pattern"],
        api_base_url_env=doc["api_base_url_env"],
        react=load_react_dom(react_dom_path),
    )
