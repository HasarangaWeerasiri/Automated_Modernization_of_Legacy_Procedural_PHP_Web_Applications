"""Write Component 2's results tables as Markdown, for the report, from the result files only (nothing is
recomputed; the same commit and result files always give byte-identical Markdown):

  (default)  docs/c2-results.md: the ten mocks, from output/stage{1..5}_<mock>.json and output/tsc_<mock>.json.
  phase4     the Phase 4 runtime tables (docs/phase4-results.md §3 / §5): for each app and database, the rules
             versions side by side, from the runs' output/runtime/<folder>/classified.json (PHASE4 names them).
  phase5     the Phase 5 metric tables (docs/eval-mapping.md §3): v1.0 and v1.1 side by side for each scored
             report (output/eval/<key>.json, written by tools/eval/score.py), plus the rows to review by hand.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/results_table.py [<markdown>]          # after `--stage 5` on every mock
    venv/Scripts/python.exe tools/results_table.py phase4 [--out <markdown>]
    venv/Scripts/python.exe tools/results_table.py phase5 [<report.json> ...] [--out <markdown>]
(phase4 / phase5 print to the console unless --out is given.)
"""

import collections
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


# ---------------------------------------------------------------- Phase 4: the runtime check

RUNTIME = OUTPUT / "runtime"
VERSIONS4 = ("v1.0", "v1.1-pre-4", "v1.1-change-4", "v1.1-final")
PHPBLOG_4F_PAGES = ("index.php", "blog.php", "gallery.php", "contact.php", "login.php", "error404.php",
                    "install__upgrade-v2.2.php")
# (title, the run folder of each rules version, the pages counted (None: all compared pages)). The folders are the
# runs reported in docs/phase4-results.md; each was classified against its own no-data build.
PHASE4 = (
    ("HMS", ("hms-v1.0", "hms-v1.1-final", "hms-v1.1-c4", "hms-final"), None),
    ("phpBlog, shipped seed", ("phpblog-v1.0", "phpblog", "phpblog-c4", "phpblog-final-seed"), None),
    ("phpBlog, seed + fixture, the 7 pages of Phase 4f",
     ("phpblog-v1.0-fx2", "phpblog-pre4-fx2", "phpblog-c4-fx2", "phpblog-final-fx"), PHPBLOG_4F_PAGES),
    ("phpBlog, seed + fixture, 13 pages",
     ("phpblog-v1.0-fx2", "phpblog-pre4-fx2", "phpblog-c4-fx2", "phpblog-final-fx"), None),
)


def phase4_column(folder: str, pages: tuple | None, runtime: Path = RUNTIME) -> dict:
    path = runtime / folder / "classified.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: capture, compare and classify that run first "
                                "(tools/runtime/README or the app's README)")
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = [p for p in report["pages"] if pages is None or p["page"] in pages]
    total = collections.Counter()
    classes = collections.Counter()
    for p in rows:
        for key in ("legacyElements", "matched", "listMatched", "listElements", "staticMatched", "staticElements",
                    "unexplained", "flagButWrong"):
            total[key] += p[key]
        classes.update(d["class"] for d in p["differences"])
    return {"pages": len(rows), "rules": report.get("rules"), "data": report.get("data", "seed"),
            "total": total, "classes": classes, "differences": sum(classes.values())}


def phase4_table(title: str, folders: tuple, pages: tuple | None, runtime: Path = RUNTIME) -> list[str]:
    columns = [phase4_column(f, pages, runtime) for f in folders]
    head = " | ".join(f"{v} (`{f}`)" for v, f in zip(VERSIONS4, folders))
    lines = [f"### {title}", "", f"| | {head} |", "|---|" + "---|" * len(columns)]

    def line(label, values):
        lines.append(f"| {label} | " + " | ".join(str(v) for v in values) + " |")

    line("Pages compared", [c["pages"] for c in columns])
    line("Legacy elements", [c["total"]["legacyElements"] for c in columns])
    line("Matched", [c["total"]["matched"] for c in columns])
    line("**Render match**", [f"{c['total']['matched'] / c['total']['legacyElements']:.3f}"
                              if c["total"]["legacyElements"] else "–" for c in columns])
    line("List elements matched", [f"{c['total']['listMatched']} / {c['total']['listElements']}" for c in columns])
    line("Static elements matched", [f"{c['total']['staticMatched']} / {c['total']['staticElements']}"
                                     for c in columns])
    line("Differences", [c["differences"] for c in columns])
    line("**UNEXPLAINED**", [c["total"]["unexplained"] for c in columns])
    line("**flag-but-wrong**", [c["total"]["flagButWrong"] for c in columns])
    for name in sorted(set().union(*(c["classes"] for c in columns))):
        line(f"`{name}`", [c["classes"].get(name, 0) for c in columns])
    return lines + [""]


def render_phase4(runtime: Path = RUNTIME, markdown: Path | None = None) -> str:
    lines = ["# Component 2 — Phase 4 runtime tables", "",
             f"Generated by `tools/results_table.py phase4` from `output/runtime/<run>/classified.json` "
             f"({source(markdown or DEFAULT_MARKDOWN)}). Unsound decisions are not in these files: they are the "
             "hand-reviewed findings of docs/phase4-results.md.", ""]
    for title, folders, pages in PHASE4:
        lines += phase4_table(title, folders, pages, runtime)
    return "\n".join(lines)


# ---------------------------------------------------------------- Phase 5: the evaluation metrics

PHASE5_ROWS = (("Scored gold rows", "scoredRows"), ("Boundary rows", "boundaryRows"), ("Read rows", "readRows"),
               ("Boundary precision", "boundaryPrecision"), ("Boundary recall", "boundaryRecall"),
               ("**Unsound decisions (strict)**", "unsoundStrict"),
               ("Unsound decisions (flag-adjusted)", "unsoundFlagAdjusted"), ("Abstain rate", "abstainRate"),
               ("Binding accuracy", "bindingAccuracy"), ("Wrong bindings", "wrongBindings"),
               ("Compile rate", "compileRate"), ("Flag rate per page", "flagRatePerPage"))
RESULTS = ("correct", "abstain", "wrong", "miss", "unmapped")


def fmt(value) -> str:
    return "–" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def phase5_table(report: dict) -> list[str]:
    name = report.get("key", report["app"])
    versions = report["versions"]
    lines = [f"### {name} (app `{report['app']}`)", "", "| Metric | v1.0 | v1.1-final |", "|---|---|---|"]
    for label, key in PHASE5_ROWS:
        lines.append(f"| {label} | " + " | ".join(fmt(versions[v]["metrics"][key]) for v in ("1.0", "1.1")) + " |")
    for result in RESULTS:
        lines.append(f"| rows `{result}` | " + " | ".join(
            str(versions[v]["metrics"]["results"].get(result, 0)) for v in ("1.0", "1.1")) + " |")
    for status in ("unsure", "not_in_mapping"):
        lines.append(f"| rows `{status}` (not scored) | " + " | ".join(
            str(sum(r["status"] == status for r in versions[v]["rows"])) for v in ("1.0", "1.1")) + " |")
    if report.get("agreement"):
        a = report["agreement"]
        lines += ["", f"Agreement on `if` decisions with the second key: {a['pairs']} pairs, "
                      f"{fmt(a['agreement'])} agreement, Cohen's κ {fmt(a['kappa'])}."]
    review = [(v, r) for v in ("1.0", "1.1") for r in versions[v]["rows"]
              if r["status"] == "not_in_mapping" or (r["status"] == "scored"
                                                     and r["score"]["result"] in ("wrong", "unmapped"))]
    lines += ["", "Rows to review by hand (wrong, unmapped, not_in_mapping):", "",
              "| Version | Gold row | Construct | Gold | Result | Tool |", "|---|---|---|---|---|---|"]
    for v, r in review:
        result = r["status"] if r["status"] != "scored" else r["score"]["result"] + (
            " †" if r["score"].get("specificReview") else "")
        tool = r["score"]["tool"] if r["status"] == "scored" else "–"
        lines.append(f"| v{v} | `{r['file']}:{r['line']}` | {r['construct']} | {r['decision'] or r['binding']} | "
                     f"{result} | {tool} |")
    if not review:
        lines.append("| – | none | | | | |")
    return lines + ["", "† a wrong decision whose node carries a specific review (flag-adjusted: not counted).", ""]


def render_phase5(reports: list[Path], markdown: Path | None = None) -> str:
    lines = ["# Component 2 — Phase 5 evaluation tables", "",
             f"Generated by `tools/results_table.py phase5` from {', '.join(f'`{p.as_posix()}`' for p in reports)} "
             f"({source(markdown or DEFAULT_MARKDOWN)}); scored by `tools/eval/score.py` (docs/eval-mapping.md "
             "v1.1). v1.0 = the frozen rules (`27c380e`), v1.1-final = changes 1–5 (`535e2e6`).", ""]
    for path in reports:
        lines += phase5_table(json.loads(path.read_text(encoding="utf-8")))
    return "\n".join(lines)


def write(text: str, markdown: Path | None) -> None:
    if markdown is None:
        sys.stdout.write(text + "\n")
        return
    markdown.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    print(f"wrote {markdown}")


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("phase4", "phase5"):
        out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
        named = [a for i, a in enumerate(argv[1:], start=1) if a != "--out" and argv[i - 1] != "--out"]
        if argv[0] == "phase4":
            write(render_phase4(RUNTIME, out), out)
        else:
            reports = [Path(a) for a in named] or sorted((OUTPUT / "eval").glob("*.json"))
            if not reports:
                print("no Phase 5 report: run tools/eval/score.py first", file=sys.stderr)
                return 2
            write(render_phase5(reports, out), out)
        return 0
    markdown = Path(argv[0]) if argv else DEFAULT_MARKDOWN
    text = render(OUTPUT, markdown)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
