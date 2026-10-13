"""Write docs/c2-results.md: Component 2's results on the ten mocks, for the report.

Reads only what the pipeline already wrote: output/stage{1..5}_<mock>.json and the compile check's
output/tsc_<mock>.json. Nothing is recomputed. The header names the git commit (and its date) the
outputs were produced from, so the same commit and outputs always give byte-identical Markdown.

Usage, from component2_frontend/ (after `--stage 5` on every mock and tools/compile_check.py):
    venv/Scripts/python.exe tools/results_table.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
DEFAULT_MARKDOWN = ROOT / "docs" / "c2-results.md"
MOCKS = ("list_while", "list_foreach", "admin", "detail", "edge_cases",
         "wp_guestbook", "wp_view", "wp_login", "wp_header", "wp_thumbnails")


def load(output: Path, mock: str) -> dict:
    stages = {}
    for stage in range(1, 6):
        path = output / f"stage{stage}_{mock}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing: run src/main.py --stage 5 for {mock} first")
        stages[stage] = json.loads(path.read_text(encoding="utf-8"))
    tsc = output / f"tsc_{mock}.json"
    stages["tsc"] = json.loads(tsc.read_text(encoding="utf-8")) if tsc.exists() else None
    return stages


def source(markdown: Path) -> str:
    """'commit <hash> of <date>', plus a note when files other than this table are uncommitted."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    commit, date = git("log", "-1", "--format=%h %cs").split()
    excluded = markdown.resolve().relative_to(ROOT).as_posix() if markdown.resolve().is_relative_to(ROOT) else None
    changes = git("status", "--porcelain", "--", ".", *([f":!{excluded}"] if excluded else []))
    return f"commit `{commit}` of {date}" + (" (+ uncommitted changes)" if changes else "")


def _walk(node):
    yield node
    for child in node["children"]:
        yield from _walk(child)


def only_unlabelled(reason: str) -> bool:
    """A review whose one reason is the missing label: it goes away when Component 1's labels arrive."""
    return {part.strip() for part in reason.split(",")} == {"unlabelled"}


def review_split(stage1: dict | None, stage2: dict, stage5: dict) -> dict:
    """Each stage's review count as (unlabelled only, any other reason). Stage 5 counts review-severity flags."""
    def split(reasons):
        reasons = list(reasons)
        unlabelled = sum(only_unlabelled(r) for r in reasons)
        return unlabelled, len(reasons) - unlabelled

    out = {"s2": split(n["reason"] for n in _walk(stage2["tree"]) if n["status"] == "review"),
           "s5": split(f["reason"] if f["flag"] == "stage2_review" else f["flag"]
                       for f in stage5["flags"] + stage5["c2todos"] if f["severity"] == "review")}
    if stage1 is not None:
        out["s1"] = split(n["reason"] for n in stage1["kept"] if n["status"] == "review")
    return out


def row(mock: str, s: dict) -> dict:
    c1, c2, c3, c4, c5 = (s[i]["counts"] for i in range(1, 6))
    nesting = sum(f["flag"] == "html_nesting_changed" for f in s[5]["flags"])
    review = review_split(s[1], s[2], s[5])
    assert tuple(sum(review[k]) for k in ("s1", "s2", "s5")) == (c1["review"], c2["review"], c5["severity"]["review"])
    return {
        "mock": mock,
        "s1": (c1["kept"], *review["s1"], c1["excluded"]),
        "s2": (c2["components"], c2["abstain"], *review["s2"]),
        "s3": (c3["field"], c3["ambiguous"], c3["unresolved"], c3["context"], c3["collection"]),
        "s4": (c4["matched"], c4["missing"], c4["excluded"], c4["cannotReconcile"], c4["unused"]),
        "s5": (c5["severity"]["todo"], *review["s5"], c5["severity"]["info"], c5["metrics"]["flagRate"]),
        "without": (c5["componentsWithoutC2Todo"], c5["components"]),
        "tsc": None if s["tsc"] is None else s["tsc"]["errors"],
        "nesting": nesting,
    }


def flag_reasons(all_stages: dict) -> dict:
    """(stage, reason, severity) -> count, for every flag of every stage."""
    counts = {}

    def add(stage, reason, severity):
        key = (stage, reason, severity)
        counts[key] = counts.get(key, 0) + 1

    for s in all_stages.values():
        for node in s[1]["kept"]:
            if node["status"] == "review":
                add(1, node["reason"], "review")
        for node in _walk(s[2]["tree"]):
            if node["status"] in ("review", "abstain"):
                add(2, node["reason"], node["status"])
            for flag in node["flags"]:
                add(2, flag, "flag")
        for component in s[3]["components"]:
            for need in component["needs"]:
                if need["flag"]:
                    add(3, need["flag"], "flag")
        for result in s[4]["results"] + s[4]["collections"]:
            if result["flagged"]:
                add(4, result["reason"], result["result"])
        for todo in s[5]["c2todos"]:
            add(5, f"{todo['kind']}: {todo['reason']}", todo["severity"])
        for flag in s[5]["flags"]:
            key = flag["flag"] if flag["flag"] == flag["reason"] else f"{flag['flag']}:{flag['reason']}"
            add(5, key, flag["severity"])
    return dict(sorted(counts.items()))


def cells(values) -> str:
    return " / ".join(str(v) for v in values)


def render(output: Path = OUTPUT, markdown: Path = DEFAULT_MARKDOWN) -> str:
    all_stages = {mock: load(output, mock) for mock in MOCKS}
    rows = [row(mock, all_stages[mock]) for mock in MOCKS]
    lines = [
        "# Component 2 — results on the mocks",
        "",
        f"Generated by `tools/results_table.py` from the stage outputs of {source(markdown)}. "
        "**Inputs are mocks, not real Member 01 / C1 input** (see CONTEXT.md §5–6: the contract and endpoint map "
        "were written by the agent, the WackoPicko labels are placeholders).",
        "",
        "## Table A — per mock",
        "",
        "| Mock | S1 ok / review unlabelled / review other / excluded | "
        "S2 components / abstain / review unlabelled / review other | "
        "S3 field / ambiguous / unresolved / context / collection | "
        "S4 matched / missing / excluded / cannot reconcile / unused | "
        "S5 todo / review unlabelled / review other / info / flag rate | "
        "S5 without C2Todo | tsc errors | Nesting flags |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    total = {key: None for key in ("s1", "s2", "s3", "s4", "s5", "without")}
    tsc_total, tsc_known, nesting_total = 0, True, 0
    for r in rows:
        tsc = "not run" if r["tsc"] is None else str(r["tsc"])
        lines.append(f"| {r['mock']} | {cells(r['s1'])} | {cells(r['s2'])} | {cells(r['s3'])} | {cells(r['s4'])} | "
                     f"{cells(r['s5'])} | {r['without'][0]}/{r['without'][1]} | {tsc} | {r['nesting']} |")
        for key in total:
            total[key] = r[key] if total[key] is None else tuple(a + b for a, b in zip(total[key], r[key]))
        tsc_known = tsc_known and r["tsc"] is not None
        tsc_total += r["tsc"] or 0
        nesting_total += r["nesting"]
    lines.append(f"| **All 10** | {cells(total['s1'])} | {cells(total['s2'])} | {cells(total['s3'])} | "
                 f"{cells(total['s4'])} | {cells(total['s5'])} | {total['without'][0]}/{total['without'][1]} | "
                 f"{tsc_total if tsc_known else 'not run for every mock'} | {nesting_total} |")
    lines += [
        "",
        "S1 ok = kept with status ok. Review is split in two: *unlabelled* = the only reason is that the node "
        "has no label (it goes away when Component 1's labels arrive); *other* = any other reason. "
        "S3 counts needs by class (one collection need per List). S4 counts field, "
        "ambiguous, unresolved and context needs, collections apart; `unused` is a lower bound (condition reads are "
        "not visible). S5 flag rate = todo + review; info is reported, not counted (generation-spec v0.3 §12). "
        "tsc errors come from tools/compile_check.py (strict).",
        "",
        "## Table B — every flag reason, all stages",
        "",
        "| Stage | Reason | Severity / status | Count |",
        "|---|---|---|---|",
    ]
    for (stage, reason, severity), count in flag_reasons(all_stages).items():
        lines.append(f"| {stage} | `{reason}` | {severity} | {count} |")
    lines += [
        "",
        "Severity / status uses each stage's own words: Stage 1 `review`; Stage 2 `review`, `abstain`, or `flag` "
        "(a tag-tracker flag on a node); Stage 3 `flag` (a need flagged for C3/C1); Stage 4 the result (`missing`, "
        "`cannot_reconcile`); Stage 5 `todo` / `review` / `info`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    markdown = Path(argv[0]) if argv else DEFAULT_MARKDOWN
    text = render(OUTPUT, markdown)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
