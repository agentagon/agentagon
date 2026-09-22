import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, operationId, post, projectPath } from "./api";
import { Button, Status } from "./components";
import { ExecutionProfileEditor, useExecutionSettings } from "./execution";
import { useTask } from "./hooks";
import { loadOperationDraft, operationBinding, useSessionOperation } from "./session";
import type { Goal } from "./types";

export type GoalRunStart = { operation_id: string; details?: string; max_elapsed_seconds?: number; max_trials?: number; profile?: string };
export type GoalRun = {
  id: string; project_id: string; agent_id: string; goal_id: string;
  state: "running" | "needs_input" | "paused" | "interrupted" | "failed" | "completed" | "cancelled";
  stage: "design" | "eval" | "baseline" | "optimize";
  active_task_id?: string | null; task_ids: string[]; summary: string; details?: string;
  requirements: Array<{ code: string; message?: string; reason?: string; blocking: boolean; resolution?: { action: string; context?: Record<string, unknown> }; evidence_refs?: unknown[] }>;
  outcome?: { kind: string; id: string } | null;
  accounting: { active_seconds: number; unknown_seconds?: number; remaining_seconds: number; trials_reserved?: number; trials_remaining?: number };
  limits?: { max_elapsed_seconds: number; max_trials: number };
  resolved?: { profile?: string; created_local_profile?: boolean };
  focus?: { agent_id: string; goal_id: string; name: string };
  available_actions?: string[]; revision: number;
};

export function startGoalRun(projectId: string, agentId: string, goalId: string, body: GoalRunStart) {
  return post<GoalRun>(projectPath(projectId, `/agents/${agentId}/goals/${goalId}/runs`), body);
}

const runningStates = new Set(["running", "needs_input", "paused", "interrupted"]);
const currentWork: Record<string, string> = { design: "Working out how to measure this goal", eval: "Preparing the evaluation", baseline: "Measuring the current behavior", optimize: "Finding and checking improvements" };

export function GoalRunWorkspace({ projectId, agentId, goal, editing = false, onActiveChange }: { projectId: string; agentId: string; goal: Goal; editing?: boolean; onActiveChange?: (active: boolean) => void }) {
  const cache = useQueryClient();
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const queryKey = ["projects", projectId, "agents", agentId, "goals", goal.id, "runs"];
  const runs = useQuery({ queryKey, queryFn: () => api<{ runs: GoalRun[] }>(projectPath(projectId, `/agents/${agentId}/goals/${goal.id}/runs`)), refetchInterval: query => query.state.data?.runs.some(item => runningStates.has(item.state)) ? 3000 : false });
  const selectedRun = search.get("run");
  const recent = runs.data?.runs[0];
  const runId = recent && runningStates.has(recent.state) ? recent.id : selectedRun || recent?.id;
  const run = useQuery({ queryKey: ["projects", projectId, "goal-runs", runId], queryFn: () => api<GoalRun>(projectPath(projectId, `/goal-runs/${runId}`)), enabled: Boolean(runId), refetchInterval: query => runningStates.has(query.state.data?.state || "") ? 2000 : false });
  const candidate = run.data || (recent?.id === runId ? recent : undefined);
  const wrongGoal = Boolean(candidate && (candidate.project_id !== projectId || candidate.agent_id !== agentId || candidate.goal_id !== goal.id));
  const current = wrongGoal ? undefined : candidate;
  const statusLoading = runs.isLoading || Boolean(runId && !candidate && !run.error);
  const statusUnavailable = statusLoading || wrongGoal || Boolean(runs.error || run.error);
  const task = useTask(projectId, current?.active_task_id || undefined);
  const execution = useExecutionSettings(projectId);
  const draftKey = `agentagon.goal-run:${projectId}:${agentId}:${goal.id}`;
  const [initial] = useState(() => loadOperationDraft<{ details: string; detailsEdited: boolean; profile: string; minutes: string; generation: number; lastTerminalRunId: string }>(draftKey));
  const [details, setDetails] = useState(initial.details || "");
  const [detailsEdited, setDetailsEdited] = useState(Boolean(initial.detailsEdited || initial.details));
  const hydratedDetails = useRef(false);
  useEffect(() => {
    if (!current || detailsEdited || hydratedDetails.current) return;
    hydratedDetails.current = true;
    setDetails(current.details || "");
  }, [current?.id, current?.details, detailsEdited]);
  const [profile, setProfile] = useState(initial.profile || "");
  const [minutes, setMinutes] = useState(initial.minutes || "");
  const [generation, setGeneration] = useState(initial.generation || 0);
  const [lastTerminalRunId, setLastTerminalRunId] = useState(initial.lastTerminalRunId || "");
  useEffect(() => {
    // A run discovered after a lost response is still a completed admission.
    // Only a newly observed terminal run rotates the next deliberate attempt;
    // retrying a failed transport against the same prior run retains its ID.
    if (current && !runningStates.has(current.state)) setLastTerminalRunId(current.id);
  }, [current?.id, current?.state]);
  const [advanced, setAdvanced] = useState(false);
  const [configureRunner, setConfigureRunner] = useState(false);
  const intent = { ...(details.trim() ? { details: details.trim() } : {}), ...(profile ? { profile } : {}), ...(minutes ? { max_elapsed_seconds: Math.round(Number(minutes) * 60) } : {}) };
  const operation = useSessionOperation(draftKey, operationBinding({ intent, generation, lastTerminalRunId }), { details, detailsEdited, profile, minutes, generation, lastTerminalRunId });
  const controlOperation = useRef<{ binding: string; id: string } | undefined>(undefined);
  const updateRun = async (result: GoalRun) => {
    cache.setQueryData(["projects", projectId, "goal-runs", result.id], result);
    await cache.invalidateQueries({ queryKey });
    const next = new URLSearchParams(search); next.set("run", result.id); next.delete("task");
    setSearch(next, { replace: true });
  };
  const start = useMutation({ mutationFn: () => {
    if (statusUnavailable) throw new Error("Wait for this goal's activity to load before starting.");
    if (current && runningStates.has(current.state)) throw new Error("This goal already has an unfinished run.");
    if (editing || configureRunner) throw new Error("Finish the optional settings before starting.");
    if (minutes && (!Number.isFinite(Number(minutes)) || Number(minutes) < 1 || Number(minutes) > 1440)) throw new Error("Choose an active time allowance between 1 and 1440 minutes.");
    return startGoalRun(projectId, agentId, goal.id, { operation_id: operation.operation, ...intent });
  }, onSuccess: async result => { try { operation.clear(); } catch { /* Rotate the next request even when browser storage is unavailable. */ } setGeneration(value => value + 1); await updateRun(result); } });
  const control = useMutation({ mutationFn: (action: "pause" | "resume" | "cancel") => {
    if (statusUnavailable || !current || !current.available_actions?.includes(action)) throw new Error("Refresh this run's status before choosing an action.");
    const binding = `${runId}:${action}`;
    if (controlOperation.current?.binding !== binding) controlOperation.current = { binding, id: operationId() };
    return post<GoalRun>(projectPath(projectId, `/goal-runs/${runId}/${action}`), { operation_id: controlOperation.current.id });
  }, onSuccess: async result => { controlOperation.current = undefined; await updateRun(result); } });
  const workTaskId = current?.active_task_id || current?.task_ids.at(-1);
  const openTask = () => { if (workTaskId) { const next = new URLSearchParams(search); next.set("task", workTaskId); setSearch(next, { replace: true }); } };
  const openEvaluation = (saved: GoalRun) => {
    if (saved.state !== "cancelled") return;
    const next = new URLSearchParams(search);
    next.set("run", saved.id); next.set("editor", "true"); next.delete("task"); next.delete("evaluator");
    setSearch(next, { replace: true });
  };
  const seenQuestion = useRef("");
  useEffect(() => {
    const question = task.data?.question?.id;
    if (!question || current?.state !== "needs_input" || seenQuestion.current === question) return;
    seenQuestion.current = question;
    if (!search.get("task")) openTask();
  }, [task.data?.question?.id, current?.state, current?.active_task_id]);
  const active = Boolean(current && runningStates.has(current.state));
  useEffect(() => { onActiveChange?.(statusUnavailable || active || start.isPending); }, [statusUnavailable, active, start.isPending, onActiveChange]);
  const actions = new Set(current?.available_actions || []);
  const pending = start.isPending || control.isPending;
  const requirement = current?.requirements.find(item => item.blocking !== false);
  const needsEvaluationReview = Boolean(requirement && ["measurement_contract", "goal_run_input"].includes(requirement.code));
  const requestedProfile = requirement?.code === "execution_profile" && typeof requirement.resolution?.context?.requested === "string" ? requirement.resolution.context.requested : undefined;
  const dependencyAgentId = requirement?.code === "dependency_goal_input" && typeof requirement.resolution?.context?.agent_id === "string" ? requirement.resolution.context.agent_id : undefined;
  const dependencyAgentName = typeof requirement?.resolution?.context?.agent_name === "string" ? requirement.resolution.context.agent_name : "this agent";
  let relatedGoalHref: string | undefined;
  if (requirement?.code === "dependency_run_active" && typeof requirement.resolution?.context?.route === "string") {
    try {
      const target = new URL(requirement.resolution.context.route, window.location.origin);
      const prefix = `/projects/${projectId}/agents/`;
      if (target.origin === window.location.origin && target.pathname.startsWith(prefix) && /^[^/]+\/goals\/[^/]+$/.test(target.pathname.slice(prefix.length))) {
        if (typeof requirement.resolution.context.run_id === "string") target.searchParams.set("run", requirement.resolution.context.run_id);
        target.searchParams.set("return", location.pathname + location.search);
        relatedGoalHref = target.pathname + target.search;
      }
    } catch { /* Keep malformed destinations out of navigation. */ }
  }
  const runSummary = current?.state === "running" && current.focus ? `${current.focus.name} · ${currentWork[current.stage] || "Working on related behavior"}` : current?.summary;
  const displayedProfile = active ? current?.resolved?.profile || requestedProfile || profile : profile;
  const requirementMessages: Record<string, string> = {
    clean_source: "Use a clean, committed Git revision for this project, then choose Check again.",
    coding_backend: "Configure and authenticate a coding backend, then return here and choose Check again.",
    execution_profile: "Configure the execution profile this run needs, then choose Check again.",
    code_binding: "Add the agent's application code paths before starting measured improvements.",
    code_scope: "Update the agent's code paths so the requested changes stay inside its scope.",
    agent_confirmation: "Detect agents to activate this identity before starting work.",
    regression_baselines: "Another saved goal needs a current baseline before these improvements can be checked.",
    pending_observation: "Finish or stop the current production observation, then choose Check again.",
  };
  const requirementMessage = requirement && (requirement.message || requirement.reason || (typeof requirement.resolution?.context?.reason === "string" ? requirement.resolution.context.reason : requirementMessages[requirement.code] || current?.summary || requirement.code.replaceAll("_", " ")));
  const setupReturn = encodeURIComponent(location.pathname + location.search);
  const requirementAction = needsEvaluationReview && current && (actions.has("cancel") || current.state === "cancelled")
    ? <div><p className="quiet-copy">{requirement?.code === "measurement_contract" ? "The draft conflicts with accepted checks. Review it while preserving the existing scoring and required behavior." : "Inspect the saved work, then review the evaluation draft. Accepted checks and earlier evidence stay saved."}</p><Button tone="secondary" disabled={pending || statusUnavailable || editing} onClick={() => { if (current.state === "cancelled") openEvaluation(current); else control.mutate("cancel", { onSuccess: openEvaluation }); }}>{pending && control.variables === "cancel" ? "Stopping…" : current.state === "cancelled" ? "Review evaluation" : "Stop and review evaluation"}</Button></div>
    : requirement && ["execution_profile", "profile"].includes(requirement.code)
    ? <Button tone="secondary" onClick={() => { setAdvanced(true); setConfigureRunner(true); }}>Configure runner</Button>
    : requirement && ["coding_backend", "assistant"].includes(requirement.code)
      ? <Link className="button button-secondary" to={`/projects/${projectId}/settings/assistants?return=${setupReturn}`}>Configure coding backend</Link>
      : relatedGoalHref
        ? <Link className="button button-secondary" to={relatedGoalHref}>Open related goal</Link>
      : dependencyAgentId
        ? <Link className="button button-secondary" to={`/projects/${projectId}/agents/${encodeURIComponent(dependencyAgentId)}/overview?return=${setupReturn}`}>Set a goal for {dependencyAgentName}</Link>
      : requirement && ["agent_confirmation", "code_binding", "code_scope"].includes(requirement.code)
        ? <Link className="button button-secondary" to={`/projects/${projectId}/agents/${agentId}/configuration?return=${setupReturn}`}>Review agent scope</Link>
        : null;
  return <section className="goal-run-workspace">
    {!active && <form className="form goal-go-form" onSubmit={event => { event.preventDefault(); if (!pending && !configureRunner) start.mutate(); }}>
      <label>Details <span className="optional">Optional</span><textarea value={details} onChange={event => { setDetails(event.target.value); setDetailsEdited(true); }} rows={2} maxLength={4000} placeholder="Examples, constraints, or anything Agentagon should know." disabled={pending} /></label>
      <div className="goal-go-actions"><Button type="submit" disabled={pending || statusUnavailable || configureRunner || editing}>{start.isPending ? "Starting…" : current ? "Start new run" : "Go"}</Button><p>{editing ? "Save or close the optional evaluator editor before starting." : current ? "Start another bounded attempt with these details. Previous results stay saved." : "Agentagon measures this goal, works on improvements, and checks the results. It asks when it needs your input."}</p></div>
    </form>}
    {statusLoading && <p role="status">Loading goal activity…</p>}
    {wrongGoal && <div className="error-banner" role="alert"><p>This run belongs to a different goal.</p><Button tone="secondary" onClick={() => { const next = new URLSearchParams(search); next.delete("run"); next.delete("task"); setSearch(next, { replace: true }); }}>Show this goal's activity</Button></div>}
    {(runs.error || run.error) && <div className="error-banner" role="alert"><p>{runs.error?.message || run.error?.message}</p><Button tone="secondary" onClick={() => { runs.refetch(); if (runId) run.refetch(); }}>Retry status</Button></div>}
    {current && <section className="goal-run-status" aria-live="polite"><header><div><h2>{current.state === "running" ? current.focus ? "Checking related behavior" : currentWork[current.stage] || "Working on your goal" : current.state === "needs_input" ? "Needs your input" : current.state === "completed" ? "Goal run complete" : current.state === "paused" ? "Paused" : current.state === "interrupted" ? "Work was interrupted" : current.state === "cancelled" ? "Stopped" : "The run needs attention"}</h2>{runSummary && <p>{runSummary}</p>}</div><Status value={current.state} /></header>
      {requirement && (requirementMessage !== runSummary || requirementAction) && <div className="goal-run-requirement">{requirementMessage !== runSummary && <p>{requirementMessage}</p>}{requirementAction}</div>}
      {task.data?.question && current.state === "needs_input" && <div className="goal-run-requirement"><p>{task.data.question.text}</p><Button onClick={openTask}>Respond</Button></div>}
      <div className="goal-run-actions">{current.outcome && <Link className="button button-primary" to={`/projects/${projectId}/results/${current.outcome.kind}/${encodeURIComponent(current.outcome.id)}`}>View result</Link>}{actions.has("resume") && !needsEvaluationReview && <Button disabled={pending || statusUnavailable} onClick={() => control.mutate("resume")}>{control.isPending && control.variables === "resume" ? "Continuing…" : current.state === "needs_input" && requirement ? "Check again" : "Continue"}</Button>}{actions.has("pause") && <Button tone="secondary" disabled={pending || statusUnavailable} onClick={() => control.mutate("pause")}>Pause</Button>}{actions.has("cancel") && <Button tone="quiet" disabled={pending || statusUnavailable} onClick={() => control.mutate("cancel")}>Stop</Button>}{workTaskId && <Button tone="quiet" onClick={openTask}>View work</Button>}</div>
      <p className="goal-run-timing">{Math.ceil(current.accounting.active_seconds / 60)} min active · {Math.ceil(current.accounting.remaining_seconds / 60)} min remaining</p>
      {Boolean(current.accounting.unknown_seconds) && <p className="quiet-copy">{Math.ceil((current.accounting.unknown_seconds || 0) / 60)} min reserved for work whose timing could not be confirmed.</p>}
    </section>}
    {(start.error || control.error) && <p className="error-banner" role="alert">{start.error?.message || control.error?.message}</p>}
    <details className="goal-run-options" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}><summary>Runner and limits <span className="optional">Optional</span></summary><div className="form">
      <label>Execution profile<select value={displayedProfile} onChange={event => setProfile(event.target.value)} disabled={active || pending}><option value="">Use project settings</option>{displayedProfile && !execution.data?.profiles[displayedProfile] && <option value={displayedProfile}>{displayedProfile} · unavailable</option>}{Object.keys(execution.data?.profiles || {}).map(name => <option key={name} value={name}>{name}</option>)}</select></label>
      <label>Active time allowance <span className="optional">Minutes</span><input type="number" min={1} max={1440} value={active && current?.limits ? current.limits.max_elapsed_seconds / 60 : minutes} onChange={event => setMinutes(event.target.value)} placeholder="Use configured limit" disabled={active || pending} /></label>
      {active && <p className="quiet-copy">This run keeps the settings it started with.</p>}
      {!configureRunner && (!active || requestedProfile) && <Button tone="quiet" onClick={() => setConfigureRunner(true)}>Configure runner</Button>}
      {execution.isLoading && <p role="status">Loading execution profiles…</p>}{execution.error && <p role="alert">{execution.error.message}<Button onClick={() => execution.refetch()}>Retry</Button></p>}
      {configureRunner && execution.data && <ExecutionProfileEditor key={requestedProfile || "new"} projectId={projectId} settings={execution.data} profileName={requestedProfile} onSaved={name => { if (!active) setProfile(name); setConfigureRunner(false); }} onCancel={() => setConfigureRunner(false)} />}
    </div></details>
  </section>;
}

const goalPresets = [
  { category: "correctness", name: "Improve task success", objective: "Improve this agent's task success and correctness while preserving its other accepted behaviors." },
  { category: "reliability", name: "Make tool use reliable", objective: "Improve this agent's reliability and tool use while preserving its other accepted behaviors." },
  { category: "latency", name: "Make it faster", objective: "Reduce this agent's response latency while preserving correctness and its other accepted behaviors." },
  { category: "cost", name: "Reduce cost", objective: "Reduce this agent's operating cost while preserving correctness and its other accepted behaviors." },
];

export function GoalPresets({ projectId, agentId, goals, onCustom }: { projectId: string; agentId: string; goals: Goal[]; onCustom: () => void }) {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const key = `agentagon.goal-presets:${projectId}:${agentId}`;
  const operations = useRef(loadOperationDraft<{ requests: Record<string, string> }>(key).requests || {});
  const choose = useMutation({ mutationFn: async (preset: typeof goalPresets[number]) => {
    const existing = goals.find(goal => goal.category === preset.category && goal.objective === preset.objective);
    if (existing) return existing;
    const binding = operationBinding(preset);
    if (!operations.current[binding]) operations.current[binding] = operationId();
    try { sessionStorage.setItem(key, JSON.stringify({ requests: operations.current })); } catch { /* Retain the same operation for in-memory retries. */ }
    return post<Goal>(projectPath(projectId, `/agents/${agentId}/goals`), { ...preset, operation_id: operations.current[binding] });
  }, onSuccess: async goal => { await cache.invalidateQueries({ queryKey: ["projects", projectId, "agents", agentId, "goals"] }); const returnTo = new URLSearchParams(location.search).get("return"); const query = returnTo?.startsWith(`/projects/${projectId}/agents/`) && returnTo.includes("/goals/") ? `?return=${encodeURIComponent(returnTo)}` : ""; navigate(`/projects/${projectId}/agents/${agentId}/goals/${goal.id}${query}`); } });
  return <section className="goal-presets"><div className="section-heading"><h2>What would you like to improve?</h2><Button tone="quiet" onClick={onCustom} disabled={choose.isPending}>Write a goal</Button></div><div className="goal-preset-grid">{goalPresets.map(preset => <Button key={preset.category} tone="secondary" onClick={() => choose.mutate(preset)} disabled={choose.isPending}>{choose.isPending && choose.variables?.category === preset.category ? "Opening…" : preset.name}</Button>)}</div>{choose.error && <p className="error-banner" role="alert">{choose.error.message}</p>}</section>;
}
