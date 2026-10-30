const state = {
  health: null,
  registry: null,
  budget: null,
  approvals: [],
  allApprovals: [],
  runs: [],
  targetManifest: null,
  evolutions: [],
  settings: null,
  models: null,
  projects: [],
  audit: [],
  history: [],
  historyFilter: "all",
  appPlan: null,
  langfuse: null,
  observability: null,
  obsTab: "traces",
  evalTasks: [],
  evalLanes: {},
  evalExperiments: [],
  contextMode: "none",
  authUser: null,
  runId: null,
  run: null,
  runState: {},
  events: [],
  messages: [],
  agentOutputs: [],
  artifacts: [],
  currentManifest: null,
  contract: null,
  contractRunId: null,
  contractVersions: [],
  contractCritique: null,
  lastPendingIds: null,
  approvalSubtab: "pending",
  agentStatus: {},
  flowRunId: null,
  flowDetailAgent: null,
  agentWindowId: null,
  flowShowAllConnections: false,
  flowAutoFit: false,
  flowFull: false,
  flowSideWidth: 360,
  artifactRunId: null,
  evolutionId: null,
  evolution: null,
  evolutionEvents: [],
  activeTab: "overview",
  flowZoom: 1,
  runSource: null,
  evolutionSource: null,
};

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "awaiting_approval", "awaiting_design_approval"]);
const ACTIVE_EVOLUTION_STATUSES = new Set(["running", "evaluating", "proposed", "pending_approval", "canary"]);

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const token = localStorage.getItem("uiux_token") || "";
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!response.ok) {
    if (response.status === 401 && !path.includes("/auth/login")) openLogin();
    const detail = data && (data.detail || data.message || data.raw) ? (data.detail || data.message || data.raw) : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function toast(message, type = "success") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.className = "toast hidden"; }, 4200);
}

function fmtTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return iso; }
}

function fmtNumber(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function short(text, length = 130) {
  const value = String(text || "");
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function statusClass(value) {
  return String(value || "idle").toLowerCase().replaceAll(" ", "_");
}

function agentById(agentId) {
  return (state.registry?.registry?.agents || []).find((agent) => agent.id === agentId) || null;
}

function agentLabel(agentId) {
  if (!agentId) return "System";
  if (agentId === "dictator") return "Dictator";
  const agent = agentById(agentId);
  return agent ? `${agent.codename || ""} · ${agent.title || agent.id}` : agentId;
}

function gateLabel(gate) {
  const value = String(gate || "");
  if (value === "evolution_approval") return "company improvement";
  if (value === "dictator_escalation") return "Dictator escalation";
  if (value === "human_design_approval") return "design approval";
  return value.replaceAll("_", " ");
}

function currentWork(agentId) {
  if (!state.run) return "";
  const messages = (state.messages || []).filter(
    (message) => message.from_agent === agentId || message.to_agent === agentId
  );
  const lastMessage = messages[messages.length - 1];
  if (lastMessage) {
    const sentence = lastMessage.doing || lastMessage.did || lastMessage.kind || "";
    if (sentence) return sentence;
  }
  const events = (state.events || []).filter((event) => event.agent_id === agentId && event.type === "agent.started");
  const last = events[events.length - 1];
  return last?.data?.work_label || agentById(agentId)?.work_label || "";
}

function switchTab(name) {
  state.activeTab = name;
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  const activeLabel = document.querySelector(`.tab[data-tab="${name}"] .rail-label`)?.textContent;
  if ($("topbar-tab")) $("topbar-tab").textContent = activeLabel || name;
  if (name === "flow") {
    renderFlow();
    setTimeout(() => {
      drawFlowEdges();
      if (state.flowAutoFit) fitFlow();
    }, 40);
  } else if (state.agentWindowId) {
    closeAgentWindow();
  }
  if (name === "artifacts") renderArtifactsTab();
  if (name === "history") renderHistory();
  if (name === "contract") {
    if (!state.contractRunId) state.contractRunId = state.runId || state.runs[0]?.run_id || null;
    renderContract();
    if (state.contractRunId) loadContract(state.contractRunId, false);
  }
  if (name === "target") renderTarget();
  if (name === "evolution") {
    if (!state.appPlan) loadAppPlan();
    if (!state.evalTasks.length) loadEval();
    if (!state.evolutionId) state.evolutionId = defaultEvolutionId();
    if (state.evolutionId) loadEvolution(state.evolutionId, true);
    else renderEvolution();
  }
  if (name === "settings") { renderSettings(); loadAudit(); }
  if (name === "approvals") renderApprovals();
  if (name === "observability") loadObservability();
}

// ------------------------------------------------------------------ init
async function init() {
  const params = new URLSearchParams(location.search);
  state.flowFull = location.pathname === "/flow" || params.get("view") === "flow";
  if (state.flowFull) {
    document.body.classList.add("flow-full");
    document.title = "Live Flow — UI/UX Agent Company";
    state.flowAutoFit = true;
    $("flow-fullscreen")?.classList.add("hidden");
    $("flow-native-fullscreen")?.classList.remove("hidden");
    $("flow-exit-full")?.classList.remove("hidden");
  }
  state.flowShowAllConnections = localStorage.getItem("uiux_flow_all_connections") === "1";
  updateFlowConnectionsToggle();
  initFlowResizer();
  try {
    try {
      state.authUser = await api("/api/auth/me");
    } catch (error) {
      if (String(error.message).includes("authentication")) {
        openLogin();
        return;
      }
    }
    const [health, registry, budget, pending, allApprovals, runs, target, current, evolutions, settings, projects, history] = await Promise.all([
      api("/api/health"),
      api("/api/registry"),
      api("/api/budget"),
      api("/api/approvals?status=pending"),
      api("/api/approvals?status=all"),
      api("/api/runs"),
      api("/api/target-ui"),
      api("/api/current-ui"),
      api("/api/evolve"),
      api("/api/settings"),
      api("/api/projects"),
      api("/api/history"),
    ]);
    state.health = health;
    state.registry = registry;
    state.budget = budget;
    state.approvals = pending.approvals || [];
    state.allApprovals = allApprovals.approvals || [];
    state.runs = filterProductionRuns(runs.runs || []);
    state.targetManifest = target;
    state.currentManifest = current;
    state.evolutions = evolutions.proposals || [];
    state.settings = settings;
    state.projects = projects.projects || [];
    state.history = history.history || [];
    state.langfuse = health.langfuse || null;
    await loadModels(false);
    renderAll();
    checkLangfuse(false);
    loadObservability();
    const initialTab = state.flowFull ? "flow" : location.hash.replace("#", "");
    if (initialTab) switchTab(initialTab);
    const requestedRun = params.get("run");
    const activeRun = (state.runs || []).find((run) => ACTIVE_RUN_STATUSES.has(run.status));
    const activeEvolution = (state.evolutions || []).find((proposal) => ACTIVE_EVOLUTION_STATUSES.has(proposal.status));
    if (requestedRun && (state.runs || []).some((run) => run.run_id === requestedRun)) {
      state.contextMode = "history";
      await loadRun(requestedRun, true);
    } else if (activeRun) {
      state.contextMode = "live";
      await loadRun(activeRun.run_id, true);
    } else {
      clearRunContext({ render: true });
    }
    if (activeEvolution) {
      state.contextMode = "live";
      state.evolutionId = activeEvolution.proposal_id;
      await loadEvolution(activeEvolution.proposal_id, true);
    } else {
      clearEvolutionContext({ render: true });
    }
    if (state.flowFull) setTimeout(fitFlow, 180);
    setInterval(poll, 4000);
  } catch (error) {
    $("footer-status").textContent = `init error: ${error.message}`;
    toast(`Init error: ${error.message}`, "error");
  }
}

function filterProductionRuns(runs) {
  return (runs || []).filter((run) => {
    const internal = run.state || {};
    return internal.backend !== "mock";
  });
}

async function poll() {
  try {
    const [budget, pending, allApprovals, runs, evolutions, history] = await Promise.all([
      api("/api/budget"),
      api("/api/approvals?status=pending"),
      api("/api/approvals?status=all"),
      api("/api/runs"),
      api("/api/evolve"),
      api("/api/history"),
    ]);
    state.budget = budget;
    state.approvals = pending.approvals || [];
    state.allApprovals = allApprovals.approvals || [];
    state.runs = filterProductionRuns(runs.runs || []);
    state.evolutions = evolutions.proposals || [];
    state.history = history.history || [];
    renderBudget();
    renderApprovals();
    renderRuns();
    renderEvolutions();
    renderHistory();
    renderOrgSummary();
    if (state.contextMode === "history") {
      if (state.runId) await loadRun(state.runId, false);
      if (state.evolutionId) await loadEvolution(state.evolutionId, false);
    } else {
      await syncLiveContext();
    }
  } catch (error) {
    $("footer-status").textContent = `poll error: ${error.message}`;
  }
}

function renderAll() {
  renderHealth();
  renderOrgSummary();
  renderBudget();
  renderApprovals();
  renderRuns();
  renderTarget();
  renderEvolutions();
  renderSettings();
  renderTimeline();
  renderArtifactsQuick();
  renderArtifactsTab();
  renderRunMeta();
  renderFlow();
  renderEvolution();
  renderLangfuse();
  renderObservability();
}

// ------------------------------------------------------------------ header
function renderHealth() {
  const h = state.health;
  if (!h) return;
  const b = h.budget || state.budget;
  if (b) $("budget-pill").textContent = `Budget ${b.used_pct}% · ${fmtNumber(b.remaining_tokens)} tokens left`;
  const lf = state.langfuse || h.langfuse || {};
  const lfLabel = lf.connected ? "connected" : (lf.configured || lf.enabled ? "unavailable" : "disabled");
  $("footer-status").textContent = `registry ${h.registry_version} · autonomy ${h.autonomy_level} · visual QA ${h.visual_qa_backend} · Langfuse ${lfLabel} · Roborazzi: ${h.roborezzi ? "on" : "off"}`;
}

function langfuseBase() {
  return (state.langfuse?.base_url || state.health?.langfuse?.base_url || "http://127.0.0.1:3000").replace(/\/$/, "");
}

function langfuseTraceUrl(traceId, storedUrl) {
  if (storedUrl) return storedUrl;
  if (!traceId) return null;
  const project = state.langfuse?.project_id || state.health?.langfuse?.project_id;
  return project ? `${langfuseBase()}/project/${encodeURIComponent(project)}/traces/${encodeURIComponent(traceId)}` : null;
}

function langfuseLink(traceId, storedUrl, label = "Open trace in Langfuse ↗") {
  const url = langfuseTraceUrl(traceId, storedUrl);
  if (!url) return "";
  return `<a class="btn ghost small" href="${escapeHtml(url)}" target="_blank" rel="noopener" title="${escapeHtml(traceId || "")}">${escapeHtml(label)}</a>`;
}

function renderLangfuse() {
  const lf = state.langfuse || state.health?.langfuse || {};
  const status = $("langfuse-status");
  const meta = $("langfuse-meta");
  const open = $("langfuse-open");
  if (!status || !meta) return;
  const connected = lf.connected === true;
  const configured = lf.configured !== false && lf.enabled !== false;
  const label = connected ? "connected" : (configured ? "unavailable" : "disabled");
  status.textContent = label;
  status.className = `status ${connected ? "completed" : (configured ? "failed" : "")}`;
  if (open) open.href = langfuseBase();
  const project = lf.project_id || "not configured";
  const latestExperiment = (state.evalExperiments || []).find((item) => item.langfuse_experiment_url);
  const tracedRuns = (state.runs || []).filter((run) => run.state?.langfuse_trace_id || run.langfuse_trace_id).length;
  meta.innerHTML = `
    <div><b>Base URL</b> ${escapeHtml(lf.base_url || langfuseBase())}</div>
    <div><b>Dataset</b> ${escapeHtml(lf.dataset || "uiux-golden-tasks")}</div>
    <div><b>Project</b> ${escapeHtml(project)}</div>
    <div><b>Environment</b> ${escapeHtml(lf.environment || "local")}</div>
    <div><b>Known traces</b> ${tracedRuns} ${tracedRuns ? "(from local run history)" : ""}</div>
    ${latestExperiment ? `<div><b>Latest experiment</b> <a href="${escapeHtml(latestExperiment.langfuse_experiment_url)}" target="_blank" rel="noopener">${escapeHtml(latestExperiment.langfuse_experiment_id || "open")} ↗</a></div>` : ""}
    <div><b>Last check</b> ${escapeHtml(lf.checked_at ? fmtTime(lf.checked_at) : "not checked")}</div>
    <div class="muted">${escapeHtml(lf.reason || (connected ? "Traces, scores, datasets, and experiments are streaming to Langfuse." : "Set LANGFUSE_* in .env to enable tracing."))}</div>
  `;
}

async function checkLangfuse(refresh = true, notify = true) {
  try {
    state.langfuse = await api(`/api/langfuse/health${refresh ? "?refresh=1" : ""}`);
    renderLangfuse();
    renderHealth();
    renderRuns();
    renderHistory();
    renderRunMeta();
    if (state.agentWindowId) renderAgentWindow();
    if (notify) toast(`Langfuse: ${state.langfuse.status || "unknown"}`);
  } catch (error) {
    if (notify) toast(`Langfuse health error: ${error.message}`, "error");
  }
}

// ------------------------------------------------------------- observability
function obsBase() {
  return (state.observability?.base_url || state.langfuse?.base_url || state.health?.langfuse?.base_url || "http://127.0.0.1:3000").replace(/\/$/, "");
}

function obsProject() {
  return state.observability?.project_id || state.langfuse?.project_id || state.health?.langfuse?.project_id || "";
}

function obsTraceUrl(traceId) {
  if (!traceId) return null;
  const project = obsProject();
  return project ? `${obsBase()}/project/${encodeURIComponent(project)}/traces/${encodeURIComponent(traceId)}` : null;
}

function obsExperimentUrl(experimentId) {
  if (!experimentId) return null;
  const project = obsProject();
  return project ? `${obsBase()}/project/${encodeURIComponent(project)}/experiments/results?baseline=${encodeURIComponent(experimentId)}` : null;
}

function obsDatasetUrl(datasetId) {
  if (!datasetId) return null;
  const project = obsProject();
  return project ? `${obsBase()}/project/${encodeURIComponent(project)}/datasets/${encodeURIComponent(datasetId)}` : null;
}

function obsDuration(group) {
  if (!group.firstStart || !group.lastEnd) return group.latency ? `${group.latency.toFixed(2)}s` : "—";
  const ms = new Date(group.lastEnd).getTime() - new Date(group.firstStart).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

async function loadObservability() {
  const status = $("obs-status");
  if (status) { status.textContent = "checking"; status.className = "status"; }
  try {
    const data = await api("/api/observability/overview");
    state.observability = data;
    renderObservability();
  } catch (error) {
    state.observability = { enabled: false, status: "unavailable", reason: error.message };
    renderObservability();
  }
}

function obsGroupObservations(list, key) {
  const groups = new Map();
  for (const item of list || []) {
    const id = item[key];
    if (!id) continue;
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        name: item.traceName || item.name || id,
        sessionId: item.sessionId,
        traceId: item.traceId,
        count: 0,
        latency: 0,
        cost: 0,
        latest: null,
        firstStart: null,
        lastEnd: null,
        spans: [],
      });
    }
    const group = groups.get(id);
    group.count += 1;
    group.latency += Number(item.latency || 0);
    group.cost += Number(item.totalCost || 0);
    group.latest = item.startTime || group.latest;
    if (item.startTime && (!group.firstStart || String(item.startTime) < String(group.firstStart))) group.firstStart = item.startTime;
    if (item.endTime && (!group.lastEnd || String(item.endTime) > String(group.lastEnd))) group.lastEnd = item.endTime;
    group.spans.push(item);
  }
  return Array.from(groups.values()).sort((a, b) => String(b.latest || "").localeCompare(String(a.latest || "")));
}

function renderObservability() {
  const data = state.observability;
  const status = $("obs-status");
  const open = $("obs-open");
  if (!status || !$("obs-kpis")) return;
  const connected = data?.enabled === true && data?.status !== "unavailable";
  status.textContent = connected ? "connected" : (data?.status || "unavailable");
  status.className = `status ${connected ? "completed" : "failed"}`;
  if (open) open.href = obsBase();
  const kpis = [
    ["Traces", data?.trace_count ?? 0],
    ["Sessions", data?.session_count ?? 0],
    ["Scores", data?.score_count ?? 0],
    ["Datasets", data?.dataset_count ?? 0],
    ["Experiments", data?.experiment_count ?? 0],
  ];
  $("obs-kpis").innerHTML = kpis.map(([label, value]) => `
    <div class="obs-kpi"><div class="label">${escapeHtml(label)}</div><div class="value ${connected ? "green" : ""}">${escapeHtml(value)}</div></div>
  `).join("");
  const observations = data?.recent_observations || [];
  const scores = data?.recent_scores || [];
  const traces = obsGroupObservations(observations, "traceId");
  const sessions = obsGroupObservations(observations, "sessionId");
  $("obs-trace-count").textContent = String(traces.length);
  $("obs-score-count").textContent = String(scores.length);
  if ($("obs-traces")) {
    $("obs-traces").innerHTML = !connected
      ? `<div class="muted">Langfuse unavailable: ${escapeHtml(data?.reason || "not configured")}</div>`
      : traces.length ? traces.slice(0, 60).map((trace) => `
        <div class="item clickable" data-obs-kind="trace" data-obs-id="${escapeHtml(trace.id)}">
          <div class="obs-row">
            <div><div class="name">${escapeHtml(short(trace.name, 80))}</div><div class="sub">${escapeHtml(trace.id)}</div></div>
            <div class="metric"><b>${trace.count}</b> spans</div>
            <div class="metric">${obsDuration(trace)}</div>
            <div class="metric">${trace.cost ? `$${trace.cost.toFixed(4)}` : "—"}</div>
          </div>
        </div>
      `).join("") : `<div class="muted">No traces yet. Run the company to create one.</div>`;
  }
  if ($("obs-sessions")) {
    $("obs-sessions").innerHTML = !connected
      ? `<div class="muted">Langfuse unavailable.</div>`
      : sessions.length ? sessions.slice(0, 60).map((session) => `
        <div class="item clickable" data-obs-kind="session" data-obs-id="${escapeHtml(session.id)}">
          <div class="obs-row">
            <div><div class="name">${escapeHtml(short(session.id, 70))}</div><div class="sub">${escapeHtml(session.name)}</div></div>
            <div class="metric"><b>${session.count}</b> spans</div>
            <div class="metric">${obsDuration(session)}</div>
            <div class="metric">${session.cost ? `$${session.cost.toFixed(4)}` : "—"}</div>
          </div>
        </div>
      `).join("") : `<div class="muted">No sessions yet.</div>`;
  }
  if ($("obs-scores")) {
    $("obs-scores").innerHTML = !connected
      ? `<div class="muted">Langfuse unavailable.</div>`
      : scores.length ? scores.slice(0, 80).map((score, index) => `
        <div class="item clickable" data-obs-kind="score" data-obs-index="${index}">
          <div class="obs-row">
            <div><div class="name">${escapeHtml(score.name)}</div><div class="sub">${escapeHtml(score.traceId || score.subject?.id || "trace")}</div></div>
            <div class="metric"><b>${escapeHtml(score.value)}</b></div>
            <div class="metric">${escapeHtml(score.dataType)}</div>
            <div class="metric">${escapeHtml(fmtTime(score.timestamp))}</div>
          </div>
        </div>
      `).join("") : `<div class="muted">No scores yet.</div>`;
  }
  if ($("obs-datasets")) {
    const datasets = data?.datasets || [];
    $("obs-datasets").innerHTML = !connected
      ? `<div class="muted">Langfuse unavailable.</div>`
      : datasets.length ? datasets.map((dataset) => `
        <div class="item clickable" data-obs-kind="dataset" data-obs-id="${escapeHtml(dataset.name)}">
          <div class="obs-row">
            <div><div class="name">${escapeHtml(dataset.name)}</div><div class="sub">${escapeHtml(dataset.description || dataset.id)}</div></div>
            <div class="metric">${escapeHtml(dataset.id)}</div>
            <div class="metric">${escapeHtml(fmtTime(dataset.createdAt))}</div>
            <div class="metric">${obsDatasetUrl(dataset.id) ? `<a href="${escapeHtml(obsDatasetUrl(dataset.id))}" target="_blank" rel="noopener">Open ↗</a>` : ""}</div>
          </div>
        </div>
      `).join("") : `<div class="muted">No datasets yet.</div>`;
  }
  if ($("obs-experiments")) {
    const experiments = data?.experiments || [];
    $("obs-experiments").innerHTML = !connected
      ? `<div class="muted">Langfuse unavailable.</div>`
      : experiments.length ? experiments.map((experiment) => `
        <div class="item clickable" data-obs-kind="experiment" data-obs-id="${escapeHtml(experiment.id)}">
          <div class="obs-row">
            <div><div class="name">${escapeHtml(short(experiment.name, 90))}</div><div class="sub">${escapeHtml(experiment.id)}</div></div>
            <div class="metric"><b>${escapeHtml(experiment.itemCount ?? 0)}</b> items</div>
            <div class="metric">${escapeHtml(fmtTime(experiment.startTime))}</div>
            <div class="metric">${obsExperimentUrl(experiment.id) ? `<a href="${escapeHtml(obsExperimentUrl(experiment.id))}" target="_blank" rel="noopener">Open ↗</a>` : ""}</div>
          </div>
        </div>
      `).join("") : `<div class="muted">No experiments yet.</div>`;
  }
}

async function openObsDetail(kind, id) {
  const data = state.observability || {};
  let title = "Observability detail";
  let sub = id;
  let body = "";
  let actions = `<button class="btn ghost" data-close-modal>Close</button>`;
  if (kind === "trace") {
    const observations = data.recent_observations || [];
    const spans = observations.filter((item) => item.traceId === id);
    const url = obsTraceUrl(id);
    title = spans[0]?.traceName || "Trace";
    body = `<pre>${escapeHtml(JSON.stringify({ traceId: id, observations: spans }, null, 2))}</pre>`;
    if (url) actions = `<a class="btn primary" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open trace in Langfuse ↗</a>` + actions;
  } else if (kind === "session") {
    const observations = (data.recent_observations || []).filter((item) => item.sessionId === id);
    title = `Session ${id}`;
    body = `<pre>${escapeHtml(JSON.stringify(observations, null, 2))}</pre>`;
  } else if (kind === "score") {
    const score = (data.recent_scores || [])[Number(id)];
    title = score?.name || "Score";
    body = `<pre>${escapeHtml(JSON.stringify(score, null, 2))}</pre>`;
    const url = obsTraceUrl(score?.traceId);
    if (url) actions = `<a class="btn primary" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open trace in Langfuse ↗</a>` + actions;
  } else if (kind === "dataset") {
    title = id;
    body = `<div class="muted">Loading dataset…</div>`;
    openModal(title, sub, body, actions);
    try {
      const dataset = await api(`/api/observability/datasets/${encodeURIComponent(id)}`);
      $("obs-detail")?.remove();
      const modal = $("modal");
      const pre = document.createElement("pre");
      pre.id = "obs-detail";
      pre.textContent = JSON.stringify(dataset, null, 2);
      modal?.querySelector(".modal-card")?.insertBefore(pre, modal.querySelector(".modal-actions"));
    } catch (error) {
      const modal = $("modal");
      modal?.querySelector(".modal-actions")?.insertAdjacentHTML("beforebegin", `<pre id="obs-detail">${escapeHtml(error.message)}</pre>`);
    }
    return;
  } else if (kind === "experiment") {
    title = id;
    body = `<div class="muted">Loading experiment…</div>`;
    openModal(title, sub, body, actions);
    try {
      const items = await api(`/api/observability/experiments/${encodeURIComponent(id)}/items`);
      const modal = $("modal");
      modal?.querySelector(".modal-actions")?.insertAdjacentHTML("beforebegin", `<pre id="obs-detail">${escapeHtml(JSON.stringify(items, null, 2))}</pre>`);
    } catch (error) {
      const modal = $("modal");
      modal?.querySelector(".modal-actions")?.insertAdjacentHTML("beforebegin", `<pre id="obs-detail">${escapeHtml(error.message)}</pre>`);
    }
    return;
  }
  openModal(title, sub, body, actions);
}

function renderBudget() {
  const b = state.budget;
  if (!b) return;
  const pct = Math.min(100, Number(b.used_pct || 0));
  $("budget-pct").textContent = `${b.used_pct}%`;
  $("budget-bar").style.width = `${pct}%`;
  $("budget-pill").textContent = `Budget ${b.used_pct}% · ${fmtNumber(b.remaining_tokens)} tokens left`;
  $("budget-details").innerHTML = `
    <span>Used</span><b>${fmtNumber(b.used_tokens)} / ${fmtNumber(b.monthly_total_tokens)}</b>
    <span>Estimated cost</span><b>$${Number(b.cost_usd || 0).toFixed(4)}</b>
    <span>Top agent</span><b>${escapeHtml(Object.keys(b.by_agent || {})[0] || "—")}</b>
    <span>Top model</span><b>${escapeHtml(Object.keys(b.by_model || {})[0] || "—")}</b>
  `;
  if (state.settings) state.settings.monthly_token_budget = b.monthly_total_tokens;
}

// ------------------------------------------------------------------ models
async function loadModels(force = false) {
  try {
    const data = await api(`/api/opencode/models${force ? "?refresh=1" : ""}`);
    state.models = data;
    const selected = state.settings?.model || data.default || "";
    fillModelSelect($("model-select"), selected);
    fillModelSelect($("run-model"), selected);
    fillModelSelect($("settings-model"), selected);
    if (data.error) toast(`OpenCode models: ${data.error}`, "error");
  } catch (error) {
    toast(`Could not list OpenCode models: ${error.message}`, "error");
  }
}

function fillModelSelect(select, selected) {
  if (!select) return;
  const grouped = state.models?.grouped || {};
  const all = state.models?.models || [];
  select.innerHTML = "";
  if (!all.length) {
    const option = document.createElement("option");
    option.value = selected || "";
    option.textContent = selected || "Model unavailable";
    select.appendChild(option);
    return;
  }
  for (const [provider, models] of Object.entries(grouped)) {
    const group = document.createElement("optgroup");
    group.label = provider;
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      if (model === selected) option.selected = true;
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  if (selected && !all.includes(selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = selected;
    option.selected = true;
    select.insertBefore(option, select.firstChild);
  }
  if (selected) select.value = selected;
}

async function saveSettings(payload) {
  try {
    const updated = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    state.settings = updated;
    state.health = await api("/api/health");
    state.budget = await api("/api/budget");
    if (updated.model) {
      fillModelSelect($("model-select"), updated.model);
      fillModelSelect($("run-model"), updated.model);
      fillModelSelect($("settings-model"), updated.model);
    }
    renderSettings();
    renderBudget();
    renderHealth();
    toast("Settings saved.");
    return updated;
  } catch (error) {
    toast(`Settings error: ${error.message}`, "error");
    throw error;
  }
}

// ------------------------------------------------------------------ overview
function renderRunMeta() {
  const run = state.run;
  if (!run) {
    $("run-status").textContent = "idle";
    $("run-status").className = "status";
    $("run-meta").textContent = "No run selected. Issue a decree to begin.";
    $("stop-run").disabled = true;
    $("flow-stop-run").disabled = true;
    if ($("resume-run")) $("resume-run").disabled = true;
    if ($("flow-resume-run")) $("flow-resume-run").disabled = true;
    return;
  }
  const terminal = ["completed", "failed", "stopped", "escalated"].includes(run.status);
  const resumeValues = state.runState?.values || {};
  const designEscalation = run.status === "escalated"
    && (resumeValues.critique_gate === "escalate"
      || ["critique", "human_gate", "awaiting_design_approval"].includes(resumeValues.status))
    && !resumeValues.implementation;
  const resumable = (["failed", "stopped"].includes(run.status) && Boolean((state.runState?.next || []).length))
    || designEscalation;
  $("stop-run").disabled = terminal;
  $("flow-stop-run").disabled = terminal;
  if ($("resume-run")) $("resume-run").disabled = !resumable;
  if ($("flow-resume-run")) $("flow-resume-run").disabled = !resumable;
  const values = state.runState?.values || {};
  $("run-status").textContent = run.status;
  $("run-status").className = `status ${statusClass(run.status)}`;
  const gates = [
    values.critique_gate ? `critique:${values.critique_gate}` : null,
    values.eval_gate ? `eval:${values.eval_gate}` : null,
    values.qa_gate ? `qa:${values.qa_gate}` : null,
  ].filter(Boolean).join(" · ");
  const traceId = values.langfuse_trace_id || (run.state && run.state.langfuse_trace_id);
  const traceUrl = values.langfuse_trace_url || (run.state && run.state.langfuse_trace_url);
  $("run-meta").innerHTML = `
    <b>${escapeHtml(run.run_id)}</b> · ${escapeHtml(run.mode)} · ${escapeHtml(run.status)}
    ${gates ? `<br><span class="muted">${escapeHtml(gates)}</span>` : ""}
    <br><span class="muted">${escapeHtml(short(run.prompt, 240))}</span>
    ${traceId ? `<div style="margin-top:7px">${langfuseLink(traceId, traceUrl)} <span class="muted">${escapeHtml(short(traceId, 20))}</span></div>` : ""}
  `;
}

function renderTimeline() {
  const timeline = $("timeline");
  const events = state.events || [];
  if (!events.length) {
    timeline.innerHTML = `<div class="muted">Events will stream here. Each artifact and gate is recorded.</div>`;
    return;
  }
  timeline.innerHTML = events.slice(-80).map((event) => `
    <div class="event">
      <span class="dot"></span>
      <div class="body">
        <div class="msg">${escapeHtml(event.message || event.type)}</div>
        <div class="meta">${escapeHtml(event.type)}${event.agent_id ? ` · ${escapeHtml(event.agent_id)}` : ""} · ${fmtTime(event.created_at)}</div>
      </div>
    </div>
  `).join("");
  timeline.scrollTop = timeline.scrollHeight;
}

function renderOrgSummary() {
  const registry = state.registry?.registry;
  if (!registry) { $("org-summary").innerHTML = `<div class="muted">Registry unavailable.</div>`; return; }
  const tiers = {};
  (registry.agents || []).forEach((agent) => {
    const tier = Number(agent.tier ?? 99);
    (tiers[tier] ||= []).push(agent);
  });
  const tierMeta = Object.fromEntries((registry.tiers || []).map((t) => [Number(t.id), t.name]));
  const activeCount = Object.values(state.agentStatus || {}).filter((s) => ["running", "waiting"].includes(s)).length;
  $("org-summary-tag").textContent = `${registry.agents.length} agents · ${activeCount} active`;
  $("org-summary").innerHTML = Object.entries(tiers).sort(([a], [b]) => Number(a) - Number(b)).map(([tier, agents]) => `
    <div class="tier-line">
      <div class="tier-name">T${tier} · ${escapeHtml(tierMeta[Number(tier)] || "")}</div>
      <div class="chips">
        ${agents.map((agent) => {
          const status = state.agentStatus[agent.id] || "idle";
          return `<span class="chip ${status === "running" ? "active" : ""}" title="${escapeHtml(agent.mission || "")}">${escapeHtml(agent.title || agent.id)}</span>`;
        }).join("")}
      </div>
    </div>
  `).join("");
}

function renderHistory() {
  const list = $("history-list");
  if (!list) return;
  const items = (state.history || []).filter((item) => state.historyFilter === "all" || item.kind === state.historyFilter);
  list.innerHTML = items.length ? items.map((item) => {
    const traceUrl = langfuseTraceUrl(item.langfuse_trace_id, item.langfuse_trace_url);
    return `
    <div class="item clickable" data-history-kind="${escapeHtml(item.kind)}" data-history-id="${escapeHtml(item.id)}">
      <div class="title">${escapeHtml(item.title)}</div>
      <div class="meta">
        <span class="badge ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
        <span>${item.kind === "evolution" ? "improvement" : "company"}</span>
        <span>${escapeHtml(item.mode || "")}</span>
        <span>${fmtTime(item.created_at)}</span>
        ${traceUrl ? `<a href="${escapeHtml(traceUrl)}" target="_blank" rel="noopener">Langfuse ↗</a>` : ""}
      </div>
      ${item.kind === "company" ? `<div class="history-actions">
        ${item.has_code ? `<button class="btn ghost small" data-history-undo="${escapeHtml(item.id)}" title="Undo the code changes from this run">Undo code</button>` : ""}
        <button class="btn danger small" data-history-delete="${escapeHtml(item.id)}" title="Delete this run and its saved data">Delete</button>
      </div>` : ""}
    </div>
  `;
  }).join("") : `<div class="muted">No history entries for this filter.</div>`;
  list.querySelectorAll("[data-history-id]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.target.closest("a, [data-history-undo], [data-history-delete]")) return;
      if (el.dataset.historyKind === "evolution") loadHistoryEvolution(el.dataset.historyId);
      else loadHistoryRun(el.dataset.historyId);
    });
  });
  list.querySelectorAll("[data-history-undo]").forEach((button) => {
    button.addEventListener("click", (event) => { event.stopPropagation(); openUndoCode(button.dataset.historyUndo); });
  });
  list.querySelectorAll("[data-history-delete]").forEach((button) => {
    button.addEventListener("click", (event) => { event.stopPropagation(); openDeleteRun(button.dataset.historyDelete); });
  });
}

function openUndoCode(runId) {
  openModal(
    "Undo this run's code?",
    runId,
    `<p class="muted">This removes the run's isolated worktree and branch when safe. If the code was already merged or the run used direct mode, it applies a reverse patch or a Git revert instead. The main branch is never reset.</p>
     <p class="hint">The run history and artifacts are kept; a <code>rollback.json</code> record is added.</p>`,
    `<button class="btn ghost" data-close-modal>Cancel</button><button class="btn danger" id="confirm-undo-code">Undo code</button>`,
    (modal) => {
      modal.querySelector("#confirm-undo-code").addEventListener("click", async () => {
        const button = modal.querySelector("#confirm-undo-code");
        button.disabled = true;
        button.textContent = "Undoing…";
        try {
          const result = await api(`/api/runs/${encodeURIComponent(runId)}/rollback`, {
            method: "POST",
            body: JSON.stringify({ mode: "auto", confirm: true }),
          });
          closeModal();
          toast(`Code rollback: ${result.action}`);
          await poll();
          if (state.runId === runId) await loadRun(runId, false);
        } catch (error) {
          button.disabled = false;
          button.textContent = "Undo code";
          toast(`Rollback error: ${error.message}`, "error");
        }
      });
    },
  );
}

function openDeleteRun(runId) {
  openModal(
    "Delete this run permanently?",
    runId,
    `<p class="muted">This removes the run record, approvals, events, agent outputs, token rows, saved artifacts, and its isolated worktree. It does not reset the main project branch.</p>
     <p class="hint">This cannot be undone from the dashboard.</p>`,
    `<button class="btn ghost" data-close-modal>Cancel</button><button class="btn danger" id="confirm-delete-run">Delete run</button>`,
    (modal) => {
      modal.querySelector("#confirm-delete-run").addEventListener("click", async () => {
        const button = modal.querySelector("#confirm-delete-run");
        button.disabled = true;
        button.textContent = "Deleting…";
        try {
          await api(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
          closeModal();
          toast("Run deleted.");
          if (state.runId === runId) clearRunContext({ render: true });
          await poll();
        } catch (error) {
          button.disabled = false;
          button.textContent = "Delete run";
          toast(`Delete error: ${error.message}`, "error");
        }
      });
    },
  );
}

function renderRuns() {
  const runs = state.runs || [];
  $("run-count").textContent = String(runs.length);
  const list = $("runs");
  if (!runs.length) {
    list.innerHTML = `<div class="muted">No production runs yet. Issue a decree.</div>`;
  } else {
    list.innerHTML = runs.slice(0, 12).map((run) => {
      const traceId = run.state?.langfuse_trace_id || run.langfuse_trace_id;
      const traceUrl = langfuseTraceUrl(traceId, run.state?.langfuse_trace_url || run.langfuse_trace_url);
      return `
      <div class="item clickable" data-run="${escapeHtml(run.run_id)}">
        <div class="title">${escapeHtml(short(run.prompt, 120))}</div>
        <div class="meta">
          <span class="badge ${statusClass(run.status)}">${escapeHtml(run.status)}</span>
          <span>${escapeHtml(run.mode)}</span>
          <span>${fmtTime(run.created_at)}</span>
          ${traceUrl ? `<a href="${escapeHtml(traceUrl)}" target="_blank" rel="noopener">Langfuse ↗</a>` : ""}
        </div>
      </div>
    `;
    }).join("");
    list.querySelectorAll("[data-run]").forEach((el) => {
      el.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        loadRun(el.dataset.run, true);
        switchTab("flow");
      });
    });
  }
  fillRunSelect($("flow-run-select"), state.flowRunId || state.runId);
  fillRunSelect($("artifact-run-select"), state.artifactRunId || state.runId);
}

function fillRunSelect(select, selected) {
  if (!select) return;
  const runs = state.runs || [];
  select.innerHTML = "";
  if (!runs.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No production runs";
    select.appendChild(option);
    return;
  }
  for (const run of runs) {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.status} · ${short(run.prompt, 70)}`;
    if (run.run_id === selected) option.selected = true;
    select.appendChild(option);
  }
  if (selected) select.value = selected;
}

// ------------------------------------------------------------------ live run
async function loadRun(runId, connect = true) {
  if (!runId) return;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
    state.runId = runId;
    state.run = data.run;
    state.runState = data.state || {};
    state.events = data.events || [];
    state.messages = data.messages || [];
    state.agentOutputs = data.agent_outputs || [];
    state.artifacts = data.artifacts || [];
    state.flowRunId = runId;
    state.artifactRunId = runId;
    deriveAgentStatus();
    renderRunMeta();
    renderTimeline();
    renderArtifactsQuick();
    renderArtifactsTab();
    renderFlow();
    renderOrgSummary();
    fillRunSelect($("flow-run-select"), runId);
    fillRunSelect($("artifact-run-select"), runId);
    if (state.flowFull && state.flowAutoFit) setTimeout(fitFlow, 100);
    if (connect) connectRunEvents(runId, state.events.length ? state.events[state.events.length - 1].id : 0);
  } catch (error) {
    toast(`Run load error: ${error.message}`, "error");
  }
}

function clearRunContext({ render = true } = {}) {
  if (state.runSource) state.runSource.close();
  state.runSource = null;
  state.runId = null;
  state.run = null;
  state.runState = {};
  state.events = [];
  state.messages = [];
  state.agentOutputs = [];
  state.artifacts = [];
  state.agentStatus = {};
  state.flowRunId = null;
  state.flowDetailAgent = null;
  state.agentWindowId = null;
  const agentWindow = $("agent-live-window");
  if (agentWindow) agentWindow.classList.add("hidden");
  state.artifactRunId = null;
  state.contractRunId = null;
  state.contract = null;
  state.contractVersions = [];
  state.contractCritique = null;
  if (render) {
    renderRunMeta();
    renderTimeline();
    renderArtifactsQuick();
    renderArtifactsTab();
    renderFlow();
    renderOrgSummary();
    renderContract();
  }
}

function clearEvolutionContext({ render = true } = {}) {
  if (state.evolutionSource) state.evolutionSource.close();
  state.evolutionSource = null;
  state.evolutionId = null;
  state.evolution = null;
  state.evolutionEvents = [];
  if (render) renderEvolution();
}

function clearAllLiveContext() {
  clearRunContext({ render: true });
  clearEvolutionContext({ render: true });
  state.contextMode = "none";
}

async function syncLiveContext() {
  if (state.contextMode === "history") return;
  const activeRun = (state.runs || []).find((run) => ACTIVE_RUN_STATUSES.has(run.status));
  const activeEvolution = (state.evolutions || []).find((proposal) => ACTIVE_EVOLUTION_STATUSES.has(proposal.status));

  if (state.contextMode === "none") {
    if (activeRun) {
      state.contextMode = "live";
      await loadRun(activeRun.run_id, true);
    }
    if (activeEvolution) {
      state.contextMode = "live";
      state.evolutionId = activeEvolution.proposal_id;
      await loadEvolution(activeEvolution.proposal_id, true);
    }
    return;
  }

  if (state.contextMode === "live") {
    if (state.runId) {
      const run = (state.runs || []).find((item) => item.run_id === state.runId);
      if (!run || !ACTIVE_RUN_STATUSES.has(run.status)) {
        clearRunContext({ render: true });
      } else {
        await loadRun(state.runId, false);
      }
    }
    if (state.evolutionId) {
      const proposal = (state.evolutions || []).find((item) => item.proposal_id === state.evolutionId);
      if (!proposal || !ACTIVE_EVOLUTION_STATUSES.has(proposal.status)) {
        clearEvolutionContext({ render: true });
      } else {
        await loadEvolution(state.evolutionId, false);
      }
    }
    if (!state.runId && !state.evolutionId) state.contextMode = "none";
  }
}

async function loadHistoryRun(runId) {
  state.contextMode = "history";
  state.runId = runId;
  state.flowRunId = runId;
  state.artifactRunId = runId;
  state.contractRunId = runId;
  await loadRun(runId, false);
  switchTab("flow");
  toast("Historical company run loaded.");
}

async function loadHistoryEvolution(proposalId) {
  state.contextMode = "history";
  state.evolutionId = proposalId;
  await loadEvolution(proposalId, false);
  switchTab("evolution");
  toast("Historical improvement audit loaded.");
}

function deriveAgentStatus() {
  const statuses = {};
  for (const agent of state.registry?.registry?.agents || []) {
    statuses[agent.id] = "idle";
  }
  for (const event of state.events || []) {
    if (!event.agent_id) continue;
    if (event.type === "agent.started") statuses[event.agent_id] = "running";
    if (event.type === "agent.completed") statuses[event.agent_id] = "completed";
    if (event.type === "agent.failed") statuses[event.agent_id] = "failed";
    if (event.type === "approval.required") statuses.dictator = "waiting";
  }
  const runStatus = state.run?.status;
  if (runStatus === "awaiting_approval" || runStatus === "awaiting_design_approval") {
    for (const [id, status] of Object.entries(statuses)) {
      if (status === "running") statuses[id] = "waiting";
    }
    statuses.dictator = "waiting";
  }
  if (runStatus === "completed") {
    for (const [id, status] of Object.entries(statuses)) {
      if (status === "running") statuses[id] = "completed";
    }
    statuses.dictator = "completed";
  }
  if (runStatus === "failed") {
    for (const [id, status] of Object.entries(statuses)) {
      if (status === "running") statuses[id] = "failed";
    }
    statuses.dictator = "failed";
  }
  if (runStatus === "stopped" || runStatus === "cancelled") {
    for (const [id, status] of Object.entries(statuses)) {
      if (status === "running") statuses[id] = "idle";
    }
    statuses.dictator = "idle";
  }
  if (runStatus === "escalated") {
    for (const [id, status] of Object.entries(statuses)) {
      if (status === "running") statuses[id] = "idle";
    }
    statuses.dictator = "waiting";
  }
  state.agentStatus = statuses;
}

function connectRunEvents(runId, afterId = 0) {
  if (state.runSource) state.runSource.close();
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?after_id=${afterId || 0}`);
  state.runSource = source;
  source.onmessage = (message) => {
    try { handleRunEvent(JSON.parse(message.data)); } catch { /* ignore */ }
  };
  source.onerror = () => { $("footer-status").textContent = "run event stream reconnecting…"; };
}

function handleRunEvent(event) {
  state.events.push(event);
  if (event.type === "agent.message" && event.data?.message) {
    state.messages.push(event.data.message);
    renderFlowFeed();
  }
  if (event.agent_id) {
    if (event.type === "agent.started") state.agentStatus[event.agent_id] = "running";
    if (event.type === "agent.completed") state.agentStatus[event.agent_id] = "completed";
    if (event.type === "agent.failed") state.agentStatus[event.agent_id] = "failed";
  }
  if (event.type === "approval.required") state.agentStatus.dictator = "waiting";
  if (["run.completed", "run.paused", "run.failed", "run.started", "run.resumed", "run.stopped", "run.rechecked"].includes(event.type)) {
    if (state.runId) loadRun(state.runId, false);
    poll();
  }
  renderTimeline();
  renderFlow();
  renderOrgSummary();
}

// ------------------------------------------------------------------ flow
const FLOW_ICONS = { idle: "○", running: "◌", completed: "✓", failed: "✕", waiting: "⏸" };

function renderFlow() {
  const registry = state.registry?.registry;
  const container = $("flow-nodes");
  if (!registry) { container.innerHTML = `<div class="muted">Registry unavailable.</div>`; return; }
  const agents = registry.agents || [];
  const tiers = {};
  agents.forEach((agent) => {
    const tier = Number(agent.tier ?? 99);
    (tiers[tier] ||= []).push(agent);
  });
  const tierMeta = Object.fromEntries((registry.tiers || []).map((t) => [Number(t.id), t.name]));
  const statusFor = (agentId) => {
    if (!state.run) return "idle";
    const status = state.agentStatus[agentId] || "idle";
    return ["running", "completed", "waiting", "failed"].includes(status) ? status : "idle";
  };
  const nodeHtml = (agent, extraClass = "") => {
    const status = statusFor(agent.id);
    const work = currentWork(agent.id);
    const selected = state.flowDetailAgent === agent.id ? "selected" : "";
    return `
      <div class="flow-node ${extraClass} ${selected} ${agent.enabled ? "" : "agent-off"} status-${status}" data-node-id="${escapeHtml(agent.id)}" data-agent-status="${status}" title="${escapeHtml(agent.mission || "")}">
        <span class="node-icon">${FLOW_ICONS[status] || "○"}</span>
        <div class="node-title">${escapeHtml(agent.codename || "")} · ${escapeHtml(agent.title || agent.id)}</div>
        <div class="node-sub">${escapeHtml(agent.model || "")} · ${status}</div>
        ${work ? `<div class="node-work" title="${escapeHtml(work)}">${escapeHtml(work)}</div>` : ""}
      </div>
    `;
  };
  const top = `
    <div class="flow-top">
      <div class="flow-node human ${state.flowDetailAgent === "dictator" ? "selected" : ""} status-${statusFor("dictator")}" data-node-id="dictator" data-agent-status="${statusFor("dictator")}">
        <span class="node-icon">${FLOW_ICONS[statusFor("dictator")]}</span>
        <div class="node-title">Dictator</div>
        <div class="node-sub">Human Product Owner</div>
      </div>
      <div class="flow-node orchestrator ${state.flowDetailAgent === "ceo" ? "selected" : ""} status-${statusFor("ceo")}" data-node-id="ceo" data-agent-status="${statusFor("ceo")}">
        <span class="node-icon">${FLOW_ICONS[statusFor("ceo")]}</span>
        <div class="node-title">${escapeHtml(agentById("ceo")?.codename || "A1")} · CEO</div>
        <div class="node-sub">Company operating system</div>
      </div>
      <div class="flow-node orchestrator ${state.flowDetailAgent === "chief_orchestrator" ? "selected" : ""} status-${statusFor("chief_orchestrator")}" data-node-id="chief_orchestrator" data-agent-status="${statusFor("chief_orchestrator")}">
        <span class="node-icon">${FLOW_ICONS[statusFor("chief_orchestrator")]}</span>
        <div class="node-title">${escapeHtml(agentById("chief_orchestrator")?.codename || "A2")} · Chief UI/UX Orchestrator</div>
        <div class="node-sub">Tier 0 · routing · budgets</div>
      </div>
    </div>
  `;
  const tierHtml = Object.entries(tiers).filter(([tier]) => Number(tier) > 0).sort(([a], [b]) => Number(a) - Number(b)).map(([tier, tierAgents]) => `
    <div class="flow-tier" data-tier="${tier}">
      <div class="flow-tier-title">Tier ${tier} · ${escapeHtml(tierMeta[Number(tier)] || "")}</div>
      <div class="flow-row">${tierAgents.map((agent) => nodeHtml(agent)).join("")}</div>
    </div>
  `).join("");
  container.innerHTML = top + tierHtml;
  container.querySelectorAll("[data-node-id]").forEach((el) => {
    el.addEventListener("click", () => openAgentDetail(el.dataset.nodeId));
  });
  $("flow-event-count").textContent = String((state.events || []).length);
  $("flow-canvas").style.zoom = String(state.flowZoom);
  renderFlowEvents();
  renderFlowFeed();
  if (state.agentWindowId) renderAgentWindow();
  if (state.activeTab === "flow") requestAnimationFrame(drawFlowEdges);
}

function renderFlowFeed() {
  const list = $("flow-feed");
  if (!list) return;
  const messages = state.messages || [];
  $("flow-feed-count").textContent = String(messages.length);
  list.innerHTML = messages.length ? messages.slice(-80).map((message) => `
    <div class="feed-item">
      <div class="who">${escapeHtml(agentLabel(message.from_agent))} → ${escapeHtml(agentLabel(message.to_agent))}</div>
      <div class="did">${escapeHtml(message.did || message.kind)}</div>
      ${message.doing ? `<div class="doing">${escapeHtml(message.doing)}</div>` : ""}
      ${message.needs ? `<div class="doing">Needs: ${escapeHtml(message.needs)}</div>` : ""}
      <div class="when">${escapeHtml(message.kind)} · ${fmtTime(message.created_at)}</div>
    </div>
  `).join("") : `<div class="muted">No structured feed for this historical run. New runs stream short agent handoffs, artifacts, reviews, and escalations here.</div>`;
  list.scrollTop = list.scrollHeight;
}

function renderFlowEvents() {
  const list = $("flow-event-list");
  const events = state.events || [];
  if (!events.length) {
    list.innerHTML = `<div class="muted">No events for this run yet.</div>`;
    return;
  }
  list.innerHTML = events.slice(-120).map((event) => `
    <div class="event">
      <span class="dot"></span>
      <div class="body">
        <div class="msg">${escapeHtml(event.message || event.type)}</div>
        <div class="meta">${escapeHtml(event.agent_id || "system")} · ${escapeHtml(event.type)} · ${fmtTime(event.created_at)}</div>
      </div>
    </div>
  `).join("");
  list.scrollTop = list.scrollHeight;
}

function applyFlowZoom() {
  const canvas = $("flow-canvas");
  if (!canvas) return;
  canvas.style.zoom = String(state.flowZoom);
  $("flow-zoom-reset").textContent = `${Math.round(state.flowZoom * 100)}%`;
  requestAnimationFrame(drawFlowEdges);
}

function updateFlowConnectionsToggle() {
  const button = $("flow-all-connections");
  if (!button) return;
  button.setAttribute("aria-pressed", String(state.flowShowAllConnections));
  button.classList.toggle("toggle-on", state.flowShowAllConnections);
  button.textContent = state.flowShowAllConnections ? "All links ✓" : "All links";
}

function toggleFlowConnections() {
  state.flowShowAllConnections = !state.flowShowAllConnections;
  localStorage.setItem("uiux_flow_all_connections", state.flowShowAllConnections ? "1" : "0");
  updateFlowConnectionsToggle();
  drawFlowEdges();
  if (state.agentWindowId) renderAgentWindow();
}

function fitFlow() {
  const canvas = $("flow-canvas");
  const nodes = $("flow-nodes");
  if (!canvas || !nodes || !canvas.offsetParent) return;
  const previous = state.flowZoom;
  state.flowZoom = 1;
  canvas.style.zoom = "1";
  const contentW = Math.max(nodes.offsetWidth, 1);
  const contentH = Math.max(nodes.offsetHeight, 1);
  const availableW = Math.max(canvas.clientWidth - 4, 1);
  const availableH = Math.max(canvas.clientHeight - 4, 1);
  let scale = Math.min(availableW / contentW, availableH / contentH);
  if (!state.flowFull) scale = Math.min(1, availableW / contentW);
  state.flowZoom = Math.max(0.35, Math.min(1.5, Math.round(scale * 100) / 100));
  state.flowAutoFit = true;
  applyFlowZoom();
  if (previous && state.flowZoom !== previous) requestAnimationFrame(drawFlowEdges);
}

function setFlowSideWidth(width, persist = true) {
  const layout = $("flow-layout");
  if (!layout) return;
  const available = layout.clientWidth || Math.max(window.innerWidth - 64, 640);
  const max = Math.max(280, Math.min(680, available - 380));
  const clamped = Math.max(260, Math.min(max, Number(width) || 360));
  layout.style.setProperty("--flow-side-width", `${clamped}px`);
  state.flowSideWidth = clamped;
  if (persist) localStorage.setItem("uiux_flow_side_width", String(clamped));
  requestAnimationFrame(drawFlowEdges);
}

function initFlowResizer() {
  const saved = Number(localStorage.getItem("uiux_flow_side_width") || 0);
  if (saved) setFlowSideWidth(saved, false);
  const resizer = $("flow-resizer");
  const layout = $("flow-layout");
  if (!resizer || !layout) return;
  let dragging = false;
  const stop = () => {
    dragging = false;
    resizer.classList.remove("dragging");
  };
  resizer.addEventListener("pointerdown", (event) => {
    dragging = true;
    resizer.classList.add("dragging");
    resizer.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  window.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const rect = layout.getBoundingClientRect();
    setFlowSideWidth(rect.right - event.clientX);
  });
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function openFullFlow() {
  const runId = state.flowRunId || state.runId || "";
  window.open(`/flow?run=${encodeURIComponent(runId)}`, "_blank", "noopener");
}

function drawFlowEdges() {
  const canvas = $("flow-canvas");
  const svg = $("flow-edges");
  const nodes = $("flow-nodes");
  if (!canvas || !svg || !nodes || !canvas.offsetParent) return;
  const zoom = Number(state.flowZoom) || 1;
  const width = nodes.offsetWidth;
  const height = nodes.offsetHeight;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.style.width = `${width}px`;
  svg.style.height = `${height}px`;
  const base = nodes.getBoundingClientRect();
  const box = (el) => {
    const rect = el.getBoundingClientRect();
    return {
      x: (rect.left - base.left) / zoom + rect.width / (2 * zoom),
      y: (rect.top - base.top) / zoom + rect.height / (2 * zoom),
      top: (rect.top - base.top) / zoom,
      bottom: (rect.top - base.top + rect.height) / zoom,
    };
  };
  const dict = document.querySelector('[data-node-id="dictator"]');
  const ceo = document.querySelector('[data-node-id="ceo"]');
  const orch = document.querySelector('[data-node-id="chief_orchestrator"]');
  if (!dict || !orch) return;
  const d = box(dict);
  const c = ceo ? box(ceo) : null;
  const o = box(orch);
  let paths = "";
  const edge = (a, b, opts = {}) => {
    const dy = Math.max(18, Math.abs(b.y - a.y) / 2);
    const dash = opts.dashed ? ' stroke-dasharray="4 5"' : "";
    const glow = opts.glow ? ' filter="url(#flow-glow)"' : "";
    const opacity = opts.opacity == null ? 1 : opts.opacity;
    paths += `<path d="M ${a.x} ${a.y} C ${a.x} ${a.y + dy}, ${b.x} ${b.y - dy}, ${b.x} ${b.y}" stroke="${opts.color || "rgba(168,85,247,.22)"}" stroke-width="${opts.width || 1.3}" fill="none"${dash}${glow} opacity="${opacity}"/>`;
  };

  // The command chain is always visible. The Dictator line glows.
  if (c) {
    edge({ x: d.x, y: d.bottom }, { x: c.x, y: c.top }, {
      color: "rgba(245,158,11,.92)", width: 2.4, glow: true,
    });
    edge({ x: c.x, y: c.bottom }, { x: o.x, y: o.top }, {
      color: "rgba(255,255,255,.34)", width: 1.5,
    });
  } else {
    edge({ x: d.x, y: d.bottom }, { x: o.x, y: o.top }, {
      color: "rgba(245,158,11,.92)", width: 2.4, glow: true,
    });
  }

  // A2's reporting lines are always visible and plain, never dotted.
  const tiers = [...document.querySelectorAll(".flow-tier")];
  for (const tier of tiers) {
    const rect = tier.getBoundingClientRect();
    const centerX = (rect.left - base.left) / zoom + rect.width / (2 * zoom);
    const topY = (rect.top - base.top) / zoom;
    edge({ x: o.x, y: o.bottom + 4 }, { x: centerX, y: topY - 14 }, {
      color: "rgba(168,85,247,.22)", width: 1.1,
    });
  }

  const signalColors = {
    handoff: "rgba(168,85,247,.62)",
    review: "rgba(56,189,248,.62)",
    artifact: "rgba(34,197,94,.62)",
    feedback: "rgba(255,59,48,.62)",
    escalation: "rgba(245,158,11,.72)",
    broadcast: "rgba(255,255,255,.28)",
    instruction: "rgba(245,158,11,.55)",
  };
  const nodeFor = (agentId) => document.querySelector(`[data-node-id="${CSS.escape(agentId || "")}"]`);
  const activeIds = new Set(
    Object.entries(state.agentStatus || {})
      .filter(([, status]) => status === "running")
      .map(([agentId]) => agentId)
  );
  const alwaysIds = new Set(["dictator", "ceo", "chief_orchestrator"]);
  const selected = state.flowDetailAgent;
  const allMode = Boolean(state.flowShowAllConnections);
  const pairMessages = new Map();
  for (const message of (state.messages || [])) {
    if (!message.from_agent || !message.to_agent || message.from_agent === message.to_agent) continue;
    pairMessages.set(`${message.from_agent}->${message.to_agent}`, message);
  }
  const messagesToDraw = allMode ? (state.messages || []) : [...pairMessages.values()];
  const pairFan = new Map();
  for (const message of messagesToDraw) {
    const from = nodeFor(message.from_agent);
    const to = nodeFor(message.to_agent);
    if (!from || !to || from === to) continue;
    const fromId = message.from_agent;
    const toId = message.to_agent;
    const touchesSelected = Boolean(selected && (fromId === selected || toId === selected));
    const touchesAlways = alwaysIds.has(fromId) || alwaysIds.has(toId);
    const touchesActive = activeIds.has(fromId) || activeIds.has(toId);
    if (!allMode && !touchesSelected && !touchesAlways && !touchesActive) continue;
    if (!allMode && selected && !touchesSelected && !touchesAlways && !touchesActive) continue;
    const isDictator = fromId === "dictator" || toId === "dictator";
    const isExec = fromId === "ceo" || toId === "ceo" || fromId === "chief_orchestrator" || toId === "chief_orchestrator";
    const a = box(from);
    const b = box(to);
    const pairKey = `${fromId}->${toId}`;
    const fanIndex = pairFan.get(pairKey) || 0;
    pairFan.set(pairKey, fanIndex + 1);
    const fanOffset = allMode ? ((fanIndex % 7) - 3) * 5 : 0;
    const bend = (a.x < b.x ? 1 : -1) * 24 + fanOffset;
    const dy = Math.max(24, Math.abs(b.y - a.y) / 2);
    const color = isDictator
      ? "rgba(245,158,11,.85)"
      : touchesSelected
        ? "rgba(168,85,247,.92)"
        : isExec
          ? "rgba(255,255,255,.36)"
          : (signalColors[message.kind] || signalColors.broadcast);
    const width = isDictator ? 2 : touchesSelected ? 1.8 : isExec ? 1.4 : 1;
    const dashed = !isDictator && !isExec && !touchesSelected;
    const opacity = allMode
      ? (touchesSelected || isDictator || isExec ? 0.9 : 0.42)
      : (touchesSelected || isDictator || isExec ? 1 : 0.75);
    const glow = isDictator ? ' filter="url(#flow-glow)"' : "";
    const dash = dashed ? ' stroke-dasharray="2 4"' : "";
    paths += `<path d="M ${a.x} ${a.y} C ${a.x + bend} ${a.y + dy}, ${b.x + bend} ${b.y - dy}, ${b.x} ${b.y}" stroke="${color}" stroke-width="${width}" fill="none"${dash}${glow} opacity="${opacity}"/>`;
  }
  svg.innerHTML = `
    <defs>
      <filter id="flow-glow" x="-60%" y="-60%" width="220%" height="220%">
        <feGaussianBlur stdDeviation="2.4" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    ${paths}`;
}

function openAgentDetail(agentId) {
  state.flowDetailAgent = agentId;
  state.agentWindowId = agentId;
  renderFlow();
  renderAgentWindow();
}

function closeAgentWindow() {
  state.agentWindowId = null;
  state.flowDetailAgent = null;
  const el = $("agent-live-window");
  if (el) el.classList.add("hidden");
  renderFlow();
}

function renderAgentWindow() {
  const el = $("agent-live-window");
  if (!el) return;
  const agentId = state.agentWindowId;
  if (!agentId) { el.classList.add("hidden"); return; }
  const agent = agentId === "dictator" ? null : agentById(agentId);
  if (agentId !== "dictator" && !agent) { closeAgentWindow(); return; }
  const status = agentId === "dictator"
    ? (state.agentStatus.dictator || "idle")
    : (state.agentStatus[agentId] || (agent.enabled ? "idle" : "disabled"));
  const messages = (state.messages || []).filter(
    (message) => message.from_agent === agentId || message.to_agent === agentId
  );
  const events = (state.events || []).filter((event) => event.agent_id === agentId);
  const artifacts = (state.artifacts || []).filter((artifact) => artifact.created_by === agentId);
  const outputs = (state.agentOutputs || []).filter((output) => output.agent_id === agentId);
  const latestOutput = outputs[outputs.length - 1];
  const usage = state.budget?.by_agent?.[agentId] || 0;
  const current = agentId === "dictator"
    ? (state.approvals?.length ? `${state.approvals.length} decision(s) waiting for you.` : "No decisions waiting right now.")
    : (currentWork(agentId) || agent?.mission || "No activity yet.");
  const connections = (state.flowShowAllConnections ? messages : messages.slice(-10)).reverse().map((message) => {
    const outgoing = message.from_agent === agentId;
    const other = outgoing ? message.to_agent : message.from_agent;
    const text = outgoing
      ? (message.did || message.doing || message.kind)
      : `${message.did || message.kind}${message.needs ? ` · Needs: ${message.needs}` : ""}`;
    return `<div class="agent-connection"><span class="to">${outgoing ? "→" : "←"} ${escapeHtml(agentLabel(other))}</span> · ${escapeHtml(text)}</div>`;
  }).join("");
  const liveEvents = events.slice(-8).reverse().map((event) => `
    <div class="event"><span class="dot"></span><div class="body">
      <div class="msg">${escapeHtml(event.message || event.type)}</div>
      <div class="meta">${escapeHtml(event.type)} · ${fmtTime(event.created_at)}</div>
    </div></div>`).join("");
  const artifactHtml = artifacts.slice(-8).reverse().map((artifact) => `
    <div class="item clickable" data-agent-artifact="${escapeHtml(artifact.name)}">
      <div class="title">${escapeHtml(artifact.name)}</div>
      <div class="meta">${fmtTime(artifact.created_at)}</div>
    </div>`).join("");
  $("agent-live-title").textContent = agentId === "dictator"
    ? "Dictator · Human Product Owner"
    : `${agent.codename || ""} · ${agent.title || agent.id}`;
  $("agent-live-sub").textContent = `${status} · ${agentId === "dictator" ? "final authority" : (agent.model || "agent")}`;
  const runValues = state.runState?.values || state.run?.state || {};
  const traceLink = runValues.langfuse_trace_id
    ? `${langfuseLink(runValues.langfuse_trace_id, runValues.langfuse_trace_url, "Open agent trace in Langfuse ↗")}`
    : "";
  $("agent-live-body").innerHTML = `
    <div class="kv" style="margin-bottom:8px">
      <span>Status</span><b class="badge ${statusClass(status)}">${escapeHtml(status)}</b>
      <span>Tier</span><b>${escapeHtml(agent?.tier ?? "—")}</b>
      <span>Tokens</span><b>${fmtNumber(usage)}</b>
      <span>Connections</span><b>${messages.length}</b>
    </div>
    ${traceLink ? `<div style="margin-bottom:8px">${traceLink}</div>` : ""}
    <div class="agent-now">${escapeHtml(current)}</div>
    <h3>Live activity</h3>
    <div class="timeline">${liveEvents || `<div class="muted">No events yet.</div>`}</div>
    <h3>Connections</h3>
    <div class="agent-connections">${connections || `<div class="muted">No connections yet.</div>`}</div>
    <h3>Latest agent output (${outputs.length})</h3>
    ${latestOutput ? `<pre>${escapeHtml(short(latestOutput.text || JSON.stringify(latestOutput.output || {}, null, 2), 3200))}</pre>` : `<div class="muted">No saved output yet.</div>`}
    <h3>Artifacts (${artifacts.length})</h3>
    <div class="list">${artifactHtml || `<div class="muted">No artifacts produced yet.</div>`}</div>
  `;
  el.classList.remove("hidden");
  el.querySelectorAll("[data-agent-artifact]").forEach((node) => {
    node.addEventListener("click", () => {
      closeAgentWindow();
      openArtifact(state.runId, node.dataset.agentArtifact);
    });
  });
}

// ------------------------------------------------------------------ approvals
function approvalCard(approval, compact = false) {
  const options = (approval.options || []).map((option) => `<span class="chip">${escapeHtml(option.label)}</span>`).join("");
  const overdue = approval.due_at && new Date(approval.due_at).getTime() < Date.now();
  return `
    <div class="approval-card">
      <div class="title">${escapeHtml(approval.title)}</div>
      <div class="meta">
        <span class="badge ${statusClass(approval.status)}">${escapeHtml(gateLabel(approval.gate))}</span>
        <span>${fmtTime(approval.created_at)}</span>
        ${approval.run_id ? `<span>${escapeHtml(short(approval.run_id, 28))}</span>` : ""}
      </div>
      <div class="meta">
        <span>From: <b>${escapeHtml(approval.from_codename ? `${approval.from_codename} · ${agentById(approval.from_agent)?.title || approval.from_agent}` : agentLabel(approval.from_agent))}</b></span>
        ${approval.due_at ? `<span>Due: ${fmtTime(approval.due_at)}</span>` : ""}
        ${overdue ? `<span class="badge failed">overdue</span>` : ""}
      </div>
      <div class="approval-summary">${escapeHtml(approval.summary || short(approval.message || approval.title || "", compact ? 180 : 260))}</div>
      <details class="approval-details" data-approval-id="${escapeHtml(approval.approval_id)}">
        <summary>Show details</summary>
        <div class="details-body">${escapeHtml(approval.details || approval.message || "No extra details recorded.")}</div>
      </details>
      ${options && !compact ? `<div class="meta" style="margin-top:7px">${options}</div>` : ""}
      <div class="approval-actions">
        ${approval.gate === "evolution_approval" && approval.run_id ? `<button class="btn small ghost" data-evolution-view="${escapeHtml(approval.run_id)}">View improvement</button>` : ""}
        <button class="btn small primary" data-approval="${escapeHtml(approval.approval_id)}" data-decision="approve">Approve</button>
        <button class="btn small danger" data-approval="${escapeHtml(approval.approval_id)}" data-decision="reject">Reject</button>
        <button class="btn small ghost" data-approval="${escapeHtml(approval.approval_id)}" data-decision="dictate">Dictate</button>
      </div>
    </div>
  `;
}

function renderApprovals() {
  const productionRunIds = new Set((state.runs || []).map((run) => run.run_id));
  const visible = (approval) => approval.gate === "evolution_approval" || !approval.run_id || productionRunIds.has(approval.run_id);
  const pending = (state.approvals || []).filter(visible);
  const all = (state.allApprovals || []).filter(visible);
  const historyAll = all.filter((approval) => approval.status !== "pending");
  const currentIds = new Set(pending.map((approval) => approval.approval_id));
  if (state.lastPendingIds && "Notification" in window && Notification.permission === "granted") {
    for (const approval of pending) {
      if (!state.lastPendingIds.has(approval.approval_id)) {
        new Notification("Dictator inbox", { body: `${approval.summary || approval.title}\nFrom ${approval.from_codename || approval.from_agent || "system"}` });
      }
    }
  }
  state.lastPendingIds = currentIds;
  $("approval-count").textContent = String(pending.length);
  $("approval-badge").textContent = String(pending.length);
  $("pending-approvals-count").textContent = String(pending.length);
  $("pending-approvals-tag").textContent = String(pending.length);
  $("history-approvals-count").textContent = String(historyAll.length);
  $("history-approvals-tag").textContent = String(historyAll.length);
  const openDetailIds = new Set(
    Array.from(document.querySelectorAll(".approval-details[open]")).map((el) => el.dataset.approvalId)
  );
  const quick = $("approvals-quick");
  const pendingEl = $("approvals-pending");
  const historyEl = $("approvals-history");
  if (!pending.length) {
    quick.innerHTML = `<div class="muted">No decisions waiting. The company is operating within its mandate.</div>`;
    pendingEl.innerHTML = `<div class="muted">No pending decisions.</div>`;
  } else {
    quick.innerHTML = pending.slice(0, 4).map((a) => approvalCard(a, true)).join("");
    pendingEl.innerHTML = pending.map((a) => approvalCard(a, false)).join("");
  }
  const history = historyAll.slice(0, 80);
  historyEl.innerHTML = history.length ? history.map((a) => `
    <div class="item">
      <div class="title">${escapeHtml(a.title)}</div>
      <div class="meta">
        <span class="badge ${statusClass(a.status)}">${escapeHtml(a.status)}</span>
        <span>${escapeHtml(gateLabel(a.gate))}</span>
        <span>From: ${escapeHtml(a.from_codename || agentLabel(a.from_agent))}</span>
        <span>${fmtTime(a.decided_at || a.created_at)}</span>
        ${a.decision?.notes ? `<span>${escapeHtml(short(a.decision.notes, 80))}</span>` : ""}
      </div>
      <div class="approval-summary">${escapeHtml(a.summary || short(a.message || a.title || "", 240))}</div>
      <details class="approval-details" data-approval-id="${escapeHtml(a.approval_id)}">
        <summary>Show details</summary>
        <div class="details-body">${escapeHtml(a.details || a.message || "No extra details recorded.")}</div>
      </details>
    </div>
  `).join("") : `<div class="muted">No decisions recorded yet.</div>`;
  document.querySelectorAll(".approval-details").forEach((el) => {
    if (openDetailIds.has(el.dataset.approvalId)) el.open = true;
  });
  document.querySelectorAll("[data-approval]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.decision === "dictate") openDictate(button.dataset.approval);
      else decideApproval(button.dataset.approval, button.dataset.decision);
    });
  });
  document.querySelectorAll("[data-evolution-view]").forEach((button) => {
    button.addEventListener("click", () => openEvolution(button.dataset.evolutionView));
  });
}

function switchApprovalSubtab(name) {
  state.approvalSubtab = name;
  document.querySelectorAll("[data-approval-subtab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.approvalSubtab === name);
  });
  $("approvals-pane-pending").classList.toggle("active", name === "pending");
  $("approvals-pane-history").classList.toggle("active", name === "history");
}

async function decideApproval(approvalId, decision, notes = "") {
  try {
    await api(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, notes: notes || `Dictator decision: ${decision}` }),
    });
    toast(`Decision recorded: ${decision}`);
    await poll();
    if (!state.approvals.length) switchApprovalSubtab("history");
    if (state.runId) await loadRun(state.runId, false);
    if (state.evolutionId) await loadEvolution(state.evolutionId, false);
  } catch (error) {
    toast(`Approval error: ${error.message}`, "error");
  }
}

function openDictate(approvalId) {
  const body = `
    <p class="muted">Write the Dictator's exact instruction. This is recorded with the decision and fed back into the run.</p>
    <textarea id="dictate-text" placeholder="e.g. Use a simpler layout, keep the current top bar, and fix the contrast before trying again."></textarea>
    <p class="hint">Approve continues with your instruction. Decline asks for another design pass. On a major one-way-door decision, declining without instructions will halt the run.</p>
  `;
  openModal("Dictate your answer", "Approve with instructions, or decline and ask for another pass.", body, `
    <button class="btn ghost" data-close-modal>Cancel</button>
    <button class="btn danger" id="send-dictate-decline">Decline + instructions</button>
    <button class="btn primary" id="send-dictate-approve">Approve + instructions</button>
  `, (modal) => {
    const send = async (decision) => {
      const text = modal.querySelector("#dictate-text").value.trim();
      if (!text) { toast("Write a dictate first.", "error"); return; }
      closeModal();
      await decideApproval(approvalId, decision, text);
    };
    modal.querySelector("#send-dictate-decline").addEventListener("click", () => send("reject"));
    modal.querySelector("#send-dictate-approve").addEventListener("click", () => send("override"));
  });
}

// ------------------------------------------------------------------ target
function renderTarget() {
  const manifest = state.targetManifest || {};
  const images = manifest.images || [];
  const path = state.settings?.target_ui_dir || manifest.source_path || "—";
  const status = manifest.status || "unknown";
  $("target-path").textContent = path;
  const currentCount = state.currentManifest?.images?.length || 0;
  $("target-status").innerHTML = `<span class="badge ${statusClass(status)}">${escapeHtml(status)}</span> <span class="muted">${images.length} target image(s) · ${currentCount} current-UI baseline(s) · ${escapeHtml(manifest.precedence || "tiered")} precedence</span>`;
  $("target-mini").innerHTML = `<span class="badge ${statusClass(status)}">${escapeHtml(status)}</span> <span class="muted">${images.length} image(s)</span>`;
  const thumbs = images.slice(0, 3).map((image) => `
    <div class="thumb"><img src="/api/target-ui/image?path=${encodeURIComponent(image.file)}" alt="${escapeHtml(image.screen || "target")}" onerror="this.style.display='none'" /><span>${escapeHtml(image.screen || "unknown")}</span></div>
  `).join("");
  $("target-thumbs").innerHTML = thumbs;
  $("target-images").innerHTML = images.length ? images.map((image) => `
    <div class="target-card" data-target-image="${escapeHtml(image.file)}" data-image-id="${escapeHtml(image.image_id)}">
      <img src="/api/target-ui/image?path=${encodeURIComponent(image.file)}" alt="${escapeHtml(image.screen || "target")}" onerror="this.style.display='none'" />
      <div class="info">
        <b>${escapeHtml(image.screen || "unknown")}</b>
        ${escapeHtml(image.status)} · conf ${Number(image.confidence || 0).toFixed(2)}<br>
        ${escapeHtml(short(image.file, 44))}
      </div>
    </div>
  `).join("") : `<div class="muted">No Target_ui images found. Use Browse folder in Settings or Target_ui.</div>`;
  const conflicts = [];
  for (const image of images) {
    for (const conflict of image.conflicts || []) conflicts.push({ screen: image.screen, conflict });
  }
  $("target-conflicts").innerHTML = conflicts.length ? conflicts.map((item) => `
    <div class="item"><div class="title">${escapeHtml(item.screen || "screen")}</div><div class="meta">${escapeHtml(item.conflict)}</div></div>
  `).join("") : `<div class="muted">No conflicts recorded. Confidence is stored per image.</div>`;
  document.querySelectorAll("[data-target-image]").forEach((el) => {
    el.addEventListener("click", () => openTargetAnnotator(el.dataset.targetImage, el.dataset.imageId));
  });
}

function openTargetAnnotator(file, imageId) {
  const imageRecord = (state.targetManifest?.images || []).find((image) => image.image_id === imageId);
  const annotations = JSON.parse(JSON.stringify(imageRecord?.annotations || []));
  const body = `
    <div class="annotator">
      <div class="row" style="margin-bottom:8px">
        <input id="annotation-label" placeholder="Region / component label" style="flex:1;background:rgba(0,0,0,.55);color:#fff;border:1px solid var(--border);border-radius:10px;padding:8px" />
        <button class="btn ghost small" id="annotation-clear">Clear</button>
      </div>
      <canvas id="annotation-canvas"></canvas>
      <div id="annotation-list" class="list" style="margin-top:8px"></div>
    </div>
  `;
  openModal("Annotate Target_ui reference", file, body, `
    <button class="btn ghost" data-close-modal>Cancel</button>
    <button class="btn primary" id="annotation-save">Save annotations</button>
  `, (modal) => {
    const canvas = modal.querySelector("#annotation-canvas");
    const ctx = canvas.getContext("2d");
    const img = new Image();
    let start = null;
    let draft = null;
    let scaleX = 1;
    let scaleY = 1;
    const redraw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      for (const ann of annotations) {
        ctx.strokeStyle = "rgba(168,85,247,.95)";
        ctx.lineWidth = Math.max(2, canvas.width / 400);
        ctx.strokeRect(ann.x * canvas.width, ann.y * canvas.height, ann.w * canvas.width, ann.h * canvas.height);
        ctx.fillStyle = "rgba(168,85,247,.95)";
        ctx.font = `${Math.max(12, canvas.width / 50)}px sans-serif`;
        ctx.fillText(ann.label || "region", ann.x * canvas.width + 4, ann.y * canvas.height + 18);
      }
      if (draft) {
        ctx.strokeStyle = "rgba(255,255,255,.9)";
        ctx.setLineDash([5, 4]);
        ctx.strokeRect(draft.x, draft.y, draft.w, draft.h);
        ctx.setLineDash([]);
      }
    };
    const renderList = () => {
      modal.querySelector("#annotation-list").innerHTML = annotations.length ? annotations.map((ann, index) => `
        <div class="item"><div class="title">${escapeHtml(ann.label || "region")}</div><div class="meta">x ${ann.x.toFixed(2)} y ${ann.y.toFixed(2)} w ${ann.w.toFixed(2)} h ${ann.h.toFixed(2)}</div></div>
      `).join("") : `<div class="muted">No annotations yet. Drag a rectangle on the image.</div>`;
    };
    const point = (event) => {
      const rect = canvas.getBoundingClientRect();
      return { x: (event.clientX - rect.left) * scaleX, y: (event.clientY - rect.top) * scaleY };
    };
    canvas.addEventListener("mousedown", (event) => {
      const p = point(event);
      start = p;
      draft = { x: p.x, y: p.y, w: 0, h: 0 };
    });
    canvas.addEventListener("mousemove", (event) => {
      if (!start) return;
      const p = point(event);
      draft = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
      redraw();
    });
    canvas.addEventListener("mouseup", () => {
      if (draft && draft.w > 8 && draft.h > 8) {
        annotations.push({
          x: draft.x / canvas.width,
          y: draft.y / canvas.height,
          w: draft.w / canvas.width,
          h: draft.h / canvas.height,
          label: modal.querySelector("#annotation-label").value.trim() || "region",
        });
        renderList();
      }
      start = null;
      draft = null;
      redraw();
    });
    modal.querySelector("#annotation-clear").addEventListener("click", () => {
      annotations.length = 0;
      renderList();
      redraw();
    });
    modal.querySelector("#annotation-save").addEventListener("click", async () => {
      try {
        const saved = await api("/api/target-ui/annotations", {
          method: "POST",
          body: JSON.stringify({ image_id: imageId, annotations }),
        });
        if (imageRecord) imageRecord.annotations = saved.annotations || annotations;
        state.targetManifest = await api("/api/target-ui");
        renderTarget();
        toast(`${annotations.length} annotation(s) saved.`);
        closeModal();
      } catch (error) {
        toast(`Annotation save error: ${error.message}`, "error");
      }
    });
    img.onload = () => {
      const maxWidth = 760;
      const ratio = Math.min(1, maxWidth / img.naturalWidth);
      canvas.width = Math.round(img.naturalWidth * ratio);
      canvas.height = Math.round(img.naturalHeight * ratio);
      scaleX = img.naturalWidth / canvas.width;
      scaleY = img.naturalHeight / canvas.height;
      renderList();
      redraw();
    };
    img.onerror = () => { modal.querySelector("#annotation-list").innerHTML = `<div class="muted">Could not load image.</div>`; };
    img.src = `/api/target-ui/image?path=${encodeURIComponent(file)}`;
  });
}

async function scanCurrent() {
  const path = state.settings?.current_ui_dir;
  if (!path) { toast("Set a Current UI directory first.", "error"); return; }
  try {
    state.currentManifest = await api("/api/current-ui/scan", {
      method: "POST",
      body: JSON.stringify({ path, analyze: false }),
    });
    toast(`Current UI scanned: ${state.currentManifest.images?.length || 0} image(s).`);
    renderSettings();
  } catch (error) {
    toast(`Current UI error: ${error.message}`, "error");
  }
}

async function scanTarget(analyze = false) {
  const path = state.settings?.target_ui_dir;
  if (!path) { toast("Set a Target_ui directory first.", "error"); return; }
  const button = analyze ? $("analyze-target") : $("scan-target");
  if (button) button.disabled = true;
  try {
    toast(analyze ? "Scanning and analyzing Target_ui…" : "Scanning Target_ui…");
    state.targetManifest = await api("/api/target-ui/scan", {
      method: "POST",
      body: JSON.stringify({ path, analyze }),
    });
    renderTarget();
    toast(analyze ? "Target_ui analyzed." : "Target_ui scanned.");
  } catch (error) {
    toast(`Target_ui error: ${error.message}`, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

// ------------------------------------------------------------------ artifacts
function renderArtifactsQuick() {
  const artifacts = state.artifacts || [];
  $("artifact-count").textContent = String(artifacts.length);
  $("artifacts-quick").innerHTML = artifacts.length ? artifacts.slice(0, 8).map((artifact) => `
    <div class="artifact" data-artifact="${escapeHtml(artifact.name)}">
      ${escapeHtml(artifact.name)}
      <span>${escapeHtml(artifact.created_by || "")} · ${fmtTime(artifact.created_at)}</span>
    </div>
  `).join("") : `<div class="muted">Artifacts appear as the run progresses.</div>`;
  document.querySelectorAll("#artifacts-quick [data-artifact]").forEach((el) => {
    el.addEventListener("click", () => openArtifact(state.runId, el.dataset.artifact));
  });
}

function renderArtifactsTab() {
  fillRunSelect($("artifact-run-select"), state.artifactRunId || state.runId);
  const artifacts = state.artifacts || [];
  const list = $("artifact-list");
  list.innerHTML = artifacts.length ? artifacts.map((artifact) => `
    <div class="artifact-row" data-artifact-name="${escapeHtml(artifact.name)}">
      ${escapeHtml(artifact.name)}
      <div class="meta">${escapeHtml(artifact.created_by || "")} · ${escapeHtml(artifact.content_type || "")} · ${fmtTime(artifact.created_at)}</div>
    </div>
  `).join("") : `<div class="muted">Select a run or wait for artifacts.</div>`;
  list.querySelectorAll("[data-artifact-name]").forEach((el) => {
    el.addEventListener("click", () => previewArtifact(state.artifactRunId || state.runId, el.dataset.artifactName));
  });
}

async function loadArtifactsForRun(runId) {
  if (!runId) return;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
    state.artifactRunId = runId;
    state.artifacts = data.artifacts || [];
    renderArtifactsTab();
  } catch (error) {
    toast(`Artifacts error: ${error.message}`, "error");
  }
}

function openArtifact(runId, name) {
  switchTab("artifacts");
  if (runId) state.artifactRunId = runId;
  fillRunSelect($("artifact-run-select"), state.artifactRunId);
  previewArtifact(state.artifactRunId, name);
}

async function previewArtifact(runId, name) {
  if (!runId || !name) return;
  const preview = $("artifact-preview");
  preview.innerHTML = `<div class="muted">Loading ${escapeHtml(name)}…</div>`;
  try {
    const response = await fetch(`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const contentType = response.headers.get("content-type") || "";
    if (contentType.startsWith("image/")) {
      const blob = await response.blob();
      preview.innerHTML = `<img src="${URL.createObjectURL(blob)}" alt="${escapeHtml(name)}" />`;
      return;
    }
    const text = await response.text();
    let rendered = text;
    if (contentType.includes("json") || name.endsWith(".json")) {
      try { rendered = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep text */ }
    }
    preview.innerHTML = `<h3 style="font-size:12px;margin-bottom:8px">${escapeHtml(name)}</h3><pre>${escapeHtml(rendered)}</pre>`;
  } catch (error) {
    preview.innerHTML = `<div class="muted">Could not load artifact: ${escapeHtml(error.message)}</div>`;
  }
}

// ------------------------------------------------------------------ design contract
function fillContractSelect(selected) {
  const select = $("contract-run-select");
  if (!select) return;
  const runs = state.runs || [];
  select.innerHTML = "";
  if (!runs.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No production runs";
    select.appendChild(option);
    return;
  }
  for (const run of runs) {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.status} · ${short(run.prompt, 70)}`;
    if (run.run_id === selected) option.selected = true;
    select.appendChild(option);
  }
  if (selected) select.value = selected;
}

function renderContract() {
  fillContractSelect(state.contractRunId || state.runId);
  const editor = $("contract-editor");
  if (state.contract) {
    editor.value = JSON.stringify(state.contract, null, 2);
    $("contract-status").textContent = `Run ${state.contractRunId} · ${state.contractVersions.length} version(s)`;
  } else {
    editor.value = "";
    $("contract-status").textContent = state.contractRunId ? `No contract loaded for ${state.contractRunId}` : "Select a run with a design contract.";
  }
  $("contract-versions").innerHTML = state.contractVersions.length ? state.contractVersions.map((version) => `
    <div class="item"><div class="title">${escapeHtml(version.name)}</div><div class="meta">${fmtTime(version.created_at)} · ${escapeHtml(short(version.sha256, 16))}</div></div>
  `).join("") : `<div class="muted">No versions yet.</div>`;
  const critique = $("contract-critique");
  if (state.contractCritique) {
    critique.innerHTML = `<h3 style="font-size:12px;margin-bottom:8px">Latest critique</h3><pre>${escapeHtml(JSON.stringify(state.contractCritique, null, 2))}</pre>`;
  } else {
    critique.innerHTML = `<div class="muted">No critique loaded for this contract.</div>`;
  }
}

async function loadContract(runId, notify = false) {
  if (!runId) return;
  state.contractRunId = runId;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}/design-contract`);
    state.contract = data.contract;
    state.contractVersions = data.versions || [];
    state.contractCritique = null;
    renderContract();
    try {
      const critique = await fetch(`/api/artifacts/${encodeURIComponent(runId)}/design_critique.json`);
      if (critique.ok) state.contractCritique = JSON.parse(await critique.text());
    } catch { /* ignore */ }
    renderContract();
    if (notify) toast(`Loaded contract for ${runId}.`);
  } catch (error) {
    state.contract = null;
    state.contractVersions = [];
    state.contractCritique = null;
    renderContract();
    if (notify || !String(error.message).includes("404")) toast(`Contract load error: ${error.message}`, "error");
  }
}

async function saveContract() {
  if (!state.contractRunId) { toast("Select a run first.", "error"); return; }
  try {
    const contract = JSON.parse($("contract-editor").value);
    const result = await api(`/api/runs/${encodeURIComponent(state.contractRunId)}/design-contract`, {
      method: "PUT",
      body: JSON.stringify({ contract }),
    });
    state.contract = contract;
    toast(`Design contract v${result.version} saved.`);
    await loadContract(state.contractRunId, false);
  } catch (error) {
    toast(`Contract save error: ${error.message}`, "error");
  }
}

async function recritiqueContract() {
  if (!state.contractRunId) { toast("Select a run first.", "error"); return; }
  const button = $("contract-recrtique");
  button.disabled = true;
  button.textContent = "Re-running critic…";
  try {
    const result = await api(`/api/runs/${encodeURIComponent(state.contractRunId)}/recritique`, { method: "POST" });
    state.contractCritique = result.critique || result;
    toast("Critique re-run complete.");
    await loadContract(state.contractRunId, false);
  } catch (error) {
    toast(`Re-critique error: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Re-run critique";
  }
}

// ------------------------------------------------------- app plan & evaluation
async function loadAppPlan(path = "") {
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    const data = await api(`/api/app-plan${query}`);
    state.appPlan = data;
    $("app-plan-path").value = data.relative_path || data.path || "";
    $("app-plan-content").value = data.content || "";
    $("app-plan-meta").textContent = data.exists
      ? `${data.size || 0} chars · ${data.headings?.length || 0} headings · sha ${short(data.sha256, 12)} · ${data.path}`
      : `Not found: ${data.path} — choose a path or create the plan.`;
    renderAppPlanFiles(data.candidates || []);
  } catch (error) {
    toast(`App plan load error: ${error.message}`, "error");
  }
}

function renderAppPlanFiles(files) {
  const list = $("app-plan-files");
  if (!list) return;
  const items = (files || []).filter((item) => item.exists !== false);
  list.innerHTML = items.length ? items.map((item) => `
    <div class="item clickable" data-app-plan-path="${escapeHtml(item.path)}">
      <div class="title">${escapeHtml(item.name || item.relative || item.path)}</div>
      <div class="meta">${escapeHtml(item.relative || item.path)}</div>
    </div>
  `).join("") : `<div class="muted">No plan file found yet. Enter a path and Save to create one.</div>`;
  list.querySelectorAll("[data-app-plan-path]").forEach((el) => {
    el.addEventListener("click", () => loadAppPlan(el.dataset.appPlanPath));
  });
}

async function findAppPlans() {
  try {
    const data = await api(`/api/app-plan/search?q=plan`);
    renderAppPlanFiles(data.files || []);
    toast(`${(data.files || []).length} plan file(s) found.`);
  } catch (error) {
    toast(`Plan search error: ${error.message}`, "error");
  }
}

async function saveAppPlan() {
  try {
    const data = await api("/api/app-plan", {
      method: "PUT",
      body: JSON.stringify({
        path: $("app-plan-path").value.trim() || null,
        content: $("app-plan-content").value,
      }),
    });
    state.appPlan = data;
    toast(`App plan saved: ${data.relative_path || data.path}`);
    await loadAppPlan(data.path);
  } catch (error) {
    toast(`App plan save error: ${error.message}`, "error");
  }
}

async function loadEval() {
  try {
    const [tasks, experiments] = await Promise.all([
      api("/api/evals/tasks"),
      api("/api/evals/experiments"),
    ]);
    state.evalTasks = tasks.tasks || [];
    state.evalLanes = tasks.lanes || {};
    state.evalExperiments = experiments.experiments || [];
    const select = $("eval-lane");
    if (select) {
      select.innerHTML = Object.entries(state.evalLanes).map(([id, label]) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("");
    }
    renderEvalTasks();
    renderEvalExperiments();
  } catch (error) {
    toast(`Evaluation load error: ${error.message}`, "error");
  }
}

function renderEvalTasks() {
  const list = $("eval-tasks");
  if (!list) return;
  list.innerHTML = state.evalTasks.length ? state.evalTasks.map((task) => `
    <label class="checks" style="margin:0;display:flex;gap:7px;align-items:flex-start;padding:7px;border:1px solid var(--border);border-radius:9px">
      <input type="checkbox" data-eval-task="${escapeHtml(task.id)}" checked />
      <span><b>${escapeHtml(task.title || task.id)}</b><br><span class="muted">${escapeHtml(short(task.prompt, 190))}</span></span>
    </label>
  `).join("") : `<div class="muted">No golden tasks found. Add org/golden_tasks.yaml.</div>`;
}

function renderEvalExperiments() {
  const list = $("eval-experiments");
  if (!list) return;
  list.innerHTML = state.evalExperiments.length ? state.evalExperiments.map((experiment) => {
    const metrics = experiment.metrics || {};
    const running = experiment.status === "running";
    return `<div class="item">
      <div class="title">${escapeHtml(experiment.lane)} · ${escapeHtml(experiment.status)}</div>
      <div class="meta">
        <span>${escapeHtml(experiment.experiment_id)}</span>
        <span>${escapeHtml(fmtTime(experiment.created_at))}</span>
        ${metrics.pass_rate != null ? `<span>pass ${Math.round(Number(metrics.pass_rate) * 100)}%</span>` : ""}
        ${metrics.avg_critic_score != null ? `<span>score ${Number(metrics.avg_critic_score).toFixed(2)}</span>` : ""}
        ${metrics.total_tokens ? `<span>${fmtNumber(metrics.total_tokens)} tokens</span>` : ""}
        ${running ? `<span class="badge running">running</span>` : ""}
        ${experiment.langfuse_status ? `<span class="badge">langfuse ${escapeHtml(experiment.langfuse_status)}</span>` : ""}
      </div>
      ${metrics.error ? `<div class="doing">${escapeHtml(short(metrics.error, 200))}</div>` : ""}
      ${experiment.langfuse_experiment_id ? `<div class="doing">Langfuse experiment: ${escapeHtml(experiment.langfuse_experiment_id)} ${experiment.langfuse_experiment_url ? `<a href="${escapeHtml(experiment.langfuse_experiment_url)}" target="_blank" rel="noopener">Open ↗</a>` : ""}</div>` : ""}
      ${metrics.langfuse?.reason ? `<div class="doing">${escapeHtml(short(metrics.langfuse.reason, 220))}</div>` : ""}
    </div>`;
  }).join("") : `<div class="muted">No evaluation experiments yet. Run one above.</div>`;
}

async function runEval() {
  const button = $("eval-run");
  const taskIds = Array.from(document.querySelectorAll("[data-eval-task]:checked")).map((el) => el.dataset.evalTask);
  if (!taskIds.length) { toast("Select at least one golden task.", "error"); return; }
  button.disabled = true;
  $("eval-status").textContent = "running";
  $("eval-status").className = "status running";
  try {
    const result = await api("/api/evals/run", {
      method: "POST",
      body: JSON.stringify({ lane: $("eval-lane").value, task_ids: taskIds }),
    });
    toast(`Evaluation started: ${result.experiment_id}`);
    const pollEval = async () => {
      await loadEval();
      const experiment = state.evalExperiments.find((item) => item.experiment_id === result.experiment_id);
      if (experiment && experiment.status === "running") {
        setTimeout(pollEval, 4000);
      } else {
        button.disabled = false;
        $("eval-status").textContent = experiment?.status || "idle";
        $("eval-status").className = `status ${statusClass(experiment?.status || "idle")}`;
      }
    };
    setTimeout(pollEval, 2500);
  } catch (error) {
    button.disabled = false;
    $("eval-status").textContent = "failed";
    $("eval-status").className = "status failed";
    toast(`Evaluation start error: ${error.message}`, "error");
  }
}

// ------------------------------------------------------------------ evolution
function fillEvolutionSelect(selected) {
  const select = $("evolution-select");
  if (!select) return;
  const proposals = state.evolutions || [];
  select.innerHTML = "";
  if (!proposals.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No proposals";
    select.appendChild(option);
    return;
  }
  for (const proposal of proposals) {
    const option = document.createElement("option");
    option.value = proposal.proposal_id;
    option.textContent = `${proposal.status} · ${short(evolutionTitle(proposal), 60)}`;
    if (proposal.proposal_id === selected) option.selected = true;
    select.appendChild(option);
  }
  if (selected) select.value = selected;
}

function evolutionTitle(item) {
  if (item && item.status === "stopped" && ["Evolution audit running", "Improvement audit starting"].includes(item.title)) {
    return "Improvement stopped before proposal";
  }
  return item?.title || "Improvement";
}

function defaultEvolutionId() {
  const proposals = state.evolutions || [];
  return proposals.find((p) => ACTIVE_EVOLUTION_STATUSES.has(p.status))?.proposal_id || null;
}

function renderEvolutions() {
  const list = $("evolutions-quick");
  const proposals = state.evolutions || [];
  fillEvolutionSelect(state.evolutionId);
  if (!proposals.length) {
    list.innerHTML = `<div class="muted">No proposals yet. Start an audit from the Improvement tab.</div>`;
    return;
  }
  list.innerHTML = proposals.slice(0, 8).map((item) => `
    <div class="item clickable" data-evolution="${escapeHtml(item.proposal_id)}">
      <div class="title">${escapeHtml(evolutionTitle(item))}</div>
      <div class="meta">
        <span class="badge ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
        <span>${fmtTime(item.created_at)}</span>
      </div>
    </div>
  `).join("");
  list.querySelectorAll("[data-evolution]").forEach((el) => {
    el.addEventListener("click", () => openEvolution(el.dataset.evolution));
  });
}

async function openEvolution(proposalId) {
  const proposal = (state.evolutions || []).find((item) => item.proposal_id === proposalId);
  state.contextMode = proposal && ACTIVE_EVOLUTION_STATUSES.has(proposal.status) ? "live" : "history";
  switchTab("evolution");
  state.evolutionId = proposalId;
  await loadEvolution(proposalId, true);
}

async function startEvolution() {
  const btn = $("evolution-start");
  state.contextMode = "live";
  if (btn) btn.disabled = true;
  try {
    const autoApprove = Boolean($("evolution-auto")?.checked);
    const response = await api("/api/evolve", {
      method: "POST",
      body: JSON.stringify({ focus: "quality", auto_approve: autoApprove }),
    });
    state.evolutionId = response.proposal_id;
    switchTab("evolution");
    await loadEvolution(response.proposal_id, true);
    if (autoApprove) state.contextMode = "history";
    toast(autoApprove ? "Improvement audit started; safe improvements will auto-apply." : "Improvement audit started; it will pause for your approval.");
    await poll();
  } catch (error) {
    toast(`Improvement start error: ${error.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadEvolution(proposalId, connect = false) {
  if (!proposalId) return;
  try {
    const data = await api(`/api/evolve/${encodeURIComponent(proposalId)}`);
    state.evolutionId = proposalId;
    state.evolution = data;
    state.evolutionEvents = data.events || [];
    renderEvolution();
    if (connect) connectEvolutionEvents(proposalId, state.evolutionEvents.length ? state.evolutionEvents[state.evolutionEvents.length - 1].id : 0);
  } catch (error) {
    if (String(error.message).includes("404")) {
      state.evolutionId = proposalId;
      state.evolution = { proposal: { proposal_id: proposalId, title: "Improvement audit starting", status: "evaluating" }, state: { values: {} }, events: [] };
      state.evolutionEvents = [];
      renderEvolution();
      return;
    }
    toast(`Improvement load error: ${error.message}`, "error");
  }
}

function connectEvolutionEvents(proposalId, afterId = 0) {
  if (state.evolutionSource) state.evolutionSource.close();
  const source = new EventSource(`/api/evolve/${encodeURIComponent(proposalId)}/events?after_id=${afterId || 0}`);
  state.evolutionSource = source;
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data);
      state.evolutionEvents.push(event);
      renderEvolution();
      if (["evolution.proposed", "evolution.evaluated", "evolution.applied", "evolution.stopped", "approval.required"].includes(event.type)) {
        loadEvolution(proposalId, false);
        poll();
      }
    } catch { /* ignore */ }
  };
  source.onerror = () => { $("footer-status").textContent = "improvement event stream reconnecting…"; };
}

function renderEvolution() {
  const record = state.evolution?.proposal || {};
  const proposal = record.proposal || state.evolution?.state?.values?.proposal || {};
  const evaluation = record.evaluation || state.evolution?.state?.values?.evaluation || null;
  const values = state.evolution?.state?.values || {};
  const status = record.status || values.status || "idle";
  const events = state.evolutionEvents || [];
  const has = (type) => events.some((event) => event.type === type);
  const pending = Boolean(state.evolution?.pending) || (state.evolution?.state?.interrupts || []).length > 0 || (state.evolution?.state?.__interrupt__ || []).length > 0;
  const terminal = ["applied", "rejected", "stopped", "promoted", "canary_failed", "rolled_back"].includes(status);
  const stopped = ["stopped", "rejected"].includes(status);
  const auditorModel = events.find((event) => event.type === "agent.started" && event.agent_id === "org_evolution_auditor")?.data?.model;
  $("evolution-status").textContent = status;
  $("evolution-status").className = `status ${statusClass(status)}`;
  $("evolution-stop").disabled = !state.evolutionId || terminal;
  fillEvolutionSelect(state.evolutionId);
  $("evolution-event-count").textContent = String(events.length);
  const stageData = [
    {
      id: "observe",
      label: "Observe",
      name: "Company Observer",
      status: has("evolution.observed") ? "completed" : (stopped ? "stopped" : (has("evolution.started") ? "running" : "idle")),
      output: "Reads traces, rework, cost, gate failures, and budget pressure.",
    },
    {
      id: "diagnose",
      label: "Diagnose",
      name: "Company Improvement Auditor",
      status: has("evolution.proposed") ? "completed" : (stopped ? "stopped" : (has("agent.started") ? "running" : "idle")),
      output: `${auditorModel ? `${auditorModel} · ` : ""}${proposal.title || (stopped ? "Stopped before a proposal was produced." : "Waiting for the independent auditor.")}`,
    },
    {
      id: "evaluate",
      label: "Evaluate",
      name: "Eval Red Team",
      status: has("evolution.evaluated") ? "completed" : (stopped ? "stopped" : (has("evolution.proposed") ? "running" : "idle")),
      output: evaluation ? `${evaluation.verdict} · ${evaluation.shadow_eval || "shadow eval"}` : (stopped ? "Not evaluated." : "Waiting for evaluation."),
    },
    {
      id: "approval",
      label: "Approve",
      name: "Dictator",
      status: pending ? "waiting" : (record.decision ? "completed" : "idle"),
      output: record.decision ? JSON.stringify(record.decision) : "Approve, reject, or dictate.",
    },
    {
      id: "apply",
      label: "Apply",
      name: "Registry Service",
      status: has("evolution.applied") || status === "applied" ? "completed" : "idle",
      output: record.applied_version ? `Registry ${record.applied_version}` : "No registry change yet.",
    },
    {
      id: "canary",
      label: "Pilot rollout",
      name: "Pilot run",
      status: record.proposal?.canary?.verdict === "pass" || status === "promoted"
        ? "completed"
        : (record.proposal?.canary?.verdict === "fail" ? "failed" : (["applied", "approved"].includes(status) ? "running" : "idle")),
      output: record.proposal?.canary
        ? `${record.proposal.canary.verdict} · ${record.proposal.canary.completed}/${record.proposal.canary.runs_observed} completed · ${record.proposal.canary.scope}`
        : (status === "rolled_back" ? "Rolled back." : "Run a small-scope metric canary, then promote or roll back."),
    },
  ];
  $("evolution-cards").innerHTML = stageData.map((stage) => `
    <div class="evolution-card ${statusClass(stage.status)}" data-evolution-stage="${escapeHtml(stage.id)}">
      <div class="stage">${escapeHtml(stage.label)}</div>
      <div class="name">${escapeHtml(stage.name)}</div>
      <div class="output">${escapeHtml(short(stage.output, 110))}</div>
    </div>
  `).join("");
  document.querySelectorAll("[data-evolution-stage]").forEach((el) => {
    el.addEventListener("click", () => openEvolutionOutput(el.dataset.evolutionStage, stageData));
  });
  const proposalEl = $("evolution-proposal");
  if (!state.evolutionId) {
    proposalEl.innerHTML = `<div class="muted">No improvement proposal selected. Start an audit or pick one from the Overview.</div>`;
  } else {
    proposalEl.innerHTML = `
      <div class="card-head"><h2>${escapeHtml(evolutionTitle({ title: proposal.title, status }))}</h2><span class="badge ${statusClass(status)}">${escapeHtml(status)}</span></div>
      <p class="muted">${escapeHtml(proposal.problem || "Waiting for the auditor to produce a proposal.")}</p>
      ${pending ? `<div class="proposal-block"><h3>Waiting for Dictator approval</h3><p>This is the human gate, not an error. Approve applies safe registry changes; Reject discards the proposal; Dictate approves with your written rationale. No change is applied until you decide.</p></div>` : ""}
      <div class="proposal-block">
        <h3>Changes</h3>
        <pre>${escapeHtml(JSON.stringify(proposal.changes || [], null, 2))}</pre>
      </div>
      <div class="proposal-block">
        <h3>Expected impact</h3>
        <pre>${escapeHtml(JSON.stringify(proposal.expected_impact || {}, null, 2))}</pre>
      </div>
      <div class="proposal-block">
        <h3>Risk & rollback</h3>
        <p>${escapeHtml((proposal.risks || []).join(" · ") || "No risks recorded.")}</p>
        <p>${escapeHtml(proposal.rollback_plan || "No rollback plan yet.")}</p>
      </div>
      ${evaluation ? `<div class="proposal-block"><h3>Evaluation</h3><pre>${escapeHtml(JSON.stringify(evaluation, null, 2))}</pre></div>` : ""}
      <div class="approval-actions">
        ${pending || status === "pending_approval" ? `
          <button class="btn primary small" id="evolution-approve">Approve</button>
          <button class="btn danger small" id="evolution-reject">Reject</button>
          <button class="btn ghost small" id="evolution-dictate">Dictate</button>
        ` : ""}
        ${["applied", "approved"].includes(status) ? `<button class="btn primary small" id="evolution-canary">Start pilot</button><button class="btn danger small" id="evolution-rollback">Undo registry change</button>` : ""}
        ${["promoted", "canary"].includes(status) ? `<button class="btn ghost small" id="evolution-promote">Adopt improvement</button><button class="btn danger small" id="evolution-rollback">Undo registry change</button>` : ""}
        ${status === "canary_failed" ? `<button class="btn danger small" id="evolution-rollback">Undo registry change</button>` : ""}
        <button class="btn ghost small" id="evolution-stop-inline" ${terminal ? "disabled" : ""}>Stop</button>
      </div>
    `;
    $("evolution-approve")?.addEventListener("click", () => decideEvolution("approve"));
    $("evolution-reject")?.addEventListener("click", () => decideEvolution("reject"));
    $("evolution-dictate")?.addEventListener("click", openEvolutionDictate);
    $("evolution-canary")?.addEventListener("click", runCanary);
    $("evolution-promote")?.addEventListener("click", promoteEvolution);
    $("evolution-rollback")?.addEventListener("click", rollbackEvolution);
    $("evolution-stop-inline")?.addEventListener("click", stopEvolution);
  }
  const eventList = $("evolution-events");
  eventList.innerHTML = events.length ? events.slice(-100).map((event) => `
    <div class="event">
      <span class="dot"></span>
      <div class="body">
        <div class="msg">${escapeHtml(event.message || event.type)}</div>
        <div class="meta">${escapeHtml(event.agent_id || "system")} · ${escapeHtml(event.type)} · ${fmtTime(event.created_at)}</div>
      </div>
    </div>
  `).join("") : `<div class="muted">Improvement events will stream here.</div>`;
  eventList.scrollTop = eventList.scrollHeight;
}

function openEvolutionOutput(stageId, stageData) {
  const stage = stageData.find((item) => item.id === stageId);
  const events = state.evolutionEvents || [];
  const relevant = events.filter((event) => {
    if (stageId === "observe") return event.type === "evolution.observed" || event.type === "evolution.started";
    if (stageId === "diagnose") return event.type === "evolution.proposed" || event.agent_id === "org_evolution_auditor";
    if (stageId === "evaluate") return event.type === "evolution.evaluated";
    if (stageId === "approval") return event.type === "approval.required" || event.type === "evolution.decided";
    if (stageId === "apply") return event.type === "evolution.applied";
    if (stageId === "canary") return event.type === "evolution.canary" || event.type === "evolution.promoted" || event.type === "evolution.rolled_back";
    return false;
  });
  const body = `
    <p class="muted">${escapeHtml(stage?.output || "")}</p>
    <h3 style="font-size:12px;margin-top:12px">Agent events</h3>
    <pre>${escapeHtml(JSON.stringify(relevant, null, 2))}</pre>
    <h3 style="font-size:12px;margin-top:12px">Proposal</h3>
    <pre>${escapeHtml(JSON.stringify(state.evolution?.proposal?.proposal || {}, null, 2))}</pre>
  `;
  openModal(stage?.name || "Improvement stage", stage?.label || "", body);
}

async function runCanary() {
  if (!state.evolutionId) return;
  try {
    const result = await api(`/api/evolve/${encodeURIComponent(state.evolutionId)}/canary`, {
      method: "POST",
      body: JSON.stringify({ scope: "one_surface", runs: 5 }),
    });
    toast(`Canary ${result.verdict}: ${result.completed}/${result.runs_observed} completed.`);
    await loadEvolution(state.evolutionId, false);
    await poll();
  } catch (error) {
    toast(`Canary error: ${error.message}`, "error");
  }
}

async function promoteEvolution() {
  if (!state.evolutionId) return;
  try {
    await api(`/api/evolve/${encodeURIComponent(state.evolutionId)}/promote`, { method: "POST" });
    toast("Improvement adopted.");
    await loadEvolution(state.evolutionId, false);
    await poll();
  } catch (error) {
    toast(`Promote error: ${error.message}`, "error");
  }
}

async function rollbackEvolution() {
  if (!state.evolutionId) return;
  try {
    const result = await api(`/api/evolve/${encodeURIComponent(state.evolutionId)}/rollback`, { method: "POST" });
    toast(`Registry rolled back to ${result.rolled_back_to}.`);
    await loadEvolution(state.evolutionId, false);
    await poll();
  } catch (error) {
    toast(`Rollback error: ${error.message}`, "error");
  }
}

async function stopEvolution() {
  if (!state.evolutionId) return;
  try {
    await api(`/api/evolve/${encodeURIComponent(state.evolutionId)}/stop`, { method: "POST" });
    toast("Improvement audit stopped.");
    await loadEvolution(state.evolutionId, false);
    await poll();
  } catch (error) {
    toast(`Stop error: ${error.message}`, "error");
  }
}

async function decideEvolution(decision, notes = "") {
  if (!state.evolutionId) return;
  try {
    await api(`/api/evolve/${encodeURIComponent(state.evolutionId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ decision, notes: notes || `Dictator decision: ${decision}` }),
    });
    toast(`Improvement decision: ${decision}`);
    await loadEvolution(state.evolutionId, false);
    await poll();
  } catch (error) {
    toast(`Improvement decision error: ${error.message}`, "error");
  }
}

function openEvolutionDictate() {
  const body = `<p class="muted">Write the exact Dictator decision. It is recorded as an override with your rationale.</p><textarea id="evolution-dictate-text" placeholder="e.g. Approve only the content-design gate; do not change the model routing yet."></textarea>`;
  openModal("Dictate evolution decision", "The auditor cannot approve its own proposal.", body, `
    <button class="btn ghost" data-close-modal>Cancel</button>
    <button class="btn primary" id="evolution-send-dictate">Send dictate</button>
  `, (modal) => {
    modal.querySelector("#evolution-send-dictate").addEventListener("click", async () => {
      const text = modal.querySelector("#evolution-dictate-text").value.trim();
      if (!text) { toast("Write a dictate first.", "error"); return; }
      closeModal();
      await decideEvolution("override", text);
    });
  });
}

// ------------------------------------------------------------------ settings
async function loadAudit() {
  try {
    state.audit = (await api("/api/audit?limit=200")).audit || [];
    const list = $("settings-audit");
    if (!list) return;
    list.innerHTML = state.audit.length ? state.audit.map((entry) => `
      <div class="item"><div class="title">${escapeHtml(entry.action)}</div><div class="meta"><span>${escapeHtml(entry.user_name)}</span><span>${escapeHtml(entry.role)}</span><span>${fmtTime(entry.created_at)}</span></div></div>
    `).join("") : `<div class="muted">No audit entries.</div>`;
  } catch { /* auth may hide it */ }
}

function renderSettings() {
  const s = state.settings;
  if (!s) return;
  const projectSelect = $("project-select");
  projectSelect.innerHTML = (state.projects || []).map((project) => `<option value="${escapeHtml(project.id)}" ${project.id === s.project_id ? "selected" : ""}>${escapeHtml(project.name)} · ${escapeHtml(project.id)}</option>`).join("");
  $("settings-project-dir").textContent = s.project_dir || "—";
  $("settings-target-dir").textContent = s.target_ui_dir || "—";
  $("settings-current-dir").textContent = s.current_ui_dir || "—";
  const validation = s.project_validation || state.health?.project_validation;
  if (validation) {
    $("settings-project-validation").innerHTML = `
      <span class="${validation.valid ? "ok" : "bad"}">${validation.valid ? "✓ Android project" : "✕ Not a valid Android project"}</span>
      <span class="${validation.has_compose ? "ok" : "warn"}">${validation.has_compose ? "✓ Compose" : "! No Compose"}</span>
      <span class="${validation.has_design_tokens ? "ok" : "warn"}">${validation.has_design_tokens ? "✓ Tokens" : "! No tokens"}</span>
      <span class="${validation.has_target_ui ? "ok" : "warn"}">${validation.has_target_ui ? "✓ Target_ui" : "! No Target_ui"}</span>
      <span class="${validation.has_current_ui ? "ok" : "warn"}">${validation.has_current_ui ? "✓ Current UI" : "! No current UI"}</span>
      ${validation.warnings?.length ? `<span class="warn">${escapeHtml(validation.warnings.join(" · "))}</span>` : ""}
    `;
  } else {
    $("settings-project-validation").innerHTML = "";
  }
  $("settings-budget").textContent = fmtNumber(s.monthly_token_budget);
  $("settings-autonomy").value = s.autonomy_level || "L2";
  $("webhook-url").value = s.webhook_url || "";
  $("code-edits-enabled").checked = Boolean(s.code_edits_enabled);
  $("code-edit-mode").value = s.code_edit_mode || "worktree";
  fillModelSelect($("settings-model"), s.model);
  fillModelSelect($("model-select"), s.model);
  fillModelSelect($("run-model"), s.model);
  $("settings-session").innerHTML = `
    <span>User</span><b>${escapeHtml(state.authUser?.name || "Local Dictator")}</b>
    <span>Role</span><b>${escapeHtml(state.authUser?.role || "dictator")}</b>
    <span>Auth</span><b>${state.authUser?.auth_enabled ? "token enabled" : "local single-user"}</b>
  `;
  $("settings-runtime").innerHTML = `
    <span>Backend</span><b>${escapeHtml(s.backend)}</b>
    <span>Registry</span><b>${escapeHtml(s.registry_version)}</b>
    <span>Project</span><b>${escapeHtml(short(s.project_dir, 42))}</b>
    <span>Target_ui</span><b>${escapeHtml(short(s.target_ui_dir, 42))}</b>
  `;
}

async function saveBudget(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 1_000_000) {
    toast("Budget must be at least 1,000,000 tokens.", "error");
    return;
  }
  await saveSettings({ monthly_token_budget: Math.round(number) });
  closeModal();
}

function openBudgetEditor() {
  const current = state.settings?.monthly_token_budget || state.budget?.monthly_total_tokens || 100_000_000;
  const body = `
    <p class="muted">Set the monthly total-token ceiling. This is persisted in <code>org/budget_policy.yaml</code> and <code>.runtime/settings.json</code>.</p>
    <input id="budget-input" type="number" min="1000000" step="1000000" value="${Number(current)}" style="width:100%;background:rgba(0,0,0,.55);color:#fff;border:1px solid var(--border);border-radius:12px;padding:11px;font-size:15px" />
  `;
  openModal("Edit monthly token budget", "Hard ceiling for all model usage.", body, `
    <button class="btn ghost" data-close-modal>Cancel</button>
    <button class="btn primary" id="save-budget">Save budget</button>
  `, (modal) => {
    modal.querySelector("#save-budget").addEventListener("click", () => saveBudget(modal.querySelector("#budget-input").value));
  });
}

async function openFolderBrowser(kind) {
  const key = kind === "project" ? "project_dir" : (kind === "current" ? "current_ui_dir" : "target_ui_dir");
  const start = state.settings?.[key] || (kind === "project" ? state.health?.project_dir : state.health?.[`${kind}_ui_dir`]) || "";
  await showFolder(start, kind);
}

async function showFolder(path, kind) {
  const key = kind === "project" ? "project_dir" : (kind === "current" ? "current_ui_dir" : "target_ui_dir");
  try {
    const data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
    const rows = (data.directories || []).map((dir) => `
      <div class="folder-row" data-folder="${escapeHtml(dir.path)}">
        <span class="folder-icon">▸</span>
        <span>${escapeHtml(dir.name)}</span>
      </div>
    `).join("");
    const body = `
      <div class="folder-path">${escapeHtml(data.path)}</div>
      <div class="folder-list">${rows || `<div class="muted">No subdirectories.</div>`}</div>
    `;
    const title = kind === "project" ? "Select project directory" : (kind === "current" ? "Select Current UI directory" : "Select Target_ui directory");
    openModal(
      title,
      "Only directories are shown. Hidden folders are omitted.",
      body,
      `<button class="btn ghost" data-close-modal>Cancel</button>
       ${kind !== "project" ? `<button class="btn ghost" id="folder-search">Search recursively</button>` : ""}
       ${data.parent ? `<button class="btn ghost" id="folder-up">Up</button>` : ""}
       <button class="btn primary" id="folder-select">Select this folder</button>`,
      (modal) => {
        modal.querySelectorAll("[data-folder]").forEach((el) => {
          el.addEventListener("click", () => showFolder(el.dataset.folder, kind));
        });
        modal.querySelector("#folder-up")?.addEventListener("click", () => showFolder(data.parent, kind));
        modal.querySelector("#folder-search")?.addEventListener("click", () => searchFolders(data.path, kind));
        modal.querySelector("#folder-select")?.addEventListener("click", async () => {
          await saveSettings({ [key]: data.path });
          closeModal();
          if (kind === "target") await scanTarget(false);
          if (kind === "current") await scanCurrent(false);
        });
      },
    );
  } catch (error) {
    toast(`Folder browse error: ${error.message}`, "error");
  }
}

async function searchFolders(root, kind) {
  const patterns = kind === "current"
    ? "current_ui,screenshots_of_current_ui,current_screens"
    : "target_ui,target-ui,reference_ui";
  try {
    const data = await api(`/api/fs/search?root=${encodeURIComponent(root)}&patterns=${encodeURIComponent(patterns)}`);
    const rows = (data.results || []).map((item) => `
      <div class="folder-row" data-search-folder="${escapeHtml(item.path)}">
        <span class="folder-icon">◉</span>
        <span>${escapeHtml(item.path)} <span class="muted">(${item.image_count} images)</span></span>
      </div>
    `).join("");
    openModal(
      "Recursive folder search",
      `Found ${data.results?.length || 0} matching folder(s) under ${data.root}`,
      `<div class="folder-list">${rows || `<div class="muted">No matching folders found.</div>`}</div>`,
      `<button class="btn ghost" data-close-modal>Close</button>`,
      (modal) => {
        modal.querySelectorAll("[data-search-folder]").forEach((el) => {
          el.addEventListener("click", () => showFolder(el.dataset.searchFolder, kind));
        });
      },
    );
  } catch (error) {
    toast(`Folder search error: ${error.message}`, "error");
  }
}

// ------------------------------------------------------------------ modal
function openLogin() {
  const body = `
    <p class="muted">This control room is protected. Enter the API token from <code>org/users.yaml</code> or <code>UIUX_AUTH_TOKEN</code>.</p>
    <input id="login-token" type="password" placeholder="API token" style="width:100%;background:rgba(0,0,0,.55);color:#fff;border:1px solid var(--border);border-radius:10px;padding:10px" />
  `;
  openModal("Dictator authentication", "Bearer token required", body, `
    <button class="btn ghost" data-close-modal>Cancel</button>
    <button class="btn primary" id="login-submit">Unlock</button>
  `, (modal) => {
    modal.querySelector("#login-submit").addEventListener("click", async () => {
      const token = modal.querySelector("#login-token").value.trim();
      if (!token) return;
      try {
        const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ token }) });
        localStorage.setItem("uiux_token", token);
        state.authUser = result;
        closeModal();
        location.reload();
      } catch (error) {
        toast(`Login failed: ${error.message}`, "error");
      }
    });
  });
}

function openModal(title, sub, bodyHtml, actionsHtml = `<button class="btn ghost" data-close-modal>Close</button>`, onMount = null) {
  const root = $("modal-root");
  root.innerHTML = `
    <div class="modal" id="modal">
      <div class="modal-card">
        <h2>${escapeHtml(title)}</h2>
        ${sub ? `<div class="modal-sub">${escapeHtml(sub)}</div>` : ""}
        ${bodyHtml}
        <div class="modal-actions">${actionsHtml}</div>
      </div>
    </div>
  `;
  const modal = $("modal");
  modal.addEventListener("click", (event) => { if (event.target === modal) closeModal(); });
  modal.querySelectorAll("[data-close-modal]").forEach((el) => el.addEventListener("click", closeModal));
  if (onMount) onMount(modal);
}

function closeModal() {
  $("modal-root").innerHTML = "";
}

// ------------------------------------------------------------------ events / wiring
$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (tab) switchTab(tab.dataset.tab);
});
function switchObsTab(name) {
  state.obsTab = name;
  document.querySelectorAll("[data-obs-tab]").forEach((button) => button.classList.toggle("active", button.dataset.obsTab === name));
  document.querySelectorAll(".obs-pane").forEach((pane) => pane.classList.toggle("active", pane.id === `obs-pane-${name}`));
}
$("obs-subtabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-obs-tab]");
  if (button) switchObsTab(button.dataset.obsTab);
});
$("obs-refresh").addEventListener("click", () => { loadObservability(); checkLangfuse(true, false); });
$("obs-detail-close").addEventListener("click", () => $("obs-detail-card").classList.add("hidden"));
["obs-traces", "obs-sessions", "obs-scores", "obs-datasets", "obs-experiments"].forEach((id) => {
  $(id)?.addEventListener("click", (event) => {
    if (event.target.closest("a")) return;
    const item = event.target.closest("[data-obs-kind]");
    if (!item) return;
    openObsDetail(item.dataset.obsKind, item.dataset.obsId || item.dataset.obsIndex);
  });
});
$("history-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-history-filter]");
  if (!button) return;
  state.historyFilter = button.dataset.historyFilter;
  document.querySelectorAll("[data-history-filter]").forEach((item) => item.classList.toggle("active", item === button));
  renderHistory();
});
$("approval-subtabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-approval-subtab]");
  if (button) switchApprovalSubtab(button.dataset.approvalSubtab);
});
$("open-flow").addEventListener("click", () => openFullFlow());
$("stop-run").addEventListener("click", () => stopRun(state.runId));
$("flow-stop-run").addEventListener("click", () => stopRun(state.flowRunId || state.runId));
$("resume-run")?.addEventListener("click", () => resumeRun(state.runId));
$("flow-resume-run")?.addEventListener("click", () => resumeRun(state.flowRunId || state.runId));
$("open-evolution").addEventListener("click", () => switchTab("evolution"));
$("flow-run-select").addEventListener("change", (event) => { loadRun(event.target.value, true); });
$("flow-refresh").addEventListener("click", () => { if (state.flowRunId) loadRun(state.flowRunId, true); });
$("flow-all-connections").addEventListener("click", toggleFlowConnections);
$("flow-fit").addEventListener("click", () => { state.flowAutoFit = true; fitFlow(); });
$("flow-fullscreen").addEventListener("click", openFullFlow);
$("flow-native-fullscreen").addEventListener("click", () => {
  const request = document.documentElement.requestFullscreen?.();
  if (request?.catch) request.catch(() => toast("Fullscreen was blocked by the browser.", "error"));
});
$("flow-exit-full").addEventListener("click", () => { window.location.href = "/"; });
$("flow-zoom-in").addEventListener("click", () => { state.flowAutoFit = false; state.flowZoom = Math.min(1.8, state.flowZoom + 0.1); applyFlowZoom(); });
$("flow-zoom-out").addEventListener("click", () => { state.flowAutoFit = false; state.flowZoom = Math.max(0.35, state.flowZoom - 0.1); applyFlowZoom(); });
$("flow-zoom-reset").addEventListener("click", () => { state.flowAutoFit = false; state.flowZoom = 1; applyFlowZoom(); });
$("agent-live-close").addEventListener("click", closeAgentWindow);
$("artifact-run-select").addEventListener("change", (event) => { loadArtifactsForRun(event.target.value); });
$("contract-run-select").addEventListener("change", (event) => { if (event.target.value) loadContract(event.target.value, true); });
$("contract-refresh").addEventListener("click", () => { if (state.contractRunId) loadContract(state.contractRunId, true); });
$("contract-save").addEventListener("click", saveContract);
$("contract-recrtique").addEventListener("click", recritiqueContract);
$("upload-screenshots").addEventListener("click", () => $("screenshot-input").click());
$("recheck-qa").addEventListener("click", async () => {
  const runId = state.artifactRunId || state.runId;
  if (!runId) { toast("Select a run first.", "error"); return; }
  const button = $("recheck-qa");
  button.disabled = true;
  button.textContent = "Re-running QA…";
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/recheck-qa`, { method: "POST" });
    toast("QA re-check complete.");
    await loadRun(runId, false);
    await poll();
  } catch (error) {
    toast(`QA re-check error: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Re-run QA";
  }
});
$("screenshot-input").addEventListener("change", async (event) => {
  const runId = state.artifactRunId || state.runId;
  if (!runId || !event.target.files.length) return;
  const form = new FormData();
  for (const file of event.target.files) form.append("files", file);
  try {
    await fetch(`/api/runs/${encodeURIComponent(runId)}/screenshots`, { method: "POST", body: form });
    toast("Screenshots uploaded as QA evidence.");
    if (runId === state.runId) await loadRun(runId, false);
  } catch (error) {
    toast(`Screenshot upload error: ${error.message}`, "error");
  } finally {
    event.target.value = "";
  }
});
$("run-btn").addEventListener("click", startRun);
$("overview-scan-target").addEventListener("click", () => { switchTab("target"); scanTarget(false); });
$("scan-target").addEventListener("click", () => scanTarget(false));
$("scan-current").addEventListener("click", scanCurrent);
$("analyze-target").addEventListener("click", () => scanTarget(true));
$("browse-target").addEventListener("click", () => openFolderBrowser("target"));
$("browse-target-settings").addEventListener("click", () => openFolderBrowser("target"));
$("browse-project").addEventListener("click", () => openFolderBrowser("project"));
$("session-login").addEventListener("click", openLogin);
$("logout").addEventListener("click", () => { localStorage.removeItem("uiux_token"); location.reload(); });
$("activate-project").addEventListener("click", async () => {
  const projectId = $("project-select").value;
  if (!projectId) return;
  try {
    state.settings = await api(`/api/projects/${encodeURIComponent(projectId)}/activate`, { method: "POST" });
    state.health = await api("/api/health");
    state.budget = await api("/api/budget");
    state.targetManifest = await api("/api/target-ui");
    state.currentManifest = await api("/api/current-ui");
    state.projects = (await api("/api/projects")).projects || [];
    renderSettings();
    renderTarget();
    renderBudget();
    renderHealth();
    toast(`Project ${projectId} activated.`);
  } catch (error) {
    toast(`Project activation error: ${error.message}`, "error");
  }
});
$("browse-current").addEventListener("click", () => openFolderBrowser("current"));
$("budget-pill").addEventListener("click", openBudgetEditor);
$("edit-budget-settings").addEventListener("click", openBudgetEditor);
$("settings-budget").addEventListener("click", openBudgetEditor);
$("model-picker").addEventListener("click", () => {
  const all = state.models?.models || [];
  const body = `<input id="model-search" placeholder="Search 179 OpenCode models…" style="width:100%;background:rgba(0,0,0,.55);color:#fff;border:1px solid var(--border);border-radius:12px;padding:10px;margin-bottom:10px" /><div id="model-results" class="folder-list"></div>`;
  openModal("Search models", `${all.length} models available`, body, `<button class="btn ghost" data-close-modal>Close</button>`, (modal) => {
    const renderResults = (query) => {
      const q = query.toLowerCase();
      const matches = all.filter((model) => model.toLowerCase().includes(q)).slice(0, 80);
      modal.querySelector("#model-results").innerHTML = matches.map((model) => `
        <div class="folder-row" data-model="${escapeHtml(model)}"><span class="folder-icon">◇</span><span>${escapeHtml(model)}</span></div>
      `).join("") || `<div class="muted">No models match.</div>`;
      modal.querySelectorAll("[data-model]").forEach((el) => {
        el.addEventListener("click", async () => {
          closeModal();
          await saveSettings({ model: el.dataset.model });
          toast(`Model set to ${el.dataset.model}.`);
        });
      });
    };
    renderResults("");
    modal.querySelector("#model-search").addEventListener("input", (event) => renderResults(event.target.value));
    modal.querySelector("#model-search").focus();
  });
});
$("test-model").addEventListener("click", async () => {
  const model = $("model-select").value;
  if (!model) { toast("Select a model first.", "error"); return; }
  openModal("Model test", model, `<div class="muted">Calling ${escapeHtml(model)} with “Hey which model you are?”…</div>`);
  try {
    const result = await api("/api/models/test", { method: "POST", body: JSON.stringify({ model }) });
    openModal(
      result.ok ? "Model test: OK" : "Model test: failed",
      model,
      `<pre>${escapeHtml(result.text || result.error || "No response")}</pre><div class="meta">${result.ok ? `latency ${result.latency_ms}ms · session ${escapeHtml(result.session_id || "—")}` : escapeHtml(result.stderr || "")}</div>`,
    );
  } catch (error) {
    openModal("Model test: failed", model, `<div class="muted">${escapeHtml(error.message)}</div>`);
  }
});
$("refresh-models").addEventListener("click", async () => { await loadModels(true); toast("OpenCode model list refreshed."); });
$("model-select").addEventListener("change", async (event) => { await saveSettings({ model: event.target.value }); });
$("settings-model").addEventListener("change", async (event) => { await saveSettings({ model: event.target.value }); });
$("settings-autonomy").addEventListener("change", async (event) => { await saveSettings({ autonomy_level: event.target.value }); });
$("save-webhook").addEventListener("click", async () => { await saveSettings({ webhook_url: $("webhook-url").value.trim() }); });
$("save-code-edits").addEventListener("click", async () => {
  await saveSettings({
    code_edits_enabled: $("code-edits-enabled").checked,
    code_edit_mode: $("code-edit-mode").value,
  });
  toast($("code-edits-enabled").checked ? `Code edits enabled (${$("code-edit-mode").value} mode).` : "Code edits disabled.");
});
$("enable-notifications").addEventListener("click", async () => {
  if (!("Notification" in window)) { toast("Browser notifications are not supported.", "error"); return; }
  const permission = await Notification.requestPermission();
  toast(permission === "granted" ? "Browser notifications enabled." : "Browser notifications not granted.", permission === "granted" ? "success" : "error");
});
$("evolve-btn").addEventListener("click", () => {
  const status = state.evolution?.proposal?.status || "";
  const active = state.evolutionId && !["applied", "rejected", "stopped"].includes(status);
  if (active) {
    switchTab("evolution");
    loadEvolution(state.evolutionId, true);
  } else {
    startEvolution();
  }
});
$("langfuse-refresh").addEventListener("click", () => checkLangfuse(true));
$("app-plan-find").addEventListener("click", findAppPlans);
$("app-plan-load").addEventListener("click", () => loadAppPlan($("app-plan-path").value.trim()));
$("app-plan-save").addEventListener("click", saveAppPlan);
$("eval-run").addEventListener("click", runEval);
$("eval-refresh").addEventListener("click", loadEval);
$("evolution-start").addEventListener("click", startEvolution);
$("evolution-stop").addEventListener("click", stopEvolution);
$("evolution-select").addEventListener("change", (event) => { if (event.target.value) openEvolution(event.target.value); });
window.addEventListener("resize", () => {
  const saved = Number(localStorage.getItem("uiux_flow_side_width") || 0);
  if (saved) setFlowSideWidth(saved, false);
  if (state.activeTab === "flow") {
    if (state.flowAutoFit) fitFlow();
    else drawFlowEdges();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.agentWindowId) closeAgentWindow();
});
document.addEventListener("scroll", (event) => {
  const el = event.target;
  if (!(el instanceof HTMLElement)) return;
  el.classList.add("is-scrolling");
  clearTimeout(el._scrollTimer);
  el._scrollTimer = setTimeout(() => el.classList.remove("is-scrolling"), 800);
}, true);
window.addEventListener("hashchange", () => {
  const name = location.hash.replace("#", "");
  if (name) switchTab(name);
});
$("purge-mock").addEventListener("click", () => {
  openModal(
    "Purge mock/demo data?",
    "This removes local mock runs, approvals, artifacts, and mock token rows. Real OpenCode runs are untouched.",
    `<p class="muted">The dashboard no longer shows mock runs, but older mock data may still exist in the local database. This cleans it.</p>`,
    `<button class="btn ghost" data-close-modal>Cancel</button><button class="btn danger" id="confirm-purge">Purge</button>`,
    (modal) => {
      modal.querySelector("#confirm-purge").addEventListener("click", async () => {
        try {
          const result = await api("/api/admin/purge-mock", { method: "POST" });
          closeModal();
          toast(`Purged ${result.runs} mock runs and ${result.token_rows} token rows.`);
          await poll();
          await loadModels(false);
        } catch (error) {
          toast(`Purge error: ${error.message}`, "error");
        }
      });
    },
  );
});
document.addEventListener("DOMContentLoaded", init);

async function resumeRun(runId) {
  if (!runId) return;
  const button = $("resume-run");
  const flowButton = $("flow-resume-run");
  button.disabled = true;
  flowButton.disabled = true;
  try {
    const result = await api(`/api/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    toast(`Resuming from ${result.from_stage || "checkpoint"}…`);
    $("run-status").textContent = "running";
    $("run-status").className = "status running";
    await new Promise((resolve) => setTimeout(resolve, 700));
    await loadRun(runId, true);
    switchTab("flow");
  } catch (error) {
    toast(`Resume error: ${error.message}`, "error");
    button.disabled = false;
    flowButton.disabled = false;
  }
}

async function stopRun(runId) {
  if (!runId) return;
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
    toast(`Run stopped: ${runId}`);
    await loadRun(runId, false);
    await poll();
  } catch (error) {
    toast(`Stop run error: ${error.message}`, "error");
  }
}

async function startRun() {
  state.contextMode = "live";
  const prompt = $("decree").value.trim();
  if (!prompt) { $("decree").focus(); return; }
  const button = $("run-btn");
  button.disabled = true;
  $("run-status").textContent = "queued";
  $("run-status").className = "status running";
  state.events = [];
  state.artifacts = [];
  renderTimeline();
  try {
    const response = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        prompt,
        mode: $("mode").value,
        backend: "opencode",
        model: $("run-model").value || $("model-select").value || null,
        target_ui_enabled: $("target-ui").checked,
        auto_approve: $("auto-approve").checked,
        demo: false,
      }),
    });
    state.runId = response.run_id;
    state.flowRunId = response.run_id;
    await loadRun(response.run_id, true);
    switchTab("flow");
    toast(`Run started: ${response.run_id}`);
  } catch (error) {
    $("run-status").textContent = "failed";
    $("run-status").className = "status failed";
    toast(`Run error: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}
