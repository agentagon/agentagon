import { GoalPresets, GoalRunWorkspace } from "./goal-runs";
import { EvaluationCases } from "./evaluation-cases";
import { EvaluationDesignEditor } from "./evaluations";
import { ExecutionSettingsPanel } from "./execution";
import { ImprovementsView, ProductionView, ProductionAttention } from "./lifecycle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

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
  agentOriginLabel,
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
  useLesson,
  useLessons,
  useOverview,
  useTask,
  useWorkflows,
  useTasks,
} from "./hooks";
import { MeasurementPlanView, RunResultView, StaticResultView } from "./results";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Agent, ConnectorType, Goal, LessonVersion, Project, ProjectConnector, ProjectOverview, ResultKind, ServiceHealth } from "./types";

type DetectionResult = { count: number; state: string; limitations?: string[]; activation_failures?: Array<{ reason?: string; message?: string } | string> };

function useDetectAgents(projectId: string) {
  const cache = useQueryClient();
  const operation = useRef<string | undefined>(undefined);
  return useMutation({ mutationFn: () => { operation.current ||= operationId(); return post<DetectionResult>(projectPath(projectId, "/agents/detect"), { operation_id: operation.current }); }, onSuccess: async () => { operation.current = undefined; await cache.invalidateQueries({ queryKey: ["projects", projectId] }); } });
}

function DetectionNotice({ result }: { result?: DetectionResult }) {
  if (!result) return null;
  const limits = [...(result.limitations || []), ...(result.activation_failures || []).map(item => typeof item === "string" ? item : item.reason || item.message || "An identity could not be activated.")];
  return <div className="detection-notice" role="status"><p>{result.count ? `${result.count} agent${result.count === 1 ? " is" : "s are"} ready to use.` : "No agents were detected. You can add one manually."}</p>{limits.length > 0 && <details><summary>Detection limits</summary><ul>{limits.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}</div>;
}

export function HomePage({ project }: { project: Project }) {
  const agents = useAgents(project.id);
  const tasks = useTasks(project.id);
  const overview = useOverview(project.id);
  const detect = useDetectAgents(project.id);
  if (agents.isLoading || tasks.isLoading || overview.isLoading) return <div className="page-loading" role="status">Loading project overview…</div>;
  const loadError = agents.error || tasks.error || overview.error;
  if (loadError) return <Empty title="Project overview unavailable" action={<Button onClick={() => { agents.refetch(); tasks.refetch(); overview.refetch(); }}>Retry</Button>}><p>{loadError.message}</p></Empty>;
  const active = agents.data?.confirmed || [];
  const attention = tasks.data?.tasks.filter(task => task.needs_attention) || [];
  const results = [...(overview.data?.baselines || []).map(item => ({ kind: "Baseline", item })), ...(overview.data?.runs || []).map(item => ({ kind: "Improvement", item })), ...(overview.data?.audits || []).map(item => ({ kind: "Audit", item }))].slice(0, 5);
  return <>
    <PageHeader title={project.name} actions={<Button tone={active.length ? "secondary" : "primary"} onClick={() => detect.mutate()} disabled={detect.isPending}>{detect.isPending ? "Detecting…" : "Detect agents"}</Button>} />
    {detect.error && <p className="error-banner" role="alert">{detect.error.message}</p>}<DetectionNotice result={detect.data} />
    <section className="overview-section"><div className="section-heading"><h2>Choose an agent</h2>{active.length > 0 && <Link to={`/projects/${project.id}/agents`}>All agents</Link>}</div>{active.length ? <div className="list-surface">{active.slice(0, 8).map(agent => <Link className="list-row" to={`/projects/${project.id}/agents/${agent.id}/overview`} key={agent.id}><div><strong>{agent.name}</strong><span>{agent.responsibility || agent.code_scopes[0] || agentOriginLabel(agent)}</span></div><span>Choose a goal →</span></Link>)}</div> : <div className="quiet-surface">Detect agents in this project, then choose what you want to improve. <Link to={`/projects/${project.id}/agents`}>Add an agent manually</Link></div>}</section>
    {attention.length > 0 && <section id="attention" className="overview-section"><div className="section-heading"><h2>Needs you</h2><Link to={`/projects/${project.id}/tasks`}>All activity</Link></div><div className="list-surface">{attention.slice(0, 5).map(task => <Link className="list-row" to={`/projects/${project.id}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Project"}</span></div><Status value={task.state} /></Link>)}</div></section>}
    <ProductionAttention projectId={project.id} />
    {results.length > 0 && <section className="overview-section"><div className="section-heading"><h2>Recent outcomes</h2></div><div className="list-surface">{results.map(({ kind, item }, index) => <div className="list-row" key={String(item.id || item.run_id || item.audit_id || index)}><div><strong>{String(item.name || item.summary || kind)}</strong><span>{kind}</span></div></div>)}</div></section>}
  </>;
}

const agentTabs = [
  { id: "overview", label: "Overview" },
  { id: "issues", label: "Issues" },
  { id: "evaluations", label: "Evaluations" },
  { id: "changes", label: "Changes" },
  { id: "production", label: "Production" },
  { id: "lessons", label: "Lessons" },
];

export function AgentPage({ projectId }: { projectId: string }) {
  const { agentId = "", tab = "overview" } = useParams();
  const agent = useAgent(projectId, agentId);
  const goals = useGoals(projectId, agentId);
  const overview = useAgentOverview(projectId, agentId);
  const [addGoal, setAddGoal] = useState<"goal" | "existing-evaluation">();
  const [edit, setEdit] = useState(false);
  if (agent.isLoading) return <div className="page-loading">Loading agent…</div>;
  if (agent.error || !agent.data) return <Empty title="Agent unavailable" action={<Button onClick={() => agent.refetch()}>Retry</Button>}><p>{agent.error?.message || "This agent is not available in this project."}</p></Empty>;
  if (![...agentTabs.map(item => item.id), "configuration"].includes(tab)) return <Empty title="Agent page not found" action={<Link to={`/projects/${projectId}/agents/${agentId}/overview`}>Back to agent</Link>} />;
  if (agent.data.status !== "confirmed") return <InactiveAgentPage projectId={projectId} agent={agent.data} />;
  return <>
    <PageHeader title={agent.data.name} actions={<><Link className="button button-secondary" to={`/projects/${projectId}/tasks?agent_id=${agentId}&view=all`}>View activity</Link><Button tone="quiet" onClick={() => setEdit(true)}>Edit identity</Button></>}>{agent.data.responsibility && <p className="agent-header-responsibility">{agent.data.responsibility}</p>}<p className="agent-header-source"><code>{agent.data.code_scopes[0] || agentOriginLabel(agent.data)}</code> · Active agent</p></PageHeader>
    <nav className="tabs" aria-label="Agent sections">{agentTabs.map((item) => <Link key={item.id} to={`/projects/${projectId}/agents/${agentId}/${item.id}`} className={tab === item.id ? "is-active" : ""}>{item.label}</Link>)}</nav>
    {tab === "overview" && (goals.isLoading ? <p role="status">Loading goals…</p> : goals.error ? <p role="alert">{goals.error.message}<Button onClick={() => goals.refetch()}>Retry</Button></p> : <AgentOverview agent={agent.data} goals={goals.data?.goals || []} issues={overview.data?.issues || []} projectId={projectId} onCustom={() => setAddGoal("goal")} />)}
    {tab === "issues" && (overview.isLoading ? <p role="status">Loading issues…</p> : overview.error ? <p role="alert">{overview.error.message}<Button onClick={() => overview.refetch()}>Retry</Button></p> : <AgentIssues projectId={projectId} agentId={agentId} issues={overview.data?.issues || []} />)}
    {tab === "evaluations" && (goals.isLoading || overview.isLoading ? <p role="status">Loading evaluations…</p> : goals.error || overview.error ? <p role="alert">{goals.error?.message || overview.error?.message}<Button onClick={() => { goals.refetch(); overview.refetch(); }}>Retry</Button></p> : <div className="evaluation-workspace"><EvaluationsList projectId={projectId} agentId={agentId} goals={goals.data?.goals || []} overview={overview.data} onCreate={() => setAddGoal("goal")} onReuse={() => setAddGoal("existing-evaluation")} /><EvidenceView overview={overview.data} /></div>)}
    {tab === "changes" && <ImprovementsView projectId={projectId} agentId={agentId} />}
    {tab === "production" && <ProductionView projectId={projectId} agent={agent.data} />}
    {tab === "lessons" && <LessonsView projectId={projectId} agentId={agentId} />}
    {tab === "configuration" && <AgentConfiguration projectId={projectId} agent={agent.data} />}
    {addGoal && <AddGoalModal projectId={projectId} agentId={agentId} intent={addGoal} onClose={() => setAddGoal(undefined)} />}
    {edit && <AddAgentModal projectId={projectId} agent={agent.data} onClose={() => setEdit(false)} />}
  </>;
}

function AgentIssues({ projectId, agentId, issues }: { projectId: string; agentId: string; issues: Array<Record<string, unknown>> }) {
  return <section className="overview-section"><div className="section-heading"><h2>Issues</h2><Link className="button button-secondary" to={`/projects/${projectId}/issues?agent_id=${agentId}`}>Inspect trace</Link></div>{issues.length ? <div className="list-surface">{issues.map((issue, index) => <Link className="list-row" key={String(issue.issue_id || issue.id || index)} to={`/projects/${projectId}/issues/${String(issue.issue_id || issue.id)}`}><div><strong>{String(issue.title || "Untitled issue")}</strong><span>{String(issue.summary || "Evidence retained")}</span></div><Status value={String(issue.status || "open")} /></Link>)}</div> : <p className="quiet-surface">No issues are linked to this agent. Inspect trace evidence or use New work to describe a problem.</p>}</section>;
}

function InactiveAgentPage({ projectId, agent }: { projectId: string; agent: Agent }) {
  const cache = useQueryClient();
  const detect = useDetectAgents(projectId);
  const excluded = agent.status === "archived";
  const [editing, setEditing] = useState(false);
  const restore = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${agent.id}/restore`), { expected_revision: agent.revision }), onSuccess: () => cache.invalidateQueries({ queryKey: ["projects", projectId] }) });
  return <>
    <PageHeader title={agent.name} actions={<Link className="button button-secondary" to={`/projects/${projectId}/agents`}>All agents</Link>}><p>{agent.responsibility || agent.code_scopes[0] || agentOriginLabel(agent)}</p></PageHeader>
    <section className="settings-card form"><h2>{excluded ? "This agent is excluded" : "This agent needs setup"}</h2><p>{excluded ? "Detection keeps this exclusion. Restore the agent when you want to use it again." : "Detection found this identity but could not activate it. Check its details, or detect again after correcting the source."}</p>{excluded ? <Button onClick={() => restore.mutate()} disabled={restore.isPending}>{restore.isPending ? "Restoring…" : "Restore agent"}</Button> : <div className="form-actions"><Button onClick={() => setEditing(true)}>Edit details</Button><Button tone="secondary" onClick={() => detect.mutate()} disabled={detect.isPending}>{detect.isPending ? "Detecting…" : "Detect again"}</Button></div>}
      {(detect.error || restore.error) && <p className="error-banner" role="alert">{detect.error?.message || restore.error?.message}</p>}<DetectionNotice result={detect.data} />
      <details><summary>Identity details</summary><dl className="compact-definition"><div><dt>Code scope</dt><dd>{agent.code_scopes.length ? agent.code_scopes.map(path => <code key={path}>{path}</code>) : "No code scope retained"}</dd></div><div><dt>Shared dependencies</dt><dd>{agent.shared_dependencies.length ? agent.shared_dependencies.map(path => <code key={path}>{path}</code>) : "None"}</dd></div></dl></details>
    </section>
    {editing && <AddAgentModal projectId={projectId} agent={agent} onClose={() => setEditing(false)} />}
  </>;
}

function AgentOverview({ agent, goals, issues, projectId, onCustom }: { agent: Agent; goals: Goal[]; issues: Array<Record<string, unknown>>; projectId: string; onCustom: () => void }) {
  return <div className="agent-overview">
    <GoalPresets key={`${projectId}:${agent.id}`} projectId={projectId} agentId={agent.id} goals={goals} onCustom={onCustom} />
    {goals.length > 0 && <section className="overview-section"><div className="section-heading"><h2>Your goals</h2></div><div className="list-surface">{goals.map(goal => <Link className="list-row" key={goal.id} to={`/projects/${projectId}/agents/${agent.id}/goals/${goal.id}`}><div><strong>{goal.name}</strong><span>{goal.objective}</span></div><span>Open →</span></Link>)}</div></section>}
    {issues.length > 0 && <section className="overview-section"><div className="section-heading"><h2>Issues</h2><Link to={`/projects/${projectId}/agents/${agent.id}/issues`}>All issues</Link></div><div className="list-surface">{issues.slice(0, 5).map((issue, index) => <Link className="list-row" key={String(issue.issue_id || issue.id || index)} to={`/projects/${projectId}/issues/${String(issue.issue_id || issue.id)}`}><div><strong>{String(issue.title || "Untitled issue")}</strong><span>{String(issue.summary || issue.status || "Evidence retained")}</span></div><Status value={String(issue.status || "open")} /></Link>)}</div></section>}
  </div>;
}

function LessonsView({ projectId, agentId }: { projectId: string; agentId: string }) {
  const lessons = useLessons(projectId, agentId);
  const [query, setQuery] = useState("");
  const [citation, setCitation] = useState("all");
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (lessons.data?.lessons || []).filter((lesson) => {
      const matchesText = !needle || [lesson.statement, lesson.key, lesson.source.title || "", lesson.source.workflow || ""].some((value) => value.toLocaleLowerCase().includes(needle));
      const matchesCitation = citation === "all" || (citation === "used" && lesson.used_count > 0) || (citation === "rejected" && lesson.rejected_count > 0) || (citation === "uncited" && lesson.used_count === 0 && lesson.rejected_count === 0);
      return matchesText && matchesCitation;
    });
  }, [citation, lessons.data?.lessons, query]);
  if (lessons.isLoading) return <div className="page-loading">Loading lessons…</div>;
  if (lessons.error) return <p className="error-banner">{lessons.error.message}</p>;
  return <section className="lessons-workspace"><div className="section-heading"><div><p className="eyebrow">Recursive loop</p><h2>Lessons from earlier attempts</h2><p className="quiet-copy">A supplied lesson, a used or rejected citation, and a measured outcome are shown as separate facts.</p></div><Link to={`/projects/${projectId}/settings/data`}>Memory settings</Link></div><div className="lesson-filters"><label>Search lessons<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search retained lesson text" /></label><label>Citation<select value={citation} onChange={(event) => setCitation(event.target.value)}><option value="all">All lessons</option><option value="used">Cited as used</option><option value="rejected">Cited as rejected</option><option value="uncited">No citation yet</option></select></label></div>{visible.length ? <div className="lesson-list">{visible.map((lesson) => <LessonRow key={`${lesson.group_id}:${lesson.id}`} lesson={lesson} projectId={projectId} agentId={agentId} />)}</div> : <Empty title={query || citation !== "all" ? "No lessons match these filters" : "No lessons recorded yet"}><p>Completed attempts and material production outcomes add versioned lessons with evidence. A missing lesson does not hide the task result.</p></Empty>}{lessons.data?.truncated && <p className="quiet-copy">Showing the 500 most recently considered lessons.</p>}</section>;
}

function LessonRow({ lesson, projectId, agentId }: { lesson: LessonVersion; projectId: string; agentId: string }) {
  const considered = lesson.last_considered_at ? new Date(lesson.last_considered_at).toLocaleDateString() : "Not considered by a later task";
  return <Link className={`lesson-row ${lesson.status === "outdated" ? "lesson-row-outdated" : ""}`} to={`/projects/${projectId}/lessons/${encodeURIComponent(lesson.group_id)}/${encodeURIComponent(lesson.id)}?agent_id=${encodeURIComponent(agentId)}`}><div className="lesson-row-main"><div className="lesson-row-meta"><Status value={lesson.status} /><span>{lesson.group.scope === "project" ? "Project lesson" : "Agent lesson"}</span><span>Version {lesson.version}</span><span>{considered}</span></div><strong>{lesson.statement}</strong><span>{lesson.source.title || lesson.source.id}</span></div><div className="lesson-citation-counts"><span><b>{lesson.used_count}</b> used</span><span><b>{lesson.rejected_count}</b> rejected</span><span><b>{lesson.supplied_count}</b> supplied</span></div></Link>;
}

export function LessonPage({ projectId }: { projectId: string }) {
  const { groupId = "", entryId = "" } = useParams();
  const [search] = useSearchParams();
  const agentId = search.get("agent_id") || undefined;
  const lesson = useLesson(projectId, groupId, entryId, agentId);
  const [correction, setCorrection] = useState<"note" | "outdated" | "revise" | null>(null);
  if (lesson.isLoading) return <div className="page-loading">Loading lesson history…</div>;
  if (lesson.error || !lesson.data) return <Empty title="Lesson unavailable"><p>{lesson.error?.message || "This lesson is no longer available in this project scope."}</p></Empty>;
  const latest = lesson.data.latest;
  const back = agentId ? `/projects/${projectId}/agents/${agentId}/lessons` : `/projects/${projectId}/home`;
  const actions = latest.group.writable ? <><Button tone="secondary" onClick={() => setCorrection("note")}>Add note</Button><Button onClick={() => setCorrection("revise")}>Create revised lesson</Button>{latest.status !== "outdated" && <Button tone="danger" onClick={() => setCorrection("outdated")}>Mark outdated</Button>}<Link className="button button-secondary" to={back}>Back to lessons</Link></> : <Link className="button button-secondary" to={back}>Back to lessons</Link>;
  return <>
    <PageHeader eyebrow="Lesson history" title={latest.key} actions={actions}><p>{latest.group.name} · {latest.group.scope === "project" ? "Project-wide" : "Agent-specific"} · version {latest.version}</p></PageHeader>
    {latest.status === "outdated" && <div className="lesson-outdated-banner"><Status value="outdated" /><div><strong>This lesson is no longer recalled for new work.</strong><p>Its evidence and every earlier version remain available here. Create a revised lesson to make a new active version.</p></div></div>}
    <section className="lesson-detail-summary"><div><div className="lesson-current-heading"><p className="eyebrow">Current lesson</p><Status value={latest.status} /></div><p className="lesson-statement">{latest.statement}</p>{latest.revision_note && <p className="lesson-revision-note"><strong>{revisionLabel(latest.revision_kind)}:</strong> {latest.revision_note}</p>}</div><dl className="compact-definition"><div><dt>Recorded</dt><dd>{new Date(latest.created_at).toLocaleString()}</dd></div><div><dt>Uncertainty</dt><dd>{latest.uncertainty || "No uncertainty was recorded."}</dd></div><div><dt>Evidence</dt><dd>{latest.evidence.length} retained reference{latest.evidence.length === 1 ? "" : "s"}</dd></div></dl></section>
    <LessonOrigin lesson={latest} projectId={projectId} />
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Later attempts</p><h2>Where this lesson was supplied or cited</h2></div></div>{latest.considerations.length ? <div className="lesson-considerations">{latest.considerations.map((item) => <article key={item.task_id}><div><Status value={item.decision || "supplied"} /><h3>{item.title}</h3><p>{item.decision ? `${item.decision === "used" ? "Used" : "Rejected"}: ${item.reason}` : "Supplied to the task; no used or rejected citation was recorded."}</p></div><Link to={`/projects/${projectId}/tasks/${item.task_id}`}>View task</Link></article>)}</div> : <div className="quiet-surface">No later task has received this version. This says nothing about whether the lesson is correct or effective.</div>}</section>
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Immutable revisions</p><h2>Version history</h2></div></div><div className="lesson-history">{lesson.data.versions.map((version) => <article key={version.version}><header><div><strong>Version {version.version}</strong><Status value={version.status} /></div><time>{new Date(version.created_at).toLocaleString()}</time></header><p>{version.statement}</p>{version.revision_note && <p className="lesson-history-note"><strong>{revisionLabel(version.revision_kind)}:</strong> {version.revision_note}</p>}<dl className="compact-definition"><div><dt>Uncertainty</dt><dd>{version.uncertainty || "Not recorded"}</dd></div><div><dt>Citations</dt><dd>{version.used_count} used · {version.rejected_count} rejected · {version.supplied_count} supplied</dd></div></dl><EvidenceReferences evidence={version.evidence} projectId={projectId} /></article>)}</div></section>
    {correction && <LessonCorrectionModal mode={correction} lesson={latest} projectId={projectId} agentId={agentId} onClose={() => setCorrection(null)} />}
  </>;
}

function revisionLabel(kind: LessonVersion["revision_kind"]) {
  return { recorded: "Recorded", note: "Note", outdated: "Marked outdated", revised: "Revision" }[kind];
}

function EvidenceReferences({ evidence, projectId }: { evidence: string[]; projectId: string }) {
  return <details><summary>Evidence references ({evidence.length})</summary>{evidence.length ? <ul className="evidence-reference-list">{evidence.map((reference) => <li key={reference}>{reference.startsWith("task_") ? <Link to={`/projects/${projectId}/tasks/${reference}`}>{reference}</Link> : <code>{reference}</code>}</li>)}</ul> : <p>No evidence reference was recorded.</p>}</details>;
}

function LessonCorrectionModal({ mode, lesson, projectId, agentId, onClose }: { mode: "note" | "outdated" | "revise"; lesson: LessonVersion; projectId: string; agentId?: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [statement, setStatement] = useState(lesson.statement);
  const [uncertainty, setUncertainty] = useState(lesson.uncertainty);
  const mutation = useMutation({
    mutationFn: () => post(projectPath(projectId, `/lessons/${encodeURIComponent(lesson.group_id)}/${encodeURIComponent(lesson.id)}`), mode === "note" ? { action: "add_note", agent_id: agentId, expected_version: lesson.version, note } : mode === "outdated" ? { action: "mark_outdated", agent_id: agentId, expected_version: lesson.version, reason: note } : { action: "revise", agent_id: agentId, expected_version: lesson.version, statement, uncertainty, reason: note }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "lessons"] });
      onClose();
    },
  });
  const labels = mode === "note" ? { title: "Add context to this lesson", purpose: "This adds an immutable version. The lesson text, status, and evidence stay unchanged.", field: "Note", submit: "Add note" } : mode === "outdated" ? { title: "Mark lesson outdated", purpose: "The lesson stays in history and will no longer be recalled for new work.", field: "Why is this outdated?", submit: "Mark outdated" } : { title: "Create revised lesson", purpose: "This creates a new active version with the same evidence references.", field: "What changed?", submit: "Create revision" };
  return <Modal title={labels.title} eyebrow="Lesson correction" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>{mode === "revise" && <><label>Lesson<textarea value={statement} onChange={(event) => setStatement(event.target.value)} rows={5} maxLength={28000} required autoFocus /></label><label>Uncertainty <span className="optional">Optional</span><textarea value={uncertainty} onChange={(event) => setUncertainty(event.target.value)} rows={3} maxLength={4000} /></label></>}<label>{labels.field}<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} maxLength={4000} required autoFocus={mode !== "revise"} /></label><p className="modal-purpose">{labels.purpose}</p>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button tone={mode === "outdated" ? "danger" : "primary"} type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : labels.submit}</Button></footer></form></Modal>;
}

function LessonOrigin({ lesson, projectId }: { lesson: LessonVersion; projectId: string }) {
  const learning = lesson.source.learning || {};
  const fields = [["Hypothesis", learning.hypothesis], ["Action", learning.action], ["Test outcome", learning.result], [lesson.source.type === "observation" ? "Production outcome" : "Source outcome", lesson.source.outcome]] as Array<[string, unknown]>;
  const visible = fields.filter(([, value]) => typeof value === "string" && value);
  return <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Origin</p><h2>Why this lesson exists</h2></div>{lesson.source.type === "task" && lesson.source.available && <Link to={`/projects/${projectId}/tasks/${lesson.source.id}`}>View source attempt</Link>}</div>{visible.length ? <dl className="lesson-origin">{visible.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl> : <div className="quiet-surface">The source retained a lesson statement and evidence, but no structured hypothesis or outcome.</div>}<div className="lesson-evidence"><EvidenceReferences evidence={lesson.evidence} projectId={projectId} /></div></section>;
}

function EvaluationsList({ projectId, agentId, goals, overview, onCreate, onReuse }: { projectId: string; agentId: string; goals: Goal[]; overview?: ProjectOverview; onCreate: () => void; onReuse: () => void }) {
  const evaluations = overview?.evaluations || [];
  const baselines = overview?.baselines || [];
  return <section className="evaluations-section"><div className="evaluation-intro"><div><h2>Evaluations</h2></div><div className="evaluation-intro-actions"><Button tone="secondary" onClick={onReuse}>Use existing evaluation</Button><Button onClick={onCreate}><Icon name="plus" size={15} />New goal</Button></div></div>{goals.length ? <div className="evaluation-card-list">{goals.map((goal) => {
    const evaluationId = goal.measurement?.evaluation_id;
    const baselineId = goal.measurement?.baseline_id;
    const evaluation = evaluations.find((item) => item.evaluation_id === evaluationId);
    const baseline = baselines.find((item) => item.baseline_id === baselineId);
    const trials = Array.isArray(evaluation?.trials) ? evaluation.trials as Array<Record<string, unknown>> : [];
    const failures = trials.filter((trial) => trial.state === "failed" || trial.outcome === "failed" || trial.outcome === "reject").length;
    const metrics = evaluation?.metrics && typeof evaluation.metrics === "object" ? Object.keys(evaluation.metrics as Record<string, unknown>).length : 0;
    const next = !evaluationId ? "Go prepares the checks for this goal" : !baselineId ? "Go measures a baseline before working on improvements" : failures ? "Inspect validation failures before improving" : "Ready for measured improvement";
    const state = baselineId ? baseline?.state === "completed" ? "baseline ready" : String(baseline?.state || "baseline recorded") : evaluationId ? String(evaluation?.state || "evaluator ready") : "draft";
    return <Link className="evaluation-card" key={goal.id} to={`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`}><div className="evaluation-card-main"><div className="evaluation-card-heading"><div><span>Goal · {goal.name}</span><h3>{goal.objective}</h3></div><Status value={state} /></div><dl className="evaluation-facts"><div><dt>Evaluator</dt><dd>{evaluationId ? "Prepared" : "Not prepared"}</dd></div><div><dt>Checks</dt><dd>{metrics ? `${metrics} accepted measure${metrics === 1 ? "" : "s"}` : "Plan needed"}</dd></div><div><dt>Baseline</dt><dd>{baselineId ? baseline?.state === "completed" ? "Measured" : String(baseline?.state || "Recorded") : "Not run"}</dd></div><div><dt>Failures</dt><dd>{trials.length ? `${failures} of ${trials.length} validation trials` : "Not checked"}</dd></div></dl><p className="evaluation-next"><strong>Next:</strong> {next}</p></div><span className="evaluation-card-open">Open goal →</span></Link>;
  })}</div> : <div className="evaluation-empty"><div><h3>No evaluations yet</h3><p>Define a behavior to check, or choose an existing evaluator.</p></div></div>}</section>;
}

function EvidenceView({ overview }: { overview?: ReturnType<typeof useAgentOverview>["data"] }) {
  const groups = [{ name: "Trace snapshots", values: overview?.traces || [] }, { name: "Datasets", values: overview?.datasets || [] }, { name: "Findings", values: overview?.issues || [] }].filter(group => group.values.length > 0);
  if (!groups.length) return null;
  return <div className="evidence-groups">{groups.map((group) => <section key={group.name}><div className="section-heading"><h2>{group.name}</h2></div><div className="list-surface">{group.values.map((item, index) => <div className="list-row" key={String(item.id || item.snapshot_id || item.issue_id || index)}><div><strong>{String(item.name || item.title || item.id || item.snapshot_id || item.issue_id)}</strong><span>{String(item.created_at || item.status || "Saved evidence")}</span></div></div>)}</div></section>)}</div>;
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

export function GoalPage({ projectId }: { projectId: string }) {
  const { agentId = "", goalId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const goal = useGoal(projectId, agentId, goalId);
  const editingDesign = search.has("evaluator") || search.get("editor") === "true";
  const [advanced, setAdvanced] = useState(editingDesign || search.has("cases"));
  const [runActive, setRunActive] = useState(true);
  useEffect(() => { if (editingDesign || search.has("cases")) setAdvanced(true); }, [editingDesign, goalId, search]);
  const closeEditor = () => { const next = new URLSearchParams(search); next.delete("evaluator"); next.delete("editor"); setSearch(next, { replace: true }); };
  if (goal.isLoading) return <div className="page-loading" role="status">Loading goal…</div>;
  if (goal.error || !goal.data) return <Empty title="Goal unavailable" action={<Button onClick={() => goal.refetch()}>Retry</Button>}><p>{goal.error?.message || "This goal is not available in this project."}</p></Empty>;
  return <>
    <div className="goal-back"><Link to={`/projects/${projectId}/agents/${agentId}/overview`}>← Back to agent</Link></div>
    <PageHeader eyebrow="Goal" title={goal.data.name}>{goal.data.objective.trim() !== goal.data.name.trim() && <p className="goal-objective">{goal.data.objective}</p>}</PageHeader>
    <GoalRunWorkspace key={`${agentId}:${goalId}`} projectId={projectId} agentId={agentId} goal={goal.data} editing={editingDesign} onActiveChange={setRunActive} />
    <details className="goal-advanced" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}><summary>Evaluation details <span className="optional">Optional</span></summary>
      {goal.data.ideal_behavior && <p className="quiet-copy">{goal.data.ideal_behavior}</p>}
      <EvaluationCases projectId={projectId} agentId={agentId} goalId={goalId} />
      {editingDesign && !runActive ? <EvaluationDesignEditor key={`${agentId}:${goalId}`} projectId={projectId} agentId={agentId} goalId={goalId} defaultReuse={search.get("evaluator") === "reuse"} onSaved={closeEditor} onCancel={closeEditor} /> : <>
        {goal.data.measurement_plan && <MeasurementPlanView plan={goal.data.measurement_plan} />}
        {editingDesign ? <Button tone="secondary" onClick={closeEditor}>Close evaluator editor</Button> : <Button tone="secondary" disabled={runActive} onClick={() => { const next = new URLSearchParams(search); next.set("editor", "true"); setSearch(next); }}>{goal.data.measurement_plan ? "Edit evaluation details" : "Configure an evaluator"}</Button>}
        {runActive && <p className="quiet-copy">Finish or stop this run before changing its evaluation.</p>}
      </>}
    </details>
  </>;
}

export function TasksPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const { taskId } = useParams();
  const selectedTask = useTask(projectId, taskId);
  const [search, setSearch] = useSearchParams();
  const searchKey = search.toString();
  const filters = useMemo<Record<string, string>>(() => Object.fromEntries(["agent_id", "goal_id", "workflow", "status"].flatMap((key) => search.get(key) ? [[key, search.get(key)!]] : [])), [searchKey]);
  const selectedState = selectedTask.data?.state;
  const selectedSegment = selectedTask.data?.needs_attention ? "needs" : selectedState && ["queued", "running"].includes(selectedState) ? "running" : selectedState && ["completed", "completed_with_limits", "failed", "cancelled"].includes(selectedState) ? "finished" : "all";
  const segment = search.get("view") || (taskId ? selectedSegment : "needs");
  const goals = useGoals(projectId, filters.agent_id);
  const tasks = useTasks(projectId, filters);
  const updateSearch = (update: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(search);
    next.delete("task");
    update(next);
    setSearch(next, { replace: true });
  };
  const selectAgent = (agentId: string) => updateSearch((next) => {
    if (agentId) next.set("agent_id", agentId);
    else next.delete("agent_id");
    next.delete("goal_id");
  });
  const selectFilter = (name: string, selected: string) => updateSearch((next) => {
    if (selected) next.set(name, selected);
    else next.delete(name);
  });
  const visibleTasks = (tasks.data?.tasks || []).filter((task) => segment === "all" || (segment === "needs" && task.needs_attention) || (segment === "running" && ["queued", "running"].includes(task.state)) || (segment === "finished" && ["completed", "completed_with_limits", "failed", "cancelled"].includes(task.state)));
  return <><PageHeader eyebrow="Activity" title="Work and results"><p>Answer questions, follow running work, and inspect completed evidence.</p></PageHeader><nav className="activity-segments" aria-label="Activity status">{[{ id: "needs", label: "Needs you" }, { id: "running", label: "Running" }, { id: "finished", label: "Finished" }, { id: "all", label: "All" }].map(item => <button type="button" key={item.id} aria-pressed={segment === item.id} onClick={() => updateSearch((next) => next.set("view", item.id))}>{item.label}</button>)}</nav><details className="filter-disclosure"><summary>Filter activity{Object.keys(filters).length ? ` · ${Object.keys(filters).length} active` : ''}</summary><div className="filters"><select aria-label="Filter by agent" value={filters.agent_id || ""} onChange={(event) => selectAgent(event.target.value)}><option value="">All agents</option>{agents.data?.confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select>{filters.agent_id && <select aria-label="Filter by goal" value={filters.goal_id || ""} onChange={(event) => selectFilter("goal_id", event.target.value)}><option value="">All goals</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select>}<select aria-label="Filter by workflow" value={filters.workflow || ""} onChange={(event) => selectFilter("workflow", event.target.value)}><option value="">All workflows</option><option value="design">Design measurements</option><option value="eval">Prepare evaluation</option><option value="baseline">Run baseline</option><option value="optimize">Improve agent</option><option value="audit">Audit agent</option><option value="assess">Assess project</option><option value="observe">Observe production</option><option value="discover">Discover issues</option><option value="fix">Fix</option></select><select aria-label="Filter by status" value={filters.status || ""} onChange={(event) => selectFilter("status", event.target.value)}><option value="">All statuses</option><option value="needs_input">Needs input</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="interrupted">Interrupted</option></select></div></details><>{tasks.isLoading ? <div className="page-loading" role="status">Loading activity…</div> : tasks.isError ? <Empty title="Activity unavailable" action={<Button onClick={() => tasks.refetch()}>Retry</Button>}><p>{tasks.error.message}</p></Empty> : <TaskList projectId={projectId} tasks={visibleTasks} search={searchKey} />}</></>;
}

export function ResultPage({ projectId }: { projectId: string }) {
  const { workflow = "", runId = "" } = useParams();
  const kinds: ResultKind[] = ["audit", "eval", "baseline", "fix", "optimize", "patch"];
  if (!runId || !kinds.includes(workflow as ResultKind)) return <Empty title="Result not found"><p>This link does not identify a supported task result.</p></Empty>;
  const kind = workflow as ResultKind;
  const titles: Record<ResultKind, string> = { audit: "Audit result", eval: "Evaluation result", baseline: "Baseline result", fix: "Fix result", optimize: "Optimization result", patch: "Reviewed patch" };
  const descriptions: Record<ResultKind, string> = { audit: "Review the requested scope, fixed-rubric findings, evidence, and limits.", eval: "Review the evaluation definition, validation trials, independent review, and delivery options.", baseline: "Review the frozen comparison point, source identity, measurements, and unknowns.", fix: "Compare the current version, inspect verified repairs, and record your decision.", optimize: "Compare every eligible verified alternative and record your decision.", patch: "Review the exact change, checks, independent review, and unmeasured limitations." };
  return <><PageHeader eyebrow="Task result" title={titles[kind]} actions={<Link className="button button-secondary" to={`/projects/${projectId}/tasks`}>Back to activity</Link>}><p>{descriptions[kind]}</p></PageHeader><div className="full-result-page">{kind === "fix" || kind === "optimize" ? <RunResultView projectId={projectId} workflow={kind} runId={runId} /> : <StaticResultView projectId={projectId} workflow={kind} resultId={runId} />}</div></>;
}

function TaskList({ projectId, tasks, search = "" }: { projectId: string; tasks: Array<{ id: string; title: string; agent_name?: string | null; goal_name?: string | null; workflow: string; state: string; updated_at?: string }>; search?: string }) {
  return tasks.length ? <div className="task-list list-surface">{tasks.map((task) => <Link className="list-row task-row" to={{ pathname: `/projects/${projectId}/tasks/${task.id}`, search }} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Project"}{task.goal_name ? ` · ${task.goal_name}` : ""} · {task.workflow}</span></div><div className="row-end"><Status value={task.state} /><time>{task.updated_at ? new Date(task.updated_at).toLocaleDateString() : ""}</time></div></Link>)}</div> : <Empty title="No activity in this view"><p>Choose another segment or start a workflow.</p></Empty>;
}

function ConnectionsSettings({ projectId }: { projectId: string }) {
  const connectors = useConnectors(projectId);
  const types = useConnectorTypes();
  const [connectType, setConnectType] = useState<ConnectorType>();
  return <div className="settings-stack"><section className="settings-section"><div className="section-heading"><div><h2>Connected providers</h2><p className="quiet-copy">Each connection has its own provider project, credentials, and health state.</p></div></div>{connectors.data?.connections.length ? <div className="connector-grid">{connectors.data.connections.map((connector) => <ConnectorCard key={connector.id} projectId={projectId} connector={connector} />)}</div> : <div className="quiet-surface">No provider is connected. You can still assess code or import a trace manually.</div>}</section><section className="settings-section"><div className="section-heading"><div><h2>Add a connection</h2><p className="quiet-copy">Choose a provider, check your credentials, then select the provider project Agentagon may read.</p></div></div><div className="connector-grid">{types.data?.connector_types.map((type) => <article className="connector-card" key={type.id}><div className="connector-mark">{type.name.slice(0, 1)}</div><div><h3>{type.name}</h3><p>{type.capabilities.join(" · ")}</p></div><Button tone="secondary" onClick={() => setConnectType(type)}>Connect {type.name}</Button></article>)}</div></section>{connectType && <ConnectModal projectId={projectId} type={connectType} onClose={() => setConnectType(undefined)} />}</div>;
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

type LocalFunnel = {
  generated_at: string;
  privacy: { automatic_external_transmission: boolean; export: string };
  metrics: {
    registration_to_first_understood_agent: { completed: boolean; duration_seconds?: number | null };
    action_intent_to_accepted_start: { retained_intents: number; accepted: number; blocked: number; abandoned: number };
    prerequisite_blocks: { by_code: Record<string, number>; intent_count: number };
    prerequisite_abandonment: { blocked_intents: number; unresolved: number; abandoned: number; after_seconds: number };
    questions: { total: number; tasks: number; repeated: number; open: number };
    duplicate_submissions: { total: number; task_count: number };
    result_selection: { decisions: number; selected_candidates: number; delivered: number };
  };
  definitions: Record<string, { start: string; end: string }>;
  recent: Record<string, unknown[]>;
  limits: Record<string, number | boolean>;
};

function LocalProductData({ projectId }: { projectId: string }) {
  const overview = useOverview(projectId);
  const queryClient = useQueryClient();
  const funnel = useQuery({ queryKey: ["projects", projectId, "funnel"], queryFn: () => api<LocalFunnel>(projectPath(projectId, "/funnel")) });
  const telemetryEnabled = Boolean((overview.data?.settings.settings as Record<string, Record<string, unknown>> | undefined)?.telemetry?.enabled);
  const [shareTelemetry, setShareTelemetry] = useState(telemetryEnabled);
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (!overview.data || initialized) return;
    setShareTelemetry(telemetryEnabled);
    setInitialized(true);
  }, [initialized, overview.data, telemetryEnabled]);
  const saveTelemetry = useMutation({
    mutationFn: () => post(projectPath(projectId, "/settings"), { scope: "user", values: { "telemetry.enabled": shareTelemetry }, unset: [] }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "overview"] }),
  });
  const download = () => {
    if (!funnel.data) return;
    const url = URL.createObjectURL(new Blob([`${JSON.stringify(funnel.data, null, 2)}\n`], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "agentagon-local-journey-metrics.json";
    link.click();
    URL.revokeObjectURL(url);
  };
  const metrics = funnel.data?.metrics;
  const understood = metrics?.registration_to_first_understood_agent;
  const blockers = Object.entries(metrics?.prerequisite_blocks.by_code || {}).sort((left, right) => right[1] - left[1]);
  return <section className="local-product-data"><div className="section-heading"><div><p className="eyebrow">Local product data</p><h2>Journey friction</h2><p className="quiet-copy">These metrics stay on this machine. Agentagon derives durable facts from agents, tasks, choices, and deliveries, and keeps only a bounded local journal for prepared actions and duplicate starts.</p></div><Button tone="secondary" onClick={download} disabled={!funnel.data}>Export JSON</Button></div>{funnel.error && <p className="error-banner">{funnel.error.message}</p>}{metrics && <><div className="funnel-metric-grid"><article><span>First understood agent</span><strong>{understood?.completed ? understood.duration_seconds === null || understood.duration_seconds === undefined ? "Recorded" : `${Math.round(understood.duration_seconds)}s` : "Not reached"}</strong></article><article><span>Action starts</span><strong>{metrics.action_intent_to_accepted_start.accepted}/{metrics.action_intent_to_accepted_start.retained_intents}</strong></article><article><span>Abandoned blocks</span><strong>{metrics.prerequisite_abandonment.abandoned}</strong></article><article><span>Repeated questions</span><strong>{metrics.questions.repeated}</strong></article><article><span>Duplicate starts reused</span><strong>{metrics.duplicate_submissions.total}</strong></article><article><span>Selections delivered</span><strong>{metrics.result_selection.delivered}/{metrics.result_selection.selected_candidates}</strong></article></div>{blockers.length > 0 && <details className="funnel-details"><summary>Prerequisite blocks · {metrics.prerequisite_abandonment.unresolved} unresolved</summary><dl className="compact-definition">{blockers.map(([code, count]) => <div key={code}><dt>{code.replaceAll("_", " ")}</dt><dd>{count}</dd></div>)}</dl></details>}<details className="funnel-details"><summary>Metric definitions and retention</summary><div className="funnel-definitions">{Object.entries(funnel.data!.definitions).map(([name, definition]) => <article key={name}><h3>{name.replaceAll("_", " ")}</h3><p><strong>Start:</strong> {definition.start}</p><p><strong>End:</strong> {definition.end}</p></article>)}</div><p className="quiet-copy">Generated {new Date(funnel.data!.generated_at).toLocaleString()}. Export is a manual local download; automatic external transmission is off.</p></details></>}
    <form className="settings-card form telemetry-consent" onSubmit={(event) => { event.preventDefault(); saveTelemetry.mutate(); }}><div><h2>Optional anonymous telemetry</h2><p className="quiet-copy">Off by default. If enabled, Agentagon sends only the documented workflow and Intelligence usage fields. It never sends this local funnel, project identifiers, paths, code, traces, prompts, goals, or credentials.</p></div><label>Share anonymous usage telemetry<select value={shareTelemetry ? "enabled" : "disabled"} onChange={(event) => setShareTelemetry(event.target.value === "enabled")}><option value="disabled">Off</option><option value="enabled">On — send bounded anonymous events</option></select></label>{saveTelemetry.error && <p className="error-banner">{saveTelemetry.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={saveTelemetry.isPending || shareTelemetry === telemetryEnabled}>{saveTelemetry.isPending ? "Saving…" : "Save telemetry choice"}</Button></footer></form>
  </section>;
}

export function SettingsPage({ projectId }: { projectId: string }) {
  const { section = "project" } = useParams();
  const activeSection = ["defaults", "privacy", "data"].includes(section) ? "data" : section;
  const sections = [{ id: "project", label: "Project" }, { id: "assistants", label: "Coding backend" }, { id: "connections", label: "Connections" }, { id: "execution", label: "Execution" }, { id: "data", label: "Data & privacy" }];
  return <><PageHeader title="Settings"><p>Configure this project’s source, coding backend, provider access, execution, and data boundaries.</p></PageHeader><nav className="tabs settings-tabs" aria-label="Settings sections">{sections.map((item) => <NavLink key={item.id} to={`/projects/${projectId}/settings/${item.id}`} className={activeSection === item.id ? "is-active" : ""}>{item.label}</NavLink>)}</nav>{activeSection === "project" && <ProjectSettings key="project" projectId={projectId} mode="project" />}{activeSection === "assistants" && <AssistantSettings />}{activeSection === "connections" && <ConnectionsSettings projectId={projectId} />}{activeSection === "execution" && <ProjectSettings key="execution" projectId={projectId} mode="execution" />}{activeSection === "data" && <div className="data-settings-stack"><ProjectSettings key="defaults" projectId={projectId} mode="defaults" /><ProjectSettings key="privacy" projectId={projectId} mode="privacy" /><LocalProductData projectId={projectId} /><MemorySettings projectId={projectId} /></div>}</>;
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
  const availableCodexModels = codexModels.data?.models;
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
    if (selected !== "codex" || !availableCodexModels?.length) return;
    if (!availableCodexModels.some((entry) => entry.id === model)) {
      setModel(codexModels.data?.default_model || availableCodexModels[0].id);
    }
  }, [availableCodexModels, codexModels.data?.default_model, model, selected]);
  const mutation = useMutation({ mutationFn: () => post("/api/assistants", { default_agent: selected, model, concurrency, ...(selected === "claude" && key ? { claude_api_key: key, credential_mode: "keyring" } : {}) }), onSuccess: () => { setKey(""); queryClient.invalidateQueries({ queryKey: ["assistants"] }); } });
  const selectAssistant = (agent: string) => {
    setSelected(agent);
    setModel(String((assistants.data?.defaults.models as Record<string, string> | undefined)?.[agent] || ""));
  };
  const modelField = selected === "codex" && availableCodexModels?.length
    ? <select value={model} onChange={(event) => setModel(event.target.value)}>{availableCodexModels.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</select>
    : <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Use the backend default" />;
  if (assistants.isLoading) return <div className="page-loading" role="status">Checking coding backends…</div>;
  if (assistants.isError) return <Empty title="Coding backend status unavailable" action={<Button onClick={() => assistants.refetch()}>Retry</Button>}><p>{assistants.error.message}</p></Empty>;
  return <div className="settings-stack"><section className="settings-section"><div className="section-heading"><div><h2>Available coding backends</h2><p className="quiet-copy">The selected backend diagnoses evidence and authors changes inside Agentagon-managed tasks.</p></div><Button tone="secondary" disabled={assistants.isFetching} onClick={() => assistants.refetch()}>{assistants.isFetching ? "Checking…" : "Refresh status"}</Button></div><div className="assistant-grid">{assistants.data?.assistants.map((assistant) => <article className={`assistant-card ${selected === assistant.id ? "is-selected" : ""}`} key={assistant.id}><div><h3>{assistant.name}</h3><Status value={assistant.available ? assistant.authenticated === false ? "sign in needed" : assistant.authenticated === true ? "ready" : "authentication unknown" : "not installed"} /></div>{assistant.version && <p>Version {assistant.version}</p>}{assistant.message && <small>{assistant.message}</small>}</article>)}</div></section><form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><div><h2>Default for new tasks</h2><p className="quiet-copy">These defaults apply to new tasks on this machine. Workflow preparation shows the effective backend and model before work starts.</p></div><label>Coding backend<select value={selected} onChange={(event) => selectAssistant(event.target.value)}><option value="codex">Codex</option><option value="claude">Claude</option></select></label><label>Model{modelField}</label>{codexModels.isError && selected === "codex" && <p className="error-banner">Could not load models from Codex. Enter a model name instead.</p>}<label>Maximum concurrent tasks<input type="number" min={1} max={8} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label>{selected === "claude" && <label>Claude API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /><small>Stored as a credential reference; the saved value is never shown here.</small></label>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save coding backend"}</Button></footer></form></div>;
}

function ProjectSettings({ projectId, mode }: { projectId: string; mode: string }) {
  const overview = useOverview(projectId);
  const health = useQuery({ queryKey: ["service", "health"], queryFn: () => api<ServiceHealth>("/api/health") });
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
  if (mode === "execution") return <ExecutionSettingsPanel projectId={projectId} />;
  if (mode === "project") { const project = overview.data?.project; return <div className="project-settings-stack"><section className="settings-card"><h2>Project source</h2><p className="quiet-copy">Agentagon reads the registered local folder and keeps its private task evidence separate from application source.</p>{project && <dl className="compact-definition"><div><dt>Name</dt><dd>{project.name}</dd></div><div><dt>Folder</dt><dd><code>{project.path}</code></dd></div><div><dt>Availability</dt><dd>{project.available === false ? "Folder unavailable" : "Available"}</dd></div><div><dt>Source</dt><dd>{project.source?.kind === "git" ? [project.branch, project.source.revision?.slice(0, 10)].filter(Boolean).join(" · ") || "Git repository" : "Local folder"}</dd></div></dl>}<div className="form-actions"><Link className="button button-secondary" to={`/projects/${projectId}/onboarding`}>Analyze project</Link></div><details className="service-diagnostics"><summary>Service diagnostics</summary>{health.isLoading && <p>Reading service build…</p>}{health.error && <p className="error-banner">{health.error.message}</p>}{health.data && <dl className="compact-definition"><div><dt>Package</dt><dd>{health.data.package_version}</dd></div><div><dt>Build</dt><dd><code>{health.data.build_id}</code></dd></div><div><dt>Frontend assets</dt><dd><code>{health.data.frontend_asset_version}</code></dd></div><div><dt>Service started</dt><dd>{new Date(health.data.service_started_at).toLocaleString()}</dd></div><div><dt>Python</dt><dd>{health.data.python_version}</dd></div><div><dt>State contracts</dt><dd><code>{JSON.stringify(health.data.state_contracts)}</code></dd></div></dl>}</details></section><section className="settings-card danger-card"><h2>Remove project registration</h2><p>Removes this project from the dashboard. Project files and saved evidence remain on disk.</p><Button tone="danger" disabled={removeProject.isPending} onClick={() => removeProject.mutate()}>{removeProject.isPending ? "Removing…" : "Remove registration"}</Button></section></div>; }
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>{mode === "defaults" ? <><div><h2>Trace evidence preference</h2><p className="quiet-copy">Choose whether workflows may propose available runtime traces. Provider collection still requires an explicit connection and scope.</p></div><label>Use runtime traces<select value={traces} onChange={(event) => setTraces(event.target.value)}><option value="unset">Ask when relevant</option><option value="enabled">Allow when explicitly selected</option><option value="disabled">Do not use runtime traces</option></select></label></> : <><div><h2>Optional Intelligence service</h2><p className="quiet-copy">Controls prepared requests to a configured external Intelligence endpoint. Raw project data is not sent by opening this page.</p></div><label>Request approval<select value={intelligenceMode} onChange={(event) => setIntelligenceMode(event.target.value)}><option value="ask">Ask before each request</option><option value="full_access">Allow reviewed prepared requests</option></select></label><label>Service URL <span className="optional">Optional</span><input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>API key <span className="optional">Optional</span><input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /><small>The saved credential is never shown here.</small></label></>}{save.error && <p className="error-banner">{save.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : mode === "defaults" ? "Save trace preference" : "Save Intelligence policy"}</Button></footer></form>;
}

export function AgentInventoryPage({ projectId }: { projectId: string }) {
  const location = useLocation();
  const agents = useAgents(projectId);
  const cache = useQueryClient();
  const detect = useDetectAgents(projectId);
  const [edit, setEdit] = useState<Agent>();
  const [add, setAdd] = useState(false);
  const [excludedAgent, setExcludedAgent] = useState<Agent>();
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const exclude = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${excludedAgent!.id}/exclude`), { expected_revision: excludedAgent!.revision, reason: reason.trim() }), onSuccess: async () => { setExcludedAgent(undefined); setReason(""); await cache.invalidateQueries({ queryKey: ["projects", projectId] }); } });
  const restore = useMutation({ mutationFn: (agent: Agent) => post(projectPath(projectId, `/agents/${agent.id}/restore`), { expected_revision: agent.revision }), onSuccess: () => cache.invalidateQueries({ queryKey: ["projects", projectId] }) });
  const inventory = useMemo(() => {
    const items = [...(agents.data?.confirmed || []), ...(agents.data?.suggestions || []), ...(agents.data?.excluded || [])];
    const needle = query.trim().toLocaleLowerCase();
    return items.filter(agent => {
      const visibleStatus = agent.status === "archived" ? "excluded" : agent.status === "confirmed" ? "active" : "not_active";
      if (status !== "all" && status !== visibleStatus) return false;
      return !needle || [agent.name, agent.responsibility || "", ...agent.code_scopes, ...agent.shared_dependencies].some(value => value.toLocaleLowerCase().includes(needle));
    });
  }, [agents.data, query, status]);
  return <>
    <PageHeader title="Agents" actions={<><Button tone="secondary" onClick={() => setAdd(true)}>Add manually</Button><Button onClick={() => detect.mutate()} disabled={detect.isPending}>{detect.isPending ? "Detecting…" : "Detect agents"}</Button></>} />
    <DetectionNotice result={detect.data || location.state?.detectionResult} />{(detect.error || restore.error) && <p className="error-banner" role="alert">{detect.error?.message || restore.error?.message}</p>}
    <div className="inventory-toolbar"><label><span>Search</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, responsibility, or code path" /></label><label><span>Status</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="all">All agents</option><option value="active">Active</option><option value="excluded">Excluded</option></select></label></div>
    {agents.isLoading ? <div className="page-loading" role="status">Loading agents…</div> : agents.error ? <Empty title="Agent inventory unavailable" action={<Button onClick={() => agents.refetch()}>Retry</Button>}><p>{agents.error.message}</p></Empty> : inventory.length ? <div className="identity-inventory"><div className="identity-inventory-head" aria-hidden="true"><span>Agent</span><span>Responsibility</span><span>Scope</span><span>Status</span><span /></div>{inventory.map(agent => <article className="identity-inventory-row" key={agent.id}><div><strong>{agent.name}</strong><small>{agentOriginLabel(agent)}</small></div><p>{agent.responsibility || "—"}</p><div><code>{agent.code_scopes[0] || String(agent.trace_selector?.name || "Trace selector")}</code>{agent.role && agent.role !== "unknown" && <small>{agent.role.replaceAll("_", " ")}</small>}</div><Status value={agent.status === "confirmed" ? "active" : agent.status === "archived" ? "excluded" : "not active"} /><div className="inventory-row-actions">{agent.status === "confirmed" ? <><Link to={`/projects/${projectId}/agents/${agent.id}/overview`}>Open</Link><Button tone="quiet" onClick={() => setEdit(agent)}>Edit details</Button></> : agent.status === "archived" ? <Button tone="secondary" onClick={() => restore.mutate(agent)} disabled={restore.isPending}>Restore</Button> : <Button tone="secondary" onClick={() => detect.mutate()} disabled={detect.isPending}>Detect agents</Button>}{agent.status !== "archived" && <Button tone="quiet" onClick={() => { setExcludedAgent(agent); setReason(""); exclude.reset(); }}>Exclude</Button>}</div></article>)}</div> : <Empty title={query || status !== "all" ? "No agents match these filters" : "No agents detected yet"}>{query || status !== "all" ? "Clear the search or choose another status." : "Detect agents in this project or add one manually."}</Empty>}
    {add && <AddAgentModal projectId={projectId} onClose={() => setAdd(false)} />}{edit && <AddAgentModal projectId={projectId} agent={edit} onClose={() => setEdit(undefined)} />}
    {excludedAgent && <Modal title={`Exclude ${excludedAgent.name}`} onClose={() => { if (!exclude.isPending) setExcludedAgent(undefined); }}><form className="form" onSubmit={event => { event.preventDefault(); exclude.mutate(); }}><label>Reason<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} rows={2} required autoFocus /></label><p className="quiet-copy">Keeps the agent out of future detection. Its history stays available.</p>{exclude.error && <p className="error-banner" role="alert">{exclude.error.message}</p>}<footer className="form-actions"><Button type="button" tone="secondary" onClick={() => setExcludedAgent(undefined)} disabled={exclude.isPending}>Cancel</Button><Button type="submit" tone="danger" disabled={exclude.isPending}>{exclude.isPending ? "Excluding…" : "Exclude agent"}</Button></footer></form></Modal>}
  </>;
}

export function IssuesPage({ projectId }: {projectId: string}) {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const issues = useQuery({queryKey: ["projects", projectId, "issues"], queryFn: () => api<{issues: Array<{issue_id: string; agent_id?: string; title: string; summary: string; status: string; severity: string; confidence: number; historical_affected_traces: number; verification?: string; tested_revision?: string; evidence: string[]; task_ids: string[]; occurrences: Array<{id:string; trace_ids:string[]; source_id:string}>}>}>(projectPath(projectId, "/issues"))});
  const workflows = useWorkflows();
  const [filters] = useSearchParams();
  const [launch, setLaunch] = useState<{workflow: string; input?: {type: string; id: string}; agent?: string}>();
  type TraceImportDraft = { provider?: string; data?: string; connection?: string; traceId?: string };
  type TraceImportPreview = {
    preview_id: string | null;
    state: string;
    diagnosis_ready: boolean;
    provider?: string | null;
    provider_detected: boolean;
    provider_candidates: string[];
    trace_count: number;
    normalized_spans: number;
    inspected_records: number;
    unusable_records: number;
    incomplete_traces: number;
    redaction: string;
    blockers: Array<{ code?: string; message?: string } | string>;
    limitations: Array<{ code?: string; message?: string } | string>;
    source_complete: boolean;
  };
  const importDraftKey = `agentagon.operation-draft:${projectId}:trace-import`;
  const initialImportDraft = useRef(loadOperationDraft<TraceImportDraft>(importDraftKey));
  const [provider, setProvider] = useState(initialImportDraft.current.provider || "auto");
  const [data, setData] = useState(initialImportDraft.current.data || "");
  const [connection, setConnection] = useState(initialImportDraft.current.connection || "");
  const [traceId, setTraceId] = useState(initialImportDraft.current.traceId || "");
  const [preview, setPreview] = useState<(TraceImportPreview & { request_binding: string })>();
  const connectors = useConnectors(projectId);
  const importPayload = connection ? { connection_id: connection, trace_id: traceId } : { provider, data };
  const importBinding = operationBinding(importPayload);
  const importBindingRef = useRef(importBinding);
  importBindingRef.current = importBinding;
  const importOperation = useSessionOperation(
    importDraftKey,
    importBinding,
    { provider, data, connection, traceId } satisfies TraceImportDraft,
    { deferSavedBinding: initialImportDraft.current.draftOmitted === true && !connection && !data },
  );
  const activePreview = preview?.request_binding === importBinding ? preview : undefined;
  const previewer = useMutation({
    mutationFn: (request: { payload: typeof importPayload; binding: string }) => post<TraceImportPreview>(projectPath(projectId, "/traces/preview"), request.payload),
    onSuccess: (result, request) => {
      if (request.binding === importBindingRef.current) setPreview({ ...result, request_binding: request.binding });
    },
  });
  const importer = useMutation({ mutationFn: (_workflow: string) => post<{id: string}>(projectPath(projectId, "/imports"), { preview_id: activePreview?.preview_id, operation_id: importOperation.operation }), onSuccess: (result, workflow) => { importOperation.clear(); cache.invalidateQueries({queryKey:["projects",projectId,"traces"]}); navigate(`/projects/${projectId}/traces/${encodeURIComponent(result.id)}?intent=${workflow}`); } });
  const editImport = (change: () => void) => { change(); setPreview(undefined); previewer.reset(); importer.reset(); };
  const previewMessage = (item: { message?: string } | string) => typeof item === "string" ? item : item.message || "Review this limitation before continuing.";
  const definition = workflows.data?.workflows.find(item => item.workflow === launch?.workflow);
  const agentFilter = filters.get("agent_id");
  const visibleIssues = issues.data?.issues.filter(issue => !agentFilter || issue.agent_id === agentFilter) || [];
  return <><PageHeader eyebrow="Selected trace evidence" title="Issues" actions={<Button onClick={() => setLaunch({workflow:"fix"})}>Fix a problem</Button>}><p>Discover failures, inspect evidence, and choose a focused repair.</p></PageHeader>
    <section className="settings-card trace-intake"><div className="section-heading"><div><h2>Share a trace</h2><p className="quiet-copy">Check the format, coverage, and redaction before retaining evidence.</p></div>{activePreview && <Status value={activePreview.diagnosis_ready ? "ready to import" : activePreview.state} />}</div><form className="form" onSubmit={event => { event.preventDefault(); previewer.mutate({ payload: importPayload, binding: importBinding }); }}><label>Source<select value={connection} onChange={event => editImport(() => setConnection(event.target.value))}><option value="">Paste or upload data</option>{connectors.data?.connections.map(item => <option value={item.id} key={item.id}>{item.name || item.provider}</option>)}</select></label>{connection ? <label>Provider trace ID<input value={traceId} onChange={event => editImport(() => setTraceId(event.target.value))} required /></label> : <><label>Trace format<select value={provider} onChange={event => editImport(() => setProvider(event.target.value))}><option value="auto">Auto-detect</option>{["otlp","braintrust","langsmith","langfuse","phoenix"].map(value => <option key={value} value={value}>{value}</option>)}</select></label><label>JSON or JSONL<textarea value={data} onChange={event => editImport(() => setData(event.target.value))} required rows={8} /></label><label>Upload trace<input type="file" accept=".json,.jsonl,application/json" onChange={async event => {const file = event.target.files?.[0]; if(file) {if(file.size > 20_000_000) {event.target.setCustomValidity("Choose a trace file smaller than 20 MB."); event.target.reportValidity(); return;} event.target.setCustomValidity(""); const content = await file.text(); editImport(() => setData(content));}}} /></label></>}{previewer.error && <p className="error-banner" role="alert">{previewer.error.message}</p>}<div className="form-actions"><Button type="submit" disabled={previewer.isPending || (!connection && !data.trim()) || (!!connection && !traceId.trim())}>{previewer.isPending ? "Checking trace…" : activePreview ? "Check again" : "Check trace"}</Button></div></form>
      {activePreview && <div className={`trace-preview trace-preview-${activePreview.diagnosis_ready ? "ready" : "blocked"}`}><div className="trace-preview-heading"><div><p className="eyebrow">Import preview</p><h3>{activePreview.diagnosis_ready ? "Trace evidence is ready" : activePreview.state === "needs_format" ? "Choose the matching format" : "This data cannot be investigated yet"}</h3></div><Status value={activePreview.state} /></div><dl className="compact-definition"><div><dt>Format</dt><dd>{activePreview.provider ? `${activePreview.provider}${activePreview.provider_detected ? " · detected" : ""}` : activePreview.provider_candidates.length ? activePreview.provider_candidates.join(" or ") : "Not recognized"}</dd></div><div><dt>Trace identities</dt><dd>{activePreview.trace_count}</dd></div><div><dt>Usable spans</dt><dd>{activePreview.normalized_spans} of {activePreview.inspected_records}</dd></div><div><dt>Coverage</dt><dd>{activePreview.source_complete ? "Complete selection" : `${activePreview.unusable_records} unusable · ${activePreview.incomplete_traces} incomplete`}</dd></div></dl><p className="trace-redaction">{activePreview.redaction}</p>{activePreview.blockers.length > 0 && <div className="trace-preview-messages" role="alert"><strong>Needs attention</strong><ul>{activePreview.blockers.map((item, index) => <li key={index}>{previewMessage(item)}</li>)}</ul></div>}{activePreview.limitations.length > 0 && <details><summary>Known limitations ({activePreview.limitations.length})</summary><ul>{activePreview.limitations.map((item, index) => <li key={index}>{previewMessage(item)}</li>)}</ul></details>}{activePreview.diagnosis_ready && activePreview.preview_id && <div className="trace-preview-confirm"><div><strong>Retain this checked evidence?</strong><p>Importing creates an immutable project snapshot. No repair starts until you choose the next action.</p></div><div className="form-actions"><Button type="button" disabled={importer.isPending} onClick={() => importer.mutate("discover")}>{importer.isPending && importer.variables === "discover" ? "Importing…" : "Import and investigate"}</Button><Button type="button" tone="secondary" disabled={importer.isPending} onClick={() => importer.mutate("fix")}>{importer.isPending && importer.variables === "fix" ? "Importing…" : "Import and prepare fix"}</Button></div></div>}{importer.error && <p className="error-banner" role="alert">{importer.error.message}</p>}</div>}
    </section>
    {issues.error && <p role="alert">{issues.error.message}</p>}<div className="list-surface">{visibleIssues.map(issue => <article className="list-row" key={issue.issue_id}><div><Link className="issue-title-link" to={`/projects/${projectId}/issues/${issue.issue_id}`}><strong>{issue.title}</strong></Link><p>{issue.summary}</p><span>{issue.severity} · {issue.historical_affected_traces} distinct trace{issue.historical_affected_traces === 1 ? "" : "s"} · {Math.round(issue.confidence * 100)}% diagnostic confidence</span>{issue.verification && <p>Verified on revision <code>{issue.tested_revision?.slice(0, 12)}</code>; production recovery has not been established.</p>}</div><div className="row-end"><Status value={issue.status} /><Link className="button button-secondary" to={`/projects/${projectId}/issues/${issue.issue_id}`}>Open issue</Link></div></article>)}</div>{!visibleIssues.length && !issues.isLoading && <div className="quiet-surface">{agentFilter ? "No issues are linked to this agent." : "No issues have been retained yet."}</div>}
    {definition && launch && <LaunchWorkflowModal projectId={projectId} workflow={definition} defaultAgentId={launch.agent} defaultInput={launch.input} onClose={() => setLaunch(undefined)} />}</>;
}

type IssueFacet = {
  state: string;
  active_task_id?: string | null;
  attempts?: Array<{ id: string; workflow: string; state: string; run_id?: string | null }>;
  tested_revision?: string | null;
  changes?: Array<{ run_id?: string; task_id?: string; tested_revision: string }>;
  deployments?: Array<Record<string, unknown>>;
  later_occurrences?: Array<Record<string, unknown>>;
};

type IssueDetail = {
  issue_id: string;
  revision: number;
  agent_id?: string;
  expected_behavior?: string;
  title: string;
  summary: string;
  severity: string;
  confidence: number;
  status: string;
  historical_affected_traces: number;
  occurrences: Array<{ id: string; source_id?: string; source_ids?: string[]; trace_ids?: string[]; observed_ns?: number; basis?: string }>;
  diagnoses: Array<{ reference: string; summary: string; expected_behavior?: string; evidence: string[] }>;
  history: Array<{ event_id?: string; at?: string; kind?: string; action?: string; status?: string; reason?: string; agent_id?: string; expected_behavior?: string }>;
  facets: { triage: IssueFacet; work: IssueFacet; test_verification: IssueFacet; delivery: IssueFacet; production: IssueFacet };
  next_actions: string[];
};

type IssueUpdate = {
  action: "dismiss" | "reopen" | "assign" | "set_expectation";
  expected_revision: number;
  reason?: string;
  agent_id?: string;
  expected_behavior?: string;
};

function IssueTriage({ projectId, issue }: { projectId: string; issue: IssueDetail }) {
  const cache = useQueryClient();
  const agents = useAgents(projectId);
  const [owner, setOwner] = useState(issue.agent_id || "");
  const [expectedBehavior, setExpectedBehavior] = useState(issue.expected_behavior || issue.diagnoses.find(item => item.expected_behavior)?.expected_behavior || "");
  const [expectationReason, setExpectationReason] = useState("");
  const [dismissReason, setDismissReason] = useState("");
  const mutation = useMutation({
    mutationFn: (update: IssueUpdate) => post<IssueDetail>(projectPath(projectId, `/issues/${issue.issue_id}`), update),
    onSuccess: async (_saved, update) => {
      if (update.action === "set_expectation") setExpectationReason("");
      if (update.action === "dismiss") setDismissReason("");
      const affectedAgentId = update.agent_id || issue.agent_id;
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["projects", projectId, "issues"], exact: true }),
        cache.invalidateQueries({ queryKey: ["projects", projectId, "issues", issue.issue_id], exact: true }),
        cache.invalidateQueries({ queryKey: ["projects", projectId, "recommendations"] }),
        cache.invalidateQueries({ queryKey: ["projects", projectId, "production"] }),
        ...(affectedAgentId ? [cache.invalidateQueries({ queryKey: ["projects", projectId, "agents", affectedAgentId, "overview"] })] : []),
      ]);
    },
  });
  const latestDecision = [...issue.history].reverse().find(item => item.action);
  const latestDecisionLabel = latestDecision?.action === "dismiss" ? "marked not actionable" : latestDecision?.action?.replaceAll("_", " ");
  const assigned = agents.data?.confirmed.find(item => item.id === issue.agent_id);
  const pending = mutation.isPending;
  const update = (value: Omit<IssueUpdate, "expected_revision">) => mutation.mutate({ ...value, expected_revision: issue.revision });
  return <section className="overview-section issue-triage"><div className="section-heading"><div><p className="eyebrow">Review</p><h2>Triage and expected behavior</h2></div><span>Revision {issue.revision}</span></div>
    <div className="settings-card issue-triage-summary"><dl className="compact-definition"><div><dt>Issue state</dt><dd><Status value={issue.status === "dismissed" ? "not actionable" : issue.status} /></dd></div><div><dt>Owner</dt><dd>{assigned?.name || (issue.agent_id ? "Assigned agent unavailable" : "Unassigned")}</dd></div><div><dt>Reviewed expectation</dt><dd>{issue.expected_behavior || "Not reviewed yet"}</dd></div></dl>{latestDecision && <div className="issue-decision-provenance"><strong>Latest user decision</strong><span>{latestDecisionLabel}{latestDecision.at ? ` · ${new Date(latestDecision.at).toLocaleString()}` : ""}</span>{latestDecision.reason && <p>{latestDecision.reason}</p>}</div>}</div>
    <div className="issue-triage-actions">
      {!issue.agent_id && <form className="settings-card form" onSubmit={event => { event.preventDefault(); update({ action: "assign", agent_id: owner }); }}><div><h3>Assign an owner</h3><p className="quiet-copy">Choose the agent responsible for this behavior.</p></div><label>Agent<select value={owner} onChange={event => setOwner(event.target.value)} required><option value="">Choose an agent</option>{agents.data?.confirmed.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><Button type="submit" disabled={pending || !owner}>{mutation.variables?.action === "assign" && pending ? "Assigning…" : "Assign agent"}</Button></form>}
      <form className="settings-card form" onSubmit={event => { event.preventDefault(); update({ action: "set_expectation", expected_behavior: expectedBehavior, reason: expectationReason }); }}><div><h3>Correct expected behavior</h3><p className="quiet-copy">Record the behavior a repair must protect. This review stays separate from discovered evidence.</p></div><label>Expected behavior<textarea rows={4} maxLength={8000} value={expectedBehavior} onChange={event => setExpectedBehavior(event.target.value)} required /></label><label>Why is this correct?<textarea rows={2} maxLength={2000} value={expectationReason} onChange={event => setExpectationReason(event.target.value)} required /></label><Button type="submit" disabled={pending || !expectedBehavior.trim() || !expectationReason.trim()}>{mutation.variables?.action === "set_expectation" && pending ? "Saving…" : "Save reviewed expectation"}</Button></form>
      {issue.status === "dismissed" ? <form className="settings-card form" onSubmit={event => { event.preventDefault(); update({ action: "reopen" }); }}><div><h3>Reopen issue</h3><p className="quiet-copy">Return this issue to the active queue while preserving its earlier decision.</p></div><Button type="submit" disabled={pending}>{mutation.variables?.action === "reopen" && pending ? "Reopening…" : "Reopen issue"}</Button></form> : <form className="settings-card form" onSubmit={event => { event.preventDefault(); update({ action: "dismiss", reason: dismissReason }); }}><div><h3>Not actionable</h3><p className="quiet-copy">Remove this issue from active work without deleting its evidence.</p></div><label>Why is this not actionable?<textarea rows={3} maxLength={2000} value={dismissReason} onChange={event => setDismissReason(event.target.value)} required /></label><Button tone="danger" type="submit" disabled={pending || !dismissReason.trim()}>{mutation.variables?.action === "dismiss" && pending ? "Saving…" : "Mark not actionable"}</Button></form>}
    </div>
    {mutation.error && <p className="error-banner" role="alert">{mutation.error.message}</p>}
  </section>;
}

export function IssuePage({ projectId }: { projectId: string }) {
  const { issueId = "" } = useParams();
  const issue = useQuery({ queryKey: ["projects", projectId, "issues", issueId], queryFn: ({ signal }) => api<IssueDetail>(projectPath(projectId, `/issues/${issueId}`), { signal }) });
  const workflows = useWorkflows();
  const [fix, setFix] = useState(false);
  const definition = workflows.data?.workflows.find(item => item.workflow === "fix");
  if (issue.isLoading) return <div className="page-loading">Loading issue evidence…</div>;
  if (issue.isError || !issue.data) return <Empty title="Issue unavailable"><p>{issue.error?.message || "This issue is not available in this project."}</p></Empty>;
  const data = issue.data;
  const latestChange = data.facets.test_verification.changes?.[0];
  const primary = data.next_actions[0];
  const action = data.status === "dismissed" ? null : primary === "inspect_task" && data.facets.work.active_task_id
    ? <Link className="button button-primary" to={`/projects/${projectId}/tasks/${data.facets.work.active_task_id}`}>Inspect active repair</Link>
    : ["review_verified_change", "prepare_local_delivery"].includes(primary) && latestChange?.run_id
      ? <Link className="button button-primary" to={`/projects/${projectId}/results/fix/${encodeURIComponent(latestChange.run_id)}`}>{primary === "review_verified_change" ? "Review verified change" : "Prepare local delivery"}</Link>
      : primary === "record_deployment" && data.agent_id
        ? <Link className="button button-primary" to={`/projects/${projectId}/agents/${data.agent_id}/changes`}>Record deployment</Link>
        : primary === "observe_production" && data.agent_id
          ? <Link className="button button-primary" to={`/projects/${projectId}/agents/${data.agent_id}/production`}>Observe production</Link>
          : <Button onClick={() => setFix(true)}>{primary === "investigate_recurrence" ? "Investigate recurrence" : "Start fix"}</Button>;
  return <>
    <PageHeader title={data.title} actions={<>{action}<Link className="button button-secondary" to={`/projects/${projectId}/issues`}>All issues</Link></>}><p>{data.summary}</p><p className="issue-heading-meta"><Status value={data.status === "dismissed" ? "not actionable" : data.status} /> <span>{data.severity} severity · {data.historical_affected_traces} distinct trace{data.historical_affected_traces === 1 ? "" : "s"} · {Math.round(data.confidence * 100)}% diagnostic confidence</span></p></PageHeader>
    <section className="issue-facet-panel"><div className="issue-facets">{Object.entries(data.facets).map(([name, facet]) => <div key={name}><span>{name.replaceAll("_", " ")}</span><Status value={facet.state} /></div>)}</div>{data.facets.test_verification.state === "verified" && <p>Verified on revision <code>{data.facets.test_verification.tested_revision?.slice(0, 12)}</code>; production is {data.facets.production.state.replaceAll("_", " ")}.</p>}</section>
    <div className="issue-detail-grid"><section><div className="section-heading"><div><p className="eyebrow">Expected and observed</p><h2>What failed</h2></div></div>{data.diagnoses.length ? data.diagnoses.map(diagnosis => <article className="settings-card issue-diagnosis" key={diagnosis.reference}><h3>{diagnosis.summary}</h3><dl className="compact-definition"><div><dt>Expected behavior</dt><dd>{diagnosis.expected_behavior || "Needs confirmation before repair"}</dd></div><div><dt>Evidence</dt><dd>{diagnosis.evidence.length ? diagnosis.evidence.join(" · ") : "Retained diagnosis"}</dd></div></dl><details><summary>Evidence reference</summary><code>{diagnosis.reference}</code></details></article>) : <div className="quiet-surface">No expanded diagnosis is available. Inspect an occurrence before starting a repair.</div>}</section>
      <section><div className="section-heading"><div><p className="eyebrow">Occurrences</p><h2>Where it appeared</h2></div></div>{data.occurrences.length ? <div className="list-surface">{data.occurrences.map(occurrence => { const snapshot = occurrence.source_id?.startsWith("snapshot_") ? occurrence.source_id : occurrence.source_ids?.find(item => item.startsWith("snapshot_")); const trace = occurrence.trace_ids?.[0]; return <article className="list-row" key={occurrence.id}><div><strong>{trace || "Recorded failure"}</strong><span>{occurrence.basis || "Evidence-backed occurrence"}</span></div>{snapshot && trace ? <Link to={`/projects/${projectId}/traces/${snapshot}?trace_id=${encodeURIComponent(trace)}&intent=fix`}>Inspect trace</Link> : <code>{occurrence.source_id || occurrence.id}</code>}</article>; })}</div> : <div className="quiet-surface">No trace occurrences are retained.</div>}</section></div>
    <IssueTriage key={data.issue_id} projectId={projectId} issue={data} />
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Repair history</p><h2>Attempts and evidence</h2></div></div>{data.facets.work.attempts?.length ? <div className="list-surface">{data.facets.work.attempts.map(attempt => <Link className="list-row" key={attempt.id} to={`/projects/${projectId}/tasks/${attempt.id}`}><div><strong>{attempt.workflow.replaceAll("_", " ")}</strong><span>{attempt.run_id ? `Run ${attempt.run_id}` : "Task evidence retained"}</span></div><Status value={attempt.state} /></Link>)}</div> : <div className="quiet-surface">No repair attempt has started.</div>}</section>
    {fix && definition && <LaunchWorkflowModal projectId={projectId} workflow={definition} defaultAgentId={data.agent_id} defaultInput={{ type: "issue", id: data.issue_id }} onClose={() => setFix(false)} />}
  </>;
}

function MemorySettings({projectId}: {projectId: string}) {
  const cache = useQueryClient();
  const agents = useAgents(projectId);
  const groups = useQuery({queryKey:["projects",projectId,"memory"],queryFn:() => api<{groups:Array<{id:string;name:string;purpose:string;path:string;agent_ids:string[];project_ids:string[];write_project_ids:string[]}>}>(projectPath(projectId,"/memory"))});
  const [name,setName] = useState(""); const [folder,setFolder] = useState(""); const [purpose,setPurpose] = useState("improvement"); const [agent,setAgent] = useState("");
  const create = useMutation({mutationFn:() => post(projectPath(projectId,"/memory"),{name,path:folder,purpose,agent_ids:agent?[agent]:[]}),onSuccess:() => {cache.invalidateQueries({queryKey:["projects",projectId,"memory"]});setName("");setFolder("");}});
  return <section className="memory-settings"><div className="section-heading"><div><p className="eyebrow">Advanced data settings</p><h2>Memory groups</h2><p className="quiet-copy">Folder locations and access bindings live here. Read improvement lessons from an agent workspace.</p></div></div>{groups.error && <p className="error-banner">{groups.error.message}</p>}<div className="memory-group-grid">{groups.data?.groups.map(group => <article className="settings-card" key={group.id}><div className="title-status"><h3>{group.name}</h3><Status value={group.purpose === "improvement" ? "improvement lessons" : "target-agent memory"} /></div><dl className="compact-definition"><div><dt>Location</dt><dd><code>{group.path}</code></dd></div><div><dt>Agent access</dt><dd>{group.agent_ids.length ? group.agent_ids.map(id => agents.data?.agents.find(item => item.id === id)?.name || id).join(", ") : "All agents in this project"}</dd></div><div><dt>Project access</dt><dd>{group.project_ids.length} read · {group.write_project_ids.length} write</dd></div></dl></article>)}</div>{!groups.isLoading && !groups.data?.groups.length && <div className="quiet-surface">No memory groups are registered. The first managed workflow creates the default improvement group.</div>}<details className="memory-register-disclosure"><summary>Register another local memory group</summary><form className="settings-card form memory-register" onSubmit={event => {event.preventDefault();create.mutate();}}><label>Name<input value={name} onChange={event => setName(event.target.value)} required /></label><label>Empty folder location<input value={folder} onChange={event => setFolder(event.target.value)} required /></label><label>Purpose<select value={purpose} onChange={event => setPurpose(event.target.value)}><option value="improvement">Improvement lessons</option><option value="agent">Target-agent memory</option></select></label><label>Agent access<select value={agent} onChange={event => setAgent(event.target.value)} required={purpose === "agent"}><option value="">All agents in this project</option>{agents.data?.confirmed.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{create.error && <p role="alert">{create.error.message}</p>}<Button disabled={create.isPending}>{create.isPending ? "Registering…" : "Register group"}</Button></form></details></section>;
}
