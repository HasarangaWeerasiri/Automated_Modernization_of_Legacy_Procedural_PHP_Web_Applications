"""Our Cooper-Harvey-Kennedy implementation against hand examples and networkx."""

from __future__ import annotations

import random

import pytest

from c1.dominance import dominates, immediate_dominators, reverse_postorder


def test_example_from_networkx_docs():
    # networkx.immediate_dominators docstring example
    succ = {1: [2, 3], 2: [5], 3: [4], 4: [5], 5: []}
    idom = immediate_dominators(succ, 1)
    assert sorted((k, v) for k, v in idom.items() if k != 1) == [(2, 1), (3, 1), (4, 3), (5, 1)]


def test_loop_and_unreachable():
    succ = {"e": ["h"], "h": ["b", "x"], "b": ["h"], "x": [], "dead": ["x"]}
    idom = immediate_dominators(succ, "e")
    assert idom == {"e": "e", "h": "e", "b": "h", "x": "h"}
    assert dominates(idom, "h", "x") and not dominates(idom, "b", "x")
    assert not dominates(idom, "e", "dead")


def test_reverse_postorder_starts_at_entry_and_is_deterministic():
    succ = {0: [1, 2], 1: [3], 2: [3], 3: []}
    assert reverse_postorder(succ, 0) == reverse_postorder(succ, 0)
    assert reverse_postorder(succ, 0)[0] == 0


def test_matches_networkx_on_random_graphs():
    nx = pytest.importorskip("networkx")
    rng = random.Random(329)
    for _ in range(300):
        n = rng.randint(2, 25)
        succ = {i: [] for i in range(n)}
        for _ in range(rng.randint(1, n * 3)):
            a, b = rng.randrange(n), rng.randrange(1, n)
            if b not in succ[a]:
                succ[a].append(b)
        g = nx.DiGraph()
        g.add_nodes_from(succ)
        g.add_edges_from((a, b) for a, kids in succ.items() for b in kids)
        expected = nx.immediate_dominators(g, 0)
        ours = immediate_dominators(succ, 0)
        expected = {k: v for k, v in expected.items() if k != 0}  # newer networkx omits start
        assert {k: v for k, v in ours.items() if k != 0} == expected
