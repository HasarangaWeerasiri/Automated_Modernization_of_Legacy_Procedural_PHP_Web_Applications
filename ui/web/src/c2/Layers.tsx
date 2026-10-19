import { useEffect, useMemo, useState } from "react";
import { C2Summary, Run, runJSON, runSource } from "../api";

// The opt-in deployment layers' own tables (tools/routing/routes.py, tools/assets/legacy_assets.py + inline_js.py).
type Where = { file: string; line: number; event?: string };
type Skip = Where & { skip: string };
type Note = Where & { note: string };
type Attrs = Record<string, string | boolean>;
type AssetRow = {
  path: string; route: string; group: string;
  styles: (Where & { attrs: Attrs })[];
  scripts: (Where & { attrs: Attrs })[];
  skipped: Skip[];
  inline?: { steps: (Where & { code?: string; src?: Attrs })[]; handlers: (Where & { selector: string; event: string; code: string })[]; skipped: Skip[]; notes: Note[] };
};
type Assets = { pages: AssetRow[] };
type Routes = { rewrites: { source: string; destination: string }[]; redirects: { source: string; destination: string }[]; noPage: { path: string; url: string | null; reason: string }[] };

// What each reason means (the tools' own docstrings, docs/assets-layer.md §2 and §7).
const WHY: Record<string, string> = {
  conditional: "Under an if / else / switch: it runs only on some requests (e.g. after a form post), so it is not run on every load.",
  include_not_literal: "An include whose path is a PHP expression: the file it names is not guessed.",
  tag_built_by_php: "A stylesheet or script tag that PHP builds at run time (e.g. from a setting): not guessed.",
  script_built_by_php: "An inline script that PHP writes piece by piece: not run.",
  handler_built_by_php: "An event handler built by PHP (e.g. a row id in onclick): it would need Stage 5 to translate it.",
  document_write: "Calls document.write: run after the page has loaded, it would replace the page.",
  ambiguous: "Several elements have the same attributes but different code: which is which cannot be told, so none is set.",
  no_selector: "A tag with no attribute to find it by.",
  dom_sink: "Writes HTML or runs code from a string (innerHTML, eval…): kept as the legacy code had it, listed for review.",
  load_event: "Waits for the page's load event: the loader calls it once, as the event has already passed.",
};

type Tab = "skipped" | "notes" | "loaded" | "routes";

export default function Layers({ run, summary }: { run: Run; summary: C2Summary }) {
  const [assets, setAssets] = useState<Assets | null>(null);
  const [routes, setRoutes] = useState<Routes | null>(null);
  const [tab, setTab] = useState<Tab>("skipped");
  const [page, setPage] = useState<string>("");
  const [sources, setSources] = useState<Record<string, string[]>>({});
  const [error, setError] = useState<string | null>(null);
  const tables = summary.layerTables;

  useEffect(() => {
    if (!tables) return;
    Promise.all([runJSON<Assets>(run.id, tables.assets), runJSON<Routes>(run.id, tables.routes)])
      .then(([a, r]) => { setAssets(a); setRoutes(r); setPage(a.pages[0]?.path ?? ""); })
      .catch((e) => setError(String(e)));
  }, [run.id, tables]);

  // One row per distinct item (the same skipped line is found from every page that includes its file).
  const collect = <T extends Where>(pick: (r: AssetRow) => [string, T[]][]) => {
    const rows = new Map<string, { layer: string; item: T; pages: string[] }>();
    for (const r of assets?.pages ?? []) {
      for (const [layer, items] of pick(r)) {
        for (const item of items) {
          const key = `${layer}|${JSON.stringify(item)}`;
          const row = rows.get(key) ?? { layer, item, pages: [] };
          row.pages.push(r.path);
          rows.set(key, row);
        }
      }
    }
    return [...rows.values()].sort((a, b) => a.item.file.localeCompare(b.item.file) || a.item.line - b.item.line);
  };
  const skipped = useMemo(() => collect<Skip>((r) => [["assets", r.skipped], ["inline JS", r.inline?.skipped ?? []]]), [assets]); // eslint-disable-line react-hooks/exhaustive-deps
  const notes = useMemo(() => collect<Note>((r) => [["inline JS", r.inline?.notes ?? []]]), [assets]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { // the legacy line each row points at
    const files = new Set([...skipped, ...notes].map((r) => r.item.file).filter((f) => !(f in sources)));
    files.forEach((file) => runSource(run.id, file)
      .then((text) => setSources((s) => ({ ...s, [file]: text.split("\n") })))
      .catch(() => setSources((s) => ({ ...s, [file]: [] }))));
  }, [skipped, notes]); // eslint-disable-line react-hooks/exhaustive-deps
  const lineOf = (w: Where) => (sources[w.file]?.[w.line - 1] ?? "").trim();

  if (!tables) {
    return <div className="banner">This run has no deployment-layer tables: it ran without “Deployment layers”, or before
      this screen existed. Run Migrate again with the option ticked.</div>;
  }
  const count = (kind: string) => skipped.filter((r) => r.item.skip === kind).length;
  const kinds = Array.from(new Set(skipped.map((r) => r.item.skip))).sort((a, b) => count(b) - count(a));
  const row = assets?.pages.find((r) => r.path === page);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Component 2 / Deployment Layers</div>
          <div className="page-title">What the opt-in layers added, and what they left out</div>
          <div className="page-sub">Routing, assets and inline JS: deployment aids after Stage 5, not part of the pipeline, not measured.</div>
        </div>
      </div>
      {error && <div className="banner error">{error}</div>}
      <section className="block">
        <div className="stat-grid">
          <div className="stat-card"><span className="num">{routes?.rewrites.length ?? "…"}</span><span className="stat-label">.php rewrites</span></div>
          <div className="stat-card"><span className="num">{assets ? assets.pages.reduce((n, r) => n + r.styles.length, 0) : "…"}</span><span className="stat-label">stylesheets</span></div>
          <div className="stat-card"><span className="num">{assets ? assets.pages.reduce((n, r) => n + r.scripts.length, 0) : "…"}</span><span className="stat-label">script files</span></div>
          <div className="stat-card"><span className="num">{assets ? assets.pages.reduce((n, r) => n + (r.inline?.handlers.length ?? 0), 0) : "…"}</span><span className="stat-label">event handlers</span></div>
          <div className="stat-card"><span className="num">{summary.staticFiles ?? "—"}</span><span className="stat-label">static files</span></div>
          <div className="stat-card"><span className="num" style={{ color: "var(--review)" }}>{skipped.length}</span><span className="stat-label">skipped (distinct)</span></div>
        </div>
      </section>

      <div className="tabs">
        {([["skipped", `Skipped · ${skipped.length}`], ["notes", `Notes · ${notes.length}`], ["loaded", "Loaded per page"], ["routes", "Routes"]] as [Tab, string][]).map(([id, label]) => (
          <button key={id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === "skipped" && (
        <>
          <div className="chips">
            {kinds.map((k) => <span key={k} className="chip" title={WHY[k]}>{k} · {count(k)}</span>)}
          </div>
          <div className="banner">Skipped means the layer could not place this with certainty, so it did not guess. Each row is
            one legacy line; “pages” counts the pages whose scan reached it.</div>
          <ItemTable rows={skipped.map((r) => ({ ...r, reason: r.item.skip }))} lineOf={lineOf} />
        </>
      )}
      {tab === "notes" && <ItemTable rows={notes.map((r) => ({ ...r, reason: r.item.note }))} lineOf={lineOf} />}

      {tab === "loaded" && assets && (
        <>
          <div className="btn-row" style={{ marginBottom: 12 }}>
            <select value={page} onChange={(e) => setPage(e.target.value)}>
              {assets.pages.map((r) => <option key={r.path} value={r.path}>{r.path} → {r.route}</option>)}
            </select>
          </div>
          {row && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Kind</th><th>What</th><th>From</th></tr></thead>
                <tbody>
                  {row.styles.map((s, i) => <tr key={`s${i}`}><td><span className="tag c-info">stylesheet</span></td><td className="mono">{String(s.attrs.href)}</td><td className="mono muted">{s.file}:{s.line}</td></tr>)}
                  {(row.inline?.steps ?? row.scripts.map((s): Where & { code?: string; src?: Attrs } => ({ file: s.file, line: s.line, src: s.attrs }))).map((s, i) => (
                    <tr key={`j${i}`}><td><span className="tag c-running">{s.src ? "script file" : "inline script"}</span></td>
                      <td className="mono">{s.src ? String(s.src.src) : (s.code ?? "").trim().slice(0, 140)}</td>
                      <td className="mono muted">{s.file ? `${s.file}:${s.line}` : ""}</td></tr>
                  ))}
                  {(row.inline?.handlers ?? []).map((h, i) => <tr key={`h${i}`}><td><span className="tag c-ok">{h.event}</span></td><td className="mono">{h.selector} → {h.code}</td><td className="mono muted">{h.file}:{h.line}</td></tr>)}
                  {!row.styles.length && !row.scripts.length && !(row.inline?.handlers.length) && <tr><td colSpan={3} className="muted">The legacy page loads nothing.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === "routes" && routes && (
        <div className="table-wrap" style={{ maxHeight: 600 }}>
          <table>
            <thead><tr><th>Legacy URL</th><th>Served by</th><th>Kind</th></tr></thead>
            <tbody>
              {routes.rewrites.map((r) => <tr key={r.source}><td className="mono">{r.source}</td><td className="mono">{r.destination}</td><td><span className="tag c-ok">rewrite</span></td></tr>)}
              {routes.redirects.map((r) => <tr key={`r${r.source}`}><td className="mono">{r.source}</td><td className="mono">{r.destination}</td><td><span className="tag c-review">redirect</span></td></tr>)}
              {routes.noPage.map((r) => <tr key={`n${r.path}`}><td className="mono">{r.url ?? r.path}</td><td className="muted">no page</td><td><span className="tag c-info">{r.reason}</span></td></tr>)}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function ItemTable({ rows, lineOf }: { rows: { layer: string; item: Where; pages: string[]; reason: string }[]; lineOf: (w: Where) => string }) {
  if (!rows.length) return <div className="muted">None.</div>;
  return (
    <div className="table-wrap" style={{ maxHeight: 620 }}>
      <table>
        <thead><tr><th>Reason</th><th>Layer</th><th>Legacy line</th><th>Code</th><th className="num">Pages</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><span className="tag c-review">{r.reason}</span>{r.item.event ? <span className="mono muted"> {r.item.event}</span> : null}
                <div className="muted" style={{ fontSize: 12, marginTop: 4, maxWidth: 320 }}>{WHY[r.reason] ?? ""}</div></td>
              <td>{r.layer}</td>
              <td className="mono">{r.item.file}:{r.item.line}</td>
              <td className="mono" style={{ maxWidth: 520, wordBreak: "break-word" }}>{lineOf(r.item)}</td>
              <td className="num" title={r.pages.join(", ")}>{r.pages.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
