"""Evaluation of the concern classifier against a hand-labelled corpus.

    python -m c1.evaluate sample analysis-out --src <app> --n 200 --out sheet.csv [--seed 329]
        writes a labelling sheet: statements drawn at random, stratified by file and statement
        kind. Our labels are NOT in the sheet, so the person labelling is not biased by them.
    python -m c1.evaluate score sheet.csv --labels hms=out-hms --labels wackopicko=out-wacko [--json r.json]
        compares the filled `gold` column with each app's labels.json (rows matched by `app`).

Metrics (master reference §9.7 — correctness, never BLEU-style similarity):
* coverage: share of statements the tool decided (not `undecided`)
* accuracy on decided statements; accuracy counting abstentions as wrong
* per-class precision / recall / F1, confusion matrix
* abstention quality: how often the gold label of an abstained statement is `mixed` or the
  annotator also marked it hard (`unsure` column)

Gold values: presentation | business_logic | data_access | mixed (and `skip` to drop a row).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

from .astdoc import AstDoc

CLASSES = ("presentation", "business_logic", "data_access", "mixed")
SHEET_COLUMNS = ["app", "nodeId", "file", "line", "kind", "code", "gold", "unsure", "notes"]


def _code(src: bytes | None, a: AstDoc, nid: str, limit: int = 300) -> str:
    if src is None:
        return ""
    loc = a.loc(nid)
    t = src[loc["startByte"]:loc["endByte"]].decode("utf-8", "replace") if loc else ""
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit] + " …"


def sample(out_dir: Path, src_root: Path | None, n: int, seed: int, app: str) -> list[dict[str, Any]]:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    labels = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))["labels"]
    asts = {f["fileId"]: AstDoc.load(out_dir / f["ast"]) for f in manifest["files"]}
    paths = {f["fileId"]: f["path"] for f in manifest["files"]}
    srcs = {}
    if src_root:
        for f in manifest["files"]:
            p = src_root / f["path"]
            if p.exists():
                srcs[f["fileId"]] = p.read_bytes()
    # strata: (file, statement kind); round-robin over shuffled strata = spread over the app
    strata: dict[tuple[str, str], list[str]] = {}
    for nid in sorted(labels):
        fid = nid.split("#")[0]
        a = asts[fid]
        k = a.kind(nid) or "?"
        if not k.startswith("Stmt_"):
            continue  # a statement corpus: conditions and loop headers are labelled but not sampled
        if k == "Stmt_InlineHTML" and not (a.attr(nid, "value") or "").strip():
            continue  # whitespace-only HTML says nothing about the classifier
        strata.setdefault((fid, k), []).append(nid)
    rng = random.Random(seed)
    keys = sorted(strata)
    rng.shuffle(keys)
    for k in keys:
        rng.shuffle(strata[k])
    picked: list[str] = []
    while len(picked) < n and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    rows = []
    for nid in sorted(picked, key=lambda x: (paths[x.split("#")[0]], x)):
        fid = nid.split("#")[0]
        a = asts[fid]
        rows.append({"app": app, "nodeId": nid, "file": paths[fid], "line": a.line(nid), "kind": a.kind(nid),
                     "code": _code(srcs.get(fid), a, nid), "gold": "", "unsure": "", "notes": ""})
    return rows


def write_sheet(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def score(labels: dict[str, dict], rows: list[dict[str, str]]) -> dict[str, Any]:
    used = [r for r in rows if r.get("gold", "").strip() and r["gold"].strip() != "skip"]
    bad = [r["nodeId"] for r in used if r["gold"].strip() not in CLASSES]
    if bad:
        raise ValueError(f"unknown gold values in rows {bad[:5]} (use {', '.join(CLASSES)} or skip)")
    confusion: dict[str, dict[str, int]] = {g: {p: 0 for p in CLASSES + ("undecided",)} for g in CLASSES}
    decided = correct = 0
    abstained_gold: dict[str, int] = {}
    unsure_on_abstain = 0
    for r in used:
        gold = r["gold"].strip()
        pred = labels.get(r["nodeId"], {}).get("concern", "undecided")
        confusion[gold][pred] += 1
        if pred == "undecided":
            abstained_gold[gold] = abstained_gold.get(gold, 0) + 1
            unsure_on_abstain += 1 if r.get("unsure", "").strip() else 0
        else:
            decided += 1
            correct += pred == gold
    per_class = {}
    for c in CLASSES:
        tp = confusion[c][c]
        fp = sum(confusion[g][c] for g in CLASSES if g != c)
        fn = sum(v for p, v in confusion[c].items() if p != c)
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
        per_class[c] = {"support": sum(confusion[c].values()), "precision": _r(prec), "recall": _r(rec), "f1": _r(f1)}
    total = len(used)
    return {
        "rows": total,
        "coverage": _r(decided / total) if total else None,
        "accuracyOnDecided": _r(correct / decided) if decided else None,
        "accuracyCountingAbstainAsWrong": _r(correct / total) if total else None,
        "abstained": total - decided,
        "abstainedByGold": dict(sorted(abstained_gold.items())),
        "abstainedMarkedUnsure": unsure_on_abstain,
        "perClass": per_class,
        "confusion": confusion,
    }


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.evaluate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="write a labelling sheet (our labels hidden)")
    s.add_argument("out_dir", type=Path)
    s.add_argument("--src", type=Path)
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=329)
    s.add_argument("--app", default="")
    s.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("score", help="score a filled sheet against labels.json")
    c.add_argument("sheet", type=Path)
    c.add_argument("--labels", action="append", required=True, help="app=analysis-out folder (repeatable)")
    c.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        rows = sample(args.out_dir, args.src, args.n, args.seed, args.app or args.out_dir.name)
        write_sheet(rows, args.out)
        print(f"{len(rows)} statements -> {args.out} (fill the `gold` column)")
        return 0
    labels: dict[str, dict] = {}
    for spec in args.labels:
        app, _, folder = spec.partition("=")
        for nid, v in json.loads((Path(folder) / "labels.json").read_text(encoding="utf-8"))["labels"].items():
            labels[f"{app}:{nid}"] = v
    with args.sheet.open(encoding="utf-8", newline="") as fh:
        rows = [dict(r, nodeId=f"{r['app']}:{r['nodeId']}") for r in csv.DictReader(fh)]
    try:
        rep = score(labels, rows)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in rep.items() if k != "confusion"}, indent=1))
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
