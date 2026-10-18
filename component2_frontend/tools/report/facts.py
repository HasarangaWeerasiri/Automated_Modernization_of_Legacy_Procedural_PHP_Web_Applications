"""The facts of the report's evidence pack (docs/report/facts.md): every number the report may quote, each read
from the committed file that records it — no value is typed in here, only where to find it.

A fact has a primary source: a committed file and a rule that reads the value out of it (a regular expression, or a
Markdown table cell), so that `commit` (the commit that last changed the file) and the text that holds the number can
be shown next to it. Many facts also have a cross-check: the local result file the number was computed from
(output/runtime/<run>/classified.json, output/eval/practice-v12/<key>.json), re-derived the same way the tools do.
`tools/report/check_facts.py` re-derives every value and fails on any mismatch; a value no source records is written
"not recorded" (never estimated).
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]  # component2_frontend/
NOT_RECORDED = "not recorded"


# ---------------------------------------------------------------- reading sources


def text(path: str) -> str:
    return (ROOT / path).read_bytes().decode("utf-8")


def strip(cell: str) -> str:
    return cell.replace("**", "").strip()


def find(path: str, pattern: str) -> str:
    """Group 1 of the first match of `pattern` in a committed file."""
    found = re.search(pattern, text(path), re.M | re.S)
    if not found:
        raise LookupError(f"{path}: no match for {pattern!r}")
    return found.group(1)


def tables(section: str) -> list[list[list[str]]]:
    """The Markdown tables of a section: [table][row][cell], the header row first, the separator dropped."""
    out, current = [], None
    for line in section.split("\n"):
        if line.startswith("|"):
            cells = [strip(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]  # (an escaped \| stays)
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            if current is None:
                current = []
                out.append(current)
            current.append(cells)
        else:
            current = None
    return out


def cell(path: str, heading: str, row: str, column: int, table: int = 0) -> str:
    """The cell `column` (0 = the row label) of the row starting with `row` in the `table`-th table under the
    first heading containing `heading` (up to the next heading of the same or a higher level)."""
    doc = text(path)
    start = next((m for m in re.finditer(r"^(#+) (.*)$", doc, re.M) if heading in m.group(2)), None)
    if start is None:
        raise LookupError(f"{path}: no heading {heading!r}")
    level = len(start.group(1))
    end = next((m.start() for m in re.finditer(r"^(#+) ", doc[start.end():], re.M) if len(m.group(1)) <= level),
               len(doc) - start.end())
    rows = [r for r in tables(doc[start.end():start.end() + end])[table] if r[0].startswith(row)]
    if not rows:
        raise LookupError(f"{path}: no row {row!r} under {heading!r}")
    return rows[0][column]


def commit(path: str) -> str:
    """The commit that last changed the file (plus a note when it has uncommitted changes)."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    last = git("log", "-1", "--format=%h", "--", path)
    dirty = git("status", "--porcelain", "--", path)
    if not last:
        return "not committed"
    return f"`{last}`" + (" + uncommitted changes" if dirty else "")


# ---------------------------------------------------------------- local result files (cross-checks)


def runtime(folder: str, key: str, pages: tuple | None = None) -> str:
    """A Phase 4 run's total, as tools/results_table.py phase4 computes it."""
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import results_table
    column = results_table.phase4_column(folder, pages, ROOT / "output" / "runtime")
    total = column["total"]
    if key == "renderMatch":
        return f"{total['matched'] / total['legacyElements']:.3f}"
    if key == "differences":
        return str(column["differences"])
    return str(total[key])


def evaluation(report: str, version: str, key: str) -> str:
    """A scored rehearsal metric, formatted as docs/phase5r-rehearsal.md writes it."""
    doc = json.loads((ROOT / report).read_text(encoding="utf-8"))
    metrics = doc["versions"][version]["metrics"]
    if key in ("miss", "unmapped", "correct", "abstain", "wrong"):
        return str(metrics["results"].get(key, 0))
    value = metrics[key]
    if key == "flagRatePerPage":
        return f"{value:.2f}"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


# ---------------------------------------------------------------- the facts


@dataclass
class Fact:
    id: str
    label: str
    source: str  # the committed file
    read: Callable[[], str]  # reads the value from `source`
    checks: list = field(default_factory=list)  # [(description, callable)] local result files, same value expected

    def value(self) -> str:
        return self.read()


SECTIONS: list[tuple[str, str, list[Fact]]] = []


def section(title: str, note: str = ""):
    facts: list[Fact] = []
    SECTIONS.append((title, note, facts))
    return facts


CTX, PF, P4, P5R = "CONTEXT.md", "docs/post-freeze-log.md", "docs/phase4-results.md", "docs/phase5r-rehearsal.md"
APPS, MOCKS, RULES, HARNESS = ("docs/c2-apps-results.md", "docs/c2-results.md", "docs/php-analysis/boundary-rules.md",
                               "tests/test_harness.py")

# -- 1. tests and commits
s = section("Tests, commits and the freeze guard")
for phase, n in (("4f", "1098"), ("4g", "1161"), ("5R", "1179"), ("5R2", "1205")):
    s.append(Fact(f"tests-{phase}", f"Tests passing at the end of Phase {phase}", CTX,
                  lambda phase=phase: find(CTX, rf"^- Phase {phase} \(18 Oct\):.*?(\d{{4}}) tests")))
s.append(Fact("commit-freeze", "Freeze commit (boundary rules v1.0)", PF,
              lambda: find(PF, r"freeze commit `([0-9a-f]{7})`")))
s.append(Fact("commit-v11-final", "v1.1-final commit (post-freeze changes 1–5)", CTX,
              lambda: find(CTX, r"v1\.1-final = `([0-9a-f]{7})`")))
s.append(Fact("guard-apps-files", "Freeze guard: output files of the five apps under --rules v1.0", PF,
              lambda: find(PF, r"five discovery apps \(([\d,]+) output files")))
s.append(Fact("guard-apps-digest", "Freeze guard: digest of the five apps' outputs (v1.0)", PF,
              lambda: find(PF, r"five discovery apps \([\d,]+ output files, sha256 of the file-hash map `([^`]+)`")))
s.append(Fact("guard-mocks-digest", "Freeze guard: digest of the ten mocks' outputs (v1.0)", PF,
              lambda: find(PF, r"through all five stages \(sha256 `([^`]+)`")))

# -- 2. the ten mocks
s = section("The ten mocks (Stages 1–5)", "Hand-written mocks; the contract and endpoint map were written by the agent.")
for i, (fid, label) in enumerate((("todo", "S5 todo flags, all 10 mocks"), ("review-unlabelled", None),
                                  ("review", "S5 review flags (other than unlabelled), all 10 mocks"),
                                  ("info", "S5 info flags, all 10 mocks"), ("flag-rate", "S5 flag rate (todo + review), all 10 mocks"))):
    if label:
        s.append(Fact(f"mocks-{fid}", label, MOCKS,
                      lambda i=i: cell(MOCKS, "Table A", "All 10", 5).split(" / ")[i]))
s.append(Fact("mocks-without-todo", "Components without a C2Todo, all 10 mocks", MOCKS,
              lambda: cell(MOCKS, "Table A", "All 10", 6)))
s.append(Fact("mocks-tsc", "Strict tsc errors, all 10 mocks", MOCKS, lambda: cell(MOCKS, "Table A", "All 10", 7)))

# -- 3. the five discovery apps at the freeze
s = section("The five discovery apps at the freeze (rules v1.0, harness input)",
            "From docs/c2-apps-results.md, generated at the freeze; no labels, no contract (every node unlabelled).")
APP_NAMES = (("hms", "HMS"), ("wackopicko", "WackoPicko"), ("phpblog", "phpBlog"), ("attendance", "attendance"),
             ("warehouse", "warehouse"))
for key, name in APP_NAMES:
    head = rf"^## {key} \(commit `[0-9a-f]+`\)\n\n(\d+) PHP files \(libraries excluded\) → (\d+) units"
    s.append(Fact(f"apps-{key}-files", f"{name}: PHP files (libraries excluded)", APPS,
                  lambda head=head: find(APPS, head)))
    s.append(Fact(f"apps-{key}-units", f"{name}: units (pages, function components, shells)", APPS,
                  lambda key=key: find(APPS, rf"^## {key} \(commit `[0-9a-f]+`\)\n\n\d+ PHP files \(libraries excluded\) → (\d+) units")))
    s.append(Fact(f"apps-{key}-crashes", f"{name}: crashes", APPS,
                  lambda key=key: find(APPS, rf"^## {key} \(commit .*?(\d+) crashes")))
    s.append(Fact(f"apps-{key}-build", f"{name}: next build", APPS,
                  lambda key=key: find(APPS, rf"^## {key} \(commit .*?`next build`: \*\*(\w+)\*\*")))
    s.append(Fact(f"apps-{key}-abstain", f"{name}: Stage 2 abstains", APPS,
                  lambda key=key: find(APPS, rf"^\| \*\*{key} total\*\* \| \| \d+ \| \d+ \| (\d+)")))

# -- 4. the five apps under v1.1-final (test pins)
s = section("The five discovery apps under rules v1.1-final (pinned in tests)",
            "Totals pinned in tests/test_harness.py::test_whole_app_totals: nodes, S2 components, S2 abstain, S2 review, "
            "S5 todo, S5 review, S5 info.")
for key, name in APP_NAMES:
    for i, what in enumerate(("nodes", "S2 components", "S2 abstains", "S2 review", "S5 todo", "S5 review", "S5 info")):
        s.append(Fact(f"v11-{key}-{i}", f"{name}: {what}", HARNESS,
                      lambda key=key, i=i: find(HARNESS, rf'"{key}": \(([\d, ]+)\)').split(", ")[i]))

# -- 5. saturation
s = section("Rule saturation (boundary-rules §8)")
s.append(Fact("sat-phase1", "New patterns found by the Phase 1 whole-app run (HMS + WackoPicko)", "docs/c2-roadmap.md",
              lambda: find("docs/c2-roadmap.md", r"0 crashes, 0 tsc errors, (\d+) new patterns")))
s.append(Fact("sat-phpblog", "phpBlog (discovery app 3): new patterns", RULES,
              lambda: find(RULES, r"phpBlog \(app 3\) added (\d+) new patterns")))
s.append(Fact("sat-attendance", "attendance (app 4): new patterns", RULES,
              lambda: "0" if re.search(r"Attendance \(app 4\) added no new pattern", text(RULES)) else NOT_RECORDED))
s.append(Fact("sat-warehouse", "warehouse (app 5): new patterns", RULES,
              lambda: "0" if re.search(r"Warehouse \(app 5\) added no new pattern", text(RULES)) else NOT_RECORDED))
s.append(Fact("sat-counter", "Saturation counter at the freeze", RULES,
              lambda: find(RULES, r"\*\*Saturation counter: (\d / \d) — FROZEN\*\*")))
s.append(Fact("sat-date", "Freeze date", RULES, lambda: find(RULES, r"Saturation counter: 2 / 2 — FROZEN\*\* \(([^)]+)\)")))

# -- 6. post-freeze changes
s = section("Post-freeze changes 1–5 (docs/post-freeze-log.md)", "Each made before any evaluation run.")
for n in range(1, 6):
    s.append(Fact(f"change-{n}-date", f"Change {n}: date", PF,
                  lambda n=n: find(PF, rf"^\| (\d+ Oct 2026) \| \*\*Change {n}, ")))
    s.append(Fact(f"change-{n}-what", f"Change {n}: what", PF,
                  lambda n=n: find(PF, rf"^\| \d+ Oct 2026 \| \*\*Change {n}, ([^*]+?)\*\*")))
s.append(Fact("change-5-translate", "Change 5: phpBlog list guards with a row count that translate (replay map)", CTX,
              lambda: find(CTX, r"(\d+) phpBlog guards translate")))
s.append(Fact("change-5-not", "Change 5: phpBlog list guards with a row count that do not translate", CTX,
              lambda: find(CTX, r"phpBlog guards translate; (\d+) do not")))

# -- 7. runtime rendering check: HMS
s = section("Runtime rendering check: HMS (docs/phase4-results.md §3)",
            "Discovery app, run unmodified in Docker; GET only, no session.")
HMS_RUNS = (("v1.0", 1, "hms-v1.0"), ("v1.1 after changes 1–2", 2, "hms-v1.1"), ("v1.1-pre-4", 3, "hms-v1.1-final"),
            ("v1.1-change-4", 4, "hms-v1.1-c4"), ("v1.1-final", 5, "hms-final"))
for name, col, run in HMS_RUNS:
    for row, key, label in (("Render match", "renderMatch", "render match"), ("Differences", "differences", "differences"),
                            ("UNEXPLAINED", "unexplained", "UNEXPLAINED differences"),
                            ("flag-but-wrong", "flagButWrong", "flag-but-wrong differences")):
        s.append(Fact(f"hms-{key}-{col}", f"HMS {name}: {label}", P4,
                      lambda row=row, col=col: cell(P4, "3. HMS results", row, col),
                      [(f"output/runtime/{run}/classified.json", lambda run=run, key=key: runtime(run, key))]))
    s.append(Fact(f"hms-unsound-{col}", f"HMS {name}: unsound decisions (hand-reviewed)", P4,
                  lambda col=col: cell(P4, "3. HMS results", "Unsound decisions", col)))

# -- 8. runtime rendering check: phpBlog
PHPBLOG = (("seed", "5.1 Shipped seed", 0, ("phpblog-v1.0", "phpblog", "phpblog-c4", "phpblog-final-seed"), None),
           ("seed + fixture, 7 pages", "5.2 Seed + fixture", 0,
            ("phpblog-v1.0-fx2", "phpblog-pre4-fx2", "phpblog-c4-fx2", "phpblog-final-fx"),
            ("index.php", "blog.php", "gallery.php", "contact.php", "login.php", "error404.php",
             "install__upgrade-v2.2.php")),
           ("seed + fixture, 13 pages", "5.2 Seed + fixture", 1,
            ("phpblog-v1.0-fx2", "phpblog-pre4-fx2", "phpblog-c4-fx2", "phpblog-final-fx"), None))
for data, heading, table, runs, pages in PHPBLOG:
    s = section(f"Runtime rendering check: phpBlog, {data} (docs/phase4-results.md §{heading.split()[0]})",
                "Discovery app, run unmodified in Docker; GET only, no session.")
    tag = data.split(",")[0].replace(" + ", "-") + ("-13" if "13" in data else "-7" if "7" in data else "")
    for col, (name, run) in enumerate(zip(("v1.0", "v1.1-pre-4", "v1.1-change-4", "v1.1-final"), runs), start=1):
        for row, key, label in (("Render match", "renderMatch", "render match"), ("Differences", "differences", "differences"),
                                ("UNEXPLAINED", "unexplained", "UNEXPLAINED differences"),
                                ("flag-but-wrong", "flagButWrong", "flag-but-wrong differences")):
            s.append(Fact(f"phpblog-{tag}-{key}-{col}", f"phpBlog {data}, {name}: {label}", P4,
                          lambda heading=heading, table=table, row=row, col=col: cell(P4, heading, row, col, table),
                          [(f"output/runtime/{run}/classified.json",
                            lambda run=run, key=key, pages=pages: runtime(run, key, pages))]))
        s.append(Fact(f"phpblog-{tag}-unsound-{col}", f"phpBlog {data}, {name}: unsound decisions (hand-reviewed)", P4,
                      lambda heading=heading, table=table, col=col: cell(P4, heading, "Unsound decisions", col, table)))

# -- 9. the Phase 5 rehearsal
s = section("Phase 5 rehearsal on HMS — **rehearsal on a discovery app; not an evaluation result**",
            "HMS is a rule-discovery app and the practice keys were drafted with knowledge of the rules, so these numbers "
            "are biased upward. They test the evaluation procedure, never the tool's quality (CONTEXT §6, Phase 5R2).")
REHEARSAL = (("key 1", 3, "output/eval/practice-v12/hms-practice-gold.json", "1.0"),
             ("key 1", 4, "output/eval/practice-v12/hms-practice-gold.json", "1.1"),
             ("key 2", 5, "output/eval/practice-v12/hms-practice-gold-v2.json", "1.0"),
             ("key 2", 6, "output/eval/practice-v12/hms-practice-gold-v2.json", "1.1"))
for key, col, report, version in REHEARSAL:
    name = f"{key}, {'v1.0' if version == '1.0' else 'v1.1-final'} (mapping v1.2)"
    for row, metric in (("Boundary precision", "boundaryPrecision"), ("Boundary recall", "boundaryRecall"),
                        ("Unsound (strict)", "unsoundStrict"), ("Unsound (flag-adjusted)", "unsoundFlagAdjusted"),
                        ("Abstain rate", "abstainRate"), ("Binding accuracy", "bindingAccuracy"),
                        ("Compile rate", "compileRate"), ("Flag rate per page", "flagRatePerPage")):
        s.append(Fact(f"rehearsal-{key[-1]}-{version}-{metric}", f"Rehearsal {name}: {row.lower()}", P5R,
                      lambda row=row, col=col: cell(P5R, "6. Phase 5R2", row, col),
                      [(report, lambda report=report, version=version, metric=metric:
                        evaluation(report, version, metric))]))
    for i, result in enumerate(("correct", "abstain", "wrong", "miss", "unmapped")):
        s.append(Fact(f"rehearsal-{key[-1]}-{version}-{result}", f"Rehearsal {name}: rows {result}", P5R,
                      lambda col=col, i=i: cell(P5R, "6. Phase 5R2", "Correct / abstain", col).split(" / ")[i],
                      [(report, lambda report=report, version=version, result=result:
                        evaluation(report, version, result))]))
s.append(Fact("rehearsal-misses-guide", "Rehearsal: misses removed by guide v1.1 line conventions (of 28)", P5R,
              lambda: find(P5R, r"Guide v1\.1 line conventions \(key v2\) \| \*\*(\d+)\*\*")))

# -- 10. what is not recorded
s = section("Numbers the report may want that no committed file records")
s.append(Fact("eval-results", "Phase 5 evaluation results on the five locked apps", "docs/eval-results.md",
              lambda: NOT_RECORDED if not (ROOT / "docs/eval-results.md").exists() else "see docs/eval-results.md"))
s.append(Fact("agreement", "Annotator agreement (Cohen's κ) on the evaluation gold standard", "gold/",
              lambda: NOT_RECORDED))
s.append(Fact("render-match-eval", "Render match on the evaluation apps", "docs/phase4-results.md",
              lambda: NOT_RECORDED))


# ---------------------------------------------------------------- rendering


def all_facts() -> list[Fact]:
    return [f for _, _, facts in SECTIONS for f in facts]


def render() -> str:
    """The facts as Markdown tables (one per section): id, fact, value, source, commit."""
    lines = []
    for title, note, facts in SECTIONS:
        lines += [f"## {title}", ""]
        if note:
            lines += [note, ""]
        lines += ["| Id | Fact | Value | Source | Commit | Cross-checked against |", "|---|---|---|---|---|---|"]
        for f in facts:
            checks = ", ".join(f"`{path}`" for path, _ in f.checks) or "–"
            lines.append(f"| `{f.id}` | {f.label} | **{f.value()}** | `{f.source}` | "
                         f"{commit(f.source) if f.source != 'gold/' else '–'} | {checks} |")
        lines.append("")
    return "\n".join(lines)


def table_of_contents() -> list[str]:
    return [title for title, _, _ in SECTIONS]
