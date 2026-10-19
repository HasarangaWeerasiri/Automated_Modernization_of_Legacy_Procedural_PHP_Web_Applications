import { useEffect, useMemo, useState } from "react";
import { C2Summary, Run, runJSON } from "../api";
import { REASONS, SEVERITY_ORDER } from "./glossary";

type Item = {
  unit: string; kind: string; flag?: string; reason: string; severity: string;
  file: string; line: number; sourceLine: number | null; detail: string;
  inAbstained?: boolean; severityIfRendered?: string; nodeId?: string;
};

export default function Flags({ run, summary }: { run: Run; summary: C2Summary }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [lines, setLines] = useState<Record<string, Record<string, number>>>({});
  const [severity, setSeverity] = useState<string>("todo");
  const [reason, setReason] = useState<string>("");

  useEffect(() => {
    // every unit Stage 5 ran on, generated or not (a not-generated unit still has flags, e.g. non_html_response)
    const units = (summary.pages ?? []).filter((p) => p.severity !== undefined);
    Promise.all(units.map(async (u) => {
      const s5 = await runJSON<{ c2todos: Item[]; flags: Item[] }>(run.id, `${summary.pipeline}/${u.key}/stage5.json`);
      return [...s5.c2todos, ...s5.flags].map((i) => ({ ...i, unit: u.key }));
    })).then((all) => setItems(all.flat()));
    if (summary.nodeLines) runJSON<Record<string, Record<string, number>>>(run.id, summary.nodeLines).then(setLines).catch(() => {});
  }, [run.id, summary]);
  // Stage 5's own source line, else the line of the item's node id (from the timeline, via the run's node index).
  const phpLine = (i: Item): [number | null, boolean] =>
    i.sourceLine ? [i.sourceLine, false] : [(i.nodeId && lines[i.unit]?.[i.nodeId]) || null, true];

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
          <div className="page-sub">
            PHP line: a number is Stage 5's own source line; <span className="mono">12*</span> is the line of the item's PHP node
            (read from the timeline); <span className="mono">—</span> is a component-tree region, not one line (its tree id is in Detail).
          </div>
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
      {severity === "info" && (
        <div className="banner">
          <b>info</b> holds expected differences (e.g. <span className="mono">escaping_changed</span>,{" "}
          <span className="mono">legacy_link</span>) and every item inside <b>abstained</b> code: the Abstain node's own
          C2Todo is the one todo for that region, so the items inside it are listed but counted once (generation-spec,
          “Abstained code is counted once”).
        </div>
      )}
      {!items && <div className="muted">Loading…</div>}
      {items && (
        <div className="table-wrap" style={{ maxHeight: 640 }}>
          <table>
            <thead><tr><th>Unit</th><th>Kind</th><th>Reason</th><th>Generated file</th><th className="num">PHP line</th><th>Detail</th></tr></thead>
            <tbody>
              {rows.map((i, n) => (
                <tr key={n}>
                  <td className="mono">{i.unit}</td>
                  <td><span className={`tag c-${i.severity}`}>{i.kind}</span>
                    {i.inAbstained && i.severityIfRendered && i.severityIfRendered !== i.severity && (
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
                        in abstained code · would be {i.severityIfRendered}
                      </div>
                    )}</td>
                  <td className="mono">{label(i)}</td>
                  <td className="mono muted">{i.file}:{i.line}</td>
                  <td className="num">{(() => {
                    const [line, viaNode] = phpLine(i);
                    if (!line) return <span className="muted" title={i.nodeId ? "node not in this file's timeline" : "a component-tree region, not one PHP line: see Detail"}>—</span>;
                    return <span title={viaNode ? `from node ${i.nodeId}` : "Stage 5 source line"}>{line}{viaNode ? <span className="muted">*</span> : null}</span>;
                  })()}</td>
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
