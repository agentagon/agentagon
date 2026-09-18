import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { operationId, post, projectPath, remove } from "./api";
import {
  AddAgentModal,
  AddGoalModal,
  Button,
  Empty,
  Icon,
  LaunchWorkflowModal,
  Modal,
  PageHeader,
  Status,
  TaskDetail,
} from "./components";
import {
  useAgent,
  useAgentOverview,
  useAgents,
  useAssistants,
  useCodexModels,
  useConnectors,
  useConnectorTypes,
  useGoal,
  useGoals,
  useOverview,
  useSkills,
  useTasks,
} from "./hooks";
import type { Agent, ConnectorType, Project, ProjectConnector, SkillDefinition } from "./types";

function useDiscoveryAssistant() {
  const assistants = useAssistants();
  const selected = String(assistants.data?.defaults.default_agent || "codex");
  const assistant = assistants.data?.assistants.find((item) => item.id === selected);
  return {
    loading: assistants.isLoading,
    ready: Boolean(assistant?.available && assistant.authenticated === true),
  };
}

function DiscoveryAction({ assistant, pending, onDiscover, onSetup }: { assistant: ReturnType<typeof useDiscoveryAssistant>; pending: boolean; onDiscover: () => void; onSetup: () => void }) {
  if (assistant.loading) return <Button disabled>Checking…</Button>;
  if (!assistant.ready) return <Button onClick={onSetup}>Set up coding assistant</Button>;
  return <Button onClick={onDiscover} disabled={pending}>{pending ? "Discovering…" : "Discover agents"}</Button>;
}

export function HomePage({ project }: { project: Project }) {
  const agents = useAgents(project.id);
  const tasks = useTasks(project.id);
  const overview = useOverview(project.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const discoveryAssistant = useDiscoveryAssistant();
  const discover = useMutation({
    mutationFn: () => post(projectPath(project.id, "/application-agents/discover"), {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects", project.id, "agents"] });
      navigate(`/projects/${project.id}/agents`);
    },
  });
  const confirmed = agents.data?.confirmed || [];
  const attention = tasks.data?.tasks.filter((task) => task.needs_attention) || [];
  const results = [
    ...(overview.data?.baselines || []).map((item) => ({ kind: "Baseline", item })),
    ...(overview.data?.runs || []).map((item) => ({ kind: "Improvement", item })),
    ...(overview.data?.audits || []).map((item) => ({ kind: "Audit", item })),
  ].slice(0, 5);
  return <>
    <PageHeader eyebrow="Project home" title={project.name}><p>{project.path}</p></PageHeader>
    {(!discoveryAssistant.ready || !confirmed.length) && <section className="onboarding-band"><div className="onboarding-count">01</div><div><h2>{discoveryAssistant.ready ? "Add an agent" : "Set up a coding assistant"}</h2></div><div className="onboarding-actions"><DiscoveryAction assistant={discoveryAssistant} pending={discover.isPending} onDiscover={() => discover.mutate()} onSetup={() => navigate(`/projects/${project.id}/settings/assistants`)} /></div></section>}
    {discover.error && <p className="error-banner">{discover.error.message}</p>}
    <div className="summary-grid">
      <section className="summary-card"><span className="summary-label">Agents</span><strong>{confirmed.length}</strong><Link to={`/projects/${project.id}/agents`}>{confirmed.length ? "View agents" : "Set up"}<Icon name="arrow" size={15} /></Link></section>
      <section className="summary-card"><span className="summary-label">Needs attention</span><strong>{attention.length}</strong><Link to={`/projects/${project.id}/tasks`}>View tasks<Icon name="arrow" size={15} /></Link></section>
      <section className="summary-card"><span className="summary-label">Evidence</span><strong>{(overview.data?.datasets.length || 0) + (overview.data?.traces.length || 0)}</strong><span className="summary-note">Datasets and trace snapshots</span></section>
    </div>
    <div className="home-columns"><section><div className="section-heading"><div><p className="eyebrow">Attention</p><h2>Work waiting on you</h2></div><Link to={`/projects/${project.id}/tasks`}>All tasks</Link></div>{attention.length ? <div className="list-surface">{attention.slice(0, 5).map((task) => <Link className="list-row" to={`/projects/${project.id}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Agent"} · {task.workflow}</span></div><Status value={task.state} /></Link>)}</div> : <div className="quiet-surface">Nothing needs your attention.</div>}</section>
      <section><div className="section-heading"><div><p className="eyebrow">Recent</p><h2>Measured results</h2></div></div>{results.length ? <div className="list-surface">{results.map(({ kind, item }, index) => <div className="list-row" key={String(item.id || item.run_id || item.audit_id || index)}><div><strong>{String(item.name || item.summary || kind)}</strong><span>{kind}</span></div></div>)}</div> : <div className="quiet-surface">Results appear after the first measurement.</div>}</section></div>
  </>;
}

const agentTabs = ["overview", "goals", "evidence", "activity", "configuration"];

export function AgentPage({ projectId }: { projectId: string }) {
  const { agentId = "", tab = "overview" } = useParams();
  const agent = useAgent(projectId, agentId);
  const goals = useGoals(projectId, agentId);
  const overview = useAgentOverview(projectId, agentId);
  const tasks = useTasks(projectId, { agent_id: agentId });
  const [addGoal, setAddGoal] = useState(false);
  const [edit, setEdit] = useState(false);
  if (agent.isLoading) return <div className="page-loading">Loading agent…</div>;
  if (!agent.data) return <Empty title="Agent not found" />;
  return <>
    <PageHeader eyebrow="Agent" title={agent.data.name} actions={<Button tone="secondary" onClick={() => setEdit(true)}>Edit agent</Button>} />
    <nav className="tabs" aria-label="Agent sections">{agentTabs.map((name) => <NavLink key={name} to={`/projects/${projectId}/agents/${agentId}/${name}`} className={({ isActive }) => isActive ? "is-active" : ""}>{name}</NavLink>)}</nav>
    {tab === "overview" && <AgentOverview agent={agent.data} goals={goals.data?.goals || []} overview={overview.data} projectId={projectId} onAddGoal={() => setAddGoal(true)} />}
    {tab === "goals" && <GoalsList projectId={projectId} agentId={agentId} goals={goals.data?.goals || []} onAddGoal={() => setAddGoal(true)} />}
    {tab === "evidence" && <EvidenceView overview={overview.data} />}
    {tab === "activity" && <TaskList projectId={projectId} tasks={tasks.data?.tasks || []} />}
    {tab === "configuration" && <AgentConfiguration projectId={projectId} agent={agent.data} />}
    {addGoal && <AddGoalModal projectId={projectId} agentId={agentId} onClose={() => setAddGoal(false)} />}
    {edit && <AddAgentModal projectId={projectId} suggestion={agent.data} onClose={() => setEdit(false)} />}
  </>;
}

function goalBehavior(goal: { objective: string; ideal_behavior?: string | null }) {
  return goal.ideal_behavior || goal.objective;
}

function AgentOverview({ agent, goals, overview, projectId, onAddGoal }: { agent: Agent; goals: Array<{ id: string; name: string; objective: string; ideal_behavior?: string | null; measurement?: { baseline_id?: string } | null }>; overview?: ReturnType<typeof useAgentOverview>["data"]; projectId: string; onAddGoal: () => void }) {
  const measured = goals.filter((goal) => goal.measurement?.baseline_id);
  return <div className="agent-overview"><section className="identity-card"><div><p className="eyebrow">Responsibility</p><h2>{agent.responsibility || "No responsibility recorded"}</h2></div><dl><div><dt>Code</dt><dd>{agent.code_scopes.length ? agent.code_scopes.join(", ") : "Trace-only"}</dd></div><div><dt>Goals</dt><dd>{goals.length}</dd></div><div><dt>Measured</dt><dd>{measured.length}</dd></div></dl></section>
    <section><div className="section-heading"><div><p className="eyebrow">Outcomes</p><h2>Goals</h2></div><Button onClick={onAddGoal}><Icon name="plus" size={15} />Create goal</Button></div>{goals.length ? <div className="goal-grid">{goals.map((goal) => <Link className="goal-card" key={goal.id} to={`/projects/${projectId}/agents/${agent.id}/goals/${goal.id}`}><div><Status value={goal.measurement?.baseline_id ? "measured" : "not measured"} /><h3>{goal.name}</h3><p>{goalBehavior(goal)}</p></div><Icon name="arrow" /></Link>)}</div> : <Empty title="No goals yet" action={<Button onClick={onAddGoal}>Create first goal</Button>} />}</section>
    <section><div className="section-heading"><div><p className="eyebrow">Latest evidence</p><h2>Measured outcomes</h2></div></div>{overview?.baselines.length ? <div className="list-surface">{overview.baselines.slice(0, 4).map((item, index) => <div className="list-row" key={String(item.baseline_id || index)}><div><strong>{String(item.name || item.baseline_id)}</strong><span>Immutable baseline</span></div><Status value={String(item.state || "complete")} /></div>)}</div> : <div className="quiet-surface">No baseline has been recorded for this agent.</div>}</section></div>;
}

function GoalsList({ projectId, agentId, goals, onAddGoal }: { projectId: string; agentId: string; goals: Array<{ id: string; name: string; objective: string; ideal_behavior?: string | null; state: string; measurement?: { baseline_id?: string } | null }>; onAddGoal: () => void }) {
  return <section><div className="section-heading"><div><p className="eyebrow">Goals</p><h2>Outcomes to improve</h2></div><Button onClick={onAddGoal}><Icon name="plus" size={15} />Create goal</Button></div>{goals.length ? <div className="list-surface">{goals.map((goal) => <Link className="list-row" key={goal.id} to={`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`}><div><strong>{goal.name}</strong><span>{goalBehavior(goal)}</span></div><Status value={goal.measurement?.baseline_id ? "measured" : goal.state} /></Link>)}</div> : <Empty title="No goals yet" action={<Button onClick={onAddGoal}>Create first goal</Button>} />}</section>;
}

function EvidenceView({ overview }: { overview?: ReturnType<typeof useAgentOverview>["data"] }) {
  const groups = [{ name: "Trace snapshots", values: overview?.traces || [] }, { name: "Datasets", values: overview?.datasets || [] }, { name: "Findings", values: overview?.issues || [] }];
  return <div className="evidence-groups">{groups.map((group) => <section key={group.name}><div className="section-heading"><h2>{group.name}</h2><span>{group.values.length}</span></div>{group.values.length ? <div className="list-surface">{group.values.map((item, index) => <div className="list-row" key={String(item.id || item.snapshot_id || item.issue_id || index)}><div><strong>{String(item.name || item.title || item.id || item.snapshot_id || item.issue_id)}</strong><span>{String(item.created_at || item.status || "Saved evidence")}</span></div></div>)}</div> : <div className="quiet-surface">None</div>}</section>)}</div>;
}

function AgentConfiguration({ projectId, agent }: { projectId: string; agent: Agent }) {
  const queryClient = useQueryClient();
  const connectors = useConnectors(projectId);
  const [responsibility, setResponsibility] = useState(agent.responsibility || "");
  const [code, setCode] = useState(agent.code_scopes.join(", "));
  const [shared, setShared] = useState(agent.shared_dependencies.join(", "));
  const [connectorId, setConnectorId] = useState(String(agent.trace_selector?.connection_id || ""));
  const mutation = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${agent.id}`), { name: agent.name, description: responsibility, code_scopes: code.split(",").map((item) => item.trim()).filter(Boolean), shared_dependencies: shared.split(",").map((item) => item.trim()).filter(Boolean), trace_selector: connectorId ? { connection_id: connectorId, project: connectors.data?.connections.find((item) => item.id === connectorId)?.project } : {}, status: "confirmed", expected_revision: agent.revision }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agent.id] }) });
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Responsibility<textarea value={responsibility} onChange={(event) => setResponsibility(event.target.value)} rows={3} /></label><label>Agent code paths<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>Shared dependencies<input value={shared} onChange={(event) => setShared(event.target.value)} /></label><label>Trace binding <span className="optional">Optional</span><select value={connectorId} onChange={(event) => setConnectorId(event.target.value)}><option value="">No trace binding</option>{connectors.data?.connections.map((connector) => <option key={connector.id} value={connector.id}>{connector.name || connector.project_name || connector.provider}</option>)}</select></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={mutation.isPending}>Save configuration</Button></footer></form>;
}

const stages = [
  { id: "define", name: "Define", workflow: "design" },
  { id: "measure", name: "Measure", workflow: "eval" },
  { id: "improve", name: "Improve", workflow: "fix" },
  { id: "review", name: "Review", workflow: "fix" },
] as const;

export function GoalPage({ projectId }: { projectId: string }) {
  const { agentId = "", goalId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const currentStage = search.get("stage") || "define";
  const goal = useGoal(projectId, agentId, goalId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const accept = useMutation({ mutationFn: () => post(projectPath(projectId, `/application-agents/${agentId}/focuses/${goalId}/design/accept`), { expected_revision: goal.data?.measurement_plan?.revision }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId] }) });
  const workflow = currentStage === "define" ? "design" : currentStage === "measure" ? goal.data?.measurement?.evaluation_id ? "baseline" : "eval" : "fix";
  const startTask = useMutation({ mutationFn: () => post<{ id: string }>(projectPath(projectId, "/tasks"), { operation_id: operationId(), workflow, agent_id: agentId, goal_id: goalId, options: {} }), onSuccess: (task) => navigate(`/projects/${projectId}/tasks/${task.id}`) });
  if (!goal.data) return <div className="page-loading">Loading goal…</div>;
  const plan = goal.data.measurement_plan;
  const stageState = { define: plan?.state === "accepted" ? "complete" : plan ? "ready" : "current", measure: goal.data.measurement?.baseline_id ? "complete" : goal.data.measurement?.evaluation_id ? "ready" : "locked", improve: goal.data.measurement?.baseline_id ? "ready" : "locked", review: "locked" } as Record<string, string>;
  return <>
    <PageHeader eyebrow="Goal" title={goal.data.name}><p>{goalBehavior(goal.data)}</p></PageHeader>
    <nav className="stage-rail" aria-label="Goal stages">{stages.map((stage, index) => <button key={stage.id} className={currentStage === stage.id ? "is-active" : ""} onClick={() => setSearch({ stage: stage.id })}><span className={`stage-index stage-${stageState[stage.id]}`}>{stageState[stage.id] === "complete" ? "✓" : index + 1}</span><span><strong>{stage.name}</strong><small>{stageState[stage.id]}</small></span></button>)}</nav>
    <section className="goal-stage"><div className="stage-heading"><p className="eyebrow">{currentStage}</p><h2>{currentStage === "define" ? "Define what good looks like" : currentStage === "measure" ? "Establish the current result" : currentStage === "improve" ? "Run a bounded improvement" : "Choose what to deliver"}</h2></div>
      {currentStage === "define" && <div className="stage-content"><dl className="definition-list"><div><dt>Ideal behavior</dt><dd>{goalBehavior(goal.data)}</dd></div></dl>{plan ? <div className="measurement-plan"><div><h3>Measurement plan</h3><Status value={plan.state || "draft"} /></div>{plan.state !== "accepted" && <Button onClick={() => accept.mutate()} disabled={accept.isPending}>Accept measurement plan</Button>}</div> : <div className="stage-empty"><h3>No measurement plan</h3><Button onClick={() => startTask.mutate()} disabled={startTask.isPending}>{startTask.isPending ? "Starting…" : "Design measurements"}</Button></div>}</div>}
      {currentStage === "measure" && <div className="stage-content"><dl className="definition-list"><div><dt>Evaluator</dt><dd>{goal.data.measurement?.evaluation_id || "Not prepared"}</dd></div><div><dt>Baseline</dt><dd>{goal.data.measurement?.baseline_id || "Not run"}</dd></div></dl><Button onClick={() => startTask.mutate()} disabled={startTask.isPending}>{startTask.isPending ? "Starting…" : goal.data.measurement?.evaluation_id ? "Run baseline" : "Prepare evaluation"}</Button></div>}
      {currentStage === "improve" && <div className="stage-content">{goal.data.measurement?.baseline_id ? <><p className="stage-summary">The frozen baseline will remain the comparison point.</p><Button onClick={() => startTask.mutate()} disabled={startTask.isPending}>{startTask.isPending ? "Starting…" : "Improve agent"}</Button></> : <div className="blocked-state"><h3>Baseline required</h3><Button tone="secondary" onClick={() => setSearch({ stage: "measure" })}>Go to Measure</Button></div>}</div>}
      {currentStage === "review" && <div className="stage-content"><div className="blocked-state"><h3>No verified candidate yet</h3><Button tone="secondary" onClick={() => setSearch({ stage: "improve" })}>Go to Improve</Button></div></div>}
      {startTask.error && <p className="error-banner">{startTask.error.message}</p>}
    </section>
  </>;
}

export function TasksPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const goals = useGoals(projectId, filters.agent_id);
  const tasks = useTasks(projectId, filters);
  const selectAgent = (agentId: string) => setFilters((value) => {
    const next = { ...value };
    if (agentId) next.agent_id = agentId;
    else delete next.agent_id;
    delete next.goal_id;
    return next;
  });
  const selectFilter = (name: string, selected: string) => setFilters((value) => {
    const next = { ...value };
    if (selected) next[name] = selected;
    else delete next[name];
    return next;
  });
  return <><PageHeader eyebrow="Tasks" title="Task history"><p>Questions, approvals, progress, and results.</p></PageHeader><div className="filters"><select aria-label="Filter by agent" value={filters.agent_id || ""} onChange={(event) => selectAgent(event.target.value)}><option value="">All agents</option>{agents.data?.confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select><select aria-label="Filter by goal" value={filters.goal_id || ""} onChange={(event) => selectFilter("goal_id", event.target.value)} disabled={!filters.agent_id}><option value="">All goals</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select><select aria-label="Filter by workflow" value={filters.workflow || ""} onChange={(event) => selectFilter("workflow", event.target.value)}><option value="">All workflows</option><option value="design">Design measurements</option><option value="eval">Prepare evaluation</option><option value="baseline">Run baseline</option><option value="fix">Improve agent</option><option value="audit">Audit agent</option></select><select aria-label="Filter by status" value={filters.status || ""} onChange={(event) => selectFilter("status", event.target.value)}><option value="">All statuses</option><option value="needs_input">Needs input</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="interrupted">Interrupted</option></select></div><TaskList projectId={projectId} tasks={tasks.data?.tasks || []} /></>;
}

export function TaskPage({ projectId }: { projectId: string }) {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  return <TaskDetail projectId={projectId} taskId={taskId} onBack={() => navigate(`/projects/${projectId}/tasks`)} />;
}

function TaskList({ projectId, tasks }: { projectId: string; tasks: Array<{ id: string; title: string; agent_name?: string | null; goal_name?: string | null; workflow: string; state: string; updated_at?: string }> }) {
  return tasks.length ? <div className="task-list list-surface">{tasks.map((task) => <Link className="list-row task-row" to={`/projects/${projectId}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Agent"}{task.goal_name ? ` · ${task.goal_name}` : ""} · {task.workflow}</span></div><div className="row-end"><Status value={task.state} /><time>{task.updated_at ? new Date(task.updated_at).toLocaleDateString() : ""}</time></div></Link>)}</div> : <Empty title="No tasks yet"><p>Start a workflow from a goal or the Skills catalog.</p></Empty>;
}

export function SkillsPage({ projectId }: { projectId: string }) {
  const skills = useSkills();
  const [launch, setLaunch] = useState<SkillDefinition>();
  return <><PageHeader eyebrow="Built-in workflows" title="Skills"><p>Five ways to move an agent from an objective to verified evidence.</p></PageHeader><div className="skill-catalog">{skills.data?.skills.map((skill, index) => <article className="skill-card" key={skill.id}><div className="skill-number">{String(index + 1).padStart(2, "0")}</div><div className="skill-main"><h2>{skill.name}</h2><p>{skill.purpose}</p><dl><div><dt>Inputs</dt><dd>{skill.inputs.join(" · ")}</dd></div><div><dt>Outputs</dt><dd>{skill.outputs.join(" · ")}</dd></div></dl></div><Button tone="secondary" onClick={() => setLaunch(skill)}>Start</Button></article>)}</div>{launch && <LaunchWorkflowModal projectId={projectId} skill={launch} onClose={() => setLaunch(undefined)} />}</>;
}

export function ConnectorsPage({ projectId }: { projectId: string }) {
  const connectors = useConnectors(projectId);
  const types = useConnectorTypes();
  const [connectType, setConnectType] = useState<ConnectorType>();
  return <><PageHeader eyebrow="Project resources" title="Connectors"><p>Trace and dataset providers connected to this project.</p></PageHeader><section><div className="section-heading"><div><p className="eyebrow">Configured</p><h2>Connections</h2></div></div>{connectors.data?.connections.length ? <div className="connector-grid">{connectors.data.connections.map((connector) => <ConnectorCard key={connector.id} projectId={projectId} connector={connector} />)}</div> : <div className="quiet-surface">No providers connected.</div>}</section><section><div className="section-heading"><div><p className="eyebrow">Available</p><h2>Providers</h2></div></div><div className="connector-grid">{types.data?.connector_types.map((type) => <article className="connector-card" key={type.id}><div className="connector-mark">{type.name.slice(0, 1)}</div><div><h3>{type.name}</h3><p>{type.capabilities.join(" · ")}</p></div><Button tone="secondary" onClick={() => setConnectType(type)}>Connect</Button></article>)}</div></section>{connectType && <ConnectModal projectId={projectId} type={connectType} onClose={() => setConnectType(undefined)} />}</>;
}

function ConnectorCard({ projectId, connector }: { projectId: string; connector: ProjectConnector }) {
  const queryClient = useQueryClient();
  const test = useMutation({ mutationFn: () => post(projectPath(projectId, `/connectors/${connector.id}/test`), {}), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "connectors"] }) });
  const disconnect = useMutation({ mutationFn: () => remove(projectPath(projectId, `/connectors/${connector.id}`)), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "connectors"] }) });
  return <article className="connector-card"><div className="connector-mark">{connector.provider.slice(0, 1).toUpperCase()}</div><div><div className="title-status"><h3>{connector.name || connector.project_name || connector.provider}</h3><Status value={connector.status} /></div><p>{connector.provider} · {connector.project_name || connector.project}</p>{connector.last_checked_at && <small>Checked {new Date(connector.last_checked_at).toLocaleString()}</small>}</div><div className="connector-actions"><Button tone="quiet" onClick={() => test.mutate()}>Test</Button><Button tone="quiet" onClick={() => disconnect.mutate()}>Disconnect</Button></div></article>;
}

function ConnectModal({ projectId, type, onClose }: { projectId: string; type: ConnectorType; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [endpoint, setEndpoint] = useState(type.default_endpoint);
  const [apiKey, setApiKey] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [discovery, setDiscovery] = useState<{ discovery_id: string; projects: Array<{ id: string; name: string; selection_id?: string }> }>();
  const [selected, setSelected] = useState("");
  const find = useMutation({ mutationFn: () => post<{ discovery_id: string; projects: Array<{ id: string; name: string; selection_id?: string }> }>(projectPath(projectId, "/connectors/discover"), { provider: type.id, endpoint, credentials: type.id === "langfuse" ? { public_key: publicKey, secret_key: apiKey } : { api_key: apiKey } }), onSuccess: (value) => { setDiscovery(value); if (value.projects.length === 1) setSelected(value.projects[0].selection_id || value.projects[0].id); } });
  const save = useMutation({ mutationFn: () => post(projectPath(projectId, "/connectors"), { discovery_id: discovery!.discovery_id, project: selected }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "connectors"] }); onClose(); } });
  return <Modal title={`Connect ${type.name}`} eyebrow="Connector" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); discovery ? save.mutate() : find.mutate(); }}><label>API URL<input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} required /></label>{type.id === "langfuse" && <label>Public key<input type="password" value={publicKey} onChange={(event) => setPublicKey(event.target.value)} required autoComplete="off" /></label>}<label>{type.id === "langfuse" ? "Secret key" : "API key"}<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required autoComplete="off" /></label>{discovery && <label>Provider project<select value={selected} onChange={(event) => setSelected(event.target.value)} required><option value="">Select a project</option>{discovery.projects.map((project) => <option key={project.selection_id || project.id} value={project.selection_id || project.id}>{project.name || project.id}</option>)}</select></label>}{(find.error || save.error) && <p className="error-banner">{(find.error || save.error)?.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={find.isPending || save.isPending || Boolean(discovery && !selected)}>{discovery ? "Connect" : "Find projects"}</Button></footer></form></Modal>;
}

export function SettingsPage({ projectId }: { projectId: string }) {
  const { section = "assistants" } = useParams();
  const current = section === "privacy" ? "intelligence" : section;
  const sections = [
    { id: "assistants", label: "Assistants" },
    { id: "execution", label: "Execution" },
    { id: "defaults", label: "Defaults" },
    { id: "intelligence", label: "AG Intelligence" },
    { id: "project", label: "Project" },
  ];
  return <><PageHeader eyebrow="Workspace settings" title="Settings" /><nav className="tabs settings-tabs">{sections.map(({ id, label }) => <NavLink key={id} end to={`/projects/${projectId}/settings/${id}`} className={({ isActive }) => isActive || current === id ? "is-active" : ""}>{label}</NavLink>)}</nav>{current === "assistants" && <AssistantSettings />}{current === "execution" && <ExecutionSettings projectId={projectId} />}{current === "defaults" && <ProjectSettings key="defaults" projectId={projectId} mode="defaults" />}{current === "intelligence" && <ProjectSettings key="intelligence" projectId={projectId} mode="intelligence" />}{current === "project" && <ProjectSettings key="project" projectId={projectId} mode="project" />}</>;
}

function AssistantSettings() {
  const assistants = useAssistants();
  const queryClient = useQueryClient();
  const defaults = assistants.data?.defaults || {};
  const [selected, setSelected] = useState(String(defaults.default_agent || "codex"));
  const [model, setModel] = useState(String((defaults.models as Record<string, string> | undefined)?.[selected] || ""));
  const [concurrency, setConcurrency] = useState(Number(defaults.concurrency || 1));
  const [key, setKey] = useState("");
  const [initialized, setInitialized] = useState(false);
  const codexModels = useCodexModels(selected === "codex");
  useEffect(() => {
    if (!assistants.data || initialized) return;
    const agent = String(assistants.data.defaults.default_agent || "codex");
    const models = assistants.data.defaults.models as Record<string, string> | undefined;
    setSelected(agent);
    setModel(String(models?.[agent] || ""));
    setConcurrency(Number(assistants.data.defaults.concurrency || 1));
    setInitialized(true);
  }, [assistants.data, initialized]);
  useEffect(() => {
    if (selected !== "codex" || !codexModels.data?.models.length) return;
    if (!codexModels.data.models.some((entry) => entry.id === model)) {
      setModel(codexModels.data.default_model || codexModels.data.models[0].id);
    }
  }, [codexModels.data, model, selected]);
  const mutation = useMutation({ mutationFn: () => post("/api/assistants", { default_agent: selected, model, concurrency, ...(selected === "claude" && key ? { claude_api_key: key, credential_mode: "keyring" } : {}) }), onSuccess: () => { setKey(""); queryClient.invalidateQueries({ queryKey: ["assistants"] }); } });
  const selectAssistant = (agent: string) => {
    setSelected(agent);
    setModel(String((assistants.data?.defaults.models as Record<string, string> | undefined)?.[agent] || ""));
  };
  const modelField = selected === "codex" && codexModels.data?.models.length
    ? <select value={model} onChange={(event) => setModel(event.target.value)}>{codexModels.data.models.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</select>
    : <input value={model} onChange={(event) => setModel(event.target.value)} />;
  return <><div className="assistant-grid">{assistants.data?.assistants.map((assistant) => <article className={`assistant-card ${selected === assistant.id ? "is-selected" : ""}`} key={assistant.id}><div><h2>{assistant.name}</h2><Status value={assistant.available ? assistant.authenticated === false ? "sign in needed" : "available" : "not available"} /></div>{assistant.version && <p>{assistant.version}</p>}</article>)}</div><form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Default coding assistant<select value={selected} onChange={(event) => selectAssistant(event.target.value)}><option value="codex">Codex</option><option value="claude">Claude</option></select></label><label>Default model{modelField}</label>{codexModels.isError && selected === "codex" && <p className="error-banner">Could not load models from Codex. Enter a model name instead.</p>}<label>Maximum concurrent tasks<input type="number" min={1} max={8} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label>{selected === "claude" && <label>Claude API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /></label>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit">Save settings</Button></footer></form></>;
}

type ExecutionLimits = {
  max_candidates: number;
  max_trials: number;
  max_elapsed_seconds: number;
  parallel_candidates: number;
  parallel_trials: number;
  trial_timeout_seconds: number;
};

type ExecutionProfile = {
  runner?: { kind?: string };
  limits?: Partial<ExecutionLimits>;
  [key: string]: unknown;
};

const defaultExecutionLimits: ExecutionLimits = {
  max_candidates: 6,
  max_trials: 24,
  max_elapsed_seconds: 1800,
  parallel_candidates: 1,
  parallel_trials: 1,
  trial_timeout_seconds: 60,
};

function ExecutionSettings({ projectId }: { projectId: string }) {
  const overview = useOverview(projectId);
  const queryClient = useQueryClient();
  const profiles = (overview.data?.settings.profiles || {}) as Record<string, ExecutionProfile>;
  const localProfile = profiles.local;
  const [limits, setLimits] = useState(defaultExecutionLimits);
  const [initialized, setInitialized] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!overview.data || initialized) return;
    setLimits({ ...defaultExecutionLimits, ...(localProfile?.limits || {}) });
    setInitialized(true);
  }, [initialized, localProfile, overview.data]);
  useEffect(() => {
    if (!saved) return;
    const timeout = window.setTimeout(() => setSaved(false), 3000);
    return () => window.clearTimeout(timeout);
  }, [saved]);
  const setLimit = (name: keyof ExecutionLimits, value: number) => setLimits((current) => ({ ...current, [name]: value }));
  const save = useMutation({
    mutationFn: () => post(projectPath(projectId, "/settings"), {
      scope: "project",
      profile_name: "local",
      profile: {
        ...(localProfile || {}),
        runner: { ...(localProfile?.runner || {}), kind: "local" },
        limits: { ...(localProfile?.limits || {}), ...limits },
      },
      expected_revision: overview.data?.settings.revision,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "overview"] });
      setSaved(true);
    },
  });
  const otherProfiles = Object.entries(profiles).filter(([name]) => name !== "local");
  return <div className="execution-settings">
    {otherProfiles.length > 0 && <div className="list-surface">{otherProfiles.map(([name, profile]) => <div className="list-row" key={name}><div><strong>{name}</strong><span>{String(profile.runner?.kind || "local")}</span></div></div>)}</div>}
    <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
      <div className="section-heading compact-heading"><div><p className="eyebrow">Execution</p><h2>Local profile</h2></div>{localProfile && <Status value="configured" />}</div>
      <div className="execution-limits">
        <label>Candidate changes<input type="number" min={1} required value={limits.max_candidates} onChange={(event) => setLimit("max_candidates", Number(event.target.value))} /></label>
        <label>Total trials<input type="number" min={1} required value={limits.max_trials} onChange={(event) => setLimit("max_trials", Number(event.target.value))} /></label>
        <label>Time limit (minutes)<input type="number" min={1} required value={Math.max(1, Math.round(limits.max_elapsed_seconds / 60))} onChange={(event) => setLimit("max_elapsed_seconds", Number(event.target.value) * 60)} /></label>
        <label>Trial timeout (seconds)<input type="number" min={1} required value={limits.trial_timeout_seconds} onChange={(event) => setLimit("trial_timeout_seconds", Number(event.target.value))} /></label>
        <label>Parallel candidates<input type="number" min={1} max={limits.max_candidates} required value={limits.parallel_candidates} onChange={(event) => setLimit("parallel_candidates", Number(event.target.value))} /></label>
        <label>Parallel trials<input type="number" min={1} max={limits.max_trials} required value={limits.parallel_trials} onChange={(event) => setLimit("parallel_trials", Number(event.target.value))} /></label>
      </div>
      {save.error && <p className="error-banner">{save.error.message}</p>}
      <footer className="form-actions">{saved && <span className="saved-label" role="status">Saved</span>}<Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : localProfile ? "Save profile" : "Create profile"}</Button></footer>
    </form>
  </div>;
}

function ProjectSettings({ projectId, mode }: { projectId: string; mode: string }) {
  const overview = useOverview(projectId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const settings = (overview.data?.settings.settings || {}) as Record<string, Record<string, unknown>>;
  const [traces, setTraces] = useState(String(settings.traces?.state || "unset"));
  const [intelligenceMode, setIntelligenceMode] = useState(String(settings.intelligence?.mode || "ask"));
  const [endpoint, setEndpoint] = useState(String(settings.intelligence?.endpoint || ""));
  const [key, setKey] = useState("");
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (!overview.data || initialized) return;
    const saved = overview.data.settings.settings as Record<string, Record<string, unknown>>;
    setTraces(String(saved.traces?.state || "unset"));
    setIntelligenceMode(String(saved.intelligence?.mode || "ask"));
    setEndpoint(String(saved.intelligence?.endpoint || ""));
    setInitialized(true);
  }, [overview.data, initialized]);
  const save = useMutation({ mutationFn: () => post(projectPath(projectId, "/settings"), mode === "defaults" ? { scope: "project", values: { "traces.state": traces }, unset: [] } : { scope: "project", values: { "intelligence.mode": intelligenceMode, ...(endpoint ? { "intelligence.endpoint": endpoint } : {}) }, unset: endpoint ? [] : ["intelligence.endpoint"], ...(key ? { intelligence_api_key: key } : {}) }), onSuccess: () => { setKey(""); queryClient.invalidateQueries({ queryKey: ["projects", projectId, "overview"] }); } });
  const removeProject = useMutation({ mutationFn: () => remove(projectPath(projectId, "")), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["projects"] }); navigate("/"); } });
  if (mode === "project") return <div className="settings-card danger-card"><h2>Remove project</h2><p>Files and saved evidence stay on disk.</p><Button tone="danger" onClick={() => removeProject.mutate()}>Remove project</Button></div>;
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>{mode === "defaults" ? <label>Use runtime traces<select value={traces} onChange={(event) => setTraces(event.target.value)}><option value="unset">Ask when relevant</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label> : <><label>Intelligence access<select value={intelligenceMode} onChange={(event) => setIntelligenceMode(event.target.value)}><option value="ask">Ask before each request</option><option value="full_access">Allow prepared requests</option></select></label><label>Service URL<input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /></label></>}{save.error && <p className="error-banner">{save.error.message}</p>}<footer className="form-actions"><Button type="submit">Save settings</Button></footer></form>;
}

export function AgentInventoryPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const discoveryAssistant = useDiscoveryAssistant();
  const [edit, setEdit] = useState<Agent>();
  const [add, setAdd] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(""), 3000);
    return () => window.clearTimeout(timeout);
  }, [notice]);
  const discover = useMutation({
    mutationFn: () => post<{ discovered: number; scanned_files: number }>(projectPath(projectId, "/application-agents/discover"), {}),
    onMutate: () => setNotice(""),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] });
      setNotice(result.discovered ? `Found ${result.discovered} ${result.discovered === 1 ? "agent" : "agents"}.` : `Scanned ${result.scanned_files} files. No agents found.`);
    },
  });
  return <><PageHeader eyebrow="Inventory" title="Agents" actions={<><DiscoveryAction assistant={discoveryAssistant} pending={discover.isPending} onDiscover={() => discover.mutate()} onSetup={() => navigate(`/projects/${projectId}/settings/assistants`)} />{discoveryAssistant.ready && <Button tone="secondary" onClick={() => setAdd(true)}>Add manually</Button>}</>} />{notice && <p className="notice-banner" role="status">{notice}</p>}<section><div className="section-heading"><h2>Confirmed</h2><span>{agents.data?.confirmed.length || 0}</span></div><div className="agent-inventory">{agents.data?.confirmed.map((agent) => <article className="inventory-card" key={agent.id}><div><h3>{agent.name}</h3><code>{agent.code_scopes.join(", ")}</code></div><Link to={`/projects/${projectId}/agents/${agent.id}/overview`}>Open</Link></article>)}</div></section><section><div className="section-heading"><h2>Suggestions</h2><span>{agents.data?.suggestions.length || 0}</span></div>{agents.data?.suggestions.length ? <div className="agent-inventory">{agents.data.suggestions.map((agent) => <article className="inventory-card" key={agent.id}><div><h3>{agent.name}</h3><code>{agent.code_scopes[0]}</code></div><Button tone="secondary" onClick={() => setEdit(agent)}>Review</Button></article>)}</div> : <div className="quiet-surface">No suggestions to review.</div>}</section>{discover.error && <p className="error-banner">{discover.error.message}</p>}{(add || edit) && <AddAgentModal projectId={projectId} suggestion={edit} onClose={() => { setAdd(false); setEdit(undefined); }} />}</>;
}
