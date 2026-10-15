"""False-recovery audit support for S13.

For every query that was UNRESOLVED (BRANCHING) before S13 and is
RECOVERED after it, print the execution point, its enclosing conditional
blocks, the full assignment chain (line, blocks, verbatim PHP expression)
and the source lines, so each case can be reviewed by hand.

It also re-checks mechanically, from the raw AST, that every block around
a chain assignment also encloses the call.

Usage (repo root, PYTHONPATH=components/c3_data_layer/src):
    python evidence/soundness/S13_guarded_blocks/audit_newly_recovered.py <before_dir> <after_dir> <app> <php_dir>
"""

import json
import sys
from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import _find_relevant_assignments, _select_assignment_chain

before_dir, after_dir, app, php_dir = sys.argv[1:5]


def load(directory):
    return {
        (Path(q["source_file"]).name, q["source_line"]): q
        for q in json.loads((Path(directory) / app / "queries.json").read_text(encoding="utf-8"))
    }


before, after = load(before_dir), load(after_dir)
ast = analyze_php_ast(Path(php_dir))
calls = {(Path(c["source_file"]).name, c["source_line"]): c for c in ast["sql_calls"]}

print(f"## {app}\n")
count = 0

for key in sorted(after):
    old, new = before.get(key), after[key]

    if not (old and old.get("reason_code") == "BRANCHING" and new.get("status") == "RECOVERED"):
        continue

    count += 1
    call = calls[key]
    direct = not call["sql_argument"].startswith("$") or " " in call["sql_argument"].strip()
    chain = [] if direct else _select_assignment_chain(_find_relevant_assignments(call, ast["assignments"]))
    call_blocks = set(call.get("branch_context") or [])
    contained = all(set(a.get("branch_context") or []) <= call_blocks for a in chain)
    lines = Path(call["source_file"]).read_text(encoding="utf-8", errors="replace").splitlines()

    print(f"### {new['query_id']} `{key[0]}:{key[1]}`, mechanical containment check: {'OK' if contained else 'VIOLATED'}\n")
    print(f"- call blocks: `{sorted(call_blocks)}`")
    print(f"- recovered SQL: `{new['recovered_sql']}`")

    for a in chain:
        print(f"- chain L{a['source_line']} {a['assignment_type']} blocks `{a.get('branch_context')}`")

    shown = sorted({a["source_line"] for a in chain} | {key[1]})
    print("\n```php")
    for number in shown:
        print(f"{number:>4}: {lines[number - 1].strip()[:150]}")
    print("```\n")

print(f"Newly recovered in {app}: {count}\n")
