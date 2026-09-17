"use strict";

// The browser holds display state only. Each request names its project explicitly.
const $ = (id) => document.getElementById(id);
const initial = new URLSearchParams(location.search);
const state = {token: null, projects: [], projectId: initial.get("project"), view: initial.get("view") || "agents", tab: initial.get("tab"), overview: null, projectOverview: null, applicationAgents: [], applicationAgentId: initial.get("agent"), focusId: initial.get("focus"), focuses: [], metrics: null, design: null, designEdits: null, designContext: null, designDirty: false, designRequest: 0, discoveryLimits: [], discoveringAgents: false, connections: [], connectionsRequest: 0, agents: [], agentSettings: {}, claudeKeyConfigured: null, jobs: [], selectedJob: initial.get("job"), source: null, generation: 0, overviewRequest: 0};
const labels = {agents: "Agents", metrics: "Metrics", traces: "Traces", overview: "Goals", design: "Measurement plan", eval: "Eval", fix: "Fix", settings: "Settings"};
const providers = {braintrust: "Braintrust", langsmith: "LangSmith", langfuse: "Langfuse"};
const providerEndpoints = {braintrust: "https://api.braintrust.dev", langsmith: "https://api.smith.langchain.com", langfuse: "https://cloud.langfuse.com"};
const activeStates = new Set(["queued", "running", "needs_input", "paused"]);
const terminalStates = new Set(["completed", "completed_with_limits", "failed", "cancelled"]);
const noticeDuration = 2500;
let noticeTimer = null;
const readable = (value) => String(value ?? "Unknown").replaceAll("_", " ");
const array = (value) => Array.isArray(value) ? value : [];
const idOf = (item) => item.id || item.audit_id || item.evaluation_id || item.run_id || item.baseline_id || item.snapshot_id || item.issue_id;
const resultId = (kind, item) => item[{audit: "audit_id", eval: "evaluation_id", fix: "run_id", baseline: "baseline_id", dataset: "snapshot_id", traces: "snapshot_id"}[kind]] || item.id;
const applicationAgent = () => state.applicationAgents.find((item) => item.id === state.applicationAgentId);
const currentFocus = () => state.focuses.find((item) => item.id === state.focusId);
const focusMeasurement = (focus = currentFocus()) => focus?.measurement || {};
const applicationAPI = (suffix = "", agentId = state.applicationAgentId, projectId = state.projectId) => projectAPI(`/application-agents/${encodeURIComponent(agentId)}${suffix}`, projectId);
const paths = (value) => value.split(",").map((part) => part.trim()).filter(Boolean);
const project = () => state.projects.find((item) => item.id === state.projectId);
const projectAPI = (suffix = "", projectId = state.projectId) => `/api/projects/${encodeURIComponent(projectId)}${suffix}`;
const date = (value) => value ? new Date(value).toLocaleDateString(undefined, {month: "short", day: "numeric"}) : "";
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = String(text); return node; }
function button(text, action, style = "secondary") { const node = el("button", style === "record-title" ? style : `button ${style}`, text); node.type = "button"; node.addEventListener("click", () => Promise.resolve().then(action).catch(showError)); return node; }
function quiet(text, action) { const node = button(text, action); node.className = "quiet-button"; return node; }
function badge(value) { const name = String(value || "unknown"); const tone = /complete|frozen|ready|passed|connected|available/.test(name) ? "good" : /fail|error|cancel/.test(name) ? "danger" : /wait|pending|partial|draft|pause|blocked|needs/.test(name) ? "warn" : ""; return el("span", `badge ${tone}`, readable(name)); }
function actions(...nodes) { const group = el("div", "actions"); group.append(...nodes); return group; }
function notice(text, error = false) {
  const banner = $("notice");
  clearTimeout(noticeTimer);
  noticeTimer = null;
  banner.textContent = text;
  banner.hidden = false;
  banner.setAttribute("role", error ? "alert" : "status");
  if (!error) noticeTimer = setTimeout(() => { banner.hidden = true; banner.textContent = ""; noticeTimer = null; }, noticeDuration);
}
function showError(error) { const text = error.message || "The request could not be completed. Try again."; if ($("dialog").open) { let alert = $("dialog-content").querySelector(".dialog-error"); if (!alert) { alert = el("p", "form-error dialog-error"); alert.setAttribute("role", "alert"); $("dialog-content").prepend(alert); } alert.textContent = text; } else notice(text, true); }
async function api(path, method = "GET", body) {
  const headers = method === "GET" ? {} : {"Content-Type": "application/json", "X-Agentagon-Token": state.token};
  if (path === "/api/session") headers["X-Agentagon-Bootstrap"] = "1";
  const response = await fetch(path, {method, headers, mode: "same-origin", credentials: "same-origin", ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  let data; try { data = await response.json(); } catch { throw new Error(`The local app returned an unreadable response (${response.status}).`); }
  if (!response.ok) throw Object.assign(new Error(data.error || data.message || `Request failed (${response.status}).`), {status: response.status});
  return data;
}
function updateURL() { const params = new URLSearchParams(); if (state.projectId) params.set("project", state.projectId); params.set("view", state.view); if (state.tab) params.set("tab", state.tab); if (state.applicationAgentId) params.set("agent", state.applicationAgentId); if (state.focusId) params.set("focus", state.focusId); if (state.selectedJob) params.set("job", state.selectedJob); history.replaceState(null, "", `/?${params}`); }
function heading(title, ...buttons) { const wrap = el("div", "page-heading"); wrap.append(el("h1", "", title)); if (buttons.length) wrap.append(actions(...buttons)); return wrap; }
function section(title, content, extra) { const wrap = el("section", "section"); const head = el("div", "section-heading"); head.append(el("h2", "", title)); if (extra) head.append(extra); wrap.append(head, content); return wrap; }
function empty(title, ...buttons) { const wrap = el("div", "empty-state"); wrap.append(el("h2", "", title)); if (buttons.length) wrap.append(actions(...buttons)); return wrap; }
function tabs(entries, selected, change) { const wrap = el("nav", "tabs"); wrap.setAttribute("aria-label", `${labels[state.view]} sections`); entries.forEach(([value, text]) => { const item = quiet(text, () => change(value)); if (value === selected) item.setAttribute("aria-current", "page"); wrap.append(item); }); return wrap; }
function changeTab(tab) { state.tab = tab; updateURL(); render(); }
function navigate(view, tab = null) { if (view === "agents") { state.view = view; return selectApplicationAgent(null).catch(showError); } state.view = view; state.tab = tab; updateURL(); render(); if (view === "settings") refreshConnections().then(render).catch(showError); if (view === "metrics") loadMetrics().catch(showError); if (view === "design") loadDesign().catch(showError); }
async function refreshConnections() { const id = state.projectId; const request = ++state.connectionsRequest; const [connections, agents] = await Promise.all([id ? api(projectAPI("/connections", id)) : Promise.resolve({connections: []}), api("/api/agents")]); if (id !== state.projectId || request !== state.connectionsRequest) return; state.connections = array(connections.connections).filter((connection) => connection.project_id === id); state.agents = agents.agents || []; state.agentSettings = agents.settings || {}; state.claudeKeyConfigured = agents.claude_key_configured ?? null; }
function renderProjectPicker() {
  $("project-select").replaceChildren();
  if (!state.projects.length) $("project-select").append(new Option("Choose a project", ""));
  state.projects.forEach((item) => $("project-select").append(new Option(item.available === false ? `${item.name} (unavailable)` : item.name, item.id)));
  $("project-select").value = state.projectId || "";
  $("project-name").textContent = project()?.name || "Your workspace";
  $("activity-count").textContent = state.projects.reduce((total, item) => total + (Number(item.active_jobs) || 0), 0);
}
async function refreshProjects() { const data = await api("/api/projects"); state.projects = data.projects || []; if (!state.projects.some((item) => item.id === state.projectId)) { state.projectId = data.selected_project_id || state.projects[0]?.id || null; state.connections = []; state.connectionsRequest += 1; } renderProjectPicker(); }
async function selectProject(id, selectedJob = null, applicationAgentId = null, focusId = null) {
  state.projectId = id; if (!applicationAgentId && state.view !== "settings") state.view = "agents"; state.applicationAgentId = applicationAgentId; state.focusId = focusId; state.focuses = []; state.applicationAgents = []; state.projectOverview = null; state.metrics = null; state.overview = null; state.connections = []; state.jobs = []; state.selectedJob = selectedJob; state.generation += 1;
  state.source?.close(); state.source = null; renderProjectPicker(); updateURL(); render(); renderAgent();
  if (!id) { await refreshConnections(); return; }
  if (project()?.available === false) { render(); return; }
  await Promise.all([loadOverview(), refreshConnections()]); if (id !== state.projectId) return; render(); connectEvents(id);
}
async function loadOverview() {
  if (!state.projectId) return;
  const generation = state.generation; const id = state.projectId; const request = ++state.overviewRequest; const agentId = state.applicationAgentId;
  const [projectData, inventory] = await Promise.all([api(projectAPI("/overview", id)), api(projectAPI("/application-agents", id))]);
  if (generation !== state.generation || request !== state.overviewRequest) return;
  state.projectOverview = projectData; state.applicationAgents = array(inventory.agents);
  if (agentId && !state.applicationAgents.some((item) => item.id === agentId && item.status === "confirmed")) { state.applicationAgentId = null; state.focusId = null; state.view = "agents"; }
  let data = projectData;
  if (state.applicationAgentId) data = await api(applicationAPI(`/overview${state.focusId ? `?focus_id=${encodeURIComponent(state.focusId)}` : ""}`, state.applicationAgentId, id));
  if (generation !== state.generation || request !== state.overviewRequest) return;
  state.overview = {...projectData, ...data}; state.focuses = array(data.focuses); state.metrics = null;
  if (!state.focuses.some((item) => item.id === state.focusId)) state.focusId = data.active_focus_id || state.focuses.find((item) => item.state !== "archived")?.id || null;
  state.jobs = array(data.jobs).filter((job) => !state.applicationAgentId || job.application_agent_id === state.applicationAgentId).map((job) => { const current = state.jobs.find((item) => idOf(item) === idOf(job)); return current && current.revision > job.revision ? current : job; });
  if (projectData.project) { const item = state.projects.find((p) => p.id === id); if (item) Object.assign(item, projectData.project); }
  if (!state.jobs.some((job) => idOf(job) === state.selectedJob)) state.selectedJob = idOf(state.jobs.find((job) => activeStates.has(job.state)) || state.jobs[0] || {});
  updateURL(); renderProjectPicker(); renderApplicationPicker(); render(); renderAgent(); if (state.view === "metrics") await loadMetrics(); if (state.view === "design") await loadDesign();
}
async function selectApplicationAgent(id, focusId = null) {
  state.applicationAgentId = id || null; state.focusId = focusId; state.focuses = []; state.metrics = null; state.overview = null; state.jobs = []; state.selectedJob = null; state.generation += 1;
  if (id && state.view === "agents") state.view = "overview";
  if (!id && state.view !== "settings") state.view = "agents";
  if ($("dialog").open) $("dialog").close(); renderApplicationPicker(); updateURL(); render(); renderAgent(); await loadOverview();
}
function renderApplicationPicker() {
  const picker = $("application-agent-select"); picker.replaceChildren(new Option("All application agents", ""));
  state.applicationAgents.filter((item) => item.status === "confirmed").forEach((item) => picker.append(new Option(item.name, item.id))); picker.value = state.applicationAgentId || "";
  $("application-agent-name").textContent = applicationAgent()?.name || ""; $("application-agent-name").hidden = !state.applicationAgentId;
}

function connectEvents(id) {
  state.source?.close(); const source = new EventSource(projectAPI("/events", id)); state.source = source;
  source.addEventListener("update", (event) => {
    if (id !== state.projectId) return;
    let value; try { value = JSON.parse(event.data); } catch { return; }
    const job = value; if (job.project_id && job.project_id !== id) return;
    if (!idOf(job) || (state.applicationAgentId && job.application_agent_id !== state.applicationAgentId)) return;
    const previous = state.jobs.find((item) => idOf(item) === idOf(job)); const index = state.jobs.indexOf(previous);
    if (index < 0) state.jobs.unshift(job); else state.jobs[index] = {...previous, ...job};
    if (!state.selectedJob) state.selectedJob = idOf(job);
    renderAgent();
    if (previous?.state !== job.state) { loadOverview().catch(showError); refreshProjects().catch(showError); }
  });
  source.addEventListener("open", () => { $("agent-panel").removeAttribute("data-disconnected"); });
  source.addEventListener("error", () => { $("agent-panel").setAttribute("data-disconnected", "true"); });
}
function render() {
  if (!labels[state.view]) state.view = "agents";
  document.querySelectorAll("#primary-nav button").forEach((node) => { node.hidden = !state.applicationAgentId && !["agents", "settings"].includes(node.dataset.view); if (node.dataset.view === state.view) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current"); });
  $("current-view").textContent = labels[state.view]; const page = $("page"); if (state.view === "design" && state.designDirty && page.dataset.design === designContext()) return; delete page.dataset.design; page.replaceChildren();
  if (!state.projectId && state.view !== "settings") { page.append(heading("Projects"), empty("No projects", button("Add project", addProject, ""))); return; }
  if (project()?.available === false) { page.append(heading("Project unavailable"), empty("Project directory not found", button("Add project location", addProject, ""), button("Remove registration", () => removeProject(state.projectId), "danger"))); return; }
  if (!state.overview && state.view !== "settings") { page.append(heading("Opening project…")); return; }
  if (!state.applicationAgentId && state.view !== "settings") return renderInventory(page);
  ({agents: renderInventory, metrics: renderMetrics, traces: renderTraces, overview: renderOverview, design: renderDesign, eval: renderEval, fix: renderFix, settings: renderSettings}[state.view])(page);
}
function renderOverview(page) {
  const target = applicationAgent(); const focus = currentFocus();
  page.append(heading(target?.name || "Agent overview", button("Edit agent", () => applicationAgentForm(target))));
  if (!state.focuses.length) { page.append(empty("Choose a focus", button("Choose a focus", () => focusForm(), ""), button("Find failures in traces", () => focusForm("recent_traces")))); }
  else {
    const chooser = field("Current goal", {choices: state.focuses.map((item) => [item.id, item.name || item.goal]), value: state.focusId}); chooser.input.addEventListener("change", () => { state.focusId = chooser.input.value; updateURL(); loadOverview().catch(showError); }); page.append(chooser.wrapper);
    const card = el("section", "card focus-summary"); card.append(badge(focus?.category || "custom"), el("h2", "", focus?.name || focus?.goal || "Selected goal")); if (focus?.goal && focus.goal !== focus.name) card.append(el("p", "", focus.goal)); if (focus?.target != null) card.append(el("p", "small", `Target: ${focus.target}`)); card.append(actions(button("Propose measurements", () => launch("design")), button("Design measurement", () => navigate("design"), ""), quiet("View metrics →", () => navigate("metrics")))); page.append(card);
  }
  page.append(readinessPanel());
  const cards = el("div", "card-grid");
  [["01", "Agree on a plan", "Review plan", () => navigate("design")], ["02", "Measure a baseline", focusReadiness().evaluation.ready ? "Run baseline" : "Prepare measurement", () => focusReadiness().evaluation.ready ? launch("baseline") : navigate("design")], ["03", "Improve the goal", "Start fix", () => launch("fix")]].forEach(([mark, title, action, handler]) => { const card = el("section", "card journey-card"); card.append(el("div", "journey-mark", mark), el("h2", "", title), actions(button(action, handler))); cards.append(card); }); page.append(cards);
  const pending = state.jobs.filter((job) => !terminalStates.has(job.state)); if (pending.length) page.append(section("Work in progress", jobList(pending)));
  const baselines = array(state.overview.baselines); if (baselines.length) page.append(section("Latest measurement", baselineCard(baselines[0]), quiet("Baseline history →", () => navigate("eval", "baselines"))));
  const issues = array(state.overview.issues); if (issues.length) page.append(section("Saved findings", issueList(issues)));
  const audits = array(state.overview.audits); if (audits.length) { const history = el("details", "section"); history.append(el("summary", "", "Previous investigations"), records(audits, "audit")); page.append(history); }
}

const focusCategories = [["correctness", "Task success and correctness"], ["reliability", "Reliability and tool use"], ["grounding", "Grounding and factuality"], ["safety", "Safety and policy"], ["security", "Security and permissions"], ["latency", "Latency"], ["cost", "Cost and efficiency"], ["interaction", "Interaction and instruction following"], ["custom", "Custom objective"]];
function renderInventory(page) {
  const discoveryButton = (text) => { const control = button(state.discoveringAgents ? "Discovering agents…" : text, discoverAgents, ""); control.disabled = state.discoveringAgents; return control; };
  page.append(heading("Your application agents", button("Add agent", () => applicationAgentForm()), discoveryButton("Discover agents")));
  const setup = el("div", "setup-strip"); setup.append(el("span", "small muted", project()?.path), quiet("Discovery options", discoverySetup), quiet("Connect trace source", () => navigate("settings", "connections")), quiet("Configure coding agent", () => navigate("settings", "agents"))); page.append(setup);
  const confirmed = state.applicationAgents.filter((item) => item.status === "confirmed"); const suggested = state.applicationAgents.filter((item) => item.status !== "confirmed");
  if (!confirmed.length && !suggested.length) page.append(empty("No application agents", discoveryButton("Discover application agents"), button("Add manually", () => applicationAgentForm())));
  const cards = (items) => { const grid = el("div", "inventory-grid"); items.forEach((item) => { const card = el("article", "card application-card"); card.append(el("h2", "", item.name)); const scopes = array(item.code_scopes); if (scopes.length) card.append(el("p", "scope-paths", scopes.join(" · "))); else card.append(el("p", "small muted", "No file detected")); card.append(actions(item.status === "confirmed" ? button("Open agent", () => selectApplicationAgent(item.id), "") : button("Review and confirm", () => applicationAgentForm(item, true), ""), quiet("Edit boundaries", () => applicationAgentForm(item)))); grid.append(card); }); return grid; };
  if (confirmed.length) page.append(section("Confirmed agents", cards(confirmed)));
  if (suggested.length) page.append(section("Suggestions to review", cards(suggested)));
  array(state.discoveryLimits).forEach((text) => page.append(el("p", "info-box", text)));
}
async function discoverAgents() {
  const preferences = state.projectOverview?.discovery || {seen: false};
  if (!preferences.seen) return discoverySetup();
  return runDiscovery();
}
function discoverySetup() {
  const current = state.projectOverview?.discovery || {seen: false}; const body = openDialog(current.seen ? "Discovery options" : "Choose optional discovery sources");
  const defaultAgent = state.agentSettings.default_agent || "codex"; const readiness = agentReadiness(defaultAgent); const capability = state.agents.find((item) => item.id === defaultAgent); const codingReady = readiness.state === "available" && capability?.authenticated === true;
  const coding = document.createElement("input"); coding.type = "checkbox"; coding.checked = codingReady && (!current.seen || current.coding_review); coding.disabled = !codingReady; const codingRow = el("label", "checkbox-row"); codingRow.append(coding, document.createTextNode(`Ask ${defaultAgent === "codex" ? "Codex" : "Claude"} to review the local suggestions`));
  const codingHint = el("p", "field-hint", codingReady ? "Runs read-only and may rename or exclude suggestions. It cannot confirm agents or edit files." : readiness.state === "available" ? "Authentication could not be verified. Refresh after signing in or configuring credentials." : readiness.message);
  const traceConnections = state.connections.filter((item) => item.status === "connected" && item.project && item.project_ids?.includes(state.projectId));
  const traces = document.createElement("input"); traces.type = "checkbox"; traces.checked = Boolean(current.trace_metadata && traceConnections.length); traces.disabled = !traceConnections.length; const traceRow = el("label", "checkbox-row"); traceRow.append(traces, document.createTextNode("Match up to 100 recent root-trace metadata records"));
  const traceHint = el("p", "field-hint", traceConnections.length ? "Reads names, tags, environment and service identifiers from the last seven days. Inputs and outputs are not fetched." : "Connect and test a trace provider with a selected provider project to enable matching.");
  const traceConnection = field("Trace source", {choices: traceConnections.map((item) => [item.id, item.name || providers[item.provider]]), value: traceConnections.some((item) => item.id === current.trace_connection_id) ? current.trace_connection_id : traceConnections[0]?.id}); traceConnection.wrapper.hidden = !traceConnections.length; traceConnection.input.disabled = !traceConnections.length;
  const settings = actions(quiet("Coding agent settings", () => { $("dialog").close(); navigate("settings", "agents"); }), quiet("Trace connections", () => { $("dialog").close(); navigate("settings", "connections"); }));
  const kit = formKit(body, "Discover agents", async () => { const preferences = {coding_review: coding.checked, trace_metadata: traces.checked, trace_cap: 100, trace_connection_id: traces.checked ? traceConnection.input.value : null}; await runDiscovery(preferences); $("dialog").close(); });
  kit.form.append(codingRow, codingHint, traceRow, traceHint, traceConnection.wrapper, settings); kit.finish();
}
async function runDiscovery(preferences) {
  if (state.discoveringAgents) return;
  const projectId = state.projectId; state.discoveringAgents = true; render();
  try {
    const result = await api(projectAPI("/application-agents/discover", projectId), "POST", preferences ? {preferences} : {});
    if (projectId !== state.projectId) return;
    state.discoveryLimits = array(result.limitations); state.view = "agents"; if (state.projectOverview) state.projectOverview.discovery = result.preferences;
    await selectApplicationAgent(null);
    notice(`${array(result.agents).filter((item) => item.status === "suggested").length} agent suggestions ready.`);
  } finally { state.discoveringAgents = false; render(); }
}
function applicationAgentForm(existing = {}, reviewing = false) {
  const projectId = state.projectId; const body = openDialog(existing.status === "suggested" ? "Confirm an application agent" : existing.id ? "Edit application agent" : "Add application agent", project()?.name);
  const generatedDescription = /^Discovered .+ entrypoint in .+\. Confirm its code and trace boundaries\.$/.test(existing.description || "") || existing.description === "Discovered in imported traces. Confirm its code binding and trace selector."; const name = field("Agent name", {required: true, value: existing.name || "", placeholder: "Support triage"}); const description = field("Agent responsibility", {type: "textarea", value: generatedDescription ? "" : existing.description || "", placeholder: "What behavior or workflow does this agent own?"}); const scopes = field("Agent code paths", {value: array(existing.code_scopes).join(", "), placeholder: "src/support, prompts/support"});
  const shared = field("Shared code paths", {value: array(existing.shared_dependencies).join(", "), placeholder: "src/shared, tools/common"});
  const connections = state.connections.filter((item) => item.project_id === projectId); const connection = field("Trace connection", {choices: [["", "No imported trace source"], ...connections.map((item) => [item.id, item.name || providers[item.provider]])], value: existing.trace_selector?.connection_id || ""}); const providerProject = field("Trace project", {value: existing.trace_selector?.project || ""}); const filters = field("Trace selector (JSON, optional)", {type: "textarea", value: existing.trace_selector?.filters ? JSON.stringify(existing.trace_selector.filters) : ""});
  providerProject.input.readOnly = true; const syncTraceProject = () => { providerProject.input.value = connections.find((item) => item.id === connection.input.value)?.project || ""; }; connection.input.addEventListener("change", syncTraceProject); if (connection.input.value) syncTraceProject();
  const kit = formKit(body, existing.status === "suggested" ? "Confirm agent" : "Save agent", async () => { const traceSelector = reviewing ? existing.trace_selector || {} : connection.input.value ? {connection_id: connection.input.value, ...(providerProject.input.value.trim() ? {project: providerProject.input.value.trim()} : {}), ...(filters.input.value.trim() ? {filters: JSON.parse(filters.input.value)} : {})} : {}; const payload = {name: name.input.value.trim(), description: description.input.value.trim(), ...(existing.revision != null ? {expected_revision: existing.revision} : {}), code_scopes: paths(scopes.input.value), shared_dependencies: paths(shared.input.value), trace_selector: traceSelector, status: "confirmed"}; const result = await api(existing.id ? applicationAPI("", existing.id, projectId) : projectAPI("/application-agents", projectId), "POST", payload); $("dialog").close(); if (state.projectId === projectId) { await loadOverview(); state.view = "overview"; await selectApplicationAgent(result.id); } });
  if (reviewing) { const source = el("dl", "details-grid"); source.append(el("dt", "", "File"), el("dd", "", array(existing.code_scopes).join(", ") || "No file detected")); kit.form.append(name.wrapper, source); }
  else { kit.form.append(name.wrapper, description.wrapper, scopes.wrapper, shared.wrapper); if (connections.length || Object.keys(existing.trace_selector || {}).length) { const trace = el("details"); trace.append(el("summary", "", "Imported trace source (optional)"), connection.wrapper, providerProject.wrapper, filters.wrapper); kit.form.append(trace); } }
  kit.finish();
}
function focusForm(route = "goal") {
  if (!state.applicationAgentId) return navigate("agents");
  const projectId = state.projectId; const agentId = state.applicationAgentId; const body = openDialog("What would you like to improve?", applicationAgent()?.name);
  const category = field("Focus category", {choices: focusCategories}); const name = field("Focus name", {placeholder: "Reliable ticket creation"}); const goal = field("Ideal Behavior (optional)", {type: "textarea", placeholder: "Create exactly one ticket for each request, including retries."}); const target = field("Target (optional)", {placeholder: "For example: p95 below one second"});
  const recent = el("div", "form-grid"); const snapshot = field("Imported trace snapshot", {choices: [["", "Choose saved trace evidence"], ...array(state.overview.traces).map((item) => [resultId("traces", item), item.name || `${item.provider || "Traces"} · ${date(item.created_at)}`])]}); const count = field("Most recent completed traces", {type: "number", min: 1, max: 1000, value: 100}); recent.append(snapshot.wrapper, count.wrapper); const missing = el("div", "warning"); missing.append(el("p", "", "Import a bounded trace sample before starting from recent traces."), quiet("Import trace evidence", () => importFlow("traces")));
  const kit = formKit(body, "Save focus", async () => { const categoryLabel = focusCategories.find(([value]) => value === category.input.value)?.[1] || "Custom objective"; const customName = name.input.value.trim(); const payload = {name: category.input.value === "custom" ? customName : categoryLabel, category: category.input.value, goal: goal.input.value.trim() || customName || categoryLabel, source: route === "recent_traces" ? {kind: "recent_traces", count: Number(count.input.value), trace_snapshot_id: snapshot.input.value} : {kind: "goal"}}; if (target.input.value !== "") payload.target = target.input.value.trim(); const result = await api(applicationAPI("/focuses", agentId, projectId), "POST", payload); if (state.projectId === projectId && state.applicationAgentId === agentId) { state.focusId = result.id; state.view = "design"; await loadOverview(); updateURL(); render(); $("dialog").close(); notice("Focus saved."); } });
  const update = () => { const custom = category.input.value === "custom"; const tracing = route === "recent_traces"; name.wrapper.hidden = !custom; name.input.required = custom; recent.hidden = !tracing; missing.hidden = !tracing || array(state.overview.traces).length > 0; snapshot.input.required = tracing; count.input.required = tracing; kit.save.disabled = tracing && !array(state.overview.traces).length; }; category.input.addEventListener("change", update); update(); kit.form.append(category.wrapper, name.wrapper, goal.wrapper, target.wrapper, recent, missing); kit.finish();
}
function setupChecklist() {
  const box = el("section", "setup-checklist"); box.setAttribute("aria-label", "Setup checklist");
  const host = state.agentSettings.default_agent || "codex";
  const ready = agentReadiness(host).state === "available";
  const connected = state.connections.some((item) => item.project_id === state.projectId);
  [["Project", true, "Connected on this computer", null], ["Coding agent", ready, ready ? `${readable(host)} is configured` : "Configure a coding agent to propose and prepare measurements", () => navigate("settings", "agents")], ["Traces · optional", connected, connected ? "A source is connected to this project" : "Start from requirements and tests; connect production evidence when useful", () => navigate("settings", "connections")]].forEach(([name, done, text, action]) => {
    const item = el("div", "setup-check"); item.append(el("span", `check-symbol ${done ? "done" : ""}`, done ? "✓" : "○"));
    const copy = el("div"); copy.append(el("strong", "", name), el("p", "small muted", text)); if (action) copy.append(quiet(done ? "Manage" : "Configure", action)); item.append(copy); box.append(item);
  }); return box;
}
function designContext() { return `${state.projectId}/${state.applicationAgentId}/${state.focusId}`; }
function designAPI(suffix = "", projectId = state.projectId, agentId = state.applicationAgentId, focusId = state.focusId) { return applicationAPI(`/focuses/${encodeURIComponent(focusId)}/design${suffix}`, agentId, projectId); }
async function loadDesign(force = false) {
  if (!currentFocus()) return;
  const context = designContext();
  if (!force && state.designContext === context && state.designDirty) return;
  const request = ++state.designRequest;
  const result = await api(designAPI());
  if (context !== designContext() || request !== state.designRequest) return;
  state.design = result; state.designContext = context; state.designEdits = null; state.designDirty = false;
  if (state.view === "design") render();
}
function emptyDesign() { return {behaviors: [], metrics: {}, scoring: {mode: "primary"}, evaluation: {mode: "create", framework: "pytest", command: {argv: [], cwd: "."}, scorer: "", output_mapping: {}}, evidence: [], background: currentFocus()?.goal || ""}; }
function textLines(value) { return String(value || "").split("\n").map((line) => line.trim()).filter(Boolean); }
function renderDesign(page) {
  const focus = currentFocus();
  page.append(heading("Measurement plan", focus?.goal || "Choose a goal before deciding what to measure.", quiet("Back to goals", () => navigate("overview"))));
  if (!focus) { page.append(empty("Start with a goal.", "Describe the outcome you want, then review a concrete plan for measuring it.", button("Add goal", () => focusForm(), ""))); return; }
  page.dataset.design = designContext();
  if (state.designContext !== designContext() || !state.design) { page.append(el("p", "loading-evidence", "Loading your goal's measurement plan…")); return; }
  const data = state.design; const saved = data.draft;
  const draft = structuredClone(state.designEdits || saved || emptyDesign());
  const draftState = saved?.state || "not started";
  const intro = el("section", "card design-intro"); intro.append(badge(draftState), el("h2", "", "Define what good looks like."), el("p", "", "Review behaviors, measurable signals and required checks before preparing an evaluator. A higher score never overrides a failed required behavior."), el("p", "small muted", "Production traces are optional. Trajectory checks need imported traces or instrumented evaluation runs that retain the relevant steps."));
  intro.append(actions(button(saved ? "Ask agent to revise plan" : "Ask agent for a plan", () => launch("design")), quiet("Reload saved plan", () => loadDesign(true)))); page.append(intro);
  if (!saved && !state.designEdits) { page.append(empty("Get a proposal or write your own.", "Your coding agent can inspect the code, requirements and existing tests, then propose behaviors and metrics for you to edit. Nothing is accepted automatically.", button("Write a plan", () => { state.designEdits = emptyDesign(); state.designDirty = true; delete page.dataset.design; render(); }, ""))); return; }
  [...array(data.limitations), ...array(saved?.limitations), ...array(saved?.native_plan?.blockers)].forEach((limit) => page.append(el("p", "warning", limit)));
  const projectId = state.projectId, agentId = state.applicationAgentId, focusId = state.focusId, context = designContext();
  const native = array(data.evaluators); const frozen = array(data.frozen_evaluators).filter((item) => item.scoring);
  const selectedFrozen = frozen.find((item) => item.evaluation_id === draft.evaluation?.evaluation_id);
  const frozenDefinition = selectedFrozen?.scoring;
  const locked = draft.evaluation?.mode === "reuse" && Boolean(selectedFrozen);
  if (locked) page.append(el("p", "info-box", "This evaluator's scoring is frozen. Its behaviors and metrics are read-only. Choose a new evaluator to change the definition."));
  const background = field("Background and requirements", {type: "textarea", value: draft.background || "", hint: "Explain what the agent should achieve and which requirements establish correct behavior."});
  const evidence = field("Supporting evidence", {type: "textarea", value: array(draft.evidence).join("\n"), hint: "One source per line: requirements, repository paths, known examples or retained traces."});
  const behaviorRows = [], metricRows = [];
  let collect;
  const kit = formKit(page, "Save draft", async () => {
    const payload = collect();
    if (!Object.keys(payload.metrics).length) throw new Error("Add at least one metric before saving.");
    await api(designAPI("", projectId, agentId, focusId), "POST", {expected_revision: saved?.revision || 0, ...payload});
    if (context === designContext()) { await loadDesign(true); notice("Draft saved. Review it, then accept the plan before preparation."); }
  });
  kit.form.classList.add("design-form"); const contextDetails = el("details", "design-context"); contextDetails.append(el("summary", "", "Goal context and supporting evidence"), background.wrapper, evidence.wrapper); kit.form.append(contextDetails);
  const scoreMode = field("Scoring approach", {choices: [["primary", "One primary metric"], ["weighted", "Weighted score"], ["custom", "Existing custom scorer"]], value: draft.scoring?.mode || "primary"});
  const primary = field("Primary metric", {choices: [["", "Choose a metric"]], value: draft.scoring?.primary || draft.scoring?.custom_metric || ""});
  const scoreTarget = field("Success target (optional)", {type: "number", value: draft.scoring?.target ?? "", hint: "Use original metric units for primary or custom scoring, and normalized score units for weighted scoring. Required behavior gates remain separate."}); scoreTarget.input.step = "any";
  const metricChoices = () => metricRows.map((row) => [row.id.input.value, row.id.input.value]).filter(([id]) => id);
  const setChoices = (input, value) => { input.replaceChildren(new Option("Choose a metric", "")); metricChoices().forEach(([id, name]) => input.append(new Option(name, id))); input.value = value || ""; };
  const refreshChoices = () => { setChoices(primary.input, primary.input.value); behaviorRows.forEach((row) => setChoices(row.metric.input, row.metric.input.value)); };
  const definition = el("fieldset", "design-definition"); definition.disabled = locked;
  const behaviorSection = el("section", "design-section"); const behaviorHead = el("div", "section-heading"); behaviorHead.append(el("h2", "", "Behaviors and signals"), quiet("Add behavior", () => { state.designEdits = collect(); state.designEdits.behaviors.push({id: `behavior_${crypto.randomUUID().slice(0, 8)}`, description: "", required: true, check: "", evidence: [], prerequisites: []}); state.designDirty = true; delete page.dataset.design; render(); })); behaviorSection.append(behaviorHead, el("p", "small muted", "Describe observable behavior. Required checks stay separate from the score used to rank improvements."));
  array(draft.behaviors).forEach((behavior, index) => {
    const card = el("article", "design-item"); const header = el("div", "section-heading"); header.append(el("h3", "", `Behavior ${index + 1}`), quiet("Remove behavior", () => { state.designEdits = collect(); state.designEdits.behaviors.splice(index, 1); state.designDirty = true; delete page.dataset.design; render(); }));
    const description = field(`Behavior ${index + 1}`, {type: "textarea", required: true, value: behavior.description || "", placeholder: "Create one ticket per request, including retries."});
    const required = document.createElement("input"); required.type = "checkbox"; required.checked = behavior.required !== false; const requiredLabel = el("label", "checkbox-row"); requiredLabel.append(required, document.createTextNode("Required behavior · failure blocks an improvement"));
    const kind = field("Measure with", {choices: [["check", "Executable check"], ["metric", "Metric threshold"]], value: behavior.metric ? "metric" : "check"});
    const check = field("Check name", {required: true, value: behavior.check || "", placeholder: "no_duplicate_tickets"});
    const metric = field("Behavior metric", {choices: [["", "Choose a metric"]]});
    const op = field("Required result", {choices: [["gte", "At least"], ["lte", "At most"]], value: behavior.op || "gte"}); const bound = field("Behavior threshold", {type: "number", value: behavior.bound ?? 0}); bound.input.step = "any";
    const rubric = field("Rule or judge rubric", {type: "textarea", value: behavior.rubric || "", placeholder: "What evidence establishes that this behavior passed?"});
    const sources = field("Behavior evidence", {type: "textarea", value: array(behavior.evidence).join("\n"), hint: "One source per line."}); const needs = field("Measurement prerequisites", {type: "textarea", value: array(behavior.prerequisites).join("\n"), hint: "For example: record tool-call IDs during test execution, or import complete traces."});
    const row = {id: behavior.id || `behavior_${index + 1}`, description, required, kind, check, metric, op, bound, rubric, sources, needs}; behaviorRows.push(row);
    const threshold = el("div", "form-grid"); threshold.append(metric.wrapper, op.wrapper, bound.wrapper);
    const update = () => { const measured = kind.input.value === "metric"; check.wrapper.hidden = measured; check.input.required = !measured; threshold.hidden = !measured; metric.input.required = measured; bound.input.required = measured; }; kind.input.addEventListener("change", update); update();
    const details = el("details"); details.append(el("summary", "", "Evidence and prerequisites"), sources.wrapper, needs.wrapper);
    card.append(header, description.wrapper, requiredLabel, kind.wrapper, check.wrapper, threshold, rubric.wrapper, details); behaviorSection.append(card);
  }); definition.append(behaviorSection);
  const metricsSection = el("section", "design-section"); const metricsHead = el("div", "section-heading"); metricsHead.append(el("h2", "", "Metrics"), quiet("Add metric", () => { state.designEdits = collect(); let key = `metric_${Object.keys(state.designEdits.metrics).length + 1}`; while (state.designEdits.metrics[key]) key += "_new"; state.designEdits.metrics[key] = {direction: "max", unit: "ratio", aggregation: "mean", missing: "unknown", scale: 1, weight: 1}; state.designDirty = true; delete page.dataset.design; render(); })); metricsSection.append(metricsHead);
  Object.entries(locked && frozenDefinition ? frozenDefinition.metrics : draft.metrics || {}).forEach(([name, value]) => {
    const card = el("article", "design-item"); const header = el("div", "section-heading"); header.append(el("h3", "", name), quiet("Remove metric", () => { state.designEdits = collect(); delete state.designEdits.metrics[id.input.value]; state.designDirty = true; delete page.dataset.design; render(); }));
    const id = field("Metric key", {required: true, value: name}); const direction = field("Direction", {choices: [["max", "Higher is better"], ["min", "Lower is better"]], value: value.direction || "max"}); const unit = field("Unit", {required: true, value: value.unit || "", placeholder: "ratio, ms, USD"});
    const aggregation = field("Aggregation", {choices: [["mean", "Mean"], ["median", "Median"], ["min", "Minimum"], ["max", "Maximum"], ["sum", "Sum"]], value: value.aggregation || "mean"}); const missing = field("Missing measurements", {choices: [["unknown", "Remain unknown"], ["fail", "Fail the check"]], value: value.missing || "unknown"});
    const scale = field("Normalization scale", {type: "number", min: 0.000001, required: true, value: value.scale ?? 1, hint: "Weighted scores divide each metric by its scale before combining it."}); scale.input.step = "any"; const weight = field("Weight", {type: "number", min: 0.000001, required: true, value: value.weight ?? 1}); weight.input.step = "any";
    const description = field("What this metric measures", {type: "textarea", value: value.description || ""}); const sources = field("Metric evidence", {type: "textarea", value: array(value.evidence).join("\n")}); const needs = field("Metric prerequisites", {type: "textarea", value: array(value.prerequisites).join("\n")});
    metricRows.push({id, direction, unit, aggregation, missing, scale, weight, description, sources, needs});
    id.input.addEventListener("change", refreshChoices);
    const grid = el("div", "form-grid"); grid.append(id.wrapper, direction.wrapper, unit.wrapper, scale.wrapper, weight.wrapper); const details = el("details"); details.append(el("summary", "", "Aggregation, evidence and prerequisites"), description.wrapper, aggregation.wrapper, missing.wrapper, sources.wrapper, needs.wrapper); card.append(header, grid, details); metricsSection.append(card);
  }); definition.append(metricsSection);
  refreshChoices(); primary.input.value = draft.scoring?.primary || draft.scoring?.custom_metric || ""; behaviorRows.forEach((row, index) => { row.metric.input.value = draft.behaviors[index].metric || ""; });
  const scoreSection = el("section", "design-section"); scoreSection.append(el("h2", "", "Rank improvements; enforce required checks"), el("p", "small muted", "The score ranks candidates. Every required behavior above remains a separate gate."), scoreMode.wrapper, primary.wrapper, scoreTarget.wrapper);
  const scoreUpdate = () => { const weighted = scoreMode.input.value === "weighted"; primary.wrapper.hidden = weighted; primary.input.required = !weighted; metricRows.forEach((row) => { row.weight.wrapper.hidden = !weighted; row.scale.wrapper.hidden = !weighted; row.weight.input.required = weighted; row.scale.input.required = weighted; }); primary.wrapper.querySelector("label").textContent = scoreMode.input.value === "custom" ? "Custom scorer output metric" : "Primary metric"; }; scoreMode.input.addEventListener("change", scoreUpdate); scoreUpdate(); definition.append(scoreSection); kit.form.append(definition);
  const evaluation = draft.evaluation || {};
  const evalSection = el("section", "design-section"); evalSection.append(el("h2", "", "Evaluator"));
  const mode = field("Evaluator approach", {choices: [["create", "Prepare a new evaluator"], ["reuse", "Reuse an existing evaluator"]], value: evaluation.mode || "create"});
  const existing = field("Existing evaluator", {choices: [["", "Enter an existing command"], ...native.map((item) => [`native:${item.id}`, `${item.framework} · ${item.entrypoint || item.command?.argv?.join(" ") || item.id}`]), ...frozen.map((item) => [`frozen:${item.evaluation_id}`, `Reviewed · ${item.goal || item.evaluation_id}`])], value: evaluation.evaluation_id ? `frozen:${evaluation.evaluation_id}` : evaluation.candidate_id ? `native:${evaluation.candidate_id}` : ""});
  const framework = field("Framework", {choices: [["pytest", "pytest"], ["braintrust", "Braintrust"], ["deepeval", "DeepEval"], ["custom", "Custom"]], value: evaluation.framework || "pytest"}); const entrypoint = field("Evaluator entrypoint", {value: evaluation.entrypoint || "", placeholder: "evals/test_agent.py"});
  const executable = field("Executable", {value: evaluation.command?.argv?.[0] || "", placeholder: "python"}); const args = field("Command arguments", {type: "textarea", value: array(evaluation.command?.argv).slice(1).join("\n"), hint: "One argument per line, for example -m on one line and pytest on the next. This is an argument list, not a shell command."}); const cwd = field("Working directory", {value: evaluation.command?.cwd || ".", required: true}); const scorer = field("Scorer or correctness rule", {type: "textarea", value: evaluation.scorer || "", placeholder: "Existing scorer path or the accepted rule to implement."});
  const datasets = array(data.snapshots).filter((item) => item.kind === "dataset" && item.provenance?.dataset_partition !== "final_holdout"); const dataset = field("Dataset input", {choices: [["", "Use repository cases or prepare accepted examples"], ...datasets.map((item) => [item.id, `${item.name || item.id} · ${item.count} cases`])], value: evaluation.dataset_snapshot_id || ""});
  const mapping = el("details"); mapping.append(el("summary", "", "Native metric output mapping")); const mappingRows = metricRows.map((row) => { const key = row.id.input.value; const item = field(`Output field for ${key}`, {value: evaluation.output_mapping?.[key] || "", placeholder: "scores.task_success", hint: "Native result field that supplies this metric. Leave blank for a new evaluator to implement."}); mapping.append(item.wrapper); return {row, item}; });
  const runnerFields = el("div", "design-runner"); const commandDetails = el("details"); commandDetails.open = mode.input.value === "reuse"; commandDetails.append(el("summary", "", "Native command and output mapping"), entrypoint.wrapper, executable.wrapper, args.wrapper, cwd.wrapper, mapping); runnerFields.append(framework.wrapper, scorer.wrapper, dataset.wrapper, commandDetails);
  if (evaluation.candidate_id) { const selected = native.find((item) => item.id === evaluation.candidate_id); if (selected) { const details = el("ul", "gate-list"); array(selected.evidence).forEach((item) => details.append(el("li", "", `${item.path}${item.line ? `:${item.line}` : ""} · ${item.detail || "Detected evaluator"}`))); runnerFields.prepend(details); } }
  const evalUpdate = () => { existing.wrapper.hidden = mode.input.value !== "reuse"; runnerFields.hidden = locked; executable.input.required = mode.input.value === "reuse" && !locked; };
  mode.input.addEventListener("change", (event) => { event.stopPropagation(); state.designEdits = collect(); if (mode.input.value === "create") { delete state.designEdits.evaluation.evaluation_id; delete state.designEdits.evaluation.candidate_id; } state.designDirty = true; delete page.dataset.design; render(); });
  existing.input.addEventListener("change", (event) => { event.stopPropagation(); state.designEdits = collect(); const selected = native.find((item) => `native:${item.id}` === existing.input.value); const reviewed = frozen.find((item) => `frozen:${item.evaluation_id}` === existing.input.value); delete state.designEdits.evaluation.candidate_id; delete state.designEdits.evaluation.evaluation_id; if (selected) Object.assign(state.designEdits.evaluation, {candidate_id: selected.id, framework: selected.framework, command: selected.command, entrypoint: selected.entrypoint}); if (reviewed) { state.designEdits.evaluation.evaluation_id = reviewed.evaluation_id; if (reviewed.scoring) { state.designEdits.metrics = reviewed.scoring.metrics; state.designEdits.behaviors = reviewed.scoring.behaviors; state.designEdits.scoring = {mode: reviewed.scoring.mode, ...(reviewed.scoring.primary ? {primary: reviewed.scoring.primary} : {}), ...(reviewed.scoring.custom_metric ? {custom_metric: reviewed.scoring.custom_metric} : {}), ...(reviewed.scoring.target != null ? {target: reviewed.scoring.target} : {})}; } } state.designDirty = true; delete page.dataset.design; render(); });
  evalUpdate(); evalSection.append(mode.wrapper, existing.wrapper, runnerFields); kit.form.append(evalSection);
  collect = () => {
    const metrics = {}; metricRows.forEach((row) => { const key = row.id.input.value.trim(); if (metrics[key]) throw new Error("Each metric needs a unique key."); metrics[key] = {direction: row.direction.input.value, unit: row.unit.input.value.trim(), aggregation: row.aggregation.input.value, missing: row.missing.input.value, scale: Number(row.scale.input.value), weight: Number(row.weight.input.value), description: row.description.input.value.trim(), evidence: textLines(row.sources.input.value), prerequisites: textLines(row.needs.input.value)}; });
    const behaviors = behaviorRows.map((row) => ({id: row.id, description: row.description.input.value.trim(), required: row.required.checked, ...(row.kind.input.value === "metric" ? {metric: row.metric.input.value, op: row.op.input.value, bound: Number(row.bound.input.value)} : {check: row.check.input.value.trim()}), ...(row.rubric.input.value.trim() ? {rubric: row.rubric.input.value.trim()} : {}), evidence: textLines(row.sources.input.value), prerequisites: textLines(row.needs.input.value)}));
    const selected = existing.input.value; const reuse = mode.input.value === "reuse";
    return {background: background.input.value.trim(), evidence: textLines(evidence.input.value), behaviors: locked ? frozenDefinition.behaviors : behaviors, metrics: locked ? frozenDefinition.metrics : metrics, scoring: locked ? Object.fromEntries(Object.entries(frozenDefinition).filter(([key]) => ["mode", "primary", "custom_metric", "target"].includes(key))) : {mode: scoreMode.input.value, ...(scoreMode.input.value === "primary" ? {primary: primary.input.value} : scoreMode.input.value === "custom" ? {custom_metric: primary.input.value} : {}), ...(scoreTarget.input.value !== "" ? {target: Number(scoreTarget.input.value)} : {})}, evaluation: locked ? {mode: "reuse", evaluation_id: selectedFrozen.evaluation_id} : {mode: mode.input.value, framework: framework.input.value, entrypoint: entrypoint.input.value.trim(), ...(executable.input.value.trim() ? {command: {argv: [executable.input.value.trim(), ...textLines(args.input.value)], cwd: cwd.input.value.trim()}} : {}), scorer: scorer.input.value.trim(), output_mapping: Object.fromEntries(mappingRows.filter(({item}) => item.input.value.trim()).map(({row, item}) => [row.id.input.value.trim(), item.input.value.trim()])), ...(dataset.input.value ? {dataset_snapshot_id: dataset.input.value} : {}), ...(reuse && selected.startsWith("native:") ? {candidate_id: selected.slice(7)} : {}), ...(reuse && selected.startsWith("frozen:") ? {evaluation_id: selected.slice(7)} : {})}};
  };
  const accept = button("Accept plan", async () => { if (state.designDirty) throw new Error("Save your changes before accepting this plan."); await api(designAPI("/accept", projectId, agentId, focusId), "POST", {expected_revision: saved.revision}); if (context === designContext()) { await loadDesign(true); await loadOverview(); notice("Plan accepted. You can now prepare or reuse the evaluator."); } }, "");
  accept.disabled = !saved || saved.state === "accepted" || state.designDirty;
  const continuePlan = button(selectedFrozen ? "Run baseline" : "Prepare evaluator", () => launch(selectedFrozen ? "baseline" : "eval", {evaluation_id: selectedFrozen?.evaluation_id}, true), "");
  const markDirty = () => { state.designDirty = true; accept.disabled = true; continuePlan.disabled = true; try { state.designEdits = collect(); } catch { /* Save reports conflicting metric keys. */ } };
  kit.form.addEventListener("input", markDirty); kit.form.addEventListener("change", markDirty);
  kit.finish(); kit.form.querySelector(".form-footer").append(accept);
  if (saved?.state === "accepted" && !state.designDirty) page.append(section("Continue with the accepted plan", actions(continuePlan, quiet("Review existing measurement", measurementForm))));
}
function focusReadiness() { return state.overview.readiness; }
function readinessPanel() {
  const readiness = focusReadiness(); const box = el("section", "section readiness-panel"); box.append(el("h2", "", "Measurement readiness"));
  [["evaluation", "Reviewed evaluation", () => { $("dialog").close(); navigate("design"); }, "Review measurement plan"], ["baseline", "Measured baseline", () => launch("baseline"), "Run baseline"], ["fix", "Objective and guardrails", () => measurementForm(), "Review plan"]].forEach(([key, label, handler, action]) => { const item = readiness[key]; const row = el("div", "readiness-row"); const text = el("div"); text.append(el("strong", "", label), el("p", "small muted", item.ready ? item.reason || "Ready for this focus" : item.reason || "Preparation is required")); row.append(el("span", `check-symbol ${item.ready ? "done" : ""}`, item.ready ? "✓" : "○"), text, item.ready ? badge("ready") : quiet(action, handler)); box.append(row); }); return box;
}
function measurementForm() {
  if (!currentFocus()) return focusForm();
  const projectId = state.projectId; const agentId = state.applicationAgentId; const focus = currentFocus(); const body = openDialog("Measurement plan", focus.name || focus.goal);
  const evals = array(state.overview.evaluations).filter((item) => item.state === "frozen");
  if (!evals.length) { body.append(empty("No reviewed evaluation", button("Prepare evaluation", () => launch("eval"), ""))); return; }
  const evaluation = field("Frozen evaluation", {choices: evals.map((item) => [resultId("eval", item), item.goal || item.name || resultId("eval", item)]), value: focusMeasurement(focus).evaluation_id || resultId("eval", evals[0]), required: true}); const baseline = field("Reference baseline", {choices: [["", "Measure a baseline next"]]}); const metric = field("Focus metric", {choices: [["", "Use the evaluator's accepted primary metric"]]});
  const update = () => { baseline.input.replaceChildren(new Option("Measure a baseline next", "")); array(state.overview.baselines).filter((item) => item.state === "completed" && item.evaluation_id === evaluation.input.value).forEach((item) => baseline.input.append(new Option(`${item.branch || "Baseline"} · ${String(item.source_revision || item.baseline_id).slice(0, 12)}`, item.baseline_id))); baseline.input.value = focusMeasurement(focus).baseline_id || ""; const selected = evals.find((item) => item.evaluation_id === evaluation.input.value); metric.input.replaceChildren(new Option("Use the evaluator's accepted primary metric", "")); Object.keys(selected?.metrics || {}).forEach((name) => metric.input.append(new Option(name, name))); if (focusMeasurement(focus).primary_metric) metric.input.value = focusMeasurement(focus).primary_metric; }; evaluation.input.addEventListener("change", update); update();
  const extra = el("details"); extra.append(el("summary", "", "Add an explicit required guardrail")); const enabled = document.createElement("input"); enabled.type = "checkbox"; const toggle = el("label", "checkbox-row"); toggle.append(enabled, document.createTextNode("Require an additional metric threshold")); const guardMetric = field("Guardrail metric", {value: focusMeasurement(focus).primary_metric || "", placeholder: "task_success"}); const relation = field("Required result", {choices: [["gte", "At least"], ["lte", "At most"]]}); const bound = field("Guardrail bound", {type: "number", value: 0}); bound.input.step = "any"; const reference = field("Compare against", {choices: [["baseline_delta", "Change from the reference baseline"], ["absolute", "An absolute value"], ["baseline_ratio", "A ratio of the reference baseline"]]}); const guardFields = el("div", "form-grid"); guardFields.append(guardMetric.wrapper, relation.wrapper, bound.wrapper, reference.wrapper); guardFields.hidden = true; enabled.addEventListener("change", () => { guardFields.hidden = !enabled.checked; guardMetric.input.required = enabled.checked; bound.input.required = enabled.checked; }); extra.append(toggle, guardFields);
  const kit = formKit(body, "Save measurement plan", async () => { const payload = {evaluation_id: evaluation.input.value, ...(focus.revision != null ? {expected_revision: focus.revision} : {})}; if (enabled.checked) payload.guardrails = [...array(focusMeasurement(focus).guardrails).map(({metric, op, bound, reference}) => ({metric, op, bound, reference})), {metric: guardMetric.input.value.trim(), op: relation.input.value, bound: Number(bound.input.value), reference: reference.input.value}]; if (baseline.input.value) payload.baseline_id = baseline.input.value; if (metric.input.value) payload.primary_metric = metric.input.value; await api(applicationAPI(`/focuses/${encodeURIComponent(focus.id)}/measurement`, agentId, projectId), "POST", payload); $("dialog").close(); if (state.projectId === projectId && state.applicationAgentId === agentId) await loadOverview(); notice("Measurement plan saved."); }); kit.form.append(evaluation.wrapper, baseline.wrapper, metric.wrapper, extra); kit.finish();
}
async function loadMetrics() {
  if (!state.applicationAgentId) return;
  const projectId = state.projectId; const agentId = state.applicationAgentId; const result = await api(applicationAPI("/metrics", agentId, projectId));
  if (projectId !== state.projectId || agentId !== state.applicationAgentId) return; state.metrics = result; if (state.view === "metrics") render();
}
function renderMetrics(page) {
  page.append(heading("Metrics and guardrails", button("Add focus", () => focusForm())));
  if (!state.metrics) { page.append(el("p", "loading-evidence", "Loading retained measurements…")); return; }
  const guards = array(state.metrics.guardrails); const list = el("div", "record-list"); guards.forEach((guard) => { const row = el("article", "record"); const body = el("div", "record-main"); body.append(el("h3", "", guard.name || guard.metric || guard.id), el("p", "record-description", `${guard.focus_name || state.focuses.find((item) => item.id === guard.focus_id)?.name || "Saved focus"}${guard.threshold != null ? ` · required ${guard.op || ""} ${numeric(guard.threshold)}` : ""}${guard.value != null ? ` · observed ${numeric(guard.value)}` : ""}`)); row.append(body, badge(guard.state || "unmeasured")); list.append(row); }); page.append(section("Accepted guardrails", guards.length ? list : empty("No accepted guardrails")));
  const metrics = array(state.metrics.metrics); metrics.forEach((metric) => { const sectionBody = el("div", "card"); sectionBody.append(el("p", "small muted", `${metric.unit || "Unit not specified"} · ${readable(metric.direction)} · evaluator ${metric.evaluator_id || "unassigned"} · execution ${metric.execution_digest ? metric.execution_digest.slice(0, 12) : "unmeasured"}`)); const table = el("table", "comparison-table"); table.setAttribute("aria-label", `${metric.name || metric.id} history`); const header = el("tr"); ["Measured", "Revision", "Value", "Evidence"].forEach((text) => header.append(el("th", "", text))); table.append(header); array(metric.measurements).forEach((item) => { const row = el("tr"); row.append(el("td", "", date(item.created_at)), el("td", "", String(item.source_revision || "Unknown").slice(0, 12)), el("td", "", numeric(item.value)), el("td", "", readable(item.state || "unmeasured"))); if (item.baseline_id) row.lastChild.append(quiet("Inspect", () => detail("baseline", {baseline_id: item.baseline_id}))); else if (item.run_id) row.lastChild.append(quiet("Inspect", () => detail("fix", {run_id: item.run_id}))); table.append(row); }); sectionBody.append(table); page.append(section(metric.name || metric.id, sectionBody)); }); if (!metrics.length) page.append(el("p", "info-box", "Metrics appear after compatible baseline measurements. Missing evidence remains unmeasured.")); array(state.metrics.limitations).forEach((text) => page.append(el("p", "info-box", text)));
}
function renderTraces(page) {
  page.append(heading("Trace evidence", button("Find failures", () => focusForm("recent_traces")), button("Import traces", () => importFlow("traces"), "")));
  const traces = array(state.overview.traces); page.append(traces.length ? records(traces, "traces") : empty("No trace evidence", button("Import trace evidence", () => importFlow("traces"), "")));
}
function modelField(agentInput, label, models = {}) {
  const item = field(label, {choices: [["", "Coding agent default"]]}); let sequence = 0;
  const update = async () => { const request = ++sequence; const host = agentInput.value; const saved = models[host] || ""; const replacement = field(label, host === "codex" ? {choices: [["", "Codex default"]], hint: "Loading supported OpenAI models from your Codex connection…"} : {value: saved, placeholder: "Claude default", hint: "Use a model supported by your Claude connection."}); item.wrapper.replaceChildren(...replacement.wrapper.childNodes); item.input = replacement.input; if (host !== "codex") return;
    try { const result = await api("/api/agents/codex/models"); if (sequence !== request) return; const models = array(result.models); item.input.replaceChildren(new Option("Codex default", "")); models.forEach((model) => item.input.append(new Option(model.name || model.id, model.id))); item.input.value = models.some((model) => model.id === saved) ? saved : result.default_model && models.some((model) => model.id === result.default_model) ? result.default_model : ""; const hint = item.wrapper.querySelector(".field-hint"); const describe = () => { hint.textContent = models.find((model) => model.id === item.input.value)?.description || "Supported models reported by your local Codex connection. The actual model is retained with task evidence."; }; item.input.addEventListener("change", describe); describe(); } catch (error) { if (sequence === request) item.wrapper.querySelector(".field-hint").textContent = `${error.message} Codex default is still available.`; }
  }; agentInput.addEventListener("change", () => update().catch(showError)); update().catch(showError); return item;
}
function records(items, kind) {
  const list = el("div", "record-list");
  items.forEach((item) => { const row = el("article", "record"); const main = el("div", "record-main"); const title = item.goal || item.title || item.name || `${readable(kind)} ${String(idOf(item)).slice(-8)}`; const trigger = button(title, () => detail(kind, item), "record-title"); main.append(trigger); const meta = el("div", "record-meta"); meta.append(badge(item.state || item.status || "saved"), el("span", "", date(item.created_at)), el("span", "", item.branch || item.provider || item.mode || "")); main.append(meta); if (item.pending_action || item.next_action) main.append(el("p", "record-description", item.pending_action || item.next_action)); row.append(main, quiet("Inspect →", () => detail(kind, item))); list.append(row); }); return list;
}
function jobList(jobs, crossProject = false) { const list = el("div", "record-list"); jobs.forEach((job) => { const row = el("article", "record"); const body = el("div", "record-main"); body.append(button(job.goal || readable(job.kind), async () => { if (job.project_id !== state.projectId) await selectProject(job.project_id, null, job.application_agent_id || null); else if (job.application_agent_id && job.application_agent_id !== state.applicationAgentId) await selectApplicationAgent(job.application_agent_id, job.focus_id); state.selectedJob = idOf(job); updateURL(); showAgent(); renderAgent(); if ($("dialog").open) $("dialog").close(); }, "record-title"), el("div", "record-meta", crossProject ? state.projects.find((item) => item.id === job.project_id)?.name || "Project" : readable(job.kind))); row.append(body, badge(job.state)); list.append(row); }); return list; }
function issueList(issues) { const list = el("div", "record-list"); issues.forEach((issue) => { const row = el("article", "record"); const body = el("div", "record-main"); body.append(el("h3", "", issue.title || issue.summary || "Saved issue"), el("p", "record-description", issue.description || issue.impact || "")); const meta = el("div", "record-meta"); meta.append(badge(issue.severity || issue.status || "open"), el("span", "", issue.confidence ? `${readable(issue.confidence)} confidence` : "")); body.append(meta); row.append(body, actions(quiet("Create eval", () => launch("eval", {issue_id: issue.issue_id || issue.id, audit_id: issue.audit_id || issue.latest_audit_id || issue.audit_ids?.at(-1), goal: issue.title})), button("Fix issue", () => launch("fix", {issue_id: issue.issue_id || issue.id, audit_id: issue.audit_id || issue.latest_audit_id || issue.audit_ids?.at(-1), goal: issue.title})))); list.append(row); }); return list; }
function renderEval(page) {
  const tab = ["evaluations", "datasets", "baselines"].includes(state.tab) ? state.tab : "evaluations";
  page.append(heading("Eval", button("Import dataset", () => importFlow("dataset")), button("Prepare eval", () => launch("eval"), "")), tabs([["evaluations", "Evaluations"], ["datasets", "Datasets"], ["baselines", "Baseline history"]], tab, changeTab));
  if (tab === "evaluations") { const items = array(state.overview.evaluations); page.append(items.length ? records(items, "eval") : empty("No evaluations", button("Prepare an evaluation", () => launch("eval"), ""))); }
  if (tab === "datasets") { const items = array(state.overview.datasets); if (!items.length) page.append(empty("No datasets", button("Import a dataset", () => importFlow("dataset"), ""))); else { const list = el("div", "record-list"); items.forEach((item) => { const row = el("div", "record"); const body = el("div", "record-main"); body.append(button(item.name || item.dataset_name || `Dataset ${String(idOf(item)).slice(-8)}`, () => detail("dataset", item), "record-title"), el("p", "record-description", `${item.count ?? item.item_count ?? item.items?.length ?? "Unknown"} cases · ${item.provider || item.provenance?.provider || "Imported"} · ${readable(item.completeness?.state || item.completeness || "saved")}`)); row.append(body, datasetActions(item)); list.append(row); }); page.append(list); } }
  if (tab === "baselines") { const items = array(state.overview.baselines); if (!items.length) page.append(section("Measurements", empty("No baselines", button("View evaluations", () => changeTab("evaluations"))))); else { const list = el("div", "section two-column"); items.forEach((item) => list.append(baselineCard(item))); page.append(list); } }
}
function baselineCard(item) { const card = el("article", "card"); const head = el("div", "section-heading"); head.append(el("h3", "", item.branch || "Saved baseline"), badge(item.state)); card.append(head); const metrics = el("div", "metric-pair"); [["Fixed benchmark", item.benchmark_score?.value ?? item.score?.value], ["Recent traces", item.recent_traces?.score?.value ?? item.recent_traces?.score]].forEach(([label, value]) => { const metric = el("div"); metric.append(el("div", "metric-label", label), el("div", "metric-value", typeof value === "number" ? value.toLocaleString(undefined, {maximumFractionDigits: 3}) : "Unmeasured")); metrics.append(metric); }); card.append(metrics, el("p", "small muted", `${String(item.source_revision || item.revision || "Revision unavailable").slice(0, 12)} · ${date(item.created_at)}`), actions(quiet("Inspect evidence", () => detail("baseline", item)), button("Rerun baseline", () => launch("baseline", {baseline_id: resultId("baseline", item), evaluation_id: item.evaluation_id, profile: item.profile_name})), button("Start fix", () => launch("fix", {baseline_id: resultId("baseline", item), evaluation_id: item.evaluation_id, profile: item.profile_name})))); if (item.state === "completed") card.querySelector(".actions").append(quiet("Compare", () => compareBaselines(item))); return card; }
function renderFix(page) { page.append(heading("Fix", button("Start fix", () => launch("fix"), ""))); const jobs = state.jobs.filter((job) => job.kind === "fix" && !terminalStates.has(job.state)); if (jobs.length) page.append(section("In progress", jobList(jobs))); const items = array(state.overview.runs); page.append(items.length ? records(items, "fix") : empty("No fixes", button("Start a measured fix", () => launch("fix"), ""))); }

function compareBaselines(left) {
  const projectId = state.projectId; const leftId = resultId("baseline", left);
  const body = openDialog("Compare baseline measurements", "Fixed benchmark"); const generation = dialogGeneration;
  const describe = (item) => `${item.branch || "Baseline"} · ${String(item.source_revision || resultId("baseline", item)).slice(0, 12)}${item.created_at ? ` · ${date(item.created_at)}` : ""}`;
  body.append(el("p", "", `Starting measurement: ${describe(left)}. Compatibility is verified from retained evidence before a comparison is shown.`));
  const choices = array(state.overview.baselines).filter((item) => item.state === "completed" && resultId("baseline", item) !== leftId);
  if (!choices.length) { body.append(el("p", "info-box", "Save another completed baseline to compare measurements. Both need the same frozen evaluator, scoring and execution settings.")); return; }
  const right = field("Compare with", {choices: choices.map((item) => [resultId("baseline", item), describe(item)]), required: true});
  const results = el("div"); results.setAttribute("aria-live", "polite");
  const kit = formKit(body, "Compare measurements", async () => {
    results.replaceChildren();
    const value = await api(projectAPI(`/baselines/${encodeURIComponent(leftId)}/compare/${encodeURIComponent(right.input.value)}`, projectId));
    if (!$("dialog").open || projectId !== state.projectId || generation !== dialogGeneration) return;
    if (value.compatible !== true) throw new Error("These measurements are not compatible. Choose baselines with the same frozen evaluator and execution settings.");
    const score = value.benchmark || {};
    results.append(el("h3", "", "Fixed benchmark"), el("p", "small muted", "Higher scores are better. Change is comparison minus starting measurement."));
    const table = el("table", "comparison-table"); table.setAttribute("aria-label", "Fixed benchmark comparison");
    const head = el("thead"); const header = el("tr"); ["Evidence", "Starting", "Comparison", "Change"].forEach((label) => header.append(el("th", "", label))); head.append(header);
    const rows = el("tbody"); const scoreRow = el("tr"); ["Score", numeric(score.left), numeric(score.right), numeric(score.delta)].forEach((text) => scoreRow.append(el("td", "", text))); rows.append(scoreRow);
    const eligibility = (value) => value === true ? "Passed" : value === false ? "Failed" : "Unknown";
    const gates = el("tr"); ["Required gates", eligibility(score.left_eligible), eligibility(score.right_eligible), "—"].forEach((text) => gates.append(el("td", "", text))); rows.append(gates); table.append(head, rows); results.append(table);
    if (score.state !== "measured") results.append(el("p", "warning", "One or both benchmark scores are unavailable or unscored. No numeric change can be established."));
    if (score.left_eligible === false || score.right_eligible === false) results.append(el("p", "warning", "A measurement failed required behavior gates. Its score does not establish an eligible improvement."));
    if (value.compatibility?.same_source_revision) results.append(el("p", "info-box", "Both measurements use the same application revision."));
    const recent = value.recent_traces || {}; const recentCards = el("div", "comparison-grid");
    [["Starting", recent.left], ["Comparison", recent.right]].forEach(([label, trace]) => { const card = el("div", "card"); card.append(el("h3", "", label), badge(trace?.state || "unavailable"), el("p", "", `Recent trace score: ${numeric(trace?.score?.value ?? trace?.score)}`)); if (trace?.count != null) card.append(el("p", "small", `${trace.count} traces`)); if (trace?.window) card.append(evidenceBlock("Trace window", trace.window)); if (trace?.completeness || trace?.alignment) card.append(evidenceBlock("Coverage and alignment", {completeness: trace.completeness, alignment: trace.alignment})); recentCards.append(card); });
    results.append(section("Recent traces · separate populations", recentCards));
    const limits = el("ul", "gate-list"); [...array(value.limitations), ...array(recent.limitations)].forEach((text) => limits.append(el("li", "", text))); results.append(limits);
  });
  right.input.addEventListener("change", () => { results.replaceChildren(); kit.error.textContent = ""; }); kit.form.append(right.wrapper); kit.finish(); body.append(results);
}

let dialogGeneration = 0;
function openDialog(title) { dialogGeneration += 1; $("dialog").classList.remove("detail-wide"); $("dialog-title").textContent = title; const body = $("dialog-content"); body.replaceChildren(); if (!$("dialog").open) $("dialog").showModal(); return body; }
let fieldCounter = 0;
function field(label, options = {}) {
  const wrapper = el("div", "field"); const id = `field-${++fieldCounter}`; const control = document.createElement(options.type === "textarea" ? "textarea" : options.choices ? "select" : "input");
  control.id = id; control.name = id; if (control.tagName === "INPUT") control.type = options.type || "text"; if (options.choices) options.choices.forEach(([value, text]) => control.append(new Option(text, value))); if (options.value != null) control.value = options.value; if (options.placeholder) control.placeholder = options.placeholder; if (options.required) control.required = true; if (options.min != null) control.min = options.min; if (options.max != null) control.max = options.max; if (control.tagName === "TEXTAREA") control.rows = options.rows || 3; if (options.type === "password") control.autocomplete = "new-password";
  const text = el("label", "", label); text.htmlFor = id; wrapper.append(text, control); if (options.hint) { const hint = el("p", "field-hint", options.hint); hint.id = `${id}-hint`; control.setAttribute("aria-describedby", hint.id); wrapper.append(hint); } return {wrapper, input: control};
}
function formKit(parent, submitText, submit) { const form = el("form", "form"); const error = el("p", "form-error"); error.setAttribute("role", "alert"); const save = el("button", "button", submitText); save.type = "submit"; const footer = el("div", "form-footer"); footer.append(save); const operationId = crypto.randomUUID(); form.addEventListener("submit", async (event) => { event.preventDefault(); if (save.disabled) return; save.disabled = true; error.textContent = ""; try { await submit(operationId); } catch (failure) { error.textContent = failure.message; } finally { save.disabled = false; } }); parent.append(form); return {form, save, error, finish: () => form.append(error, footer)}; }
function addProject() {
  const body = openDialog("Add a project", "Your workspace"); const source = field("Project source", {choices: [["local", "Existing local directory"], ["github", "Clone a GitHub repository"]]}); const repository = field("GitHub repository URL", {type: "url", placeholder: "https://github.com/owner/repository"}); const path = field("Project directory", {required: true, placeholder: "/Users/you/projects/your-agent"});
  const hint = el("p", "info-box", "Local Git credentials are used for GitHub access. Cloning reads the repository; publication remains a separate action.");
  const update = () => { repository.wrapper.hidden = source.input.value !== "github"; repository.input.required = source.input.value === "github"; hint.hidden = source.input.value !== "github"; }; source.input.addEventListener("change", update); update();
  const kit = formKit(body, "Add project", async () => { const result = await api(source.input.value === "github" ? "/api/projects/clone" : "/api/projects", "POST", {path: path.input.value.trim(), ...(source.input.value === "github" ? {repository: repository.input.value.trim()} : {})}); await refreshProjects(); $("dialog").close(); await selectProject(result.id); notice("Project added. Discover or register its application agents."); }); kit.form.append(source.wrapper, repository.wrapper, path.wrapper, hint); kit.finish();
}

function agentReadiness(id) {
  const agent = state.agents.find((item) => item.id === id);
  if (!agent || agent.available === false) return {state: "not available", message: agent?.message || agent?.reason || (id === "codex" ? "Install Codex and sign in with codex login." : "Install agentagon[claude] to use Claude.")};
  if (id === "codex" && agent?.authenticated === false) return {state: "needs sign in", message: "Run codex login in your terminal, then refresh the app."};
  if (id === "claude" && state.claudeKeyConfigured === false) return {state: "needs credentials", message: "Configure a Claude API key in Settings → Coding agents."};
  return {state: "available", message: "This local app can start and manage sessions."};
}
function launch(kind, preset = {}, acceptedPlan = false) {
  if (!state.projectId) return addProject();
  if (!applicationAgent()) return navigate("agents");
  if (!currentFocus()) return focusForm();
  const projectId = state.projectId; const applicationAgentId = state.applicationAgentId; const focusId = state.focusId; const focus = currentFocus();
  preset = {goal: focus.goal, evaluation_id: focusMeasurement(focus).evaluation_id || undefined, ...(kind === "fix" && focusMeasurement(focus).baseline_id ? {baseline_id: focusMeasurement(focus).baseline_id} : {}), ...(kind === "fix" ? {permitted_paths: applicationAgent().code_scopes || []} : {}), ...(focus.source?.trace_snapshot_id ? {trace_snapshot_id: focus.source.trace_snapshot_id} : {}), ...preset};
  if (kind === "fix" && !focusReadiness().fix.ready) { const readiness = focusReadiness(); const body = openDialog("Get ready to improve", applicationAgent().name); body.append(el("p", "", readiness.fix.reason || "The active objective and every retained guardrail need a reviewed evaluator and baseline."), readinessPanel(), actions(button("Review measurement plan", measurementForm), button(readiness.evaluation.ready ? "Run baseline" : "Prepare evaluation", () => launch(readiness.evaluation.ready ? "baseline" : "eval"), ""))); return; }
  const body = openDialog({design: "Propose a measurement plan", eval: "Prepare an evaluation", fix: "Start a fix", baseline: preset.baseline_id ? "Rerun baseline" : "Run a baseline"}[kind], project()?.name);
  const goal = field(kind === "eval" ? "What should your agent do correctly?" : "Goal", {type: "textarea", required: kind !== "baseline", value: preset.goal || "", placeholder: "Known tool names select the matching tool. Unknown names return a validation error."});
  const defaultAgent = state.agentSettings.default_agent || "codex";
  const agent = field("Coding agent", {choices: [["codex", "Codex"], ["claude", "Claude"]], value: defaultAgent});
  const model = modelField(agent.input, "Model", state.agentSettings.models);
  const agentFeedback = el("p", "warning");
  const codePaths = field("Permitted edit paths", {value: (preset.permitted_paths || []).join(", "), placeholder: "src/agent, tests"});
  const allEvals = array(state.overview?.evaluations);
  const evals = kind === "eval" ? allEvals : allEvals.filter((item) => item.state === "frozen");
  const evaluation = field("Evaluation", {choices: [["", kind === "baseline" ? "Choose a frozen evaluation" : kind === "eval" ? "Create or find a suitable evaluation" : "Let the agent prepare a suitable evaluation"], ...evals.map((item) => [idOf(item), item.goal || item.name || idOf(item)])], value: preset.evaluation_id || "", required: kind === "baseline" && !preset.baseline_id});
  if (preset.evaluation_id && !evals.some((item) => idOf(item) === preset.evaluation_id)) evaluation.input.append(new Option(preset.evaluation_id, preset.evaluation_id, true, true));
  // A saved baseline fixes evaluator identity; the server intentionally ignores replacements.
  if (preset.baseline_id || acceptedPlan) evaluation.input.disabled = true;
  const profiles = state.overview?.settings?.profiles || {};
  const profileNames = Object.keys(profiles);
  const initialProfile = preset.profile || (profileNames.length === 1 ? profileNames[0] : "");
  const profile = field("Execution profile", {choices: [["", "Choose a saved profile"], ...profileNames.map((name) => [name, name])], value: initialProfile, required: kind !== "design"});
  const engine = field("Optimization engine", {choices: [["omni", "Omni (default)"], ["gepa", "GEPA"], ["autoresearch", "AutoResearch"], ["meta_harness", "Meta-Harness"]], value: preset.engine || "omni"});
  const finalists = field("Finalists to verify", {type: "number", min: 1, max: 10, value: preset.finalist_count || 3, hint: "Verify up to this many candidates against the accepted goals. Required verification uses the shared budget."});
  const hostConcurrency = field("Concurrent coding sessions", {type: "number", min: 1, max: 10, value: preset.host_concurrency || state.agentSettings.concurrency || 1, hint: "Session capacity is separate from the number of finalists. The app also enforces its global capacity."});
  const limits = el("div", "form-grid");
  const trials = field("Maximum evaluation runs", {type: "number", min: 1, required: true, value: 24});
  const seconds = field("Total time limit (seconds)", {type: "number", min: 1, required: true, value: 1800});
  const timeout = field("Per-trial timeout (seconds)", {type: "number", min: 1, required: true, value: 60});
  const profileLimits = () => {
    const saved = profiles[profile.input.value]?.limits || {};
    [[trials, "max_trials", 24], [seconds, "max_elapsed_seconds", 1800], [timeout, "trial_timeout_seconds", 60]].forEach(([item, key, fallback]) => { item.input.max = Math.min(saved[key] || 86400, 86400); item.input.value = Math.min(saved[key] || fallback, 86400); });
  };
  profile.input.addEventListener("change", profileLimits); profileLimits();
  const kit = formKit(body, kind === "design" ? "Propose plan" : kind === "baseline" ? "Start measurement" : `Start ${kind}`, async (operationId) => {
    const options = Object.fromEntries(Object.entries(preset).filter(([, value]) => value !== undefined)); delete options.goal;
    if (kind !== "design") { options.profile = profile.input.value; options.max_trials = Number(trials.input.value); options.max_elapsed_seconds = Number(seconds.input.value); options.trial_timeout_seconds = Number(timeout.input.value); if (evaluation.input.value) options.evaluation_id = evaluation.input.value; else delete options.evaluation_id; }
    if (kind === "fix") { options.permitted_paths = paths(codePaths.input.value); options.engine = engine.input.value; options.finalist_count = Number(finalists.input.value); options.host_concurrency = Number(hostConcurrency.input.value); }
    const payload = {operation_id: operationId, kind, application_agent_id: applicationAgentId, focus_id: focusId, goal: goal.input.value.trim() || (preset.baseline_id ? "Rerun saved baseline" : "Measure selected evaluation"), agent: agent.input.value, options};
    payload.model = model.input.value.trim();
    const job = await api(projectAPI("/jobs", projectId), "POST", payload);
    $("dialog").close();
    if (state.projectId === projectId && state.applicationAgentId === applicationAgentId) { state.jobs.unshift(job); state.selectedJob = idOf(job); updateURL(); showAgent(); renderAgent(); await loadOverview(); }
    notice("Task started.");
  });
  const refreshAgent = () => { const readiness = agentReadiness(agent.input.value); agentFeedback.textContent = readiness.message; agentFeedback.hidden = readiness.state === "available"; kit.save.disabled = readiness.state !== "available"; };
  agent.input.addEventListener("change", refreshAgent); refreshAgent();
  kit.form.append(el("p", "info-box", `${applicationAgent().name} · ${focus.name || focus.goal}`), goal.wrapper); if (acceptedPlan) kit.form.append(el("p", "info-box", "This task uses the evaluator choice, behaviors and metrics in your accepted measurement plan. Save and accept a revised plan to change them.")); const agentRow = el("div", "form-grid"); agentRow.append(agent.wrapper, model.wrapper); kit.form.append(agentRow, agentFeedback);
  if (kind !== "design") { const row = el("div", "form-grid"); row.append(evaluation.wrapper, profile.wrapper); kit.form.append(row); if (!profileNames.length) kit.form.append(el("p", "warning", "Save an execution profile in Settings → Execution before starting measured work.")); limits.append(trials.wrapper, seconds.wrapper, timeout.wrapper); kit.form.append(limits, el("p", "info-box", "Limits come from the selected execution profile and include failed attempts and retries. You can lower them for this task. Execution needs clean committed source and trusted expectations.")); }
  if (kind === "fix") { const advanced = el("details"); advanced.append(el("summary", "", "Additional options"), engine.wrapper, finalists.wrapper, hostConcurrency.wrapper); kit.form.append(codePaths.wrapper, advanced); }
  if (preset.issue_id || preset.dataset_snapshot_id) kit.form.append(el("p", "info-box", preset.issue_id ? `Linked issue: ${preset.issue_id}` : `Using dataset snapshot: ${preset.dataset_snapshot_id}`));
  kit.finish();
}
function datasetActions(item) {
  const partition = item.provenance?.dataset_partition;
  if (partition === "final_holdout") return el("p", "small muted", "Reserved final inputs · separate verification required");
  const split = item.split || array(state.overview.dataset_splits).find((record) => record.parameters?.source_snapshot_id === idOf(item));
  if (split) return actions(button("Create development eval", () => launch("eval", {dataset_snapshot_id: split.development_snapshot_id})), quiet("Export development dataset", () => exportDataset({...item, id: split.development_snapshot_id, snapshot_id: split.development_snapshot_id})), el("span", "small muted", `${split.counts.holdout} final cases reserved`));
  const controls = actions(button("Create eval", () => launch("eval", {dataset_snapshot_id: idOf(item)})), quiet("Export", () => exportDataset(item)), quiet("Publish to Braintrust", () => publishDataset(item)));
  if (!partition || partition === "unsplit") controls.append(quiet("Split dataset", () => splitDataset(item)));
  else controls.append(badge("development"));
  return controls;
}
function artifactLinks(urls) {
  const links = actions(); Object.entries(urls || {}).forEach(([name, path]) => { if (typeof path !== "string") return; const url = new URL(path, location.origin); if (url.origin !== location.origin || !url.pathname.startsWith("/api/projects/")) return; const link = el("a", "button secondary", `Download ${readable(name)}`); link.href = url.href; link.setAttribute("download", ""); links.append(link); }); return links;
}
function exportDataset(item) {
  const projectId = state.projectId; const body = openDialog("Export a native dataset", item.name || "Dataset snapshot"); const generation = dialogGeneration;
  body.append(el("p", "", "Create a local dataset and runnable helper for your evaluation framework. Review missing expectations, scorer wiring and dependencies before execution."));
  const framework = field("Export framework", {choices: [["braintrust", "Braintrust"], ["deepeval", "DeepEval"]]});
  const kit = formKit(body, "Create export", async () => {
    const result = await api(projectAPI(`/datasets/${encodeURIComponent(idOf(item))}/export`, projectId), "POST", {framework: framework.input.value});
    if (!$("dialog").open || projectId !== state.projectId || generation !== dialogGeneration) return;
    body.replaceChildren(badge(result.state), el("p", "", `${result.count} examples exported for ${readable(result.framework)}.`));
    if (result.missing_expectations) body.append(el("p", "warning", `${result.missing_expectations} examples have no accepted expectation. Observed outputs are not labels.`));
    array(result.limitations).forEach((limit) => body.append(el("p", "info-box", limit)));
    body.append(artifactLinks(result.artifact_urls), evidenceBlock("Export provenance", {snapshot_id: result.snapshot_id, snapshot_digest: result.snapshot_digest, digest: result.digest}));
  }); kit.form.append(framework.wrapper); kit.finish();
}
async function publishDataset(item) {
  const projectId = state.projectId; await refreshConnections();
  if (projectId !== state.projectId) return;
  const connections = state.connections.filter((connection) => connection.provider === "braintrust" && connection.project_id === projectId);
  const body = openDialog("Publish dataset to Braintrust", item.name || "Dataset snapshot"); const generation = dialogGeneration;
  if (!connections.length) { body.append(empty("Connect Braintrust first.", "Connect Braintrust to this project in Settings. You can export a local dataset without a connection.", button("Open connections", () => { $("dialog").close(); navigate("settings", "connections"); }))); return; }
  body.append(el("p", "", "Review the exact destination and rows before sending this dataset. Previewing prepares local evidence; publication is a separate action."));
  const connection = field("Braintrust connection", {choices: connections.map((value) => [value.id, value.name || "Braintrust"])}); const destination = field("Destination project", {required: true, value: connections[0].project || "", hint: "Uses this connection’s saved project. Choose a provider project in Settings → Connections if it is missing."}); destination.input.readOnly = true;
  connection.input.addEventListener("change", () => { destination.input.value = connections.find((value) => value.id === connection.input.value)?.project || ""; });
  const name = field("Dataset name", {required: true, value: item.name || "Agent examples", hint: "The preview adds an immutable snapshot suffix. It shows the final name before publication."});
  const kit = formKit(body, "Preview publication", async () => {
    const connectionId = connection.input.value;
    const preview = await api(projectAPI(`/datasets/${encodeURIComponent(idOf(item))}/publish-preview`, projectId), "POST", {connection_id: connectionId, project: destination.input.value, name: name.input.value.trim()});
    if (!$("dialog").open || projectId !== state.projectId || generation !== dialogGeneration) return;
    const review = openDialog("Review dataset publication", "Braintrust"); const reviewGeneration = dialogGeneration;
    review.append(el("p", "info-box", `${preview.count} examples → ${preview.destination.project} / ${preview.destination.name}`));
    if (preview.connection?.endpoint) review.append(el("p", "small muted", preview.connection.endpoint));
    if (preview.warning) review.append(el("p", "warning", preview.warning));
    if (preview.missing_expectations) review.append(el("p", "warning", `${preview.missing_expectations} examples have no accepted expectation. Those expected values will remain missing.`));
    review.append(evidenceBlock("Rows to publish", preview.events, true));
    const publish = formKit(review, "Publish reviewed dataset", async (operationId) => {
      const result = await api(projectAPI(`/datasets/${encodeURIComponent(idOf(item))}/publish`, projectId), "POST", {connection_id: connectionId, preview_id: preview.preview_id, operation_id: operationId});
      if (!$("dialog").open || projectId !== state.projectId || reviewGeneration !== dialogGeneration) return;
      review.replaceChildren(badge(result.state), el("p", "", `Published ${result.receipt.count} examples to ${result.receipt.project} / ${result.receipt.name}.`), evidenceBlock("Publication receipt", result.receipt, true));
    }); publish.form.append(el("p", "small muted", "This sends the reviewed input, output, metadata and available expected values to your connected Braintrust project.")); publish.finish();
  }); kit.form.append(connection.wrapper, destination.wrapper, name.wrapper); kit.finish();
}
function deriveDataset(item) {
  const projectId = state.projectId; const agentId = state.applicationAgentId;
  const body = openDialog("Create dataset from traces", applicationAgent()?.name);
  body.append(el("p", "", "Copy completed trace inputs into a local draft. Observed outputs remain separate from expected behavior; review a correctness rule before evaluating."));
  const cap = field("Maximum examples", {type: "number", value: 100, min: 1, max: 10000, required: true});
  const kit = formKit(body, "Create draft", async () => {
    const result = await api(projectAPI("/datasets/derive", projectId), "POST", {trace_snapshot_id: idOf(item), application_agent_id: agentId, selection: {cap: Number(cap.input.value)}});
    if (projectId !== state.projectId || agentId !== state.applicationAgentId) return;
    await loadOverview(); $("dialog").close(); navigate("eval", "datasets"); notice(`Created ${result.count} draft examples. Expectations still need review.`);
  }); kit.form.append(cap.wrapper); kit.finish();
}
function splitDataset(item) {
  const projectId = state.projectId; const agentId = state.applicationAgentId;
  const body = openDialog("Split development and final cases", item.name || "Dataset snapshot");
  body.append(el("p", "", "Keep related conversations and duplicate inputs together. This creates one immutable partition; later refreshes produce new snapshots."));
  const percent = field("Final partition (%)", {type: "number", value: 20, min: 1, max: 99, required: true});
  const group = field("Additional family metadata field (optional)", {placeholder: "task_family", hint: "Every case needs a conversation, session, thread or task-family identifier."});
  const kit = formKit(body, "Create split", async () => {
    const result = await api(projectAPI(`/datasets/${encodeURIComponent(idOf(item))}/split`, projectId), "POST", {holdout_fraction: Number(percent.input.value) / 100, ...(group.input.value.trim() ? {group_key: group.input.value.trim()} : {})});
    if (projectId !== state.projectId || agentId !== state.applicationAgentId) return;
    await loadOverview(); body.replaceChildren(el("p", "info-box", `${result.counts.development} development cases · ${result.counts.holdout} reserved final cases · ${result.counts.duplicates_removed} duplicates removed.`));
    if (result.counts.missing_expectations) body.append(el("p", "warning", `${result.counts.missing_expectations} cases still need accepted expectations or a reviewed correctness rule.`));
    body.append(el("p", "", "Create your evaluator from the development partition. Final cases are excluded from ordinary tasks and need a separate reviewed verification evaluator. A local host can access files, so this is not a sealed holdout."), actions(button("Create development eval", () => launch("eval", {dataset_snapshot_id: result.development_snapshot_id}), "")), evidenceBlock("Split provenance", result));
  }); kit.form.append(percent.wrapper, group.wrapper); kit.finish();
}
async function detail(kind, item) {
  const projectId = state.projectId; const body = openDialog(item.goal || item.title || item.name || readable(kind), "Saved evidence"); const generation = dialogGeneration; body.append(badge(item.state || item.status || "saved"));
  const placeholder = el("p", "muted small", "Loading evidence…"); body.append(placeholder);
  let record = item; try { const value = await api(projectAPI(`/results/${encodeURIComponent(kind)}/${encodeURIComponent(resultId(kind, item))}`, projectId)); record = value; } catch (error) { placeholder.textContent = `${error.message} Showing the saved summary.`; }
  if (!$("dialog").open || projectId !== state.projectId || generation !== dialogGeneration) return;
  if (placeholder.textContent === "Loading evidence…") placeholder.remove();
  const description = record.goal || record.summary || record.description; if (description) body.append(el("p", "record-description", description));
  const fields = el("dl", "details-grid"); ["state", "source_revision", "evaluator_digest", "evaluation_id", "created_at", "pending_action", "next_action"].forEach((key) => { if (record[key] != null) fields.append(el("dt", "", readable(key)), el("dd", "", record[key])); }); body.append(fields);
  if (Array.isArray(record.issues) && record.issues.length) body.append(section("Issues", issueList(record.issues.map((issue) => ({...issue, audit_id: record.audit_id || issue.audit_id})))));
  if (kind === "audit") renderAuditEvidence(body, record);
  if (kind === "eval") { renderEvaluationEvidence(body, record); if (record.state === "frozen") body.append(actions(button("Prepare local delivery", () => prepareDelivery("eval", idOf(item), projectId)))); }
  if (kind === "fix") renderFixEvidence(body, record, projectId);
  if (record.benchmark_score || record.recent_traces) body.append(baselineCard(record));
  const choices = actions(); if (kind === "eval") { if (record.state === "frozen") choices.append(button("Run baseline", () => launch("baseline", {evaluation_id: idOf(item)})), button("Start fix", () => launch("fix", {evaluation_id: idOf(item)}))); choices.append(button(record.state === "frozen" ? "Improve evaluation" : "Continue preparation", () => { const savedJob = state.jobs.find((job) => job.kind === "eval" && !terminalStates.has(job.state) && (job.workflow_ids?.evaluation_id === idOf(item) || job.result?.evaluation_id === idOf(item))); if (savedJob) { state.selectedJob = idOf(savedJob); updateURL(); $("dialog").close(); showAgent(); renderAgent(); } else launch("eval", {evaluation_id: idOf(item), goal: item.goal}); })); } if (kind === "dataset") choices.append(datasetActions(record)); if (kind === "traces") choices.append(button("Create dataset draft", () => deriveDataset(record)));
  body.append(choices);
  const evidence = el("details"); evidence.append(el("summary", "", "Full saved evidence"), el("pre", "", JSON.stringify(record, null, 2))); body.append(evidence);
}
function evidenceBlock(title, text, open = false) { const block = el("details"); block.open = open; block.append(el("summary", "", title), el("pre", "evidence-log", typeof text === "string" ? text : JSON.stringify(text, null, 2))); return block; }
function numeric(value) { return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 4}) : "Unmeasured"; }
function gateList(items) { const list = el("ul", "gate-list"); items.forEach((item) => { const passed = item.passed; const text = `${passed === true ? "✓" : passed === false ? "✕" : "○"} ${item.id || item.metric || item.name || "Check"}${item.actual != null ? ` · ${numeric(item.actual)} ${item.op || ""} ${numeric(item.threshold ?? item.bound)}` : ""} · ${passed === true ? "passed" : passed === false ? "failed" : "unknown"}`; list.append(el("li", "", text)); }); return list; }
function renderAuditEvidence(body, record) {
  if (record.coverage) { const summary = el("dl", "details-grid"); Object.entries(record.coverage).forEach(([key, value]) => { if (typeof value === "number") summary.append(el("dt", "", readable(key)), el("dd", "", value)); }); body.append(section("Evidence coverage", summary)); }
  const findings = array(record.ungrouped_findings || record.findings); if (findings.length) { const list = el("div", "record-list"); findings.forEach((finding) => { const row = el("article", "record"); const main = el("div", "record-main"); main.append(el("h3", "", finding.title || finding.summary || finding.signal || "Finding"), el("p", "record-description", finding.rationale || finding.description || "")); if (finding.evidence) main.append(evidenceBlock("Supporting evidence", finding.evidence)); row.append(main, badge(finding.severity || finding.status || "reviewed")); list.append(row); }); body.append(section("Findings", list)); }
  if (record.limits?.length) { const limits = el("ul", "gate-list"); record.limits.forEach((limit) => limits.append(el("li", "", typeof limit === "string" ? limit : limit.description || JSON.stringify(limit)))); body.append(section("Scope and limits", limits)); }
}
function renderEvaluationEvidence(body, record) {
  if (record.usage || record.budget) body.append(el("p", "info-box", `${record.usage?.trials ?? 0} / ${record.budget?.max_trials ?? "—"} preparation trials used · ${record.budget?.max_elapsed_seconds ?? "—"} seconds allowed`));
  if (record.coverage) body.append(section("Coverage", el("p", "small muted", `${readable(record.coverage.status)} · ${record.coverage.rationale || ""}`)));
  if (Object.keys(record.metrics || {}).length) { const metrics = el("div", "record-list"); Object.entries(record.metrics).forEach(([name, value]) => { const row = el("div", "record"); row.append(el("strong", "record-main", name), el("span", "small muted", `${readable(value.direction)} · ${value.unit || "unitless"}`)); metrics.append(row); }); body.append(section("Metrics", metrics)); }
  const trials = array(record.trials); if (trials.length) { const list = el("ol", "timeline-list"); trials.forEach((trial) => { const row = el("li"); const outcome = trial.state === "passed" ? trial.kind === "negative" ? "Deliberately incorrect behavior rejected" : trial.kind === "baseline" ? "Baseline expectations met" : "Correctness checks passed" : readable(trial.state); row.append(badge(trial.state), document.createTextNode(`${trial.case_id || trial.trial_id} · repetition ${(trial.repetition || 0) + 1}`), el("p", "small muted", outcome)); if (trial.error) row.append(el("p", "warning", trial.error)); list.append(row); }); body.append(section("Validation and sensitivity", list)); }
  if (record.metric_comparison_error) body.append(el("p", "warning", record.metric_comparison_error));
  array(record.metric_comparisons).forEach((item) => { const card = el("div", "card"); card.append(el("h3", "", item.metric), badge(item.passed ? "passed" : "failed"), el("p", "", `${item.better}: ${numeric(item.better_value)} · ${item.worse}: ${numeric(item.worse_value)} · improvement ${numeric(item.improvement)}; required separation ${numeric(item.min_delta)}`)); body.append(card); });
  body.append(section("Independent review", badge(record.review_verdict || "not recorded"))); if (record.review_branch) body.append(el("p", "small muted", `Evaluation branch: ${record.review_branch}`));
}
function behaviorOutcomes(items) {
  const list = el("ul", "gate-list"); array(items).forEach((item) => list.append(el("li", "", `${item.passed === true ? "✓" : item.passed === false ? "✕" : "○"} ${item.description || item.id} · ${item.required ? "required" : "informational"} · ${item.passed === true ? "passed" : item.passed === false ? "failed" : "unknown"}`))); return list;
}
function candidateVerification(candidate, suite) {
  const details = el("details", "candidate-verification"); details.append(el("summary", "", "Signals, behaviors and required goals"));
  const score = candidate.score || {};
  const components = array(score.components);
  if (components.length) { const table = el("table", "comparison-table"); table.setAttribute("aria-label", `${candidate.hypothesis || candidate.id} signals`); const head = el("tr"); ["Signal", "Value", "Direction / weight"].forEach((text) => head.append(el("th", "", text))); table.append(head); components.forEach((item) => { const row = el("tr"); row.append(el("td", "", item.id), el("td", "", `${numeric(item.value)}${item.unit ? ` ${item.unit}` : ""}`), el("td", "", `${item.direction === "min" ? "Lower is better" : item.direction === "max" ? "Higher is better" : "Unspecified"}${item.weight != null ? ` · weight ${numeric(item.weight)}` : ""}`)); table.append(row); }); details.append(table); }
  if (array(score.behaviors).length) details.append(el("h4", "", "Agreed behaviors"), behaviorOutcomes(score.behaviors));
  const finalist = suite?.finalists?.[candidate.id];
  if (suite && !finalist) details.append(el("p", "small muted", "No separate full-goal verification is recorded for this candidate."));
  if (finalist) {
    details.append(badge(finalist.state));
    array(suite.members).forEach((member) => {
      const result = array(finalist.result?.members).find((entry) => entry.focus_id === member.focus_id);
      const section = el("section", "candidate-goal"); section.append(el("h4", "", member.name), badge(result?.passed === true ? "passed" : result?.passed === false ? "failed" : "unmeasured"));
      if (!result) { section.append(el("p", "small muted", "Required measurements are incomplete.")); details.append(section); return; }
      const table = el("table", "comparison-table"); table.setAttribute("aria-label", `${candidate.hypothesis || candidate.id} · ${member.name}`); const head = el("tr"); ["Metric", "Reference", "Candidate"].forEach((text) => head.append(el("th", "", text))); table.append(head);
      Object.entries(member.metrics || {}).forEach(([metric, definition]) => { const row = el("tr"); row.append(el("td", "", `${metric}${definition.unit ? ` (${definition.unit})` : ""}`), el("td", "", numeric(result.reference?.[metric])), el("td", "", numeric(result.finalist?.[metric]))); table.append(row); });
      section.append(table, el("p", "small muted", `Agreed score: ${numeric(result.score?.value)}${result.primary ? ` · primary improvement ${result.improved ? "established" : "not established"}` : ""}`), behaviorOutcomes(result.score?.behaviors));
      const checks = [...array(result.checks), ...array(result.constraints), ...array(result.guardrails).map((guard) => ({...guard, name: guard.metric, id: `${guard.metric} · ${readable(guard.reference)}${guard.baseline ? ` · ${readable(guard.baseline)}` : ""}`}))];
      if (checks.length) section.append(gateList(checks)); details.append(section);
    });
  }
  if (!components.length && !array(score.behaviors).length && !suite) details.append(el("p", "small muted", "No additional signal or behavior evidence was recorded."));
  return details;
}
function renderFixEvidence(body, run, projectId) {
  $("dialog").classList.add("detail-wide"); const runId = run.run_id || idOf(run); const candidates = array(run.candidates); const baseline = candidates.find((item) => item.id === run.baseline_id); const result = run.comparisons?.result;
  if (run.measurement_suite) {
    const suite = run.measurement_suite; const content = el("div", "card");
    content.append(badge(suite.state), el("p", "small muted", "Every required focus evaluates the same candidate with its frozen evaluator. Required checks cannot be offset by another metric."));
    if (suite.next_action) content.append(el("p", "info-box", suite.next_action));
    const table = el("table", "comparison-table"); table.setAttribute("aria-label", "Required focus verification");
    const head = el("tr"); ["Focus", "Metric", "Reference", "Candidate", "Verification"].forEach((text) => head.append(el("th", "", text))); table.append(head);
    array(suite.members).forEach((member) => {
      const measured = array(suite.result?.members).find((item) => item.focus_id === member.focus_id);
      const row = el("tr"); const metric = member.primary_metric;
      row.append(el("td", "", member.name), el("td", "", metric), el("td", "", numeric(measured?.reference?.[metric])), el("td", "", numeric(measured?.finalist?.[metric])), el("td", "", measured?.passed === true ? "Passed" : measured?.passed === false ? "Failed" : "Unmeasured")); table.append(row);
      if (measured) content.append(evidenceBlock(`${member.name} · checks and guardrails`, {checks: measured.checks, constraints: measured.constraints, guardrails: measured.guardrails}));
    }); content.prepend(table); body.append(section("Required focus verification", content));
  }
  body.append(el("p", "info-box", `${run.shared_budget?.used_trials ?? run.usage?.trials ?? 0} / ${run.shared_budget?.max_trials ?? run.limits?.max_trials ?? "—"} ${run.shared_budget ? "total trials, including required focuses" : "trials"} · ${run.usage?.candidates ?? candidates.length} / ${run.limits?.max_candidates ?? "—"} candidates${run.shared_budget ? "" : ` · ${numeric(run.usage?.elapsed_seconds)} execution seconds`}`));
  if (result) body.append(section("Result", el("p", "", result === "verified_improvement" ? "A verified improvement is available. Compare it with the baseline and qualifying alternatives." : result === "final_verification_pending" ? "Promising candidates still need final verification." : "The baseline is retained. No verified improvement has been established.")));
  const comparisons = array(run.comparisons?.alternatives); const shown = baseline ? [baseline, ...comparisons.filter((item) => item.id !== baseline.id)] : comparisons;
  const inspector = el("div", "evidence-section"); inspector.id = "candidate-detail"; let requestId = 0;
  const inspect = async (candidate) => { const generation = ++requestId; inspector.replaceChildren(el("h3", "", candidate.hypothesis || "Baseline"), el("p", "loading-evidence", "Loading retained candidate evidence…")); const record = await api(projectAPI(`/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidate.id)}`, projectId)); if (generation !== requestId || !inspector.isConnected || state.projectId !== projectId) return; inspector.replaceChildren(el("h3", "", candidate.hypothesis || "Baseline"), candidateVerification(candidate, run.measurement_suite)); if (record.candidate?.brief) inspector.append(el("p", "small muted", `Permitted edits: ${(record.candidate.brief.editable_paths || []).join(", ")}`)); inspector.append(evidenceBlock(`Candidate diff${record.diff?.truncated ? " (truncated)" : ""}`, record.diff?.text || "No source differences.", true)); if (record.review) inspector.append(evidenceBlock(`Independent review · ${readable(record.review.verdict)}`, record.review.rationale || record.review, true)); else inspector.append(el("p", "warning", "Independent review has not been recorded.")); const trials = array(record.trials); trials.forEach((trial) => { const block = el("section", "evidence-section"); block.append(el("h3", "", `Trial ${String(trial.trial_id).slice(-8)} · ${readable(trial.state)}`)); if (trial.error) block.append(el("p", "warning", trial.error)); if (trial.checks?.length) block.append(gateList(trial.checks)); const events = array(trial.evidence?.events); if (events.length) block.append(evidenceBlock("Task inputs, outputs and progress", events.map((event) => `${event.task_id || ""} · ${readable(event.event)}\n${JSON.stringify(event.data, null, 2)}`).join("\n\n"))); array(trial.commands).forEach((command) => block.append(evidenceBlock(`${command.id} · exit ${command.exit_code ?? "unknown"}`, `stdout${command.stdout_truncated ? " (truncated)" : ""}\n${command.stdout || ""}\n\nstderr${command.stderr_truncated ? " (truncated)" : ""}\n${command.stderr || ""}`))); array(trial.skipped_commands).forEach((command) => block.append(el("p", "small muted", `${command.id} skipped: ${command.reason}`))); if (trial.evidence?.incomplete || trial.evidence?.truncated) block.append(el("p", "warning", "Retained task evidence is incomplete or reached its retention limit.")); inspector.append(block); }); if (!trials.length) inspector.append(el("p", "small muted", "No execution evidence yet.")); };
  if (shown.length) { const grid = el("div", "comparison-grid"); shown.forEach((candidate) => { const card = el("article", "card"); card.append(el("p", "eyebrow", candidate.id === run.baseline_id ? "Baseline" : candidate.id === run.selected_candidate_id ? "Selected candidate" : "Verified alternative"), el("h3", "", candidate.hypothesis || "Original application"), badge(candidate.display_state || candidate.state)); const score = el("div", "metric-pair"); const value = el("div"); value.append(el("div", "metric-label", "Agreed score"), el("div", "metric-value", numeric(candidate.score?.score))); score.append(value); card.append(score); const table = el("table", "comparison-table"); const head = el("tr"); ["Metric", "Value", "vs baseline"].forEach((label) => head.append(el("th", "", label))); table.append(head); Object.entries(candidate.metrics || {}).forEach(([metric, value]) => { const row = el("tr"); const reference = baseline?.metrics?.[metric]; const delta = typeof reference === "number" && typeof value === "number" ? `${value - reference > 0 ? "+" : ""}${numeric(value - reference)}` : "—"; row.append(el("td", "", metric), el("td", "", numeric(value)), el("td", "", candidate.id === run.baseline_id ? "baseline" : delta)); table.append(row); }); card.append(table, gateList([...(candidate.checks || []), ...(candidate.constraints || [])]), candidateVerification(candidate, run.measurement_suite), el("p", "small muted", `Independent review: ${candidate.review_verdict || "not recorded"}`)); const controls = actions(quiet("Inspect diff & evidence", () => inspect(candidate))); if (candidate.id !== run.baseline_id) controls.append(button("Select candidate", async () => { await api(projectAPI(`/runs/${encodeURIComponent(runId)}/control`, projectId), "POST", {version: 1, operation_id: crypto.randomUUID(), expected_revision: run.revision, action: "select", candidate_id: candidate.id}); notice("Candidate selected. Review delivery separately."); await detail("fix", run); })); card.append(controls); grid.append(card); }); body.append(grid); }
  if (candidates.length) { const list = el("div", "record-list"); candidates.forEach((candidate) => { const row = el("div", "record"); const main = el("div", "record-main"); main.append(button(candidate.hypothesis || (candidate.id === run.baseline_id ? "Baseline" : candidate.id), () => inspect(candidate), "record-title"), el("p", "record-description", `${String(candidate.id).slice(-8)}${candidate.parent_id ? ` · from ${String(candidate.parent_id).slice(-8)}` : ""} · ${candidate.review_verdict ? `review ${candidate.review_verdict}` : "review pending"}`)); row.append(main, badge(candidate.display_state || candidate.state)); list.append(row); }); body.append(section("All attempts", list), inspector); }
  if (run.selected_branch) { body.append(section("Selected branch", el("p", "info-box", `${run.selected_branch} · Selection is separate from publication, merge and deployment.`)), actions(button("Prepare local delivery", () => prepareDelivery("fix", runId, projectId)))); }
}
async function prepareDelivery(kind, sourceId, projectId) {
  const body = openDialog("Prepare delivery", "Review your result"); const generation = dialogGeneration; body.append(el("p", "loading-evidence", "Preparing the reviewed local branch and delivery artifacts…"));
  const result = await api(projectAPI("/deliveries", projectId), "POST", {kind, source_id: sourceId, publish: false});
  if (!$("dialog").open || state.projectId !== projectId || generation !== dialogGeneration) return;
  body.replaceChildren(el("p", "info-box", "Your local delivery is ready to review. Publishing creates a draft PR; merging and deployment remain separate actions."));
  if (result.branch) body.append(el("p", "record-description", `Branch: ${result.branch}`));
  body.append(artifactLinks(result.artifact_urls));
  if (result.diffstat) body.append(evidenceBlock("Changes", result.diffstat, true));
  if (result.summary) body.append(evidenceBlock("Validation summary", result.summary, true));
  body.append(evidenceBlock("Delivery details", result));
  const remote = field("Git remote", {required: true, placeholder: "origin"}); const base = field("Pull request base branch", {required: true, placeholder: "main"});
  const review = el("label", "checkbox-row"); const confirm = document.createElement("input"); confirm.type = "checkbox"; confirm.required = true; review.append(confirm, document.createTextNode("I reviewed the local delivery and want to publish it as a draft PR."));
  const kit = formKit(body, "Publish draft PR", async () => { const published = await api(projectAPI("/deliveries", projectId), "POST", {kind, source_id: sourceId, delivery_id: result.delivery_id, publish: true, remote: remote.input.value.trim(), base: base.input.value.trim()}); kit.form.replaceChildren(el("p", "info-box", "Draft PR publication completed.")); const candidateUrl = published.pr?.url; if (typeof candidateUrl === "string") { const url = new URL(candidateUrl); if (url.protocol === "https:") { const link = el("a", "button secondary", "Open draft PR ↗"); link.href = url.href; link.target = "_blank"; link.rel = "noopener"; kit.form.append(link); } } kit.form.append(evidenceBlock("Publication receipt", published)); }); const row = el("div", "form-grid"); row.append(remote.wrapper, base.wrapper); kit.form.append(el("h3", "", "Publish separately"), row, review); kit.finish();
}
function showAgent() { $("agent-panel").hidden = false; $("agent-toggle").setAttribute("aria-expanded", "true"); }
function questionForm(parent, job, question, projectId) {
  const prompts = array(question.questions);
  const fields = [];
  const kit = formKit(parent, "Send answer", async (operationId) => {
    let answer;
    if (!prompts.length) answer = {text: fields[0].input.value};
    else {
      const answers = {};
      fields.forEach((item) => {
        const value = item.checkboxes ? item.checkboxes.filter((input) => input.checked).map((input) => input.value) : item.input.value === "__custom__" ? item.custom.value.trim() : item.input.value;
        if (Array.isArray(value) && !value.length) throw new Error(`Choose at least one answer for ${item.label}.`);
        answers[item.key] = value;
      });
      answer = {answers};
    }
    await jobAction(idOf(job), "reply", {operation_id: operationId, question_id: question.id, answer}, projectId);
  });
  if (!prompts.length) { const answer = field("Your answer", {type: "textarea", required: true, rows: 3}); fields.push(answer); kit.form.append(answer.wrapper); }
  prompts.forEach((prompt) => {
    const key = job.agent === "claude" ? prompt.question : prompt.id;
    const label = prompt.question || prompt.header || "Your answer";
    const choices = array(prompt.options).map((option) => typeof option === "string" ? {label: option} : option);
    if (prompt.multiSelect && choices.length) {
      const group = el("fieldset", "form-section"); group.append(el("legend", "", label));
      const checkboxes = choices.map((choice) => { const row = el("label", "checkbox-row"); const input = document.createElement("input"); input.type = "checkbox"; input.value = choice.label; row.append(input, document.createTextNode(choice.label)); group.append(row); if (choice.description) group.append(el("p", "field-hint", choice.description)); return input; });
      fields.push({key, label, checkboxes}); kit.form.append(group); return;
    }
    const answer = field(label, choices.length ? {required: true, choices: [["", "Choose an answer"], ...choices.map((choice) => [choice.label, choice.label]), ["__custom__", "Write another answer"]]} : {type: "textarea", required: true, rows: 3});
    const custom = field("Your response", {type: "textarea", rows: 2}); custom.wrapper.hidden = true;
    if (choices.length) { answer.input.addEventListener("change", () => { custom.wrapper.hidden = answer.input.value !== "__custom__"; custom.input.required = !custom.wrapper.hidden; }); choices.filter((choice) => choice.description).forEach((choice) => answer.wrapper.append(el("p", "field-hint", `${choice.label}: ${choice.description}`))); }
    fields.push({key: key || label, label, input: answer.input, custom: custom.input}); kit.form.append(answer.wrapper, custom.wrapper);
  });
  kit.finish();
}
function renderAgent() {
  const selected = state.jobs.find((job) => idOf(job) === state.selectedJob); const select = $("job-select"); select.replaceChildren(); if (!state.jobs.length) select.append(new Option("No task selected", "")); state.jobs.forEach((job) => select.append(new Option(`${readable(job.kind)} · ${(job.goal || idOf(job)).slice(0, 65)}`, idOf(job)))); select.value = state.selectedJob || "";
  const activity = $("agent-activity"); const jobState = selected?.state || "idle";
  if (activity.dataset.state !== jobState) {
    activity.dataset.state = jobState;
    $("agent-spinner").hidden = jobState !== "running";
    $("agent-state").replaceWith(Object.assign(badge(jobState === "queued" ? "waiting" : jobState), {id: "agent-state"}));
  }
  $("agent-name").textContent = selected ? `${selected.agent === "claude" ? "Claude" : "Codex"}${selected.active_review_id ? " · reviewing" : ""}` : "Ready when you are";
  const content = $("agent-content"); const focused = content.contains(document.activeElement); if (focused && selected && content.dataset.jobId === idOf(selected) && content.dataset.jobState === selected.state && content.dataset.question === JSON.stringify(selected.question)) return;
  content.replaceChildren(); content.dataset.jobId = selected ? idOf(selected) : ""; content.dataset.jobState = selected?.state || ""; content.dataset.question = JSON.stringify(selected?.question);
  if (!selected) { const wrap = el("div", "agent-empty"); wrap.append(el("span", "", "✳"), el("h3", "", "No active task")); content.append(wrap); return; }
  content.append(el("p", "agent-goal", selected.goal)); if (selected.actual_model || selected.model) content.append(el("p", "job-info", `Model: ${selected.actual_model || selected.model}`));
  const events = el("ol", "event-list");
  array(selected.events).filter((event) => event.type === "message" && typeof event.text === "string" && event.text.trim()).slice(-60).forEach((event) => {
    const item = el("li", "event");
    item.append(el("span", "event-label", `${event.role === "reviewer" ? "Reviewer · " : ""}${readable(event.type)}`), document.createTextNode(event.text));
    events.append(item);
  });
  content.append(events);
  if (selected.next_action) content.append(el("p", "info-box", selected.next_action));
  const jobId = idOf(selected); const projectId = state.projectId;
  if (selected.question) {
    const question = selected.question; const wrap = el("div", "agent-question"); const approval = question.kind === "approval"; wrap.append(el("h3", "", approval ? "Your approval is needed" : "A decision is needed"), el("p", "", question.text || "Review the agent's request."));
    if (question.command || question.payload) { const details = el("details"); details.append(el("summary", "", "Request details"), el("pre", "", typeof question.command === "string" ? question.command : JSON.stringify(question.command || question.payload, null, 2))); wrap.append(details); }
    if (approval) wrap.append(actions(button("Approve", () => jobAction(jobId, "reply", {question_id: question.id, answer: {decision: "accept"}}, projectId), ""), button("Decline", () => jobAction(jobId, "reply", {question_id: question.id, answer: {decision: "decline"}}, projectId))));
    else questionForm(wrap, selected, question, projectId); content.append(wrap);
  }
  const controls = el("div", "agent-controls");
  if (!terminalStates.has(selected.state)) {
    if (!selected.question) { const needsAnswer = selected.state === "needs_input"; const message = field(needsAnswer ? "Your answer" : "Message your agent", {type: "textarea", placeholder: needsAnswer ? "Provide the missing decision or context…" : "Add context or steer this task…", rows: 3, required: true}); const kit = formKit(controls, needsAnswer ? "Send and resume" : "Send message", async (operationId) => { await jobAction(jobId, "message", {operation_id: operationId, message: message.input.value}, projectId); if (needsAnswer) await jobAction(jobId, "resume", {}, projectId); message.input.value = ""; }); kit.form.append(message.wrapper); kit.finish(); }
    controls.append(actions(["paused", "interrupted"].includes(selected.state) || (selected.state === "needs_input" && !selected.question) ? button("Resume", () => jobAction(jobId, "resume", {}, projectId)) : button("Pause", () => jobAction(jobId, "pause", {}, projectId)), button("Cancel task", () => jobAction(jobId, "cancel", {}, projectId), "danger")));
  } else if (selected.state === "failed") controls.append(button("Resume task", () => jobAction(jobId, "resume", {}, projectId)));
  if (selected.result) { const details = el("details"); details.append(el("summary", "", "Task result"), el("pre", "", typeof selected.result === "string" ? selected.result : JSON.stringify(selected.result, null, 2))); controls.append(details); }
  controls.append(el("p", "job-info", `${project()?.name || "Project"} · ${jobId}`)); content.append(controls);
}
async function jobAction(jobId, action, payload = {}, projectId = state.projectId) { const result = await api(projectAPI(`/jobs/${encodeURIComponent(jobId)}/${action}`, projectId), "POST", {operation_id: crypto.randomUUID(), ...payload}); if (projectId === state.projectId) { const job = result; const index = state.jobs.findIndex((item) => idOf(item) === jobId); if (index >= 0 && idOf(job)) state.jobs[index] = job; renderAgent(); await loadOverview(); } }
async function showActivity() { const body = openDialog("Activity across projects"); const results = await Promise.allSettled(state.projects.map(async (item) => { const response = await api(projectAPI("/jobs", item.id)); return array(response.jobs).map((job) => ({...job, project_id: item.id})); })); const jobs = []; results.forEach((result) => { if (result.status === "fulfilled") jobs.push(...result.value); else body.append(el("p", "warning", result.reason.message)); }); jobs.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))); body.append(jobs.length ? jobList(jobs, true) : empty("No tasks")); }
function renderSettings(page) {
  if (state.applicationAgentId) return renderApplicationAgentSettings(page);
  const tab = ["connections", "agents", "execution", "defaults", "project", "privacy"].includes(state.tab) ? state.tab : "connections";
  page.append(heading("Settings"), tabs([["connections", "Connections"], ["agents", "Coding agents"], ["execution", "Execution"], ["defaults", "Project defaults"], ["project", "Project"], ["privacy", "Privacy"]], tab, changeTab));
  const wrap = el("div", "settings-layout"); page.append(wrap);
  if (tab === "connections") renderConnections(wrap); if (tab === "agents") renderAgentSettings(wrap); if (["execution", "defaults", "project", "privacy"].includes(tab)) { if (!state.projectId) wrap.append(empty("Choose a project", button("Add project", addProject))); else if (tab === "execution") renderExecution(wrap); else renderProjectSettings(wrap, tab); }
}
function renderApplicationAgentSettings(page) {
  const existing = applicationAgent();
  if (!existing) { page.append(heading("Agent settings"), empty("Agent not found")); return; }
  const projectId = state.projectId; const agentId = existing.id; const box = el("section", "card settings-form");
  const generatedDescription = /^Discovered .+ entrypoint in .+\. Confirm its code and trace boundaries\.$/.test(existing.description || "") || existing.description === "Discovered in imported traces. Confirm its code binding and trace selector."; const name = field("Agent name", {required: true, value: existing.name || "", placeholder: "Support triage"}); const description = field("Agent responsibility", {type: "textarea", value: generatedDescription ? "" : existing.description || "", placeholder: "What behavior or workflow does this agent own?"}); const scopes = field("Agent code paths", {value: array(existing.code_scopes).join(", "), placeholder: "src/support, prompts/support"}); const shared = field("Shared code paths", {value: array(existing.shared_dependencies).join(", "), placeholder: "src/shared, tools/common"});
  const connections = state.connections.filter((item) => item.project_id === projectId); const connection = field("Trace connection", {choices: [["", "No imported trace source"], ...connections.map((item) => [item.id, item.name || providers[item.provider]])], value: existing.trace_selector?.connection_id || ""}); const providerProject = field("Trace project", {value: existing.trace_selector?.project || ""}); const filters = field("Trace selector (JSON, optional)", {type: "textarea", value: existing.trace_selector?.filters ? JSON.stringify(existing.trace_selector.filters) : ""});
  providerProject.input.readOnly = true; const syncTraceProject = () => { providerProject.input.value = connections.find((item) => item.id === connection.input.value)?.project || ""; }; connection.input.addEventListener("change", syncTraceProject); if (connection.input.value) syncTraceProject();
  const trace = el("details"); trace.append(el("summary", "", "Trace binding (optional)"), connection.wrapper, providerProject.wrapper, filters.wrapper);
  const kit = formKit(box, "Save agent settings", async () => { const traceSelector = connection.input.value ? {connection_id: connection.input.value, ...(providerProject.input.value.trim() ? {project: providerProject.input.value.trim()} : {}), ...(filters.input.value.trim() ? {filters: JSON.parse(filters.input.value)} : {})} : {}; const payload = {name: name.input.value.trim(), description: description.input.value.trim(), ...(existing.revision != null ? {expected_revision: existing.revision} : {}), code_scopes: paths(scopes.input.value), shared_dependencies: paths(shared.input.value), trace_selector: traceSelector, status: "confirmed"}; await api(applicationAPI("", agentId, projectId), "POST", payload); if (state.projectId === projectId && state.applicationAgentId === agentId) { await loadOverview(); notice("Agent settings saved."); } });
  kit.form.append(name.wrapper, description.wrapper, scopes.wrapper, shared.wrapper, trace); kit.finish(); page.append(heading(`${existing.name} settings`), box);
}
function providerProjectField(connection) {
  const selected = field("Source project", {value: connection.project_name || connection.project || ""});
  selected.input.readOnly = true;
  return selected;
}
function renderConnections(parent) {
  if (!state.projectId) { parent.append(empty("Choose a project", button("Add project", addProject))); return; }
  const projectId = state.projectId;
  const head = el("div", "section-heading"); head.append(el("h2", "", "Trace and dataset sources"), button("Add connection", () => connectionForm(), ""));
  parent.append(head);
  const cards = el("div", "section two-column");
  const connections = state.connections.filter((connection) => connection.project_id === projectId);
  connections.forEach((connection) => {
    const card = el("article", "card connection-card"); card.append(el("div", "provider-mark", providers[connection.provider].slice(0, 1)));
    const body = el("div", "connection-content"); body.append(el("h3", "", connection.name), el("p", "small muted", connection.endpoint), badge(connection.status || "connected"));
    if (connection.last_checked_at) body.append(el("p", "small muted", `Last checked ${new Date(connection.last_checked_at).toLocaleString()}`));
    if (connection.credential_mode === "session") body.append(el("p", "small muted", "Credentials last until this app stops."));
    body.append(actions(quiet("Test", async () => { await api(projectAPI(`/connections/${encodeURIComponent(connection.id)}/test`, projectId), "POST", {}); await refreshConnections(); render(); notice("Connection checked."); }), quiet("Edit", () => connectionForm(connection)), quiet("Disconnect", () => disconnect(connection))));
    card.append(body); cards.append(card);
  });
  parent.append(cards);
  if (!connections.length) { const grid = el("div", "provider-grid"); Object.entries(providers).forEach(([provider, name]) => { const choice = quiet(name, () => connectionForm({provider})); choice.className = "provider-choice"; choice.replaceChildren(el("strong", "", name), el("span", "", "Connect source")); grid.append(choice); }); parent.append(grid); }
}
function connectionForm(existing = {}) {
  if (!state.projectId) return addProject();
  const projectId = state.projectId;
  const body = openDialog(existing.id ? "Edit connection" : "Connect a source", project()?.name);
  const generation = dialogGeneration;
  const provider = field("Provider", {choices: Object.entries(providers), value: existing.provider || "braintrust"}); provider.input.disabled = Boolean(existing.id);
  const endpoint = field("API URL", {type: "url", value: existing.endpoint || providerEndpoints[provider.input.value], required: true});
  const credentials = el("div", "form-grid");
  const source = field("Source project", {choices: [["", "Choose a project"]]}); source.wrapper.hidden = true;
  const status = el("p", "field-hint"); status.setAttribute("role", "status");
  let credentialFields = {}, discovery = null, revision = 0;
  const current = () => generation === dialogGeneration && $("dialog").open && projectId === state.projectId;
  const invalidate = () => { revision += 1; discovery = null; source.wrapper.hidden = true; source.input.required = false; status.textContent = ""; kit.save.textContent = "Find projects"; };
  const kit = formKit(body, "Find projects", async () => {
    const version = revision;
    if (!discovery) {
      const secrets = {}; Object.entries(credentialFields).forEach(([key, input]) => { if (input.value.trim()) secrets[key] = input.value.trim(); });
      status.textContent = "Finding projects…";
      const payload = {provider: provider.input.value, endpoint: endpoint.input.value.trim(), credentials: secrets}; if (existing.id) payload.id = existing.id;
      let result;
      try { result = await api(projectAPI("/connections/discover", projectId), "POST", payload); }
      finally { if (current() && revision === version) status.textContent = ""; }
      if (!current() || version !== revision) return;
      const projects = array(result.projects);
      if (!projects.length) { status.textContent = "No projects found. Check this key’s access and API URL, then try again."; return; }
      discovery = result.discovery_id;
      source.input.replaceChildren(new Option("Choose a project", ""), ...projects.map((item) => new Option(item.workspace_name ? `${item.name} · ${item.workspace_name}` : item.name || item.id, item.selection_id || item.id)));
      const previous = projects.find((item) => item.id === existing.project && (!existing.workspace_id || item.workspace_id === existing.workspace_id));
      source.input.value = previous ? previous.selection_id || previous.id : projects.length === 1 ? projects[0].selection_id || projects[0].id : "";
      source.wrapper.hidden = false; source.input.required = true;
      kit.save.textContent = existing.id ? "Save connection" : "Connect";
      source.input.focus();
      return;
    }
    try { await api(projectAPI("/connections", projectId), "POST", {discovery_id: discovery, project: source.input.value}); }
    catch (error) { if ([400, 404, 409].includes(error.status)) invalidate(); throw error; }
    Object.values(credentialFields).forEach((input) => { input.value = ""; });
    if (current()) $("dialog").close();
    await refreshConnections(); render(); notice("Source connected.");
  });
  const renderCredentials = () => {
    credentials.replaceChildren(); credentialFields = {};
    const keys = provider.input.value === "langfuse" ? [["public_key", "Public key"], ["secret_key", "Secret key"]] : [["api_key", "API key"]];
    credentials.className = keys.length === 1 ? "" : "form-grid";
    keys.forEach(([key, label]) => { const item = field(label, {type: "password", required: !existing.id, placeholder: existing.id ? "Leave blank to keep the saved key" : ""}); item.input.autocomplete = "off"; item.input.addEventListener("input", invalidate); credentialFields[key] = item.input; credentials.append(item.wrapper); });
  };
  provider.input.addEventListener("change", () => { endpoint.input.value = providerEndpoints[provider.input.value]; renderCredentials(); invalidate(); });
  endpoint.input.addEventListener("input", invalidate);
  renderCredentials();
  kit.form.append(provider.wrapper, credentials, endpoint.wrapper, source.wrapper, status); kit.finish();
}
function disconnect(connection) {
  const projectId = connection.project_id;
  const body = openDialog("Disconnect this source?", connection.name);
  body.append(el("p", "", "Saved local snapshots remain available. Future imports will need a connection."), actions(button("Keep connection", () => $("dialog").close()), button("Disconnect", async () => { await api(projectAPI(`/connections/${encodeURIComponent(connection.id)}`, projectId), "DELETE", {}); $("dialog").close(); await refreshConnections(); render(); notice("Connection removed. Saved evidence was retained."); }, "danger")));
}
function renderAgentSettings(parent) {
  const cards = el("div", "two-column"); ["codex", "claude"].forEach((id) => { const agent = state.agents.find((item) => item.id === id) || {}; const readiness = agentReadiness(id); const card = el("article", "card"); card.append(el("h3", "", id === "codex" ? "Codex" : "Claude"), badge(readiness.state)); if (readiness.state !== "available") card.append(el("p", "", readiness.message)); if (agent.version) card.append(el("p", "small", agent.version)); cards.append(card); }); parent.append(cards);
  const box = el("section", "section card settings-form"); const agent = field("Default coding agent", {choices: [["codex", "Codex"], ["claude", "Claude"]], value: state.agentSettings.default_agent || "codex"}); const model = modelField(agent.input, "Default model", state.agentSettings.models); const concurrency = field("Maximum concurrent sessions", {type: "number", min: 1, max: 8, value: state.agentSettings.concurrency || 1}); const key = field("Claude API key", {type: "password", placeholder: state.claudeKeyConfigured ? "Configured" : ""}); const mode = field("Store credential", {choices: [["keyring", "System credential store"], ["session", "For this app session"], ["env", "Environment variable reference"]]}); const updateCredentialMode = () => { key.input.type = mode.input.value === "env" ? "text" : "password"; key.wrapper.querySelector("label").textContent = mode.input.value === "env" ? "Claude API key environment variable" : "Claude API key"; }; const updateProviderFields = () => { const claude = agent.input.value === "claude"; key.wrapper.hidden = !claude; mode.wrapper.hidden = !claude; }; mode.input.addEventListener("change", updateCredentialMode); agent.input.addEventListener("change", updateProviderFields); updateCredentialMode(); updateProviderFields(); const kit = formKit(box, "Save agent settings", async () => { const payload = {default_agent: agent.input.value, model: model.input.value.trim(), concurrency: Number(concurrency.input.value)}; if (agent.input.value === "claude") { payload.credential_mode = mode.input.value; if (key.input.value.trim()) payload.claude_api_key = key.input.value.trim(); } await api("/api/agents", "POST", payload); key.input.value = ""; await refreshConnections(); render(); notice("Agent settings saved."); }); kit.form.append(agent.wrapper, model.wrapper, concurrency.wrapper, key.wrapper, mode.wrapper); kit.finish(); parent.append(box);
}
function settingsValue() { return state.overview?.settings?.settings || {}; }
function renderExecution(parent) {
  const profiles = state.overview?.settings?.profiles || {}; const header = el("div", "section-heading"); header.append(el("h2", "", "Execution profiles"), button("New profile", () => profileForm(), "")); parent.append(header); const list = el("div", "section record-list"); Object.entries(profiles).forEach(([name, value]) => { const row = el("div", "record"); const body = el("div", "record-main"); body.append(el("h3", "", name), el("p", "record-description", `${readable(value.runner?.kind || "local")} · ${value.limits?.max_trials || "—"} runs · ${Math.round((value.limits?.max_elapsed_seconds || 0) / 60)} min`)); row.append(body, quiet("Edit profile", () => profileForm(name, value))); list.append(row); }); parent.append(Object.keys(profiles).length ? list : section("Execution profiles", empty("No execution profiles", button("Create execution profile", () => profileForm(), ""))));
}
function profileForm(existingName, existing = {}) {
  const projectId = state.projectId; const body = openDialog(existingName ? "Edit execution profile" : "Create execution profile", project()?.name); const name = field("Profile name", {required: true, value: existingName || "local"}); const runner = field("Run evaluations on", {choices: [["local", "This computer"], ["ssh", "SSH host"], ["e2b", "E2B sandbox"]], value: existing.runner?.kind || "local"}); const remote = el("div", "form-grid"); let remoteFields = {}; const updateRunner = () => { remote.replaceChildren(); remoteFields = {}; const choices = runner.input.value === "ssh" ? [["host", "SSH host alias"], ["remote_root", "Remote directory"]] : runner.input.value === "e2b" ? [["template", "E2B template"], ["api_key_env", "API key environment variable"]] : []; choices.forEach(([key, label]) => { const item = field(label, {required: true, value: existing.runner?.[key] || (key === "api_key_env" ? "E2B_API_KEY" : "")}); remote.append(item.wrapper); remoteFields[key] = item.input; }); }; runner.input.addEventListener("change", updateRunner); updateRunner();
  const fields = {}; const limits = el("div", "form-grid"); [["max_candidates", "Maximum candidates", 3], ["max_trials", "Maximum evaluation runs", 24], ["max_elapsed_seconds", "Total time limit (seconds)", 1800], ["trial_timeout_seconds", "Per-trial timeout (seconds)", 60], ["parallel_candidates", "Parallel candidates", 1], ["parallel_trials", "Parallel trials", 1]].forEach(([key, label, value]) => { const item = field(label, {type: "number", min: 1, required: true, value: existing.limits?.[key] || value}); fields[key] = item.input; limits.append(item.wrapper); });
  const independent = document.createElement("input"); independent.type = "checkbox"; independent.checked = Boolean(existing.runner?.independent_capacity); const capacity = el("label", "checkbox-row"); capacity.append(independent, document.createTextNode("This runner has independent capacity for parallel measurements"));
  const advanced = el("details"); advanced.append(el("summary", "", "Setup commands and environment references")); const setup = field("Setup commands (JSON argv arrays)", {type: "textarea", value: JSON.stringify(existing.setup || [], null, 2)}); const env = field("Environment references (JSON object)", {type: "textarea", value: JSON.stringify(existing.env || {}, null, 2), hint: "Map environment variable names to credential variable references. Never paste secret values."}); advanced.append(setup.wrapper, env.wrapper);
  const kit = formKit(body, "Save profile", async () => { const profile = {...existing, runner: {kind: runner.input.value}, limits: {...existing.limits}, setup: JSON.parse(setup.input.value), env: JSON.parse(env.input.value)}; Object.entries(fields).forEach(([key, input]) => { profile.limits[key] = Number(input.value); }); Object.entries(remoteFields).forEach(([key, input]) => { profile.runner[key] = input.value.trim(); }); if (independent.checked) profile.runner.independent_capacity = true; await api(projectAPI("/settings", projectId), "POST", {scope: "project", values: {}, unset: [], profile_name: name.input.value.trim(), profile}); $("dialog").close(); await loadOverview(); notice("Execution profile saved for future work."); }); kit.form.append(name.wrapper, runner.wrapper, remote, limits, capacity, advanced); kit.finish();
}
function renderProjectSettings(parent, tab) {
  const data = settingsValue(); const projectId = state.projectId; const box = el("section", "card settings-form");
  if (tab === "privacy") {
    const mode = field("Optional Intelligence", {choices: [["ask", "Ask before each request"], ["full_access", "Allow prepared requests without individual prompts"]], value: data.intelligence?.mode || "ask"}); const endpoint = field("Intelligence service URL", {value: data.intelligence?.endpoint || "", placeholder: "https://brain.agentagon.ai"}); const key = field("Intelligence API key", {type: "password", placeholder: state.overview?.settings?.intelligence_key_configured ? "Configured for this session" : ""}); const telemetry = field("Usage telemetry (all projects)", {choices: [["true", "Enabled"], ["false", "Disabled"]], value: String(data.telemetry?.enabled ?? true)}); const kit = formKit(box, "Save privacy settings", async () => { const values = {"intelligence.mode": mode.input.value}; const unset = []; if (endpoint.input.value.trim()) values["intelligence.endpoint"] = endpoint.input.value.trim(); else unset.push("intelligence.endpoint"); const payload = {scope: "project", values, unset}; if (key.input.value.trim()) payload.intelligence_api_key = key.input.value.trim(); await api(projectAPI("/settings", projectId), "POST", payload); key.input.value = ""; if (String(data.telemetry?.enabled ?? true) !== telemetry.input.value) await api(projectAPI("/settings", projectId), "POST", {scope: "user", values: {"telemetry.enabled": telemetry.input.value === "true"}, unset: []}); await loadOverview(); notice("Privacy settings saved."); }); kit.form.append(mode.wrapper, endpoint.wrapper, key.wrapper, telemetry.wrapper); kit.finish();
  } else if (tab === "defaults") {
    const traces = field("Use runtime traces", {choices: [["unset", "Ask when starting work"], ["enabled", "Enabled"], ["disabled", "Disabled"]], value: data.traces?.state || "unset"}); const kit = formKit(box, "Save project defaults", async () => { await api(projectAPI("/settings", projectId), "POST", {scope: "project", values: {"traces.state": traces.input.value}, unset: []}); await loadOverview(); notice("Project defaults saved."); }); kit.form.append(traces.wrapper); kit.finish();
  } else {
    box.append(el("h2", "", project()?.name || "Project"), el("p", "muted small", project()?.path)); const danger = el("div", "danger-zone"); danger.append(el("h3", "", "Remove this project from the app"), el("p", "", "Its directory, configuration and saved Agentagon evidence remain on disk."), button("Remove project", () => removeProject(projectId), "danger")); box.append(danger);
  } parent.append(box);
}
function removeProject(projectId) { const body = openDialog("Remove project from this workspace?", project()?.name); body.append(el("p", "", "The app stops listing this checkout. Its files and local evidence remain available, and you can add it again."), actions(button("Keep project", () => $("dialog").close()), button("Remove project", async () => { await api(projectAPI("", projectId), "DELETE", {}); $("dialog").close(); await refreshProjects(); await selectProject(state.projectId); notice("Project registration removed. Files and evidence were retained."); }, "danger"))); }
async function importFlow(kind) {
  if (!state.projectId) return addProject(); const projectId = state.projectId; const generation = state.generation; const opening = dialogGeneration; await refreshConnections(); if (projectId !== state.projectId || generation !== state.generation || opening !== dialogGeneration) return; const available = state.connections.filter((item) => item.project_id === projectId); const body = openDialog(kind === "dataset" ? "Import a dataset" : "Import trace evidence", project()?.name); const dialog = dialogGeneration;
  if (!available.length) { body.append(empty("No connected source", button("Add connection", () => connectionForm(), ""))); return; }
  const connection = field("Connection", {choices: available.map((item) => [item.id, item.name || providers[item.provider]])}); let providerProject = providerProjectField(available[0]); const dataset = field("Dataset", {choices: [["", "Choose a dataset"]], required: kind === "dataset"}); const datasetStatus = el("p", "field-hint"); datasetStatus.setAttribute("role", "status"); const version = field("Dataset version (optional)", {placeholder: "Latest available"}); const start = field("Start of time window", {type: "datetime-local", required: kind === "traces"}); const end = field("End of time window", {type: "datetime-local", required: kind === "traces"}); const cap = field(kind === "dataset" ? "Maximum cases" : "Maximum traces", {type: "number", value: 50, min: 1, max: 1000, required: true}); const filters = field("Provider filters (JSON, optional)", {type: "textarea", value: "{}", rows: 2});
  let loadId = 0; const loadDatasets = async () => { const sequence = ++loadId; const value = available.find((item) => item.id === connection.input.value); const replacement = providerProjectField(value || {}); providerProject.wrapper.replaceWith(replacement.wrapper); providerProject = replacement; if (kind !== "dataset") return; dataset.input.replaceChildren(new Option("Loading datasets…", "")); datasetStatus.textContent = ""; try { const result = await api(projectAPI(`/connections/${encodeURIComponent(connection.input.value)}/datasets`, projectId)); if (sequence !== loadId) return; dataset.input.replaceChildren(new Option("Choose a dataset", "")); array(result.datasets).forEach((item) => dataset.input.append(new Option(item.name || item.id || item.dataset_id, item.id || item.dataset_id || item.name))); if (dataset.input.options.length === 1) datasetStatus.textContent = "No datasets are available. Check the provider project and connection access."; } catch (error) { datasetStatus.textContent = error.message; dataset.input.replaceChildren(new Option("Dataset lookup failed", "")); } }; connection.input.addEventListener("change", loadDatasets);
  const kit = formKit(body, "Preview import", async () => { const selection = {project: available.find((item) => item.id === connection.input.value)?.project, cap: Number(cap.input.value), filters: JSON.parse(filters.input.value || "{}")}; if (kind === "dataset") { selection.dataset_id = dataset.input.value; if (version.input.value.trim()) selection.version = version.input.value.trim(); } else { selection.start = new Date(start.input.value).toISOString(); selection.end = new Date(end.input.value).toISOString(); if (selection.start >= selection.end) throw new Error("The end of the time window must be after its start."); } const preview = await api(projectAPI("/imports/preview", projectId), "POST", {connection_id: connection.input.value, kind, selection}); if (projectId === state.projectId && dialog === dialogGeneration && $("dialog").open) previewImport(kind, preview, projectId); }); kit.form.append(connection.wrapper, providerProject.wrapper); if (kind === "dataset") kit.form.append(dataset.wrapper, datasetStatus, version.wrapper); else { const row = el("div", "form-grid"); row.append(start.wrapper, end.wrapper); kit.form.append(row); } kit.form.append(cap.wrapper, filters.wrapper); kit.finish(); await loadDatasets();
}
function previewImport(kind, preview, projectId) {
  const body = openDialog("Review your import", kind === "dataset" ? "Dataset preview" : "Trace preview"); const generation = dialogGeneration; const items = array(preview.items); body.append(el("p", "", `${items.length} ${kind === "dataset" ? "cases" : "traces"} in this preview.`));
  const completeness = typeof preview.completeness === "object" ? preview.completeness.state || preview.completeness.status || (preview.completeness.complete === true ? "complete" : preview.completeness.complete === false ? "partial" : "unknown") : preview.completeness; body.append(badge(completeness || "unknown completeness"));
  const provenance = el("dl", "details-grid"); Object.entries(preview.provenance || {}).forEach(([key, value]) => { if (value != null && typeof value !== "object") provenance.append(el("dt", "", readable(key)), el("dd", "", value)); }); body.append(provenance);
  const columns = [...new Set(items.slice(0, 5).flatMap((item) => typeof item === "object" && item ? Object.keys(item) : ["value"]))].slice(0, 6); const wrap = el("div", "data-preview"); const table = el("table"); const head = el("thead"); const row = el("tr"); columns.forEach((key) => row.append(el("th", "", readable(key)))); head.append(row); const tbody = el("tbody"); items.slice(0, 12).forEach((item) => { const row = el("tr"); columns.forEach((key) => { const value = typeof item === "object" && item ? item[key] : item; row.append(el("td", "", value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : value)); }); tbody.append(row); }); table.append(head, tbody); wrap.append(table); body.append(wrap);
  if (kind === "dataset") { const hasExpected = items.every((item) => item && ["expected", "expected_output", "expected_behavior", "reference", "ground_truth", "label"].some((key) => item[key] != null && item[key] !== "")); if (!hasExpected) body.append(el("p", "warning", "Some cases have no explicit expected behavior. Observed outputs are not trusted labels. Your agent must resolve expectations before freezing an evaluation.")); const mapping = el("details"); mapping.open = true; mapping.append(el("summary", "", "Input and expectation fields")); const report = preview.mappings || preview.mapping || preview.provenance?.mapping; mapping.append(el("p", "small muted", report ? JSON.stringify(report) : "Provider fields are retained as imported. The evaluation workflow will confirm which fields describe inputs, observed outputs and trusted expectations.")); body.append(mapping); }
  if (preview.warnings?.length) preview.warnings.forEach((warning) => body.append(el("p", "warning", warning)));
  const kit = formKit(body, "Save local snapshot", async (operationId) => { const result = await api(projectAPI("/imports", projectId), "POST", {preview_id: preview.preview_id, operation_id: operationId}); if (state.projectId === projectId) await loadOverview(); if ($("dialog").open && generation === dialogGeneration && state.projectId === projectId) { if (kind === "dataset") navigate("eval", "datasets"); else navigate("traces"); $("dialog").close(); } notice(`Saved ${kind === "dataset" ? "dataset" : "trace"} snapshot. ${result.count} records retained.`); }); kit.finish();
}
$("primary-nav").addEventListener("click", (event) => { const target = event.target.closest("button[data-view]"); if (target) navigate(target.dataset.view); });
$("project-select").addEventListener("change", () => selectProject($("project-select").value).catch(showError));
$("application-agent-select").addEventListener("change", () => selectApplicationAgent($("application-agent-select").value).catch(showError));
$("add-project").addEventListener("click", addProject); $("close-dialog").addEventListener("click", () => $("dialog").close());
$("refresh").addEventListener("click", async () => { try { const previous = state.projectId; await refreshProjects(); if (previous !== state.projectId) return await selectProject(state.projectId); await Promise.all([refreshConnections(), loadOverview()]); render(); } catch (error) { showError(error); } });
$("activity-button").addEventListener("click", () => showActivity().catch(showError));
$("agent-toggle").addEventListener("click", () => { $("agent-panel").hidden = !$("agent-panel").hidden; $("agent-toggle").setAttribute("aria-expanded", String(!$("agent-panel").hidden)); });
$("job-select").addEventListener("change", () => { state.selectedJob = $("job-select").value; updateURL(); renderAgent(); });
if (matchMedia("(max-width: 920px)").matches) { $("agent-panel").hidden = true; $("agent-toggle").setAttribute("aria-expanded", "false"); }
window.addEventListener("beforeunload", () => state.source?.close());
(async () => { try { state.token = (await api("/api/session")).token; await refreshProjects(); await selectProject(state.projectId, state.selectedJob, state.applicationAgentId, state.focusId); if (!state.projectId || state.view === "settings") render(); } catch (error) { showError(error); $("page").replaceChildren(empty("The local app is unavailable", button("Retry", () => location.reload()))); } })();
