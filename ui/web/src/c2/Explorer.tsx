import { useEffect, useMemo, useRef, useState } from "react";
import { C2Summary, Run, runFile, runJSON, runSource } from "../api";
import { REASONS, RULES, roleLabel } from "./glossary";

// Stage outputs, as the pipeline writes them (only the fields shown here).
type TreeNode = {
  type: string; rule: string | null; rules: string[]; status: string; reason: string;
  flags: string[]; nodeIds: string[]; children?: TreeNode[];
};
type Reference = { file: string; line: number; expr?: string };
type Need = { kind: string; name: string; flag: string | null; references?: Reference[] };
type Stage3Component = { componentId: string; type: string; needs: Need[] };
type Stage4Collection = { componentId: string; result: string; reason: string };
type Stage5Component = { componentId: string; type: string; name: string; file: string; c2todos: number };
type Flag = { kind: string; flag?: string; reason: string; severity: string; file: string; line: number; sourceLine: number | null; detail: string };
type Unit = {
  tree: TreeNode;
  s3: Stage3Component[];
  s4: Stage4Collection[];
  s5: { components: Stage5Component[]; files: string[]; c2todos: Flag[]; flags: Flag[] };
  source: string;
};

function withIds(node: TreeNode, id = "0"): (TreeNode & { id: string; depth: number })[] {
  const out: (TreeNode & { id: string; depth: number })[] = [{ ...node, id, depth: id.split(".").length - 1 }];
  (node.children ?? []).forEach((child, i) => out.push(...withIds(child, `${id}.${i}`)));
  return out;
}

export default function Explorer({ run, summary }: { run: Run; summary: C2Summary }) {
  const units = useMemo(() => (summary.pages ?? []).filter((p) => (p.components ?? []).length > 0), [summary]);
  const [key, setKey] = useState(units.find((u) => u.key === "admin-panel.php")?.key ?? units[0]?.key ?? "");
  const [unit, setUnit] = useState<Unit | null>(null);
  const [selected, setSelected] = useState("0");
  const [file, setFile] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const sourceBox = useRef<HTMLDivElement>(null);
  const base = `${summary.pipeline}/${key}`;
  const row = units.find((u) => u.key === key);

  useEffect(() => {
    if (!key || !row) return;
    setUnit(null);
    setSelected("0");
    setError(null);
    (async () => {
      try {
        const [s2, s3, s4, s5, source] = await Promise.all([
          runJSON<{ tree: TreeNode }>(run.id, `${base}/stage2.json`),
          runJSON<{ components: Stage3Component[] }>(run.id, `${base}/stage3.json`),
          runJSON<{ collections: Stage4Collection[] }>(run.id, `${base}/stage4.json`),
          runJSON<Unit["s5"]>(run.id, `${base}/stage5.json`),
          runSource(run.id, row.path).catch(() => ""),
        ]);
        setUnit({ tree: s2.tree, s3: s3.components, s4: s4.collections, s5, source });
        setFile(s5.components[0]?.file ?? s5.files[0] ?? null);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [run.id, key, base, row]);

  useEffect(() => {
    if (!file) return;
    runFile(run.id, `${base}/generated/${file}`).then(setCode).catch((e) => setCode(String(e)));
  }, [run.id, base, file]);

  const nodes = useMemo(() => (unit ? withIds(unit.tree) : []), [unit]);
  const node = nodes.find((n) => n.id === selected);
  const inside = (id: string) => id === selected || id.startsWith(`${selected}.`);
  const fileName = row?.path.split("/").pop() ?? "";
  const lines = useMemo(() => {
    const own = new Set<number>();
    for (const c of unit?.s3 ?? []) {
      if (!inside(c.componentId)) continue;
      for (const need of c.needs) for (const r of need.references ?? []) if (r.file.endsWith(row?.path ?? "\0")) own.add(r.line);
    }
    return own;
  }, [unit, selected, row]); // eslint-disable-line react-hooks/exhaustive-deps
  const todoLines = useMemo(
    () => new Set((unit?.s5.c2todos ?? []).map((t) => t.sourceLine).filter((l): l is number => !!l)), [unit]);

  useEffect(() => {
    const box = sourceBox.current;
    const first = box?.querySelector<HTMLElement>(".ln.hl");
    if (box && first) box.scrollTop = first.offsetTop - 120;
  }, [lines]);

  const s3 = unit?.s3.find((c) => c.componentId === selected);
  const s4 = unit?.s4.filter((c) => inside(c.componentId)) ?? [];
  const s5 = unit?.s5.components.find((c) => c.componentId === selected);
  useEffect(() => {
    if (s5?.file) setFile(s5.file); // the selected component's own generated file
  }, [s5?.file]);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Component 2 / Component Explorer</div>
          <div className="page-title">Why each component is there</div>
          <div className="page-sub">PHP source → Stage 2 component tree (rule, status) → generated React code.</div>
        </div>
        <select value={key} onChange={(e) => setKey(e.target.value)}>
          {units.map((u) => <option key={u.key} value={u.key}>{u.key} ({roleLabel(u.role)})</option>)}
        </select>
      </div>
      {error && <div className="banner error">{error}</div>}
      {!unit && !error && <div className="muted">Loading…</div>}
      {unit && (
        <div className="explorer">
          <div>
            <div className="section-title">{fileName} <span className="muted mono" style={{ fontSize: 12 }}>legacy PHP</span></div>
            <div className="code" ref={sourceBox}>
              {(unit.source || "(source not available)").split("\n").map((text, i) => (
                <div key={i} className={`ln ${lines.has(i + 1) ? "hl" : todoLines.has(i + 1) ? "hl-todo" : ""}`}>
                  <span>{i + 1}</span><span>{text.replace(/\r$/, "")}</span>
                </div>
              ))}
            </div>
            <div className="legend">
              <span><span className="tag c-running">blue</span> lines the selected component reads</span>
              <span><span className="tag c-todo">red</span> a C2Todo's source line</span>
            </div>
          </div>

          <div>
            <div className="section-title">Component tree <span className="muted mono" style={{ fontSize: 12 }}>Stage 2</span></div>
            <div className="tree">
              {nodes.map((n) => (
                <div key={n.id} className={`node ${n.id === selected ? "selected" : ""}`} style={{ paddingLeft: 6 + n.depth * 14 }}
                  onClick={() => setSelected(n.id)} title={`${n.id} · ${n.status}: ${n.reason}`}>
                  <span className={`status-${n.status}`}>●</span>
                  <span>{n.type}</span>
                  {n.rule && <span className="rule">{n.rule}</span>}
                </div>
              ))}
            </div>
            <div className="legend">
              <span className="status-ok">● ok</span><span className="status-review">● review</span><span className="status-abstain">● abstain</span>
            </div>
          </div>

          <div>
            {node && (
              <div className="detail">
                <h4>{s5?.name ?? node.type} <span className="muted mono">{node.id}</span></h4>
                <div className="kv">
                  <span className="k">Type</span><span>{node.type}</span>
                  <span className="k">Rules</span>
                  <span>{node.rules.length ? node.rules.map((r) => <div key={r}><b className="mono">{r}</b> {RULES[r] ?? ""}</div>) : <span className="muted">none</span>}</span>
                  <span className="k">Status</span><span><span className={`tag c-${node.status}`}>{node.status}</span></span>
                  <span className="k">Reason</span>
                  <span>{node.reason.split(",").map((r) => <div key={r}><span className="mono">{r}</span>{REASONS[r] ? <span className="muted">: {REASONS[r]}</span> : null}</div>)}</span>
                  {node.flags.length > 0 && <><span className="k">Flags</span><span className="mono">{node.flags.join(", ")}</span></>}
                  {s3 && <><span className="k">Needs</span><span>{s3.needs.length ? s3.needs.map((n, i) => (
                    <div key={i} className="mono">{n.name} <span className="muted">{n.kind}{n.flag ? ` · ${n.flag}` : ""}{n.references?.[0]?.line ? ` · line ${n.references[0].line}` : ""}</span></div>
                  )) : <span className="muted">none</span>}</span></>}
                  {s4.length > 0 && <><span className="k">Data (Stage 4)</span><span>{s4.map((c) => (
                    <div key={c.componentId} className="mono">{c.componentId}: {c.result} <span className="muted">({c.reason})</span></div>
                  ))}</span></>}
                  {s5 && <><span className="k">Generated</span><span className="mono">{s5.file} <span className="muted">· {s5.c2todos} C2Todos</span></span></>}
                </div>
              </div>
            )}
            <div className="section-title">Generated code <span className="muted mono" style={{ fontSize: 12 }}>Stage 5</span></div>
            <div className="tabs">
              {unit.s5.files.map((f) => (
                <button key={f} className={`tab ${f === file ? "active" : ""}`} onClick={() => setFile(f)}>{f.split("/").pop()}</button>
              ))}
            </div>
            <div className="code">
              {code.split("\n").map((text, i) => (
                <div key={i} className={`ln ${/C2Todo/.test(text) ? "hl-todo" : ""}`}><span>{i + 1}</span><span>{text}</span></div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
