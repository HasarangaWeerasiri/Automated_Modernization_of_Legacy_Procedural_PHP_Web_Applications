"""Proof for roadmap Phase 1b: the mocks changed only in node ids, loc and `basis`.

The mocks moved from token-index ids and a 1-based, inclusive loc to Member 01's conventions
(pre-order AST node ids; loc with 0-based columns, exclusive ends and bytes). This compares each
mock as committed at a git revision (the old convention) with the mock on disk, and checks that:

  * old id -> new id is a function and one-to-one, found by walking both files side by side;
  * every new id is a node of the source's AST, of the entry's kind, and its loc is that node's loc;
  * with ids mapped, every entry is equal: kind, raw, reads, enclosures (role, kind, branch,
    iterExpr, iterSourceKind, valueVar, keyVar, condExpr), queries and labels;
  * every old loc, converted to the new convention from the source bytes, equals the new loc -
    except an echo closed by "?>", whose AST node (and so its loc) ends after that tag;
  * the only new read field is `basis`.

Usage, from component2_frontend/ (the revision must hold the old-convention mocks):
    venv/Scripts/python.exe tools/id_mapping_proof.py 33a3cfc [mapping.json]
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_timelines  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "mocks"
ENCLOSURE_FIELDS = ("kind", "role", "branch", "iterExpr", "iterSourceKind", "valueVar", "keyVar", "condExpr")
CLOSING_TAG = re.compile(rb"[ \t\r\n]*\?>\n?")


def at_revision(revision: str, name: str) -> dict:
    blob = subprocess.run(["git", "show", f"{revision}:./mocks/{name}"], cwd=ROOT, capture_output=True, check=True)
    return json.loads(blob.stdout.decode("utf-8"))


def on_disk(name: str) -> dict:
    with (MOCKS / name).open("r", encoding="utf-8", newline="") as f:
        return json.load(f)


def converted(loc: dict, source: bytes) -> dict:
    """An old loc (1-based columns, inclusive end) in the new convention, from the source bytes."""
    starts = [0] + [m.end() for m in re.finditer(b"\n", source)]
    start = starts[loc["startLine"] - 1] + loc["startCol"] - 1
    end = starts[loc["endLine"] - 1] + loc["endCol"]  # one past the old last byte
    line = max(i for i, s in enumerate(starts) if s <= end)
    return {"startLine": loc["startLine"], "endLine": line + 1, "startCol": loc["startCol"] - 1,
            "endCol": end - starts[line], "startByte": start, "endByte": end}


class Proof:
    def __init__(self, name: str, old: dict, new: dict, old_labels: dict, new_labels: dict, source: bytes, ast):
        self.name, self.source, self.ast = name, source, ast
        self.map: dict[str, str] = {}
        self.problems: list[str] = []
        self.loc_same = self.loc_closing_tag = self.basis = 0
        self.timelines(old, new)
        self.labels(old_labels, new_labels)
        if len(set(self.map.values())) != len(self.map):
            self.problems.append("two old ids map to one new id")

    def pair(self, old: str, new: str, where: str) -> None:
        if self.map.setdefault(old, new) != new:
            self.problems.append(f"{where}: old id {old} maps to both {self.map[old]} and {new}")
        if new not in self.ast.nodes:
            self.problems.append(f"{where}: new id {new} is not a node of the AST")

    def same(self, old, new, where: str) -> None:
        if old != new:
            self.problems.append(f"{where}: {json.dumps(old)[:120]} != {json.dumps(new)[:120]}")

    def read(self, old: dict, new: dict, where: str) -> None:
        extra = set(new) - set(old)
        self.basis += "basis" in extra
        self.same(sorted(extra - {"basis"}), [], f"{where} new read keys")
        for key in ("expr", "var", "path", "sourceKind", "confidence"):
            self.same(old[key], new[key], f"{where} read {key}")
        if (old["source"] is None) != (new["source"] is None):
            self.problems.append(f"{where}: read source appeared or vanished")
        elif old["source"] is not None:
            for key in ("fetchNodeId", "queryNodeId"):
                self.pair(old["source"][key], new["source"][key], f"{where} {key}")
            self.same((old["source"]["table"], old["source"]["column"]),
                      (new["source"]["table"], new["source"]["column"]), f"{where} read table/column")
        self.same(len(old.get("derivedFrom", [])), len(new.get("derivedFrom", [])), f"{where} derivedFrom")
        for inner_old, inner_new in zip(old.get("derivedFrom", []), new.get("derivedFrom", [])):
            self.read(inner_old, inner_new, where)

    def loc(self, old: dict, new: dict, kind: str, where: str) -> None:
        expected = converted(old, self.source)
        if expected == new:
            self.loc_same += 1
        elif (kind == "Stmt_Echo" and expected["startByte"] == new["startByte"]
              and CLOSING_TAG.fullmatch(self.source[expected["endByte"]:new["endByte"]])):
            self.loc_closing_tag += 1
        else:
            self.problems.append(f"{where}: loc {new} is not the old loc converted ({expected})")

    def timelines(self, old: dict, new: dict) -> None:
        self.same(old["entrypoint"], new["entrypoint"], "entrypoint")
        self.same(len(old["sequence"]), len(new["sequence"]), "sequence length")
        for a, b in zip(old["sequence"], new["sequence"]):
            where = f"{a['id']} -> {b['id']}"
            self.pair(a["id"], b["id"], where)
            for key in ("kind", "raw"):
                self.same(a.get(key), b.get(key), f"{where} {key}")
            node = self.ast.nodes.get(b["id"])
            if node is not None:
                self.same(node.kind, b["kind"], f"{where} AST node kind")
                self.same(gen_timelines.loc_dict(node.loc), b["loc"], f"{where} loc vs AST node loc")
            self.loc(a["loc"], b["loc"], a["kind"], where)
            self.same(len(a.get("reads", [])), len(b.get("reads", [])), f"{where} reads")
            for old_read, new_read in zip(a.get("reads", []), b.get("reads", [])):
                self.read(old_read, new_read, where)
            self.same(len(a["enclosedBy"]), len(b["enclosedBy"]), f"{where} enclosedBy")
            for x, y in zip(a["enclosedBy"], b["enclosedBy"]):
                self.pair(x["nodeId"], y["nodeId"], f"{where} enclosure")
                if "condNodeId" in x:
                    self.pair(x["condNodeId"], y["condNodeId"], f"{where} condNodeId")
                    cond = self.ast.nodes.get(y["condNodeId"])
                    if cond is not None and "condExpr" in y:  # Member 01 Q2: condExpr is the cond node's source
                        text = self.source[cond.loc.start_byte:cond.loc.end_byte].decode("utf-8")
                        self.same(text, y["condExpr"], f"{where} condExpr vs AST cond node source")
                for key in ENCLOSURE_FIELDS:
                    self.same(x.get(key), y.get(key), f"{where} enclosure {key}")
                self.same(sorted(set(y) - set(x)), ["loc"], f"{where} new enclosure keys")
                owner = self.ast.nodes.get(y["nodeId"])
                if owner is not None:
                    self.same(gen_timelines.loc_dict(owner.loc), y["loc"], f"{where} enclosure loc vs AST node loc")
        self.same({self.map.get(q, q): v for q, v in old["queries"].items()}, new["queries"], "queries")

    def labels(self, old: dict, new: dict) -> None:
        missing = [key for key in old["labels"] if key not in self.map]
        if missing:
            self.problems.append(f"label keys with no mapping: {missing}")
        self.same({self.map.get(k, k): v for k, v in old["labels"].items()}, new["labels"], "labels")


def run(revision: str) -> dict[str, Proof]:
    specs = gen_timelines.specs() | gen_timelines.wackopicko_specs()
    asts, _ = gen_timelines.spec_asts(specs)
    return {name: Proof(name, at_revision(revision, f"timeline_{name}.json"), on_disk(f"timeline_{name}.json"),
                        at_revision(revision, f"labels_{name}.json"), on_disk(f"labels_{name}.json"),
                        spec["src"], asts[spec["file_id"]])
            for name, spec in specs.items()}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    proofs = run(argv[0])
    combined: dict[str, str] = {}
    failed = False
    print("| Mock | Ids mapped | One-to-one | Entries, labels equal | loc = old converted | loc: echo closed by ?> | "
          "Reads with basis |")
    print("|---|---|---|---|---|---|---|")
    for name, proof in proofs.items():
        for old, new in proof.map.items():
            if combined.setdefault(old, new) != new:
                proof.problems.append(f"{old} maps differently in another mock")
        ok = not proof.problems
        failed |= not ok
        print(f"| {name} | {len(proof.map)} | {'yes' if ok else 'NO'} | {'yes' if ok else 'NO'} | {proof.loc_same} | "
              f"{proof.loc_closing_tag} | {proof.basis} |")
        for problem in proof.problems:
            print(f"    PROBLEM {problem}")
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8", newline="\n") as f:
            json.dump(dict(sorted(combined.items())), f, indent=1)
            f.write("\n")
        print(f"wrote {argv[1]} ({len(combined)} ids)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
