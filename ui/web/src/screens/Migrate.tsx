import { useEffect, useRef, useState } from "react";
import { Config, postJSON } from "../api";

const STEPS: [string, string][] = [
  ["harness", "Harness (timelines)"],
  ["stages", "Stages 1–5"],
  ["build", "next build"],
  ["layers", "Deployment layers"],
  ["done", "Done"],
];

type Props = {
  config: Config;
  current: string | null;
  running: boolean;
  onStarted: (id: string) => void;
  onFinished: (id: string) => void;
  onShowResults: () => void;
};

export default function Migrate({ config, current, running, onStarted, onFinished, onShowResults }: Props) {
  const c2 = config.components.c2;
  const [app, setApp] = useState(c2.apps?.[0] ?? "");
  const [options, setOptions] = useState<Record<string, boolean>>(
    Object.fromEntries((c2.options ?? []).map((o) => [o.id, o.default])),
  );
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setError(null);
    try {
      const { id } = await postJSON<{ id: string }>("/api/runs", { component: "c2", app, options });
      onStarted(id);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Run / Migrate</div>
          <div className="page-title">Migrate a PHP app's frontend to Next.js</div>
          <div className="page-sub">
            Runs Component 2 as it is: the harness, Stages 1–5, the Next.js assembly and <span className="mono">next build</span>.
          </div>
        </div>
      </div>

      <div className="banner">
        Only the <b>discovery apps</b> can be run here. The evaluation apps stay locked until the gold standard is
        committed, and the deployment layers are never used for a measured run.
      </div>

      <section className="block">
        <div className="section-title">App</div>
        <div className="card-grid">
          {(c2.apps ?? []).map((id) => (
            <button key={id} className={`app-card ${app === id ? "selected" : ""}`} onClick={() => setApp(id)}>
              <div className="name">{config.apps[id]?.name ?? id}</div>
              <div className="note mono">{id} · {config.apps[id]?.note}</div>
            </button>
          ))}
        </div>
      </section>

      <section className="block">
        <div className="section-title">Options</div>
        <div className="panel">
          {(c2.options ?? []).map((o) => (
            <label key={o.id} className="check">
              <input type="checkbox" checked={options[o.id] ?? false}
                onChange={(e) => setOptions({ ...options, [o.id]: e.target.checked })} />
              <span>{o.label}<div className="help">{o.help}</div></span>
            </label>
          ))}
        </div>
      </section>

      <div className="btn-row" style={{ marginBottom: 26 }}>
        <button className="btn btn-primary" onClick={start} disabled={!app || running}>
          {running ? "Migration running…" : `Migrate ${config.apps[app]?.name ?? app}`}
        </button>
        {error && <span className="tag c-todo">{error}</span>}
      </div>

      {current && <Progress key={current} id={current} onEnd={onFinished} onShowResults={onShowResults} />}
    </>
  );
}

function Progress({ id, onEnd, onShowResults }: { id: string; onEnd: (id: string) => void; onShowResults: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<string>("running");
  const log = useRef<HTMLDivElement>(null);
  const endRef = useRef(onEnd); // the parent's callback changes on every render; the stream must not
  endRef.current = onEnd;

  useEffect(() => {
    setLines([]);
    const source = new EventSource(`/api/runs/${id}/events`);
    source.onmessage = (e) => setLines((l) => [...l, JSON.parse(e.data)]);
    source.addEventListener("end", (e) => {
      setStatus(JSON.parse((e as MessageEvent).data));
      source.close();
      endRef.current(id);
    });
    return () => source.close();
  }, [id]);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [lines]);

  const reached = lines.filter((l) => l.startsWith("::step ")).map((l) => l.slice(7));
  const last = reached[reached.length - 1];
  const total = Number(lines.map((l) => /^(\d+) timelines/.exec(l)?.[1]).find(Boolean) ?? 0);
  const units = lines.filter((l) => /^\[\d+\] Stages/.test(l)).length;
  const detailOf = (step: string) => {
    if (step === "stages" && units) return `${units} units${total ? ` (${total} timelines)` : ""}`;
    const start = lines.indexOf(`::step ${step}`);
    if (start < 0) return "";
    const next = lines.findIndex((l, i) => i > start && l.startsWith("::step "));
    const own = lines.slice(start + 1, next < 0 ? undefined : next).filter((l) => l && !l.startsWith("["));
    return own[own.length - 1] ?? "";
  };
  const state = (step: string) => {
    if (!reached.includes(step)) return ""; // not reached (or skipped, e.g. no layers)
    if (step === last && status === "running") return "running";
    if (step === last && status === "failed") return "failed";
    return "done";
  };

  return (
    <section className="block">
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div className="section-title" style={{ margin: 0 }}>Pipeline run <span className="mono muted">{id}</span></div>
        <div className="btn-row">
          <span className={`pill c-${status}`}>{status}</span>
          {status === "ok" && <button className="btn btn-secondary" onClick={onShowResults}>View results →</button>}
        </div>
      </div>
      <div className="steps">
        {STEPS.map(([step, name]) => (
          <div key={step} className="step">
            <div className={`dot ${state(step)}`} />
            <div className="step-name">{name}</div>
            <div className="step-detail">{detailOf(step)}</div>
          </div>
        ))}
      </div>
      <div className="section-title" style={{ marginTop: 20 }}>Live log</div>
      <div className="log" ref={log}>
        {lines.map((l, i) => (
          <div key={i} className={l.startsWith("::step") ? "marker" : ""}>{l}</div>
        ))}
      </div>
    </section>
  );
}
