"""Read-only helpers over one schema-1.0 AST document (ast/<fileId>.json).

Every later stage (CFG, includes, DFG, classifier) reads the AST through this
class, so the node-access rules live in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

# Nodes that start a new scope: their bodies are analysed as separate units,
# never as part of the code around them.
SCOPE_KINDS = frozenset({
    "Stmt_Function", "Stmt_ClassMethod", "Expr_Closure", "Expr_ArrowFunction",
})
CLASS_LIKE_KINDS = frozenset({"Stmt_Class", "Stmt_Interface", "Stmt_Trait", "Stmt_Enum"})


class AstDoc:
    def __init__(self, doc: dict[str, Any]):
        self.doc = doc
        self.file_id: str = doc["fileId"]
        self.path: str = doc["path"]
        self.root_id: str = doc["rootId"]
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in doc["nodes"]}

    @classmethod
    def load(cls, path: Path) -> "AstDoc":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- basic access -------------------------------------------------------
    def node(self, nid: str | None) -> dict[str, Any] | None:
        return self.nodes.get(nid) if nid else None

    def kind(self, nid: str | None) -> str | None:
        n = self.node(nid)
        return n["kind"] if n else None

    def field(self, nid: str, name: str) -> Any:
        return self.nodes[nid]["fields"].get(name)

    def field_list(self, nid: str, name: str) -> list[str]:
        v = self.field(nid, name)
        if v is None:
            return []
        if isinstance(v, list):
            return [x for x in v if isinstance(x, str)]
        return [v]

    def attr(self, nid: str, name: str, default: Any = None) -> Any:
        return self.nodes[nid]["attrs"].get(name, default)

    def parent(self, nid: str) -> str | None:
        return self.nodes[nid]["parent"]

    def loc(self, nid: str) -> dict[str, int] | None:
        return self.nodes[nid]["loc"]

    def line(self, nid: str) -> int | None:
        loc = self.loc(nid)
        return loc["startLine"] if loc else None

    # -- tree walks -----------------------------------------------------------
    def walk(self, nid: str, *, into_scopes: bool = False) -> Iterator[str]:
        """Pre-order walk of a subtree. By default does not enter nested functions/closures."""
        stack = [nid]
        while stack:
            cur = stack.pop()
            yield cur
            n = self.nodes[cur]
            if cur != nid and not into_scopes and (n["kind"] in SCOPE_KINDS or n["kind"] in CLASS_LIKE_KINDS):
                continue
            stack.extend(reversed(n["children"]))

    def ancestors(self, nid: str) -> Iterator[str]:
        cur = self.parent(nid)
        while cur is not None:
            yield cur
            cur = self.parent(cur)

    # -- names ----------------------------------------------------------------
    def name_of(self, nid: str | None) -> str | None:
        """Plain text of a Name / Identifier / Name_FullyQualified node, else None (dynamic)."""
        if nid is None:
            return None
        n = self.nodes[nid]
        if n["kind"] in ("Name", "Name_FullyQualified", "Name_Relative", "Identifier", "VarLikeIdentifier"):
            v = n["attrs"].get("name")
            return v if isinstance(v, str) else None
        return None

    def call_name(self, nid: str) -> str | None:
        """Lower-cased static name of a plain function call (PHP function names are case-insensitive)."""
        if self.kind(nid) != "Expr_FuncCall":
            return None
        name = self.name_of(self.field(nid, "name"))
        return name.lstrip("\\").lower() if name else None

    def unit_name(self, nid: str) -> str:
        k = self.kind(nid)
        if k == "Stmt_Function":
            return self.name_of(self.field(nid, "name")) or "?"
        if k == "Stmt_ClassMethod":
            cls = next((a for a in self.ancestors(nid) if self.kind(a) in CLASS_LIKE_KINDS), None)
            cname = (self.name_of(self.field(cls, "name")) if cls else None) or "{anonymous-class}"
            return f"{cname}::{self.name_of(self.field(nid, 'name')) or '?'}"
        if k == "Expr_Closure":
            return "{closure}"
        if k == "Expr_ArrowFunction":
            return "{arrow-fn}"
        return "{main}"
