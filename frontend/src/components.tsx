import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { useWorkflowDraft, useSaveWorkflowDraft, workflowDraftKey, type WorkflowDraft, type SavedWorkflowDraft, type DraftResponse } from "./workflow-drafts";
import { ExecutionProfileEditor, useExecutionSettings } from "./execution";
import { startGoalRun, type GoalRun } from "./goal-runs";
import { api, operationId, post, projectPath } from "./api";
import { useAgents, useGoals, useTask } from "./hooks";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Agent, Goal, PreparedWorkflowStart, Project, ResultKind, TaskDetail, WorkflowDefinition } from "./types";

type IconName = "home" | "tasks" | "agent" | "connector" | "settings" | "plus" | "close" | "arrow" | "moon" | "sun" | "menu";

const iconPaths: Record<IconName, ReactNode> = {
  home: <><path d="M3 10.8 12 3l9 7.8" /><path d="M5.5 9.8V21h13V9.8M9 21v-7h6v7" /></>,
  tasks: <><path d="M7 4h10M7 12h10M7 20h10" /><path d="m3 4 .7.7L5 3.3M3 12l.7.7L5 11.3M3 20l.7.7L5 19.3" /></>,
  agent: <><path d="M12 3 4.5 7.2v9.6L12 21l7.5-4.2V7.2L12 3Z" /><circle cx="12" cy="12" r="3" /></>,
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

export function agentOriginLabel(agent: Agent) {
  if (agent.origin === "manual") return "Added manually";
  if (agent.origin === "code") return "Detected in code";
  if (agent.origin === "traces") return "Detected from traces";
  return "Source not recorded";
}

const agentRoles = [
  ["unknown", "Not classified"],
  ["serving", "Serving users"],
  ["background", "Background work"],
  ["evaluation", "Evaluation"],
  ["development_utility", "Development utility"],
] as const;

function AgentRoleField({ value, onChange, disabled }: { value: NonNullable<Agent["role"]>; onChange: (value: NonNullable<Agent["role"]>) => void; disabled?: boolean }) {
  return <label>Role<select value={value} onChange={event => onChange(event.target.value as NonNullable<Agent["role"]>)} disabled={disabled}>{agentRoles.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>;
}

export function Modal({ title, eyebrow, children, onClose, wide = false }: { title: string; eyebrow?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  const trigger = useRef<HTMLElement | null>(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const focusable = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []).filter((element) => !element.hasAttribute("hidden") && element.getClientRects().length > 0);
    const frame = window.requestAnimationFrame(() => {
      if (panel.current?.contains(document.activeElement)) return;
      const initial = panel.current?.querySelector<HTMLElement>("input:not([disabled]), textarea:not([disabled]), select:not([disabled])") || focusable()[0];
      initial?.focus();
    });
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
      if (!panel.current?.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
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

export function Sidebar({ projects, project, agents, open, onClose, onAddProject, onAddAgent }: { projects: Project[]; project: Project; agents: Agent[]; open: boolean; onClose: () => void; onAddProject: () => void; onAddAgent: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const projectBase = `/projects/${project.id}`;
  const currentAgent = agents.find(agent => location.pathname.startsWith(`${projectBase}/agents/${agent.id}/`));
  const visibleAgents = agents.slice(0, 8);
  if (currentAgent && !visibleAgents.includes(currentAgent)) visibleAgents.splice(0, 1, currentAgent);
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
      <NavLink to={`${projectBase}/agents`} className={linkClass}><Icon name="agent" />Agents</NavLink>
      <NavLink to={`${projectBase}/production`} className={linkClass}><Icon name="connector" />Production</NavLink>
      <NavLink to={`${projectBase}/tasks`} className={location.pathname.startsWith(`${projectBase}/results/`) ? "nav-link is-active" : linkClass}><Icon name="tasks" />Activity</NavLink>
      <NavLink to={`${projectBase}/settings/project`} className={location.pathname.startsWith(`${projectBase}/settings/`) ? "nav-link is-active" : linkClass}><Icon name="settings" />Settings</NavLink>
    </nav>
    <div className="nav-section"><div className="nav-heading"><span>Agent workspaces</span><Button tone="quiet" aria-label="Add agent" onClick={onAddAgent}><Icon name="plus" size={15} /></Button></div>
      <nav className="agent-links" onClick={onClose}>{visibleAgents.map((agent) => <NavLink key={agent.id} to={`${projectBase}/agents/${agent.id}/overview`} className={location.pathname.startsWith(`${projectBase}/agents/${agent.id}/`) ? "nav-link is-active" : linkClass}><span className="agent-glyph" aria-hidden="true">A</span><span>{agent.name}</span></NavLink>)}{agents.length > visibleAgents.length && <Link className="nav-empty" to={`${projectBase}/agents`}>All {agents.length} agents →</Link>}{!agents.length && <Link className="nav-empty" to={`${projectBase}/onboarding`}>Detect agents</Link>}</nav>
    </div>
    <div className="sidebar-bottom"><p className="local"><span />Local workspace</p></div>
  </aside>;
}

export function Topbar({ project, taskCount, onMenu, newWork }: { project: Project; taskCount: number; onMenu: () => void; newWork?: ReactNode }) {
  const [dark, setDark] = useState(document.documentElement.dataset.theme === "dark");
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    localStorage.setItem("agentagon-theme", next ? "dark" : "light");
  };
  return <header className="topbar"><div className="topbar-project"><Button tone="quiet" className="mobile-only" aria-label="Open navigation" onClick={onMenu}><Icon name="menu" /></Button><span className="scope-dot" /><span className="topbar-project-name">{project.name}</span>{project.branch && <span className="branch">{project.branch}</span>}</div><div className="topbar-actions">{taskCount > 0 && <Link className="activity-link" to={`/projects/${project.id}/tasks`}>Needs you <strong>{taskCount}</strong></Link>}{newWork}<Button tone="quiet" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggle}><Icon name={dark ? "sun" : "moon"} /></Button></div></header>;
}

function AssessmentOutcome({ projectId, state, result }: { projectId: string; state: string; result: Record<string, unknown> }) {
  const candidates = Array.isArray(result.candidates) ? result.candidates.filter((candidate) => typeof candidate !== "object" || candidate === null || (candidate as Record<string, unknown>).keep !== false) : [];
  const issues = Array.isArray(result.issues) ? result.issues : [];
  const recommendations = Array.isArray(result.recommendations) ? result.recommendations : [];
  const limitations = Array.isArray(result.limitations) ? result.limitations.filter((item): item is string => typeof item === "string") : [];
  const title = state === "completed" ? "Project assessment complete" : state === "completed_with_limits" ? "Project assessed with limits" : state === "cancelled" ? "Assessment cancelled; discoveries retained" : "Assessment stopped; discoveries retained";
  return <section className="assessment-outcome"><header><div><p className="eyebrow">Assessment outcome</p><h3>{title}</h3><p>{typeof result.summary === "string" ? result.summary : "Review the available discoveries and limits before choosing what to do next."}</p></div><Status value={state} /></header><div className="assessment-counts"><div><strong>{candidates.length}</strong><span>agents found</span></div><div><strong>{issues.length}</strong><span>issues found</span></div><div><strong>{recommendations.length}</strong><span>next actions</span></div></div>{limitations.length > 0 && <div className="assessment-limits"><strong>What was limited</strong><ul className="plain-list">{limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>}<div className="assessment-actions"><Link className="button button-primary" to={`/projects/${projectId}/agents`}>Open agents</Link>{issues.length > 0 && <Link className="button button-secondary" to={`/projects/${projectId}/issues`}>Review issues</Link>}<Link className="button button-secondary" to={`/projects/${projectId}/home`}>Return to overview</Link></div></section>;
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
  const navigate = useNavigate();
  const task = useTask(projectId, taskId);
  const goalRunId = task.data?.goal_run_id;
  const goalRun = useQuery({
    queryKey: ["projects", projectId, "goal-runs", goalRunId],
    queryFn: () => api<GoalRun>(projectPath(projectId, `/goal-runs/${goalRunId}`)),
    enabled: Boolean(goalRunId),
  });
  const [answer, setAnswer] = useState("");
  const [guidance, setGuidance] = useState("");
  const [continuing, setContinuing] = useState(false);
  const continuationDraft = useRef(loadOperationDraft<{ minutes: number }>(`agentagon.continuation:${projectId}:${taskId}`));
  const [minutes, setMinutes] = useState(continuationDraft.current.minutes || 30);
  const controlOperation = useRef<{ binding: string; id: string } | undefined>(undefined);
  const continuation = useSessionOperation(`agentagon.continuation:${projectId}:${taskId}`, operationBinding({ taskId, minutes }), { minutes });
  const continueTask = useMutation({
    mutationFn: () => post<{ task_id: string }>(projectPath(projectId, `/tasks/${taskId}/continue`), { operation_id: continuation.operation, max_elapsed_seconds: Math.round(minutes * 60) }),
    onSuccess: async next => {
      const nextId = next.task_id;
      if (!nextId) throw new Error("The new attempt did not return a task identity. Retry with the same request.");
      continuation.clear();
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] });
      navigate(`/projects/${projectId}/tasks/${nextId}?view=running`);
    },
  });
  const mutate = useMutation({
    mutationFn: ({ action, body }: { action: string; body: Record<string, unknown> }) => {
      const binding = operationBinding({ taskId, action, body });
      if (controlOperation.current?.binding !== binding) controlOperation.current = { binding, id: operationId() };
      return post(projectPath(projectId, `/tasks/${taskId}/${action}`), { operation_id: controlOperation.current.id, ...body });
    },
    onSuccess: (_result, variables) => { controlOperation.current = undefined; if (variables.action === "answer") setAnswer(""); if (variables.action === "message") setGuidance(""); queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] }); },
  });
  const assessmentCandidates = task.data?.result?.candidates;
  const assessmentCandidateCount = task.data?.workflow === "assess" && Array.isArray(assessmentCandidates)
    ? assessmentCandidates.filter((candidate) => typeof candidate !== "object" || candidate === null || (candidate as Record<string, unknown>).keep !== false).length
    : 0;
  const resultReference = taskResultReference(task.data);
  const hasTypedResult = Boolean(resultReference);
  const terminal = Boolean(task.data && ["completed", "completed_with_limits", "failed", "cancelled"].includes(task.data.state));
  const hasAssessmentOutcome = Boolean(terminal && task.data?.workflow === "assess" && task.data.result);
  const allowed = new Set(task.data?.available_actions || []);
  const canResume = allowed.has("resume");
  const canCancel = allowed.has("cancel");
  const canContinue = allowed.has("continue");
  const canAnswer = allowed.has("answer");
  const budgetExhausted = task.data?.recovery?.reason_code === "budget_exhausted";
  const account = task.data?.accounting;
  const elapsed = (seconds: number) => seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds)} sec`;
  const latestUpdate = task.data?.events.filter(event => event.text).at(-1);
  const latestGuidance = task.data?.conversation.filter(message => message.kind === "guidance").at(-1);
  const lessons = Array.isArray(task.data?.result?.lessons)
    ? task.data.result.lessons.filter((item): item is { id: string; version: number; decision: string; reason: string } => Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).id === "string"))
    : [];
  const activity = task.data ? <div className="conversation" aria-live="polite">
    {task.data.conversation.map((message, index) => <article className={`message message-${message.role}`} key={index}><span>{message.role === "user" ? "You" : message.role === "system" ? "Agentagon" : "Coding assistant"}{message.delivery_state ? ` · ${message.delivery_state === "pending" ? "Waiting for a safe stopping point" : "Delivered"}` : ""}</span><p>{message.text}</p></article>)}
    {task.data.events.map((event, index) => <article className="task-event" key={index}><span>{event.type || "Progress"}</span><p>{event.text}</p></article>)}
    {!task.data.conversation.length && !task.data.events.length && <p className="quiet-copy">{task.data.state === "queued" ? "Waiting for coding assistant capacity. This task continues in the background." : task.data.state === "running" ? "Running in the background." : "Waiting for the first update."}</p>}
  </div> : null;
  return <aside className={`task-panel ${hasAssessmentOutcome ? "task-panel-result" : ""}`} aria-label="Task details">
    <header className="task-panel-header"><div><p className="eyebrow">Task</p><h2>{task.data?.title || (task.isError ? "Task unavailable" : "Loading task…")}</h2></div><Button tone="quiet" aria-label="Close task" onClick={onClose}><Icon name="close" /></Button></header>
    {task.isError && <div className="error-banner" role="alert"><p>{task.error.message}</p><Button tone="secondary" onClick={() => task.refetch()} disabled={task.isFetching}>Retry</Button></div>}
    {task.data && <><div className="task-meta"><Status value={task.data.state} /><span>{task.data.agent_name || "Project"}</span>{task.data.goal_name && <span>{task.data.goal_name}</span>}</div>
      {!terminal && !task.data.question && <div className="next-action" aria-live="polite"><strong>{task.data.state === "queued" ? "Waiting for capacity" : "Latest update"}</strong><p>{latestUpdate?.text || (task.data.state === "queued" ? "This task starts when coding backend capacity is available." : task.data.state === "running" ? "Work is running. The first progress update has not arrived yet." : "Open the available recovery action to continue this task.")}</p>{task.data.updated_at && <small>Updated {new Date(task.data.updated_at).toLocaleString()}</small>}</div>}
      {latestGuidance && <div className="next-action" role="status"><strong>{latestGuidance.delivery_state === "delivered" ? "Guidance delivered" : "Guidance queued"}</strong><p>{latestGuidance.text}</p>{latestGuidance.delivery_state !== "delivered" && <small>Waiting for a safe stopping point before continuing.</small>}</div>}
      {account && <p className="quiet-copy">Human waiting does not use this task’s active allowance. Evaluation, reflection, and review operations can still reach their separately admitted deadlines.</p>}
      {account && <dl className="compact-definition"><div><dt>Active work</dt><dd>{elapsed(account.active_seconds)}</dd></div><div><dt>Task allowance remaining</dt><dd>{elapsed(account.remaining_seconds)}</dd></div>{account.waiting_seconds > 0 && <div><dt>Waiting for input</dt><dd>{elapsed(account.waiting_seconds)}</dd></div>}{account.queued_seconds > 0 && <div><dt>Queued</dt><dd>{elapsed(account.queued_seconds)}</dd></div>}{account.offline_seconds > 0 && <div><dt>Service offline</dt><dd>{elapsed(account.offline_seconds)}</dd></div>}{account.unknown_seconds > 0 && <div><dt>Unclassified time</dt><dd>{elapsed(account.unknown_seconds)}</dd></div>}</dl>}
      {task.data.continuation_of && <p className="quiet-copy">Continues <Link to={`/projects/${projectId}/tasks/${task.data.continuation_of}?view=all`}>an earlier attempt</Link> with a new budget.</p>}
      {canAnswer && task.data.question?.kind === "approval" && <div className="decision"><h3>Approval needed</h3><p>{task.data.question.text}</p><div className="task-controls"><Button disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { decision: "accept" } } })}>Approve</Button><Button tone="secondary" disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { decision: "decline" } } })}>Decline</Button></div></div>}
      {canAnswer && task.data.question && task.data.question.kind !== "approval" && <form className="decision" onSubmit={(event) => { event.preventDefault(); mutate.mutate({ action: "answer", body: { question_id: task.data!.question!.id, answer: { text: answer } } }); }}><h3>Decision needed</h3><p>{task.data.question.text}</p><textarea aria-label="Response" value={answer} onChange={(event) => setAnswer(event.target.value)} required rows={3} /><Button type="submit" disabled={mutate.isPending}>Send response</Button></form>}
      {resultReference && <section className="task-result-summary task-result-peek"><p className="eyebrow">Result</p><h3>{task.data.state === "failed" ? "Task stopped with retained evidence" : task.data.state === "completed_with_limits" ? "Result ready with limits" : terminal ? "Result ready for review" : "Partial result available"}</h3><p>{typeof task.data.result?.summary === "string" ? task.data.result.summary : "Open the full result to inspect its evidence, supported actions, and limits."}</p>{Array.isArray(task.data.result?.limitations) && <ul className="plain-list">{task.data.result.limitations.filter((item): item is string => typeof item === "string").map((item) => <li key={item}>{item}</li>)}</ul>}<Link className="button button-primary" to={`/projects/${projectId}/results/${resultReference.kind}/${encodeURIComponent(resultReference.id)}`}>Open full result</Link></section>}
      {hasAssessmentOutcome && <AssessmentOutcome projectId={projectId} state={task.data.state} result={task.data.result!} />}
      {!hasTypedResult && !hasAssessmentOutcome && typeof task.data.result?.summary === "string" && <div className="task-result-summary"><p className="eyebrow">Result</p><h3>{task.data.state === "failed" ? "Task failed" : task.data.state === "completed_with_limits" ? "Completed with limits" : "Task completed"}</h3><p>{task.data.result.summary}</p>{Array.isArray(task.data.result.limitations) && <ul className="plain-list">{task.data.result.limitations.filter((item): item is string => typeof item === "string").map((item) => <li key={item}>{item}</li>)}</ul>}</div>}
      {assessmentCandidateCount > 0 && !hasAssessmentOutcome && <section className="task-handoff"><p className="eyebrow">Next step</p><h3>{assessmentCandidateCount} agent{assessmentCandidateCount === 1 ? "" : "s"} found</h3><p>Open an agent and choose what to improve.</p><Link className="button button-primary" to={`/projects/${projectId}/agents`}>Open agents<Icon name="arrow" size={15} /></Link></section>}
      {task.data.next_action && !task.data.question && <div className={task.data.state === "failed" ? "error-banner" : "next-action"}><strong>{task.data.state === "failed" ? "Why it stopped" : "Next action"}</strong><p>{task.data.next_action}</p></div>}
      {budgetExhausted && <div className="next-action"><strong>This attempt reached its time limit</strong><p>Its discoveries and evidence are retained. A new attempt needs a new budget; resuming cannot extend this one.</p>{task.data.workflow === "assess" && !hasAssessmentOutcome && <Link to={`/projects/${projectId}/agents`}>Open agents</Link>}{task.data.workflow === "assess" && !canContinue && <p><Link to={`/projects/${projectId}/onboarding`}>Set up another assessment</Link></p>}</div>}
      {task.data.recovery?.reason_code === "execution_unreconciled" && <div className="next-action"><strong>Some execution time is unresolved</strong><p>The remaining budget reserves that unknown time. The earlier evidence stays available; start another attempt only with a new explicit budget.</p></div>}
      <div className="task-controls">{goalRun.data && <Link className="button button-secondary" to={`/projects/${projectId}/agents/${goalRun.data.agent_id}/goals/${goalRun.data.goal_id}?run=${goalRun.data.id}`}>Open goal</Link>}{!goalRunId && canResume && <Button disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "resume", body: {} })}>Resume</Button>}{!goalRunId && allowed.has("pause") && <Button tone="secondary" disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "pause", body: {} })}>Pause</Button>}{!goalRunId && canCancel && <Button tone="secondary" disabled={mutate.isPending} onClick={() => mutate.mutate({ action: "cancel", body: {} })}>Cancel</Button>}{canContinue && !continuing && <Button onClick={() => setContinuing(true)}>Start another attempt</Button>}</div>
      {goalRunId && goalRun.isLoading && <p role="status">Loading goal controls…</p>}
      {goalRunId && goalRun.error && <p className="error-banner" role="alert">Goal controls could not be loaded. <Button tone="quiet" onClick={() => goalRun.refetch()}>Retry</Button></p>}
      {allowed.has("message") && <form className="decision form" onSubmit={event => { event.preventDefault(); mutate.mutate({ action: "message", body: { message: guidance } }); }}><label>Guide this task<textarea rows={3} maxLength={4000} value={guidance} onChange={event => setGuidance(event.target.value)} required placeholder="Add context or correct the direction of this work" /></label><p className="quiet-copy">Guidance is delivered at a safe stopping point. Paused work stays paused until you resume it; other work continues within this attempt’s remaining allowance.</p><Button type="submit" disabled={mutate.isPending || !guidance.trim()}>{mutate.isPending && mutate.variables?.action === "message" ? "Sending…" : "Send guidance"}</Button></form>}
      {continuing && canContinue && <form className="decision form" onSubmit={event => { event.preventDefault(); continueTask.mutate(); }}><h3>Start another attempt</h3><p>Uses the same work request and checks its current prerequisites. The earlier result stays in history.</p><label>Maximum active time (minutes)<input type="number" min={1} max={1440} step={1} required value={minutes} onChange={event => setMinutes(Number(event.target.value))} /></label><div className="form-actions"><Button tone="secondary" type="button" onClick={() => setContinuing(false)} disabled={continueTask.isPending}>Back</Button><Button type="submit" disabled={continueTask.isPending}>{continueTask.isPending ? "Starting…" : "Start another attempt"}</Button></div>{continueTask.error && <p className="error-banner" role="alert">{continueTask.error.message}</p>}</form>}
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
  return <Modal title={mode === "clone" ? "Clone a repository" : "Open a local folder"} eyebrow={mode === "clone" ? "New local checkout" : "Existing project"} onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>{mode === "clone" ? <><p className="modal-purpose">Clone a Git repository into a new folder, then analyze it locally.</p><label>Repository URL<input type="url" value={repository} onChange={event => setRepository(event.target.value)} placeholder="https://example.com/team/agent.git" required autoFocus /></label><label>Clone into folder<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Users/you/code/my-agent" required /></label></> : <><p className="modal-purpose">Open an existing agent project on this machine.</p><label>Local folder path<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Users/you/code/my-agent" required autoFocus /></label></>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mode === "clone" ? "Clone and open" : "Open folder"}</Button></footer></form></Modal>;
}

export function AddAgentModal({ projectId, agent, onClose }: { projectId: string; agent?: Agent; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(agent?.name || "");
  const [description, setDescription] = useState(agent?.responsibility || "");
  const [paths, setPaths] = useState(agent?.code_scopes.join(", ") || "");
  const [role, setRole] = useState<NonNullable<Agent["role"]>>(agent?.role || "unknown");
  const mutation = useMutation({ mutationFn: () => post<Agent>(projectPath(projectId, agent ? `/agents/${agent.id}` : "/agents"), { name, description, role, code_scopes: paths.split(",").map((item) => item.trim()).filter(Boolean), shared_dependencies: agent?.shared_dependencies || [], trace_selector: agent?.trace_selector || {}, status: "confirmed", ...(agent?.revision !== undefined ? { expected_revision: agent.revision } : {}) }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] }); onClose(); } });
  return <Modal title={agent ? "Edit agent" : "Add agent"} eyebrow="Agent configuration" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Agent name<input value={name} onChange={(event) => setName(event.target.value)} required autoFocus /></label><label>Responsibility<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} placeholder="What this agent does for its users" /></label><AgentRoleField value={role} onChange={setRole} /><label>Code paths<input value={paths} onChange={(event) => setPaths(event.target.value)} placeholder="src/agent.py, src/tools" required={!agent?.trace_selector || !Object.keys(agent.trace_selector).length} /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : agent ? "Save changes" : "Add agent"}</Button></footer></form></Modal>;
}

export function AddGoalModal({ projectId, agentId, intent = "goal", onClose }: { projectId: string; agentId: string; intent?: "goal" | "existing-evaluation"; onClose: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const draftKey = `agentagon.new-goal:${projectId}:${agentId}:${intent}`;
  const initial = useRef(loadOperationDraft<{ objective: string; details: string }>(draftKey));
  const [objective, setObjective] = useState(initial.current.objective ?? "");
  const [details, setDetails] = useState(initial.current.details || "");
  const goalPayload = { category: "custom", name: objective.trim().slice(0, 160), objective: objective.trim() };
  const creation = useSessionOperation(draftKey, operationBinding(goalPayload), { objective, details });
  const runOperation = useSessionOperation(`${draftKey}:run`, operationBinding({ goalOperation: creation.operation, details }), {});
  const mutation = useMutation({
    mutationFn: async () => {
      const goal = await post<Goal>(projectPath(projectId, `/agents/${agentId}/goals`), { operation_id: creation.operation, ...goalPayload });
      const run = intent === "existing-evaluation" ? undefined : await startGoalRun(projectId, agentId, goal.id, { operation_id: runOperation.operation, details: details.trim() });
      return { goal, run };
    },
    onSuccess: async ({ goal, run }) => {
      creation.clear(); runOperation.clear();
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals"] });
      onClose();
      const params = new URLSearchParams(run ? { run: run.id } : { evaluator: "reuse" });
      const returnTo = new URLSearchParams(location.search).get("return");
      if (returnTo?.startsWith(`/projects/${projectId}/`)) params.set("return", returnTo);
      navigate(`/projects/${projectId}/agents/${agentId}/goals/${goal.id}?${params}`);
    },
  });
  const close = () => { if (!mutation.isPending) onClose(); };
  return <Modal title={intent === "existing-evaluation" ? "Use an existing evaluation" : "What should improve?"} onClose={close}>
    <form className="form" onSubmit={event => { event.preventDefault(); mutation.mutate(); }}>
      <label>Goal<textarea value={objective} onChange={event => setObjective(event.target.value)} rows={3} maxLength={4000} required autoFocus disabled={mutation.isPending} placeholder="For example, resolve support requests without repeating failed tool calls" /></label>
      {intent !== "existing-evaluation" && <><label>Details <span className="optional">Optional</span><textarea value={details} onChange={event => setDetails(event.target.value)} rows={3} maxLength={4000} disabled={mutation.isPending} placeholder="Examples, constraints, or what a good result looks like" /></label><p className="quiet-copy">Agentagon measures this goal, works on improvements, and checks the results. It asks when it needs your input.</p></>}
      {mutation.error && <p className="error-banner" role="alert">{mutation.error.message}</p>}
      <footer className="form-actions"><Button tone="secondary" type="button" onClick={close} disabled={mutation.isPending}>Cancel</Button><Button type="submit" disabled={mutation.isPending || !objective.trim()}>{mutation.isPending ? "Starting…" : intent === "existing-evaluation" ? "Choose evaluator" : "Go"}</Button></footer>
    </form>
  </Modal>;
}


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

type WorkflowLaunchProps = { projectId: string; workflow: WorkflowDefinition; defaultAgentId?: string; defaultGoalId?: string; defaultInput?: {type: string; id?: string; text?: string}; onClose: () => void };

export function LaunchWorkflowModal(props: WorkflowLaunchProps) {
  const key = workflowDraftKey(props.projectId, props.workflow.workflow, props.defaultAgentId, props.defaultGoalId, props.defaultInput);
  const [reload, setReload] = useState(0);
  return <WorkflowDraftLoader key={`${key}:${reload}`} {...props} draftKey={key} onReloadDraft={() => setReload(value => value + 1)} />;
}

function WorkflowDraftLoader(props: WorkflowLaunchProps & { draftKey: string; onReloadDraft: () => void }) {
  const saved = useWorkflowDraft(props.projectId, props.draftKey);
  const [chosen, setChosen] = useState<SavedWorkflowDraft>();
  const remote = saved.query.data;
  const conflict = Boolean(saved.local.operationId && remote && ((remote.draft && (remote.draft.operationBinding !== saved.local.operationBinding || remote.draft.operationId !== saved.local.operationId)) || (!remote.draft && remote.revision > 0)));
  function adopt(draft: SavedWorkflowDraft) {
    try { sessionStorage.setItem(props.draftKey, JSON.stringify(draft)); } catch { /* The service still retains this draft. */ }
    setChosen(draft);
  }
  useEffect(() => {
    if (remote && !saved.query.isFetching && chosen === undefined && !conflict) adopt(remote.draft || saved.local);
  }, [remote, saved.query.isFetching, conflict, chosen]);
  if (!remote || chosen === undefined) return <Modal title={props.workflow.name} onClose={props.onClose}>
    {saved.query.error ? <div role="alert"><p>{saved.query.error.message}</p><Button onClick={() => saved.query.refetch()}>Retry loading draft</Button></div> : conflict && !saved.query.isFetching ? <div className="form"><p>A different draft was saved or cleared in another session. Choose which version to continue.</p><Button onClick={() => adopt(remote?.draft || {})}>Use saved version</Button><Button tone="secondary" onClick={() => adopt({ ...saved.local, operationId: operationId(), operationBinding: undefined })}>Keep this browser’s draft</Button></div> : <p role="status">Restoring your workflow draft…</p>}
  </Modal>;
  return <PreparedWorkflowDialog {...props} initialWorkflowDraft={chosen} initialRemote={remote} />;
}

function PreparedWorkflowDialog({ projectId, workflow, defaultAgentId, defaultGoalId, defaultInput, onClose, onReloadDraft, initialWorkflowDraft, initialRemote }: WorkflowLaunchProps & { onReloadDraft: () => void; initialWorkflowDraft: SavedWorkflowDraft; initialRemote: DraftResponse }) {
  const navigate = useNavigate();
  const location = useLocation();
  const agents = useAgents(projectId);
  const confirmed = agents.data?.confirmed || [];
  const draftKey = workflowDraftKey(projectId, workflow.workflow, defaultAgentId, defaultGoalId, defaultInput);
  const initialDraft = useRef(initialWorkflowDraft);
  const execution = useExecutionSettings(projectId);
  const [profile, setProfile] = useState(initialDraft.current.profile || "");
  const [configureRunner, setConfigureRunner] = useState(false);
  const readiness = useRef<HTMLElement>(null);
  const [agentId, setAgentId] = useState(initialDraft.current.agentId ?? defaultAgentId ?? "");
  const goals = useGoals(projectId, agentId);
  const [goalId, setGoalId] = useState(initialDraft.current.goalId ?? defaultGoalId ?? "");
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
  const intent = { workflow: workflow.workflow, ...(agentId ? { agent_id: agentId } : {}), input, options: profile ? { profile } : {} };
  const intentKey = JSON.stringify(intent);
  const editableDraft = { agentId, goalId, problem, expected, helpDefine, traceId, profile } satisfies WorkflowDraft;
  const draftBinding = operationBinding({ intent, ...editableDraft });
  const operationDraft = useSessionOperation(draftKey, draftBinding, editableDraft);
  const durableDraft = useSaveWorkflowDraft(projectId, draftKey, initialRemote, { workflow: workflow.workflow, ...editableDraft, operationId: operationDraft.operation, operationBinding: draftBinding });
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
  const canStart = !configureRunner && !durableDraft.pending && !durableDraft.error && currentPreparation && (prepared?.state === "ready" || prepared?.state === "ready_with_limits");
  const mutation = useMutation({
    mutationFn: () => {
      if (!canStart) throw new Error("Finish setup and review the current requirements before starting.");
      return post<{ task_id: string }>(projectPath(projectId, "/tasks"), { ...prepared!.normalized_intent, operation_id: operation });
    },
    onSuccess: (task) => {
      const taskId = task.task_id;
      if (!taskId) throw new Error("The task did not return its identity. Retry with the same request.");
      void durableDraft.clear().catch(() => { /* Reusing the saved operation still returns this task. */ });
      operationDraft.clear();
      onClose();
      navigate(`/projects/${projectId}/tasks/${taskId}?view=running`);
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
  return <Modal title={workflow.name} onClose={() => { if (!mutation.isPending) onClose(); }} wide><form className="form prepared-action-form" onSubmit={(event) => { event.preventDefault(); if (!mutation.isPending) mutation.mutate(); }}><fieldset className="workflow-fields" disabled={mutation.isPending}><p className="modal-purpose">{workflow.purpose}</p>
    <section className="prepared-action-section"><div className="prepared-action-fields"><label>Agent<select value={agentId} onChange={(event) => { setAgentId(event.target.value); setGoalId(""); }} required={workflow.workflow !== "discover"}><option value="">Select an agent</option>{confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>{workflow.requires_goal && <label>Goal<select value={goalId} onChange={(event) => setGoalId(event.target.value)} required><option value="">Select a goal</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select></label>}{!defaultInput && ["fix", "discover"].includes(workflow.workflow) && <><label>Trace evidence<select value={traceId} onChange={event => setTraceId(event.target.value)} required={workflow.workflow === "discover"}><option value="">{workflow.workflow === "fix" ? "Describe a problem instead" : "Select retained trace evidence"}</option>{traces.data?.traces.map(trace => <option key={trace.id} value={trace.id}>{trace.name} · {trace.count} spans</option>)}</select></label>{workflow.workflow === "fix" && !traceId && <><label>What went wrong?<textarea value={problem} onChange={event => setProblem(event.target.value)} maxLength={1800} required /></label><label>What should happen instead?<textarea value={expected} onChange={event => setExpected(event.target.value)} maxLength={1800} required={!helpDefine} disabled={helpDefine} /></label><label className="checkbox-label"><input type="checkbox" checked={helpDefine} onChange={event => setHelpDefine(event.target.checked)} /> Help me define the expected behavior</label></>}</>}</div>{(input.type !== "description" || helpDefine) && <dl className="prepared-action-summary">{input.type !== "description" && <div><dt>Selected input</dt><dd>{inputLabel}</dd></div>}{helpDefine && <div><dt>Expected behavior</dt><dd>The task must ask before authoring a repair.</dd></div>}</dl>}</section>
    {(["fix", "eval", "baseline", "optimize"].includes(workflow.workflow) || configureRunner) && <section className="prepared-runner"><label>Execution profile<select value={profile} onChange={event => setProfile(event.target.value)}><option value="">{Object.keys(execution.data?.profiles || {}).length === 1 ? "Use the configured profile" : "Select a profile"}</option>{Object.keys(execution.data?.profiles || {}).map(name => <option key={name} value={name}>{name}</option>)}</select></label>{!configureRunner && <Button type="button" tone="quiet" onClick={() => setConfigureRunner(true)}>Configure runner</Button>}{execution.error && <p role="alert">{execution.error.message}</p>}{configureRunner && execution.data && <ExecutionProfileEditor projectId={projectId} settings={execution.data} onSaved={name => { setProfile(name); setConfigureRunner(false); }} onCancel={() => setConfigureRunner(false)} />}</section>}
    {(preparation.isLoading || !currentPreparation) && <p className="preparation-loading">Checking the current evidence and prerequisites…</p>}
    {preparation.error && <p className="error-banner">{preparation.error.message}</p>}
    {prepared && <>
      <section ref={readiness} className={`workflow-preparation preparation-${prepared.state}`}><header><div><p className="eyebrow">Start readiness</p><strong>{prepared.state === "ready" ? "Ready to start" : prepared.state === "ready_with_limits" ? "Ready with limits" : "Resolve the requirements below"}</strong></div><Status value={prepared.state} /></header>{blockers.length > 0 && <div className="preparation-block"><span>Required before start</span><ul>{blockers.map((item) => { const route = prerequisiteRoute(projectId, agentId, goalId, item.code); const reason = item.resolution?.context?.reason; return <li key={`${item.code}-${item.field || "task"}`}><div><strong>{preparationName(item.code)}</strong>{typeof reason === "string" && <small>{reason}</small>}</div>{item.code === "execution_profile" ? <Button type="button" tone="secondary" onClick={() => setConfigureRunner(true)}>Configure runner</Button> : item.code === "stale_preparation" ? <Button type="button" tone="secondary" onClick={() => preparation.refetch()}>Review current inputs</Button> : route ? <Link className="button button-secondary" to={`${route}?return=${encodeURIComponent(location.pathname + location.search)}`} state={{ workflowReturn: { to: location.pathname + location.search, workflow: workflow.workflow, defaultAgentId, defaultGoalId, defaultInput } }} onClick={event => { if (mutation.isPending) event.preventDefault(); else onClose(); }}>Resolve requirement</Link> : <small>Complete this in the fields above.</small>}</li>; })}</ul></div>}<div className="preparation-refresh"><Button type="button" tone="quiet" onClick={() => preparation.refetch()} disabled={preparation.isFetching}>↻ {preparation.isFetching ? "Checking…" : "Check again"}</Button><span>{Object.keys(prepared.revisions).length} frozen input revision{Object.keys(prepared.revisions).length === 1 ? "" : "s"}; checked again at start.</span></div></section>
      <details className="prepared-action-section"><summary>Scope and evidence</summary><dl className="prepared-action-summary"><div><dt>Agent</dt><dd>{selectedAgent?.name || (workflow.workflow === "discover" ? "Determined from selected evidence" : "Not selected")}</dd></div><div><dt>Permitted code</dt><dd>{prepared.normalized_intent.scope?.length ? prepared.normalized_intent.scope.map((path) => <code key={path}>{path}</code>) : selectedAgent?.code_scopes?.length ? selectedAgent.code_scopes.map((path) => <code key={path}>{path}</code>) : "Read-only or unresolved"}</dd></div><div><dt>Source</dt><dd>{prepared.code_source?.kind === "git" ? `${prepared.code_source.revision?.slice(0, 12) || "No commit"}${prepared.code_source.dirty ? " · uncommitted changes" : " · clean"}` : prepared.code_source?.kind === "folder" ? "Local non-Git folder" : "No code source required"}</dd></div><div><dt>Evidence</dt><dd>{prepared.source_identity ? `${String(prepared.source_identity.kind || "source")} ${String(prepared.source_identity.id || "")}` : inputLabel}</dd></div></dl></details>
      <details className="prepared-action-section"><summary>Verification requirements</summary>{verification.length ? <ul className="prepared-check-list">{verification.map((item) => <li key={item.code} className={item.blocking ? "is-blocked" : "is-ready"}><span>{item.blocking ? "Required" : "Ready"}</span><strong>{preparationName(item.code)}</strong><small>{item.evidence_refs.length ? `${item.evidence_refs.length} retained reference${item.evidence_refs.length === 1 ? "" : "s"}` : "No retained reference yet"}</small></li>)}</ul> : <p className="quiet-surface">This workflow records evidence under its built-in verification contract.</p>}<p className="prepared-outputs">Expected result: {prepared.expected_outputs.map((item) => item.replaceAll("_", " ")).join(" · ")}</p></details>
      <section className="prepared-action-section"><h3>Execution limits</h3><dl className="prepared-action-summary"><div><dt>Time</dt><dd>{Math.round((prepared.normalized_intent.limits?.max_elapsed_seconds || 0) / 60)} minutes maximum</dd></div><div><dt>Attempts</dt><dd>{prepared.normalized_intent.limits?.max_trials || 0} maximum</dd></div><div><dt>Per attempt</dt><dd>{prepared.normalized_intent.limits?.trial_timeout_seconds || 0} seconds</dd></div><div><dt>Coding backend</dt><dd>{String(backend?.id || prepared.normalized_intent.assistant || "Project default")}{backend?.model ? ` · ${String(backend.model)}` : ""}</dd></div></dl>{limitations.length > 0 && <div className="preparation-limits"><strong>Known limits</strong><ul>{limitations.map((item) => <li key={`${item.code}-${item.field || "task"}`}>{preparationName(item.code)}</li>)}</ul></div>}</section>
    </>}
    {durableDraft.error ? <div className="error-banner" role="alert"><p>Draft not saved: {durableDraft.error.replace(/[.]+$/, "")}. Your text remains in this browser.</p><Button type="button" tone="secondary" onClick={durableDraft.retry}>Retry saving</Button><Button type="button" tone="quiet" onClick={onReloadDraft}>Reload saved draft</Button></div> : <p className="quiet-copy" role="status">{durableDraft.pending ? "Saving draft…" : "Draft saved to this project"}</p>}
    {mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions">{blockers.length > 0 && <Button type="button" tone="quiet" className="readiness-shortcut" onClick={() => readiness.current?.scrollIntoView({ block: "start" })}>{blockers.length} requirement{blockers.length === 1 ? "" : "s"} before start</Button>}<Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={!canStart || mutation.isPending}>{mutation.isPending ? "Starting…" : startLabel[workflow.workflow] || "Start"}</Button></footer></fieldset></form></Modal>;
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
