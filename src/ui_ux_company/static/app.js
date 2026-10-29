const state = {
  health: null,
  registry: null,
  budget: null,
  approvals: [],
  runs: [],
  targetManifest: null,
  evolutions: [],
  runId: null,
  run: null,
  events: [],
  artifacts: [],
  agentEls: {},
  source: null,
  evolution: null,
};

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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!response.ok) {
    const detail = data && (data.detail || data.message || data.raw) ? (data.detail || data.message || data.raw) : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function fmtTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return iso; }
}

function fmtNumber(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

async function init() {
  try {
    state.health = await api("/api/health");
    state.registry = await api("/api/registry");
    state.budget = await api("/api/budget");
    state.approvals = (await api("/api/approvals")).approvals || [];
    state.runs = (await api("/api/runs")).runs || [];
    state.targetManifest = await api("/api/target-ui");
    state.evolutions = (await api("/api/evolve")).proposals || [];
    if (state.health.model_backend === "mock") $("backend").value = "mock";
    renderHealth();
    renderOrg();
    renderBudget();
    renderApprovals();
    renderRuns();
    renderTarget();
    renderEvolutions();
    renderTimeline();
  } catch (error) {
    $("footer-status").textContent = `init error: ${error.message}`;
  }
  setInterval(poll, 4000);
}

async function poll() {
  try {
    state.budget = await api("/api/budget");
    state.approvals = (await api("/api/approvals")).approvals || [];
    state.runs = (await api("/api/runs")).runs || [];
    state.evolutions = (await api("/api/evolve")).proposals || [];
    renderBudget();
    renderApprovals();
    renderRuns();
    renderEvolutions();
    if (state.runId) await loadRun(state.runId, false);
  } catch (error) {
    $("footer-status").textContent = `poll error: ${error.message}`;
  }
}

function renderHealth() {
  const h = state.health;
  if (!h) return;
  $("backend-pill").textContent = `${h.model_backend} · ${h.model}`;
  $("footer-status").textContent = `registry ${h.registry_version} · autonomy ${h.autonomy_level} · visual QA ${h.visual_qa_backend} · Roborazzi: ${h.roborezzi ? "on" : "off"}`;
  const budget = h.budget;
  $("budget-pill").textContent = `Budget ${budget.used_pct}% · ${fmtNumber(budget.remaining_tokens)} tokens left`;
}

function renderOrg() {
  const org = $("org");
  const registry = state.registry?.registry;
  if (!registry) { org.innerHTML = `<div class="muted">Registry unavailable.</div>`; return; }
  const tiers = {};
  (registry.agents || []).forEach((agent) => {
    const tier = Number(agent.tier ?? 99);
    (tiers[tier] ||= []).push(agent);
  });
  const tierMeta = Object.fromEntries((registry.tiers || []).map((t) => [Number(t.id), t.name]));
  state.agentEls = {};
  org.innerHTML = Object.entries(tiers).sort(([a], [b]) => Number(a) - Number(b)).map(([tier, agents]) => `
    <div class="tier">
      <div class="tier-title">Tier ${tier} · ${escapeHtml(tierMeta[Number(tier)] || "")}</div>
      <div class="agents">
        ${agents.map((agent) => `
          <div class="agent ${agent.enabled ? "enabled" : "disabled"}" data-agent="${escapeHtml(agent.id)}" title="${escapeHtml(agent.mission || "")}">
            <div class="name">${escapeHtml(agent.title || agent.id)}</div>
            <div class="role">${escapeHtml(agent.model || "")} · ${agent.enabled ? "active" : "on-demand"}</div>
          </div>
        `).join("")}
      </div>
    </div>
  `).join("");
  org.querySelectorAll(".agent").forEach((el) => { state.agentEls[el.dataset.agent] = el; });
}

function setAgentStatus(agentId, status) {
  const el = state.agentEls[agentId];
  if (!el) return;
  el.classList.remove("active", "done", "failed");
  if (status === "started") el.classList.add("active");
  if (status === "completed") el.classList.add("done");
  if (status === "failed") el.classList.add("failed");
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
}

function renderApprovals() {
  const list = $("approvals");
  const approvals = state.approvals || [];
  $("approval-count").textContent = String(approvals.length);
  if (!approvals.length) {
    list.innerHTML = `<div class="muted">No decisions waiting. The company is operating within its mandate.</div>`;
    return;
  }
  list.innerHTML = approvals.map((a) => `
    <div class="item">
      <div class="title">${escapeHtml(a.title)}</div>
      <div class="meta"><span class="badge pending">${escapeHtml(a.gate)}</span><span>${fmtTime(a.created_at)}</span></div>
      <div class="muted" style="margin-top:6px">${escapeHtml((a.message || "").slice(0, 260))}</div>
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
        <button class="btn small primary" data-approval="${escapeHtml(a.approval_id)}" data-decision="approve">Approve</button>
        <button class="btn small danger" data-approval="${escapeHtml(a.approval_id)}" data-decision="reject">Reject</button>
        <button class="btn small ghost" data-approval="${escapeHtml(a.approval_id)}" data-decision="override">Override</button>
      </div>
    </div>
  `).join("");
  list.querySelectorAll("button[data-approval]").forEach((button) => {
    button.addEventListener("click", () => decideApproval(button.dataset.approval, button.dataset.decision));
  });
}

async function decideApproval(approvalId, decision) {
  try {
    await api(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, notes: `Decision from control room: ${decision}` }),
    });
    await poll();
    if (state.runId) await loadRun(state.runId, false);
  } catch (error) {
    $("footer-status").textContent = `approval error: ${error.message}`;
  }
}

function renderRuns() {
  const runs = state.runs || [];
  $("run-count").textContent = String(runs.length);
  const list = $("runs");
  if (!runs.length) { list.innerHTML = `<div class="muted">No runs yet.</div>`; return; }
  list.innerHTML = runs.slice(0, 12).map((run) => `
    <div class="item clickable" data-run="${escapeHtml(run.run_id)}">
      <div class="title">${escapeHtml((run.prompt || "").slice(0, 110))}</div>
      <div class="meta">
        <span class="badge ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
        <span>${escapeHtml(run.mode)}</span>
        <span>${fmtTime(run.created_at)}</span>
      </div>
    </div>
  `).join("");
  list.querySelectorAll("[data-run]").forEach((el) => {
    el.addEventListener("click", () => loadRun(el.dataset.run, true));
  });
}

async function loadRun(runId, connect = true) {
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
    state.runId = runId;
    state.run = data.run;
    state.runState = data.state || {};
    state.events = data.events || [];
    state.artifacts = data.artifacts || [];
    renderRunMeta();
    renderTimeline();
    renderArtifacts();
    if (connect) connectEvents(runId, state.events.length ? state.events[state.events.length - 1].id : 0);
  } catch (error) {
    $("footer-status").textContent = `run load error: ${error.message}`;
  }
}

function renderRunMeta() {
  const run = state.run;
  const values = (state.runState || {}).values || {};
  if (!run) {
    $("run-status").textContent = "idle";
    $("run-meta").textContent = "No run selected. Issue a decree to begin.";
    return;
  }
  $("run-status").textContent = run.status;
  $("run-status").className = `status ${run.status}`;
  const gates = [
    values.critique_gate ? `critique:${values.critique_gate}` : null,
    values.eval_gate ? `eval:${values.eval_gate}` : null,
    values.qa_gate ? `qa:${values.qa_gate}` : null,
  ].filter(Boolean).join(" · ");
  $("run-meta").innerHTML = `
    <b>${escapeHtml(run.run_id)}</b> · ${escapeHtml(run.mode)} · ${escapeHtml(run.status)}
    ${gates ? `<br><span class="muted">${escapeHtml(gates)}</span>` : ""}
    <br><span class="muted">${escapeHtml((run.prompt || "").slice(0, 220))}</span>
  `;
}

function connectEvents(runId, afterId = 0) {
  if (state.source) state.source.close();
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?after_id=${afterId || 0}`);
  state.source = source;
  source.onmessage = (message) => {
    try { handleEvent(JSON.parse(message.data)); } catch { /* ignore */ }
  };
  source.onerror = () => {
    $("footer-status").textContent = "event stream reconnecting…";
  };
}

function handleEvent(event) {
  state.events.push(event);
  if (event.agent_id) {
    if (event.type === "agent.started") setAgentStatus(event.agent_id, "started");
    if (event.type === "agent.completed") setAgentStatus(event.agent_id, "completed");
    if (event.type === "agent.failed") setAgentStatus(event.agent_id, "failed");
  }
  if (event.type === "approval.required") poll();
  if (["run.completed", "run.paused", "run.failed", "run.started", "run.resumed"].includes(event.type)) {
    if (state.runId) loadRun(state.runId, false);
    poll();
  }
  renderTimeline();
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

function renderArtifacts() {
  const artifacts = state.artifacts || [];
  $("artifact-count").textContent = String(artifacts.length);
  const list = $("artifacts");
  if (!artifacts.length) { list.innerHTML = `<div class="muted">Artifacts appear here as the run progresses.</div>`; return; }
  list.innerHTML = artifacts.map((artifact) => `
    <a class="artifact" href="/api/artifacts/${encodeURIComponent(artifact.run_id)}/${encodeURIComponent(artifact.name)}" target="_blank" rel="noreferrer">
      ${escapeHtml(artifact.name)}
      <span>${escapeHtml(artifact.content_type || "")} · ${fmtTime(artifact.created_at)}</span>
    </a>
  `).join("");
}

function renderTarget() {
  const manifest = state.targetManifest || {};
  const images = manifest.images || [];
  $("target-status").innerHTML = `
    <span class="badge ${images.length ? "pass" : "pending"}">${escapeHtml(manifest.status || "unknown")}</span>
    <span class="muted"> ${images.length} image(s) · ${escapeHtml(manifest.precedence || "tiered")}</span>
  `;
  $("target-images").innerHTML = images.slice(0, 6).map((image) => `
    <div class="thumb">
      <img src="/api/target-ui/image?path=${encodeURIComponent(image.file)}" alt="${escapeHtml(image.screen || "target")}" onerror="this.style.display='none'" />
      <span>${escapeHtml(image.screen || "unknown")}</span>
    </div>
  `).join("");
}

async function scanTarget() {
  $("scan-target").disabled = true;
  try {
    state.targetManifest = await api("/api/target-ui/scan", {
      method: "POST",
      body: JSON.stringify({ analyze: false }),
    });
    renderTarget();
  } catch (error) {
    $("footer-status").textContent = `target scan error: ${error.message}`;
  } finally {
    $("scan-target").disabled = false;
  }
}

async function startRun() {
  const prompt = $("decree").value.trim();
  if (!prompt) { $("decree").focus(); return; }
  $("run-btn").disabled = true;
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
        backend: $("backend").value,
        target_ui_enabled: $("target-ui").checked,
        demo: $("demo").checked,
        auto_approve: $("auto-approve").checked,
      }),
    });
    state.runId = response.run_id;
    await loadRun(response.run_id, true);
    $("footer-status").textContent = `run ${response.run_id} started`;
  } catch (error) {
    $("footer-status").textContent = `run error: ${error.message}`;
    $("run-status").textContent = "failed";
    $("run-status").className = "status failed";
  } finally {
    $("run-btn").disabled = false;
  }
}

function renderEvolutions() {
  const list = $("evolutions");
  const proposals = state.evolutions || [];
  if (!proposals.length) {
    list.innerHTML = `<div class="muted">No proposals yet. The Evolve button asks the independent auditor for one.</div>`;
    return;
  }
  list.innerHTML = proposals.slice(0, 8).map((item) => `
    <div class="item clickable" data-evo="${escapeHtml(item.proposal_id)}">
      <div class="title">${escapeHtml(item.title)}</div>
      <div class="meta">
        <span class="badge ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
        <span>${fmtTime(item.created_at)}</span>
      </div>
    </div>
  `).join("");
  list.querySelectorAll("[data-evo]").forEach((el) => {
    el.addEventListener("click", () => showEvolution(el.dataset.evo));
  });
}

async function showEvolution(proposalId) {
  try {
    const data = await api(`/api/evolve/${encodeURIComponent(proposalId)}`);
    state.evolution = data;
    renderEvolutionDetail();
  } catch (error) {
    $("footer-status").textContent = `evolution load error: ${error.message}`;
  }
}

function renderEvolutionDetail() {
  const data = state.evolution;
  const detail = $("evolution-detail");
  if (!data) { detail.innerHTML = ""; return; }
  const proposal = data.proposal?.proposal || data.proposal || {};
  const evaluation = data.proposal?.evaluation || data.evaluation || null;
  const pending = ((data.state?.interrupts || data.state?.__interrupt__ || [])).length > 0;
  detail.innerHTML = `
    <div class="item" style="margin-top:8px">
      <div class="title">${escapeHtml(proposal.title || "Evolution proposal")}</div>
      <div class="muted" style="margin-top:6px">${escapeHtml(proposal.problem || "")}</div>
      <pre style="white-space:pre-wrap;max-height:240px;overflow:auto;font-size:10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:10px;padding:8px;margin-top:8px">${escapeHtml(JSON.stringify(proposal.changes || [], null, 2))}</pre>
      ${evaluation ? `<div class="meta"><span class="badge ${evaluation.verdict === "PASS" ? "pass" : "pending"}">${escapeHtml(evaluation.verdict)}</span><span>${escapeHtml(evaluation.shadow_eval || "")}</span></div>` : ""}
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
        ${pending ? `<button class="btn small primary" data-evo-decision="approve">Approve & apply safe changes</button>
        <button class="btn small danger" data-evo-decision="reject">Reject</button>` : ""}
      </div>
    </div>
  `;
  detail.querySelectorAll("[data-evo-decision]").forEach((button) => {
    button.addEventListener("click", () => decideEvolution(proposal.proposal_id || data.proposal?.proposal_id, button.dataset.evoDecision));
  });
}

async function runEvolution() {
  const button = $("evolve-btn");
  button.disabled = true;
  button.textContent = "✦ Auditing…";
  $("evolution-detail").innerHTML = `<div class="muted" style="margin-top:8px">The independent org auditor is reading traces, rework, budget, and gate evidence…</div>`;
  try {
    const result = await api("/api/evolve", {
      method: "POST",
      body: JSON.stringify({ focus: "quality", auto_approve: false }),
    });
    state.evolution = { proposal: result.proposal, state: result, evaluation: result.evaluation };
    await poll();
    renderEvolutionDetail();
  } catch (error) {
    $("evolution-detail").innerHTML = `<div class="muted">Evolution error: ${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "✦ Evolve / Improve";
  }
}

async function decideEvolution(proposalId, decision) {
  if (!proposalId) return;
  try {
    const result = await api(`/api/evolve/${encodeURIComponent(proposalId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ decision, notes: `Decision from control room: ${decision}` }),
    });
    state.evolution = { proposal: result.proposal, state: result };
    await poll();
    renderEvolutionDetail();
  } catch (error) {
    $("footer-status").textContent = `evolution decision error: ${error.message}`;
  }
}

$("run-btn").addEventListener("click", startRun);
$("scan-target").addEventListener("click", scanTarget);
$("evolve-btn").addEventListener("click", runEvolution);
document.addEventListener("DOMContentLoaded", init);
