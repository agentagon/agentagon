import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { AddGoalModal, Button, Icon, LaunchWorkflowModal, Modal } from "./components";
import { useAgents, useGoals, useWorkflows } from "./hooks";
import type { Goal } from "./types";

export type NewWorkAction = "fix" | "improve";

export function NewWorkMenu({ projectId, agentId, agentName, onAction }: { projectId: string; agentId?: string; agentName?: string; onAction: (action: NewWorkAction) => void }) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const location = useLocation();
  useEffect(() => { setOpen(false); }, [location.pathname, location.search]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => { if (!panel.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); } };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", dismiss); window.removeEventListener("keydown", escape); };
  }, [open]);
  const act = (action: NewWorkAction) => { setOpen(false); trigger.current?.focus(); onAction(action); };
  return <div className="new-work" ref={panel}>
    <button ref={trigger} className="button button-primary" type="button" aria-expanded={open} aria-controls="new-work-options" onClick={() => setOpen(!open)}><Icon name="plus" size={15} />New work</button>
    {open && <div className="new-work-options" id="new-work-options"><p>{agentName ? `For ${agentName}` : "In this project"}</p><nav aria-label="Start new work"><button type="button" onClick={() => act("fix")}>Fix a problem</button><button type="button" onClick={() => act("improve")}>Improve behavior</button><Link to={`/projects/${projectId}/onboarding`}>Detect agents</Link><Link to={`/projects/${projectId}/issues${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`}>Inspect trace</Link></nav></div>}
  </div>;
}

export function NewWorkFlow({ projectId, agentId, action, onClose }: { projectId: string; agentId?: string; action: NewWorkAction; onClose: () => void }) {
  const trigger = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => () => { if (trigger.current?.isConnected) trigger.current.focus(); }, []);
  const workflows = useWorkflows();
  if (action === "improve") return <ImproveBehaviorDialog projectId={projectId} defaultAgentId={agentId} onClose={onClose} />;
  const workflow = workflows.data?.workflows.find(item => item.workflow === "fix");
  if (workflow) return <LaunchWorkflowModal projectId={projectId} workflow={workflow} defaultAgentId={agentId} onClose={onClose} />;
  return <Modal title="Fix a problem" onClose={onClose}>{workflows.isLoading ? <p role="status">Loading the fix workflow…</p> : <><p role="alert">{workflows.error?.message || "The fix workflow is unavailable."}</p><Button onClick={() => workflows.refetch()}>Retry</Button></>}</Modal>;
}

function ImproveBehaviorDialog({ projectId, defaultAgentId, onClose }: { projectId: string; defaultAgentId?: string; onClose: () => void }) {
  const agents = useAgents(projectId);
  const confirmed = agents.data?.confirmed || [];
  const [agentId, setAgentId] = useState(defaultAgentId || "");
  const [create, setCreate] = useState(false);
  const goals = useGoals(projectId, agentId);
  const navigate = useNavigate();
  useEffect(() => { if (!agentId && confirmed.length === 1) setAgentId(confirmed[0].id); }, [agentId, confirmed]);
  if (create && agentId) return <AddGoalModal projectId={projectId} agentId={agentId} onClose={onClose} />;
  const openGoal = (goal: Goal) => { onClose(); navigate(`/projects/${projectId}/agents/${agentId}/goals/${goal.id}`); };
  return <Modal title="Improve behavior" onClose={onClose}><div className="form">
    {agents.isLoading ? <p role="status">Loading agents…</p> : agents.error ? <p role="alert">{agents.error.message}<Button onClick={() => agents.refetch()}>Retry</Button></p> : !confirmed.length ? <p>Detect agents to get started. <Link to={`/projects/${projectId}/agents`} onClick={onClose}>Open agents</Link></p> : <>
      <label>Agent<select value={agentId} onChange={event => setAgentId(event.target.value)}><option value="">Choose an agent</option>{confirmed.map(agent => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select></label>
      {agentId && (goals.isLoading ? <p role="status">Loading goals…</p> : goals.error ? <p role="alert">{goals.error.message}<Button onClick={() => goals.refetch()}>Retry</Button></p> : <>
        {goals.data?.goals.length ? <div className="new-work-goals"><p className="quiet-copy">Choose a behavior to continue.</p>{goals.data.goals.map(goal => <button type="button" className="list-row" key={goal.id} onClick={() => openGoal(goal)}><span><strong>{goal.name}</strong><small>{goal.objective}</small></span><span>Open →</span></button>)}</div> : <p className="quiet-copy">Describe what you want this agent to do better.</p>}
        <Button onClick={() => setCreate(true)}>New goal</Button>
      </>)}
    </>}
  </div></Modal>;
}
