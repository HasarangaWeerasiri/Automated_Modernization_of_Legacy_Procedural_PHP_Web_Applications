"""Dominators for a CFG.

Algorithm: Cooper, Harvey & Kennedy, "A Simple, Fast Dominance Algorithm" (2006),
https://hdl.handle.net/1911/96345 . The same paper is the first reference of
networkx.immediate_dominators; tests/test_dominance.py checks we agree with it.

Definition used (from the paper): node n dominates b if n lies on every path from
the entry node to b. The immediate dominator of b is the dominator closest to b.

Like the paper, we assume a single entry and only consider nodes reachable from it.
"""

from __future__ import annotations

from typing import Hashable, Mapping, Sequence

Node = Hashable


def reverse_postorder(succ: Mapping[Node, Sequence[Node]], entry: Node) -> list[Node]:
    """Iterative DFS (no recursion limit). Successor order is respected, so the result is deterministic."""
    seen = {entry}
    post: list[Node] = []
    stack: list[tuple[Node, int]] = [(entry, 0)]
    while stack:
        node, i = stack[-1]
        kids = succ.get(node, ())
        if i < len(kids):
            stack[-1] = (node, i + 1)
            nxt = kids[i]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, 0))
        else:
            stack.pop()
            post.append(node)
    post.reverse()
    return post


def immediate_dominators(succ: Mapping[Node, Sequence[Node]], entry: Node) -> dict[Node, Node]:
    """Return {node: idom} for every node reachable from entry. The entry maps to itself."""
    order = reverse_postorder(succ, entry)
    index = {n: i for i, n in enumerate(order)}  # smaller index = earlier in reverse postorder
    preds: dict[Node, list[Node]] = {n: [] for n in order}
    for n in order:
        for s in succ.get(n, ()):
            if s in index:
                preds[s].append(n)

    idom: dict[Node, Node] = {entry: entry}

    def intersect(a: Node, b: Node) -> Node:
        # walk the two "fingers" up the dominator tree until they meet (paper, Figure 3)
        while a != b:
            while index[a] > index[b]:
                a = idom[a]
            while index[b] > index[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for b in order[1:]:
            processed = [p for p in preds[b] if p in idom]
            if not processed:
                continue
            new = processed[0]
            for p in processed[1:]:
                new = intersect(p, new)
            if idom.get(b) != new:
                idom[b] = new
                changed = True
    return idom


def dominates(idom: Mapping[Node, Node], a: Node, b: Node) -> bool:
    """True if a dominates b (every node dominates itself). False if b is unreachable."""
    if b not in idom:
        return False
    cur = b
    while True:
        if cur == a:
            return True
        parent = idom[cur]
        if parent == cur:
            return False
        cur = parent


def reverse_graph(succ: Mapping[Node, Sequence[Node]]) -> dict[Node, list[Node]]:
    """Predecessor map, used for post-dominators (dominators of the reversed graph from EXIT)."""
    rev: dict[Node, list[Node]] = {n: [] for n in succ}
    for n, kids in succ.items():
        for k in kids:
            rev.setdefault(k, []).append(n)
    return rev
