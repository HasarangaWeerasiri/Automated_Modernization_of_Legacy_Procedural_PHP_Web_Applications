import { ComponentConfig } from "../api";

export default function Placeholder({ id, component }: { id: string; component?: ComponentConfig }) {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Other components / {id.toUpperCase()}</div>
          <div className="page-title">{component?.name ?? id}</div>
        </div>
      </div>
      <div className="banner">
        {component?.connected
          ? "Connected: its runs appear in Run History."
          : component?.note ?? "Not connected yet."}
      </div>
      <div className="panel">
        <div className="section-title">How a component connects</div>
        <p className="muted" style={{ marginTop: 0 }}>
          Add it to <span className="mono">ui/config.json</span> with a command that follows the group contract:
        </p>
        <pre className="log" style={{ padding: "12px 14px" }}>{`<command> --app <app> --out <folder>
  exit 0 = ok, 1 = failed
  stdout = log lines ("::step <name>" starts a step)
  <folder>/summary.json = what the UI shows`}</pre>
        <p className="muted">Its result screens go in <span className="mono">ui/web/src/{id}/</span>. See ui/README.md.</p>
      </div>
    </>
  );
}
