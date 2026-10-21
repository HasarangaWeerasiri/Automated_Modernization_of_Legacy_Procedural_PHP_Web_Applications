"""Before/after metrics and the architecture advisor, end to end on cached ASTs (no PHP needed)."""

import json
from pathlib import Path

from c1 import architecture, metrics
from c1.__main__ import analyse, fingerprint
from test_pipeline_e2e import make_out  # same small app the end-to-end test uses


def _run(tmp_path: Path) -> Path:
    src, out = make_out(tmp_path, "m")
    analyse(out, src, "T", [])
    return out


def test_metrics_counts_unguarded_ear_and_tangling(tmp_path):
    out = _run(tmp_path)
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    sec, st = m["security"], m["structure"]
    assert sec["S2_executionAfterRedirect"]["after"] == 0
    assert sec["S3_sqlBuiltFromInput"]["before"]["queriesBuiltFromInput"] == 1  # add.php pastes $_POST['n'] into SQL
    assert sec["S3_sqlBuiltFromInput"]["after"]["queriesBuiltFromInput"] == 0
    assert sec["S1_missingAuthorization"]["before"]["pages"] == 1  # add.php
    t1 = st["T1_concernTangling"]["before"]
    assert 0 <= t1["mixTwoOrMore"] <= t1["files"] and t1["percentMixed"] == round(100 * t1["mixTwoOrMore"] / t1["files"], 1)
    assert st["C1_cyclomaticComplexity"]["before"]["max"] >= 1
    assert set(m["totals"]) >= {"unguardedDataPages", "earBefore", "sqlFromInputBefore", "tangledFilesPercent"}


def test_architecture_options_are_scored_and_explained(tmp_path):
    out = _run(tmp_path)
    a = json.loads((out / "architecture.json").read_text(encoding="utf-8"))
    assert [o["id"] for o in a["options"]] == list(architecture.CANDIDATES)
    assert a["recommended"] in architecture.CANDIDATES and a["generated"] == "layered-monolith"
    for o in a["options"]:
        assert 0 <= o["score"] <= 100 and all("why" in r for r in o["reasons"])
    assert [o["generation"] for o in a["options"]] == ["available", "planned", "planned"]


def test_rules_prefer_monolith_for_small_coupled_apps_and_split_large_separate_ones():
    small = {"endpoints": 10, "domainTables": 3, "areas": 1, "crossAreaShare": 0.0, "identityShared": False}
    s = architecture.score(small)
    assert s["layered-monolith"]["score"] > s["modular-monolith"]["score"] > s["microservices"]["score"]
    big = {"endpoints": 80, "domainTables": 20, "areas": 6, "crossAreaShare": 0.1, "identityShared": True}
    b = architecture.score(big)
    assert b["microservices"]["score"] > b["layered-monolith"]["score"]


def test_areas_group_tables_used_by_the_same_pages():
    eps = [
        {"id": "GET /api/cart", "path": "/api/cart", "table": "cart", "sources": [{"page": "cart.php"}], "params": []},
        {"id": "POST /api/cart_items", "path": "/api/cart_items", "table": "cart_items",
         "sources": [{"page": "cart.php"}], "params": []},
        {"id": "GET /api/pictures", "path": "/api/pictures", "table": "pictures",
         "sources": [{"page": "view.php"}], "params": []},
    ]
    a = architecture.areas(eps)
    assert sorted(map(sorted, a["clusters"])) == [["cart", "cart_items"], ["pictures"]]
    assert a["crossAreaShare"] == 0.0


def test_fingerprint_is_stable_and_changes_with_content(tmp_path):
    out = _run(tmp_path)
    f1 = fingerprint(out)
    assert f1 == fingerprint(out) and len(f1.replace(" ", "")) == 16
    (out / "extra.txt").write_text("x", encoding="utf-8")
    assert fingerprint(out) != f1
