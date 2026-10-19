import { useEffect, useMemo, useState } from "react";
import { C2Summary, Run, runJSON } from "../api";
import { REASONS, SEVERITY_ORDER } from "./glossary";

type Item = {
  unit: string; kind: string; flag?: string; reason: string; severity: string;
  file: string; line: number; sourceLine: number | null; detail: string;
};

export default function Flags({ run, summary }: { run: Run; summary: C2Summary }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [severity, setSeverity] = useState<string>("todo");
  const [reason, setReason] = useState<string>("");

  useEffect(() => {
    const units = (summary.pages ?? []).filter((p) => (p.files ?? []).length > 0);
    Promise.all(units.map(async (u) => {
      const s5 = await runJSON<{ c2todos: Item[]; flags: Item[] }>(run.id, `${summary.pipeline}/${u.key}/stage5.json`);
      return [...s5.c2todos, ...s5.flags].map((i) => ({ ...i, unit: u.key }));
    })).then((all) => setItems(all.flat()));
  }, [run.id, summary]);

  const label = (i: Item) => (i.kind === "C2Todo" ? i.reason : i.flag ?? i.reason);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const i of items ?? []) c[i.severity] = (c[i.severity] ?? 0) + 1;
    return c;
  }, [items]);
  const shown = (items ?? []).filter((i) => i.severity === severity);
  const reasons = Array.from(new Set(shown.map(label))).sort();
  const rows = shown.filter((i) => !reason || label(i) === reason);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Component 2 / Flags & C2Todos</div>
          <div className="page-title">What a developer still has to do</div>
          <div className="page-sub">Every C2Todo and flag of the run's Stage 5 outputs, with its generated file line and PHP source line.</div>
        </div>
      </div>
      <div className="chips">
        {SEVERITY_ORDER.map((s) => (
          <button key={s} className={`chip ${s === severity ? "active" : ""}`} onClick={() => { setSeverity(s); setReason(""); }}>
            {s} · {counts[s] ?? 0}
          </button>
        ))}
        <select value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">all reasons ({shown.length})</option>
          {reasons.map((r) => <option key={r} value={r}>{r} ({shown.filter((i) => label(i) === r).length})</option>)}
        </select>
      </div>
      {reason && REASONS[reason] && <div className="banner">{REASONS[reason]}</div>}
      {!items && <div className="muted">Loading…</div>}
      {items && (
        <div className="table-wrap" style={{ maxHeight: 640 }}>
          <table>
            <thead><tr><th>Unit</th><th>Kind</th><th>Reason</th><th>Generated file</th><th className="num">PHP line</th><th>Detail</th></tr></thead>
            <tbody>
              {rows.map((i, n) => (
                <tr key={n}>
                  <td className="mono">{i.unit}</td>
                  <td><span className={`tag c-${i.severity}`}>{i.kind}</span></td>
                  <td className="mono">{label(i)}</td>
                  <td className="mono muted">{i.file}:{i.line}</td>
                  <td className="num">{i.sourceLine ?? ""}</td>
                  <td className="muted">{i.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
