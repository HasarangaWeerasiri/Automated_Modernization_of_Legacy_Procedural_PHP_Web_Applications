"""Summarise pre-adaptation outputs, including runs that stopped early.

Reads only files the unchanged pipeline wrote; writes partial_summary.json.
Usage: python summarize_partial.py <evidence/preadaptation/app>
"""
import collections
import json
import sys
from pathlib import Path

app_dir = Path(sys.argv[1])
out = app_dir / "output"
log = (app_dir / "run_log.txt").read_text(encoding="utf-8", errors="replace")
summary = {"application": app_dir.name, "files_written": sorted(p.name for p in out.iterdir()) if out.exists() else []}
summary["exit_code"] = int(log.split("# exit code: ")[1].split()[0])
summary["last_stage_started"] = [l for l in log.splitlines() if l.startswith("[")][-1]
summary["error"] = next((l for l in log.splitlines() if l.split(":")[0].endswith("Error")), None)
q = json.loads((out / "queries.json").read_text(encoding="utf-8")) if (out / "queries.json").exists() else []
c = json.loads((out / "converted_queries.json").read_text(encoding="utf-8")) if (out / "converted_queries.json").exists() else []
summary["total_queries"] = len(q)
summary["recovery_status"] = dict(collections.Counter(x.get("status") for x in q))
summary["recovery_reason_codes"] = dict(collections.Counter(str(x.get("reason_code")) for x in q if x.get("status") != "RECOVERED"))
summary["executors"] = dict(collections.Counter(x.get("executor") for x in q))
summary["conversion_status"] = dict(collections.Counter(x.get("status") for x in c))
summary["conversion_unresolved_reasons"] = dict(collections.Counter((x.get("reason") or "")[:80] for x in c if x.get("status") != "CONVERTED"))
summary["files_with_sql_sites"] = len({x.get("source_file") for x in q})
(app_dir / "partial_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
