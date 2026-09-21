import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, operationId, post, projectPath } from './api';
import { AddGoalModal, Button, LaunchWorkflowModal, PageHeader, Status } from './components';
import { useAgents, useAssistants, useConnectors, useWorkflows } from './hooks';
import { loadOperationDraft, operationBinding, useSessionOperation } from './session';
import type { Agent, Project } from './types';

type Setup = { state: string; scope: Record<string, unknown>; task_id?: string; limitations?: string[] };
type Recommendation = { id: string; agent_id?: string; title: string; basis: string; workflow: string; action?: string; target?: { type: string; id: string; task_id?: string; run_id?: string; workflow?: string }; input?: { type: string; id: string }; prerequisite: string; category?: string; confidence?: string; evidence?: string[]; measurement?: { value: number; count: number; coverage: number } | null; evidence_revision?: string; active?: boolean; disposition?: { value: string; reason?: string; decided_at?: string; revision: number } | null };
type Measurement = { name: string; metric: string; aggregation: string; direction: string; minimum_samples: number; minimum_coverage: number; material_change: number; accepted: boolean; window_hours: number; reference_hours: number; population: Record<string, unknown>; quality_key?: string; issue_id?: string; target?: number };
type Monitor = { id: string; agent_id: string; enabled: boolean; state: string; revision: number; measurements: Measurement[]; selector: Record<string, string>; last_checked?: string; next_due: string; stale: boolean; error?: string; task_id?: string; storage_budget_bytes: number; trace_cap: number; interval_seconds: number; catchup_days: number; diagnosis: boolean; public_state?: { code: string; label: string; reason: string; resolution_action?: string | null; task_state?: string | null; next_due?: string | null; last_checked?: string | null }; last_successful_window?: Record<string, unknown> | null; last_successful_checkpoint?: Record<string, unknown> | null; coverage_summary?: Record<string, unknown>; revision_summary?: Record<string, unknown> };
type Metric = { name: string; status: string; reference: number | null; current: number | null; count: number; population: number; reference_count: number; reference_coverage: number; interval: number[] | null; reference_interval: number[] | null; coverage: number; delta: number | null; limitations: string[]; cohorts: string[] };
type Improvement = { id: string; agent_id: string; task_id: string; workflow: 'fix' | 'optimize'; run_id: string; candidate_id: string; recommended_by_task: boolean; selected_by_user: boolean; tested_revision: string; summary: string; next_action?: string; lessons: Array<{ id: string; version: number; decision: string; reason: string }> };
type Observation = { id: string; agent_id: string; monitor_id: string; task_id: string; window: { start: string; end: string; gap: boolean }; metrics: Metric[]; limitations: string[]; coverage: { complete: boolean } };
type Deployment = { id: string; agent_id: string; improvement_id?: string; release: string; environment: string; linkage: 'user_declared' | 'trace_reported' | string; deployed_revision: string; tested_revision: string; exact_tested_revision: boolean; deployed_at?: string | null; first_seen_at?: string; evidence?: { snapshot_id?: string; trace_id?: string } };
type Production = { monitors: Monitor[]; observations: Observation[]; improvements: Improvement[]; deployments: Deployment[]; attention: Array<{ id: string; kind?: string; message: string; agent_id: string; task_id?: string; at: string }> };

function useProduction(project: string) {
  return useQuery({ queryKey: ['projects', project, 'production'], queryFn: () => api<Production>(projectPath(project, '/production')), refetchInterval: 10000 });
}

export function SetupBanner({ projectId }: { projectId: string }) {
  const setup = useQuery({ queryKey: ['projects', projectId, 'onboarding'], queryFn: () => api<Setup>(projectPath(projectId, '/onboarding')) });
  if (!setup.data || setup.data.state === 'complete') return null;
  const analyzing = setup.data.state === 'analyzing';
  return <section className="onboarding-band"><div><h2>{analyzing ? 'Project analysis is running' : setup.data.state === 'ready' ? 'Project is ready to analyze' : 'Analyze this project'}</h2><p>{analyzing ? 'Agent identities and available evidence are being reviewed.' : 'Discover the agents in this folder and get evidence-backed next actions. Traces are optional.'}</p></div><Link className="button button-secondary" to={analyzing && setup.data.task_id ? `/projects/${projectId}/tasks/${setup.data.task_id}` : `/projects/${projectId}/onboarding`}>{analyzing ? 'View activity' : setup.data.state === 'ready' ? 'Analyze now' : 'Choose evidence'}</Link></section>;
}

export function OnboardingPage({ project }: { project: Project }) {
  const query = useQueryClient();
  const navigate = useNavigate();
  const setup = useQuery({ queryKey: ['projects', project.id, 'onboarding'], queryFn: () => api<Setup>(projectPath(project.id, '/onboarding')) });
  const assistants = useAssistants();
  const connectors = useConnectors(project.id);
  const traces = useQuery({ queryKey: ['projects', project.id, 'traces'], queryFn: () => api<{ traces: Array<{ id: string; name: string }> }>(projectPath(project.id, '/traces')) });
  type AssessmentDraft = { connection?: string; snapshot?: string; environment?: string; cap?: number; days?: number };
  const draftKey = `agentagon.operation-draft:${project.id}:assess`;
  const initialDraft = useRef(loadOperationDraft<AssessmentDraft>(draftKey));
  const [connection, setConnection] = useState(initialDraft.current.connection || '');
  const [snapshot, setSnapshot] = useState(initialDraft.current.snapshot || '');
  const [environment, setEnvironment] = useState(initialDraft.current.environment || '');
  const [cap, setCap] = useState(initialDraft.current.cap || 100);
  const [days, setDays] = useState(initialDraft.current.days || 7);
  const [savedAt, setSavedAt] = useState<string>();
  useEffect(() => { if (setup.data && !initialDraft.current.operationId) { const s = setup.data.scope; setConnection(String(s.connection_id || '')); setSnapshot(String(s.snapshot_id || '')); setEnvironment(String(s.environment || '')); setCap(Number(s.trace_cap || 100)); setDays(Number(s.lookback_days || 7)); } }, [setup.data]);
  const scope = { ...(connection ? { connection_id: connection } : {}), ...(snapshot ? { snapshot_id: snapshot } : {}), ...(environment ? { environment } : {}), trace_cap: cap, lookback_days: days };
  const assessmentRequest = { workflow: 'assess', input: { type: 'project', id: project.id }, options: { assessment: scope } };
  const operationDraft = useSessionOperation(draftKey, operationBinding(assessmentRequest), { connection, snapshot, environment, cap, days } satisfies AssessmentDraft);
  const save = useMutation({ mutationFn: () => post(projectPath(project.id, '/onboarding'), { scope, state: 'ready' }), onSuccess: () => { setSavedAt(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })); query.invalidateQueries({ queryKey: ['projects', project.id, 'onboarding'] }); } });
  const analyze = useMutation({ mutationFn: async () => { await save.mutateAsync(); return post<{ id?: string; task_id?: string }>(projectPath(project.id, '/tasks'), { operation_id: operationDraft.operation, ...assessmentRequest }); }, onSuccess: task => { const taskId = task.task_id || task.id; if (!taskId) return; operationDraft.clear(); navigate(`/projects/${project.id}/tasks/${taskId}`); } });
  const sourceState = project.source?.kind === 'git'
    ? project.source.dirty
      ? 'Git checkout with uncommitted changes'
      : `Clean Git revision ${project.source.revision?.slice(0, 12) || 'unknown'}`
    : 'Local folder · code assessment available';
  const configuredBackend = assistants.data?.assistants.find(item => item.id === assistants.data?.defaults.default_agent) || assistants.data?.assistants[0];
  const selectedConnection = connectors.data?.connections.find(item => item.id === connection);
  const selectedSnapshot = traces.data?.traces.find(item => item.id === snapshot);
  const connectionReady = Boolean(selectedConnection && selectedConnection.status === 'connected');
  const snapshotReady = Boolean(selectedSnapshot);
  const traceSelectionUnavailable = Boolean((connection && !connectionReady) || (snapshot && !snapshotReady));
  const traceState = connection
    ? selectedConnection
      ? `${selectedConnection.name || selectedConnection.project_name || selectedConnection.provider} · ${selectedConnection.status === 'connected' ? 'Ready' : (selectedConnection.status || 'not tested').replaceAll('_', ' ')}`
      : 'Selected provider connection is unavailable'
    : snapshot
      ? selectedSnapshot?.name ? `${selectedSnapshot.name} · Ready` : 'Selected imported traces are unavailable'
      : 'Code-only assessment selected';
  return <>
    <PageHeader title={`Start with ${project.name}`} actions={<><Link className="button button-secondary" to={`/projects/${project.id}/home`}>Open workspace</Link><Button type="submit" form="project-analysis-form" disabled={analyze.isPending || traceSelectionUnavailable}>{analyze.isPending ? 'Starting analysis…' : setup.data?.state === 'complete' ? 'Analyze again' : 'Analyze project'}</Button></>}><p>Review the source, optional trace scope, and limits before analysis.</p></PageHeader>
    {(save.error || analyze.error) && <p className="error-banner" role="alert">Analysis could not start: {(save.error || analyze.error)?.message}</p>}
    {setup.data?.task_id && <section className={`onboarding-outcome outcome-${setup.data.state}`}><div><p className="eyebrow">{setup.data.state === 'complete' ? 'Latest outcome' : 'Current activity'}</p><h2>{setup.data.state === 'complete' ? 'Project analysis is complete' : 'Project analysis is running'}</h2><p>{setup.data.state === 'complete' ? 'Inspect the discovered agents, then choose one useful action.' : 'Incremental discoveries appear in the task as they are retained.'}</p></div><div className="onboarding-outcome-actions"><Link className="button button-secondary" to={`/projects/${project.id}/tasks/${setup.data.task_id}`}>View assessment</Link>{setup.data.state === 'complete' && <Link className="button button-primary" to={`/projects/${project.id}/agents`}>Explore agents</Link>}</div></section>}
    <div className="setup-preflight">
      <section className="preflight-row"><div><span className="preflight-label">Code</span><strong>{sourceState}</strong><p><code>{project.path}</code></p></div><Link to={`/projects/${project.id}/settings/project`}>Inspect source</Link></section>
      <section className="preflight-row"><div><span className="preflight-label">Coding backend</span>{configuredBackend ? <p><strong>{configuredBackend.name}</strong> · {!configuredBackend.available ? 'Not installed' : configuredBackend.authenticated === true ? 'Ready and authenticated' : configuredBackend.authenticated === false ? 'Authentication needed' : 'Authentication state unknown'}</p> : <p><strong>No coding backend detected</strong></p>}<small>Code candidates are retained without a backend. Responsibility inference, semantic diagnosis, and code authoring require an authenticated backend.</small></div><Link to={`/projects/${project.id}/settings/assistants`}>{configuredBackend?.available && configuredBackend.authenticated === true ? 'Review' : 'Configure'}</Link></section>
      <section className="preflight-row"><div><span className="preflight-label">Traces</span><strong>{traceState}</strong><p>{connection || snapshot ? traceSelectionUnavailable ? 'This source cannot be acquired. Fix it or explicitly choose code only.' : 'Selected evidence will be acquired within the bounds below.' : 'Production evidence is optional. This run will inspect code only.'}</p></div><Link to={`/projects/${project.id}/settings/connections`}>{connection ? 'Check connection' : 'Manage connections'}</Link></section>
    </div>
    <form id="project-analysis-form" className="settings-card form assessment-scope" onSubmit={event => { event.preventDefault(); analyze.mutate(); }} onBlur={() => save.mutate()}>
      <div className="section-heading"><div><p className="eyebrow">Assessment scope</p><h2>Optional production evidence</h2></div><small>{save.isPending ? 'Saving…' : savedAt ? `Saved ${savedAt}` : 'Changes save when you leave a field'}</small></div>
      <div className="form-grid"><label>Provider connection<select value={connection} onChange={e => { setConnection(e.target.value); setSnapshot(''); }}><option value="">No provider — analyze code only</option>{connectors.data?.connections.map(c => <option value={c.id} key={c.id}>{c.name || c.provider} · {c.project_name || c.project || 'project'} · {c.status === 'connected' ? 'ready' : (c.status || 'not tested').replaceAll('_', ' ')}</option>)}</select></label><label>Imported traces<select value={snapshot} onChange={e => { setSnapshot(e.target.value); setConnection(''); }}><option value="">No imported trace</option>{traces.data?.traces.map(t => <option value={t.id} key={t.id}>{t.name}</option>)}</select></label><label>Environment <span className="optional">Optional</span><input value={environment} onChange={e => setEnvironment(e.target.value)} placeholder="production" /></label></div>
      {traceSelectionUnavailable && <div className="assessment-prerequisite" role="alert"><div><strong>Trace acquisition is not ready</strong><p>The selected trace source will not be presented to the assessment. Repair its connection or continue explicitly with code only.</p></div><div><Button type="button" tone="secondary" onClick={() => { setConnection(''); setSnapshot(''); }}>Use code only</Button><Link className="button button-secondary" to={`/projects/${project.id}/settings/connections`}>Fix connection</Link></div></div>}
      <details className="scope-bounds"><summary>Review analysis bounds</summary><div className="form-grid"><label>Lookback days<input type="number" min="1" max="7" value={days} onChange={e => setDays(Number(e.target.value))} /></label><label>Completed root traces<input type="number" min="1" max="100" value={cap} onChange={e => setCap(Number(e.target.value))} /></label></div><p>Analysis stops after 30 minutes. Generated, private-state, and dependency folders are excluded. Source files will not be edited.</p></details>
      <div className={`assessment-summary ${traceSelectionUnavailable ? 'assessment-summary-blocked' : ''}`}><strong>{traceSelectionUnavailable ? 'Choose a ready trace source or continue with code only.' : connection || snapshot ? 'Ready to inspect this folder and the selected trace evidence.' : 'Ready for an explicit code-only assessment.'}</strong><p>{configuredBackend?.available && configuredBackend.authenticated === true ? 'The coding backend can infer responsibilities and diagnose available evidence.' : 'Static code candidates will be retained. Semantic findings will be marked unavailable until the coding backend is authenticated.'} Repairs and recurring monitoring start only when you choose them.</p></div>
    </form>
  </>;
}

export function Recommendations({ projectId, agentId, limit }: { projectId: string; agentId?: string; limit?: number }) {
  const query = useQueryClient();
  const recommendations = useQuery({ queryKey: ['projects', projectId, 'recommendations'], queryFn: () => api<{ recommendations: Recommendation[] }>(projectPath(projectId, '/recommendations')) });
  const workflows = useWorkflows();
  const [selected, setSelected] = useState<Recommendation>();
  const [goal, setGoal] = useState<Recommendation>();
  const disposition = useMutation({ mutationFn: ({ recommendation, value }: { recommendation: Recommendation; value: 'not_now' | 'not_relevant' }) => post(projectPath(projectId, `/recommendations/${recommendation.id}/disposition`), { value, reason: '', evidence_revision: recommendation.evidence_revision }), onSuccess: () => query.invalidateQueries({ queryKey: ['projects', projectId, 'recommendations'] }) });
  const workflow = workflows.data?.workflows.find(w => w.workflow === selected?.workflow);
  const active = (recommendations.data?.recommendations.filter(r => (!agentId || r.agent_id === agentId) && r.active !== false) || []).slice(0, limit);
  const openAction = (r: Recommendation) => {
    if (r.action === 'assign_agent') return <Link className="button button-secondary" to={`/projects/${projectId}/agents`}>Assign agent</Link>;
    if (r.action === 'inspect_task' && r.target?.task_id) return <Link className="button button-secondary" to={`/projects/${projectId}/tasks/${r.target.task_id}`}>Inspect task</Link>;
    if (['review_verified_change', 'prepare_local_delivery'].includes(r.action || '') && r.target?.run_id) return <Link className="button button-secondary" to={`/projects/${projectId}/results/${r.target.workflow || 'fix'}/${encodeURIComponent(r.target.run_id)}`}>{r.action === 'review_verified_change' ? 'Review change' : 'Prepare delivery'}</Link>;
    if (r.action === 'record_deployment' && r.agent_id) return <Link className="button button-secondary" to={`/projects/${projectId}/agents/${r.agent_id}/improvements`}>Record deployment</Link>;
    if (r.action === 'observe_production' && r.agent_id) return <Link className="button button-secondary" to={`/projects/${projectId}/agents/${r.agent_id}/production`}>Observe production</Link>;
    return <Button tone="secondary" onClick={() => r.input ? setSelected(r) : setGoal(r)}>{r.action === 'investigate_recurrence' ? 'Investigate recurrence' : r.input ? 'Fix issue' : 'Define goal'}</Button>;
  };
  return <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Recommended next</p><h2>{active.length ? "Best supported action" : "No action is waiting"}</h2></div></div>{active.length ? <div className="list-surface">{active.map(r => <article className="list-row recommendation-row" key={r.id}><div><strong>{r.title}</strong><span>{r.basis === 'not_measured' ? 'Available action · not measured yet' : 'Supported by retained evidence'}</span><span>{r.prerequisite}</span>{r.measurement && <p>{r.measurement.value.toPrecision(4)} · {r.measurement.count} scored traces · {Math.round(r.measurement.coverage*100)}% measurement coverage</p>}{r.confidence && <p>Diagnostic confidence: {r.confidence}</p>}{!!r.evidence?.length && <details><summary>Evidence and uncertainty</summary>{r.evidence.map(e => <p key={e}><code>{e}</code></p>)}<p>Measurements describe this sample. Confirm expected behavior and measure alternatives before concluding improvement.</p></details>}</div><div className="recommendation-actions">{openAction(r)}{r.evidence_revision && <><Button tone="quiet" disabled={disposition.isPending} onClick={() => disposition.mutate({ recommendation: r, value: 'not_now' })}>Not now</Button><Button tone="quiet" disabled={disposition.isPending} onClick={() => disposition.mutate({ recommendation: r, value: 'not_relevant' })}>Not relevant</Button></>}</div></article>)}</div> : <p className="quiet-surface">Nothing new requires a decision. You can still describe a problem or create an evaluation.</p>}{disposition.error && <p className="error-banner">{disposition.error.message}</p>}{selected && workflow && <LaunchWorkflowModal projectId={projectId} workflow={workflow} defaultAgentId={selected.agent_id || agentId} defaultInput={selected.input} onClose={() => setSelected(undefined)} />}{goal && goal.agent_id && <AddGoalModal projectId={projectId} agentId={goal.agent_id} defaultCategory={goal.category} defaultObjective={goal.title} onClose={() => setGoal(undefined)} />}</section>;
}

export function ProductionAttention({ projectId }: { projectId: string }) {
  const data = useProduction(projectId);
  return <section className="production-attention">{data.data?.attention.map(item => <Link className="list-row" key={item.id} to={item.kind === 'result_decision' && item.task_id ? `/projects/${projectId}/tasks/${item.task_id}` : `/projects/${projectId}/agents/${item.agent_id}/production`}><div><strong>{item.message}</strong><span>{item.kind === 'result_decision' ? 'Review the measured result and choose what to keep' : 'Production evidence needs attention'}</span></div><span>{item.at}</span></Link>)}</section>;
}

export function ImprovementsView({ projectId, agentId }: { projectId: string; agentId: string }) {
  const data = useProduction(projectId);
  const query = useQueryClient();
  const [selected, setSelected] = useState<Improvement>();
  const [release, setRelease] = useState('');
  const [revision, setRevision] = useState('');
  const [environment, setEnvironment] = useState('production');
  const [deployed, setDeployed] = useState('');
  const [operation, setOperation] = useState(operationId);
  const save = useMutation({ mutationFn: () => post(projectPath(projectId, '/deployments'), { operation_id: operation, improvement_id: selected?.id, release, environment, revision, deployed_at: new Date(deployed).toISOString() }), onSuccess: () => { setSelected(undefined); setOperation(operationId()); query.invalidateQueries({ queryKey: ['projects', projectId, 'production'] }); } });
  const improvements = data.data?.improvements.filter(i => i.agent_id === agentId) || [];
  const deployments = data.data?.deployments.filter(d => d.agent_id === agentId) || [];
  const resultLink = (improvement: Improvement) => `/projects/${projectId}/results/${improvement.workflow}/${encodeURIComponent(improvement.run_id)}?candidate_id=${encodeURIComponent(improvement.candidate_id)}`;
  return <>
    <h2>Verified improvements</h2>
    <p>Task recommendations, your choices, deployment, and production outcomes are recorded separately.</p>
    {!improvements.length && <p className="quiet-surface">No verified improvements yet. Failed and ongoing attempts remain in Activity.</p>}
    {improvements.map(i => <article className="settings-card improvement-card" key={i.id}>
      <div className="improvement-heading"><h3>{i.summary || 'Verified candidate'}</h3><Status value={i.selected_by_user ? 'selected by you' : i.recommended_by_task ? 'recommended by task' : 'verified alternative'} /></div>
      <p>Tested revision <code>{i.tested_revision}</code></p>
      <div className="improvement-actions"><Link to={resultLink(i)}>Review this candidate and decision</Link><Link to={`/projects/${projectId}/tasks/${i.task_id}`}>View task activity</Link></div>
      <details><summary>Lessons considered</summary>{i.lessons.map(l => <p key={l.id}>{l.decision}: {l.reason} ({l.id}, version {l.version})</p>)}</details>
      <Button disabled={!i.selected_by_user} onClick={() => { setSelected(i); setRevision(''); setOperation(operationId()); }}>Record deployment</Button>
      {!i.selected_by_user && <small>Choose this candidate in the measured result before recording a deployment.</small>}
    </article>)}
    {selected && <form className="settings-card form" onSubmit={e => { e.preventDefault(); save.mutate(); }} onChange={() => setOperation(operationId())}>
      <h3>Record deployment</h3>
      <p>Tested candidate revision <code>{selected.tested_revision}</code>. Enter the revision that actually reached this environment.</p>
      <label>Release<input required value={release} onChange={e => setRelease(e.target.value)} /></label>
      <label>Actual deployed revision<input required value={revision} onChange={e => setRevision(e.target.value)} placeholder="Commit or release revision from deployment" /></label>
      <label>Environment<input required value={environment} onChange={e => setEnvironment(e.target.value)} /></label>
      <label>Deployed at<input type="datetime-local" required value={deployed} onChange={e => setDeployed(e.target.value)} /></label>
      <p>This records your declaration. Traces must identify the release before Agentagon can attribute its production measurements.</p>
      {save.error && <p role="alert">{save.error.message}</p>}<Button disabled={save.isPending}>Save deployment</Button>
    </form>}
    <section className="deployment-history">
      <div className="section-heading"><div><p className="eyebrow">Deployment history</p><h3>Recorded releases</h3></div></div>
      {!deployments.length && <p className="quiet-surface">No deployment has been linked to a verified improvement.</p>}
      {deployments.map(deployment => {
        const improvement = improvements.find(item => item.id === deployment.improvement_id);
        const linkage = deployment.linkage === 'trace_reported' ? 'Trace-reported' : deployment.linkage === 'user_declared' ? 'User-declared' : deployment.linkage.replaceAll('_', ' ');
        const recordedAt = deployment.deployed_at || deployment.first_seen_at;
        return <article className="settings-card deployment-card" key={deployment.id}>
          <div className="improvement-heading"><div><h4>{deployment.release}</h4><p>{deployment.environment}</p></div><Status value={deployment.exact_tested_revision ? 'exact revision' : 'different revision'} /></div>
          <dl className="compact-definition">
            <div><dt>Deployed revision</dt><dd><code>{deployment.deployed_revision}</code></dd></div>
            <div><dt>Tested revision</dt><dd><code>{deployment.tested_revision}</code></dd></div>
            <div><dt>Revision match</dt><dd>{deployment.exact_tested_revision ? 'Exact tested candidate' : 'Different from the tested candidate; production observations cannot validate this improvement'}</dd></div>
            <div><dt>Linkage</dt><dd>{linkage}{deployment.linkage === 'trace_reported' ? ' from retained trace metadata' : ' deployment record'}</dd></div>
            <div><dt>{deployment.deployed_at ? 'Deployed at' : 'First observed'}</dt><dd>{recordedAt ? new Date(recordedAt).toLocaleString() : 'Time unavailable'}</dd></div>
          </dl>
          <div className="improvement-actions">
            {improvement && <Link to={resultLink(improvement)}>Review linked candidate</Link>}
            {deployment.evidence?.snapshot_id && deployment.evidence.trace_id && <Link to={`/projects/${projectId}/traces/${encodeURIComponent(deployment.evidence.snapshot_id)}?trace_id=${encodeURIComponent(deployment.evidence.trace_id)}`}>Inspect trace evidence</Link>}
          </div>
        </article>;
      })}
    </section>
  </>;
}

export function ProjectProductionPage({ projectId }: { projectId: string }) {
  const data = useProduction(projectId);
  const agents = useAgents(projectId);
  const connectors = useConnectors(projectId);
  const monitors = data.data?.monitors || [];
  const observations = data.data?.observations || [];
  const attention = data.data?.attention || [];
  const hasProductionData = monitors.length > 0 || observations.length > 0 || attention.length > 0;
  const firstAgent = agents.data?.confirmed[0];
  const hasConnection = Boolean(connectors.data?.connections.some(item => item.status === 'connected'));
  return <><PageHeader eyebrow="Production feedback" title="Production" actions={<Link className="button button-secondary" to={`/projects/${projectId}/settings/connections`}>Connections</Link>}><p>See whether selected improvements hold up in live traces. Monitoring is enabled separately for each agent.</p></PageHeader>{hasProductionData ? <><div className="summary-grid production-summary"><section className="summary-card"><span className="summary-label">Enabled monitors</span><strong>{monitors.filter(item => item.enabled).length}</strong></section><section className="summary-card"><span className="summary-label">Observations</span><strong>{observations.length}</strong></section><section className="summary-card"><span className="summary-label">Needs attention</span><strong>{attention.length}</strong></section></div><ProductionAttention projectId={projectId} /></> : <section className="production-entry"><div><p className="eyebrow">No production evidence yet</p><h2>{hasConnection ? 'Choose an agent to observe' : 'Connect production traces'}</h2><p>{hasConnection ? 'Select an environment, measurement, comparison criteria, and collection limits before monitoring starts.' : 'Add a provider connection, or inspect an imported trace without enabling recurring collection.'}</p></div><div className="production-entry-actions">{hasConnection && firstAgent ? <Link className="button button-primary" to={`/projects/${projectId}/agents/${firstAgent.id}/production`}>Set up monitoring</Link> : <Link className="button button-primary" to={`/projects/${projectId}/settings/connections`}>Add connection</Link>}<Link className="button button-secondary" to={`/projects/${projectId}/issues`}>Inspect an imported trace</Link></div></section>}<section><div className="section-heading"><div><p className="eyebrow">By agent</p><h2>Production coverage</h2></div></div>{agents.data?.confirmed.length ? <div className="production-agent-grid">{agents.data.confirmed.map(agent => { const agentMonitors = monitors.filter(item => item.agent_id === agent.id); const latest = observations.find(item => item.agent_id === agent.id); const visibleState = agentMonitors.find(item => item.public_state)?.public_state; return <article className="settings-card production-agent-card" key={agent.id}><div><h3>{agent.name}</h3><Status value={visibleState?.code || (agentMonitors.length ? 'configured' : 'not enabled')} /></div><p>{visibleState ? `${visibleState.label}. ${visibleState.reason}` : agentMonitors.length ? `${agentMonitors.length} saved monitor${agentMonitors.length === 1 ? '' : 's'} · ${latest ? 'latest observation ' + latest.window.end : 'no observation yet'}` : 'Connect traces and accept comparison criteria to start observing this agent.'}</p><Link to={`/projects/${projectId}/agents/${agent.id}/production`}>{agentMonitors.length ? 'Open production details' : 'Set up monitoring'}</Link></article>; })}</div> : <p className="quiet-surface">Confirm an agent before configuring production monitoring.</p>}</section></>;
}

export function ProductionView({ projectId, agent }: { projectId: string; agent: Agent }) {
  const data = useProduction(projectId);
  const connectors = useConnectors(projectId);
  const query = useQueryClient();
  const navigate = useNavigate();
  const [observeOperation, setObserveOperation] = useState(operationId);
  const [editing, setEditing] = useState<Monitor | null>();
  const [environment, setEnvironment] = useState(String(agent.trace_selector?.environment || 'production'));
  const [metric, setMetric] = useState('latency_ms');
  const [aggregation, setAggregation] = useState('mean');
  const [minimum, setMinimum] = useState(100);
  const [coverage, setCoverage] = useState(95);
  const [effect, setEffect] = useState(0);
  const [accepted, setAccepted] = useState(false);
  const [key, setKey] = useState('');
  const [interval, setInterval] = useState(3600);
  const [cap, setCap] = useState(100);
  const [budget, setBudget] = useState(1);
  const [diagnosis, setDiagnosis] = useState(true);
  const [direction, setDirection] = useState('lower');
  const [measurementWindow, setMeasurementWindow] = useState(24);
  const [referenceWindow, setReferenceWindow] = useState(168);
  const [boundConnectionId, setBoundConnectionId] = useState(String(agent.trace_selector?.connection_id || ''));
  const [connectionId, setConnectionId] = useState(String(agent.trace_selector?.connection_id || ''));
  useEffect(() => { const value = String(agent.trace_selector?.connection_id || ''); setBoundConnectionId(value); setConnectionId(value); }, [agent.id, agent.trace_selector?.connection_id]);
  const refresh = () => query.invalidateQueries({ queryKey: ['projects', projectId, 'production'] });
  const save = useMutation({ mutationFn: () => post(projectPath(projectId, `/monitors${editing ? '/' + editing.id : ''}`), { agent_id: agent.id, environment, connection_id: boundConnectionId, enabled: editing ? editing.enabled : true, ...(editing ? { expected_revision: editing.revision } : {}), interval_seconds: interval, trace_cap: cap, storage_budget_bytes: Math.round(budget*1024**3), catchup_days: editing?.catchup_days || 7, diagnosis, measurements: [{ name: metric, metric, aggregation, direction, minimum_samples: minimum, minimum_coverage: coverage / 100, material_change: effect, accepted, window_hours: measurementWindow, reference_hours: referenceWindow, population: editing?.measurements[0]?.population || {}, ...(editing?.measurements[0]?.metric === metric && editing.measurements[0].target !== undefined ? { target: editing.measurements[0].target } : {}), ...(metric === 'quality' ? { quality_key: key } : {}), ...(metric === 'issue_recurrence' ? { issue_id: key } : {}) }, ...(editing?.measurements.slice(1) || [])] }), onSuccess: () => { setEditing(undefined); refresh(); } });
  const bind = useMutation({ mutationFn: () => post<Agent>(projectPath(projectId, `/agents/${agent.id}`), { trace_selector: { ...(agent.trace_selector || {}), connection_id: connectionId, environment }, ...(agent.revision !== undefined ? { expected_revision: agent.revision } : {}) }), onSuccess: async (saved) => { const value = String(saved.trace_selector?.connection_id || ''); setBoundConnectionId(value); setConnectionId(value); await query.invalidateQueries({ queryKey: ['projects', projectId, 'agents'] }); } });
  const control = useMutation({ mutationFn: ({ monitor, action }: { monitor: Monitor; action: string }) => post(projectPath(projectId, `/monitors/${monitor.id}/${action}`), {}), onSuccess: refresh });
  const observe = useMutation({ mutationFn: (monitor: Monitor) => post<{ task_id: string }>(projectPath(projectId, '/tasks'), { operation_id: observeOperation, workflow: 'observe', input: { type: 'monitor', id: monitor.id } }), onSuccess: task => { setObserveOperation(operationId()); navigate(`/projects/${projectId}/tasks/${task.task_id}`); } });
  const monitors = data.data?.monitors.filter(m => m.agent_id === agent.id) || [];
  function edit(monitor: Monitor | null) { setEditing(monitor); if (!monitor) return; const m = monitor.measurements[0]; setEnvironment(monitor.selector.environment); setInterval(monitor.interval_seconds); setCap(monitor.trace_cap); setBudget(monitor.storage_budget_bytes/1024**3); setDiagnosis(monitor.diagnosis); if (m) { setMetric(m.metric); setAggregation(m.aggregation); setMinimum(m.minimum_samples); setCoverage(Math.round(m.minimum_coverage * 100)); setEffect(m.material_change); setAccepted(m.accepted); setKey(m.quality_key || m.issue_id || ''); setDirection(m.direction); setMeasurementWindow(m.window_hours); setReferenceWindow(m.reference_hours); } }
  const traceBound = Boolean(boundConnectionId);
  return <>
    <div className="section-heading"><div><h2>Production</h2><p>Checks run while the local service and machine are available.</p></div><Button onClick={() => edit(null)}>Enable monitoring</Button></div>
    {monitors.map(m => <article className="settings-card monitor-card" key={m.id}><div className="monitor-heading"><h3>{m.selector.environment}</h3><Status value={m.public_state?.code || (m.enabled ? m.state : 'paused')} /></div>{m.public_state ? <div className="monitor-public-state"><strong>{m.public_state.label}</strong><p>{m.public_state.reason}</p>{m.public_state.resolution_action && <small>Next: {m.public_state.resolution_action.replaceAll('_', ' ')}</small>}</div> : <p>Monitor status is being prepared.</p>}<p>Last checked: {m.public_state?.last_checked || m.last_checked || 'Never'} · Next: {m.public_state?.next_due || m.next_due || 'Not scheduled'}</p>{m.error && <p role="alert">{m.error}</p>}<div className="form-actions"><Button tone="secondary" onClick={() => control.mutate({ monitor: m, action: m.enabled ? 'pause' : 'enable' })}>{m.enabled ? 'Pause' : 'Enable'}</Button><Button tone="secondary" disabled={observe.isPending} onClick={() => observe.mutate(m)}>Analyze now</Button><Button tone="secondary" onClick={() => edit(m)}>Edit monitor</Button>{m.task_id && <><Link to={`/projects/${projectId}/tasks/${m.task_id}`}>Inspect or resume task</Link><Button tone="secondary" onClick={() => control.mutate({ monitor: m, action: 'discard' })}>Discard pending check</Button></>}</div><details className="monitor-diagnostics"><summary>Monitor diagnostics</summary><code>runtime state: {m.state}</code>{m.coverage_summary && <code>coverage: {JSON.stringify(m.coverage_summary)}</code>}{m.revision_summary && <code>revisions: {JSON.stringify(m.revision_summary)}</code>}</details></article>)}
    {(control.error || observe.error) && <p role="alert">{(control.error || observe.error)?.message}</p>}
    {editing !== undefined && <form className="settings-card form monitor-policy-form" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
      <div className="section-heading"><div><p className="eyebrow">Monitoring policy</p><h3>{editing ? 'Edit production monitoring' : 'Enable production monitoring'}</h3></div><Button type="button" tone="quiet" onClick={() => setEditing(undefined)}>Close</Button></div>
      {!traceBound && <div className="monitor-prerequisite"><strong>Choose the production trace source</strong><p>Your monitoring policy stays here while you bind an existing project connection. Adding a new provider opens in another tab.</p>{connectors.data?.connections.length ? <div className="monitor-inline-binding"><label>Provider connection<select value={connectionId} onChange={e => setConnectionId(e.target.value)}><option value="">Select a connection</option>{connectors.data.connections.filter(item => item.status === 'connected').map(item => <option value={item.id} key={item.id}>{item.name || item.project_name || item.provider}</option>)}</select></label><Button type="button" tone="secondary" disabled={!connectionId || bind.isPending} onClick={() => bind.mutate()}>{bind.isPending ? 'Binding…' : 'Bind to this agent'}</Button></div> : <p>No connected provider is available for this project.</p>}<div><Link className="button button-secondary" target="_blank" rel="noreferrer" to={`/projects/${projectId}/settings/connections?return=${encodeURIComponent(`/projects/${projectId}/agents/${agent.id}/production`)}`}>Add provider connection</Link></div>{bind.error && <p role="alert">{bind.error.message}</p>}</div>}
      <fieldset className="monitor-form-section"><legend>Scope</legend><label>Environment<input value={environment} required onChange={e => setEnvironment(e.target.value)} /></label><p>Population: traces matching this agent’s confirmed selector in <strong>{environment}</strong>. Expanding that selector requires a separate identity change.</p></fieldset>
      <fieldset className="monitor-form-section"><legend>Schedule</legend><label>Collection schedule<select value={interval} onChange={e => setInterval(Number(e.target.value))}><option value={3600}>Hourly</option><option value={21600}>Every 6 hours</option><option value={86400}>Daily</option></select></label><p>Missed time is reported as a gap. Catch-up is bounded to {editing?.catchup_days || 7} days.</p></fieldset>
      <fieldset className="monitor-form-section"><legend>Measure</legend><label>Measurement<select value={metric} onChange={e => setMetric(e.target.value)}><option value="latency_ms">Latency</option><option value="cost_usd">Reported cost</option><option value="failure_rate">Explicit failure rate</option><option value="issue_recurrence">Issue recurrence</option><option value="quality">Accepted quality score</option></select></label>{['quality', 'issue_recurrence'].includes(metric) && <label>{metric === 'quality' ? 'Accepted trace score key' : 'Issue ID'}<input required value={key} onChange={e => setKey(e.target.value)} /></label>}<div className="form-grid"><label>Summary<select value={aggregation} onChange={e => setAggregation(e.target.value)}><option value="mean">Mean</option><option value="p95">95th percentile</option></select></label><label>Better direction<select value={direction} onChange={e => setDirection(e.target.value)}><option value="lower">Lower is better</option><option value="higher">Higher is better</option></select></label></div></fieldset>
      <fieldset className="monitor-form-section"><legend>Comparison</legend><div className="form-grid"><label>Minimum samples per cohort<input type="number" min="2" max="10000" value={minimum} onChange={e => setMinimum(Number(e.target.value))} /></label><label>Minimum measured coverage (%)<input type="number" min="1" max="100" step="1" value={coverage} onChange={e => setCoverage(Number(e.target.value))} /></label><label>Material change (measurement units)<input type="number" min="0" step="any" value={effect} onChange={e => setEffect(Number(e.target.value))} /></label><label>Current window (hours)<input type="number" min="1" max="168" value={measurementWindow} onChange={e => setMeasurementWindow(Number(e.target.value))} /></label><label>Reference window before deployment (hours)<input type="number" min="1" max="168" value={referenceWindow} onChange={e => setReferenceWindow(Number(e.target.value))} /></label></div><label className="checkbox-label"><input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} /> Accept these criteria for Improved, Regressed, or No material change classifications</label>{!accepted && <p>Observations remain descriptive until these criteria are accepted.</p>}</fieldset>
      <fieldset className="monitor-form-section"><legend>Resource limits</legend><div className="form-grid"><label>Traces per window<input type="number" min="1" max="100" value={cap} onChange={e => setCap(Number(e.target.value))} /></label><label>Project evidence budget (GiB)<input type="number" min="0.02" step="0.01" value={budget} onChange={e => setBudget(Number(e.target.value))} /></label></div><label className="checkbox-label"><input type="checkbox" checked={diagnosis} onChange={e => setDiagnosis(e.target.checked)} /> Diagnose new evidence at most daily with a five-minute limit</label></fieldset>
      <div className="monitor-policy-summary"><strong>Policy summary</strong><p>Collect up to {cap} matching traces {interval === 3600 ? 'hourly' : interval === 21600 ? 'every 6 hours' : 'daily'} in {environment}. Compare {measurementWindow} hours with the {referenceWindow}-hour pre-deployment reference. Store at most {budget} GiB of project evidence.</p></div>
      {editing && !editing.enabled && <p className="quiet-copy">This monitor is paused. Saving its policy will keep it paused.</p>}
      {save.error && <p role="alert">{save.error.message}</p>}<footer className="form-actions"><Button disabled={save.isPending || !traceBound}>{save.isPending ? 'Saving…' : editing ? 'Save monitoring policy' : 'Enable monitoring'}</Button><Button type="button" tone="secondary" onClick={() => setEditing(undefined)}>Cancel</Button></footer>
    </form>}
    <section className="overview-section"><div className="section-heading"><div><p className="eyebrow">Observations</p><h2>Production evidence</h2></div></div>{data.data?.observations.filter(o => o.agent_id === agent.id).slice(0, 20).map(o => <article className="settings-card" key={o.id}><p>{o.window.start} – {o.window.end}</p>{(!o.coverage.complete || o.window.gap) && <p>Partial coverage or a collection gap.</p>}{o.metrics.map(m => <div className="list-row" key={m.name}><div><strong>{m.name.replaceAll('_', ' ')}: {m.status.replaceAll('_', ' ')}</strong><p>{m.reference ?? 'Unknown'} → {m.current ?? 'Unknown'} · {m.count}/{m.population} current traces scored · {Math.round(m.coverage*100)}% coverage</p><p>Reference: {m.reference_count} scored · {Math.round(m.reference_coverage*100)}% coverage</p>{m.interval && <p>Current estimate interval: {m.interval.map(v => v.toPrecision(4)).join(' – ')}</p>}{m.reference_interval && <p>Reference interval: {m.reference_interval.map(v => v.toPrecision(4)).join(' – ')}</p>}<p>Revisions: {m.cohorts.join(', ') || 'Unknown'}. Observational comparison; causation is not established.</p>{m.limitations.map(l => <p key={l}>{l}</p>)}</div></div>)}{o.limitations.map(l => <p key={l}>{l}</p>)}<Link to={`/projects/${projectId}/tasks/${o.task_id}`}>Inspect observation</Link></article>)}{!data.data?.observations.some(o => o.agent_id === agent.id) && <div className="quiet-surface">No production observation is available yet.</div>}</section>
  </>;
}
