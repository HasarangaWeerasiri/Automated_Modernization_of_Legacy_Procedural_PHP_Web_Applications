// C4 validation dashboard. Every value shown is read from a file the engine wrote, or
// from the progress the engine printed during a run; nothing is computed here that the
// engine did not decide.

"use strict";

const state = {
  benchmarks: [],
  benchmark: null,
  source: "committed",   // "committed" (the evidence kept in git) or a run id
  results: null,
  runs: [],
  status: null,
  liveRun: null,         // the run being watched on the Pipeline Run screen
  validation: { endpoint: null, scenario: null },
  flagFilter: "all",
};

const STEPS = [
  ["environment", "Environment", "both systems in containers, no route out"],
  ["scenarios", "Scenarios", "generated once, kept as inputs"],
  ["capture", "Capture", "the legacy system's behaviour: the oracle"],
  ["suite", "Test suite", "standalone pytest suite for the migrated system"],
  ["verdict", "Replay · Compare · Verdict", "the migrated system held to the oracle"],
];

// ------------------------------------------------------------------- helpers

const $ = (selector) => document.querySelector(selector);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

function fileUrl(path) {
  return `/api/results/${encodeURIComponent(state.source)}/file?path=${encodeURIComponent(path)}`;
}

function verdictTag(verdict) {
  const cls = { PASS: "tag-verified", FAIL: "tag-flagged", INCONCLUSIVE: "tag-assisted" }[verdict] || "tag-neutral";
  return `<span class="tag ${cls}">${esc(String(verdict).toLowerCase())}</span>`;
}

const PROBE_CLASS = {
  "security improvement": "tag-verified", "safe, as before": "tag-verified",
  "vulnerability kept": "tag-flagged", "vulnerability introduced": "tag-flagged",
  "unhandled input": "tag-flagged", "not judged": "tag-assisted",
};

function probeTag(label) {
  return `<span class="tag ${PROBE_CLASS[label] || "tag-neutral"}">${esc(label)}</span>`;
}

function pageHead(eyebrow, title, sub, right = "") {
  return `<div class="page-head"><div><div class="page-eyebrow">${esc(eyebrow)}</div>
    <div class="page-title">${esc(title)}</div>${sub ? `<div class="page-sub">${sub}</div>` : ""}</div>${right}</div>`;
}

function sourceLabel() {
  return state.source === "committed" ? "evidence kept in git" : `run ${state.source}`;
}

function json(value) {
  return JSON.stringify(value, null, 2);
}

function count(list, value) {
  return list.filter((item) => item === value).length;
}

// The engine's own wording for a finding (verdict._describe), so screens and reports agree.
const DELTA_CHANGES = {
  values_changed: "value differs",
  repetition_change: "written a different number of times",
  iterable_item_removed: "missing on the migrated side",
  dictionary_item_removed: "missing on the migrated side",
  iterable_item_added: "only on the migrated side",
  dictionary_item_added: "only on the migrated side",
};
const EXCLUSION_REASONS = {
  computed_on_page: "computed by the legacy page from other data",
  observed_nondeterministic: "different in every capture",
  declared: "declared as excluded",
};

function deltaLines(detail) {
  const lines = [];
  for (const [change, places] of Object.entries(detail || {})) {
    const paths = Array.isArray(places) ? places : Object.keys(places);
    for (const path of paths) {
      const place = path.replace(/^root/, "").replace(/\['([^']*)'\]/g, ".$1").replace(/^\./, "");
      let line = `${place}: ${DELTA_CHANGES[change] || change.replace(/_/g, " ")}`;
      const info = Array.isArray(places) ? null : places[path];
      if (change === "values_changed" && info.new_value === "different on every run") line += ` (legacy ${JSON.stringify(info.old_value)}; the migrated value is different on every run)`;
      else if (change === "values_changed") line += ` (legacy ${JSON.stringify(info.old_value)}, migrated ${JSON.stringify(info.new_value)})`;
      if (change === "repetition_change") line += ` (legacy ${info.old_repeat}, migrated ${info.new_repeat})`;
      lines.push(line);
    }
  }
  return lines;
}

function describe(finding) {
  const kind = finding.kind;
  if ((kind === "delta_differs" || kind === "excluded_field_differs") && finding.detail) {
    const prefix = finding.note ? `${finding.note}: ` : kind === "excluded_field_differs" ? "excluded field differs: " : "";
    return [prefix + "database change", ...deltaLines(finding.detail)];
  }
  if (kind === "record_not_reproduced") {
    const lines = ["a record the legacy page showed is not reproduced"];
    for (const [label, value] of Object.entries(finding.values_not_found || {})) lines.push(`"${label}" = ${JSON.stringify(value)} not found`);
    for (const [label, entry] of Object.entries(finding.values_under_another_column || {})) {
      lines.push(`"${label}" (${entry.column}): legacy ${JSON.stringify(entry.legacy)}, migrated ${JSON.stringify(entry.migrated)}`);
    }
    return lines;
  }
  if (kind === "unexpected_record") return ["a record the legacy page did not show", JSON.stringify(finding.migrated_record)];
  if (kind === "outcome_differs") return [`legacy ${finding.legacy}, migrated ${finding.migrated} (status ${finding.migrated_status_code})`];
  if (kind === "conflicting_expectations") return [`${finding.detail} (also: ${(finding.with || []).join(", ")})`];
  if (kind === "excluded_field_differs" && finding.field) {
    return [`"${finding.field}" is not reproduced, and it is ${EXCLUSION_REASONS[finding.reason] || "excluded"}, so it cannot be judged`];
  }
  if (kind === "unreadable") return [String(finding.detail)];
  if (kind === "nothing_to_compare") return ["the excluded fields leave nothing to compare"];
  return [kind.replace(/_/g, " ")];
}

function findingBlock(finding, flagged) {
  const [title, ...rest] = describe(finding);
  return `<div class="finding"><div class="finding-head">${flagged ? '<span class="tag tag-flagged">difference</span>' : '<span class="tag tag-assisted">cannot be judged</span>'}
    <span class="mono">${esc(finding.where)}</span><span>${esc(title)}</span></div>
    ${rest.length ? `<div class="finding-body"><ul>${rest.map((line) => `<li>${esc(line)}</li>`).join("")}</ul></div>` : ""}</div>`;
}

// A record as JSON, with the lines of the given keys marked.
function recordBlock(record, marked) {
  if (!record) return '<div class="code-block">—</div>';
  const keys = Object.keys(record);
  const lines = keys.map((key, i) => {
    const text = `  ${JSON.stringify(key)}: ${JSON.stringify(record[key])}${i < keys.length - 1 ? "," : ""}`;
    return marked.has(key) ? `<span class="hl">${esc(text)}</span>` : esc(text);
  });
  return `<div class="code-block">{\n${lines.join("\n")}\n}</div>`;
}

// ------------------------------------------------------------------ loading

async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (e) {
    state.status = null;
  }
  const pill = $("#systems-pill");
  const s = state.status;
  if (!s || !s.docker) {
    pill.className = "pill pill-flagged";
    pill.innerHTML = '<span class="pill-dot"></span>Docker is not running';
  } else if (!s.running || s.containers.some((c) => c.state !== "running")) {
    pill.className = "pill pill-neutral";
    pill.innerHTML = '<span class="pill-dot"></span>Systems not running';
  } else {
    pill.className = "pill pill-verified";
    pill.innerHTML = `<span class="pill-dot"></span>Systems running · ${esc(s.running)}`;
  }
}

async function loadRuns() {
  state.runs = await api("/api/runs");
  const select = $("#source-select");
  const runs = state.runs.filter((run) => run.benchmark === state.benchmark && run.finished);
  select.innerHTML = `<option value="committed">Evidence kept in git</option>` +
    runs.map((run) => `<option value="${esc(run.id)}">Run ${esc(run.id)}${run.fault ? " · fault: " + esc(run.fault) : ""}</option>`).join("");
  if (![...select.options].some((o) => o.value === state.source)) state.source = "committed";
  select.value = state.source;
}

async function loadResults() {
  state.results = null;
  state.validation = { endpoint: null, scenario: null };
  try {
    state.results = await api(`/api/results/${encodeURIComponent(state.source)}/${encodeURIComponent(state.benchmark)}`);
  } catch (e) {
    state.results = { error: e.message };
  }
  if (state.source !== "committed") {
    try { state.results.run = await api(`/api/runs/${encodeURIComponent(state.source)}`); } catch (e) { /* no run detail */ }
  }
  renderResults();
}

function renderResults() {
  renderOverview();
  renderValidation();
  renderInjection();
  renderFlags();
  renderOutputs();
}

// --------------------------------------------------------------- benchmarks

function renderBenchmarks() {
  const s = state.status;
  const cards = state.benchmarks.map((b) => `
    <div class="proj-card">
      <div class="proj-card-gutter">benchmarks/${esc(b.id)}.env</div>
      <div class="proj-card-body">
        <div class="proj-card-name">${esc(b.title)}</div>
        <div class="proj-card-meta">
          <span>Scenarios <span class="v">${b.scenarios}</span></span>
          <span>Oracle records <span class="v">${b.oracle_records}</span></span>
          <span>Results <span class="v">${b.has_results ? "kept in git" : "none yet"}</span></span>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary" data-pick="${esc(b.id)}">Show results</button>
          <button class="btn btn-primary" data-run="${esc(b.id)}">Run</button>
        </div>
      </div>
    </div>`).join("");

  const rows = s && s.docker ? s.containers.map((c) => `<tr><td class="mono">${esc(c.name)}</td><td>${
    c.state === "running" ? '<span class="tag tag-verified">running</span>' : `<span class="tag tag-neutral">${esc(c.state)}</span>`
  }</td><td class="mono muted-text">${esc(c.health || "—")}</td></tr>`).join("") : "";

  $("#benchmarks").innerHTML = pageHead("Setup / Benchmarks", "Benchmarks",
    "The legacy applications the engine is developed against. A benchmark is never edited; it runs unmodified in a container.") + `
    <section class="block"><div class="card-grid">${cards || '<div class="empty">No benchmark has an endpoint map yet.</div>'}</div></section>
    <section class="block">
      <div class="section-title">Environment</div>
      ${!s || !s.docker ? '<div class="panel"><div class="empty">Docker is not running. Start Docker Desktop, then refresh.</div></div>' : `
      <div class="info-list" style="margin-bottom:16px;">
        <div class="info-row"><span class="k">Running benchmark</span><span class="v">${esc(s.running || "none")}</span></div>
        <div class="info-row"><span class="k">Gateway</span><span class="v">127.0.0.1 only</span></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Container</th><th>State</th><th>Health</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="note-line">A run starts the systems, or switches them to its benchmark, and confirms they have no route out before anything is validated.</div>`}
    </section>`;

  document.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", async () => {
    await chooseBenchmark(button.dataset.pick);
    showScreen("overview");
  }));
  document.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", async () => {
    await chooseBenchmark(button.dataset.run);
    showScreen("run");
  }));
}

// ------------------------------------------------------------- pipeline run

function stepsFromEvents(events, finished) {
  const steps = Object.fromEntries(STEPS.map(([id]) => [id, { state: "pending", detail: "", time: "" }]));
  let previous = null;
  let active = events.length ? "environment" : null;
  const seconds = (at) => { const [h, m, s] = String(at || "0:0:0").split(":").map(Number); return h * 3600 + m * 60 + s; };
  for (const e of events) {
    if (e.stage === "run" && e.event === "start") previous = seconds(e.at);
    if (e.event === "done" && steps[e.stage]) {
      const step = steps[e.stage];
      step.state = "done";
      const elapsed = previous === null ? 0 : seconds(e.at) - previous;
      step.time = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
      previous = seconds(e.at);
      if (e.stage === "environment") step.detail = `${e.action} · outbound network ${e.network}`;
      if (e.stage === "scenarios") step.detail = `${e.count} ${e.action}`;
      if (e.stage === "capture") step.detail = `${e.captured} captured · ${e.kept} kept · ${e.refused.length} refused`;
      if (e.stage === "suite") step.detail = `${e.tests} tests`;
      if (e.stage === "verdict") {
        const v = Object.values(e.endpoints);
        step.detail = `${v.length} endpoints · ${count(v, "PASS")} pass · ${count(v, "FAIL")} fail · ${count(v, "INCONCLUSIVE")} inconclusive`;
      }
      const index = STEPS.findIndex(([id]) => id === e.stage);
      active = STEPS[index + 1] ? STEPS[index + 1][0] : null;
    }
    if (e.stage === "capture" && e.event === "scenario") {
      steps.capture.progress = e.number / e.of;
      steps.capture.detail = `${e.number} of ${e.of} · ${e.result} ${e.scenario_id}`;
    }
    if (e.stage === "run" && e.event === "error" && active) {
      steps[active].state = "failed";
      steps[active].detail = e.message;
      active = null;
    }
  }
  if (active && !finished) steps[active].state = "running";
  return steps;
}

function logLine(e) {
  let tag = e.stage, message = "", flag = false;
  if (e.stage === "run" && e.event === "start") message = `run started for ${e.benchmark}`;
  else if (e.stage === "run" && e.event === "done") message = "run complete · outputs written";
  else if (e.stage === "run" && e.event === "error") { message = e.message; flag = true; }
  else if (e.stage === "environment" && e.event === "done") message = `${e.action}; both systems ready; outbound network ${e.network}`;
  else if (e.stage === "environment" && e.event === "stopped") message = "systems stopped";
  else if (e.stage === "scenarios") message = `${e.count} scenarios ${e.action}`;
  else if (e.stage === "capture" && e.event === "scenario") {
    if (e.result === "kept") return "";
    message = `${e.result} ${e.scenario_id}${e.reason ? " — " + e.reason : ""}`;
    flag = e.result === "refused";
  } else if (e.stage === "capture" && e.event === "done") message = `${e.captured} captured, ${e.kept} kept, ${e.refused.length} refused`;
  else if (e.stage === "suite") message = `${e.tests} tests generated`;
  else if (e.stage === "verdict") message = Object.entries(e.endpoints).map(([endpoint, v]) => `${v} ${endpoint}`).join(" · ");
  else if (e.stage === "fault") { message = `fault ${e.fault}: ${e.event}`; flag = e.event !== "cleared"; }
  else message = e.message || JSON.stringify(e);
  return `<div class="log-line${flag ? " flag" : ""}"><span class="log-time">${esc(e.at)}</span><span class="log-tag">${esc(tag)}</span><span class="log-msg">${esc(message)}</span></div>`;
}

async function renderRun() {
  let faults = { available: [], running: null };
  try { faults = await api("/api/faults"); } catch (e) { /* no stub answering */ }
  const canPlant = faults.running === state.benchmark && faults.available.length > 0;
  const run = state.liveRun;
  const busy = run && !run.finished;
  const events = run ? run.events : [];
  const steps = stepsFromEvents(events, run ? run.finished : true);

  const pill = !run ? "" : busy
    ? '<span class="pill pill-verified"><span class="pill-dot"></span>Running</span>'
    : run.exit_code === 2 ? '<span class="pill pill-flagged"><span class="pill-dot"></span>Could not run</span>'
    : '<span class="pill pill-neutral"><span class="pill-dot"></span>Finished</span>';

  $("#run").innerHTML = pageHead("Validate / Pipeline Run", "Pipeline Run",
    run ? `${esc(run.benchmark)} · started ${esc(run.started)}${run.fault ? " · fault planted: " + esc(run.fault) : ""}` :
      "One command runs every stage: <span class=\"mono\">python -m c4 run</span>. Each run writes to its own folder; the evidence kept in git is never touched.", pill) + `
    <section class="block"><div class="panel"><div class="field-grid">
      <div class="field"><label class="field-label" for="run-benchmark">Benchmark</label>
        <select class="field-input" id="run-benchmark">${state.benchmarks.map((b) => `<option value="${esc(b.id)}"${b.id === state.benchmark ? " selected" : ""}>${esc(b.title)} (${esc(b.id)})</option>`).join("")}</select></div>
      <div class="field"><label class="field-label" for="run-fault">Plant a known fault (fault injection demo)</label>
        <select class="field-input" id="run-fault"${canPlant ? "" : " disabled"}><option value="">None: the migrated system as it is</option>${
          canPlant ? faults.available.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("") : ""}</select>
        <div class="note-line">${canPlant ? "The stub API standing in for the migrated system misbehaves in this one known way for the run, then behaves correctly again."
          : "Faults are listed once this benchmark's systems are running: run it once first."}</div></div>
      <div class="field full"><label class="check-item"><input type="checkbox" id="run-recapture"> Capture the legacy system again (a new oracle), instead of using the records kept in git</label></div>
    </div>
    <div class="btn-row" style="margin-top:20px;">
      <button class="btn btn-primary" id="run-start"${busy ? " disabled" : ""}>${busy ? "Running…" : "Run the pipeline"}</button>
      ${run && run.finished && run.exit_code !== 2 ? `<button class="btn btn-secondary" id="run-results">View this run's results →</button>` : ""}
    </div></div></section>
    <section class="block"><div class="pipeline">${STEPS.map(([id, name, what]) => {
      const step = steps[id];
      const icon = step.state === "done" ? "✓" : step.state === "failed" ? "!" : "";
      return `<div class="step ${step.state}"><div class="step-icon ${step.state}">${icon}</div><div class="step-name">${esc(name)}</div>
        <div class="step-detail">${esc(step.detail || (step.state === "pending" ? what : ""))}${
          id === "capture" && step.state === "running" && step.progress ? `<div class="progress-track"><div class="progress-fill" style="width:${Math.round(step.progress * 100)}%;"></div></div>` : ""}</div>
        <div class="step-time">${esc(step.time || "—")}</div></div>`;
    }).join("")}</div></section>
    <section class="block"><div class="section-title">Live Log</div>
      <div class="log-panel" id="log-panel">${events.map(logLine).join("") || '<div class="empty">Nothing yet. Start a run.</div>'}</div></section>`;

  const panel = $("#log-panel");
  panel.scrollTop = panel.scrollHeight;
  $("#run-benchmark").addEventListener("change", async (e) => { await chooseBenchmark(e.target.value); renderRun(); });
  $("#run-start").addEventListener("click", startRun);
  const toResults = $("#run-results");
  if (toResults) toResults.addEventListener("click", async () => {
    state.source = run.id;
    await loadRuns();
    await loadResults();
    showScreen("overview");
  });
}

async function startRun() {
  const request = {
    benchmark: $("#run-benchmark").value,
    fault: $("#run-fault").value || null,
    recapture: $("#run-recapture").checked,
  };
  let run;
  try {
    run = await api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
  } catch (e) {
    alert(e.message);
    return;
  }
  state.liveRun = { ...run, events: [] };
  watch(run.id);
}

function watch(runId) {
  renderRun();
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  let pending = false;
  source.onmessage = (message) => {
    state.liveRun.events.push(JSON.parse(message.data));
    if (!pending) { pending = true; setTimeout(() => { pending = false; if (currentScreen() === "run") renderRun(); }, 250); }
  };
  source.addEventListener("end", async () => {
    source.close();
    state.liveRun = await api(`/api/runs/${encodeURIComponent(runId)}`);
    await loadStatus();
    await loadRuns();
    if (currentScreen() === "run") renderRun();
  });
}

// ---------------------------------------------------------------- history

function renderHistory() {
  const outcome = (run) => !run.finished ? "running" : run.exit_code === 0 ? "no endpoint failed" : run.exit_code === 1 ? "an endpoint failed" : "could not run";
  const rows = state.runs.map((run) => `<tr class="clickable" data-run-id="${esc(run.id)}" data-benchmark="${esc(run.benchmark)}">
    <td class="mono">${esc(run.id)}</td><td class="mono">${esc(run.benchmark)}</td><td class="mono">${esc(run.fault || "—")}</td>
    <td class="mono muted-text">${esc(run.started)}</td><td>${run.exit_code === 1 ? '<span class="tag tag-flagged">' : run.exit_code === 0 ? '<span class="tag tag-verified">' : '<span class="tag tag-neutral">'}${esc(outcome(run))}</span></td></tr>`).join("");
  $("#history").innerHTML = pageHead("Validate / Run History", "Run History", "Every run keeps its own folder: dashboard/runs/&lt;id&gt;/. Choose one to see its results.") + `
    <div class="table-wrap"><table><thead><tr><th>Run</th><th>Benchmark</th><th>Fault planted</th><th>Started</th><th>Outcome</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="empty">No runs yet.</td></tr>'}</tbody></table></div>`;
  document.querySelectorAll("#history [data-run-id]").forEach((row) => row.addEventListener("click", async () => {
    await chooseBenchmark(row.dataset.benchmark, row.dataset.runId);
    showScreen("overview");
  }));
}

// ---------------------------------------------------------------- overview

function noResults(screen, eyebrow, title) {
  const r = state.results;
  if (r && r.verdicts) return false;
  $(screen).innerHTML = pageHead(eyebrow, title, `${esc(state.benchmark || "")} · ${esc(sourceLabel())}`) +
    `<div class="panel"><div class="empty">${r && r.error ? esc(r.error) : "No results yet for this benchmark: run the pipeline first."}</div></div>`;
  return true;
}

function allScenarios() {
  return Object.entries(state.results.verdicts.endpoints).flatMap(([endpoint, entry]) => entry.scenarios.map((s) => ({ ...s, endpoint })));
}

function renderOverview() {
  if (noResults("#overview", "Results / Overview", "Overview")) return;
  const r = state.results;
  const endpoints = Object.values(r.verdicts.endpoints).map((e) => e.verdict);
  const scenarios = allScenarios().map((s) => s.verdict);
  const probes = (r.injection && r.injection.summary) || {};
  const notCaptured = r.scenarios.filter((id) => !r.oracle_records.includes(id)).length;

  $("#overview").innerHTML = pageHead("Results / Overview", "Overview", `${esc(r.benchmark)} · ${esc(sourceLabel())}`) + `
    <section class="block"><div class="stat-card-row">
      <div class="stat-card"><span class="num mono">${r.scenarios.length}</span><span class="stat-label">Scenarios</span></div>
      <div class="stat-card"><span class="num mono">${r.oracle_records.length}</span><span class="stat-label">Oracle records</span></div>
      <div class="stat-card"><span class="num mono" style="color:var(--assisted);">${notCaptured}</span><span class="stat-label">Not captured</span></div>
      <div class="stat-card"><span class="num mono">${endpoints.length}</span><span class="stat-label">Endpoints judged</span></div>
      <div class="stat-card"><span class="num mono">${r.suite_tests}</span><span class="stat-label">Suite tests</span></div>
    </div></section>
    <section class="block"><div class="section-title">Validation verdict</div>
      <div class="verdict-strip">
        <div class="verdict-chip pass"><span class="num mono">${count(endpoints, "PASS")}</span><span>pass</span><span class="what">endpoints</span></div>
        <div class="verdict-chip fail"><span class="num mono">${count(endpoints, "FAIL")}</span><span>fail</span><span class="what">endpoints</span></div>
        <div class="verdict-chip incon"><span class="num mono">${count(endpoints, "INCONCLUSIVE")}</span><span>inconclusive</span><span class="what">endpoints</span></div>
      </div>
      <div class="verdict-strip">
        <div class="verdict-chip pass"><span class="num mono">${count(scenarios, "PASS")}</span><span>pass</span><span class="what">scenarios</span></div>
        <div class="verdict-chip fail"><span class="num mono">${count(scenarios, "FAIL")}</span><span>fail</span><span class="what">scenarios</span></div>
        <div class="verdict-chip incon"><span class="num mono">${count(scenarios, "INCONCLUSIVE")}</span><span>inconclusive</span><span class="what">scenarios</span></div>
      </div>
      <div class="note-line">INCONCLUSIVE means the only differences are in fields that cannot be judged, or nothing was left to compare. The engine never guesses PASS or FAIL.</div>
    </section>
    <section class="block"><div class="section-title">Injection probes</div>
      <div class="fields-list">${Object.entries(probes).map(([label, n]) => `${probeTag(label)}<span class="mono" style="margin-right:12px;">${n}</span>`).join("") || '<span class="muted-text">None captured.</span>'}</div>
      <div class="note-line">Probes are judged for safe handling, not for equivalence. They never change a verdict.</div>
    </section>`;
}

// -------------------------------------------------------------- validation

function legacyPages(entry) {
  return [...new Set(entry.scenarios.map((s) => s.legacy_request))];
}

function renderValidation() {
  if (noResults("#validation", "Results / Validation", "Validation · C4")) return;
  const { endpoint, scenario } = state.validation;
  if (scenario) return renderScenario(endpoint, scenario);
  if (endpoint) return renderEndpoint(endpoint);

  const endpoints = state.results.verdicts.endpoints;
  const verdicts = Object.values(endpoints).map((e) => e.verdict);
  const rows = Object.entries(endpoints).map(([id, entry]) => {
    const [method, ...path] = id.split(" ");
    const s = entry.scenarios.map((x) => x.verdict);
    return `<tr class="clickable" data-endpoint="${esc(id)}"><td class="mono">${esc(method)}</td><td class="mono">${esc(path.join(" "))}</td>
      <td class="mono muted-text">${legacyPages(entry).map(esc).join("<br>")}</td>
      <td class="mono">${count(s, "PASS")} / ${count(s, "FAIL")} / ${count(s, "INCONCLUSIVE")}</td><td>${verdictTag(entry.verdict)}</td></tr>`;
  }).join("");

  $("#validation").innerHTML = pageHead("Results / Validation", "Validation · C4",
    `Behavioural equivalence between the legacy application and the migrated one, endpoint by endpoint · ${esc(sourceLabel())}`) + `
    <section class="block"><div class="verdict-strip">
      <div class="verdict-chip pass"><span class="num mono">${count(verdicts, "PASS")}</span><span>pass</span></div>
      <div class="verdict-chip fail"><span class="num mono">${count(verdicts, "FAIL")}</span><span>fail</span></div>
      <div class="verdict-chip incon"><span class="num mono">${count(verdicts, "INCONCLUSIVE")}</span><span>inconclusive</span></div>
    </div></section>
    <section class="block"><div class="table-wrap"><table>
      <thead><tr><th style="width:80px;">Method</th><th>Endpoint</th><th>Legacy page</th><th style="width:150px;">Scenarios P / F / I</th><th style="width:120px;">Verdict</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="note-line">Choose an endpoint to see its scenarios and what was compared.</div></section>`;
  document.querySelectorAll("#validation [data-endpoint]").forEach((row) => row.addEventListener("click", () => {
    state.validation = { endpoint: row.dataset.endpoint, scenario: null };
    renderValidation();
  }));
}

function renderEndpoint(endpoint) {
  const entry = state.results.verdicts.endpoints[endpoint];
  const fields = entry.fields_compared || { by_label: [], by_value: [] };
  const rows = entry.scenarios.map((s) => `<tr class="clickable" data-scenario="${esc(s.scenario_id)}">
    <td class="mono">${esc(s.scenario_id)}</td><td class="mono muted-text">${esc(s.scenario_type)}</td>
    <td class="mono muted-text">${esc(s.legacy_request)}</td><td>${verdictTag(s.verdict)}</td></tr>`).join("");
  $("#validation").innerHTML = `<span class="crumb" id="back-endpoints">← All endpoints</span>` +
    pageHead("Results / Validation", endpoint, `${verdictTag(entry.verdict)} · ${entry.scenarios.length} scenarios · ${esc(sourceLabel())}`) + `
    <section class="block"><div class="section-title">Fields compared</div>
      <div class="two-col">
        <div class="panel"><div class="field-label" style="margin-bottom:8px;">By column</div><div class="fields-list">${
          fields.by_label.map((f) => `<span class="tag tag-verified">${esc(f)}</span>`).join("") || '<span class="muted-text">none</span>'}</div>
          <div class="note-line">Held to the same database column on both sides, which also catches two swapped fields.</div></div>
        <div class="panel"><div class="field-label" style="margin-bottom:8px;">By value only</div><div class="fields-list">${
          fields.by_value.map((f) => `<span class="tag tag-neutral">${esc(f)}</span>`).join("") || '<span class="muted-text">none</span>'}</div>
          <div class="note-line">No unambiguous column on both sides; the value must still be present.</div></div>
      </div></section>
    <section class="block"><div class="section-title">Scenarios</div><div class="table-wrap"><table>
      <thead><tr><th>Scenario</th><th>Type</th><th>Legacy page</th><th style="width:120px;">Verdict</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  $("#back-endpoints").addEventListener("click", () => { state.validation = { endpoint: null, scenario: null }; renderValidation(); });
  document.querySelectorAll("#validation [data-scenario]").forEach((row) => row.addEventListener("click", () => {
    state.validation = { endpoint, scenario: row.dataset.scenario };
    renderValidation();
  }));
}

async function renderScenario(endpoint, scenarioId) {
  const entry = state.results.verdicts.endpoints[endpoint];
  const s = entry.scenarios.find((x) => x.scenario_id === scenarioId);
  if (!s) { state.validation = { endpoint, scenario: null }; return renderEndpoint(endpoint); }
  // Where the record sits within these results' own folder, whatever folder the run wrote from.
  const oraclePath = `oracles/${state.results.benchmark}/${scenarioId}.json`;
  let record = null;
  try {
    const response = await fetch(fileUrl(oraclePath));
    if (response.ok) record = await response.json();
  } catch (e) { /* shown as missing */ }

  const comparisons = s.differences.filter((d) => d.kind === "record_not_reproduced").map((d) => {
    const legacyMarked = new Set([...Object.keys(d.values_not_found || {}), ...Object.keys(d.values_under_another_column || {})]);
    const wrong = Object.values(d.values_under_another_column || {}).map((x) => JSON.stringify(x.migrated));
    const migrated = d.closest_migrated_record;
    const migratedMarked = new Set(migrated ? Object.keys(migrated).filter((k) => wrong.includes(JSON.stringify(migrated[k]))) : []);
    return `<div class="diff-grid" style="margin-bottom:16px;">
      <div><div class="diff-col-label">Legacy — ${esc(s.legacy_request)}</div>${recordBlock(d.legacy_record, legacyMarked)}</div>
      <div><div class="diff-col-label">Migrated — closest record</div>${recordBlock(migrated, migratedMarked)}</div></div>`;
  }).join("");

  const legacy = record ? record.legacy_response : null;
  $("#validation").innerHTML = `<span class="crumb" id="back-endpoint">← ${esc(endpoint)}</span>` +
    pageHead("Results / Validation / Scenario", scenarioId, `${verdictTag(s.verdict)} · ${esc(s.scenario_type)} · legacy page ${esc(s.legacy_request)} · migrated ${esc(s.request.method)} ${esc(s.request.path)}`) + `
    ${s.differences.length ? `<section class="block"><div class="section-title">Differences</div>${s.differences.map((d) => findingBlock(d, true)).join("")}${comparisons}</section>` : ""}
    ${s.abstentions.length ? `<section class="block"><div class="section-title">Cannot be judged</div>${s.abstentions.map((d) => findingBlock(d, false)).join("")}</section>` : ""}
    ${!s.differences.length && !s.abstentions.length ? `<section class="block"><div class="panel">Everything compared agrees with what the legacy system was observed to do.</div></section>` : ""}
    <section class="block"><div class="section-title">The oracle: what the legacy system did</div>
      ${!record ? '<div class="panel"><div class="empty">The oracle record could not be read.</div></div>' : `
      <div class="info-list" style="margin-bottom:14px;">
        <div class="info-row"><span class="k">Outcome</span><span class="v">${esc(legacy.outcome)} (status ${esc(legacy.status_code)})</span></div>
        <div class="info-row"><span class="k">Records shown</span><span class="v">${legacy.records.length}</span></div>
        <div class="info-row"><span class="k">Request</span><span class="v">${esc(record.input.method)} ${esc(record.legacy_url)}</span></div>
        <div class="info-row"><span class="k">Excluded fields</span><span class="v">${record.excluded_fields.map((f) => esc(f.field)).join(", ") || "none"}</span></div>
      </div>
      <div class="two-col">
        <div><div class="diff-col-label">Records the page showed</div><div class="code-block">${esc(json(legacy.records.slice(0, 5)))}${legacy.records.length > 5 ? `\n… ${legacy.records.length - 5} more` : ""}</div></div>
        <div><div class="diff-col-label">Database change</div><div class="code-block">${esc(json(record.db_state.delta))}</div></div>
      </div>
      <div class="note-line">Oracle record: <a href="${fileUrl(oraclePath)}" target="_blank" class="mono" style="color:var(--accent);">${esc(s.oracle_record)}</a></div>`}
    </section>`;
  $("#back-endpoint").addEventListener("click", () => { state.validation = { endpoint, scenario: null }; renderValidation(); });
}

// --------------------------------------------------------------- injection

function renderInjection() {
  if (noResults("#injection", "Results / Injection Safety", "Injection Safety")) return;
  const report = state.results.injection || { probes: [], summary: {} };
  const rows = report.probes.map((p) => `<tr><td class="mono">${esc(p.scenario_id)}</td><td class="mono">${esc(p.parameter)}</td>
    <td class="mono">${esc(JSON.stringify(p.probe))}</td><td>${probeTag(p.label)}</td>
    <td class="muted-text" style="font-size:12.5px;">${p.reason ? esc(p.reason) : `legacy: ${esc(p.legacy.behaviour || p.legacy.outcome)} · migrated: ${esc((p.migrated || {}).behaviour || "—")}`}</td></tr>`).join("");
  $("#injection").innerHTML = pageHead("Results / Injection Safety", "Injection Safety",
    `What each injection probe made each system do · ${esc(sourceLabel())}`) + `
    <section class="block"><div class="fields-list">${Object.entries(report.summary).map(([label, n]) => `${probeTag(label)}<span class="mono" style="margin-right:12px;">${n}</span>`).join("") || '<span class="muted-text">No probes captured.</span>'}</div>
      <div class="note-line">A probe is judged only when the same request without it succeeded on the legacy side; otherwise it is listed as not judged, with the reason.</div></section>
    <section class="block"><div class="table-wrap"><table>
      <thead><tr><th>Scenario</th><th>Parameter</th><th>Probe</th><th>Label</th><th>Why</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="empty">No probes captured.</td></tr>'}</tbody></table></div></section>`;
}

// ------------------------------------------------------------------- flags

function renderFlags() {
  if (noResults("#flags", "Results / Flags", "Flags")) return;
  const r = state.results;
  const cards = [];
  for (const s of allScenarios()) {
    if (s.verdict === "FAIL") {
      cards.push({ kind: "differences", cls: "", title: "Differs from the legacy system", src: s.scenario_id, endpoint: s.endpoint,
        body: s.differences.flatMap(describe) });
    } else if (s.verdict === "INCONCLUSIVE") {
      cards.push({ kind: "judged", cls: "assisted", title: "Cannot be judged", src: s.scenario_id, endpoint: s.endpoint,
        body: s.abstentions.flatMap(describe) });
    }
  }
  for (const p of (r.injection || { probes: [] }).probes) {
    if (p.label === "not judged") cards.push({ kind: "injection", cls: "assisted", title: "Injection probe not judged", src: p.scenario_id, endpoint: p.endpoint_id, body: [p.reason] });
    else if (!["security improvement", "safe, as before"].includes(p.label)) {
      cards.push({ kind: "injection", cls: "", title: `Injection: ${p.label}`, src: p.scenario_id, endpoint: p.endpoint_id,
        body: [`probe ${JSON.stringify(p.probe)} on ${p.parameter}`, `legacy: ${p.legacy.behaviour}`, `migrated: ${(p.migrated || {}).behaviour}`] });
    }
  }
  // Scenarios with no oracle record, grouped by the reason the run gave, if it gave one.
  const refused = {};
  const done = r.run ? r.run.events.find((e) => e.stage === "capture" && e.event === "done") : null;
  const reasons = Object.fromEntries((done ? done.refused : []).map((x) => [x.scenario_id, x.reason]));
  for (const id of r.scenarios.filter((x) => !r.oracle_records.includes(x))) {
    const reason = reasons[id] || "no oracle record: the capture was refused, so this scenario is not judged";
    (refused[reason] = refused[reason] || []).push(id);
  }
  for (const [reason, ids] of Object.entries(refused)) {
    cards.push({ kind: "capture", cls: "assisted", title: `Not captured (${ids.length})`, src: "capture", endpoint: "", body: [reason, ids.join(", ")] });
  }

  const filters = [["all", "All"], ["differences", "Differences"], ["judged", "Cannot be judged"], ["injection", "Injection"], ["capture", "Not captured"]];
  const shown = cards.filter((c) => state.flagFilter === "all" || c.kind === state.flagFilter);
  $("#flags").innerHTML = pageHead("Results / Flags", "Flags", `${cards.length} items need a person's attention · ${esc(sourceLabel())}`) + `
    <div class="chip-row">${filters.map(([id, name]) => `<span class="chip${state.flagFilter === id ? " active" : ""}" data-filter="${id}" style="cursor:pointer;">${name} · ${
      id === "all" ? cards.length : cards.filter((c) => c.kind === id).length}</span>`).join("")}</div>
    ${shown.map((c) => `<div class="flag-card ${c.cls}"><div class="flag-card-head"><span class="flag-card-title">${esc(c.title)}</span>
      <span class="flag-card-src mono">${esc(c.src)}</span>${c.endpoint ? `<span class="flag-card-src mono">${esc(c.endpoint)}</span>` : ""}</div>
      <div class="flag-card-body">${[...new Set(c.body)].map(esc).join("<br>")}</div></div>`).join("") || '<div class="panel"><div class="empty">Nothing to flag.</div></div>'}`;
  document.querySelectorAll("#flags [data-filter]").forEach((chip) => chip.addEventListener("click", () => {
    state.flagFilter = chip.dataset.filter;
    renderFlags();
  }));
}

// ----------------------------------------------------------------- outputs

function renderOutputs() {
  if (noResults("#outputs", "Deliver / Outputs", "Outputs")) return;
  const r = state.results;
  const base = state.source === "committed" ? "" : `dashboard/runs/${state.source}/`;
  const row = (what, path, note) => `<div class="output-row"><div><div class="what">${esc(what)}</div><div class="where">${esc(base + path)}</div>${
    note ? `<div class="note-line" style="margin-top:2px;">${esc(note)}</div>` : ""}</div><a href="${fileUrl(path)}" target="_blank">Open</a></div>`;
  $("#outputs").innerHTML = pageHead("Deliver / Outputs", "Outputs", `The four outputs of the validation engine · ${esc(sourceLabel())}`) + `
    <div class="export-grid">
      <div class="panel">
        ${row("Verdicts", `reports/${r.benchmark}/verdicts.json`, "Per endpoint and scenario, with every difference and the oracle record it rests on.")}
        ${row("Injection-safety report", `reports/${r.benchmark}/injection_safety.json`, "What each probe made each system do.")}
        ${r.has_readable_report ? row("Readable differential report", `reports/${r.benchmark}/differential_report.md`, "The same findings, written for a person.") : ""}
        <div class="output-row"><div><div class="what">Generated test suite · ${r.suite_tests} tests</div><div class="where">${esc(base)}generated/${esc(r.benchmark)}/</div>
          <div class="note-line" style="margin-top:2px;">Runs on its own against the migrated system: pip install -r requirements.txt, then pytest.</div></div></div>
        ${r.suite_files.filter((name) => name === "test_equivalence.py").map((name) => `<div class="output-row" style="padding:6px 0 6px 16px;"><span class="where">${esc(name)}</span><a href="${fileUrl(`generated/${r.benchmark}/${name}`)}" target="_blank">Open</a></div>`).join("")}
      </div>
      <div class="export-list"><div class="panel" style="padding:16px;"><div class="section-title" style="font-size:13px;">From a terminal</div>
        <div class="code-block">python -m c4 run --benchmark ${esc(r.benchmark)}</div>
        <div class="note-line">The same command this dashboard runs. Exit status 0: no endpoint failed; 1: one did; 2: the pipeline could not run.</div></div></div>
    </div>`;
}

// ------------------------------------------------------------- navigation

function currentScreen() {
  const active = document.querySelector(".nav-item.active");
  return active ? active.dataset.screen : "benchmarks";
}

function showScreen(screen) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.screen === screen));
  document.querySelectorAll(".screen").forEach((section) => section.classList.toggle("active", section.id === screen));
  if (screen === "benchmarks") renderBenchmarks();
  if (screen === "run") renderRun();
  if (screen === "history") renderHistory();
  if (screen === "validation") renderValidation();
  if (location.hash !== `#${screen}`) history.replaceState(null, "", `#${screen}`);
  window.scrollTo(0, 0);
}

async function chooseBenchmark(id, source = "committed") {
  state.benchmark = id;
  state.source = source;
  $("#benchmark-select").value = id;
  await loadRuns();
  await loadResults();
}

async function start() {
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => showScreen(item.dataset.screen)));
  state.benchmarks = await api("/api/benchmarks");
  const select = $("#benchmark-select");
  select.innerHTML = state.benchmarks.map((b) => `<option value="${esc(b.id)}">${esc(b.title)} (${esc(b.id)})</option>`).join("");
  select.addEventListener("change", () => chooseBenchmark(select.value));
  $("#source-select").addEventListener("change", async (e) => { state.source = e.target.value; await loadResults(); });
  await loadStatus();
  if (state.benchmarks.length) await chooseBenchmark(state.benchmarks[0].id);
  // A run already in progress (the page was reloaded) is picked up again.
  const runs = await api("/api/runs");
  const running = runs.find((run) => !run.finished);
  if (running) { state.liveRun = { ...running, events: [] }; watch(running.id); }  // the stream replays every event
  else if (runs.length) state.liveRun = await api(`/api/runs/${encodeURIComponent(runs[0].id)}`);  // the latest run
  // A link may name a screen (#validation), a set of results (?source=<run id>), and an
  // endpoint and scenario to open (?endpoint=...&scenario=...).
  const params = new URLSearchParams(location.search);
  const linked = params.get("source");
  if (linked) {
    const run = runs.find((r) => r.id === linked);
    if (run) await chooseBenchmark(run.benchmark, linked);
  }
  if (params.get("endpoint")) state.validation = { endpoint: params.get("endpoint"), scenario: params.get("scenario") };
  const screen = location.hash.slice(1);
  showScreen(document.getElementById(screen) ? screen : "benchmarks");
  setInterval(loadStatus, 15000);
}

start();
