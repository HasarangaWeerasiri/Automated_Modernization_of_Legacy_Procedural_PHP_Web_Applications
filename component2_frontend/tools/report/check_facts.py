"""Phase 6E: re-derive every number of docs/report/facts.md from its source and fail on any mismatch.

For each row of facts.md (an id, the fact, the value in bold, ...): the value is read again from its committed source
(tools/report/facts.py) and, where the row names one, from the local result file it is cross-checked against. Exit 1
when a value differs, a row has no rule, a rule has no row, or a source cannot be read; a result file that is not on
this machine is reported as skipped (it is git-ignored output). Values written "not recorded" are listed.

Usage, from component2_frontend/:
    venv/Scripts/python.exe tools/report/check_facts.py [docs/report/facts.md]
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import facts  # noqa: E402

ROW = re.compile(r"^\| `([^`]+)` \| .*? \| \*\*(.*?)\*\* \| ", re.M)


def check(path: Path) -> dict:
    written = dict(ROW.findall(path.read_bytes().decode("utf-8")))
    rules = {f.id: f for f in facts.all_facts()}
    problems, skipped, not_recorded, checked = [], [], [], 0
    for fact_id, value in written.items():
        fact = rules.get(fact_id)
        if fact is None:
            problems.append(f"{fact_id}: in facts.md but no rule derives it")
            continue
        try:
            derived = fact.value()
        except Exception as error:  # a source that cannot be read is a failure, not a skip
            problems.append(f"{fact_id}: {fact.source}: {error}")
            continue
        checked += 1
        if derived != value:
            problems.append(f"{fact_id}: facts.md says {value!r}, {fact.source} gives {derived!r}")
        if value == facts.NOT_RECORDED:
            not_recorded.append(f"{fact_id}: {fact.label}")
        for source, read in fact.checks:
            if not (facts.ROOT / source).exists():
                skipped.append(f"{fact_id}: {source} (not on this machine)")
                continue
            again = read()
            if again != value:
                problems.append(f"{fact_id}: facts.md says {value!r}, {source} gives {again!r}")
    for fact_id in sorted(set(rules) - set(written)):
        problems.append(f"{fact_id}: a rule with no row in facts.md")
    return {"checked": checked, "problems": problems, "skipped": skipped, "notRecorded": not_recorded}


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else facts.ROOT / "docs" / "report" / "facts.md"
    result = check(path)
    for line in result["problems"]:
        print("MISMATCH", line)
    for line in result["skipped"]:
        print("skipped ", line)
    for line in result["notRecorded"]:
        print("not recorded", line)
    print(f"{result['checked']} facts re-derived, {len(result['problems'])} problems, "
          f"{len(result['skipped'])} cross-checks skipped, {len(result['notRecorded'])} not recorded")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
