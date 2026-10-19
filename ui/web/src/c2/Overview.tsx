import { C2Summary, Run } from "../api";
import { SEVERITY_ORDER } from "./glossary";

export default function Overview({ run, summary }: { run: Run; summary: C2Summary }) {
  const pages = summary.pages ?? [];
  const byRole = (role: string) => pages.filter((p) => p.role === role).length;
  const severity = summary.severity ?? {};
  const total = SEVERITY_ORDER.reduce((n, s) => n + (severity[s] ?? 0), 0) || 1;

  const stats: [string, string | number, string?][] = [
    ["Units", summary.units ?? pages.length],
    ["Pages", byRole("page")],
    ["Function components", byRole("component")],
    ["Shells", (summary.shells ?? []).length],
    ["Components generated", summary.components ?? 0],
    ["C2Todos", summary.c2todos ?? 0],
    ["Crashes", (summary.crashes ?? []).length, (summary.crashes ?? []).length ? "c-todo" : "c-ok"],
    ["Not compiling", summary.tscRan ? (summary.notCompiling ?? []).length : "not run"],
    ["next build", summary.nextBuild ? "ok" : "failed", summary.nextBuild ? "c-ok" : "c-todo"],
    ["Took", `${summary.seconds ?? Math.round((run.finished ?? run.started) - run.started)} s`],
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Component 2 / Overview</div>
          <div className="page-title">{summary.app}: frontend migration</div>
          <div className="page-sub">
            Rules v{summary.rules} · {summary.data ? "replay data (test fixture)" : "no data contract"} ·{" "}
            {summary.layers ? "deployment layers on" : "no deployment layers"}
          </div>
        </div>
      </div>

      <div className="banner">
        <b>Input:</b> {summary.input}: no concern labels, no real contract. <b>{summary.app}</b> is a discovery app, so these
        numbers describe this run, <b>not an evaluation result</b>.
      </div>

      <section className="block">
        <div className="stat-grid">
          {stats.map(([label, value, tone]) => (
            <div key={label} className="stat-card">
              <span className={`num ${tone ?? ""}`} style={tone ? { background: "none" } : undefined}>{value}</span>
              <span className="stat-label">{label}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="block">
        <div className="section-title">What the generated code still needs</div>
        <div className="bar">
          {SEVERITY_ORDER.map((s) => (severity[s] ?? 0) > 0 && (
            <div key={s} className={s} style={{ width: `${(100 * (severity[s] ?? 0)) / total}%` }}>{severity[s]}</div>
          ))}
        </div>
        <div className="legend">
          <span><span className="tag c-todo">todo</span> a C2Todo: a developer must finish it</span>
          <span><span className="tag c-review">review</span> generated, a developer should check it</span>
          <span><span className="tag c-info">info</span> an expected difference, counted apart</span>
        </div>
      </section>

      <section className="block">
        <div className="section-title">Units</div>
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table>
            <thead>
              <tr><th>Unit</th><th>Role</th><th>Route</th><th>Components</th><th className="num">C2Todos</th>
                <th className="num">review</th><th className="num">info</th><th>Stage 2</th></tr>
            </thead>
            <tbody>
              {pages.map((p) => (
                <tr key={p.key}>
                  <td className="mono">{p.key}</td>
                  <td>{p.notGenerated ? <span className="tag c-info">not generated: {p.notGenerated.reason}</span> : p.role}</td>
                  <td className="mono muted">{p.route ?? ""}</td>
                  <td className="mono">{(p.components ?? []).join(", ")}</td>
                  <td className="num">{p.c2todos ?? ""}</td>
                  <td className="num">{p.severity?.review ?? ""}</td>
                  <td className="num">{p.severity?.info ?? ""}</td>
                  <td className="mono muted">
                    {p.stage2 ? Object.entries(p.stage2).map(([k, v]) => `${k} ${v}`).join(" · ") : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
