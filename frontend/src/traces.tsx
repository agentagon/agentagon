import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api, post, projectPath } from "./api";
import { Button, LaunchWorkflowModal, Modal, PageHeader, Status } from "./components";
import { useWorkflows } from "./hooks";

type Limitation = { code: string; message: string };
type TraceSummary = {
  trace_id: string;
  name?: string | null;
  span_count: number;
  error_count: number;
  started_ns?: number | null;
  ended_ns?: number | null;
  environment?: unknown;
  release?: unknown;
  complete: boolean;
  missing_parent_count: number;
};
type TraceSpan = {
  id: string;
  span_id: string;
  parent_span_ids: string[];
  root: boolean;
  depth?: number | null;
  name?: string | null;
  kind?: string;
  operation?: string | null;
  status: string;
  started_ns?: number | null;
  ended_ns?: number | null;
  duration_ns?: number | null;
  model?: string | null;
  session_id?: string | null;
  tool_call_id?: string | null;
  usage?: unknown;
  error?: unknown;
  input?: unknown;
  output?: unknown;
  messages?: unknown;
  metadata?: unknown;
  attributes?: unknown;
  events?: unknown;
  content_truncated: boolean;
  redacted_value_count: number;
  record_locator: string;
};
type TraceDetail = {
  snapshot_id: string;
  source: {
    provider?: string | null;
    provider_project?: string | null;
    created_at?: string | null;
    connection_id?: string | null;
    source_snapshot_id?: string | null;
    selection?: Record<string, unknown>;
  };
  readiness: {
    state: string;
    diagnosis_ready: boolean;
    viewer_ready: boolean;
    selection_required: boolean;
    blockers: Limitation[];
    limitations: Limitation[];
  };
  coverage: {
    source_records?: number | null;
    normalized_spans: number;
    trace_count: number;
    incomplete_traces: number;
    returned_spans: number;
    omitted_spans: number;
    normalization_failures: number;
    source_complete: boolean;
  };
  handling: {
    redaction: { redacted_value_count: number; limitation: string };
    normalization: { limitation: string };
  };
  traces: TraceSummary[];
  selected_trace_id?: string | null;
  trace?: TraceSummary | null;
  timeline: { spans: TraceSpan[]; total_spans: number; returned_spans: number; omitted_spans: number };
  errors: Array<{ span_id: string; name?: string | null; status: string; error?: unknown }>;
  omitted_errors: number;
  diagnostics: Array<{ locator: string; code: string; message: string }>;
};

type TraceIssue = {
  issue_id: string;
  title: string;
  summary: string;
  status: string;
  severity: string;
  occurrences?: Array<{
    source_id?: string;
    source_ids?: string[];
    trace_ids?: string[];
  }>;
};

type ProposedCase = {
  id: string;
  name: string;
  count: number;
  missing_expectations: number;
};

function duration(value?: number | null) {
  if (value === null || value === undefined) return "Unknown duration";
  const milliseconds = value / 1_000_000;
  if (milliseconds < 1) return `${Math.round(value / 1_000)} µs`;
  if (milliseconds < 1000) return `${milliseconds.toFixed(milliseconds < 10 ? 2 : 1)} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

function time(value?: number | null) {
  if (value === null || value === undefined) return "Unknown time";
  return new Date(value / 1_000_000).toLocaleString();
}

function DataBlock({ title, value }: { title: string; value: unknown }) {
  if (value === undefined || value === null || value === "") return null;
  return <details className="trace-data"><summary>{title}</summary><pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre></details>;
}

export function TracePage({ projectId }: { projectId: string }) {
  const { snapshotId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const requestedTrace = search.get("trace_id") || undefined;
  const intent = search.get("intent") === "fix" ? "fix" : "discover";
  const workflows = useWorkflows();
  const [selectedSpanId, setSelectedSpanId] = useState<string>();
  const [launchSnapshot, setLaunchSnapshot] = useState<string>();
  const [evaluationCaseOpen, setEvaluationCaseOpen] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const detail = useQuery({
    queryKey: ["projects", projectId, "traces", snapshotId, requestedTrace],
    queryFn: ({ signal }) => api<TraceDetail>(projectPath(projectId, `/traces/${encodeURIComponent(snapshotId)}${requestedTrace ? `?trace_id=${encodeURIComponent(requestedTrace)}` : ""}`), { signal }),
  });
  const issues = useQuery({
    queryKey: ["projects", projectId, "issues"],
    queryFn: ({ signal }) => api<{ issues: TraceIssue[] }>(projectPath(projectId, "/issues"), { signal }),
  });
  const selected = useMemo(
    () => detail.data?.timeline.spans.find((span) => span.span_id === selectedSpanId) || detail.data?.timeline.spans[0],
    [detail.data, selectedSpanId],
  );
  useEffect(() => {
    setSelectedSpanId(detail.data?.timeline.spans[0]?.span_id);
  }, [detail.data?.selected_trace_id]);
  const chooseTrace = (traceId: string) => {
    const next = new URLSearchParams(search);
    next.set("trace_id", traceId);
    setSearch(next);
  };
  const selectTrace = useMutation({
    mutationFn: () => detail.data!.traces.length > 1
      ? post<{ id: string }>(projectPath(projectId, `/traces/${encodeURIComponent(snapshotId)}/select`), { trace_id: detail.data!.selected_trace_id })
      : Promise.resolve({ id: snapshotId }),
    onSuccess: (snapshot) => setLaunchSnapshot(snapshot.id),
  });
  const workflow = workflows.data?.workflows.find((item) => item.workflow === intent);
  const title = detail.data?.trace?.name || (detail.data?.readiness.selection_required ? "Choose a trace" : "Trace evidence");
  const relatedIssues = useMemo(() => {
    const traceId = detail.data?.selected_trace_id;
    if (!traceId) return [];
    return (issues.data?.issues || []).filter((issue) => issue.occurrences?.some((occurrence) => {
      const sources = [occurrence.source_id, ...(occurrence.source_ids || [])];
      return occurrence.trace_ids?.includes(traceId) && sources.some((source) => source === snapshotId || source === detail.data?.source.source_snapshot_id);
    }));
  }, [detail.data?.selected_trace_id, detail.data?.source.source_snapshot_id, issues.data?.issues, snapshotId]);

  const copyTraceId = async () => {
    const traceId = detail.data?.selected_trace_id;
    if (!traceId) return;
    try {
      await navigator.clipboard.writeText(traceId);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.setTimeout(() => setCopyState("idle"), 1600);
  };

  return <>
    <PageHeader title={title} actions={<><Link className="button button-secondary" to={`/projects/${projectId}/issues`}>Back to issues</Link>{detail.data?.selected_trace_id && <Button tone="secondary" onClick={copyTraceId}>{copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy trace ID"}</Button>}{detail.data?.readiness.viewer_ready && detail.data.selected_trace_id && <Button tone="secondary" onClick={() => setEvaluationCaseOpen(true)}>Use as evaluation case</Button>}{detail.data?.readiness.diagnosis_ready && detail.data.selected_trace_id && <Button disabled={selectTrace.isPending} onClick={() => selectTrace.mutate()}>{selectTrace.isPending ? "Freezing selection…" : intent === "fix" ? "Continue to fix" : "Investigate trace"}</Button>}</>}>
      <p>{detail.data?.source.provider || "Imported"}{detail.data?.source.provider_project ? ` · ${detail.data.source.provider_project}` : ""}</p>
    </PageHeader>
    {detail.isLoading && <p className="quiet-surface">Reading normalized trace evidence…</p>}
    {detail.isError && <p className="error-banner">{detail.error.message}</p>}
    {detail.data && <>
      <section className="trace-readiness"><div><Status value={detail.data.readiness.state} /><strong>{detail.data.coverage.normalized_spans} usable spans across {detail.data.coverage.trace_count} trace{detail.data.coverage.trace_count === 1 ? "" : "s"}</strong><p>{detail.data.readiness.diagnosis_ready ? "Evidence can be used for bounded diagnosis." : "Diagnosis is unavailable until the import contains usable spans."}</p></div><dl><div><dt>Imported</dt><dd>{detail.data.source.created_at ? new Date(detail.data.source.created_at).toLocaleString() : "Unknown"}</dd></div><div><dt>Coverage</dt><dd>{detail.data.coverage.source_complete ? "Source reported complete" : "Partial or bounded"}</dd></div><div><dt>Redaction</dt><dd>{detail.data.handling.redaction.redacted_value_count} values marked</dd></div></dl></section>
      {(detail.data.readiness.blockers.length > 0 || detail.data.readiness.limitations.length > 0) && <section className="trace-limits"><h2>Evidence limits</h2><ul>{[...detail.data.readiness.blockers, ...detail.data.readiness.limitations].map((item, index) => <li key={`${item.code}-${index}`}><strong>{item.code.replaceAll("_", " ")}</strong><span>{item.message}</span></li>)}</ul></section>}
      {detail.data.traces.length > 1 && <section className="trace-picker"><div className="section-heading"><div><h2>Traces in this import</h2><p>Select the trace to inspect and diagnose.</p></div></div><div className="list-surface">{detail.data.traces.map((trace) => <button type="button" className="list-row" aria-pressed={detail.data?.selected_trace_id === trace.trace_id} key={trace.trace_id} onClick={() => chooseTrace(trace.trace_id)}><div><strong>{trace.name || trace.trace_id}</strong><span>{trace.span_count} spans · {trace.error_count} errors · {trace.complete ? "complete structure" : "partial structure"}</span></div><code>{trace.trace_id}</code></button>)}</div></section>}
      {detail.data.trace && <section className="trace-summary"><div><p className="eyebrow">Selected trace</p><h2>{detail.data.trace.name || "Unnamed trace"}</h2><p><code>{detail.data.trace.trace_id}</code></p></div><dl><div><dt>Window</dt><dd>{time(detail.data.trace.started_ns)} – {time(detail.data.trace.ended_ns)}</dd></div><div><dt>Environment</dt><dd>{String(detail.data.trace.environment ?? "Unknown")}</dd></div><div><dt>Release</dt><dd>{String(detail.data.trace.release ?? "Unknown")}</dd></div><div><dt>Structure</dt><dd>{detail.data.trace.complete ? "Complete" : `${detail.data.trace.missing_parent_count} missing parents`}</dd></div></dl></section>}
      {detail.data.readiness.viewer_ready && <div className="trace-viewer">
        <aside className="trace-timeline" aria-label="Trace spans"><header><strong>Timeline</strong><span>{detail.data.timeline.returned_spans}/{detail.data.timeline.total_spans}</span></header>{detail.data.timeline.spans.map((span) => <button type="button" key={span.id} aria-pressed={selected?.span_id === span.span_id} onClick={() => setSelectedSpanId(span.span_id)} style={{ paddingLeft: `${.75 + Math.min(span.depth || 0, 8) * .75}rem` }}><span className={`trace-span-state trace-span-${span.status}`} /><span><strong>{span.name || span.operation || "Unnamed span"}</strong><small>{duration(span.duration_ns)} · {span.status}</small></span></button>)}{detail.data.timeline.omitted_spans > 0 && <p>{detail.data.timeline.omitted_spans} spans omitted by this bounded view.</p>}</aside>
        <section className="trace-span-detail">{selected ? <><header><div><p className="eyebrow">Selected span</p><h2>{selected.name || selected.operation || "Unnamed span"}</h2></div><Status value={selected.status} /></header><dl className="compact-definition"><div><dt>Duration</dt><dd>{duration(selected.duration_ns)}</dd></div><div><dt>Kind</dt><dd>{selected.kind || "Unknown"}</dd></div><div><dt>Model</dt><dd>{selected.model || "Not reported"}</dd></div><div><dt>Span ID</dt><dd><code>{selected.span_id}</code></dd></div></dl>{selected.error !== undefined && selected.error !== null && <div className="trace-error"><strong>Error</strong><pre>{typeof selected.error === "string" ? selected.error : JSON.stringify(selected.error, null, 2)}</pre></div>}<DataBlock title="Input" value={selected.input} /><DataBlock title="Output" value={selected.output} /><DataBlock title="Messages" value={selected.messages} /><DataBlock title="Tool and runtime metadata" value={{ tool_call_id: selected.tool_call_id, usage: selected.usage, metadata: selected.metadata, attributes: selected.attributes, events: selected.events }} />{(selected.content_truncated || selected.redacted_value_count > 0) && <p className="trace-content-note">{selected.content_truncated ? "Some content is omitted by the public display bound. " : ""}{selected.redacted_value_count > 0 ? `${selected.redacted_value_count} values are redacted.` : ""}</p>}<details className="monitor-diagnostics"><summary>Span provenance</summary><code>{selected.record_locator}</code></details></> : <p>Select a span.</p>}</section>
      </div>}
      {detail.data.selected_trace_id && <section className="trace-annotations"><div className="section-heading"><div><p className="eyebrow">Issue annotations</p><h2>Problems linked to this trace</h2></div><span>{relatedIssues.length}</span></div>{relatedIssues.length ? <div className="list-surface">{relatedIssues.map((issue) => <Link className="list-row" key={issue.issue_id} to={`/projects/${projectId}/issues/${issue.issue_id}`}><div><strong>{issue.title}</strong><span>{issue.summary}</span></div><div className="row-end"><Status value={issue.status} /><small>{issue.severity} severity</small></div></Link>)}</div> : <div className="quiet-surface">No retained issue currently references this exact trace snapshot. Investigate the trace to diagnose and group supported failures.</div>}</section>}
      {(detail.data.diagnostics.length > 0 || detail.data.errors.length > 0) && <details className="trace-diagnostics"><summary>Import diagnostics and errors</summary>{detail.data.errors.map((error) => <article key={error.span_id}><strong>{error.name || error.span_id}</strong><pre>{typeof error.error === "string" ? error.error : JSON.stringify(error.error, null, 2)}</pre></article>)}{detail.data.diagnostics.map((diagnostic, index) => <p key={`${diagnostic.locator}-${index}`}><code>{diagnostic.code}</code> {diagnostic.message}</p>)}</details>}
    </>}
    {selectTrace.error && <p className="error-banner">{selectTrace.error.message}</p>}
    {launchSnapshot && workflow && <LaunchWorkflowModal projectId={projectId} workflow={workflow} defaultInput={{ type: "trace", id: launchSnapshot }} onClose={() => setLaunchSnapshot(undefined)} />}
    {evaluationCaseOpen && detail.data?.selected_trace_id && <EvaluationCaseModal projectId={projectId} snapshotId={snapshotId} traceId={detail.data.selected_trace_id} traceName={detail.data.trace?.name || undefined} onClose={() => setEvaluationCaseOpen(false)} />}
  </>;
}

function EvaluationCaseModal({ projectId, snapshotId, traceId, traceName, onClose }: { projectId: string; snapshotId: string; traceId: string; traceName?: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(traceName ? `${traceName} expected behavior` : `Trace ${traceId.slice(0, 32)} expected behavior`);
  const [expected, setExpected] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const save = useMutation({
    mutationFn: () => post<ProposedCase>(projectPath(projectId, `/traces/${encodeURIComponent(snapshotId)}/evaluation-case`), {
      trace_id: traceId,
      case_name: name,
      expected_behavior: expected,
      reviewed,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects", projectId] }),
  });
  return <Modal title={save.data ? "Evaluation case saved" : "Use trace as an evaluation case"} eyebrow="Proposed evaluation evidence" onClose={onClose}>
    {save.data ? <div className="evaluation-case-receipt"><Status value="reviewed" /><h3>{save.data.name}</h3><p>The expected behavior was saved separately from the observed output. This immutable case remains a draft until you attach it to a reviewed evaluation.</p><dl className="compact-definition"><div><dt>Dataset snapshot</dt><dd><code>{save.data.id}</code></dd></div><div><dt>Source trace</dt><dd><code>{traceId}</code></dd></div><div><dt>Expectation</dt><dd>Reviewed</dd></div></dl><footer className="form-actions"><Button type="button" onClick={onClose}>Done</Button></footer></div> : <form className="form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
      <p className="modal-purpose">Describe what should have happened. Agentagon retains the observed output as source evidence and never promotes it to ground truth.</p>
      <dl className="compact-definition evaluation-case-source"><div><dt>Source trace</dt><dd><code>{traceId}</code></dd></div><div><dt>Source snapshot</dt><dd><code>{snapshotId}</code></dd></div></dl>
      <label>Case name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required autoFocus /></label>
      <label>Expected behavior<textarea value={expected} onChange={(event) => setExpected(event.target.value)} rows={5} maxLength={8000} placeholder="Describe the correct answer, action, or refusal and the evidence it should use." required /></label>
      <label className="checkbox-label evaluation-case-review"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} required /><span>I reviewed this expectation. Do not use the observed output as ground truth.</span></label>
      {save.error && <p className="error-banner">{save.error.message}</p>}
      <footer className="form-actions"><Button tone="secondary" type="button" onClick={onClose}>Cancel</Button><Button type="submit" disabled={save.isPending || !reviewed}>{save.isPending ? "Saving case…" : "Save proposed case"}</Button></footer>
    </form>}
  </Modal>;
}
