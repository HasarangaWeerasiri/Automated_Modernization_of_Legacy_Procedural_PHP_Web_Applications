"""Read-only holdout assessment using the current C3 Python package.
Run from components/c3_data_layer with project dependencies activated.
"""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from c3.mapping.sql_locator import analyze_php_ast
from c3.mapping.sql_recovery import recover_queries_from_ast
from c3.conversion.query_converter import convert_queries
from c3.generation.emitter import generate_data_access_source

HERE = Path(__file__).resolve().parent
LEGACY = HERE / "legacy"
OUT = HERE / "evaluation_output"
OUT.mkdir(exist_ok=True)
print("=" * 70)
print("C3 INDEPENDENT HOLDOUT — COMMUNITY SERVICE DESK")
print("=" * 70)
recovered = recover_queries_from_ast(analyze_php_ast(LEGACY))
converted = convert_queries(recovered)
recovery_counts = Counter(str(x.get("status")) for x in recovered)
conversion_counts = Counter(str(x.get("status")) for x in converted)
for i, query in enumerate(converted, 1):
    name = Path(str(query.get("source_file", "unknown")).replace(chr(92), "/")).name
    print(f"{i:02}. {name:17} {str(query.get('query_id')):8} {str(query.get('status')):12} {str(query.get('reason') or '')[:85]}")
print("Recovered:", len(recovered), "| Conversion states:", dict(conversion_counts))
rate = (100 * conversion_counts['CONVERTED'] / len(converted)) if converted else 0
print(f"Converted: {conversion_counts['CONVERTED']}/{len(converted)} ({rate:.1f}%)")
print("This is a new-application conversion result, NOT behavioral equivalence.")
report = {
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "application": "community_service_desk_holdout",
    "query_count": len(recovered),
    "recovery_states": dict(recovery_counts),
    "conversion_states": dict(conversion_counts),
    "converted": conversion_counts['CONVERTED'],
    "conversion_rate_percent": round(rate, 2),
    "queries": converted,
}
(OUT / 'conversion_report.json').write_text(json.dumps(report, indent=2, default=str),encoding='utf-8')
try:
    generated = generate_data_access_source(converted)
    compile(generated, str(OUT / 'generated_data_access.py'), 'exec')
except Exception as exc:
    print('Generated source FAILED:', type(exc).__name__, str(exc))
    report['generation_error'] = f'{type(exc).__name__}: {exc}'
    (OUT / 'conversion_report.json').write_text(json.dumps(report, indent=2, default=str),encoding='utf-8')
else:
    (OUT / 'generated_data_access.py').write_text(generated,encoding='utf-8')
    print('Generated Python syntax: PASS')
print('Evidence folder:', OUT)
