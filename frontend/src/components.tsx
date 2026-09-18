import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";

import { api, operationId, post, projectPath } from "./api";
import { useAgents, useGoals, useTask } from "./hooks";
import type { Agent, Goal, Project, SkillDefinition, WorkflowReadiness } from "./types";

type IconName = "home" | "tasks" | "agent" | "skill" | "connector" | "settings" | "plus" | "close" | "arrow" | "moon" | "sun" | "menu";

const iconPaths: Record<IconName, ReactNode> = {
  home: <><path d="M3 10.8 12 3l9 7.8" /><path d="M5.5 9.8V21h13V9.8M9 21v-7h6v7" /></>,
  tasks: <><path d="M7 4h10M7 12h10M7 20h10" /><path d="m3 4 .7.7L5 3.3M3 12l.7.7L5 11.3M3 20l.7.7L5 19.3" /></>,
  agent: <><path d="M12 3 4.5 7.2v9.6L12 21l7.5-4.2V7.2L12 3Z" /><circle cx="12" cy="12" r="3" /></>,
  skill: <><path d="M8 3h8l1 5 4 3-4 3-1 7H8l-1-7-4-3 4-3 1-5Z" /><path d="m9.5 12 1.7 1.7 3.6-4" /></>,
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
  const tone = ["completed", "connected", "accepted", "ready", "pass"].includes(value) ? "good" : ["failed", "cancelled", "blocked"].includes(value) ? "bad" : ["running", "queued"].includes(value) ? "active" : "neutral";
  return <span className={`status status-${tone}`}><span aria-hidden="true" />{value.replaceAll("_", " ")}</span>;
}

export function Modal({ title, eyebrow, children, onClose, wide = false }: { title: string; eyebrow?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
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

export function Sidebar({ projects, project, agents, open, onClose, onAddProject }: { projects: Project[]; project: Project; agents: Agent[]; open: boolean; onClose: () => void; onAddProject: () => void }) {
  const navigate = useNavigate();
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
      <NavLink to={`${projectBase}/home`} className={linkClass}><Icon name="home" />Home</NavLink>
      <NavLink to={`${projectBase}/tasks`} className={linkClass}><Icon name="tasks" />Tasks</NavLink>
    </nav>
    <div className="nav-section"><div className="nav-heading"><NavLink to={`${projectBase}/agents`} onClick={onClose}>Agents</NavLink></div>
      <nav className="agent-links" onClick={onClose}>{agents.map((agent) => <NavLink key={agent.id} to={`${projectBase}/agents/${agent.id}/overview`} className={linkClass}><span className="agent-glyph" aria-hidden="true">A</span><span>{agent.name}</span></NavLink>)}{!agents.length && <NavLink className="nav-empty" to={`${projectBase}/agents`}>Discover agents</NavLink>}</nav>
    </div>
    <nav className="primary-nav sidebar-resources" onClick={onClose}>
      <NavLink to={`${projectBase}/skills`} className={linkClass}><Icon name="skill" />Skills</NavLink>
      <NavLink to={`${projectBase}/connectors`} className={linkClass}><Icon name="connector" />Connectors</NavLink>
    </nav>
    <div className="sidebar-bottom"><NavLink to={`${projectBase}/settings/assistants`} className={linkClass} onClick={onClose}><Icon name="settings" />Settings</NavLink><p className="local"><span />Local workspace</p></div>
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
  return <header className="topbar"><div className="topbar-project"><Button tone="quiet" className="mobile-only" aria-label="Open navigation" onClick={onMenu}><Icon name="menu" /></Button><span className="scope-dot" /><span>{project.name}</span>{project.branch && <span className="branch">{project.branch}</span>}</div><div className="topbar-actions"><Link className="activity-link" to={`/projects/${project.id}/tasks`}>Needs attention <strong>{taskCount}</strong></Link><Button tone="quiet" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={toggle}><Icon name={dark ? "sun" : "moon"} /></Button></div></header>;
}

export function TaskDetail({ projectId, taskId, onBack }: { projectId: string; taskId: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const task = useTask(projectId, taskId);
  const [guidance, setGuidance] = useState("");
  const [input, setInput] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] });
  const control = useMutation({ mutationFn: ({ action, body }: { action: string; body: Record<string, unknown> }) => post(projectPath(projectId, `/tasks/${taskId}/${action}`), { operation_id: operationId(), ...body }), onSuccess: refresh });
  const send = useMutation({ mutationFn: (message: string) => post(projectPath(projectId, `/tasks/${taskId}/message`), { operation_id: operationId(), message }), onSuccess: async () => { setGuidance(""); await refresh(); } });
  useEffect(() => setInput(""), [task.data?.question?.id]);
  const submitGuidance = () => {
    const message = guidance.trim();
    if (message && !send.isPending) send.mutate(message);
  };
  const question = task.data?.question;
  const approval = question && ["approval", "intelligence", "trace_access"].includes(question.kind || "");
  const reply = (answer: Record<string, unknown>) => question && control.mutate({ action: "reply", body: { question_id: question.id, answer } });
  return <section className="task-detail" aria-label="Task details">
    <header className="task-detail-header"><div><p className="eyebrow">Task</p><h1>{task.data?.title || "Loading task…"}</h1></div><Button tone="secondary" onClick={onBack}>Back to tasks</Button></header>
    {task.isError && <p className="error-banner">{task.error.message}</p>}
    {task.data && <><div className="task-meta"><Status value={task.data.state} /><span>{task.data.agent_name || "Agent"}</span>{task.data.goal_name && <span>{task.data.goal_name}</span>}</div>
      <div className="conversation" aria-live="polite">
        {task.data.conversation.map((message, index) => <article className={`message message-${message.role || "assistant"}`} key={index}><span>{message.role === "user" ? "You" : "Coding assistant"}</span><p>{message.text || message.content}</p></article>)}
        {!task.data.conversation.length && <p className="quiet-copy">Waiting for an update.</p>}
      </div>
      {approval && <section className="decision" aria-label="Approval needed"><h3>{question.kind === "intelligence" ? "AG Intelligence request" : question.kind === "trace_access" ? "Trace access" : "Approval needed"}</h3><p>{question.text || question.prompt}</p>{question.kind === "intelligence" && <div className="approval-payload"><dl><div><dt>Destination</dt><dd>{question.endpoint}</dd></div><div><dt>Workflow</dt><dd>{question.workflow}</dd></div></dl><pre>{JSON.stringify(question.request, null, 2)}</pre></div>}{question.kind === "trace_access" && <div className="approval-payload"><pre>{JSON.stringify(question.request, null, 2)}</pre></div>}<div className="task-controls"><Button disabled={control.isPending} onClick={() => reply({ decision: "accept" })}>Approve</Button><Button tone="secondary" disabled={control.isPending} onClick={() => reply({ decision: "decline" })}>Decline</Button></div></section>}
      {question && !approval && <form className="decision input-request" aria-label="Input needed" onSubmit={(event) => { event.preventDefault(); if (input.trim()) reply({ text: input.trim() }); }}><h3>Input needed</h3><p>{question.text || question.prompt}</p>{question.options?.length ? <select aria-label="Response" value={input} onChange={(event) => setInput(event.target.value)} required><option value="">Choose an option</option>{question.options.map((option) => <option key={option}>{option}</option>)}</select> : <textarea aria-label="Response" value={input} onChange={(event) => setInput(event.target.value)} rows={3} maxLength={4000} required />}<div className="task-controls"><Button type="submit" disabled={!input.trim() || control.isPending}>Continue</Button></div></form>}
      {!question && task.data.next_action && ["failed", "interrupted", "needs_input"].includes(task.data.state) && <p className="next-action">{task.data.next_action}</p>}
      {task.data.result && <details className="tool-detail"><summary>Result details</summary><pre>{JSON.stringify(task.data.result, null, 2)}</pre></details>}
      {(control.error || send.error) && <p className="error-banner">{(control.error || send.error)?.message}</p>}
      <footer className="task-footer">
        {task.data.can_message && <form className="task-composer" aria-label="Steer task" onSubmit={(event) => { event.preventDefault(); submitGuidance(); }}><textarea aria-label="Message" value={guidance} onChange={(event) => setGuidance(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submitGuidance(); } }} placeholder="Add guidance…" rows={2} maxLength={4000} /><Button type="submit" disabled={!guidance.trim() || send.isPending}>{send.isPending ? "Sending…" : "Send"}</Button></form>}
        {task.data.can_cancel && <Button tone="quiet" disabled={control.isPending} onClick={() => control.mutate({ action: "cancel", body: {} })}>Cancel task</Button>}
        {task.data.can_resume && <Button tone="secondary" disabled={control.isPending} onClick={() => control.mutate({ action: "resume", body: {} })}>Resume</Button>}
      </footer>
    </>}
  </section>;
}

export function AddProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const mutation = useMutation({ mutationFn: () => post<Project>("/api/projects", { path }), onSuccess: async (project) => { await queryClient.invalidateQueries({ queryKey: ["projects"] }); onClose(); navigate(`/projects/${project.id}/home`); } });
  return <Modal title="Add a project" eyebrow="Local checkout" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Project directory<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Users/you/code/my-agent" required autoFocus /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>Add project</Button></footer></form></Modal>;
}

export function AddAgentModal({ projectId, suggestion, onClose }: { projectId: string; suggestion?: Agent; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(suggestion?.name || "");
  const [description, setDescription] = useState(suggestion?.responsibility || suggestion?.description || "");
  const [paths, setPaths] = useState(suggestion?.code_scopes.join(", ") || "");
  const mutation = useMutation({ mutationFn: () => post<Agent>(projectPath(projectId, suggestion ? `/agents/${suggestion.id}` : "/agents"), { name, description, code_scopes: paths.split(",").map((item) => item.trim()).filter(Boolean), shared_dependencies: suggestion?.shared_dependencies || [], trace_selector: suggestion?.trace_selector || {}, status: "confirmed", ...(suggestion?.revision ? { expected_revision: suggestion.revision } : {}) }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents"] }); onClose(); } });
  return <Modal title={suggestion ? "Confirm agent" : "Add agent"} eyebrow="Agent configuration" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Agent name<input value={name} onChange={(event) => setName(event.target.value)} required autoFocus /></label><label>Responsibility<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label><label>Code paths<input value={paths} onChange={(event) => setPaths(event.target.value)} placeholder="src/agent.py, src/tools" required /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{suggestion ? "Confirm agent" : "Add agent"}</Button></footer></form></Modal>;
}

const idealBehaviorByCategory: Record<string, string> = {
  correctness: "The agent completes the requested task correctly and produces the expected result.",
  reliability: "The agent completes the task consistently, uses the right tools, and recovers safely from failures.",
  grounding: "The agent's response is accurate, supported by available evidence, and does not invent facts.",
  safety: "The agent follows applicable policies, protects sensitive data, and refuses unsafe actions.",
  latency: "The agent completes the task within the expected response time without reducing quality.",
  cost: "The agent completes the task with the minimum necessary model and tool usage without reducing quality.",
};

export function AddGoalModal({ projectId, agentId, onClose }: { projectId: string; agentId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [category, setCategory] = useState("correctness");
  const [name, setName] = useState("");
  const [ideal, setIdeal] = useState(idealBehaviorByCategory.correctness);
  const selectCategory = (next: string) => {
    setCategory(next);
    setIdeal(idealBehaviorByCategory[next] || "");
  };
  const mutation = useMutation({ mutationFn: () => post<Goal>(projectPath(projectId, `/agents/${agentId}/goals`), { category, ...(category === "custom" ? { name } : {}), ideal_behavior: ideal }), onSuccess: async (goal) => { await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals"] }); onClose(); navigate(`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`); } });
  return <Modal title="Create a goal" eyebrow="Outcome to improve" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label>Improve<select value={category} onChange={(event) => selectCategory(event.target.value)}><option value="correctness">Task success and correctness</option><option value="reliability">Reliability and tool use</option><option value="grounding">Grounding and factuality</option><option value="safety">Safety and policy</option><option value="latency">Latency</option><option value="cost">Cost and efficiency</option><option value="custom">Custom</option></select></label>{category === "custom" && <label>Goal name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>}<label>Ideal behavior<textarea value={ideal} onChange={(event) => setIdeal(event.target.value)} rows={4} required autoFocus /></label>{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>Create goal</Button></footer></form></Modal>;
}

export function LaunchWorkflowModal({ projectId, skill, defaultAgentId, defaultGoalId, onClose }: { projectId: string; skill: SkillDefinition; defaultAgentId?: string; defaultGoalId?: string; onClose: () => void }) {
  const navigate = useNavigate();
  const agents = useAgents(projectId);
  const confirmed = agents.data?.confirmed || [];
  const [agentId, setAgentId] = useState(defaultAgentId || "");
  const goals = useGoals(projectId, agentId);
  const [goalId, setGoalId] = useState(defaultGoalId || "");
  useEffect(() => { if (!agentId && confirmed.length === 1) setAgentId(confirmed[0].id); }, [agentId, confirmed]);
  useEffect(() => { if (skill.requires_goal && !goalId && goals.data?.goals.length === 1) setGoalId(goals.data.goals[0].id); }, [goalId, goals.data, skill.requires_goal]);
  const params = new URLSearchParams({ ...(agentId ? { agent_id: agentId } : {}), ...(goalId ? { goal_id: goalId } : {}) });
  const readiness = useQuery({ queryKey: ["projects", projectId, "workflows", skill.workflow, "readiness", agentId, goalId], queryFn: () => api<WorkflowReadiness>(projectPath(projectId, `/workflows/${skill.workflow}/readiness?${params}`)) });
  const mutation = useMutation({ mutationFn: () => post<{ id: string }>(projectPath(projectId, "/tasks"), { operation_id: operationId(), workflow: skill.workflow, agent_id: agentId, ...(goalId ? { goal_id: goalId } : {}), options: {} }), onSuccess: (task) => { onClose(); navigate(`/projects/${projectId}/tasks/${task.id}`); } });
  return <Modal title={skill.name} eyebrow="Start workflow" onClose={onClose}><form className="form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><p className="modal-purpose">{skill.purpose}</p><label>Agent<select value={agentId} onChange={(event) => { setAgentId(event.target.value); setGoalId(""); }} required><option value="">Select an agent</option>{confirmed.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>{skill.requires_goal && <label>Goal<select value={goalId} onChange={(event) => setGoalId(event.target.value)} required><option value="">Select a goal</option>{goals.data?.goals.map((goal) => <option key={goal.id} value={goal.id}>{goal.name}</option>)}</select></label>}{readiness.data && !readiness.data.ready && <div className="blockers">{readiness.data.blockers.map((blocker) => <p key={blocker.code}>{blocker.message}</p>)}</div>}{mutation.error && <p className="error-banner">{mutation.error.message}</p>}<footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={!readiness.data?.ready || mutation.isPending}>Start task</Button></footer></form></Modal>;
}

export function AgentGoalNames({ projectId, agentId, goalId }: { projectId: string; agentId?: string | null; goalId?: string | null }) {
  const agents = useAgents(projectId);
  const goals = useGoals(projectId, agentId || undefined);
  return <>{agents.data?.agents.find((item) => item.id === agentId)?.name || "Unknown agent"}{goalId && <> · {goals.data?.goals.find((item) => item.id === goalId)?.name || "Unknown goal"}</>}</>;
}
