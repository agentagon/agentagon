import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, post, projectPath } from "./api";
import { Button, Modal } from "./components";
import { useAgents, useGoals } from "./hooks";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Goal, MeasurementPlan } from "./types";

type Cases = { dataset_snapshot_id: string; case_count: number; revision: number; limitations: string[] };
type CaseDesign = { draft: MeasurementPlan | null; attached_cases: Cases | null };
type CaseSelection = { agentId: string; goalId: string; name: string; refresh: number };
type PendingAttachment = { binding: string; agentId: string; goalId: string; payload: Record<string, unknown>; selection: CaseSelection };

export function AttachEvaluationCaseModal({ projectId, snapshotId, caseName, onClose }: { projectId: string; snapshotId: string; caseName: string; onClose: () => void }) {
  const operationKey = `agentagon.case-attachment:${projectId}:${snapshotId}`;
  const creationKey = `agentagon.case-goal:${projectId}:${snapshotId}`;
  const requestKey = `agentagon.case-request:${projectId}:${snapshotId}`;
  const [initial] = useState(() => {
    const saved = loadOperationDraft<CaseSelection>(operationKey);
    const pending = loadOperationDraft<{ request?: PendingAttachment }>(requestKey).request;
    const created = loadOperationDraft<{ agentId: string; name: string }>(creationKey);
    return { selection: saved.operationId && !saved.draftOmitted ? saved : pending?.selection || created, pending };
  });
  const agents = useAgents(projectId);
  const [agentId, setAgentId] = useState(initial.selection.agentId || "");
  const [goalId, setGoalId] = useState("goalId" in initial.selection ? initial.selection.goalId || "create" : "create");
  const [name, setName] = useState(initial.selection.name ?? caseName.slice(0, 160));
  const [refresh, setRefresh] = useState("refresh" in initial.selection ? initial.selection.refresh || 0 : 0);
  const goals = useGoals(projectId, agentId);
  const cache = useQueryClient();
  const navigate = useNavigate();
  const selectedDesign = useQuery({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId, "design"], queryFn: () => api<CaseDesign>(projectPath(projectId, `/agents/${agentId}/goals/${goalId}/design`)), enabled: Boolean(agentId && goalId !== "create") });
  useEffect(() => { if (!agentId && agents.data?.confirmed.length === 1) setAgentId(agents.data.confirmed[0].id); }, [agentId, agents.data]);
  const binding = operationBinding({ agentId, goalId, name, snapshotId, refresh });
  const selection = { agentId, goalId, name, refresh };
  const operation = useSessionOperation(operationKey, binding, selection);
  const creation = useSessionOperation(creationKey, operationBinding({ agentId, name, caseName, snapshotId }), { agentId, name });
  const pendingRequest = useRef<PendingAttachment | undefined>(initial.pending);
  const attach = useMutation({
    mutationFn: async (submitted: CaseSelection & { binding: string; creationOperation: string; attachmentOperation: string }) => {
      if (!submitted.agentId) throw new Error("Choose the agent that owns this behavior.");
      if (!pendingRequest.current || pendingRequest.current.binding !== submitted.binding) {
        let selectedGoal = submitted.goalId;
        if (selectedGoal === "create") {
          const goal = await post<Goal>(projectPath(projectId, `/agents/${submitted.agentId}/goals`), { category: "correctness", name: submitted.name, objective: `Protect the reviewed behavior: ${caseName}`, operation_id: submitted.creationOperation });
          selectedGoal = goal.id;
        }
        const design = await api<CaseDesign>(projectPath(projectId, `/agents/${submitted.agentId}/goals/${selectedGoal}/design`));
        pendingRequest.current = { binding: submitted.binding, agentId: submitted.agentId, goalId: selectedGoal, selection: { agentId: submitted.agentId, goalId: submitted.goalId, name: submitted.name, refresh: submitted.refresh }, payload: { dataset_snapshot_id: snapshotId, operation_id: submitted.attachmentOperation, expected_revision: design.draft?.revision || 0, expected_cases_revision: design.attached_cases?.revision || 0 } };
        // Preserve the exact revisions before sending: a lost response must replay
        // this request after reopening, rather than rebind its operation ID.
        try { sessionStorage.setItem(requestKey, JSON.stringify({ request: pendingRequest.current })); } catch { /* Same-session retries still retain the request in memory. */ }
      }
      const request = pendingRequest.current;
      await post(projectPath(projectId, `/agents/${request.agentId}/goals/${request.goalId}/design/cases`), request.payload);
      return { agentId: request.agentId, goalId: request.goalId };
    },
    onSuccess: async result => { try { operation.clear(); creation.clear(); sessionStorage.removeItem(requestKey); } catch { /* The successful server receipt remains authoritative. */ } await cache.invalidateQueries({ queryKey: ["projects", projectId] }); onClose(); navigate(`/projects/${projectId}/agents/${result.agentId}/goals/${result.goalId}?cases=saved`); },
  });
  const noAgents = !agents.isLoading && !agents.error && !agents.data?.confirmed.length;
  const close = () => { if (!attach.isPending) onClose(); };
  return <Modal title="Add case to evaluation" onClose={close}><form className="form" onSubmit={event => { event.preventDefault(); if (!attach.isPending) attach.mutate({ ...selection, binding, creationOperation: creation.operation, attachmentOperation: operation.operation }); }}>
    <p>{caseName}</p>
    <label>Agent<select value={agentId} onChange={event => { setAgentId(event.target.value); setGoalId("create"); }} required disabled={attach.isPending}><option value="">Choose agent</option>{agents.data?.confirmed.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
    {noAgents && <p className="quiet-surface">Detect or add an agent first. This case stays saved. <Link to={`/projects/${projectId}/agents`} onClick={close}>Open agents</Link></p>}
    {agentId && <label>Evaluation<select value={goalId} onChange={event => setGoalId(event.target.value)} disabled={attach.isPending}><option value="create">Create evaluation with this case</option>{goals.data?.goals.map(goal => <option key={goal.id} value={goal.id}>{goal.name} · {goal.objective}</option>)}</select></label>}
    {goalId === "create" && <label>Evaluation name<input value={name} onChange={event => setName(event.target.value)} maxLength={160} required disabled={attach.isPending} /></label>}
    <p className="quiet-copy">Keeps existing cases and required checks. An accepted or frozen evaluator gets a new draft; its retained results stay unchanged. No evaluation runs.</p>
    {selectedDesign.isFetching && goalId !== "create" && <p role="status">Loading the selected draft…</p>}
    {(agents.error || goals.error || selectedDesign.error || attach.error) && <div className="error-banner" role="alert"><p>{(attach.error || selectedDesign.error || goals.error || agents.error)?.message}</p>{attach.error && <Button type="button" tone="secondary" disabled={attach.isPending} onClick={() => { pendingRequest.current = undefined; try { sessionStorage.removeItem(requestKey); } catch { /* The refreshed operation still has a new binding. */ } setRefresh(value => value + 1); if (goalId !== "create") void selectedDesign.refetch(); attach.reset(); }}>Reload evaluation</Button>}</div>}
    <footer className="form-actions"><Button type="button" tone="secondary" onClick={close} disabled={attach.isPending}>Cancel</Button><Button type="submit" disabled={!agentId || attach.isPending || (goalId !== "create" && (!selectedDesign.data || selectedDesign.isFetching))}>{attach.isPending ? "Attaching…" : goalId === "create" ? "Create evaluation with this case" : "Add to evaluation"}</Button></footer>
  </form></Modal>;
}

export function EvaluationCases({ projectId, agentId, goalId }: { projectId: string; agentId: string; goalId: string }) {
  const query = useQuery({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId, "design"], queryFn: () => api<CaseDesign>(projectPath(projectId, `/agents/${agentId}/goals/${goalId}/design`)) });
  if (query.error) return <p className="error-banner" role="alert">Cases could not load: {query.error.message}</p>;
  const cases = query.data?.attached_cases;
  if (!cases) return null;
  return <section className="settings-card evaluation-cases"><h3>{cases.case_count} retained case{cases.case_count === 1 ? "" : "s"}</h3><p>The reviewed inputs and expectations are attached to this evaluation draft.</p>{!query.data?.draft && <p className="quiet-copy">Go prepares an evaluator using these cases.</p>}<details><summary>Case evidence</summary><p><code>{cases.dataset_snapshot_id}</code></p>{cases.limitations?.map(item => <p key={item}>{item}</p>)}</details></section>;
}
