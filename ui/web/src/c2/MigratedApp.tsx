import { useEffect, useState } from "react";
import { Run, getJSON, postJSON } from "../api";

type Served = { run: string | null; url: string | null; running: boolean };

export default function MigratedApp({ run, url }: { run: Run; url: string }) {
  const [served, setServed] = useState<Served | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const poll = () => getJSON<Served>("/api/serve").then(setServed).catch((e) => setError(String(e)));
  useEffect(() => { poll(); }, []);

  const start = async () => {
    setError(null);
    setReady(false);
    try {
      await postJSON("/api/runs/" + run.id + "/serve");
      await poll();
      setTimeout(() => setReady(true), 6000); // next start needs a few seconds
    } catch (e) {
      setError(String(e));
    }
  };
  const stop = async () => { await postJSON("/api/serve/stop"); setReady(false); await poll(); };
  const mine = served?.run === run.id && served.running;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Component 2 / Migrated App</div>
          <div className="page-title">The generated Next.js app</div>
          <div className="page-sub">Starts <span className="mono">next start</span> on this run's build{run.summary?.data ? " and the replay API (test fixture)" : ""}.</div>
        </div>
        <div className="btn-row">
          {!mine && <button className="btn btn-primary" onClick={start}>Start the migrated app</button>}
          {mine && <a className="btn btn-primary" href={url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>Open in a new tab ↗</a>}
          {served?.running && <button className="btn btn-secondary" onClick={stop}>Stop</button>}
        </div>
      </div>
      {error && <div className="banner error">{error}</div>}
      <div className="banner">
        Forms, logins and sessions are Component 1's: a page shows, a POST does not work yet.
        {run.summary?.layers ? " Links, styles and inline JS come from the opt-in deployment layers (not measured)." : " Without the deployment layers, .php links and styles do not work."}
      </div>
      {mine && ready && <iframe title="migrated app" src={url} style={{ width: "100%", height: 720, border: "1px solid var(--rule)", borderRadius: 4, background: "#fff" }} />}
      {mine && !ready && <div className="muted">Starting…</div>}
    </>
  );
}
