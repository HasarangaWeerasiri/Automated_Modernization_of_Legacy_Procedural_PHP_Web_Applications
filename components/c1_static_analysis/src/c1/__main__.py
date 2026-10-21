"""Run the whole Component 1 pipeline.

    python -m c1 run <legacy-app> --out analysis-out [--exclude-dir TCPDF] [--title HMS] [--plain]
        extract (needs PHP) -> cfg -> includes -> dfg -> concerns -> auth -> endpoints -> contract -> timeline -> report.html
    python -m c1 analyse analysis-out --src <legacy-app> [--title HMS] [--plain]
        every stage after extract (no PHP needed: reads analysis-out/ast)

The console output is colourful in a terminal and plain in pipes/CI (or with --plain / NO_COLOR).
It never changes the files the pipeline writes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import hashlib

from . import architecture, auth, cfg, concerns, contract, dfg, endpoints, extract, includes, metrics, report, timeline
from . import console as ui

STEP_TITLES = {
    "extract": "Parse PHP into ASTs",
    "cfg": "Build control-flow graphs",
    "includes": "Resolve includes",
    "dfg": "Trace data flow",
    "concerns": "Label concerns",
    "auth": "Analyse login & guards",
    "endpoints": "Recover REST endpoints",
    "contract": "Generate contract + backend",
    "metrics": "Measure before / after",
    "architecture": "Suggest target architecture",
    "timeline": "Build output timelines (C2)",
    "report": "Write review report",
}


def _steps(out: Path, src: Path | None, title: str, schema: list[Path], arch: str | None = None):
    return [
        ("cfg", lambda: cfg.run(out), ui.describe_cfg),
        ("includes", lambda: includes.run(out), ui.describe_includes),
        ("dfg", lambda: dfg.run(out), ui.describe_dfg),
        ("concerns", lambda: concerns.run(out), ui.describe_concerns),
        ("auth", lambda: auth.run(out, src), ui.describe_auth),
        ("endpoints", lambda: endpoints.run(out, src, schema), ui.describe_endpoints),
        ("contract", lambda: contract.run(out, title), ui.describe_contract),
        ("metrics", lambda: metrics.run(out), ui.describe_metrics),
        ("architecture", lambda: architecture.run(out, arch), ui.describe_architecture),
        ("timeline", lambda: timeline.run(out, src), ui.describe_timeline),
        ("report", lambda: _report(out, title), ui.describe_report),
    ]


N_ANALYSE = 11  # stages after extract


def analyse(out: Path, src: Path | None, title: str, schema: list[Path],
            con: ui.Console | None = None, first: int = 1, total: int = N_ANALYSE, arch: str | None = None) -> dict:
    """Run every stage after extract. Returns each stage's result by name."""
    con = con or ui.Console(plain=True)
    results = {}
    for i, (name, fn, describe) in enumerate(_steps(out, src, title, schema, arch), start=first):
        results[name] = con.step(i, total, STEP_TITLES[name], fn, describe)
    return results


def _report(out: Path, title: str) -> dict:
    (out / "report.html").write_text(report.build(out, title), encoding="utf-8")
    return {"report": str(out / "report.html")}


def fingerprint(out: Path) -> str:
    """One short code for everything the run wrote: same input -> same code, on any machine or folder."""
    h = hashlib.sha256()
    for p in sorted(x for x in out.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(p.relative_to(out).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    d = h.hexdigest()[:16].upper()
    return " ".join(d[i:i + 4] for i in range(0, 16, 4))


def _summary(con: ui.Console, out: Path, results: dict) -> None:
    t = lambda name: ui._totals(results.get(name))  # noqa: E731
    e, k, ar = t("endpoints"), t("contract"), t("architecture")
    m = results.get("metrics") or {}
    sec, st = m.get("security", {}), m.get("structure", {})
    s1 = sec.get("S1_missingAuthorization", {})
    s3 = sec.get("S3_sqlBuiltFromInput", {}).get("before", {})
    t1 = st.get("T1_concernTangling", {}).get("before", {})
    flags = k.get("flags", 0)
    rec = architecture.TITLES.get(ar.get("recommended", ""), "-")
    con.summary([
        ("Pages using data with no login check",
         f"{s1.get('before', {}).get('pages', 0)} -> {s1.get('after', {}).get('tokenRequired', 0)} locked, "
         f"{s1.get('after', {}).get('awaitingDecision', 0)} flagged", "ok"),
        ("Redirects that do not stop (EAR)", f"{sec.get('S2_executionAfterRedirect', {}).get('before', 0)} -> 0", "ok"),
        ("SQL built from request input", f"{s3.get('queriesBuiltFromInput', 0)} -> 0", "ok"),
        ("Files mixing HTML, logic and SQL", f"{t1.get('percentMixed', 0)}% -> 0%", "ok"),
        ("REST endpoints recovered", f"{e.get('endpoints', 0)}", "ok"),
        ("Suggested architecture", rec if ar.get("score") else "not enough evidence", "ok"),
        ("Items a person must review", f"{flags}", "warn" if flags else "ok"),
    ])
    win = sys.platform.startswith("win")
    report_path = out / "report.html"
    backend = out / "backend"
    con.next_steps([
        ("Open the report", [("start " if win else "open ") + str(report_path)]),
        ("Start the new backend", [f"cd {backend}", "python -m uvicorn app.main:app --reload"]),
        ("Then open the API docs", ["http://localhost:8000/docs"]),
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1", description="Component 1: Backend & Auth migration of legacy PHP")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract + analyse a legacy app")
    r.add_argument("src", type=Path)
    r.add_argument("--out", type=Path, default=Path("analysis-out"))
    r.add_argument("--exclude-dir", action="append", default=[])
    r.add_argument("--schema", type=Path, action="append", default=[])
    r.add_argument("--title", default="Migrated")
    r.add_argument("--php")
    r.add_argument("--plain", action="store_true", help="no colours or banner")
    r.add_argument("--architecture", choices=architecture.CANDIDATES,
                   help="target architecture to select (only layered-monolith is generated today)")
    a = sub.add_parser("analyse", help="run every stage after extract")
    a.add_argument("out", type=Path)
    a.add_argument("--src", type=Path)
    a.add_argument("--schema", type=Path, action="append", default=[])
    a.add_argument("--title", default="Migrated")
    a.add_argument("--plain", action="store_true", help="no colours or banner")
    a.add_argument("--architecture", choices=architecture.CANDIDATES)
    args = ap.parse_args(argv)

    con = ui.Console(plain=args.plain)
    con.expected = (extract.ExtractError,)
    con.banner()
    src = args.src
    info = [("App", args.title)]
    if src:
        info.append(("Source", str(src)))
    info.append(("Output", str(args.out)))
    if getattr(args, "exclude_dir", None):
        info.append(("Skip", ", ".join(args.exclude_dir)))
    if args.schema:
        info.append(("Schema", ", ".join(str(s) for s in args.schema)))
    con.info(info)

    t0 = time.perf_counter()
    try:
        if args.cmd == "run":
            excl = extract.DEFAULT_EXCLUDE_DIRS + tuple(args.exclude_dir)
            con.step(1, N_ANALYSE + 1, STEP_TITLES["extract"],
                     lambda: extract.extract_tree(args.src, args.out, php=args.php, exclude_dirs=excl), ui.describe_extract)
            results = analyse(args.out, args.src, args.title, args.schema, con, first=2, total=N_ANALYSE + 1,
                              arch=args.architecture)
        else:
            results = analyse(args.out, args.src, args.title, args.schema, con, first=1, total=N_ANALYSE,
                              arch=args.architecture)
    except ui.StepFailed as e:
        con.failed(str(e))
        return 1
    _summary(con, args.out, results)
    if args.architecture and args.architecture != "layered-monolith":
        con.say("  " + con.c(f"Note: '{args.architecture}' is selected in the report, but only the layered monolith "
                             "is generated today. Its module plan is in architecture.json.", "93"))
        con.say()
    con.done(time.perf_counter() - t0, fingerprint(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
