"""Phase 5 scorer: the owner's gold standard against the tool's outputs, by docs/eval-mapping.md v1.0, exactly.

Reads `gold/<app>.csv` (docs/gold-standard-guide.md section 3) and the tool's outputs written by
tools/eval/run_eval.py (`output/eval/runs/<app>/v1.0/` and `v1.1/`, each the Stage 1-5 outputs of every unit of
the app, plus `run.json`). Writes `output/eval/<app>.json` (every gold row with its result, both versions) and
`docs/eval-results.md` (every scored app, per app and in total, v1.0 and v1.1 side by side).

Mapping section 4.1 is enforced first: the scorer refuses to run if the app's gold files (`gold/<app>.csv`, and
`gold/<app>.second.csv` / `gold/sample.txt` when present) have uncommitted changes, are not committed, or were last
committed after the first tool output (`firstRun` in run.json). `gold/corrections.md` is not checked: it is meant to
be written after the run (guide section 1.4).

Results per gold row: `correct`, `abstain` (a miss, not wrong), `wrong`, `miss` (no tool construct at that file and
line), `manual` (the mapping leaves it to the owner's judgement, e.g. an `inline` gold row that the tool made R-I5
without a "large" note; listed, not scored as correct or wrong), `unmapped` (a tool result the mapping table does not
name for that decision; listed, counted like a miss). Gold rows with `unsure` in their notes, and gold decisions the
mapping table does not name (e.g. loop `cannot_migrate`, unit `layout_part`), are not scored: they are listed.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/eval/score.py <app> [--gold-dir gold] [--runs output/eval/runs] [--out output/eval]
        [--doc docs/eval-results.md]
"""

import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # component2_frontend/
VERSIONS = ("1.0", "1.1")
BOUNDARY = ("loop", "if", "function", "include", "unit")
GOLD_COLUMNS = ("app", "file", "line", "construct", "decision", "container", "item_root", "binding", "notes")
SCORED = {  # docs/eval-mapping.md section 2: the decisions it maps
    "loop": {"list", "inline_map", "not_ui"},
    "if": {"page_guard", "list_guard", "attribute", "attribute_presence", "inline", "own_component",
           "redirect_guard", "auth_check", "request_handler", "not_ui"},
    "function": {"component", "shell"},
    "include": {"shell", "not_ui"},
    "unit": {"page", "not_ui"},
    "read": None,  # every binding
}
CONDITIONAL_RULES = ("R-I2", "R-I2b", "R-I4", "R-I5", "R-I6", "R-I8")
# Reasons that are on every node of an evaluation run, because its apps have no C1 labels and no contract: not a
# specific review (mapping section 3, flag-adjusted unsound decisions)
GENERIC_REASONS = {"unlabelled", "no_endpoint"}


class Refused(Exception):
    """Mapping section 4.1 is not met: no score is computed."""


# ---------------------------------------------------------------- section 4.1: gold before the tool


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def gold_files(gold_dir: Path, app: str) -> list[Path]:
    return [p for p in (gold_dir / f"{app}.csv", gold_dir / f"{app}.second.csv", gold_dir / "sample.txt")
            if p.exists() or p.name == f"{app}.csv"]


def gold_commit_time(gold_dir: Path, app: str) -> int:
    """The latest commit time (epoch) of the app's gold files. Refuses when one is missing, not committed, or
    changed since its commit."""
    files = gold_files(gold_dir, app)
    if not files[0].exists():
        raise Refused(f"{files[0]} does not exist")
    repo = Path(git(gold_dir, "rev-parse", "--show-toplevel").strip())
    names = [p.resolve().relative_to(repo.resolve()).as_posix() for p in files]
    dirty = git(repo, "status", "--porcelain", "--", *names).strip()
    if dirty:
        raise Refused(f"gold files have uncommitted changes: {dirty}")
    times = []
    for name in names:
        stamp = git(repo, "log", "-1", "--format=%ct", "--", name).strip()
        if not stamp:
            raise Refused(f"{name} is not committed")
        times.append(int(stamp))
    return max(times)


def check_order(gold_dir: Path, runs: Path, app: str) -> dict:
    run_file = runs / app / "run.json"
    if not run_file.exists():
        raise Refused(f"no tool output for {app} ({run_file})")
    run = json.loads(run_file.read_text(encoding="utf-8"))
    committed = gold_commit_time(gold_dir, app)
    if committed > run["firstRun"]:
        raise Refused(f"gold for {app} was committed at {committed}, after the first tool output ({run['firstRun']})")
    return {"goldCommitted": committed, "firstRun": run["firstRun"]}


# ---------------------------------------------------------------- the gold standard


def load_gold(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if rows and set(GOLD_COLUMNS) - set(rows[0]):
        raise ValueError(f"{path}: missing columns {sorted(set(GOLD_COLUMNS) - set(rows[0]))}")
    for index, row in enumerate(rows, start=2):  # (line 1 is the header)
        row["row"] = index
        row["line"] = int(row["line"]) if (row.get("line") or "").strip().isdigit() else None
        for key in GOLD_COLUMNS:
            row[key] = (row.get(key) or "").strip() if key != "line" else row["line"]
    return rows


def unsure(row: dict) -> bool:
    return bool(re.search(r"\bunsure\b", row["notes"], re.I))


# ---------------------------------------------------------------- the tool's outputs


def _walk(node, parents=()):
    yield node, parents
    for child in node["children"]:
        yield from _walk(child, parents + (node,))


class ToolRun:
    """One run (one rules version) of one app: its constructs indexed by file and start line."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        self.units = {e["key"]: e for e in self.index["pages"]}
        self.loops, self.ifs, self.calls, self.reads = {}, {}, {}, {}
        self.nodes = {}  # construct id -> [(unit key, node, under an abstain)]
        self.flags = {}  # node id -> [Stage 5 flag / C2Todo]
        self.redirects = {}  # if id -> [redirect]
        self.inside = {}  # loop / if id -> the output nodes (and parts) it encloses
        self.abstained = {}  # output node id -> the reason of the abstained region it is in
        for key, entry in self.units.items():
            unit = folder / key
            if not (unit / "timeline.json").exists():
                continue
            path = entry["path"]
            timeline = json.loads((unit / "timeline.json").read_text(encoding="utf-8"))
            for item in timeline["sequence"]:
                self._enclosures(path, item["enclosedBy"])
                for e in item["enclosedBy"]:
                    self.inside.setdefault(e["nodeId"], []).extend(
                        [item["id"]] + [p["nodeId"] for p in item.get("parts", [])])
                line = (item.get("loc") or {}).get("startLine")
                if item["kind"] in ("call", "include"):
                    self.calls.setdefault((path, line), []).append((key, item))
                    continue
                in_parts = set()
                for part in item.get("parts", []):
                    if part["kind"] != "literal":
                        in_parts.add(part["read"])
                        read = item["reads"][part["read"]]
                        self.reads.setdefault((path, part["loc"]["startLine"]), []).append((key, part["nodeId"], read))
                for index, read in enumerate(item.get("reads", [])):  # reads no part holds (e.g. a literal echo)
                    if index not in in_parts:
                        self.reads.setdefault((path, line), []).append((key, f"{item['id']}.{index}", read))
            for redirect in timeline.get("redirects", []):
                self._enclosures(path, redirect["enclosedBy"])
                for e in redirect["enclosedBy"]:
                    if e["role"] == "branch":
                        self.redirects.setdefault(e["nodeId"], []).append(redirect)
            for top in timeline.get("topLevelIfs", []):
                self.ifs.setdefault((path, top["loc"]["startLine"]), set()).add(top["nodeId"])
            if (unit / "stage2.json").exists():
                tree = json.loads((unit / "stage2.json").read_text(encoding="utf-8"))["tree"]
                for node, parents in _walk(tree):
                    under = any(p["status"] == "abstain" for p in parents)
                    region = next((p for p in (*parents, node) if p["status"] == "abstain"), None)
                    if region is not None:
                        for output in node["nodeIds"]:
                            self.abstained.setdefault(output, region["reason"])
                    ids = set(node["sourceIds"]) | {v for v in (node.get("shell") or {}).values() if v}
                    for source in ids:
                        self.nodes.setdefault(source, []).append((key, node, under))
            if (unit / "stage5.json").exists():
                stage5 = json.loads((unit / "stage5.json").read_text(encoding="utf-8"))
                for flag in stage5["flags"] + stage5["c2todos"]:
                    for node_id in flag["nodeId"].split(","):
                        self.flags.setdefault(node_id, []).append(flag)

    def _enclosures(self, path: str, enclosures: list) -> None:
        for e in enclosures:
            line = (e.get("loc") or {}).get("startLine")
            if e["role"] == "iteration":
                self.loops.setdefault((path, line), set()).add(e["nodeId"])
            elif e["role"] == "branch":
                self.ifs.setdefault((path, line), set()).add(e["nodeId"])

    def unit_text(self, key: str, node_id: str) -> list:
        """(text, first line) pieces of the output node `node_id` of unit `key` (inline HTML or literal parts)."""
        timeline = json.loads((self.folder / key / "timeline.json").read_text(encoding="utf-8"))
        for item in timeline["sequence"]:
            if item["id"] == node_id:
                if item.get("raw") is not None:
                    return [(item["raw"], item["loc"]["startLine"])]
                return [(p["value"], p["loc"]["startLine"]) for p in item.get("parts", []) if p["kind"] == "literal"]
        return []

    def specific_review(self, ids) -> bool:
        """A review or todo at these nodes with a reason that is not generic (mapping section 3)."""
        for node_id in ids:
            for flag in self.flags.get(node_id, []):
                reasons = set(flag["reason"].split(",")) - GENERIC_REASONS
                if flag["severity"] in ("review", "todo") and reasons:
                    return True
            for _, node, _ in self.nodes.get(node_id, []):
                if node["status"] == "review" and set(node["reason"].split(",")) - GENERIC_REASONS:
                    return True
        return False

    def region_abstain(self, construct: str) -> str | None:
        """The reason of the abstained region the construct's output stands in, when it has no node of its own."""
        return next((self.abstained[o] for o in self.inside.get(construct, []) if o in self.abstained), None)

    def unresolved(self, if_id: str) -> str | None:
        """The reason of an `unresolvedCondition` on this if (its content never renders), or None."""
        found = [f["reason"] for f in self.flags.get(if_id, []) if f["kind"] == "unresolvedCondition"]
        return found[0] if found else None


def container_line(run: ToolRun, key: str, container: dict) -> int | None:
    """The line of the List container's start tag: the last still-open `<tag` of the output node that opened it."""
    pieces = run.unit_text(key, container["openedIn"]) if container.get("openedIn") else []
    tag, open_at = container["tag"], []
    for text, line in pieces:
        for found in re.finditer(rf"<(/?){re.escape(tag)}(?=[\s/>])", text, re.I):
            at = line + text.count("\n", 0, found.start())
            if found.group(1):
                if open_at:
                    open_at.pop()
            else:
                open_at.append(at)
    return open_at[-1] if open_at else None


# ---------------------------------------------------------------- one gold row against one run


def loop_result(run: ToolRun, row: dict, loop: str) -> tuple[str, str]:
    found = run.nodes.get(loop, [])
    if not found and run.region_abstain(loop):
        return "abstain", f"abstain {run.region_abstain(loop)} (the region it stands in)"
    if any(under or node["status"] == "abstain" for _, node, under in found):
        node = next(n for _, n, u in found if u or n["status"] == "abstain")
        return "abstain", f"abstain {node['reason']}"
    lists = [(key, n) for key, n, _ in found if n["type"] == "List"]
    statics = [n for _, n, _ in found if n["type"] == "Static" and set(n["rules"]) & {"R-L5", "R-I7"}]
    decision = row["decision"]
    if lists:
        key, node = lists[0]
        inline = bool({"R-L3", "R-L3b"} & set(node["rules"]))
        if inline:
            return ("correct" if decision == "inline_map" else "wrong"), "inline map " + " ".join(node["rules"])
        container = node["container"] or {}
        tag = "fragment" if not container.get("hasWrapper") else \
            f"{container['tag']}@{container_line(run, key, container)}"
        roots = next((c["rootTags"] for c in node["children"] if c["type"] == "Item"), [])
        described = f"List container {tag}, item {'+'.join(roots) or '-'}"
        if decision == "list":
            same_root = row["item_root"] in ("+".join(roots), *(roots[:1] if len(set(roots)) == 1 else ()))
            return ("correct" if tag == row["container"] and same_root else "wrong"), described
        return "wrong", described  # inline_map: a List with another item shape; not_ui: it renders output
    if statics or not found:
        described = " ".join(statics[0]["rules"]) if statics else "no node"
        if decision == "not_ui":
            return "correct", described
        return ("wrong" if decision in ("list", "inline_map") else "unmapped"), described + " (not a boundary)"
    return "unmapped", ", ".join(f"{n['type']} {' '.join(n['rules'])}" for _, n, _ in found)


def if_kind(run: ToolRun, if_id: str) -> tuple[str, dict | None]:
    """What the tool made of this if: (kind, node). Kinds: abstain, R-I1, R-I2, R-I3, R-I3b, conditional rules,
    R-I7, R-I8-redirect, R-I8c, header_redirect, none."""
    found = run.nodes.get(if_id, [])
    if not found and run.region_abstain(if_id):
        return "abstain", {"reason": run.region_abstain(if_id) + " (the region it stands in)"}
    for _, node, under in found:
        if under or node["status"] == "abstain":
            return "abstain", node
    for _, node, _ in found:
        rules = set(node["rules"])
        if node["type"] == "Page" and "R-I1" in rules:
            return "R-I1", node
        if "R-I8" in rules and node["type"] == "Static":
            return "R-I8-redirect", node
        for rule in ("R-I2", "R-I2b", "R-I3", "R-I3b", "R-I5", "R-I4", "R-I6", "R-I8", "R-I7", "R-I8c"):
            if rule in rules:
                return rule, node
    if run.redirects.get(if_id):
        return "header_redirect", None
    return ("none", None) if not found else ("other", found[0][1])


def if_result(run: ToolRun, row: dict, if_id: str) -> tuple[str, str]:
    kind, node = if_kind(run, if_id)
    hidden = run.unresolved(if_id)
    omitted = any((f.get("flag") or "") == "attribute_omitted" for f in run.flags.get(if_id, []))
    described = kind + (f", unresolvedCondition({hidden})" if hidden else "")
    conditional = kind in CONDITIONAL_RULES
    decision = row["decision"]
    if kind == "abstain":
        if decision == "auth_check" and node["reason"] == "cuts_across_business_logic":
            return "correct", f"abstain {node['reason']}"
        return "abstain", f"abstain {node['reason']}"
    if decision == "page_guard":
        if kind == "R-I1":
            return "correct", described
        if conditional and hidden:
            return "abstain", described
        return ("wrong" if conditional else "unmapped"), described
    if decision == "list_guard":
        if kind in ("R-I2", "R-I2b"):
            return "correct", described
        if conditional and hidden:
            return "abstain", described
        return ("wrong" if kind in ("R-I4", "R-I5", "R-I1") else "unmapped"), described
    if decision in ("attribute", "attribute_presence"):
        if omitted:
            return "abstain", described + ", attribute omitted"
        if kind == ("R-I3" if decision == "attribute" else "R-I3b"):
            return "correct", described
        return ("wrong" if kind in ("R-I1", "none") else "unmapped"), described
    if decision == "inline":
        if kind in ("R-I4", "R-I6"):
            return "correct", described
        if kind == "R-I5":
            return ("correct" if re.search(r"\blarge\b", row["notes"], re.I) else "manual"), described
        return ("wrong" if kind == "R-I1" else "unmapped"), described
    if decision == "own_component":
        if kind in ("R-I5", "R-I4"):
            return "correct", described
        return ("wrong" if kind == "R-I1" else "unmapped"), described
    if decision == "redirect_guard":
        if kind in ("R-I8-redirect", "header_redirect"):
            return "correct", described
        return ("wrong" if kind in ("R-I1", "R-I8") or conditional else "unmapped"), described
    if decision == "auth_check":
        if hidden == "needs_auth_context":
            return "correct", described
        return ("wrong" if kind == "R-I1" else "unmapped"), described
    if decision == "request_handler":
        if hidden == "needs_request_context" or kind == "R-I7":
            return "correct", described
        return ("wrong" if kind == "R-I1" else "unmapped"), described
    if decision == "not_ui":
        if kind in ("R-I7", "none"):
            return "correct", described
        return ("wrong" if conditional or kind == "R-I1" else "unmapped"), described
    return "unmapped", described


def call_result(run: ToolRun, row: dict, key: str, call: dict) -> tuple[str, str]:
    found = run.nodes.get(call["id"], [])
    decision = row["decision"]
    shell = [n for _, n, _ in found if n["type"] == "Page" and "R-F2" in n["rules"]]
    component = [n for _, n, _ in found if n["type"] == "FunctionCall"]
    abstain = [n for _, n, u in found if n["status"] == "abstain" or u]
    silent = [n for _, n, _ in found if n["type"] == "Static" and "R-I8c" in n["rules"]]
    target = call.get("callee") or call.get("path")
    error_only = any(e["entrypoint"] == target and (e.get("notGenerated") or {}).get("reason") == "error_output_only"
                     for e in run.units.values())
    described = (f"{call['kind']} outputsInside={call['outputsInside']}"
                 + (", R-F2 shell" if shell else "") + (", R-F1" if component else "")
                 + (f", abstain {abstain[0]['reason']}" if abstain else ""))
    if decision == "component":
        if component:
            return "correct", described
        if abstain:
            return "abstain", described
        return ("wrong" if call["outputsInside"] is False and not run.specific_review([call["id"]]) else "unmapped"), \
            described
    if decision == "shell":
        if shell:
            return "correct", described
        if abstain:
            return "abstain", described
        return "unmapped", described
    if decision == "not_ui":
        if call["outputsInside"] is False or call.get("whitespace") or silent or error_only:
            return "correct", described
        if abstain:
            return "abstain", described
        return ("wrong" if shell or component else "unmapped"), described
    return "unmapped", described


def unit_result(run: ToolRun, row: dict) -> tuple[str, str]:
    entries = [e for e in run.units.values() if e["path"] == row["file"] and e.get("function") is None
               and e["role"] in ("page", "not_called")]
    generated = [e for e in entries if not e.get("notGenerated")]
    if generated:
        return ("correct" if row["decision"] == "page" else "wrong"), "generated page"
    described = f"not generated ({entries[0]['notGenerated'].get('reason')})" if entries else "no unit"
    return ("correct" if row["decision"] == "not_ui" else "wrong"), described


def binding(read: dict) -> str | None:
    """A tool read as a gold binding: `table.column`, session, request, literal; None when unresolved."""
    if read["sourceKind"] == "computed" and len(read.get("derivedFrom") or []) == 1:
        return binding(read["derivedFrom"][0])
    if read.get("confidence") != "resolved":
        return None
    kind = read["sourceKind"]
    if kind == "db_row_field":
        source = read.get("source") or {}
        return f"{source.get('table')}.{source.get('column')}" if source.get("table") else None
    return {"server": "request"}.get(kind, kind)


def read_result(row: dict, read: dict) -> tuple[str, str]:
    tool, gold = binding(read), row["binding"]
    described = f"{read['expr']} -> {tool or 'unresolved'}"
    if gold == "unknown":
        return ("correct" if tool is None else "wrong"), described
    if tool is None:
        return "abstain", described
    return ("correct" if tool == gold else "wrong"), described


def score_row(run: ToolRun, row: dict, used: set) -> dict:
    construct, where = row["construct"], (row["file"], row["line"])
    result, described, ids = "miss", "no tool construct at this file and line", []
    if construct == "loop":
        loops = sorted(run.loops.get(where, set()) - used)
        if loops:
            ids = [loops[0]]
            result, described = loop_result(run, row, loops[0])
        elif row["decision"] == "not_ui":  # a loop with no output has no place in the timeline: no node
            result, described = "correct", "no node (prints nothing)"
    elif construct == "if":
        ifs = sorted(run.ifs.get(where, set()) - used)
        if ifs:
            ids = [ifs[0]]
            result, described = if_result(run, row, ifs[0])
        elif row["decision"] == "not_ui":
            result, described = "correct", "no node (prints nothing)"
    elif construct in ("function", "include"):
        calls = [(k, c) for k, c in run.calls.get(where, []) if c["id"] not in used]
        if calls:
            ids = [calls[0][1]["id"]]
            result, described = call_result(run, row, *calls[0])
    elif construct == "unit":
        result, described = unit_result(run, row)
    elif construct == "read":
        candidates = [(k, n, r) for k, n, r in run.reads.get(where, []) if n not in used]
        scored = [(read_result(row, r), n) for _, n, r in candidates]
        for wanted in ("correct", "abstain", "wrong"):  # the best of the reads on that line
            match = next(((res, n) for res, n in scored if res[0] == wanted), None)
            if match:
                (result, described), ids = match[0], [match[1]]
                break
    used.update(ids)
    out = {"result": result, "tool": described, "nodeIds": ids}
    if result == "wrong":
        out["specificReview"] = run.specific_review(ids + [s for i in ids for _, n, _ in run.nodes.get(i, [])
                                                            for s in n["sourceIds"]])
    return out


def extra(run: ToolRun, rows: list, used: set) -> list:
    """Tool decisions on constructs in the gold's files that no gold row names (reported, not scored)."""
    files = {r["file"] for r in rows}
    out = []
    for kind, table in (("loop", run.loops), ("if", run.ifs)):
        for (path, line), ids in sorted(table.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
            for node_id in sorted(ids - used):
                if path in files and run.nodes.get(node_id):
                    node = run.nodes[node_id][0][1]
                    out.append({"file": path, "line": line, "construct": kind,
                                "tool": f"{node['type']} {' '.join(node['rules'])} {node['status']}".strip()})
    return out


# ---------------------------------------------------------------- metrics


def metrics(rows: list, run: ToolRun) -> dict:
    scored = [r for r in rows if r["status"] == "scored"]
    boundary = [r for r in scored if r["construct"] in BOUNDARY]
    reads = [r for r in scored if r["construct"] == "read"]
    count = lambda rs, result: sum(r["score"]["result"] == result for r in rs)  # noqa: E731
    correct, wrong = count(boundary, "correct"), count(boundary, "wrong")
    pages = [e for e in run.units.values() if e["role"] == "page" and not e.get("notGenerated") and not e["crash"]]
    rates = []
    for e in pages:
        stage5 = run.folder / e["key"] / "stage5.json"
        if stage5.exists():
            rates.append(json.loads(stage5.read_text(encoding="utf-8"))["counts"]["metrics"]["flagRate"])
    wrong_rows = [r for r in scored if r["score"]["result"] == "wrong"]
    return {
        "scoredRows": len(scored), "boundaryRows": len(boundary), "readRows": len(reads),
        "results": dict(sorted(Counter(r["score"]["result"] for r in scored).items())),
        "boundaryPrecision": ratio(correct, correct + wrong), "boundaryRecall": ratio(correct, len(boundary)),
        "unsoundStrict": len(wrong_rows),
        "unsoundFlagAdjusted": sum(not r["score"].get("specificReview") for r in wrong_rows),
        "abstainRate": ratio(count(scored, "abstain"), len(scored)),
        "bindingAccuracy": ratio(count(reads, "correct"), len(reads)),
        "wrongBindings": count(reads, "wrong"),
        "pages": len(pages),  # compile rate: None when tsc did not run
        "compileRate": None if any(e["tsc"] is None for e in pages) else ratio(sum(e["tsc"] == 0 for e in pages),
                                                                                len(pages)),
        "flagRatePerPage": round(sum(rates) / len(rates), 2) if rates else None, "flagSum": sum(rates),
        "counts": {"correct": correct, "wrong": wrong, "abstain": count(scored, "abstain"),
                   "readsCorrect": count(reads, "correct")},
    }


def ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def kappa(pairs: list) -> dict:
    """Percentage agreement and Cohen's kappa on paired labels."""
    if not pairs:
        return {"pairs": 0, "agreement": None, "kappa": None}
    agree = sum(a == b for a, b in pairs) / len(pairs)
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(first[k] * second[k] for k in first) / len(pairs) ** 2
    return {"pairs": len(pairs), "agreement": round(agree, 4),
            "kappa": round((agree - chance) / (1 - chance), 4) if chance < 1 else None}


def agreement(gold_dir: Path, app: str, rows: list) -> dict | None:
    second = gold_dir / f"{app}.second.csv"
    if not second.exists():
        return None
    theirs = {(r["file"], r["line"]): r["decision"] for r in load_gold(second) if r["construct"] == "if"}
    pairs = [(r["decision"], theirs[(r["file"], r["line"])]) for r in rows
             if r["construct"] == "if" and (r["file"], r["line"]) in theirs]
    return kappa(pairs)


# ---------------------------------------------------------------- one app, both versions


def score_app(app: str, gold_dir: Path, runs: Path) -> dict:
    order = check_order(gold_dir, runs, app)
    gold = load_gold(gold_dir / f"{app}.csv")
    report = {"app": app, "mapping": "docs/eval-mapping.md v1.0", "order": order, "versions": {},
              "agreement": agreement(gold_dir, app, gold)}
    for version in VERSIONS:
        run = ToolRun(runs / app / f"v{version}" / app)
        used, rows = set(), []
        for row in gold:
            entry = {k: row[k] for k in ("row", "file", "line", "construct", "decision", "container", "item_root",
                                         "binding", "notes")}
            allowed = SCORED.get(row["construct"], set())
            if unsure(row):
                entry["status"] = "unsure"
            elif row["construct"] not in SCORED or (allowed is not None and row["decision"] not in allowed):
                entry["status"] = "not_in_mapping"
            else:
                entry["status"] = "scored"
                entry["score"] = score_row(run, row, used)
            rows.append(entry)
        report["versions"][version] = {"metrics": metrics(rows, run), "rows": rows, "extra": extra(run, gold, used)}
    return report


# ---------------------------------------------------------------- docs/eval-results.md


def fmt(value) -> str:
    return "–" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def totals(reports: list, version: str) -> dict:
    keys = ("scoredRows", "boundaryRows", "readRows", "unsoundStrict", "unsoundFlagAdjusted", "wrongBindings",
            "pages")
    sums = {k: sum(r["versions"][version]["metrics"][k] for r in reports) for k in keys}
    counts = Counter()
    for r in reports:
        counts.update(r["versions"][version]["metrics"]["counts"])
    abstains = sum(r["versions"][version]["metrics"]["results"].get("abstain", 0) for r in reports)
    measured = all(r["versions"][version]["metrics"]["compileRate"] is not None for r in reports)
    compiled = sum(round((r["versions"][version]["metrics"]["compileRate"] or 0) *
                         r["versions"][version]["metrics"]["pages"]) for r in reports)
    flags = sum(r["versions"][version]["metrics"]["flagSum"] for r in reports)
    return sums | {
        "boundaryPrecision": ratio(counts["correct"], counts["correct"] + counts["wrong"]),
        "boundaryRecall": ratio(counts["correct"], sums["boundaryRows"]),
        "abstainRate": ratio(abstains, sums["scoredRows"]),
        "bindingAccuracy": ratio(counts["readsCorrect"], sums["readRows"]),
        "compileRate": ratio(compiled, sums["pages"]) if measured else None,
        "flagRatePerPage": round(flags / sums["pages"], 2) if sums["pages"] else None}


ROWS = (("Scored gold rows", "scoredRows"), ("Boundary precision", "boundaryPrecision"),
        ("Boundary recall", "boundaryRecall"), ("**Unsound decisions (strict)**", "unsoundStrict"),
        ("Unsound decisions (flag-adjusted)", "unsoundFlagAdjusted"), ("Abstain rate", "abstainRate"),
        ("Binding accuracy", "bindingAccuracy"), ("Wrong bindings", "wrongBindings"),
        ("Compile rate", "compileRate"), ("Flag rate per page", "flagRatePerPage"))


def write_doc(out: Path, doc: Path) -> None:
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json"))]
    lines = ["# Component 2 — Phase 5 Evaluation Results", "",
             "Generated by `tools/eval/score.py` from `output/eval/<app>.json` (mapping: `docs/eval-mapping.md` v1.0). "
             "Every metric for **v1.0** (frozen rules) and **v1.1** (post-freeze changes 1–3), side by side.", ""]
    columns = [(r["app"], r["versions"]) for r in reports]
    header = "| Metric | " + " | ".join(f"{app} v1.0 | {app} v1.1" for app, _ in columns) + " | total v1.0 | total v1.1 |"
    lines += [header, "|" + "---|" * (2 * len(columns) + 3)]
    both = {v: totals(reports, v) for v in VERSIONS}
    for label, key in ROWS:
        cells = [fmt(versions[v]["metrics"][key]) for _, versions in columns for v in VERSIONS]
        lines.append(f"| {label} | " + " | ".join(cells + [fmt(both[v][key]) for v in VERSIONS]) + " |")
    for report in reports:
        lines += ["", f"## {report['app']}", ""]
        if report.get("agreement"):
            a = report["agreement"]
            lines.append(f"Agreement on `if` decisions with the second annotator: {a['pairs']} pairs, "
                         f"{fmt(a['agreement'])} agreement, Cohen's κ {fmt(a['kappa'])}.")
            lines.append("")
        lines += ["| Gold row | Construct | Decision | v1.0 | v1.1 |", "|---|---|---|---|---|"]
        rows10, rows11 = (report["versions"][v]["rows"] for v in VERSIONS)
        for a, b in zip(rows10, rows11):
            cell = lambda r: (r["score"]["result"] + (" †" if r["score"].get("specificReview") else "")  # noqa: E731
                              + f" — {r['score']['tool']}") if r["status"] == "scored" else r["status"]
            lines.append(f"| `{a['file']}:{a['line']}` | {a['construct']} | {a['decision'] or a['binding']} | "
                         f"{cell(a)} | {cell(b)} |")
        lines += ["", "† a wrong decision whose node carries a specific review (not counted by the flag-adjusted "
                      "definition)."]
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("--"):
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    option = lambda name, default: Path(argv[argv.index(name) + 1]) if name in argv else default  # noqa: E731
    app = argv[0]
    gold_dir, runs = option("--gold-dir", ROOT / "gold"), option("--runs", ROOT / "output" / "eval" / "runs")
    out, doc = option("--out", ROOT / "output" / "eval"), option("--doc", ROOT / "docs" / "eval-results.md")
    try:
        report = score_app(app, gold_dir, runs)
    except Refused as refusal:
        print(f"refused (docs/eval-mapping.md section 4.1): {refusal}", file=sys.stderr)
        return 3
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{app}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                     newline="\n")
    write_doc(out, doc)
    for version in VERSIONS:
        m = report["versions"][version]["metrics"]
        print(f"{app} v{version}: precision {fmt(m['boundaryPrecision'])}, recall {fmt(m['boundaryRecall'])}, "
              f"unsound {m['unsoundStrict']} (flag-adjusted {m['unsoundFlagAdjusted']}), abstain "
              f"{fmt(m['abstainRate'])}, bindings {fmt(m['bindingAccuracy'])}, results {m['results']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
