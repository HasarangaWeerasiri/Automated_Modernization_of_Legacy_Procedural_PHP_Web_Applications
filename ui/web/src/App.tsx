import { useCallback, useEffect, useState } from "react";
import { Config, Run, getJSON } from "./api";
import Migrate from "./screens/Migrate";
import History from "./screens/History";
import Placeholder from "./screens/Placeholder";
import Overview from "./c2/Overview";
import Explorer from "./c2/Explorer";
import Flags from "./c2/Flags";
import MigratedApp from "./c2/MigratedApp";
import Layers from "./c2/Layers";

type Screen = "migrate" | "history" | "c2-overview" | "c2-explorer" | "c2-flags" | "c2-layers" | "c2-app"
  | `other-${string}`;

const C2_SCREENS: [Screen, string][] = [
  ["c2-overview", "Overview"],
  ["c2-explorer", "Component Explorer"],
  ["c2-flags", "Flags & C2Todos"],
  ["c2-layers", "Deployment Layers"],
  ["c2-app", "Migrated App"],
];

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [screen, setScreen] = useState<Screen>("migrate");
  const [current, setCurrent] = useState<string | null>(null); // the run the Migrate screen follows
  const [running, setRunning] = useState(false); // whether that run is still going
  const [result, setResult] = useState<Run | null>(null); // the run the result screens show
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const list = await getJSON<Run[]>("/api/runs");
    setRuns(list);
    return list;
  }, []);

  const showResult = useCallback(async (id: string) => {
    setResult(await getJSON<Run>(`/api/runs/${id}`));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        setConfig(await getJSON<Config>("/api/config"));
        const list = await refresh();
        const running = list.find((r) => r.status === "running");
        if (running) {
          setCurrent(running.id);
          setRunning(true);
        }
        const last = list.find((r) => r.component === "c2" && r.status === "ok");
        if (last) await showResult(last.id);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [refresh, showResult]);

  const finished = async (id: string) => {
    setRunning(false);
    await refresh();
    await showResult(id);
  };

  const summary = result?.summary ?? null;
  const appName = summary ? config?.apps[summary.app]?.name ?? summary.app : "—";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-name">PHP Modernizer</span>
          <span className="brand-tag">RESEARCH UI</span>
        </div>
        <div className="nav-group">
          <div className="nav-label">Run</div>
          <button className={`nav-item ${screen === "migrate" ? "active" : ""}`} onClick={() => setScreen("migrate")}>
            Migrate
          </button>
          <button className={`nav-item ${screen === "history" ? "active" : ""}`} onClick={() => setScreen("history")}>
            Run History
          </button>
        </div>
        <div className="nav-group">
          <div className="nav-label">Component 2 · Frontend</div>
          {C2_SCREENS.map(([id, label]) => (
            <button key={id} className={`nav-item ${screen === id ? "active" : ""}`} disabled={!summary}
              onClick={() => setScreen(id)}>
              {label}
            </button>
          ))}
        </div>
        <div className="nav-group">
          <div className="nav-label">Other components</div>
          {config &&
            Object.entries(config.components)
              .filter(([id]) => id !== "c2")
              .map(([id, c]) => (
                <button key={id} className={`nav-item ${screen === `other-${id}` ? "active" : ""}`}
                  onClick={() => setScreen(`other-${id}`)}>
                  {c.name.split(" · ")[0]}
                  {!c.connected && <span className="soon">not connected</span>}
                </button>
              ))}
        </div>
      </aside>

      <div className="main">
        <div className="header-bar">
          <div className="hb-field"><span className="hb-label">Project</span><span className="hb-value">{appName}</span></div>
          <div className="hb-field"><span className="hb-label">Rules</span><span className="hb-value">{summary ? `v${summary.rules}` : "—"}</span></div>
          <div className="hb-field"><span className="hb-label">Run</span><span className="hb-value">{result?.id ?? "—"}</span></div>
          <div className="hb-field"><span className="hb-label">Input</span><span className="hb-value">{summary ? "C2 test harness" : "—"}</span></div>
          <div className="hb-spacer" />
          {result && <span className={`pill c-${result.status}`}>{result.status}</span>}
        </div>

        <div className="content">
          {error && <div className="banner error">The UI server did not answer: {error}</div>}
          {config && screen === "migrate" && (
            <Migrate config={config} current={current} running={running}
              onStarted={(id) => { setCurrent(id); setRunning(true); }} onFinished={finished}
              onShowResults={() => setScreen("c2-overview")} />
          )}
          {config && screen === "history" && (
            <History config={config} runs={runs} selected={result?.id ?? null} onRefresh={refresh}
              onSelect={async (id) => { await showResult(id); setScreen("c2-overview"); }} />
          )}
          {result && summary && screen === "c2-overview" && <Overview run={result} summary={summary} />}
          {result && summary && screen === "c2-explorer" && <Explorer run={result} summary={summary} />}
          {result && summary && screen === "c2-flags" && <Flags run={result} summary={summary} />}
          {result && summary && screen === "c2-layers" && <Layers run={result} summary={summary} />}
          {result && summary && config && screen === "c2-app" && (
            <MigratedApp run={result} url={config.components.c2.serve?.url ?? ""} />
          )}
          {config && screen.startsWith("other-") && (
            <Placeholder id={screen.slice(6)} component={config.components[screen.slice(6)]} />
          )}
        </div>
      </div>
    </div>
  );
}
