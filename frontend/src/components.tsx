import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { api, operationId, post, projectPath } from "./api";
import { useAgents, useGoals, useTask } from "./hooks";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Agent, Goal, PreparedWorkflowStart, Project, ResultKind, TaskDetail, WorkflowDefinition } from "./types";

type IconName = "home" | "tasks" | "agent" | "workflow" | "connector" | "settings" | "plus" | "close" | "arrow" | "moon" | "sun" | "menu";

const iconPaths: Record<IconName, ReactNode> = {
  home: <><path d="M3 10.8 12 3l9 7.8" /><path d="M5.5 9.8V21h13V9.8M9 21v-7h6v7" /></>,
  tasks: <><path d="M7 4h10M7 12h10M7 20h10" /><path d="m3 4 .7.7L5 3.3M3 12l.7.7L5 11.3M3 20l.7.7L5 19.3" /></>,
  agent: <><path d="M12 3 4.5 7.2v9.6L12 21l7.5-4.2V7.2L12 3Z" /><circle cx="12" cy="12" r="3" /></>,
  workflow: <><path d="M8 3h8l1 5 4 3-4 3-1 7H8l-1-7-4-3 4-3 1-5Z" /><path d="m9.5 12 1.7 1.7 3.6-4" /></>,
  connector: <><path d="M8 12h8M5 8v8M19 8v8" /><rect x="2" y="6" width="5" height="12" rx="2" /><rect x="17" y="6" width="5" height="12" rx="2" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19 13.5v-3l-2-.7-.7-1.7.9-1.9-2.1-2.1-1.9.9-1.7-.7-.7-2h-3l-.7 2-1.7.7-1.9-.9-2.1 2.1.9 1.9-.7 1.7-2 .7v3l2 .7.7 1.7-.9 1.9 2.1 2.1 1.9-.9 1.7.7.7 2h3l.7-2 1.7-.7 1.9.9 2.1-2.1-.9-1.9.7-1.7 2-.7Z" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  arrow: <path d="m9 18 6-6-6-6" />,
  moon: <path d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{iconPaths[name]}</svg>;
}

export function Button({ children, tone = "primary", className = "", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "secondary" | "quiet" | "danger" }) {
  return <button className={`button button-${tone} ${className}`} {...props}>{children}</button>;
}

export function Status({ value }: { value: string }) {
  const tone = ["completed", "connected", "accepted", "ready", "pass", "monitoring", "selected by you"].includes(value) ? "good" : ["failed", "cancelled", "blocked", "regressed"].includes(value) ? "bad" : ["running", "queued", "analyzing"].includes(value) ? "active" : "neutral";
  return <span className={`status status-${tone}`}><span aria-hidden="true" />{value.replaceAll("_", " ")}</span>;
}

export function Modal({ title, eyebrow, children, onClose, wide = false }: { title: string; eyebrow?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  const trigger = useRef<HTMLElement | null>(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const focusable = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []).filter((element) => !element.hasAttribute("hidden"));
    const initial = panel.current?.querySelector<HTMLElement>("[autofocus]") || focusable()[0];
    const frame = window.requestAnimationFrame(() => initial?.focus());
    const contain = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      if (!elements.length) {
        event.preventDefault();
        panel.current?.focus();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", contain);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", contain);
      trigger.current?.focus();
    };
  }, []);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section ref={panel} className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1}>
      <header className="modal-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2 id="modal-title">{title}</h2></div><Button tone="quiet" aria-label="Close" onClick={onClose}><Icon name="close" /></Button></header>
      {children}
    </section>
  </div>;
}

export function Empty({ title, action, children }: { title: string; action?: ReactNode; children?: ReactNode }) {
  return <div className="empty"><div className="empty-mark" aria-hidden="true">✦</div><h2>{title}</h2>{children && <div className="empty-copy">{children}</div>}{action && <div className="empty-action">{action}</div>}</div>;
}

export function PageHeader({ eyebrow, title, children, actions }: { eyebrow?: string; title: string; children?: ReactNode; actions?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{children && <div className="page-intro">{children}</div>}</div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

export function Sidebar({ projects, project, agents, suggestionCount, open, onClose, onAddProject, onAddAgent }: { projects: Project[]; project: Project; agents: Agent[]; suggestionCount: number; open: boolean; onClose: () => void; onAddProject: () => void; onAddAgent: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const projectBase = `/projects/${project.id}`;
  const switchProject = (id: string) => navigate(`/projects/${id}/home`);
  const linkClass = ({ isActive }: { isActive: boolean }) => `nav-link ${isActive ? "is-active" : ""}`;
  return <aside className={`sidebar ${open ? "is-open" : ""}`} aria-label="Workspace navigation">
    <div className="sidebar-top">
      <Link className="brand" to={`${projectBase}/home`} onClick={onClose}><img src="/logo-split-crown-96.png" alt="" /><span>agentagon</span></Link>
      <Button tone="quiet" className="mobile-only" aria-label="Close navigation" onClick={onClose}><Icon name="close" /></Button>
    </div>
    <div className="project-control"><label htmlFor="project-picker">Project</label><select id="project-picker" value={project.id} onChange={(event) => switchProject(event.target.value)}>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><Button tone="quiet" onClick={onAddProject}><Icon name="plus" size={15} /> Add project</Button></div>
    <nav className="primary-nav" onClick={onClose}>
      <NavLink to={`${projectBase}/home`} className={linkClass}><Icon name="home" />Overview</NavLink>
      <NavLink to={`${projectBase}/agents`} className={linkClass}><Icon name="agent" />Agents{suggestionCount > 0 && <span className="nav-link-count" aria-label={`${suggestionCount} suggestions`}>{suggestionCount}</span>}</NavLink>
      <NavLink to={`${projectBase}/production`} className={linkClass}><Icon name="connector" />Production</NavLink>
      <NavLink to={`${projectBase}/tasks`} className={location.pathname.startsWith(`${projectBase}/results/`) ? "nav-link is-active" : linkClass}><Icon name="tasks" />Activity</NavLink>
      <NavLink to={`${projectBase}/settings/project`} className={linkClass}><Icon name="settings" />Settings</NavLink>
    </nav>
    <div className="nav-section"><div className="nav-heading"><span>Agent workspaces</span><Button tone="quiet" aria-label="Add agent" onClick={onAddAgent}><Icon name="plus" size={15} /></Button></div>
      <nav className="agent-links" onClick={onClose}>{agents.map((agent) => <NavLink key={agent.id} to={`${projectBase}/agents/${agent.id}/overview`} className={linkClass}><span className="agent-glyph" aria-hidden="true">A</span><span>{agent.name}</span></NavLink>)}{!agents.length && (suggestionCount > 0 ? <Link className="nav-empty" to={`${projectBase}/agents`}>Review {suggestionCount} suggestion{suggestionCount === 1 ? "" : "s"}</Link> : <button className="nav-empty" onClick={onAddAgent}>Add your first agent</button>)}</nav>
    </div>
    <div className="sidebar-bottom"><p className="local"><span />Local workspace</p></div>
  </aside>;
}

export function Topbar({ project, taskCount, onMenu }: { project: Project; taskCount: number; onMenu: () => void }) {
  const [dark, setDark] = useState(document.documentElement.dataset.theme === "dark");
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    localStorage.setItem("agentagon-theme", next ? "dark" : "light");
  };
  return <header className="topbar"><div className="topbar-project"><Button tone="quiet" className="mobile-only" aria-label="Open navigation" onClick={onMenu}><Icon name="menu" /></Button><span className="scope-dot" /><span>{project.name}</span>{project.branch && <span className="branch">{project.branch}</span>}</div><div className="topbar-actions">{taskCount > 0 && <Link className="activity-link" to={`/projects/${project.id}/tasks`}>Needs you <strong>{taskCount}</strong></Link>}<Button tone="quiet" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggle}><Icon name={dark ? "sun" : "moon"} /></Button></div></header>;
}

function AssessmentOutcome({ projectId, state, result }: { projectId: string; state: string; result: Record<string, unknown> }) {
  const candidates = Array.isArray(result.candidates) ? result.candidates.filter((candidate) => typeof candidate !== "object" || candidate === null || (candidate as Record<string, unknown>).keep !== false) : [];
  const issues = Array.isArray(result.issues) ? result.issues : [];
  const recommendations = Array.isArray(result.recommendations) ? result.recommendations : [];
  const limitations = Array.isArray(result.limitations) ? result.limitations.filter((item): item is string => typeof item === "string") : [];
  return <section className="assessment-outcome"><header><div><p className="eyebrow">Assessment outcome</p><h3>{state === "completed_with_limits" ? "Project assessed with limits" : "Project assessment complete"}</h3><p>{typeof result.summary === "string" ? result.summary : "Agent identities and available evidence were reviewed."}</p></div><Status value={state} /></header><div className="assessment-counts"><div><strong>{candidates.length}</strong><span>agents to review</span></div><div><strong>{issues.length}</strong><span>issues found</span></div><div><strong>{recommendations.length}</strong><span>next actions</span></div></div>{limitations.length > 0 && <div className="assessment-limits"><strong>What was limited</strong><ul className="plain-list">{limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>}<div className="assessment-actions">{candidates.length > 0 && <Link className="button button-primary" to={`/projects/${projectId}/agents`}>Review discovered agents</Link>}{issues.length > 0 && <Link className="button button-secondary" to={`/projects/${projectId}/issues`}>Review issues</Link>}<Link className="button button-secondary" to={`/projects/${projectId}/home`}>Return to overview</Link></div></section>;
}

function taskResultReference(task?: TaskDetail) {
  if (!task) return null;
  const fields: Partial<Record<ResultKind, "audit_id" | "evaluation_id" | "baseline_id" | "run_id" | "patch_id">> = {
    audit: "audit_id",
    eval: "evaluation_id",
    baseline: "baseline_id",
    fix: "run_id",
    optimize: "run_id",
    patch: "patch_id",
  };
  const kind = task.workflow as ResultKind;
  const field = fields[kind];
  if (!field) return null;
  const resultValue = task.result?.[field];
  const resultId = typeof resultValue === "string" ? resultValue : task.workflow_ids?.[field];
  return resultId ? { kind, id: resultId } : null;
}

export function TaskPanel({ projectId, taskId, onClose }: { projectId: string; taskId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const task = useTask(projectId, taskId);
  const [answer, setAnswer] = useState("");
  const mutate = useMutation({ mutationFn: ({ action, body }: { action: string; body: Record<string, unknown> }) => post(projectPath(projectId, `/tasks/${taskId}/${action}`), { operation_id: operationId(), ...body }), onSuccess: () => { setAnswer(""); queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] }); } });
  const assessmentCandidates = task.data?.result?.candidates;
  const assessmentCandidateCount = task.data?.workflow === "assess" && Array.isArray(assessmentCandidates)
    ? assessmentCandidates.filter((candidate) => typeof candidate !== "object" || candidate === null || (candidate as Record<string, unknown>).keep !== false).length
    : 0;
  const resultReference = taskResultReference(task.data);
  const hasTypedResult = Boolean(resultReference);
  const terminal = Boolean(task.data && ["completed", "completed_with_limits", "failed", "cancelled"].includes(task.data.state));
  const hasAssessmentOutcome = Boolean(terminal && task.data?.workflow === "assess" && task.data.result);
  const lessons = Array.isArray(task.data?.result?.lessons)
    ? task.data.result.lessons.filter((item): item is { id: string; version: number; decision: string; reason: string } => Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).id === "string"))
    : [];
  const activity = task.data ? <div className="conversation" aria-live="polite">
    {task.data.conversation.map((message, index) => <article className={`message message-${message.role || "assistant"}`} key={index}><span>{message.role === "user" ? "You" : "Coding assistant"}</span><p>{message.text || message.content}</p></article>)}
    {task.data.events.map((event, index) => <article className="task-event" key={index}><span>{event.type || "Progress"}</span><p>{event.text}</p></article>)}
    {!task.data.conversation.length && !task.data.events.length && <p className="quiet-copy">Waiting for the first update.</p>}
  </div> : null;
  return <aside className={`task-panel ${hasAssessmentOutcome ? "task-panel-result" : ""}`} aria-label="Task details">
    <header className="task-panel-header"><div><p className="eyebrow">Task</p><h2>{task.data?.title || "Loading task…"}</h2></div><Button tone="quiet" aria-label="Close task" onClick={onClose}><Icon name="close" /></Button></header>
    {task.isError && <p className="error-banner">{task.error.message}</p>}
    {task.data && <><div className="task-meta"><Status value={task.data.state} /><span>{task.data.agent_name || "Project"}</span>{task.data.goal_name && <span>{task.data.goal_name}</span>}</div>
      {task.data.question?.kind === "approval" && <div className="decision"><h3>Approval needed</h3><p>{task.data.question.text || task.data.question.prompt}</p><div className="task-controls"><Button disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { decision: "accept" } } })}>Approve</Button><Button tone="secondary" disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { decision: "decline" } } })}>Decline</Button></div></div>}
      {task.data.question && task.data.question.kind !== "approval" && <form className="decision" onSubmit={(event) => { event.preventDefault(); mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { text: answer } } }); }}><h3>Decision needed</h3><p>{task.data.question.prompt || task.data.question.text}</p><textarea aria-label="Response" value={answer} onChange={(event) => setAnswer(event.target.value)} required rows={3} /><Button type="submit" disabled={mutate.isPending}>Send response</Button></form>}
      {resultReference && <section className="task-result-summary task-result-peek"><p className="eyebrow">Result</p><h3>{task.data.state === "failed" ? "Task stopped with retained evidence" : task.data.state === "completed_with_limits" ? "Result ready with limits" : terminal ? "Result ready for review" : "Partial result available"}</h3><p>{typeof task.data.result?.summary === "string" ? task.data.result.summary : "Open the full result to inspect its evidence, supported actions, and limits."}</p>{Array.isArray(task.data.result?.limitations) && <ul className="plain-list">{task.data.result.limitations.filter((item): item is string => typeof item === "string").map((item) => <li key={item}>{item}</li>)}</ul>}<Link className="button button-primary" to={`/projects/${projectId}/results/${resultReference.kind}/${encodeURIComponent(resultReference.id)}`}>Open full result</Link></section>}
      {hasAssessmentOutcome && <AssessmentOutcome projectId={projectId} state={task.data.state} result={task.data.result!} />}
      {!hasTypedResult && !hasAssessmentOutcome && typeof task.data.result?.summary === "string" && <div className="task-result-summary"><p className="eyebrow">Result</p><h3>{task.data.state === "failed" ? "Task failed" : task.data.state === "completed_with_limits" ? "Completed with limits" : "Task completed"}</h3><p>{task.data.result.summary}</p>{Array.isArray(task.data.result.limitations) && <ul className="plain-list">{task.data.result.limitations.filter((item): item is string => typeof item === "string").map((item) => <li key={item}>{item}</li>)}</ul>}</div>}
      {assessmentCandidateCount > 0 && !hasAssessmentOutcome && <section className="task-handoff"><p className="eyebrow">Next step</p><h3>{assessmentCandidateCount} agent{assessmentCandidateCount === 1 ? "" : "s"} ready for review</h3><p>Confirm the application agents you want Agentagon to improve. Discovery does not start repairs.</p><Link className="button button-primary" to={`/projects/${projectId}/agents`}>Review suggested agents<Icon name="arrow" size={15} /></Link></section>}
      {task.data.next_action && !task.data.question && <div className={task.data.state === "failed" ? "error-banner" : "next-action"}><strong>{task.data.state === "failed" ? "Why it stopped" : "Next action"}</strong><p>{task.data.next_action}</p></div>}
      <div className="task-controls">{task.data.can_resume && <Button onClick={() => mutate.mutate({ action: "resume", body: {} })}>Resume</Button>}{task.data.can_cancel && <Button tone="secondary" onClick={() => mutate.mutate({ action: "cancel", body: {} })}>Cancel</Button>}</div>
      {!!lessons.length && <details className="task-lessons"><summary>Lessons considered ({lessons.length})</summary>{lessons.map((lesson) => <article key={`${lesson.id}-${lesson.version}`}><Status value={lesson.decision} /><div><strong>{lesson.id} · version {lesson.version}</strong><p>{lesson.reason}</p></div></article>)}</details>}
      {task.data.memory_recording && <div className="memory-recording-state"><div><strong>{task.data.memory_recording.automatic_retry ? "Outcome saved; lesson recording will retry" : "Outcome saved; lesson recording needs attention"}</strong><p>{task.data.memory_recording.message} The task result and verification evidence are already retained.</p></div>{!task.data.memory_recording.automatic_retry && <Button tone="secondary" disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "retry-memory", body: {} })}>Retry lesson recording</Button>}</div>}
      {mutate.error && <p className="error-banner" role="alert">{mutate.error.message}</p>}
      <details className="task-transcript"><summary>Activity and diagnostics ({task.data.conversation.length + task.data.events.length})</summary>{activity}{task.data.result && <details className="tool-detail"><summary>Raw result data</summary><pre>{JSON.stringify(task.data.result, null, 2)}</pre></details>}</details>
    </>}
  </aside>;
}

export function AddProjectModal({ onClose, mode = "local" }: { onClose: () => void; mode?: "local" | "clone" }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [repository, setRepository] = useState("");
  const mutation = useMutation({ mutationFn: () => post<Project>(mode === "clone" ? "/api/projects/clone" : "/api/projects", mode === "clone" ? { path, repository } : { path }), onSuccess: async (project) => { await queryClient.invalidateQueries({ queryKey: ["projects"] }); onClose(); navigate(`/projects/${project.id}/onboarding`); } });
  return <Modal title={mode === "clone" ? "Clone a repository" : "Open a local folder"} eyebrow={mode === "clone" ? "New local checkout" : "Existing project"} onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>{mode === "clone" ? <><p className="modal-purpose">Clone a Git repository into a new folder, then analyze it locally.</p><label>Repository URL<input type="url" value={repository} onChange={event => setRepository(event.target.value)} placeholder="https://example.com/team/agent.git" required autoFocus /></label><label>Clone into folder<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Users/you/code/my-agent" required /></label></> : <><p className="modal-purpose">Use an existing folder on this machine. It can be private, non-Git, and have no remote.</p><label>Local folder path<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Users/you/code/my-agent" required autoFocus /></label></>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mode === "clone" ? "Clone and open" : "Open folder"}</Button></footer></form></Modal>;
}

export function AddAgentModal({ projectId, suggestion, onClose }: { projectId: string; suggestion?: Agent; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(suggestion?.name || "");
  const [description, setDescription] = useState(suggestion?.responsibility || suggestion?.description || "");
  const [paths, setPaths] = useState(suggestion?.code_scopes.join(", ") || "");
  const inference = suggestion?.responsibility_inference;
  const mutation = useMutation({ mutationFn: () => post<Agent>(projectPath(projectId, suggestion ? `/agents/${suggestion.id}` : "/agents"), { name, description, code_scopes: paths.split(",").map((item) => item.trim()).filter(Boolean), shared_dependencies: suggestion?.shared_dependencies || [], trace_selector: suggestion?.trace_selector || {}, status: "confirmed", ...(suggestion?.revision ? { expected_revision: suggestion.revision } : {}) }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] }); onClose(); } });
  return <Modal title={suggestion ? "Confirm agent" : "Add agent"} eyebrow="Agent configuration" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Agent name<input value={name} onChange={(event) => setName(event.target.value)} required autoFocus /></label><label>Responsibility<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} placeholder="What this agent does for its users" /></label>{suggestion && !description && <div className={`inference-note inference-${inference?.state || "unavailable"}`}><strong>{inference?.state === "pending" ? "Responsibility is still being inferred" : "Responsibility needs your review"}</strong><p>{inference?.reason || (inference?.state === "pending" ? "Review can continue after the assessment records a responsibility." : "The assessment did not record a responsibility. Describe what this agent owns before confirming it.")}</p></div>}<label>Code paths<input value={paths} onChange={(event) => setPaths(event.target.value)} placeholder="src/agent.py, src/tools" required={!suggestion?.trace_selector || !Object.keys(suggestion.trace_selector).length} /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{suggestion ? "Confirm agent" : "Add agent"}</Button></footer></form></Modal>;
}

export function IdentityReviewModal({ projectId, identity, onClose }: { projectId: string; identity: Agent; onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [current, setCurrent] = useState(identity);
  const [responsibility, setResponsibility] = useState(identity.responsibility || identity.description || "");
  const [excluding, setExcluding] = useState(false);
  const [reason, setReason] = useState("");
  const [auditOperation] = useState(operationId);
  const [inferenceOperation] = useState(operationId);
  useEffect(() => {
    setCurrent(identity);
    setResponsibility(identity.responsibility || identity.description || "");
  }, [identity]);
  const revision = current.revision ?? 0;
  const inference = current.responsibility_inference;
  const sourceEvidence = (current.evidence || []).filter((item) => item.kind === "code");
  const traceEvidence = (current.evidence || []).filter((item) => item.kind === "traces" || item.kind === "trace_metadata");
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] });
    await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", current.id] });
  };
  const saveResponsibility = useMutation({
    mutationFn: () => post<Agent>(projectPath(projectId, `/agents/${current.id}`), { description: responsibility, expected_revision: revision }),
    onSuccess: async (saved) => { setCurrent(saved); setResponsibility(saved.responsibility || saved.description || ""); await refresh(); },
  });
  const confirm = useMutation({
    mutationFn: () => post<Agent>(projectPath(projectId, `/agents/${current.id}/confirm`), { expected_revision: revision }),
    onSuccess: async (saved) => { await refresh(); onClose(); navigate(`/projects/${projectId}/agents/${saved.id}/overview`); },
  });
  const exclude = useMutation({
    mutationFn: () => post<Agent>(projectPath(projectId, `/agents/${current.id}/exclude`), { reason, expected_revision: revision }),
    onSuccess: async () => { await refresh(); onClose(); },
  });
  const restore = useMutation({
    mutationFn: () => post<Agent>(projectPath(projectId, `/agents/${current.id}/restore`), { expected_revision: revision }),
    onSuccess: async () => { await refresh(); onClose(); },
  });
  const retry = useMutation({
    mutationFn: () => post<{ id?: string; task_id?: string }>(projectPath(projectId, `/agents/${current.id}/infer-responsibility`), { operation_id: inferenceOperation }),
    onSuccess: (task) => { onClose(); navigate(`/projects/${projectId}/tasks/${task.id || task.task_id}`); },
  });
  const audit = useMutation({
    mutationFn: async () => {
      const prepared = await post<PreparedWorkflowStart>(projectPath(projectId, "/workflows/prepare"), { operation_id: auditOperation, workflow: "audit", agent_id: current.id, input: { type: "agent", id: current.id }, options: {} });
      if (prepared.state !== "ready" && prepared.state !== "ready_with_limits") {
        const blockers = prepared.prerequisites.filter((item) => item.blocking).map((item) => item.code.replaceAll("_", " "));
        throw new Error(`Read-only audit needs ${blockers.join(", ") || "additional setup"}.`);
      }
      return post<{ id?: string; task_id?: string }>(projectPath(projectId, "/tasks"), { ...prepared.normalized_intent, operation_id: auditOperation });
    },
    onSuccess: (task) => { onClose(); navigate(`/projects/${projectId}/tasks/${task.id || task.task_id}`); },
  });
  const error = saveResponsibility.error || confirm.error || exclude.error || restore.error || retry.error || audit.error;
  const pending = saveResponsibility.isPending || confirm.isPending || exclude.isPending || restore.isPending || retry.isPending || audit.isPending;
  const excluded = current.status === "archived";
  const auditAvailable = current.code_scopes.length > 0;
  const savedResponsibility = current.responsibility || current.description || "";
  const responsibilityChanged = responsibility.trim() !== savedResponsibility.trim();
  return <Modal title={current.name} eyebrow={excluded ? "Excluded identity" : "Review suggested identity"} onClose={onClose} wide>
    <div className="identity-review-layout">
      <section className="identity-review-summary">
        <div className="identity-review-state"><Status value={excluded ? "excluded" : current.status} /><span>Revision {revision}</span></div>
        <div className="identity-review-responsibility"><p className="eyebrow">Responsibility</p>{excluded ? <h3>{savedResponsibility || "Responsibility unresolved"}</h3> : <><textarea aria-label="Agent responsibility" value={responsibility} onChange={(event) => setResponsibility(event.target.value)} rows={4} maxLength={1000} placeholder="What this agent does for its users" /><div className="identity-responsibility-actions"><small>{inference?.state === "edited" ? "Manually edited" : savedResponsibility ? "Inferred from retained source context" : "Required before confirmation"}</small><Button tone="secondary" type="button" disabled={pending || !responsibility.trim() || !responsibilityChanged} onClick={() => saveResponsibility.mutate()}>{saveResponsibility.isPending ? "Saving…" : "Save responsibility"}</Button></div></>}<p>{inference?.reason || (savedResponsibility ? "This text can be corrected before confirmation; manual edits keep their own provenance." : "Ask the coding backend to inspect only this retained definition, or enter the responsibility yourself.")}</p></div>
        <dl className="identity-review-meta"><div><dt>Inference state</dt><dd>{(inference?.state || "unavailable").replaceAll("_", " ")}</dd></div><div><dt>Recorded</dt><dd>{inference?.at ? new Date(inference.at).toLocaleString() : "Not recorded"}</dd></div><div><dt>Discovery identity</dt><dd><code>{inference?.source_discovery_key || current.identity_review?.discovery_key || "Unavailable"}</code></dd></div></dl>
        {excluded && <div className="identity-exclusion"><strong>Why this identity is excluded</strong><p>{current.identity_review?.reason || "No exclusion reason was retained."}</p>{current.identity_review?.at && <time>{new Date(current.identity_review.at).toLocaleString()}</time>}</div>}
      </section>
      <section className="identity-review-scope">
        <header><div><p className="eyebrow">Exact scope</p><h3>{excluded ? "What was excluded" : "What audit will inspect"}</h3></div><span>Read only</span></header>
        <div className="identity-scope-group"><strong>Agent code paths</strong>{current.code_scopes.length ? <ul>{current.code_scopes.map((path) => <li key={path}><code>{path}</code></li>)}</ul> : <p>None</p>}</div>
        <div className="identity-scope-group"><strong>Shared dependencies</strong>{current.shared_dependencies.length ? <ul>{current.shared_dependencies.map((path) => <li key={path}><code>{path}</code></li>)}</ul> : <p>None</p>}</div>
        <div className="identity-scope-group"><strong>Trace selector</strong>{current.trace_selector && Object.keys(current.trace_selector).length ? <pre>{JSON.stringify(current.trace_selector, null, 2)}</pre> : <p>None</p>}</div>
      </section>
    </div>
    <section className="identity-evidence">
      <header><div><p className="eyebrow">Retained evidence</p><h3>Why Agentagon suggested this identity</h3></div><span>{sourceEvidence.length + traceEvidence.length} source{sourceEvidence.length + traceEvidence.length === 1 ? "" : "s"}</span></header>
      {sourceEvidence.map((item, index) => <article className="source-receipt" key={`${item.path}-${item.symbol}-${index}`}><div className="source-receipt-heading"><div><strong>{item.symbol || current.name}</strong><code>{item.path}{item.line ? `:${item.line}` : ""}</code></div><span>{item.call || "Detected agent constructor"}</span></div>{item.context ? <ol start={item.context.start_line}>{item.context.source.split("\n").map((line, lineIndex) => <li key={item.context!.start_line + lineIndex}><code>{line || " "}</code></li>)}</ol> : <p>Source context was unavailable during discovery.</p>}</article>)}
      {traceEvidence.map((item, index) => <article className="trace-evidence-row" key={`${item.snapshot_id || item.trace_id}-${index}`}><strong>{item.provider || "Trace evidence"}</strong><code>{item.trace_id || item.snapshot_id}</code>{item.matched_on?.length ? <span>Matched {item.matched_on.join(", ")}</span> : null}</article>)}
      {!sourceEvidence.length && !traceEvidence.length && <div className="quiet-surface">No retained discovery evidence is available for this identity.</div>}
    </section>
    {!excluded && <section className="identity-audit-action"><div><p className="eyebrow">Safe before confirmation</p><strong>Audit this exact scope without changing code</strong><p>{auditAvailable ? "The coding backend receives a read-only sandbox. The task is rejected if this identity or its source changes after preparation." : "Retain a code path before auditing this identity. Trace-only review requires selecting the retained trace evidence."}</p></div><Button tone="secondary" onClick={() => audit.mutate()} disabled={pending || !auditAvailable}>{audit.isPending ? "Starting audit…" : "Run read-only audit"}</Button></section>}
    {!excluded && !savedResponsibility && <section className="identity-resolution"><div><strong>Infer this responsibility from its code</strong><p>The coding backend receives only this suggested identity and its retained source context. Other unresolved agents are not included. If the backend is unavailable, the task keeps this suggestion and reports that limitation.</p></div><Button tone="secondary" onClick={() => retry.mutate()} disabled={pending || sourceEvidence.length === 0}>{retry.isPending ? "Starting…" : "Infer from this code"}</Button></section>}
    {excluding && <form className="identity-exclude-form" onSubmit={(event) => { event.preventDefault(); exclude.mutate(); }}><label>Why should this identity stay out of the inventory?<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={500} required autoFocus /></label><div><Button tone="quiet" type="button" onClick={() => { setExcluding(false); setReason(""); }}>Keep suggestion</Button><Button tone="danger" type="submit" disabled={pending}>Exclude identity</Button></div></form>}
    {error && <p className="error-banner" role="alert">{error.message}</p>}
    <footer className="form-actions identity-review-actions"><Button tone="secondary" type="button" onClick={onClose}>Close</Button>{excluded ? <Button type="button" onClick={() => restore.mutate()} disabled={pending}>Restore suggestion</Button> : <>{!excluding && <Button tone="danger" type="button" onClick={() => setExcluding(true)} disabled={pending}>Exclude</Button>}<Button type="button" onClick={() => confirm.mutate()} disabled={pending || !savedResponsibility || responsibilityChanged}>Confirm this exact scope</Button></>}</footer>
  </Modal>;
}

export function AddGoalModal({ projectId, agentId, defaultCategory, defaultObjective, intent = "goal", onClose }: { projectId: string; agentId: string; defaultCategory?: string; defaultObjective?: string; intent?: "goal" | "evaluation" | "existing-evaluation"; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [category, setCategory] = useState(defaultCategory || "correctness");
  const [name, setName] = useState("");
  const [objective, setObjective] = useState(defaultObjective || "");
  const [ideal, setIdeal] = useState("");
  const evaluationIntent = intent !== "goal";
  const mutation = useMutation({ mutationFn: () => post<Goal>(projectPath(projectId, `/agents/${agentId}/goals`), { category, ...(category === "custom" ? { name } : {}), objective, ideal_behavior: ideal || null }), onSuccess: async (goal) => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals"] }); onClose(); navigate(`/projects/${projectId}/agents/${agentId}/goals/${goal.id}${intent === "existing-evaluation" ? "?stage=measure" : ""}`); } });
  const title = intent === "existing-evaluation" ? "Use an existing evaluation" : evaluationIntent ? "Create an evaluation" : "Create a goal";
  return <Modal title={title} eyebrow={evaluationIntent ? "Behavior to check" : "Outcome to improve"} onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>{evaluationIntent && <p className="quiet-copy">Describe the behavior this evaluation protects. Agentagon keeps its goal grouping visible so measured improvements use the same objective.</p>}<label>{evaluationIntent ? "Behavior area" : "Goal category"}<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="correctness">Task success and correctness</option><option value="reliability">Reliability and tool use</option><option value="grounding">Grounding and factuality</option><option value="safety">Safety and policy</option><option value="latency">Latency</option><option value="cost">Cost and efficiency</option><option value="custom">Custom objective</option></select></label>{category === "custom" && <label>{evaluationIntent ? "Evaluation name" : "Goal name"}<input value={name} onChange={(event) => setName(event.target.value)} required /></label>}<label>{evaluationIntent ? "Behavior to check" : "Objective"}<textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={4} required autoFocus /></label><label>{evaluationIntent ? "Expected behavior" : "Ideal behavior"} <span className="optional">Optional</span><textarea value={ideal} onChange={(event) => setIdeal(event.target.value)} rows={3} /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{intent === "existing-evaluation" ? "Choose evaluator" : evaluationIntent ? "Create evaluation draft" : "Create goal"}</Button></footer></form></Modal>;
}

type WorkflowDraft = {
  agentId?: string;
  goalId?: string;
  problem?: string;
  expected?: string;
  helpDefine?: boolean;
  traceId?: string;
};

function prerequisiteRoute(projectId: string, agentId: string, goalId: string, code: string) {
  const base = `/projects/${projectId}`;
  if (code === "coding_backend") return `${base}/settings/assistants`;
  if (code === "execution_profile") return `${base}/settings/execution`;
  if (code === "clean_source") return `${base}/settings/project`;
  if (["agent_selection", "agent_confirmation", "code_binding", "code_scope", "issue_ownership"].includes(code)) return `${base}/agents`;
  if (["measurement_plan", "evaluation", "baseline", "regression_baselines"].includes(code) && agentId) return goalId ? `${base}/agents/${agentId}/goals/${goalId}` : `${base}/agents/${agentId}/evaluations`;
  if (["trace_selection", "input_required"].includes(code)) return `${base}/issues`;
  if (code === "pending_observation" && agentId) return `${base}/agents/${agentId}/production`;
  return undefined;
}

export function LaunchWorkflowModal({ projectId, workflow, defaultAgentId, defaultGoalId, defaultInput, onClose }: { projectId: string; workflow: WorkflowDefinition; defaultAgentId?: string; defaultGoalId?: string; defaultInput?: {type: string; id?: string; text?: string}; onClose: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const agents = useAgents(projectId);
  const confirmed = agents.data?.confirmed || [];
  const draftKey = `agentagon.workflow-draft:${projectId}:${workflow.workflow}:${defaultAgentId || "any"}:${defaultGoalId || "any"}:${defaultInput?.type || "new"}:${defaultInput?.id || "none"}`;
  const initialDraft = useRef(loadOperationDraft<WorkflowDraft>(draftKey));
  const [agentId, setAgentId] = useState(defaultAgentId || initialDraft.current.agentId || "");
  const goals = useGoals(projectId, agentId);
  const [goalId, setGoalId] = useState(defaultGoalId || initialDraft.current.goalId || "");
  useEffect(() => { if (!agentId && confirmed.length === 1) setAgentId(confirmed[0].id); }, [agentId, confirmed]);
  useEffect(() => { if (workflow.requires_goal && !goalId && goals.data?.goals.length === 1) setGoalId(goals.data.goals[0].id); }, [goalId, goals.data, workflow.requires_goal]);
  const [problem, setProblem] = useState(defaultInput?.text || initialDraft.current.problem || "");
  const [expected, setExpected] = useState(initialDraft.current.expected || "");
  const [helpDefine, setHelpDefine] = useState(initialDraft.current.helpDefine || false);
  const traces = useQuery({ queryKey: ["projects", projectId, "traces"], queryFn: () => api<{traces: Array<{id: string; name: string; count: number}>}>(projectPath(projectId, "/traces")) });
  const [traceId, setTraceId] = useState(defaultInput?.type === "trace" ? defaultInput.id || "" : initialDraft.current.traceId || "");
  const describedProblem = `What went wrong: ${problem.trim()}\nExpected behavior: ${helpDefine ? "Help me define the intended behavior." : expected.trim()}`;
  const input = defaultInput || (workflow.requires_goal
    ? { type: "goal", id: goalId }
    : traceId
      ? { type: "trace", id: traceId }
      : workflow.workflow === "fix"
        ? { type: "description", text: describedProblem }
        : workflow.workflow === "discover"
          ? { type: "trace" }
          : { type: "agent" });
  const intent = { workflow: workflow.workflow, ...(agentId ? { agent_id: agentId } : {}), input, options: {} };
  const intentKey = JSON.stringify(intent);
  const operationDraft = useSessionOperation(draftKey, operationBinding(intentKey), { agentId, goalId, problem, expected, helpDefine, traceId } satisfies WorkflowDraft);
  const operation = operationDraft.operation;
  const [preparedIntent, setPreparedIntent] = useState(intent);
  const preparedIntentKey = JSON.stringify(preparedIntent);
  const currentPreparation = preparedIntentKey === intentKey;
  useEffect(() => {
    const timer = window.setTimeout(() => setPreparedIntent(intent), 300);
    return () => window.clearTimeout(timer);
  }, [intentKey]);
  const preparation = useQuery({
    queryKey: ["projects", projectId, "workflows", "prepare", preparedIntent, operation],
    queryFn: () => post<PreparedWorkflowStart>(projectPath(projectId, "/workflows/prepare"), { ...preparedIntent, operation_id: operation }),
    enabled: currentPreparation,
  });
  const prepared = preparation.data;
  const blockers = prepared?.prerequisites.filter((item) => item.blocking) || [];
  const limitations = prepared?.limitations || [];
  const canStart = currentPreparation && (prepared?.state === "ready" || prepared?.state === "ready_with_limits");
  const mutation = useMutation({
    mutationFn: () => post<{ id?: string; task_id?: string }>(projectPath(projectId, "/tasks"), { ...prepared!.normalized_intent, operation_id: operation }),
    onSuccess: (task) => {
      const taskId = task.task_id || task.id;
      if (!taskId) return;
      operationDraft.clear();
      onClose();
      navigate(`/projects/${projectId}/tasks/${taskId}`);
    },
  });
  const startLabel: Record<string, string> = { fix: "Start fix", optimize: "Find improvements", baseline: "Run baseline", eval: "Prepare evaluator", design: "Create evaluation proposal", audit: "Start audit", discover: "Discover issues", assess: "Analyze project", observe: "Analyze production" };
  const selectedAgent = confirmed.find((agent) => agent.id === agentId);
  const selectedGoal = goals.data?.goals.find((goal) => goal.id === goalId);
  const inputLabel = input.type === "description"
    ? problem || "Problem description required"
    : input.type === "trace"
      ? traces.data?.traces.find((trace) => trace.id === input.id)?.name || input.id || "Trace required"
      : input.type === "goal"
        ? selectedGoal?.name || "Goal required"
        : input.type.replaceAll("_", " ");
  const verification = prepared?.prerequisites.filter((item) => ["measurement_plan", "evaluation", "baseline", "regression_baselines", "trace_selection", "problem_description"].includes(item.code)) || [];
  const backend = prepared?.prerequisites.find((item) => item.code === "coding_backend")?.evidence_refs[0];
  return <Modal title={workflow.name} eyebrow="Prepared action" onClose={onClose} wide><form className="form prepared-action-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><p className="modal-purpose">{workflow.purpose}</p>
    <section className="prepared-action-section"><header><span>1</span><div><h3>Intent</h3><p>The behavior and evidence this task will act on.</p></div></header><div className="prepared-action-fields"><label>Agent<select value={agentId} onChange={(event) => { setAgentId(event.target.value); setGoalId(""); }} required={workflow.workflow !== "discover"}><option value="">Select an agent</option>{confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>{workflow.requires_goal && <label>Goal<select value={goalId} onChange={(event) => setGoalId(event.target.value)} required><option value="">Select a goal</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select></label>}{!defaultInput && ["fix", "discover"].includes(workflow.workflow) && <><label>Trace evidence<select value={traceId} onChange={event => setTraceId(event.target.value)} required={workflow.workflow === "discover"}><option value="">{workflow.workflow === "fix" ? "Describe a problem instead" : "Select retained trace evidence"}</option>{traces.data?.traces.map(trace => <option key={trace.id} value={trace.id}>{trace.name} · {trace.count} spans</option>)}</select></label>{workflow.workflow === "fix" && !traceId && <><label>What went wrong?<textarea value={problem} onChange={event => setProblem(event.target.value)} maxLength={1800} required /></label><label>What should happen instead?<textarea value={expected} onChange={event => setExpected(event.target.value)} maxLength={1800} required={!helpDefine} disabled={helpDefine} /></label><label className="checkbox-label"><input type="checkbox" checked={helpDefine} onChange={event => setHelpDefine(event.target.checked)} /> Help me define the expected behavior</label></>}</>}</div><dl className="prepared-action-summary"><div><dt>Selected input</dt><dd>{inputLabel}</dd></div>{helpDefine && <div><dt>Expected behavior</dt><dd>The task must ask before authoring a repair.</dd></div>}</dl></section>
    {(preparation.isLoading || !currentPreparation) && <p className="preparation-loading">Checking the current evidence and prerequisites…</p>}
    {preparation.error && <p className="error-banner">{preparation.error.message}</p>}
    {prepared && <>
      <section className="prepared-action-section"><header><span>2</span><div><h3>Scope</h3><p>The exact identity, code, and retained evidence boundary.</p></div></header><dl className="prepared-action-summary"><div><dt>Agent</dt><dd>{selectedAgent?.name || (workflow.workflow === "discover" ? "Determined from selected evidence" : "Not selected")}</dd></div><div><dt>Permitted code</dt><dd>{prepared.normalized_intent.scope?.length ? prepared.normalized_intent.scope.map((path) => <code key={path}>{path}</code>) : selectedAgent?.code_scopes?.length ? selectedAgent.code_scopes.map((path) => <code key={path}>{path}</code>) : "Read-only or unresolved"}</dd></div><div><dt>Source</dt><dd>{prepared.code_source?.kind === "git" ? `${prepared.code_source.revision?.slice(0, 12) || "No commit"}${prepared.code_source.dirty ? " · uncommitted changes" : " · clean"}` : prepared.code_source?.kind === "folder" ? "Local non-Git folder" : "No code source required"}</dd></div><div><dt>Evidence</dt><dd>{prepared.source_identity ? `${String(prepared.source_identity.kind || "source")} ${String(prepared.source_identity.id || "")}` : inputLabel}</dd></div></dl></section>
      <section className="prepared-action-section"><header><span>3</span><div><h3>Verification</h3><p>Evidence that must exist before this task can make a measured claim.</p></div></header>{verification.length ? <ul className="prepared-check-list">{verification.map((item) => <li key={item.code} className={item.blocking ? "is-blocked" : "is-ready"}><span>{item.blocking ? "Required" : "Ready"}</span><strong>{preparationName(item.code)}</strong><small>{item.evidence_refs.length ? `${item.evidence_refs.length} retained reference${item.evidence_refs.length === 1 ? "" : "s"}` : "No retained reference yet"}</small></li>)}</ul> : <p className="quiet-surface">This workflow records evidence under its built-in verification contract.</p>}<p className="prepared-outputs">Expected result: {prepared.expected_outputs.map((item) => item.replaceAll("_", " ")).join(" · ")}</p></section>
      <section className="prepared-action-section"><header><span>4</span><div><h3>Limits</h3><p>Runtime bounds and known coverage limits.</p></div></header><dl className="prepared-action-summary"><div><dt>Time</dt><dd>{Math.round((prepared.normalized_intent.limits?.max_elapsed_seconds || 0) / 60)} minutes maximum</dd></div><div><dt>Attempts</dt><dd>{prepared.normalized_intent.limits?.max_trials || 0} maximum</dd></div><div><dt>Per attempt</dt><dd>{prepared.normalized_intent.limits?.trial_timeout_seconds || 0} seconds</dd></div><div><dt>Coding backend</dt><dd>{String(backend?.id || prepared.normalized_intent.assistant || "Project default")}{backend?.model ? ` · ${String(backend.model)}` : ""}</dd></div></dl>{limitations.length > 0 && <div className="preparation-limits"><strong>Known limits</strong><ul>{limitations.map((item) => <li key={`${item.code}-${item.field || "task"}`}>{preparationName(item.code)}</li>)}</ul></div>}</section>
      <section className={`workflow-preparation preparation-${prepared.state}`}><header><div><p className="eyebrow">Start readiness</p><strong>{prepared.state === "ready" ? "Ready to start" : prepared.state === "ready_with_limits" ? "Ready with limits" : "Resolve the requirements below"}</strong></div><Status value={prepared.state} /></header>{blockers.length > 0 && <div className="preparation-block"><span>Required before start</span><ul>{blockers.map((item) => { const route = prerequisiteRoute(projectId, agentId, goalId, item.code); const reason = item.resolution?.context?.reason; return <li key={`${item.code}-${item.field || "task"}`}><div><strong>{preparationName(item.code)}</strong>{typeof reason === "string" && <small>{reason}</small>}</div>{item.code === "stale_preparation" ? <Button type="button" tone="secondary" onClick={() => preparation.refetch()}>Review current inputs</Button> : route ? <Link className="button button-secondary" target="_blank" rel="noreferrer" to={`${route}?return=${encodeURIComponent(location.pathname + location.search)}`}>Resolve in a new tab</Link> : <small>Complete this in the fields above.</small>}</li>; })}</ul></div>}<div className="preparation-refresh"><Button type="button" tone="quiet" onClick={() => preparation.refetch()} disabled={preparation.isFetching}>↻ {preparation.isFetching ? "Checking…" : "Check again"}</Button><span>{Object.keys(prepared.revisions).length} frozen input revision{Object.keys(prepared.revisions).length === 1 ? "" : "s"}; checked again at start.</span></div></section>
    </>}
    {mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={!canStart || mutation.isPending}>{mutation.isPending ? "Starting…" : startLabel[workflow.workflow] || "Start"}</Button></footer></form></Modal>;
}

function preparationName(code: string) {
  const names: Record<string, string> = {
    input_required: "Choose the evidence or objective",
    agent_selection: "Select the agent that owns this work",
    agent_confirmation: "Confirm this agent before starting",
    issue_ownership: "Select the agent that owns this issue",
    goal_selection: "Select a goal",
    trace_selection: "Select trace evidence",
    problem_description: "Describe the problem and expected behavior",
    code_binding: "Bind this agent to application code",
    code_scope: "Keep the repair inside the confirmed code scope",
    clean_source: "Use a clean committed Git revision",
    coding_backend: "Configure and authenticate the coding backend",
    measurement_plan: "Accept a measurement plan",
    evaluation: "Prepare a reviewed evaluation",
    baseline: "Run a current baseline",
    regression_baselines: "Refresh the required regression baselines",
    pending_observation: "Resolve the pending production observation",
    execution_profile: "Choose a configured execution profile",
    stale_preparation: "Review inputs changed since preparation",
    execution_settings_unavailable: "Execution settings could not be fully inspected",
  };
  return names[code] || code.replaceAll("_", " ");
}

export function useSelectedTask(projectId: string) {
  const location = useLocation();
  const navigate = useNavigate();
  const match = location.pathname.match(/\/tasks\/([^/]+)$/);
  const selected = match?.[1] || new URLSearchParams(location.search).get("task");
  const close = () => {
    const search = new URLSearchParams(location.search);
    search.delete("task");
    if (match) navigate({ pathname: `/projects/${projectId}/tasks`, search: search.toString() });
    else navigate({ pathname: location.pathname, search: search.toString() }, { replace: true });
  };
  return { selected, close };
}

export function AgentGoalNames({ projectId, agentId, goalId }: { projectId: string; agentId?: string | null; goalId?: string | null }) {
  const agents = useAgents(projectId);
  const goals = useGoals(projectId, agentId || undefined);
  return <>{agents.data?.agents.find((item) => item.id === agentId)?.name || "Unknown agent"}{goalId && <> · {goals.data?.goals.find((item) => item.id === goalId)?.name || "Unknown goal"}</>}</>;
}
