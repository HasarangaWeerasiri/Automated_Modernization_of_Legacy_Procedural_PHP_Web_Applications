"""Independent check: every line of app PHP that textually calls a DB executor
must appear as a detected site in the unchanged pipeline's queries.json.

The grep is independent of C3's PHP AST extractor. Commented-out calls are
expected to be undetected and are listed so a human can confirm them.
Usage (repo root): python evidence/preadaptation/detection_check.py
"""
import json
import re
from pathlib import Path

PATTERN = re.compile(r"mysqli_query\s*\(|mysql_query\s*\(|->query\s*\(|->prepare\s*\(|mysqli_prepare\s*\(")

for app in ["hms_kishan0725", "news_buzz", "fee_rainbow"]:
    root = Path("benchmarks/apps/_app_only") / app
    queries = json.loads(Path(f"evidence/preadaptation/{app}/output/queries.json").read_text(encoding="utf-8"))
    detected = {(Path(q["source_file"]).resolve(), q["source_line"]) for q in queries}
    missing, total = [], 0
    for php in sorted(root.rglob("*.php")):
        for number, line in enumerate(php.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PATTERN.search(line):
                total += 1
                if (php.resolve(), number) not in detected:
                    missing.append((php.relative_to(root).as_posix(), number, line.strip()[:110]))
    commented = sum(1 for _, _, text in missing if text.startswith(("//", "#", "*", "/*")))
    print(f"== {app}: executor lines={total}, detected sites={len(detected)}, "
          f"not detected={len(missing)} (commented out: {commented})")
    for item in missing:
        print("   ", item)
