import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api, operationId, post, projectPath } from "./api";
import type {
  AuditResult,
  BaselineResult,
  CandidateEvidence,
  DeliveryResult,
  EvaluationResult,
  MeasurementPlan,
  PatchResult,
  ResultKind,
  RunCandidate,
  RunDecision,
  RunDecisionOperation,
  RunResult,
  ScoreSummary,
  StaticWorkflowResult,
} from "./types";

function HumanStatus({ value }: { value: string }) {
  const tone = ["completed", "verified", "pass", "accepted", "prepared"].includes(value)
    ? "good"
    : ["failed", "rejected", "cancelled"].includes(value)
      ? "bad"
      : ["running", "pending", "final_verification_pending"].includes(value)
        ? "active"
        : "neutral";
  return <span className={`status status-${tone}`}><span aria-hidden="true" />{value.replaceAll("_", " ")}</span>;
}

function formatValue(value: unknown, unit?: string) {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (typeof value === "number") {
    const formatted = Math.abs(value) >= 1000
      ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : value.toLocaleString(undefined, { maximumSignificantDigits: 4 });
    return `${formatted}${unit ? ` ${unit}` : ""}`;
  }
  return String(value).replaceAll("_", " ");
}

function humanKey(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim())) : [];
}

function displayValue(value: unknown) {
  if (Array.isArray(value)) return value.map(String).join(" · ") || "None";
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return formatValue(value);
}

function resultLimitations(result: { task?: { result?: Record<string, unknown> | null } | null }, own: unknown = []) {
  return [...new Set([...stringList(own), ...stringList(result.task?.result?.limitations)])];
}

function ResultLimits({ limits, limitations }: { limits?: Record<string, number>; limitations?: string[] }) {
  if (!Object.keys(limits || {}).length && !limitations?.length) return null;
  return <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Bounds and limitations</p><h4>What this result covers</h4></div></div>{Boolean(Object.keys(limits || {}).length) && <div className="limit-grid">{Object.entries(limits || {}).map(([name, value]) => <div key={name}><span>{humanKey(name)}</span><strong>{formatValue(value)}</strong></div>)}</div>}{Boolean(limitations?.length) && <ul className="plain-list limitation-list">{limitations!.map((item) => <li key={item}>{item}</li>)}</ul>}</section>;
}

export function MeasurementPlanView({ plan }: { plan: MeasurementPlan }) {
  const behaviors = plan.behaviors || [];
  const metrics = Object.entries(plan.metrics || {});
  const scoring = plan.scoring || {};
  const evaluation = plan.evaluation || {};
  return <article className="measurement-proposal" aria-label="Measurement proposal">
    <header className="proposal-header"><div><p className="eyebrow">Measurement proposal</p><h3>How this goal will be judged</h3></div><HumanStatus value={plan.state || "draft"} /></header>
    {plan.background && <section className="proposal-section"><h4>Context</h4><p>{plan.background}</p></section>}
    <section className="proposal-section"><div className="proposal-section-heading"><h4>Expected behavior</h4><span>{behaviors.length} check{behaviors.length === 1 ? "" : "s"}</span></div>
      {behaviors.length ? <div className="behavior-list">{behaviors.map((behavior, index) => <article className="behavior-row" key={behavior.id || index}><span className={behavior.required ? "behavior-required" : "behavior-supporting"}>{behavior.required ? "Required" : "Supporting"}</span><div><strong>{behavior.description || behavior.id || `Behavior ${index + 1}`}</strong>{behavior.check && <p>{behavior.check}</p>}{behavior.rubric && <p>Rubric: {behavior.rubric}</p>}<small>{[behavior.metric, behavior.op, behavior.bound].filter((item) => item !== undefined).map(String).join(" ")}</small>{!!behavior.prerequisites?.length && <small>Requires: {behavior.prerequisites.join(" · ")}</small>}{!!behavior.evidence?.length && <small>Evidence: {behavior.evidence.join(" · ")}</small>}</div></article>)}</div> : <p className="quiet-copy">No separate behavior checks were proposed.</p>}
    </section>
    <section className="proposal-section"><div className="proposal-section-heading"><h4>Measures</h4><span>{metrics.length}</span></div>
      <div className="measurement-table" role="table" aria-label="Proposed measures">{metrics.map(([name, metric]) => <div className="measurement-row" role="row" key={name}><div role="cell"><strong>{humanKey(name)}</strong><p>{metric.description || "No description supplied"}</p>{!!metric.evidence?.length && <small>Evidence: {metric.evidence.join(" · ")}</small>}</div><div role="cell"><span>{metric.direction ? `${humanKey(metric.direction)} is better` : "Direction not set"}</span><small>{[metric.aggregation, metric.unit].filter(Boolean).join(" · ") || "No unit"}</small></div><div role="cell"><span>{metric.weight !== undefined ? `Weight ${formatValue(metric.weight)}` : "Unweighted"}</span><small>Missing: {metric.missing || "not specified"}</small>{!!metric.prerequisites?.length && <small>Requires: {metric.prerequisites.join(" · ")}</small>}</div></div>)}</div>
    </section>
    <div className="proposal-grid">
      <section className="proposal-section"><h4>Decision rule</h4><dl className="compact-definition"><div><dt>Mode</dt><dd>{scoring.mode ? humanKey(scoring.mode) : "Not specified"}</dd></div><div><dt>Primary measure</dt><dd>{scoring.primary || scoring.custom_metric || "Not specified"}</dd></div><div><dt>Target</dt><dd>{formatValue(scoring.target)}</dd></div></dl></section>
      <section className="proposal-section"><h4>Evaluation source</h4><dl className="compact-definition"><div><dt>Plan</dt><dd>{evaluation.mode === "reuse" ? "Reuse reviewed evaluation" : "Create a reviewed evaluation"}</dd></div>{evaluation.evaluation_id && <div><dt>Evaluation</dt><dd><code>{evaluation.evaluation_id}</code></dd></div>}{evaluation.framework && <div><dt>Framework</dt><dd>{evaluation.framework}</dd></div>}{evaluation.entrypoint && <div><dt>Entry point</dt><dd><code>{evaluation.entrypoint}</code></dd></div>}{evaluation.command && <div><dt>Command</dt><dd><code>{evaluation.command}</code></dd></div>}{evaluation.scorer && <div><dt>Scorer</dt><dd>{evaluation.scorer}</dd></div>}{evaluation.dataset_snapshot_id && <div><dt>Dataset</dt><dd><code>{evaluation.dataset_snapshot_id}</code></dd></div>}{evaluation.output_mapping && <div><dt>Output mapping</dt><dd><code>{JSON.stringify(evaluation.output_mapping)}</code></dd></div>}</dl></section>
    </div>
    {!!plan.evidence?.length && <section className="proposal-section"><h4>Evidence used</h4><ul className="plain-list">{plan.evidence.map((item) => <li key={item}>{item}</li>)}</ul></section>}
    {!!plan.limitations?.length && <section className="proposal-section proposal-limits"><h4>Limits to this measurement</h4><ul className="plain-list">{plan.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section>}
  </article>;
}

function ResultOutcome({ title, detail, state }: { title: string; detail: string; state: string }) {
  return <header className="result-outcome"><div><p className="eyebrow">Outcome</p><h3>{title}</h3><p>{detail}</p></div><HumanStatus value={state} /></header>;
}

function AuditResultView({ result }: { result: AuditResult }) {
  const limitations = resultLimitations(result, result.limits);
  const taskState = result.task?.state || result.state;
  const failed = taskState === "failed";
  const issueCount = result.issues.length;
  return <>
    <ResultOutcome title={failed ? "The audit did not complete" : issueCount ? `${issueCount} issue${issueCount === 1 ? "" : "s"} found` : "No audit issues found"} detail={typeof result.task?.result?.summary === "string" ? result.task.result.summary : result.goal || "The requested fixed-rubric audit evidence is available below."} state={taskState} />
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Scope and coverage</p><h4>Evidence reviewed</h4></div></div><dl className="compact-definition"><div><dt>Mode</dt><dd>{result.mode ? humanKey(result.mode) : "Not recorded"}</dd></div><div><dt>Code scope</dt><dd>{result.code_scope || result.code_scopes?.join(" · ") || "No code scope recorded"}</dd></div><div><dt>Trace alignment</dt><dd>{result.trace_alignment?.status ? humanKey(result.trace_alignment.status) : "Not available"}</dd></div>{Object.entries(result.coverage || {}).map(([name, value]) => <div key={name}><dt>{humanKey(name)}</dt><dd>{displayValue(value)}</dd></div>)}</dl>{result.trace_alignment?.warning && <p className="quiet-surface">{result.trace_alignment.warning}</p>}{Boolean(result.changes?.length) && <details className="evidence-detail"><summary>Changed files reviewed ({result.changes!.length})</summary><ul className="plain-list">{result.changes!.map((change, index) => <li key={`${change.path || change.new_path || index}`}><code>{change.path || change.new_path || change.old_path}</code>{change.change_type ? ` · ${humanKey(change.change_type)}` : ""}</li>)}</ul></details>}</section>
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Findings</p><h4>{issueCount ? "Grouped issues" : "No grouped issues"}</h4></div></div>{issueCount ? <div className="attempt-list">{result.issues.map((issue, index) => <article key={issue.issue_id || index}><div><strong>{issue.title || `Issue ${index + 1}`}</strong><HumanStatus value={issue.severity || issue.status || "recorded"} /></div><p>{issue.summary || "Evidence retained without a summary."}</p><small>{issue.findings?.length || 0} finding{issue.findings?.length === 1 ? "" : "s"}{issue.confidence !== undefined ? ` · ${Math.round(issue.confidence * 100)}% confidence` : ""}</small></article>)}</div> : <p className="quiet-surface">The retained audit contains no grouped issue supported by its reviewed evidence.</p>}{Boolean(result.ungrouped_findings?.length) && <details className="attempts"><summary>Ungrouped findings ({result.ungrouped_findings!.length})</summary><ul className="plain-list">{result.ungrouped_findings!.map((finding, index) => <li key={finding.id || index}><strong>{finding.title || `Finding ${index + 1}`}</strong>{finding.summary ? ` — ${finding.summary}` : ""}</li>)}</ul></details>}</section>
    <ResultLimits limits={result.task?.limits} limitations={limitations} />
  </>;
}

function EvaluationResultView({ result }: { result: EvaluationResult }) {
  const metrics = Object.entries(result.metrics || {});
  const trials = result.trials || [];
  const taskState = result.task?.state || result.state;
  const frozen = result.state === "frozen";
  return <>
    <ResultOutcome title={frozen ? "Evaluation is frozen and reviewed" : taskState === "failed" ? "Evaluation preparation failed" : "Evaluation is incomplete"} detail={typeof result.task?.result?.summary === "string" ? result.task.result.summary : result.goal || (frozen ? "This evaluator can be used for a bounded baseline or delivered for review." : "Review the retained checks and remaining action before using this evaluator.")} state={taskState} />
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Evaluation definition</p><h4>Measures and review</h4></div><span>{metrics.length} measure{metrics.length === 1 ? "" : "s"}</span></div><dl className="compact-definition"><div><dt>Evaluation</dt><dd><code>{result.evaluation_id}</code></dd></div><div><dt>Independent review</dt><dd>{result.review_verdict ? humanKey(result.review_verdict) : "Not complete"}</dd></div><div><dt>Review branch</dt><dd>{result.review_branch || "Not prepared"}</dd></div>{Object.entries(result.coverage || {}).map(([name, value]) => <div key={name}><dt>{humanKey(name)}</dt><dd>{displayValue(value)}</dd></div>)}</dl>{metrics.length ? <div className="measurement-table" role="table" aria-label="Evaluation measures">{metrics.map(([name, metric]) => <div className="measurement-row" role="row" key={name}><div role="cell"><strong>{humanKey(name)}</strong><p>{metric.description || "No description supplied"}</p></div><div role="cell"><span>{metric.direction ? `${humanKey(metric.direction)} is better` : "Direction not set"}</span><small>{metric.aggregation || "Aggregation not set"}</small></div><div role="cell"><span>{metric.unit || "No unit"}</span><small>Missing: {metric.missing || "not specified"}</small></div></div>)}</div> : <p className="quiet-surface">No accepted metric definition is retained yet.</p>}</section>
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Validation</p><h4>Retained trials</h4></div><span>{trials.length}</span></div>{trials.length ? <div className="attempt-list">{trials.map((trial, index) => <article key={trial.trial_id || index}><div><strong>{trial.case_id || `Trial ${index + 1}`}</strong><HumanStatus value={trial.state || "recorded"} /></div><p>{trial.kind ? humanKey(trial.kind) : "Evaluation case"}{trial.outcome ? ` · ${humanKey(trial.outcome)}` : ""}</p>{trial.error && <small>{trial.error}</small>}</article>)}</div> : <p className="quiet-surface">No validation trials are available.</p>}</section>
    {result.metric_comparison_error && <p className="error-banner">{result.metric_comparison_error}</p>}
    <ResultLimits limits={result.task?.limits || result.budget} limitations={resultLimitations(result)} />
  </>;
}

function scoreSummary(value: BaselineResult["benchmark_score"]): ScoreSummary {
  if (typeof value === "number") return { state: "measured", score: value };
  return value && typeof value === "object" ? value : { state: "unmeasured", score: null };
}

function BaselineResultView({ result }: { result: BaselineResult }) {
  const benchmark = scoreSummary(result.benchmark_score);
  const recent = result.recent_traces || {};
  const traceScore = recent.score || { state: "unmeasured", score: null };
  const taskState = result.task?.state || result.state;
  const incomplete = taskState === "failed" || !["completed", "complete", "measured", "ready"].includes(result.state);
  return <>
    <ResultOutcome title={taskState === "failed" ? "Baseline measurement failed" : incomplete ? "Baseline is incomplete" : "Baseline is ready"} detail={typeof result.task?.result?.summary === "string" ? result.task.result.summary : result.pending_action || "The frozen evaluation and source identity define this comparison point."} state={taskState} />
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Controlled baseline</p><h4>Evaluation and source</h4></div></div><dl className="compact-definition"><div><dt>Evaluation</dt><dd>{result.evaluation_id ? <code>{result.evaluation_id}</code> : "Unavailable"}</dd></div><div><dt>Source revision</dt><dd>{result.source_revision ? <code>{result.source_revision}</code> : "Unavailable"}</dd></div><div><dt>Execution run</dt><dd>{result.execution_run_id ? <code>{result.execution_run_id}</code> : "Unavailable"}</dd></div><div><dt>Profile</dt><dd>{result.profile_name || "Not recorded"}</dd></div></dl></section>
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Measurements</p><h4>Benchmark and recent traces</h4></div></div><div className="result-comparison" role="table" aria-label="Baseline measurements"><div className="comparison-head" role="row"><span role="columnheader">Population</span><span role="columnheader">State</span><span role="columnheader">Score</span><span role="columnheader">Samples</span></div><div className="comparison-row" role="row"><strong role="cell">Controlled benchmark</strong><span role="cell">{humanKey(benchmark.state || "unmeasured")}</span><span role="cell">{formatValue(benchmark.score)}</span><span role="cell">{formatValue(benchmark.measured_count)}</span></div><div className="comparison-row" role="row"><strong role="cell">Recent traces</strong><span role="cell">{humanKey(traceScore.state || recent.state || "unmeasured")}</span><span role="cell">{formatValue(traceScore.score ?? traceScore.value)}</span><span role="cell">{formatValue(recent.count)}</span></div></div><dl className="compact-definition"><div><dt>Provider project</dt><dd>{[recent.provider, recent.project].filter(Boolean).join(" · ") || "Not connected"}</dd></div><div><dt>Completeness</dt><dd>{recent.completeness ? humanKey(recent.completeness) : "Unknown"}</dd></div><div><dt>Source alignment</dt><dd>{recent.alignment ? humanKey(recent.alignment) : "Unknown"}</dd></div></dl>{recent.next_action && <p className="quiet-surface">{recent.next_action}</p>}</section>
    <ResultLimits limits={result.task?.limits} limitations={resultLimitations(result)} />
  </>;
}

function PatchResultView({ result }: { result: PatchResult }) {
  const reviewed = result.state === "reviewed_unmeasured" && result.independent_review === "pass";
  const taskState = result.task?.state || result.state;
  return <>
    <ResultOutcome title={reviewed ? "Reviewed unmeasured patch" : taskState === "failed" ? "Patch preparation failed" : "Patch review is incomplete"} detail={typeof result.task?.result?.summary === "string" ? result.task.result.summary : result.goal || "This change has no controlled baseline comparison and is not a measured improvement."} state={taskState} />
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Change</p><h4>Reviewed scope</h4></div></div><dl className="compact-definition"><div><dt>Tested source</dt><dd>{result.source_revision ? <code>{result.source_revision}</code> : "Not recorded"}</dd></div><div><dt>Measurement</dt><dd>Unavailable</dd></div><div><dt>Independent review</dt><dd>{result.independent_review ? humanKey(result.independent_review) : "Not complete"}</dd></div></dl>{result.editable_paths?.length ? <ul className="plain-list">{result.editable_paths.map((path) => <li key={path}><code>{path}</code></li>)}</ul> : <p className="quiet-surface">No editable paths are exposed.</p>}</section>
    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Checks</p><h4>Recorded verification</h4></div></div>{result.checks?.length ? <ul className="check-list">{result.checks.map((check) => <li key={check.id} className={check.state === "passed" ? "check-pass" : check.state === "failed" ? "check-fail" : "check-unknown"}><span>{check.state === "passed" ? "✓" : check.state === "failed" ? "×" : "?"}</span><div><strong>{humanKey(check.id)}</strong><small>{humanKey(check.state)}</small></div></li>)}</ul> : <p className="quiet-surface">No checks are retained.</p>}</section>
    <ResultLimits limits={result.task?.limits} limitations={resultLimitations(result, result.limitations)} />
  </>;
}

export function StaticResultView({ projectId, workflow, resultId }: { projectId: string; workflow: Exclude<ResultKind, "fix" | "optimize">; resultId: string }) {
  const path = projectPath(projectId, `/results/${workflow}/${encodeURIComponent(resultId)}`);
  const result = useQuery({ queryKey: ["projects", projectId, "results", workflow, resultId], queryFn: ({ signal }) => api<StaticWorkflowResult>(path, { signal }) });
  const [prepared, setPrepared] = useState<DeliveryResult>();
  const deliver = useMutation({ mutationFn: () => post<DeliveryResult>(projectPath(projectId, "/deliveries"), { kind: workflow, source_id: resultId, publish: false }), onSuccess: setPrepared });
  if (result.isLoading) return <section className="result-workspace"><p className="quiet-copy">Loading result evidence…</p></section>;
  if (result.isError) return <section className="result-workspace"><p className="error-banner">{result.error.message}</p></section>;
  if (!result.data || result.data.result_kind !== workflow) return <section className="result-workspace"><p className="error-banner">This result does not match the requested workflow.</p></section>;
  const data = result.data;
  const canDeliver = "allowed_actions" in data && data.allowed_actions?.includes("prepare_local_delivery");
  const existingDelivery = "deliveries" in data
    ? data.deliveries?.find((item) => item.state === "prepared" || item.state === "published")
    : undefined;
  const artifactUrls = prepared?.artifact_urls || existingDelivery?.artifact_urls;
  return <section className="result-workspace" aria-label="Task result">
    {data.result_kind === "audit" && <AuditResultView result={data} />}
    {data.result_kind === "eval" && <EvaluationResultView result={data} />}
    {data.result_kind === "baseline" && <BaselineResultView result={data} />}
    {data.result_kind === "patch" && <PatchResultView result={data} />}
    {data.task?.next_action && <section className="result-section"><p className="eyebrow">Next supported action</p><h4>{data.task.next_action}</h4></section>}
    {canDeliver && <section className="delivery-card"><div><p className="eyebrow">Local delivery</p><h4>{workflow === "eval" ? "Prepare the reviewed evaluator" : "Prepare the reviewed patch"}</h4><p>Creates reviewable local artifacts. It does not merge, push, deploy, or turn an unmeasured patch into a measured improvement.</p></div>{artifactUrls && Object.keys(artifactUrls).length ? <div className="artifact-actions">{Object.entries(artifactUrls).map(([name, url]) => <a className="button button-secondary" href={url} key={name} download>Download {humanKey(name)}</a>)}</div> : <button className="button button-primary" disabled={deliver.isPending} onClick={() => deliver.mutate()}>{deliver.isPending ? "Preparing…" : "Prepare local delivery"}</button>}</section>}
    {deliver.error && <p className="error-banner">{deliver.error.message}</p>}
  </section>;
}

function comparisonMetrics(baseline: RunCandidate | undefined, candidate: RunCandidate | undefined) {
  const names = new Set([
    ...Object.keys(baseline?.metrics || {}),
    ...Object.keys(candidate?.metrics || {}),
    ...Object.keys(baseline?.task_metrics || {}),
    ...Object.keys(candidate?.task_metrics || {}),
  ]);
  return [...names].map((name) => ({
    name,
    baseline: baseline?.metrics?.[name] ?? baseline?.task_metrics?.[name],
    candidate: candidate?.metrics?.[name] ?? candidate?.task_metrics?.[name],
    change: candidate?.variation?.[name] ?? candidate?.task_variation?.[name],
  }));
}

function candidateVerification(candidate?: RunCandidate) {
  if (!candidate) return { passed: 0, failed: 0, unknown: 0 };
  const values = candidate.checks || [];
  return {
    passed: values.filter((item) => item.passed === true).length,
    failed: values.filter((item) => item.passed === false).length,
    unknown: values.filter((item) => item.passed !== true && item.passed !== false).length,
  };
}

type RunDelivery = NonNullable<RunResult["deliveries"]>[number] | DeliveryResult;

export function deliveryMatchesDecision(delivery: RunDelivery, decision: RunDecision | null, candidate?: RunCandidate) {
  return Boolean(
    decision?.current !== false
    && decision?.decision === "select_candidate"
    && decision.id
    && decision.candidate_id
    && candidate?.source_revision
    && delivery.state && ["prepared", "published"].includes(delivery.state)
    && delivery.user_decision_id === decision.id
    && delivery.user_decision_revision === decision.revision
    && delivery.candidate_id === decision.candidate_id
    && delivery.source_revision === candidate.source_revision
    && (!decision.source_revision || delivery.source_revision === decision.source_revision)
  );
}

function outcomeCopy(run: RunResult, taskState?: string) {
  if (taskState === "failed") return { title: "The task failed", detail: "No change is ready to select. The retained attempts and limits below show what was established." };
  if (taskState === "cancelled") return { title: "The task was cancelled", detail: "No new decision is available. Retained attempts and evidence remain inspectable below." };
  if (taskState === "interrupted") return { title: "The task was interrupted", detail: "Resume the task before treating any partial candidate as a verified result." };
  if (["queued", "running", "needs_input"].includes(taskState || "")) return { title: "Verification is still in progress", detail: "Partial evidence is visible, but no candidate can be selected until the task completes its required checks." };
  if (run.comparisons.result === "verification_incomplete") return { title: "Verification evidence changed", detail: "No affected candidate can be selected until its exact source and retained evidence are verified again." };
  if (run.comparisons.result === "final_verification_pending") return { title: "Verification is incomplete", detail: "Candidate measurements exist, but final independent verification has not finished." };
  if (run.comparisons.result === "baseline_retained") return { title: "No verified improvement", detail: "The current version remains the strongest verified result under this measurement." };
  if (taskState === "completed_with_limits") return { title: "A verified improvement was found with limits", detail: "Review the comparison and recorded limits before choosing what to keep." };
  return { title: "A verified improvement is ready", detail: "Agentagon recommends a measured candidate. You still choose whether to use it." };
}

export function RunResultView({ projectId, workflow, runId, taskState, taskResult }: { projectId: string; workflow: "fix" | "optimize"; runId: string; taskState?: string; taskResult?: Record<string, unknown> | null }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const path = projectPath(projectId, `/results/${workflow}/${encodeURIComponent(runId)}`);
  const run = useQuery({ queryKey: ["projects", projectId, "results", workflow, runId], queryFn: ({ signal }) => api<RunResult>(path, { signal }) });
  const [prepared, setPrepared] = useState<DeliveryResult>();
  const data = run.data;
  const fixedRepairId = data?.selection?.engine_selected_candidate_id || data?.selected_candidate_id || undefined;
  const recommendedId = workflow === "fix" ? fixedRepairId : data?.selection?.recommended_candidate_id || data?.comparisons.alternatives[0]?.id;
  const decision = data?.selection?.decision || null;
  const decisionOperation = data?.selection?.decision_operation || null;
  const chosenId = decision?.current !== false && decision?.decision === "select_candidate" ? decision.candidate_id : null;
  const eligibleCandidates = data?.comparisons.alternatives || [];
  const recommended = eligibleCandidates.find((candidate) => candidate.id === recommendedId) || eligibleCandidates[0];
  const chosen = data?.candidates.find((candidate) => candidate.id === chosenId);
  const inspectedCandidateId = search.get("candidate_id") || undefined;
  const inspected = eligibleCandidates.find((candidate) => candidate.id === inspectedCandidateId);
  const baseline = data?.candidates.find((candidate) => candidate.id === data.baseline_id);
  const focus = inspected || chosen || recommended;
  const metrics = useMemo(() => comparisonMetrics(baseline, focus), [baseline, focus]);
  const checks = candidateVerification(focus);
  const evidence = useQuery({
    queryKey: ["projects", projectId, "runs", runId, "candidates", focus?.id],
    queryFn: ({ signal }) => api<CandidateEvidence>(projectPath(projectId, `/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(focus!.id)}`), { signal }),
    enabled: Boolean(focus?.id && focus.id !== data?.baseline_id),
  });
  const decide = useMutation({
    mutationFn: ({ action, candidateId, retainedOperationId, expectedRevision }: { action: "select_candidate" | "keep_current"; candidateId?: string | null; retainedOperationId?: string; expectedRevision?: number }) => post<RunDecision | RunDecisionOperation>(`${path}/decision`, {
      operation_id: retainedOperationId || operationId(),
      expected_revision: expectedRevision ?? data?.selection?.expected_revision ?? decision?.revision ?? 0,
      decision: action,
      ...(candidateId ? { candidate_id: candidateId } : {}),
    }),
    onSuccess: async (_result, variables) => {
      setPrepared(undefined);
      if (variables.candidateId) {
        const next = new URLSearchParams(search);
        next.set("candidate_id", variables.candidateId);
        setSearch(next, { replace: true });
      }
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "production"] });
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "results", workflow, runId] });
    },
  });
  const deliver = useMutation({
    mutationFn: () => post<DeliveryResult>(projectPath(projectId, "/deliveries"), { kind: workflow, source_id: runId, publish: false }),
    onSuccess: async (result) => {
      setPrepared(result);
      await queryClient.invalidateQueries({ queryKey: ["projects", projectId, "results", workflow, runId] });
    },
  });
  if (run.isLoading) return <section className="result-workspace"><p className="quiet-copy">Loading measured result…</p></section>;
  if (run.isError) return <section className="result-workspace"><p className="error-banner">{run.error.message}</p></section>;
  if (!data) return null;
  const effectiveTaskState = data.task?.state || taskState;
  const effectiveTaskResult = data.task?.result || taskResult;
  const outcome = outcomeCopy(data, effectiveTaskState);
  const taskLimits = stringList(effectiveTaskResult?.limitations);
  const verificationLimits = typeof effectiveTaskResult?.verification === "object" && effectiveTaskResult.verification !== null
    ? stringList((effectiveTaskResult.verification as Record<string, unknown>).limitations)
    : [];
  const limitations = [...new Set([...stringList(data.limitations), ...taskLimits, ...verificationLimits])];
  const eligibleIds = new Set(eligibleCandidates.map((candidate) => candidate.id));
  const otherAttempts = data.candidates.filter((candidate) => candidate.id !== data.baseline_id && !eligibleIds.has(candidate.id));
  const displayedLimits = { ...(data.task?.limits || {}), ...(data.limits || {}) };
  const allowed = new Set(data.selection?.allowed_actions || []);
  const activeDecisionOperation = decisionOperation?.persisted_state === "pending" ? decisionOperation : null;
  const resumeDecision = () => {
    if (!activeDecisionOperation) return;
    decide.mutate({
      action: activeDecisionOperation.decision,
      candidateId: activeDecisionOperation.candidate_id,
      retainedOperationId: activeDecisionOperation.operation_id,
      expectedRevision: activeDecisionOperation.expected_revision,
    });
  };
  const decisionCopy = activeDecisionOperation?.state === "stale_pending"
    ? {
        title: "Your saved decision no longer matches this result",
        detail: "The evidence changed before the receipt was finalized. Clear the stale operation, then review the current result and choose again.",
        action: "Clear stale decision",
      }
    : activeDecisionOperation
      ? {
          title: "Your saved decision is waiting to finish",
          detail: "Agentagon retained the exact candidate and evidence you chose. Resume this saved operation instead of creating another decision.",
          action: "Resume saved decision",
        }
      : decisionOperation?.state === "stale"
        ? {
            title: "The previous decision was not applied",
            detail: "The evidence changed before Agentagon could record a receipt. Review the current result and choose again.",
            action: null,
          }
        : !allowed.has("select_candidate") && !allowed.has("keep_current") && !decision
          ? {
              title: "No decision is available yet",
              detail: "Verification did not establish an eligible change. Inspect the retained evidence and supported next action.",
              action: null,
            }
          : {
            title: decision?.current === false
              ? "Your earlier decision is stale"
              : decision?.decision === "keep_current"
                ? "Current version selected by you"
                : decision?.decision === "select_candidate"
                  ? "Change selected by you"
                  : "Your decision is still open",
            detail: decision?.current === false
              ? "The measured run changed. Review this result and choose again."
              : decision
                ? `Recorded ${decision.decided_at ? new Date(decision.decided_at).toLocaleString() : "for this result"}.`
                : "The task recommendation does not change your project until you choose.",
            action: null,
          };
  const selectedCandidate = data.candidates.find((candidate) => candidate.id === decision?.candidate_id);
  const currentDelivery = [prepared, ...(data.deliveries || [])]
    .filter((item): item is RunDelivery => Boolean(item))
    .find((item) => deliveryMatchesDecision(item, decision, selectedCandidate));
  const artifacts = currentDelivery?.artifact_urls;
  const previousDeliveries = (data.deliveries || []).filter((item) => item.delivery_id !== currentDelivery?.delivery_id);
  const inspectCandidate = (candidateId: string) => {
    const next = new URLSearchParams(search);
    next.set("candidate_id", candidateId);
    setSearch(next, { replace: true });
  };
  return <section className="result-workspace" aria-label="Task result">
    <header className="result-outcome"><div><p className="eyebrow">Measured outcome</p><h3>{outcome.title}</h3><p>{typeof effectiveTaskResult?.summary === "string" ? effectiveTaskResult.summary : outcome.detail}</p>{typeof effectiveTaskResult?.summary === "string" && <small>{outcome.detail}</small>}</div><HumanStatus value={effectiveTaskState || data.state} /></header>

    {!!eligibleCandidates.length && <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">{workflow === "fix" ? "Verified repair" : "Verified candidates"}</p><h4>{workflow === "fix" ? "Choose whether to use the repair verified by this task" : "Choose from every eligible alternative"}</h4></div><span>{eligibleCandidates.length}</span></div><div className="verified-candidate-list">{eligibleCandidates.map((candidate) => { const selected = decision?.current !== false && decision?.decision === "select_candidate" && decision.candidate_id === candidate.id; const engineSelectedRepair = workflow === "fix" && candidate.id === fixedRepairId; const recommendedCandidate = workflow === "optimize" && candidate.id === recommended?.id; const canSelect = allowed.has("select_candidate") && (workflow === "optimize" || engineSelectedRepair); return <article className={`result-decision-card ${focus?.id === candidate.id ? "is-focused" : ""}`} id={`candidate-${candidate.id}`} key={candidate.id}><div className="decision-marker">{engineSelectedRepair ? "Task's verified repair" : recommendedCandidate ? "Recommendation" : workflow === "fix" ? "Additional verified evidence" : "Verified alternative"}</div><div><h4>{candidate.hypothesis || "Verified candidate"}</h4><p>{engineSelectedRepair ? "This is the focused repair selected and independently verified by the Fix task." : recommendedCandidate ? "Recommended by the task from the accepted measurement and independent verification." : workflow === "fix" ? "You can inspect this evidence, but Fix can only select the repair it completed and verified." : "Eligible under the same accepted measurement and verification gates."}</p><div className="candidate-meta"><HumanStatus value={candidate.display_state || candidate.state} />{candidate.source_revision && <code title={candidate.source_revision}>{candidate.source_revision.slice(0, 10)}</code>}{candidate.review_verdict && <span>Review: {candidate.review_verdict}</span>}</div></div><div className="artifact-actions"><button className="button button-secondary" type="button" aria-pressed={focus?.id === candidate.id} onClick={() => inspectCandidate(candidate.id)}>{focus?.id === candidate.id ? "Inspecting" : "Inspect candidate"}</button>{selected ? <div className="user-choice"><span>✓</span><strong>Selected by you</strong></div> : canSelect && <button className="button button-primary" disabled={decide.isPending} onClick={() => decide.mutate({ action: "select_candidate", candidateId: candidate.id })}>{workflow === "fix" ? "Use this repair" : "Select this candidate"}</button>}</div></article>; })}</div></section>}

    <div className="result-choice-bar"><div><strong>{decisionCopy.title}</strong><p>{decisionCopy.detail}</p></div>{decisionCopy.action && <button className="button button-primary" disabled={decide.isPending} onClick={resumeDecision}>{decide.isPending ? "Working…" : decisionCopy.action}</button>}{!activeDecisionOperation && allowed.has("keep_current") && (decision?.decision !== "keep_current" || decision.current === false) && <button className="button button-secondary" disabled={decide.isPending} onClick={() => decide.mutate({ action: "keep_current" })}>Keep current version</button>}</div>
    {decide.error && <p className="error-banner">{decide.error.message}</p>}

    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Comparison</p><h4>Current versus {focus ? focus.id === chosen?.id ? "selected" : "inspected" : "retained baseline"}</h4></div>{data.comparisons.baseline_score !== undefined && <span>Baseline score {formatValue(data.comparisons.baseline_score)}</span>}</div>
      {metrics.length ? <div className="result-comparison" role="table" aria-label="Candidate comparison"><div className="comparison-head" role="row"><span role="columnheader">Measure</span><span role="columnheader">Current</span><span role="columnheader">Candidate</span><span role="columnheader">Change</span></div>{metrics.map((metric) => <div className="comparison-row" role="row" key={metric.name}><strong role="cell">{humanKey(metric.name)}</strong><span role="cell">{formatValue(metric.baseline)}</span><span role="cell">{formatValue(metric.candidate)}</span><span role="cell">{metric.change === undefined || metric.change === null ? "—" : `${metric.change > 0 ? "+" : ""}${formatValue(metric.change)}`}</span></div>)}</div> : <p className="quiet-surface">{focus ? "This candidate has no comparable numeric measures. Its retained verification evidence is shown below." : "No eligible verified candidate is available for comparison. Inspect the retained attempts and limitations below."}</p>}
    </section>

    {focus && <div className="result-columns"><section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Change</p><h4>{focus.hypothesis || "Candidate change"}</h4></div></div><dl className="compact-definition"><div><dt>Revision</dt><dd>{focus.source_revision ? <code>{focus.source_revision}</code> : "Not sealed"}</dd></div><div><dt>Branch</dt><dd>{focus.branch || "Prepared worktree"}</dd></div></dl>{evidence.data?.diff && <details className="evidence-detail"><summary>Inspect code diff{evidence.data.diff.truncated ? " (truncated)" : ""}</summary><pre>{evidence.data.diff.text || "No source changes."}</pre></details>}</section>
      <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Verification</p><h4>{focus.review_verdict === "pass" ? "Independent review passed" : focus.review_verdict === "reject" ? "Independent review rejected" : "Recorded checks"}</h4></div></div><div className="verification-counts"><span><strong>{checks.passed}</strong> passed</span><span><strong>{checks.failed}</strong> failed</span><span><strong>{checks.unknown}</strong> unknown</span></div>{!!focus.checks?.length && <ul className="check-list">{focus.checks.map((check) => <li key={check.id} className={check.passed === true ? "check-pass" : check.passed === false ? "check-fail" : "check-unknown"}><span>{check.passed === true ? "✓" : check.passed === false ? "×" : "?"}</span><div><strong>{humanKey(check.id)}</strong>{!!check.issue_ids?.length && <small>Issues: {check.issue_ids.join(", ")}</small>}</div></li>)}</ul>}{!!focus.constraints?.length && <ul className="constraint-list">{focus.constraints.map((constraint, index) => <li key={`${constraint.metric || "constraint"}-${index}`}><span>{constraint.passed === true ? "✓" : constraint.passed === false ? "×" : "?"}</span><div><strong>{humanKey(constraint.metric || "Constraint")}</strong><small>{[constraint.op, constraint.bound ?? constraint.threshold, "actual", constraint.actual].filter((item) => item !== undefined).map(String).join(" ")}</small></div></li>)}</ul>}</section></div>}

    <section className="result-section"><div className="result-section-heading"><div><p className="eyebrow">Run limits</p><h4>What this result covers</h4></div></div><div className="limit-grid">{Object.entries(displayedLimits).map(([name, limit]) => { const used = data.usage?.[name.replace("max_", "")] ?? data.usage?.[name]; return <div key={name}><span>{humanKey(name)}</span><strong>{used === undefined ? formatValue(limit) : `${formatValue(used)} / ${formatValue(limit)}`}</strong></div>; })}</div>{limitations.length ? <ul className="plain-list limitation-list">{limitations.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="quiet-copy">No additional task limitations were reported. Verification still applies only to the tested revision and measurement.</p>}{data.task?.next_action && <p className="quiet-surface">Next supported action: {data.task.next_action}</p>}</section>

    {!!otherAttempts.length && <details className="result-section attempts"><summary>Other attempts ({otherAttempts.length})</summary><div className="attempt-list">{otherAttempts.map((candidate) => <article key={candidate.id}><div><strong>{candidate.hypothesis || candidate.id}</strong><HumanStatus value={candidate.display_state || candidate.state} /></div><p>{candidate.invalidated ? "Invalidated by later evidence" : candidate.feasible ? "Verified, but not eligible under the final comparison" : "Did not qualify as a verified improvement"}</p></article>)}</div></details>}

    {decision?.current !== false && decision?.decision === "select_candidate" && allowed.has("prepare_local_delivery") && <section className="delivery-card"><div><p className="eyebrow">Local delivery</p><h4>Prepare the selected change</h4><p>Creates a reviewable local patch and summary. It does not merge, push, or deploy anything.</p></div>{artifacts ? <div className="artifact-actions">{Object.entries(artifacts).map(([name, url]) => <a className="button button-secondary" href={url} key={name} download>{name === "diff" ? "Download patch" : name === "summary" ? "Download summary" : `Download ${humanKey(name)}`}</a>)}</div> : <button className="button button-primary" disabled={deliver.isPending} onClick={() => deliver.mutate()}>{deliver.isPending ? "Preparing…" : "Prepare local delivery"}</button>}</section>}
    {!!previousDeliveries.length && <details className="result-section attempts"><summary>Earlier local packages ({previousDeliveries.length})</summary><div className="attempt-list">{previousDeliveries.map((delivery) => <article key={delivery.delivery_id}><div><strong>{delivery.candidate_id === decision?.candidate_id ? "Previous decision package" : "Earlier candidate package"}</strong><HumanStatus value={delivery.state || "recorded"} /></div><p>{delivery.candidate_id ? <>Candidate <code>{delivery.candidate_id}</code></> : "Candidate identity unavailable"}{delivery.source_revision && <> · revision <code>{delivery.source_revision.slice(0, 12)}</code></>}</p><small>This package remains in history and does not represent the current decision.</small></article>)}</div></details>}
    {deliver.error && <p className="error-banner">{deliver.error.message}</p>}
  </section>;
}
