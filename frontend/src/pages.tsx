import { SetupBanner, Recommendations, ImprovementsView, ProductionView, ProductionAttention } from "./lifecycle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, operationId, post, projectPath, remove } from "./api";
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
} from "./components";
import {
  useAgent,
  useAgentOverview,
  useAgents,
  useAssistants,
  useConnectors,
  useConnectorTypes,
  useGoal,
  useGoals,
  useOverview,
  useWorkflows,
  useTasks,
} from "./hooks";
import type { Agent, ConnectorType, Project, ProjectConnector, WorkflowDefinition } from "./types";

export function HomePage({ project }: { project: Project }) {
  const agents = useAgents(project.id);
  const tasks = useTasks(project.id);
  const overview = useOverview(project.id);
  const [addAgent, setAddAgent] = useState(false);
  const confirmed = agents.data?.confirmed || [];
  const suggestions = agents.data?.suggestions || [];
  const inventoryCount = agents.data?.agents.length || 0;
  const attention = tasks.data?.tasks.filter((task) => task.needs_attention) || [];
  const attentionCount = attention.length + (suggestions.length ? 1 : 0);
  const results = [
    ...(overview.data?.baselines || []).map((item) => ({ kind: "Baseline", item })),
    ...(overview.data?.runs || []).map((item) => ({ kind: "Improvement", item })),
    ...(overview.data?.audits || []).map((item) => ({ kind: "Audit", item })),
  ].slice(0, 5);
  return <>
    <PageHeader eyebrow="Project home" title={project.name}><p>{project.path}</p></PageHeader><SetupBanner projectId={project.id} /><ProductionAttention projectId={project.id} />
    {!confirmed.length && (suggestions.length ? <section className="onboarding-band"><div className="onboarding-count">01</div><div><h2>Review discovered agents</h2><p>{suggestions.length} code-backed agent{suggestions.length === 1 ? " is" : "s are"} ready for confirmation.</p></div><Link className="button button-primary" to={`/projects/${project.id}/agents`}>Review suggestions</Link></section> : <section className="onboarding-band"><div className="onboarding-count">01</div><div><h2>Add an agent</h2><p>Choose the application agent you want to improve.</p></div><Button onClick={() => setAddAgent(true)}>Add agent</Button></section>)}
    <div className="summary-grid">
      <section className="summary-card"><span className="summary-label">Agent inventory</span><strong>{inventoryCount}</strong><Link to={`/projects/${project.id}/agents`}>{suggestions.length ? `Review ${suggestions.length} suggestion${suggestions.length === 1 ? "" : "s"}` : inventoryCount ? "View agents" : "Open inventory"}<Icon name="arrow" size={15} /></Link></section>
      <section className="summary-card"><span className="summary-label">Needs attention</span><strong>{attentionCount}</strong><div className="summary-card-actions">{suggestions.length > 0 && <Link to={`/projects/${project.id}/agents`}>Review agent suggestions<Icon name="arrow" size={15} /></Link>}{attention.length > 0 && <Link to={`/projects/${project.id}/tasks`}>View tasks<Icon name="arrow" size={15} /></Link>}{!attentionCount && <Link to={`/projects/${project.id}/tasks`}>View tasks<Icon name="arrow" size={15} /></Link>}</div></section>
      <section className="summary-card"><span className="summary-label">Evidence</span><strong>{(overview.data?.datasets.length || 0) + (overview.data?.traces.length || 0)}</strong><span className="summary-note">Datasets and trace snapshots</span></section>
    </div>
    <div className="home-columns"><section id="attention"><div className="section-heading"><div><p className="eyebrow">Attention</p><h2>Work waiting on you</h2></div><Link to={`/projects/${project.id}/tasks`}>All tasks</Link></div>{attentionCount ? <div className="list-surface">{suggestions.length > 0 && <Link className="list-row" to={`/projects/${project.id}/agents`}><div><strong>Review {suggestions.length} discovered agent{suggestions.length === 1 ? "" : "s"}</strong><span>Confirm identities before starting improvement work.</span></div><Status value="review" /></Link>}{attention.slice(0, 5).map((task) => <Link className="list-row" to={`/projects/${project.id}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Agent"} · {task.workflow}</span></div><Status value={task.state} /></Link>)}</div> : <div className="quiet-surface">Nothing needs your attention.</div>}</section>
      <section><div className="section-heading"><div><p className="eyebrow">Recent</p><h2>Measured results</h2></div></div>{results.length ? <div className="list-surface">{results.map(({ kind, item }, index) => <div className="list-row" key={String(item.id || item.run_id || item.audit_id || index)}><div><strong>{String(item.name || item.summary || kind)}</strong><span>{kind}</span></div></div>)}</div> : <div className="quiet-surface">Results appear after the first measurement.</div>}</section></div>
    {addAgent && <AddAgentModal projectId={project.id} onClose={() => setAddAgent(false)} />}
  </>;
}

const agentTabs = ["overview", "goals", "improvements", "production", "evidence", "activity", "configuration"];

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
    {tab === "overview" && <Recommendations projectId={projectId} agentId={agentId} />}
    {tab === "improvements" && <ImprovementsView projectId={projectId} agentId={agentId} />}
    {tab === "production" && <ProductionView projectId={projectId} agent={agent.data} />}
    {tab === "goals" && <GoalsList projectId={projectId} agentId={agentId} goals={goals.data?.goals || []} onAddGoal={() => setAddGoal(true)} />}
    {tab === "evidence" && <EvidenceView overview={overview.data} />}
    {tab === "activity" && <TaskList projectId={projectId} tasks={tasks.data?.tasks || []} />}
    {tab === "configuration" && <AgentConfiguration projectId={projectId} agent={agent.data} />}
    {addGoal && <AddGoalModal projectId={projectId} agentId={agentId} onClose={() => setAddGoal(false)} />}
    {edit && <AddAgentModal projectId={projectId} suggestion={agent.data} onClose={() => setEdit(false)} />}
  </>;
}

function AgentOverview({ agent, goals, overview, projectId, onAddGoal }: { agent: Agent; goals: Array<{ id: string; name: string; objective: string; measurement?: { baseline_id?: string } | null }>; overview?: ReturnType<typeof useAgentOverview>["data"]; projectId: string; onAddGoal: () => void }) {
  const measured = goals.filter((goal) => goal.measurement?.baseline_id);
  return <div className="agent-overview"><section className="identity-card"><div><p className="eyebrow">Responsibility</p><h2>{agent.responsibility || "No responsibility recorded"}</h2></div><dl><div><dt>Code</dt><dd>{agent.code_scopes.length ? agent.code_scopes.join(", ") : "Trace-only"}</dd></div><div><dt>Goals</dt><dd>{goals.length}</dd></div><div><dt>Measured</dt><dd>{measured.length}</dd></div></dl></section>
    <section><div className="section-heading"><div><p className="eyebrow">Outcomes</p><h2>Goals</h2></div><Button onClick={onAddGoal}><Icon name="plus" size={15} />Create goal</Button></div>{goals.length ? <div className="goal-grid">{goals.map((goal) => <Link className="goal-card" key={goal.id} to={`/projects/${projectId}/agents/${agent.id}/goals/${goal.id}`}><div><Status value={goal.measurement?.baseline_id ? "measured" : "not measured"} /><h3>{goal.name}</h3><p>{goal.objective}</p></div><Icon name="arrow" /></Link>)}</div> : <Empty title="No goals yet" action={<Button onClick={onAddGoal}>Create first goal</Button>} />}</section>
    <section><div className="section-heading"><div><p className="eyebrow">Latest evidence</p><h2>Measured outcomes</h2></div></div>{overview?.baselines.length ? <div className="list-surface">{overview.baselines.slice(0, 4).map((item, index) => <div className="list-row" key={String(item.baseline_id || index)}><div><strong>{String(item.name || item.baseline_id)}</strong><span>Immutable baseline</span></div><Status value={String(item.state || "complete")} /></div>)}</div> : <div className="quiet-surface">No baseline has been recorded for this agent.</div>}</section></div>;
}

function GoalsList({ projectId, agentId, goals, onAddGoal }: { projectId: string; agentId: string; goals: Array<{ id: string; name: string; objective: string; state: string; measurement?: { baseline_id?: string } | null }>; onAddGoal: () => void }) {
  return <section><div className="section-heading"><div><p className="eyebrow">Goals</p><h2>Outcomes to improve</h2></div><Button onClick={onAddGoal}><Icon name="plus" size={15} />Create goal</Button></div>{goals.length ? <div className="list-surface">{goals.map((goal) => <Link className="list-row" key={goal.id} to={`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`}><div><strong>{goal.name}</strong><span>{goal.objective}</span></div><Status value={goal.measurement?.baseline_id ? "measured" : goal.state} /></Link>)}</div> : <Empty title="No goals yet" action={<Button onClick={onAddGoal}>Create first goal</Button>} />}</section>;
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
  const mutation = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${agent.id}`), { name: agent.name, description: responsibility, code_scopes: code.split(",").map((item) => item.trim()).filter(Boolean), shared_dependencies: shared.split(",").map((item) => item.trim()).filter(Boolean), trace_selector: connectorId ? { ...agent.trace_selector, connection_id: connectorId, project: connectors.data?.connections.find((item) => item.id === connectorId)?.project } : {}, status: "confirmed", expected_revision: agent.revision }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agent.id] }) });
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Responsibility<textarea value={responsibility} onChange={(event) => setResponsibility(event.target.value)} rows={3} /></label><label>Agent code paths<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>Shared dependencies<input value={shared} onChange={(event) => setShared(event.target.value)} /></label><label>Trace binding <span className="optional">Optional</span><select value={connectorId} onChange={(event) => setConnectorId(event.target.value)}><option value="">No trace binding</option>{connectors.data?.connections.map((connector) => <option key={connector.id} value={connector.id}>{connector.name || connector.project_name || connector.provider}</option>)}</select></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={mutation.isPending}>Save configuration</Button></footer></form>;
}

const stages = [
  { id: "define", name: "Define", workflow: "design" },
  { id: "measure", name: "Measure", workflow: "eval" },
  { id: "improve", name: "Improve", workflow: "optimize" },
  { id: "review", name: "Review", workflow: "optimize" },
] as const;

export function GoalPage({ projectId }: { projectId: string }) {
  const { agentId = "", goalId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const currentStage = search.get("stage") || "define";
  const goal = useGoal(projectId, agentId, goalId);
  const workflows = useWorkflows();
  const [launch, setLaunch] = useState<WorkflowDefinition>();
  const queryClient = useQueryClient();
  const accept = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${agentId}/goals/${goalId}/design/accept`), { expected_revision: goal.data?.measurement_plan?.revision }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId] }) });
  if (!goal.data) return <div className="page-loading">Loading goal…</div>;
  const workflowKey = currentStage === "define" ? "design" : currentStage === "measure" ? goal.data.measurement?.evaluation_id ? "baseline" : "eval" : "optimize";
  const workflow = workflows.data?.workflows.find((item) => item.workflow === workflowKey);
  const plan = goal.data.measurement_plan;
  const stageState = { define: plan?.state === "accepted" ? "complete" : plan ? "ready" : "current", measure: goal.data.measurement?.baseline_id ? "complete" : goal.data.measurement?.evaluation_id ? "ready" : "locked", improve: goal.data.measurement?.baseline_id ? "ready" : "locked", review: "locked" } as Record<string, string>;
  return <>
    <PageHeader eyebrow="Goal" title={goal.data.name}><p>{goal.data.objective}</p></PageHeader>
    <nav className="stage-rail" aria-label="Goal stages">{stages.map((stage, index) => <button key={stage.id} className={currentStage === stage.id ? "is-active" : ""} onClick={() => setSearch({ stage: stage.id })}><span className={`stage-index stage-${stageState[stage.id]}`}>{stageState[stage.id] === "complete" ? "✓" : index + 1}</span><span><strong>{stage.name}</strong><small>{stageState[stage.id]}</small></span></button>)}</nav>
    <section className="goal-stage"><div className="stage-heading"><p className="eyebrow">{currentStage}</p><h2>{currentStage === "define" ? "Define what good looks like" : currentStage === "measure" ? "Establish the current result" : currentStage === "improve" ? "Run a bounded improvement" : "Choose what to deliver"}</h2></div>
      {currentStage === "define" && <div className="stage-content"><dl className="definition-list"><div><dt>Objective</dt><dd>{goal.data.objective}</dd></div>{goal.data.ideal_behavior && <div><dt>Ideal behavior</dt><dd>{goal.data.ideal_behavior}</dd></div>}</dl>{plan ? <div className="measurement-plan"><div><h3>Measurement plan</h3><Status value={plan.state || "draft"} /></div>{plan.state !== "accepted" && <Button onClick={() => accept.mutate()} disabled={accept.isPending}>Accept measurement plan</Button>}</div> : <div className="stage-empty"><h3>No measurement plan</h3><Button onClick={() => workflow && setLaunch(workflow)}>Design measurements</Button></div>}</div>}
      {currentStage === "measure" && <div className="stage-content"><dl className="definition-list"><div><dt>Evaluator</dt><dd>{goal.data.measurement?.evaluation_id || "Not prepared"}</dd></div><div><dt>Baseline</dt><dd>{goal.data.measurement?.baseline_id || "Not run"}</dd></div></dl><Button onClick={() => workflow && setLaunch(workflow)} disabled={!workflow}>{goal.data.measurement?.evaluation_id ? "Run baseline" : "Prepare evaluation"}</Button></div>}
      {currentStage === "improve" && <div className="stage-content">{goal.data.measurement?.baseline_id ? <><p className="stage-summary">The frozen baseline will remain the comparison point.</p><Button onClick={() => workflow && setLaunch(workflow)}>Improve agent</Button></> : <div className="blocked-state"><h3>Baseline required</h3><Button tone="secondary" onClick={() => setSearch({ stage: "measure" })}>Go to Measure</Button></div>}</div>}
      {currentStage === "review" && <div className="stage-content"><div className="blocked-state"><h3>No verified candidate yet</h3><Button tone="secondary" onClick={() => setSearch({ stage: "improve" })}>Go to Improve</Button></div></div>}
    </section>
    {launch && <LaunchWorkflowModal projectId={projectId} workflow={launch} defaultAgentId={agentId} defaultGoalId={goalId} onClose={() => setLaunch(undefined)} />}
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
  return <><PageHeader eyebrow="Tasks" title="Task history"><p>Questions, approvals, progress, and results.</p></PageHeader><div className="filters"><select aria-label="Filter by agent" value={filters.agent_id || ""} onChange={(event) => selectAgent(event.target.value)}><option value="">All agents</option>{agents.data?.confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select><select aria-label="Filter by goal" value={filters.goal_id || ""} onChange={(event) => selectFilter("goal_id", event.target.value)} disabled={!filters.agent_id}><option value="">All goals</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select><select aria-label="Filter by workflow" value={filters.workflow || ""} onChange={(event) => selectFilter("workflow", event.target.value)}><option value="">All workflows</option><option value="design">Design measurements</option><option value="eval">Prepare evaluation</option><option value="baseline">Run baseline</option><option value="optimize">Improve agent</option><option value="audit">Audit agent</option><option value="assess">Assess project</option><option value="observe">Observe production</option><option value="discover">Discover issues</option><option value="fix">Fix</option></select><select aria-label="Filter by status" value={filters.status || ""} onChange={(event) => selectFilter("status", event.target.value)}><option value="">All statuses</option><option value="needs_input">Needs input</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="interrupted">Interrupted</option></select></div><TaskList projectId={projectId} tasks={tasks.data?.tasks || []} /></>;
}

function TaskList({ projectId, tasks }: { projectId: string; tasks: Array<{ id: string; title: string; agent_name?: string | null; goal_name?: string | null; workflow: string; state: string; updated_at?: string }> }) {
  return tasks.length ? <div className="task-list list-surface">{tasks.map((task) => <Link className="list-row task-row" to={`/projects/${projectId}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Agent"}{task.goal_name ? ` · ${task.goal_name}` : ""} · {task.workflow}</span></div><div className="row-end"><Status value={task.state} /><time>{task.updated_at ? new Date(task.updated_at).toLocaleDateString() : ""}</time></div></Link>)}</div> : <Empty title="No tasks yet"><p>Start a workflow from a goal or the Workflows catalog.</p></Empty>;
}

export function WorkflowsPage({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const workflows = useWorkflows();
  const [launch, setLaunch] = useState<WorkflowDefinition>();
  return <><PageHeader eyebrow="Built-in workflows" title="Workflows"><p>Discover issues, fix failures, and optimize broader goals.</p></PageHeader><div className="workflow-catalog">{workflows.data?.workflows.map((workflow, index) => <article className="workflow-card" key={workflow.id}><div className="workflow-number">{String(index + 1).padStart(2, "0")}</div><div className="workflow-main"><h2>{workflow.name}</h2><p>{workflow.purpose}</p><dl><div><dt>Inputs</dt><dd>{workflow.inputs.join(" · ")}</dd></div><div><dt>Outputs</dt><dd>{workflow.outputs.join(" · ")}</dd></div></dl></div><Button tone="secondary" onClick={() => workflow.workflow === "assess" ? navigate(`/projects/${projectId}/onboarding`) : workflow.workflow === "observe" ? navigate(`/projects/${projectId}/agents`) : setLaunch(workflow)}>{workflow.workflow === "observe" ? "Choose agent" : "Start"}</Button></article>)}</div>{launch && <LaunchWorkflowModal projectId={projectId} workflow={launch} onClose={() => setLaunch(undefined)} />}</>;
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
  const sections = ["assistants", "execution", "defaults", "privacy", "project"];
  return <><PageHeader eyebrow="Workspace settings" title="Settings" /><nav className="tabs settings-tabs">{sections.map((name) => <NavLink key={name} to={`/projects/${projectId}/settings/${name}`} className={({ isActive }) => isActive ? "is-active" : ""}>{name}</NavLink>)}</nav>{section === "assistants" && <AssistantSettings />}{section === "execution" && <ProjectSettings key="execution" projectId={projectId} mode="execution" />}{section === "defaults" && <ProjectSettings key="defaults" projectId={projectId} mode="defaults" />}{section === "privacy" && <ProjectSettings key="privacy" projectId={projectId} mode="privacy" />}{section === "project" && <ProjectSettings key="project" projectId={projectId} mode="project" />}</>;
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
  useEffect(() => {
    if (!assistants.data || initialized) return;
    const agent = String(assistants.data.defaults.default_agent || "codex");
    const models = assistants.data.defaults.models as Record<string, string> | undefined;
    setSelected(agent);
    setModel(String(models?.[agent] || ""));
    setConcurrency(Number(assistants.data.defaults.concurrency || 1));
    setInitialized(true);
  }, [assistants.data, initialized]);
  const mutation = useMutation({ mutationFn: () => post("/api/assistants", { default_agent: selected, model, concurrency, ...(selected === "claude" && key ? { claude_api_key: key, credential_mode: "keyring" } : {}) }), onSuccess: () => { setKey(""); queryClient.invalidateQueries({ queryKey: ["assistants"] }); } });
  const selectAssistant = (agent: string) => {
    setSelected(agent);
    setModel(String((assistants.data?.defaults.models as Record<string, string> | undefined)?.[agent] || ""));
  };
  return <><div className="assistant-grid">{assistants.data?.assistants.map((assistant) => <article className={`assistant-card ${selected === assistant.id ? "is-selected" : ""}`} key={assistant.id}><div><h2>{assistant.name}</h2><Status value={assistant.available ? assistant.authenticated === false ? "sign in needed" : "available" : "not available"} /></div>{assistant.version && <p>{assistant.version}</p>}</article>)}</div><form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Default coding assistant<select value={selected} onChange={(event) => selectAssistant(event.target.value)}><option value="codex">Codex</option><option value="claude">Claude</option></select></label><label>Default model<input value={model} onChange={(event) => setModel(event.target.value)} /></label><label>Maximum concurrent tasks<input type="number" min={1} max={8} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label>{selected === "claude" && <label>Claude API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /></label>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit">Save settings</Button></footer></form></>;
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
  if (mode === "execution") return <div className="settings-card"><div className="section-heading"><div><p className="eyebrow">Execution</p><h2>Profiles</h2></div></div><div className="list-surface">{Object.entries(overview.data?.settings.profiles || {}).map(([name, profile]) => <div className="list-row" key={name}><div><strong>{name}</strong><span>{String((profile as { runner?: { kind?: string } }).runner?.kind || "local")}</span></div></div>)}</div></div>;
  if (mode === "project") return <div className="settings-card danger-card"><h2>Remove project</h2><p>Files and saved evidence stay on disk.</p><Button tone="danger" onClick={() => removeProject.mutate()}>Remove project</Button></div>;
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>{mode === "defaults" ? <label>Use runtime traces<select value={traces} onChange={(event) => setTraces(event.target.value)}><option value="unset">Ask when relevant</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label> : <><label>Intelligence access<select value={intelligenceMode} onChange={(event) => setIntelligenceMode(event.target.value)}><option value="ask">Ask before each request</option><option value="full_access">Allow prepared requests</option></select></label><label>Service URL<input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /></label></>}{save.error && <p className="error-banner">{save.error.message}</p>}<footer className="form-actions"><Button type="submit">Save settings</Button></footer></form>;
}

export function AgentInventoryPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const queryClient = useQueryClient();
  const [edit, setEdit] = useState<Agent>();
  const [add, setAdd] = useState(false);
  const discover = useMutation({ mutationFn: () => post(projectPath(projectId, "/agents/discover"), { operation_id: operationId() }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] }) });
  return <><PageHeader eyebrow="Inventory" title="Agents" actions={<><Button tone="secondary" onClick={() => discover.mutate()} disabled={discover.isPending}>Discover agents</Button><Button onClick={() => setAdd(true)}>Add agent</Button></>} /><section><div className="section-heading"><h2>Confirmed</h2><span>{agents.data?.confirmed.length || 0}</span></div><div className="agent-inventory">{agents.data?.confirmed.map((agent) => <article className="inventory-card" key={agent.id}><div><h3>{agent.name}</h3><code>{agent.code_scopes.join(", ")}</code></div><Link to={`/projects/${projectId}/agents/${agent.id}/overview`}>Open</Link></article>)}</div></section><section><div className="section-heading"><h2>Suggestions</h2><span>{agents.data?.suggestions.length || 0}</span></div>{agents.data?.suggestions.length ? <div className="agent-inventory">{agents.data.suggestions.map((agent) => <article className="inventory-card" key={agent.id}><div><h3>{agent.name}</h3><code>{agent.code_scopes[0]}</code></div><Button tone="secondary" onClick={() => setEdit(agent)}>Review</Button></article>)}</div> : <div className="quiet-surface">No suggestions to review.</div>}</section>{discover.error && <p className="error-banner">{discover.error.message}</p>}{(add || edit) && <AddAgentModal projectId={projectId} suggestion={edit} onClose={() => { setAdd(false); setEdit(undefined); }} />}</>;
}

export function IssuesPage({ projectId }: {projectId: string}) {
  const cache = useQueryClient();
  const issues = useQuery({queryKey: ["projects", projectId, "issues"], queryFn: () => api<{issues: Array<{issue_id: string; agent_id?: string; title: string; summary: string; status: string; severity: string; confidence: number; historical_affected_traces: number; verification?: string; tested_revision?: string; evidence: string[]; task_ids: string[]; occurrences: Array<{id:string; trace_ids:string[]; source_id:string}>}>}>(projectPath(projectId, "/issues"))});
  const workflows = useWorkflows();
  const [launch, setLaunch] = useState<{workflow: string; input?: {type: string; id: string}; agent?: string}>();
  const [provider, setProvider] = useState("otlp");
  const [data, setData] = useState("");
  const [connection, setConnection] = useState("");
  const [traceId, setTraceId] = useState("");
  const connectors = useConnectors(projectId);
  const [importOperation, setImportOperation] = useState(operationId);
  const importer = useMutation({mutationFn: (_workflow: string) => post<{id: string}>(projectPath(projectId, "/traces/import"), connection ? {operation_id: importOperation, connection_id: connection, trace_id: traceId} : {operation_id: importOperation, provider, data}), onSuccess: (result, workflow) => {setImportOperation(operationId()); cache.invalidateQueries({queryKey:["projects",projectId,"traces"]}); setLaunch({workflow, input:{type:"trace",id:result.id}});}});
  const definition = workflows.data?.workflows.find(item => item.workflow === launch?.workflow);
  return <><PageHeader eyebrow="Selected trace evidence" title="Issues" actions={<Button onClick={() => setLaunch({workflow:"fix"})}>Fix a problem</Button>}><p>Discover failures, inspect evidence, and choose a focused repair.</p></PageHeader>
    <section className="settings-card trace-intake"><h2>Share a trace</h2><form className="form" onSubmit={event => {event.preventDefault(); importer.mutate("discover");}}><label>Source<select value={connection} onChange={event => {setConnection(event.target.value);setImportOperation(operationId());}}><option value="">Paste or upload data</option>{connectors.data?.connections.map(item => <option value={item.id} key={item.id}>{item.name || item.provider}</option>)}</select></label>{connection ? <label>Provider trace ID<input value={traceId} onChange={event => {setTraceId(event.target.value);setImportOperation(operationId());}} required /></label> : <><label>Trace format<select value={provider} onChange={event => {setProvider(event.target.value);setImportOperation(operationId());}}>{["otlp","braintrust","langsmith","langfuse","phoenix"].map(value => <option key={value}>{value}</option>)}</select></label><label>JSON or JSONL<textarea value={data} onChange={event => {setData(event.target.value);setImportOperation(operationId());}} required /></label><label>Upload trace<input type="file" accept=".json,.jsonl,application/json" onChange={async event => {const file = event.target.files?.[0]; if(file) {if(file.size > 20_000_000) {event.target.setCustomValidity("Choose a trace file smaller than 20 MB."); event.target.reportValidity(); return;} event.target.setCustomValidity(""); setData(await file.text());setImportOperation(operationId());}}} /></label></>}{importer.error && <p role="alert">{importer.error.message}</p>}<Button disabled={importer.isPending}>Import and discover issues</Button><Button type="button" tone="secondary" disabled={importer.isPending || (!connection && !data) || (!!connection && !traceId)} onClick={() => importer.mutate("fix")}>Import and fix</Button></form></section>
    {issues.error && <p role="alert">{issues.error.message}</p>}<div className="list-surface">{issues.data?.issues.map(issue => <article className="list-row" key={issue.issue_id}><div><strong>{issue.title}</strong><p>{issue.summary}</p><span>{issue.severity} · {issue.historical_affected_traces} traces · {Math.round(issue.confidence * 100)}% confidence</span>{issue.verification && <p>Verified on revision <code>{issue.tested_revision?.slice(0, 12)}</code>. Production recovery has not been established.</p>}<details className="issue-evidence"><summary>Evidence and repair attempts</summary><IssueDiagnosis projectId={projectId} issueId={issue.issue_id} /><ul>{issue.occurrences?.map(item => <li key={item.id}>Trace {item.trace_ids.join(", ")} · <code>{item.source_id}</code></li>)}</ul>{issue.evidence?.map(ref => <code key={ref}>{ref}</code>)}{issue.task_ids?.map(id => <Link key={id} to={`/projects/${projectId}/tasks/${id}`}>Inspect task {id}</Link>)}</details></div><div className="row-end"><Status value={issue.status} /><Button tone="secondary" onClick={() => setLaunch({workflow:"fix", input:{type:"issue",id:issue.issue_id}, agent:issue.agent_id})}>Fix</Button></div></article>)}</div>
    {definition && launch && <LaunchWorkflowModal projectId={projectId} workflow={definition} defaultAgentId={launch.agent} defaultInput={launch.input} onClose={() => setLaunch(undefined)} />}</>;
}

export function MemoryPage({projectId}: {projectId: string}) {
  const cache = useQueryClient();
  const agents = useAgents(projectId);
  const groups = useQuery({queryKey:["projects",projectId,"memory"],queryFn:() => api<{groups:Array<{id:string;name:string;purpose:string;path:string;agent_ids:string[]}>}>(projectPath(projectId,"/memory"))});
  const [name,setName] = useState(""); const [folder,setFolder] = useState(""); const [purpose,setPurpose] = useState("improvement"); const [agent,setAgent] = useState("");
  const [selected,setSelected] = useState(""); const [query,setQuery] = useState(""); const [key,setKey] = useState(""); const [text,setText] = useState(""); const [evidence,setEvidence] = useState("");
  const create = useMutation({mutationFn:() => post(projectPath(projectId,"/memory"),{name,path:folder,purpose,agent_ids:agent?[agent]:[]}),onSuccess:() => {cache.invalidateQueries({queryKey:["projects",projectId,"memory"]});setName("");setFolder("");}});
  const recall = useMutation({mutationFn:() => post<{entries:Array<{id:string;text:string;version:number;evidence:string[]}>}>(projectPath(projectId,`/memory/${selected}/recall`),{query,agent_id:agent || null})});
  const record = useMutation({mutationFn:() => post(projectPath(projectId,`/memory/${selected}/record`),{agent_id:agent || null,entry:{key,text,evidence:evidence.split("\n").filter(Boolean)}}),onSuccess:() => {setText("");recall.mutate();}});
  return <><PageHeader eyebrow="Folder-backed groups" title="Memory"><p>Keep improvement lessons and target-agent memory in separate groups with explicit access.</p></PageHeader><div className="list-surface memory-groups">{groups.data?.groups.map(group => <button aria-pressed={selected === group.id} className="list-row" key={group.id} onClick={() => {setSelected(group.id);setAgent(group.agent_ids[0] || "");recall.reset();}}><strong>{group.name}</strong><span>{group.purpose} · {group.path}</span></button>)}</div><form className="settings-card form memory-register" onSubmit={event => {event.preventDefault();create.mutate();}}><h2>Register a group</h2><label>Name<input value={name} onChange={event => setName(event.target.value)} required /></label><label>Empty folder location<input value={folder} onChange={event => setFolder(event.target.value)} required /></label><label>Purpose<select value={purpose} onChange={event => setPurpose(event.target.value)}><option value="improvement">Improvement lessons</option><option value="agent">Target-agent memory</option></select></label><label>Agent access<select value={agent} onChange={event => setAgent(event.target.value)} required={purpose === "agent"}><option value="">All agents in this project</option>{agents.data?.confirmed.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{create.error && <p role="alert">{create.error.message}</p>}<Button disabled={create.isPending}>Register group</Button></form>{groups.error && <p className="error-banner">{groups.error.message}</p>}{selected && <section className="settings-card memory-editor"><h2>Recall and record</h2><form className="form" onSubmit={event => {event.preventDefault();recall.mutate();}}><label>Search lessons<input value={query} onChange={event => setQuery(event.target.value)} /></label><Button>Recall</Button></form>{recall.error && <p role="alert">{recall.error.message}</p>}{recall.data?.entries.map(entry => <article className="memory-entry" key={entry.id}><p>{entry.text}</p><small>Version {entry.version} · {entry.evidence.join(" · ")}</small></article>)}<form className="form" onSubmit={event => {event.preventDefault();record.mutate();}}><label>Stable entry key<input required value={key} onChange={event => setKey(event.target.value)} /></label><label>Lesson<textarea required value={text} onChange={event => setText(event.target.value)} /></label><label>Evidence references, one per line<textarea value={evidence} onChange={event => setEvidence(event.target.value)} /></label>{record.error && <p role="alert">{record.error.message}</p>}<Button disabled={record.isPending}>Record version</Button></form></section>}</>;
}


function IssueDiagnosis({projectId, issueId}: {projectId: string; issueId: string}) {
  const [show, setShow] = useState(false);
  const result = useQuery({queryKey:["projects",projectId,"issues",issueId], enabled:show, queryFn:() => api<{diagnoses:Array<{reference:string; summary:string; expected_behavior?:string; evidence:string[]}>}>(projectPath(projectId,`/issues/${issueId}`))});
  return <><Button tone="quiet" onClick={() => setShow(true)}>Read diagnosis</Button>{result.error && <p role="alert">{result.error.message}</p>}{result.data?.diagnoses.map(item => <div key={item.reference}><p>{item.summary}</p>{item.expected_behavior && <p>Expected: {item.expected_behavior}</p>}<ul>{item.evidence.map((text,index) => <li key={index}>{text}</li>)}</ul></div>)}</>;
}

export function GoalsPage({projectId}: {projectId:string}) {
  const agents = useAgents(projectId);
  return <><PageHeader eyebrow="Broader improvement" title="Goals"><p>Define expected behavior and compare measured progress for each agent.</p></PageHeader>{agents.data?.confirmed.map(agent => <AgentGoals key={agent.id} projectId={projectId} agent={agent} />)}{!agents.data?.confirmed.length && <Empty title="Add an agent to define goals" action={<Link to={`/projects/${projectId}/agents`}>Open agents</Link>} />}</>;
}

function AgentGoals({projectId, agent}: {projectId:string;agent:Agent}) {
  const goals = useGoals(projectId,agent.id);
  const [add,setAdd] = useState(false);
  return <section><div className="section-heading"><h2>{agent.name}</h2><Button tone="secondary" onClick={() => setAdd(true)}>Add goal</Button></div><div className="goal-grid">{goals.data?.goals.map(goal => <Link key={goal.id} className="goal-card" to={`/projects/${projectId}/agents/${agent.id}/goals/${goal.id}`}><div><Status value={goal.state} /><h3>{goal.name}</h3><p>{goal.objective}</p></div></Link>)}</div>{goals.error && <p role="alert">{goals.error.message}</p>}{add && <AddGoalModal projectId={projectId} agentId={agent.id} onClose={() => setAdd(false)} />}</section>;
}
