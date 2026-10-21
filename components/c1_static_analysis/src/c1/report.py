"""Human-readable review report (master reference step 11: "Flag file -> human developer").

    python -m c1.report analysis-out --title HMS     # -> analysis-out/report.html

One self-contained HTML page (no scripts, no external files), built only from the JSON
outputs: page protection, recovered endpoints, and every flagged item to review.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

CSS = """
:root{--bg:#fafaf8;--fg:#1d1d1b;--muted:#6b6b66;--line:#e3e2dc;--card:#fff;--bad:#b42318;--warn:#b54708;--ok:#067647;--chip:#f2f1ec}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#2e2d2a;--card:#1e1e1c;--bad:#f97066;--warn:#fdb022;--ok:#47cd89;--chip:#2a2926}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.stat b{display:block;font-size:24px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:block;overflow-x:auto}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}th{font-weight:600;background:var(--chip)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}.chip{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--chip);font-size:12px;margin:1px 2px 1px 0}
.arrow{color:var(--muted);padding:0 6px}
.arch{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:12px 0}
.arch input{position:absolute;opacity:0;pointer-events:none}
.arch label{display:block;height:100%;background:var(--card);border:2px solid var(--line);border-radius:12px;padding:14px;cursor:pointer}
.arch input:checked+label{border-color:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 20%,transparent)}
.arch input:focus-visible+label{outline:2px solid var(--fg);outline-offset:2px}
.arch h3{margin:0 0 4px;font-size:16px}.bar{height:8px;background:var(--chip);border-radius:99px;overflow:hidden;margin:8px 0}
.bar i{display:block;height:100%;background:var(--ok)}.reasons{margin:8px 0 0;padding-left:0;list-style:none;font-size:13px}
.reasons li{margin:3px 0}.plus{color:var(--ok);font-weight:600}.minus{color:var(--bad);font-weight:600}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;padding:2px 8px;border-radius:99px;margin-right:4px}
.b-rec{background:var(--ok);color:var(--bg)}.b-now{background:var(--chip)}.b-plan{background:var(--chip);color:var(--warn)}.note{color:var(--muted);font-size:13px}
.bad{color:var(--bad);font-weight:600}.warn{color:var(--warn);font-weight:600}.ok{color:var(--ok);font-weight:600}
"""

STATUS_CLASS = {"guarded": "ok", "unguarded": "bad", "partially-guarded": "warn", "no-data-access": "muted"}


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def build(out_dir: Path, title: str) -> str:
    j = lambda name: json.loads((out_dir / name).read_text(encoding="utf-8"))  # noqa: E731
    manifest, auth, eps, flags, labels = j("manifest.json"), j("auth.json"), j("endpoints.json"), j("flags.json"), j("labels.json")
    pages = auth["pages"]
    unguarded = [p for p in pages if p["status"] in ("unguarded", "partially-guarded")]
    lt = labels["totals"]
    decided = sum(v for k, v in lt.items() if k != "undecided")
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>{esc(title)} analysis</title><style>{CSS}</style></head><body><main>",
             f"<h1>{esc(title)}: analysis report</h1>",
             f"<p class=muted>Component 1 (static analysis, sound-or-abstain). Input hash <code>{esc(manifest['contentHash'][:12])}</code>. "
             f"Everything below comes from the JSON outputs next to this file.</p>",
             "<div class=grid>"]
    for label, val in (("PHP files", manifest["totals"]["files"]), ("Pages", len(pages)),
                       ("Unguarded pages with data", len([p for p in unguarded if p["status"] == "unguarded"])),
                       ("Endpoints", len(eps["endpoints"])), ("Items to review", len(flags["items"])),
                       ("Statements decided", f"{100 * decided / max(1, sum(lt.values())):.1f}%")):
        parts.append(f"<div class=stat><span class=muted>{esc(label)}</span><b>{esc(val)}</b></div>")
    parts.append("</div>")
    if (out_dir / "metrics.json").exists():
        parts.append(metrics_section(j("metrics.json")))
    if (out_dir / "architecture.json").exists():
        parts.append(architecture_section(j("architecture.json")))

    parts.append("<h2>Pages and their guards</h2><table><tr><th>Page</th><th>Status</th><th>Data access</th><th>Guards</th><th>Notes</th></tr>")
    for p in sorted(pages, key=lambda p: (p["status"] != "unguarded", p["status"], p["page"])):
        guards = ", ".join(g.get("helper") or g.get("file") or g["via"] for g in p["guards"]) or "—"
        notes = []
        if p.get("unguardedWrites"):
            notes.append(f"<span class=bad>{p['unguardedWrites']} unguarded write(s)</span>")
        if p.get("writesSession"):
            notes.append("login-like page")
        if p.get("needsDecision"):
            notes.append("<span class=warn>needs a decision: public, or missing guard?</span>")
        parts.append(f"<tr><td><code>{esc(p['page'])}</code></td><td class={STATUS_CLASS.get(p['status'], '')}>{esc(p['status'])}</td>"
                     f"<td>{p['dataAccess']} ({p['protected']} protected)</td><td>{esc(guards)}</td><td>{'; '.join(notes)}</td></tr>")
    parts.append("</table>")

    checks = auth["checks"]
    if checks:
        parts.append("<h2>Session checks</h2><table><tr><th>Where</th><th>Condition</th><th>Kind</th><th>Effect</th><th>Stays on server</th></tr>")
        for c in checks:
            eff_cls = "bad" if c["effect"] == "broken-guard" else ("warn" if c["effect"] == "ambiguous" else "")
            parts.append(f"<tr><td><code>{esc(c['path'])}:{c['line']}</code></td><td><code>{esc(c.get('condExpr'))}</code></td>"
                         f"<td>{esc(c['kind'])}</td><td class={eff_cls}>{esc(c['effect'])}</td><td>{esc(c['serverSide'])}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>Recovered endpoints</h2><table><tr><th>Endpoint</th><th>From legacy</th><th>Parameters</th><th>Response</th><th>Auth</th><th>Flags</th></tr>")
    for e in eps["endpoints"]:
        src = "<br>".join(f"<code>{esc(s['legacy']['method'])} /{esc(s['page'])}</code>" for s in e["sources"][:4])
        params = " ".join(f"<span class=chip>{esc(p['name'])} ← {esc(p['legacy_name'])}</span>" for p in e["params"][:8])
        fields = list(e["response"].get("fields", {}))
        resp = esc(e["response"]["kind"]) + (f": {esc(', '.join(fields[:8]))}{' …' if len(fields) > 8 else ''}" if fields else "")
        auth_txt = "token" if e["auth"].get("required") else "<span class=warn>none in legacy</span>"
        fl = " ".join(f"<span class=chip>{esc(f['flag'])}</span>" for f in e["flags"])
        parts.append(f"<tr><td><code>{esc(e['id'])}</code></td><td>{src}</td><td>{params}</td><td>{resp}</td><td>{auth_txt}</td><td>{fl}</td></tr>")
    parts.append("</table>")

    parts.append("<h2>Items to review</h2><p class=muted>What the tool did not decide. Each one needs a person.</p>"
                 "<table><tr><th>Kind</th><th>Where</th><th>Detail</th></tr>")
    for i in flags["items"]:
        where = i.get("page") or i.get("endpoint") or ""
        if i.get("line"):
            where = f"{where}:{i['line']}"
        detail = i.get("reason") or i.get("decision") or i.get("column") or i.get("condExpr") or ""
        parts.append(f"<tr><td><span class=chip>{esc(i['kind'])}</span></td><td><code>{esc(where)}</code></td><td>{esc(detail)}</td></tr>")
    parts.append("</table></main></body></html>")
    return "".join(parts)


def architecture_section(a: dict) -> str:
    """Three candidate architectures as selectable cards (pure HTML/CSS, no script)."""
    f = a["features"]
    out = ["<h2>Target architecture</h2>",
           f"<p class=muted>Not every legacy app should become the same thing. From {f['endpoints']} endpoints, "
           f"{f['domainTables']} tables and how pages use them, the tool found <b>{f['areas']}</b> business area(s); "
           f"{f['crossAreaShare']:.0%} of the pages that use data combine several areas. Click an option to select it.</p>",
           "<div class=arch role=radiogroup aria-label='Target architecture'>"]
    for o in a["options"]:
        oid = esc(o["id"])
        checked = " checked" if o["id"] == a["selected"] else ""
        badges = ""
        if o["id"] == a["recommended"] and o["score"] > 0:
            badges += "<span class='badge b-rec'>Recommended</span>"
        badges += ("<span class='badge b-now'>Generated now</span>" if o["generation"] == "available"
                   else "<span class='badge b-plan'>Planned</span>")
        reasons = "".join(f"<li><span class={'plus' if r['points'] > 0 else 'minus' if r['points'] < 0 else 'muted'}>"
                          f"{'+' if r['points'] > 0 else ''}{r['points']}</span> {esc(r['why'])}</li>"
                          for r in o["reasons"])
        mods = ""
        if o["modules"]:
            word = "services" if o["id"] == "microservices" else "modules"
            mods = (f"<p class=note style='margin:10px 0 4px'>Proposed {word}:</p>" + " ".join(
                f"<span class=chip title='{esc(', '.join(m['endpoints']))}'><b>{esc(m['name'])}</b> "
                f"{esc(', '.join(m['tables']) or ('login, reads ' + ', '.join(m['reads']) if m.get('reads') else 'no own tables'))}"
                f" · {len(m['endpoints'])} endpoint(s)</span>"
                for m in o["modules"]))
        out.append(f"<div><input type=radio name=arch id=arch-{oid} value={oid}{checked}>"
                   f"<label for=arch-{oid}>{badges}<h3>{esc(o['title'])}</h3><span class=muted>{esc(o['summary'])}</span>"
                   f"<div class=bar aria-hidden=true><i style='width:{o['score']}%'></i></div>"
                   f"<b>{o['score']}</b><span class=muted> / 100 fit</span><ul class=reasons>{reasons}</ul>{mods}</label></div>")
    out.append("</div><p class=note>Scores are transparent rules over the analysis (listed on each card). "
               "Only the layered monolith is generated today; the other options show the module plan the "
               "generator will follow. Method after Service Cutter (Gysel et al. 2016) and Mono2Micro "
               "(Kalia et al. 2021); prefers the simpler option on a tie (Fowler, MonolithFirst).</p>")
    return "".join(out)


def metrics_section(m: dict) -> str:
    """Before/after table: what the migration changed (metrics.json)."""
    sec, st = m["security"], m["structure"]
    s1, s3, s4 = sec["S1_missingAuthorization"], sec["S3_sqlBuiltFromInput"], sec["S4_untypedInputs"]
    t1, t2, c1 = st["T1_concernTangling"], st["T2_htmlAndSqlInOneFile"], st["C1_cyclomaticComplexity"]
    a1 = s1["after"]
    rows = [
        ("Security", "Pages that use data with no working login check (CWE-862)",
         f"{s1['before']['pages']} pages, {s1['before']['dataOperations']} data operations",
         f"{a1['tokenRequired']} token-protected, {a1['awaitingDecision']} awaiting a decision, {a1['publicLogin']} login",
         "ok" if a1["awaitingDecision"] == 0 else "warn", s1["note"]),
        ("Security", "Redirects that do not stop the page (EAR, CWE-698)",
         str(sec["S2_executionAfterRedirect"]["before"]), "0", "ok", sec["S2_executionAfterRedirect"]["note"]),
        ("Security", "SQL text built from request input (CWE-89)",
         f"{s3['before']['queriesBuiltFromInput']} queries", "0 (typed arguments)", "ok", s3["note"]),
        ("Security", "Request inputs with no declared type",
         str(s4["before"]["untypedRequestInputs"]), f"0 ({s4['after']['declaredParameters']} declared parameters)", "ok", s4["note"]),
        ("Security", "Password columns sent to the client",
         str(sec["S5_sensitiveFields"]["before"]), f"{sec['S5_sensitiveFields']['after']} (flagged)",
         "warn" if sec["S5_sensitiveFields"]["after"] else "ok", sec["S5_sensitiveFields"]["note"]),
        ("Structure", "Files mixing HTML, logic and SQL (concern tangling)",
         f"{t1['before']['mixTwoOrMore']} of {t1['before']['files']} files ({t1['before']['percentMixed']}%)",
         "0 (one job per module)", "ok", t1["note"]),
        ("Structure", "Files that print HTML and run SQL",
         str(t2["before"]), "0", "ok", "after: SQL lives only in the data layer"),
    ]
    out = ["<h2>What the migration changed</h2><p class=muted>Before = the legacy PHP app. After = the generated "
           "backend. Measures: attack surface (Manadhata &amp; Wing 2011), concern tangling (Eaddy et al. 2008), "
           "cyclomatic complexity (McCabe 1976).</p>",
           "<table><tr><th>Factor</th><th>Measure</th><th>Before</th><th>After</th><th>Why</th></tr>"]
    for fac, name, before, after, cls, note in rows:
        out.append(f"<tr><td><span class=chip>{esc(fac)}</span></td><td>{esc(name)}</td><td class=bad>{esc(before)}</td>"
                   f"<td class={cls}>{esc(after)}</td><td class=note>{esc(note)}</td></tr>")
    out.append("</table>")
    b = c1["before"]
    out.append(f"<p class=muted>Cyclomatic complexity of the legacy code: {b['units']} units, median {b['median']}, "
               f"max {b['max']}, {b['over10']} over McCabe's threshold of 10. {esc(c1['note'])}.</p>")
    split = c1["after"]["pagesSplitIntoEndpoints"][:6]
    if split:
        out.append("<table><tr><th>Legacy page</th><th>V(G) of the page</th><th>Split into endpoints</th></tr>")
        for sp in split:
            out.append(f"<tr><td><code>{esc(sp['page'])}</code></td><td>{sp['vg']}</td><td>{sp['endpoints']}</td></tr>")
        out.append("</table>")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1.report", description="HTML review report")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path("analysis-out"))
    ap.add_argument("--title", default="Legacy app")
    args = ap.parse_args(argv)
    if not (args.out_dir / "flags.json").exists():
        print("error: run `python -m c1.contract` first", file=sys.stderr)
        return 1
    (args.out_dir / "report.html").write_text(build(args.out_dir, args.title), encoding="utf-8")
    print(f"-> {args.out_dir}/report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
