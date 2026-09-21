import { SetupBanner, Recommendations, ImprovementsView, ProductionView, ProductionAttention } from "./lifecycle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, operationId, post, projectPath, remove } from "./api";
import {
  AddAgentModal,
  AddGoalModal,
  Button,
  Empty,
  IdentityReviewModal,
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
  useLesson,
  useLessons,
  useOverview,
  useTask,
  useWorkflows,
  useTasks,
} from "./hooks";
import { MeasurementPlanView, RunResultView, StaticResultView } from "./results";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Agent, ConnectorType, Goal, LessonVersion, Project, ProjectConnector, ProjectOverview, ResultKind, ServiceHealth, WorkflowDefinition } from "./types";

export function HomePage({ project }: { project: Project }) {
  const agents = useAgents(project.id);
  const tasks = useTasks(project.id);
  const overview = useOverview(project.id);
  const suggestions = agents.data?.suggestions || [];
  const confirmed = agents.data?.confirmed || [];
  const attention = tasks.data?.tasks.filter((task) => task.needs_attention) || [];
  const attentionCount = attention.length + (suggestions.length ? 1 : 0);
  const results = [
    ...(overview.data?.baselines || []).map((item) => ({ kind: "Baseline", item })),
    ...(overview.data?.runs || []).map((item) => ({ kind: "Improvement", item })),
    ...(overview.data?.audits || []).map((item) => ({ kind: "Audit", item })),
  ].slice(0, 5);
  return <>
    <PageHeader title={project.name} actions={<><Link className="button button-secondary" to={`/projects/${project.id}/issues`}>All issues</Link><Link className="button button-primary" to={`/projects/${project.id}/onboarding`}>Analyze project</Link></>}><p>{confirmed.length} confirmed · {suggestions.length} suggested</p></PageHeader>
    <SetupBanner projectId={project.id} />
    {attentionCount > 0 && <section id="attention" className="overview-section"><div className="section-heading"><div><p className="eyebrow">Needs you</p><h2>Choose what happens next</h2></div><Link to={`/projects/${project.id}/tasks`}>All activity</Link></div><div className="list-surface">{attention.slice(0, 5).map((task) => <Link className="list-row" to={`/projects/${project.id}/tasks/${task.id}`} key={task.id}><div><strong>{task.title}</strong><span>{task.agent_name || "Project"} · {task.workflow}</span></div><Status value={task.state} /></Link>)}{suggestions.length > 0 && <Link className="list-row" to={`/projects/${project.id}/agents`}><div><strong>Review discovered agents</strong><span>{suggestions.length} suggested identit{suggestions.length === 1 ? "y" : "ies"}; choose only the ones you want to use.</span></div><Status value="review" /></Link>}</div></section>}
    <ProductionAttention projectId={project.id} />
    {confirmed[0] && <Recommendations projectId={project.id} limit={1} />}
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Agents</p><h2>What you are maintaining</h2></div><Link to={`/projects/${project.id}/agents`}>All agents</Link></div>{confirmed.length ? <div className="list-surface">{confirmed.slice(0, 6).map((agent) => <Link className="list-row" to={`/projects/${project.id}/agents/${agent.id}/overview`} key={agent.id}><div><strong>{agent.name}</strong><span>{agent.responsibility || agent.responsibility_inference?.reason || "Responsibility needs review"}</span></div><Status value={agent.status} /></Link>)}</div> : <div className="quiet-surface">Analyze this project to find application agents, or add one manually.</div>}</section>
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Recent outcomes</p><h2>What changed</h2></div></div>{results.length ? <div className="list-surface">{results.map(({ kind, item }, index) => <div className="list-row" key={String(item.id || item.run_id || item.audit_id || index)}><div><strong>{String(item.name || item.summary || kind)}</strong><span>{kind}</span></div></div>)}</div> : <div className="quiet-surface">Verified comparisons and audit outcomes will appear here.</div>}</section>
  </>;
}

const agentTabs = [
  { id: "overview", label: "Overview" },
  { id: "evaluations", label: "Evaluations" },
  { id: "improvements", label: "Improvements" },
  { id: "production", label: "Production" },
];

export function AgentPage({ projectId }: { projectId: string }) {
  const { agentId = "", tab = "overview" } = useParams();
  const agent = useAgent(projectId, agentId);
  const goals = useGoals(projectId, agentId);
  const overview = useAgentOverview(projectId, agentId);
  const tasks = useTasks(projectId, { agent_id: agentId });
  const [addGoal, setAddGoal] = useState<"goal" | "evaluation" | "existing-evaluation">();
  const [edit, setEdit] = useState(false);
  if (agent.isLoading) return <div className="page-loading">Loading agent…</div>;
  if (!agent.data) return <Empty title="Agent not found" />;
  if (agent.data.status !== "confirmed") return <UnconfirmedAgentPage projectId={projectId} agent={agent.data} />;
  const visibleTab = ["goals", "evidence"].includes(tab) ? "evaluations" : tab;
  return <>
    <PageHeader title={agent.data.name} actions={<><Link className="button button-secondary" to={`/projects/${projectId}/agents/${agentId}/lessons`}>Lessons</Link><Link className="button button-secondary" to={`/projects/${projectId}/tasks?agent_id=${agentId}`}>View activity</Link><Button tone="secondary" onClick={() => setEdit(true)}>Edit identity</Button></>}><p className="agent-header-responsibility">{agent.data.responsibility || agent.data.responsibility_inference?.reason || "Responsibility needs review"}</p><p className="agent-header-source"><code>{agent.data.code_scopes[0] || "Trace-backed identity"}</code> · {agent.data.status === "confirmed" ? "Confirmed identity" : "Suggested identity"}</p></PageHeader>
    <nav className="tabs" aria-label="Agent sections">{agentTabs.map((item) => <Link key={item.id} to={`/projects/${projectId}/agents/${agentId}/${item.id}`} className={visibleTab === item.id ? "is-active" : ""}>{item.label}</Link>)}</nav>
    {tab === "overview" && <Recommendations projectId={projectId} agentId={agentId} />}
    {tab === "overview" && <AgentOverview agent={agent.data} goals={goals.data?.goals || []} issues={overview.data?.issues || []} projectId={projectId} />}
    {["evaluations", "goals", "evidence"].includes(tab) && <div className="evaluation-workspace"><EvaluationsList projectId={projectId} agentId={agentId} goals={goals.data?.goals || []} overview={overview.data} onCreate={() => setAddGoal("evaluation")} onReuse={() => setAddGoal("existing-evaluation")} onAddGoal={() => setAddGoal("goal")} /><EvidenceView overview={overview.data} /></div>}
    {tab === "improvements" && <ImprovementsView projectId={projectId} agentId={agentId} />}
    {tab === "production" && <ProductionView projectId={projectId} agent={agent.data} />}
    {tab === "lessons" && <LessonsView projectId={projectId} agentId={agentId} />}
    {tab === "activity" && <TaskList projectId={projectId} tasks={tasks.data?.tasks || []} />}
    {tab === "configuration" && <AgentConfiguration projectId={projectId} agent={agent.data} />}
    {addGoal && <AddGoalModal projectId={projectId} agentId={agentId} intent={addGoal} onClose={() => setAddGoal(undefined)} />}
    {edit && <AddAgentModal projectId={projectId} suggestion={agent.data} onClose={() => setEdit(false)} />}
  </>;
}

function UnconfirmedAgentPage({ projectId, agent }: { projectId: string; agent: Agent }) {
  const [review, setReview] = useState(false);
  const excluded = agent.status === "archived";
  const evidenceCount = agent.evidence?.length || 0;
  return <>
    <PageHeader eyebrow={excluded ? "Excluded identity" : "Suggested identity"} title={agent.name} actions={<><Link className="button button-secondary" to={`/projects/${projectId}/agents`}>Back to inventory</Link><Button onClick={() => setReview(true)}>{excluded ? "Inspect identity" : "Review exact scope"}</Button></>}>
      <p>{excluded ? "This identity remains outside Agentagon’s active workspace." : "Review the retained evidence and exact scope before creating persistent goals, evaluations, improvements, or monitors."}</p>
    </PageHeader>
    <section className="identity-workspace-gate">
      <div className="identity-workspace-gate-copy"><Status value={excluded ? "excluded" : "suggested"} /><h2>{excluded ? "Restore this suggestion before confirming it" : "Confirm this identity to open its workspace"}</h2><p>{excluded ? "Inspect the exclusion reason and retained discovery evidence. Restoring it returns the identity to review without enabling write actions." : "The suggested identity is read only. You can inspect its evidence, confirm this exact scope, exclude it, or run a read-only audit. Agentagon will not create goals, change code, or enable monitoring until you confirm it."}</p></div>
      <dl className="compact-definition"><div><dt>Responsibility</dt><dd>{agent.responsibility || agent.responsibility_inference?.reason || "Needs review"}</dd></div><div><dt>Code scope</dt><dd>{agent.code_scopes.length ? agent.code_scopes.map((path) => <code key={path}>{path}</code>) : "No code path retained"}</dd></div><div><dt>Shared scope</dt><dd>{agent.shared_dependencies.length ? agent.shared_dependencies.map((path) => <code key={path}>{path}</code>) : "None"}</dd></div><div><dt>Discovery evidence</dt><dd>{evidenceCount} retained source{evidenceCount === 1 ? "" : "s"}</dd></div><div><dt>Allowed now</dt><dd>{excluded ? "Inspect or restore suggestion" : "Review, confirm, exclude, or run a read-only audit"}</dd></div></dl>
      <div className="form-actions"><Button onClick={() => setReview(true)}>{excluded ? "Inspect exclusion and evidence" : "Review, confirm, or audit"}</Button></div>
    </section>
    {review && <IdentityReviewModal projectId={projectId} identity={agent} onClose={() => setReview(false)} />}
  </>;
}

function AgentOverview({ agent, goals, issues, projectId }: { agent: Agent; goals: Array<{ id: string; name: string; objective: string; measurement?: { baseline_id?: string } | null }>; issues: Array<Record<string, unknown>>; projectId: string }) {
  const measured = goals.filter((goal) => goal.measurement?.baseline_id);
  return <div className="agent-overview"><section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Open issues</p><h2>Problems supported by evidence</h2></div><Link to={`/projects/${projectId}/issues?agent_id=${agent.id}`}>All issues</Link></div>{issues.length ? <div className="list-surface">{issues.slice(0, 5).map((issue, index) => <Link className="list-row" key={String(issue.issue_id || issue.id || index)} to={`/projects/${projectId}/issues/${String(issue.issue_id || issue.id)}`}><div><strong>{String(issue.title || "Untitled issue")}</strong><span>{String(issue.summary || issue.status || "Evidence retained")}</span></div><Status value={String(issue.status || "open")} /></Link>)}</div> : <div className="quiet-surface">No issues are currently linked to this agent.</div>}</section><section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Evaluations</p><h2>Accepted behavior and baselines</h2></div><Link to={`/projects/${projectId}/agents/${agent.id}/evaluations`}>{goals.length ? "Open evaluations" : "Create evaluation"}</Link></div>{goals.length ? <div className="list-surface">{goals.slice(0, 4).map((goal) => <Link className="list-row" key={goal.id} to={`/projects/${projectId}/agents/${agent.id}/goals/${goal.id}`}><div><strong>{goal.name}</strong><span>{goal.objective}</span></div><Status value={goal.measurement?.baseline_id ? "measured" : "needs preparation"} /></Link>)}</div> : <div className="quiet-surface">Define one behavior to check consistently before broader optimization.</div>}</section><nav className="agent-shortcuts" aria-label="Agent details"><Link to={`/projects/${projectId}/agents/${agent.id}/evaluations`}><strong>{goals.length}</strong><span>Evaluations</span></Link><Link to={`/projects/${projectId}/issues?agent_id=${agent.id}`}><strong>{issues.length}</strong><span>Issues</span></Link><Link to={`/projects/${projectId}/tasks?agent_id=${agent.id}&view=all`}><strong>View</strong><span>Activity</span></Link><Link to={`/projects/${projectId}/agents/${agent.id}/lessons`}><strong>{measured.length ? "Learn" : "View"}</strong><span>Lessons</span></Link></nav></div>;
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

function EvaluationsList({ projectId, agentId, goals, overview, onCreate, onReuse, onAddGoal }: { projectId: string; agentId: string; goals: Goal[]; overview?: ProjectOverview; onCreate: () => void; onReuse: () => void; onAddGoal: () => void }) {
  const evaluations = overview?.evaluations || [];
  const baselines = overview?.baselines || [];
  return <section className="evaluations-section"><div className="evaluation-intro"><div><p className="eyebrow">Evaluations</p><h2>Define what this agent should do and check it consistently</h2><p>Each evaluation connects an objective to accepted checks, an evaluator, and a reproducible baseline.</p></div><div className="evaluation-intro-actions"><Button tone="secondary" onClick={onReuse}>Use existing evaluation</Button><Button onClick={onCreate}><Icon name="plus" size={15} />Create evaluation</Button></div></div>{goals.length ? <div className="evaluation-card-list">{goals.map((goal) => {
    const evaluationId = goal.measurement?.evaluation_id;
    const baselineId = goal.measurement?.baseline_id;
    const evaluation = evaluations.find((item) => item.evaluation_id === evaluationId);
    const baseline = baselines.find((item) => item.baseline_id === baselineId);
    const trials = Array.isArray(evaluation?.trials) ? evaluation.trials as Array<Record<string, unknown>> : [];
    const failures = trials.filter((trial) => trial.state === "failed" || trial.outcome === "failed" || trial.outcome === "reject").length;
    const metrics = evaluation?.metrics && typeof evaluation.metrics === "object" ? Object.keys(evaluation.metrics as Record<string, unknown>).length : 0;
    const next = !evaluationId ? "Review the behavior and prepare an evaluator" : !baselineId ? "Run a baseline on the committed source" : failures ? "Inspect validation failures before improving" : "Ready for measured improvement";
    const state = baselineId ? baseline?.state === "completed" ? "baseline ready" : String(baseline?.state || "baseline recorded") : evaluationId ? String(evaluation?.state || "evaluator ready") : "draft";
    return <Link className="evaluation-card" key={goal.id} to={`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`}><div className="evaluation-card-main"><div className="evaluation-card-heading"><div><span>Goal · {goal.name}</span><h3>{goal.objective}</h3></div><Status value={state} /></div><dl className="evaluation-facts"><div><dt>Evaluator</dt><dd>{evaluationId ? "Prepared" : "Not prepared"}</dd></div><div><dt>Checks</dt><dd>{metrics ? `${metrics} accepted measure${metrics === 1 ? "" : "s"}` : "Plan needed"}</dd></div><div><dt>Baseline</dt><dd>{baselineId ? baseline?.state === "completed" ? "Measured" : String(baseline?.state || "Recorded") : "Not run"}</dd></div><div><dt>Failures</dt><dd>{trials.length ? `${failures} of ${trials.length} validation trials` : "Not checked"}</dd></div></dl><p className="evaluation-next"><strong>Next:</strong> {next}</p></div><span className="evaluation-card-open">Review evaluation →</span></Link>;
  })}</div> : <div className="evaluation-empty"><div><h3>No evaluations yet</h3><p>Start with one behavior that must remain correct. You can add cases, scoring, and guardrails before accepting the plan.</p></div><Button onClick={onCreate}>Create evaluation</Button></div>}<div className="goal-context"><div><strong>Goals organize broader improvement outcomes</strong><p>Evaluations provide the checks and baselines used to measure them.</p></div><Button tone="quiet" onClick={onAddGoal}>New goal</Button></div></section>;
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
  const resultTasks = useTasks(projectId, { agent_id: agentId, goal_id: goalId });
  const resultTaskSummary = resultTasks.data?.tasks.find((task) => ["fix", "optimize"].includes(task.workflow));
  const resultTask = useTask(projectId, resultTaskSummary?.id);
  const [launch, setLaunch] = useState<WorkflowDefinition>();
  const queryClient = useQueryClient();
  const accept = useMutation({ mutationFn: () => post(projectPath(projectId, `/agents/${agentId}/goals/${goalId}/design/accept`), { expected_revision: goal.data?.measurement_plan?.revision }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId] }) });
  if (!goal.data) return <div className="page-loading">Loading goal…</div>;
  const workflowKey = currentStage === "define" ? "design" : currentStage === "measure" ? goal.data.measurement?.evaluation_id ? "baseline" : "eval" : "optimize";
  const workflow = workflows.data?.workflows.find((item) => item.workflow === workflowKey);
  const plan = goal.data.measurement_plan;
  const resultRunId = typeof resultTask.data?.result?.run_id === "string" ? resultTask.data.result.run_id : undefined;
  const stageState = { define: plan?.state === "accepted" ? "complete" : plan ? "ready" : "current", measure: goal.data.measurement?.baseline_id ? "complete" : goal.data.measurement?.evaluation_id ? "ready" : "locked", improve: goal.data.measurement?.baseline_id ? "ready" : "locked", review: resultTaskSummary ? resultRunId ? "ready" : resultTaskSummary.state : "locked" } as Record<string, string>;
  return <>
    <PageHeader eyebrow="Goal" title={goal.data.name}><p>{goal.data.objective}</p></PageHeader>
    <nav className="stage-rail" aria-label="Goal stages">{stages.map((stage, index) => <button key={stage.id} className={currentStage === stage.id ? "is-active" : ""} onClick={() => setSearch({ stage: stage.id })}><span className={`stage-index stage-${stageState[stage.id]}`}>{stageState[stage.id] === "complete" ? "✓" : index + 1}</span><span><strong>{stage.name}</strong><small>{stageState[stage.id]}</small></span></button>)}</nav>
    <section className="goal-stage"><div className="stage-heading"><p className="eyebrow">{currentStage}</p><h2>{currentStage === "define" ? "Define what good looks like" : currentStage === "measure" ? "Establish the current result" : currentStage === "improve" ? "Run a bounded improvement" : "Choose what to deliver"}</h2></div>
      {currentStage === "define" && <div className="stage-content"><dl className="definition-list"><div><dt>Objective</dt><dd>{goal.data.objective}</dd></div>{goal.data.ideal_behavior && <div><dt>Ideal behavior</dt><dd>{goal.data.ideal_behavior}</dd></div>}</dl>{plan ? <><MeasurementPlanView plan={plan} />{plan.state !== "accepted" && <div className="proposal-accept"><div><strong>Accept this plan to prepare its evaluator.</strong><p>Acceptance freezes these behaviors, measures, decision rules, evidence, and limitations for comparison.</p></div><Button onClick={() => accept.mutate()} disabled={accept.isPending}>{accept.isPending ? "Accepting…" : "Accept this measurement plan"}</Button></div>}{accept.error && <p className="error-banner">{accept.error.message}</p>}</> : <div className="stage-empty"><div><h3>No measurement plan</h3><p className="quiet-copy">Define the evidence and decision rule before measuring changes.</p></div><Button onClick={() => workflow && setLaunch(workflow)}>Design measurements</Button></div>}</div>}
      {currentStage === "measure" && <div className="stage-content"><dl className="definition-list"><div><dt>Evaluator</dt><dd>{goal.data.measurement?.evaluation_id || "Not prepared"}</dd></div><div><dt>Baseline</dt><dd>{goal.data.measurement?.baseline_id || "Not run"}</dd></div></dl><Button onClick={() => workflow && setLaunch(workflow)} disabled={!workflow}>{goal.data.measurement?.evaluation_id ? "Run baseline" : "Prepare evaluation"}</Button></div>}
      {currentStage === "improve" && <div className="stage-content">{goal.data.measurement?.baseline_id ? <><p className="stage-summary">The frozen baseline will remain the comparison point.</p><Button onClick={() => workflow && setLaunch(workflow)}>Improve agent</Button></> : <div className="blocked-state"><h3>Baseline required</h3><Button tone="secondary" onClick={() => setSearch({ stage: "measure" })}>Go to Measure</Button></div>}</div>}
      {currentStage === "review" && <div className="stage-content">{resultRunId && resultTask.data && ["fix", "optimize"].includes(resultTask.data.workflow) ? <><div className="stage-result-action"><Link className="button button-secondary" to={`/projects/${projectId}/results/${resultTask.data.workflow}/${encodeURIComponent(resultRunId)}`}>Open full result</Link></div><RunResultView projectId={projectId} workflow={resultTask.data.workflow as "fix" | "optimize"} runId={resultRunId} taskState={resultTask.data.state} taskResult={resultTask.data.result} /></> : resultTaskSummary ? <div className="blocked-state"><div><h3>{["running", "queued", "needs_input"].includes(resultTaskSummary.state) ? "Improvement task is still in progress" : "No measured comparison is available"}</h3><p className="quiet-copy">Open the task to review its progress, failure, or recorded limits.</p></div><Link className="button button-secondary" to={`/projects/${projectId}/tasks/${resultTaskSummary.id}`}>Open task</Link></div> : <div className="blocked-state"><div><h3>No improvement result yet</h3><p className="quiet-copy">Run a bounded improvement against the frozen baseline, then return here to compare and choose.</p></div><Button tone="secondary" onClick={() => setSearch({ stage: "improve" })}>Go to Improve</Button></div>}</div>}
    </section>
    {launch && <LaunchWorkflowModal projectId={projectId} workflow={launch} defaultAgentId={agentId} defaultGoalId={goalId} onClose={() => setLaunch(undefined)} />}
  </>;
}

export function TasksPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const [search, setSearch] = useSearchParams();
  const searchKey = search.toString();
  const filters = useMemo<Record<string, string>>(() => Object.fromEntries(["agent_id", "goal_id", "workflow", "status"].flatMap((key) => search.get(key) ? [[key, search.get(key)!]] : [])), [searchKey]);
  const segment = search.get("view") || "needs";
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
  return <><PageHeader eyebrow="Activity" title="Work and results"><p>Answer questions, follow running work, and inspect completed evidence.</p></PageHeader><nav className="activity-segments" aria-label="Activity status">{[{ id: "needs", label: "Needs you" }, { id: "running", label: "Running" }, { id: "finished", label: "Finished" }, { id: "all", label: "All" }].map(item => <button type="button" key={item.id} aria-pressed={segment === item.id} onClick={() => updateSearch((next) => next.set("view", item.id))}>{item.label}</button>)}</nav><details className="filter-disclosure"><summary>Filter activity{Object.keys(filters).length ? ` · ${Object.keys(filters).length} active` : ''}</summary><div className="filters"><select aria-label="Filter by agent" value={filters.agent_id || ""} onChange={(event) => selectAgent(event.target.value)}><option value="">All agents</option>{agents.data?.confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select>{filters.agent_id && <select aria-label="Filter by goal" value={filters.goal_id || ""} onChange={(event) => selectFilter("goal_id", event.target.value)}><option value="">All goals</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select>}<select aria-label="Filter by workflow" value={filters.workflow || ""} onChange={(event) => selectFilter("workflow", event.target.value)}><option value="">All workflows</option><option value="design">Design measurements</option><option value="eval">Prepare evaluation</option><option value="baseline">Run baseline</option><option value="optimize">Improve agent</option><option value="audit">Audit agent</option><option value="assess">Assess project</option><option value="observe">Observe production</option><option value="discover">Discover issues</option><option value="fix">Fix</option></select><select aria-label="Filter by status" value={filters.status || ""} onChange={(event) => selectFilter("status", event.target.value)}><option value="">All statuses</option><option value="needs_input">Needs input</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="interrupted">Interrupted</option></select></div></details><TaskList projectId={projectId} tasks={visibleTasks} search={searchKey} /></>;
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
  return <div className="settings-stack"><section className="settings-section"><div className="section-heading"><div><h2>Available coding backends</h2><p className="quiet-copy">The selected backend diagnoses evidence and authors changes inside Agentagon-managed tasks.</p></div></div><div className="assistant-grid">{assistants.data?.assistants.map((assistant) => <article className={`assistant-card ${selected === assistant.id ? "is-selected" : ""}`} key={assistant.id}><div><h3>{assistant.name}</h3><Status value={assistant.available ? assistant.authenticated === false ? "sign in needed" : assistant.authenticated === true ? "ready" : "authentication unknown" : "not installed"} /></div>{assistant.version && <p>Version {assistant.version}</p>}{assistant.message && <small>{assistant.message}</small>}</article>)}</div></section><form className="settings-card form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><div><h2>Default for new tasks</h2><p className="quiet-copy">Workflow preparation shows this backend and model before any task starts.</p></div><label>Coding backend<select value={selected} onChange={(event) => selectAssistant(event.target.value)}><option value="codex">Codex</option><option value="claude">Claude</option></select></label><label>Model<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Use the backend default" /></label><label>Maximum concurrent tasks<input type="number" min={1} max={8} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label>{selected === "claude" && <label>Claude API key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /><small>Stored as a credential reference; the saved value is never shown here.</small></label>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save coding backend"}</Button></footer></form></div>;
}

function ProjectSettings({ projectId, mode }: { projectId: string; mode: string }) {
  const overview = useOverview(projectId);
  const workflows = useWorkflows();
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
  const profiles = Object.entries(overview.data?.settings.profiles || {});
  if (mode === "execution") return <div className="settings-stack"><section className="settings-card"><div className="section-heading"><div><h2>Execution profiles</h2><p className="quiet-copy">A workflow’s prepared action shows its effective profile, limits, and source requirements before it starts.</p></div></div>{profiles.length ? <div className="list-surface">{profiles.map(([name, profile]) => <div className="list-row" key={name}><div><strong>{name}</strong><span>{String((profile as { runner?: { kind?: string } }).runner?.kind || "Runner not specified")}</span></div></div>)}</div> : <div className="quiet-surface"><strong>No execution profile configured</strong><p>Command-running workflows will identify this prerequisite during preparation. Agentagon will not assume a runner.</p></div>}</section><details className="settings-card advanced-actions"><summary>Advanced actions · built-in workflows</summary><p className="quiet-copy">These are the shared operations used by the dashboard and MCP. Start them from an agent, issue, trace, evaluation, or monitor so the action has explicit scope.</p><div className="list-surface">{workflows.data?.workflows.map((workflow) => <div className="list-row" key={workflow.id}><div><strong>{workflow.name}</strong><span>{workflow.purpose}</span><small>Inputs: {workflow.inputs.join(" · ")} · Outputs: {workflow.outputs.join(" · ")}</small></div></div>)}</div></details></div>;
  if (mode === "project") { const project = overview.data?.project; return <div className="project-settings-stack"><section className="settings-card"><h2>Project source</h2><p className="quiet-copy">Agentagon reads the registered local folder and keeps its private task evidence separate from application source.</p>{project && <dl className="compact-definition"><div><dt>Name</dt><dd>{project.name}</dd></div><div><dt>Folder</dt><dd><code>{project.path}</code></dd></div><div><dt>Availability</dt><dd>{project.available === false ? "Folder unavailable" : "Available"}</dd></div><div><dt>Source</dt><dd>{project.source?.kind === "git" ? [project.branch, project.source.revision?.slice(0, 10)].filter(Boolean).join(" · ") || "Git repository" : "Local folder"}</dd></div></dl>}<div className="form-actions"><Link className="button button-secondary" to={`/projects/${projectId}/onboarding`}>Analyze project</Link></div><details className="service-diagnostics"><summary>Service diagnostics</summary>{health.isLoading && <p>Reading service build…</p>}{health.error && <p className="error-banner">{health.error.message}</p>}{health.data && <dl className="compact-definition"><div><dt>Package</dt><dd>{health.data.package_version}</dd></div><div><dt>Build</dt><dd><code>{health.data.build_id}</code></dd></div><div><dt>Frontend assets</dt><dd><code>{health.data.frontend_asset_version}</code></dd></div><div><dt>Service started</dt><dd>{new Date(health.data.service_started_at).toLocaleString()}</dd></div><div><dt>Python</dt><dd>{health.data.python_version}</dd></div><div><dt>State contracts</dt><dd><code>{JSON.stringify(health.data.state_contracts)}</code></dd></div></dl>}</details></section><section className="settings-card danger-card"><h2>Remove project registration</h2><p>Removes this project from the dashboard. Project files and saved evidence remain on disk.</p><Button tone="danger" disabled={removeProject.isPending} onClick={() => removeProject.mutate()}>{removeProject.isPending ? "Removing…" : "Remove registration"}</Button></section></div>; }
  return <form className="settings-card form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>{mode === "defaults" ? <><div><h2>Trace evidence preference</h2><p className="quiet-copy">Choose whether workflows may propose available runtime traces. Provider collection still requires an explicit connection and scope.</p></div><label>Use runtime traces<select value={traces} onChange={(event) => setTraces(event.target.value)}><option value="unset">Ask when relevant</option><option value="enabled">Allow when explicitly selected</option><option value="disabled">Do not use runtime traces</option></select></label></> : <><div><h2>Optional Intelligence service</h2><p className="quiet-copy">Controls prepared requests to a configured external Intelligence endpoint. Raw project data is not sent by opening this page.</p></div><label>Request approval<select value={intelligenceMode} onChange={(event) => setIntelligenceMode(event.target.value)}><option value="ask">Ask before each request</option><option value="full_access">Allow reviewed prepared requests</option></select></label><label>Service URL <span className="optional">Optional</span><input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>API key <span className="optional">Optional</span><input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /><small>The saved credential is never shown here.</small></label></>}{save.error && <p className="error-banner">{save.error.message}</p>}<footer className="form-actions"><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : mode === "defaults" ? "Save trace preference" : "Save Intelligence policy"}</Button></footer></form>;
}

export function AgentInventoryPage({ projectId }: { projectId: string }) {
  const agents = useAgents(projectId);
  const navigate = useNavigate();
  const [review, setReview] = useState<Agent>();
  const [add, setAdd] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const discover = useMutation({ mutationFn: () => post<{ id?: string; task_id?: string }>(projectPath(projectId, "/agents/discover"), { operation_id: operationId() }), onSuccess: (task) => navigate(`/projects/${projectId}/tasks/${task.id || task.task_id}`) });
  const inventory = useMemo(() => {
    const items = [...(agents.data?.confirmed || []), ...(agents.data?.suggestions || []), ...(agents.data?.excluded || [])];
    const needle = query.trim().toLocaleLowerCase();
    return items.filter((agent) => {
      const visibleStatus = agent.status === "archived" ? "excluded" : agent.status;
      if (status !== "all" && status !== visibleStatus) return false;
      if (!needle) return true;
      return [agent.name, agent.responsibility || "", ...agent.code_scopes, ...agent.shared_dependencies].some((value) => value.toLocaleLowerCase().includes(needle));
    });
  }, [agents.data, query, status]);
  return <><PageHeader eyebrow="Application identities" title="Agents" actions={<><Button tone="secondary" onClick={() => discover.mutate()} disabled={discover.isPending}>{discover.isPending ? "Starting assessment…" : "Analyze project"}</Button><Button onClick={() => setAdd(true)}>Add manually</Button></>}><p>Confirm only the identities Agentagon should evaluate, improve, and monitor.</p></PageHeader>
    <div className="inventory-toolbar"><label><span>Search</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, responsibility, or code path" /></label><label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All identities</option><option value="suggested">Suggested</option><option value="confirmed">Confirmed</option><option value="excluded">Excluded</option></select></label><div className="inventory-counts" aria-label="Identity counts"><span><strong>{agents.data?.suggestions.length || 0}</strong> to review</span><span><strong>{agents.data?.confirmed.length || 0}</strong> confirmed</span><span><strong>{agents.data?.excluded?.length || 0}</strong> excluded</span></div></div>
    {agents.isLoading ? <div className="page-loading">Loading identities…</div> : inventory.length ? <div className="identity-inventory"><div className="identity-inventory-head" aria-hidden="true"><span>Identity</span><span>Responsibility</span><span>Scope</span><span>Status</span><span /></div>{inventory.map((agent) => { const visibleStatus = agent.status === "archived" ? "excluded" : agent.status; const source = agent.code_scopes.length ? `${agent.code_scopes.length} code path${agent.code_scopes.length === 1 ? "" : "s"}` : "Trace-backed"; return <article className="identity-inventory-row" key={agent.id}><div><strong>{agent.name}</strong><small>{agent.evidence?.some((item) => item.kind === "code") ? "Detected in code" : "Detected from traces"}</small></div><p className={!agent.responsibility ? "is-unresolved" : ""}>{agent.responsibility || agent.responsibility_inference?.reason || "Responsibility unresolved"}</p><div><code>{agent.code_scopes[0] || String(agent.trace_selector?.name || "Trace selector")}</code><small>{source}</small></div><Status value={visibleStatus} />{agent.status === "confirmed" ? <Link to={`/projects/${projectId}/agents/${agent.id}/overview`}>Open</Link> : <Button tone="quiet" onClick={() => setReview(agent)}>{agent.status === "archived" ? "Inspect" : "Review"}</Button>}</article>; })}</div> : <Empty title={query || status !== "all" ? "No identities match these filters" : "No agent identities yet"}>{query || status !== "all" ? "Clear the search or choose another status." : "Analyze the project to find application agents, or add one manually."}</Empty>}
    {agents.error && <p className="error-banner">{agents.error.message}</p>}{discover.error && <p className="error-banner">{discover.error.message}</p>}{add && <AddAgentModal projectId={projectId} onClose={() => setAdd(false)} />}{review && <IdentityReviewModal projectId={projectId} identity={review} onClose={() => setReview(undefined)} />}</>;
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
  const importer = useMutation({ mutationFn: (workflow: string) => post<{id: string}>(projectPath(projectId, "/imports"), { preview_id: activePreview?.preview_id, operation_id: importOperation.operation }), onSuccess: (result, workflow) => { importOperation.clear(); cache.invalidateQueries({queryKey:["projects",projectId,"traces"]}); navigate(`/projects/${projectId}/traces/${encodeURIComponent(result.id)}?intent=${workflow}`); } });
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
      {!issue.agent_id && <form className="settings-card form" onSubmit={event => { event.preventDefault(); update({ action: "assign", agent_id: owner }); }}><div><h3>Assign an owner</h3><p className="quiet-copy">Only confirmed agents can own repair work.</p></div><label>Confirmed agent<select value={owner} onChange={event => setOwner(event.target.value)} required><option value="">Choose an agent</option>{agents.data?.confirmed.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><Button type="submit" disabled={pending || !owner}>{mutation.variables?.action === "assign" && pending ? "Assigning…" : "Assign agent"}</Button></form>}
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
        ? <Link className="button button-primary" to={`/projects/${projectId}/agents/${data.agent_id}/improvements`}>Record deployment</Link>
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


function IssueDiagnosis({projectId, issueId, agentId, onFix}: {projectId: string; issueId: string; agentId?: string; onFix: () => void}) {
  const [show, setShow] = useState(false);
  type Facet = { state: string; active_task_id?: string | null; attempts?: Array<{ id: string; workflow: string; state: string; run_id?: string | null }>; tested_revision?: string | null; changes?: Array<{ run_id?: string; task_id?: string; tested_revision: string }>; deployments?: unknown[] };
  type Detail = { diagnoses: Array<{reference:string; summary:string; expected_behavior?:string; evidence:string[]}>; facets: { triage: Facet; work: Facet; test_verification: Facet; delivery: Facet; production: Facet }; next_actions: string[] };
  const result = useQuery({queryKey:["projects",projectId,"issues",issueId], enabled:show, queryFn:() => api<Detail>(projectPath(projectId,`/issues/${issueId}`))});
  const latestChange = result.data?.facets.test_verification.changes?.[0];
  const action = (name: string) => {
    if (["start_fix", "investigate_recurrence"].includes(name)) return <Button tone="secondary" key={name} onClick={onFix}>{name === "start_fix" ? "Start Fix" : "Investigate recurrence"}</Button>;
    if (name === "inspect_task") { const taskId = result.data?.facets.work.active_task_id || result.data?.facets.work.attempts?.[0]?.id; return taskId ? <Link className="button button-secondary" key={name} to={`/projects/${projectId}/tasks/${taskId}`}>Inspect task</Link> : null; }
    if (["review_verified_change", "prepare_local_delivery"].includes(name) && latestChange?.run_id) return <Link className="button button-secondary" key={name} to={`/projects/${projectId}/results/fix/${encodeURIComponent(latestChange.run_id)}`}>{name === "review_verified_change" ? "Review verified change" : "Prepare delivery"}</Link>;
    if (name === "record_deployment" && agentId) return <Link className="button button-secondary" key={name} to={`/projects/${projectId}/agents/${agentId}/improvements`}>Record deployment</Link>;
    if (name === "observe_production" && agentId) return <Link className="button button-secondary" key={name} to={`/projects/${projectId}/agents/${agentId}/production`}>Observe production</Link>;
    if (name === "assign_agent") return <Link className="button button-secondary" key={name} to={`/projects/${projectId}/agents`}>Assign an agent</Link>;
    return <span className="issue-next-label" key={name}>{name.replaceAll("_", " ")}</span>;
  };
  return <><Button tone="quiet" onClick={() => show ? result.refetch() : setShow(true)} disabled={result.isLoading}>{result.isLoading ? 'Loading lifecycle…' : show ? 'Refresh lifecycle' : 'Read lifecycle'}</Button>{result.error && <p role="alert">{result.error.message}</p>}{result.data && <><div className="issue-facets">{Object.entries(result.data.facets).map(([name, facet]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><Status value={facet.state} /></div>)}</div>{result.data.facets.test_verification.state === 'verified' && <p className="issue-verification-copy">Verified on revision <code>{result.data.facets.test_verification.tested_revision?.slice(0, 12)}</code>; production is {result.data.facets.production.state === 'not_observed' ? 'not observed yet' : result.data.facets.production.state.replaceAll('_', ' ')}.</p>} {!!result.data.next_actions.length && <div className="issue-next-actions"><strong>Next</strong>{result.data.next_actions.map(action)}</div>}{result.data.diagnoses.map(item => <div key={item.reference}><p>{item.summary}</p>{item.expected_behavior && <p>Expected: {item.expected_behavior}</p>}<ul>{item.evidence.map((text,index) => <li key={index}>{text}</li>)}</ul></div>)}</>}</>;
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
