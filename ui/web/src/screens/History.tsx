import { Config, Run } from "../api";

type Props = {
  config: Config;
  runs: Run[];
  selected: string | null;
  onRefresh: () => void;
  onSelect: (id: string) => void;
};

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "—");

export default function History({ config, runs, selected, onRefresh, onSelect }: Props) {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Run / History</div>
          <div className="page-title">Run History</div>
          <div className="page-sub">Every run is kept in ui/runs/ (its log, summary.json and outputs).</div>
        </div>
        <button className="btn btn-secondary" onClick={onRefresh}>Refresh</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Run</th><th>Component</th><th>App</th><th>Options</th><th>Started</th><th>Took</th><th>Status</th><th /></tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className={r.id === selected ? "selected" : ""}>
                <td className="mono">{r.id}</td>
                <td>{config.components[r.component]?.name.split(" · ")[0] ?? r.component}</td>
                <td>{config.apps[r.app]?.name ?? r.app}</td>
                <td className="mono muted">{Object.entries(r.options).filter(([, v]) => v).map(([k]) => k).join(", ") || "—"}</td>
                <td className="mono muted">{when(r.started)}</td>
                <td className="num">{r.finished ? `${Math.round(r.finished - r.started)} s` : "—"}</td>
                <td><span className={`tag c-${r.status}`}>{r.status}</span></td>
                <td>{r.status === "ok" && r.component === "c2" && (
                  <button className="btn btn-secondary" onClick={() => onSelect(r.id)}>Show results</button>
                )}</td>
              </tr>
            ))}
            {!runs.length && <tr><td colSpan={8} className="muted">No run yet: start one on the Migrate screen.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
