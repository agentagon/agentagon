"use strict";

const byId = (id) => document.getElementById(id);
const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};
const readable = (value) => String(value || "unknown").replaceAll("_", " ");
const date = (value) => new Date(value).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
const modes = {code: "Code audit", traces: "Trace audit", combined: "Code + traces audit"};
const auditLabel = (audit) => audit.workflow === "review"
  ? audit.mode === "combined" ? "Local changes + traces review" : "Local changes review"
  : modes[audit.mode] || "Audit overview";
const findingCategory = (finding) => finding.kind === "evaluation_coverage" ? "Eval recommendation"
  : finding.improvement ? "Improvement" : "Defect";
const params = new URLSearchParams(window.location.search);
let selected = params.get("audit");
let selectedRun = params.get("run");
let view = params.get("evaluation") ? "eval" : selectedRun ? "fix" : selected ? "audit" : ["audit", "fix", "eval"].includes(params.get("view")) ? params.get("view") : null;
let requestVersion = 0;

async function getJSON(path) {
  const response = await fetch(path, {credentials: "omit", cache: "no-store"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Unable to load local results.");
  return result;
}

function findingView(finding) {
  const block = element("section", "finding");
  const category = findingCategory(finding);
  block.append(element("p", "finding-category", category));
  block.append(element("h4", "", finding.title), element("p", "", finding.observation));
  const facts = element("dl");
  for (const [label, value] of [
    ["Expected", finding.expected_behavior], ["Authority", finding.authority],
    ["Evidence basis", readable(finding.basis)], [category === "Defect" ? "Next investigation / hypothesis" : "Proposed action", finding.hypothesis],
    ["Connection", finding.correlation_rationale],
  ]) {
    if (value) facts.append(element("dt", "", label), element("dd", "", value));
  }
  block.append(facts);
  const refs = Object.entries(finding.source_references || {});
  if (refs.length) {
    block.append(element("p", "detail-label", "Evidence references"));
    const list = element("ul", "evidence-list");
    for (const [id, ref] of refs) {
      const label = ref.kind === "code" && ref.change ? changeLocation(ref.change)
        : ref.kind === "code"
        ? `${ref.path} · lines ${ref.start_line}–${ref.end_line}`
        : `${readable(ref.kind)} · ${id}`;
      list.append(element("li", "", label));
    }
    block.append(list);
  }
  return block;
}

function changeLocation(change) {
  return [["old", "Before"], ["new", "After"]].flatMap(([side, label]) => {
    const path = change[`${side}_path`];
    if (!path) return [];
    const start = change[`${side}_start`], count = change[`${side}_count`];
    return [`${label}: ${path}${start && count ? ` · lines ${start}–${start + count - 1}` : ""}`];
  }).join(" → ");
}

function evaluationHandoff(auditId, issue) {
  const panel = element("details", "evaluation-handoff");
  panel.append(element("summary", "", "Create regression evaluation"));
  panel.append(element("p", "muted small", "Continue in your coding agent. Review the expected behavior above, then include ordinary successes and boundary cases before freezing a test."));
  const prompt = element("textarea", "handoff-prompt");
  prompt.readOnly = true;
  prompt.rows = 6;
  prompt.setAttribute("aria-label", `Evaluation request for ${issue.title}`);
  prompt.value = `Use ag:eval to prepare a regression evaluation for saved issue ${issue.issue_id} from audit ${auditId}.\nCarry this selection with eval start --audit ${auditId} --issue ${issue.issue_id}. Inspect its captured evidence and review the proposed expected behavior; do not treat observed outputs as ground truth. Include ordinary successes, relevant boundary cases and resettable tool state where needed. Reuse a suitable evaluation or prepare one with a chosen execution profile and explicit preparation budget. Keep missing expectations and evidence limits visible.`;
  const copy = element("button", "button", "Copy request");
  copy.type = "button";
  const feedback = element("p", "muted small");
  feedback.setAttribute("role", "status");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(prompt.value);
      feedback.textContent = "Copied. Paste into your coding agent to continue.";
    } catch {
      prompt.focus();
      prompt.select();
      feedback.textContent = "Select and copy the request, then paste it into your coding agent.";
    }
  });
  panel.append(prompt, copy, feedback);
  return panel;
}

function issueView(issue, auditId) {
  const details = element("details", "issue");
  const summary = element("summary");
  const severity = ["critical", "high", "medium", "low"].includes(issue.severity) ? issue.severity : "low";
  summary.append(element("span", `severity ${severity}`, severity));
  const heading = element("div", "issue-heading");
  const affected = issue.affected_trace_ids.length;
  const support = affected ? `${affected}/${issue.reviewed_trace_denominator} reviewed traces` : "Code evidence";
  const categories = [...new Set(issue.findings.map(findingCategory))].join(", ");
  heading.append(element("p", "issue-title", issue.title), element("p", "issue-subtitle",
    `${categories} · ${support} · ${readable(issue.status)} · ${Math.round(issue.confidence * 100)}% confidence`));
  summary.append(heading);
  const body = element("div", "details-body");
  body.append(element("p", "", issue.summary));
  if (issue.rationale) body.append(element("p", "muted", issue.rationale));
  for (const finding of issue.findings) body.append(findingView(finding));
  body.append(evaluationHandoff(auditId, issue));
  if (issue.history.length) {
    body.append(element("p", "detail-label", "Issue history"));
    const history = element("ol", "history-list");
    for (const event of issue.history) history.append(element("li", "", `${date(event.at)} · ${readable(event.status)} — ${event.reason}`));
    body.append(history);
  }
  body.append(element("p", "muted small", issue.issue_id));
  details.append(summary, body);
  return details;
}

function renderAudit(audit) {
  const isReview = audit.workflow === "review";
  byId("audit").hidden = false;
  byId("empty").hidden = true;
  byId("audit-title").textContent = auditLabel(audit);
  byId("audit-date").textContent = date(audit.created_at);
  byId("audit-state").textContent = readable(audit.state);
  byId("audit-state").className = `status ${audit.pending_action ? "active" : "complete"}`;
  byId("goal").textContent = audit.goal || (isReview
    ? "Review local changes for defects, improvements, and eval coverage."
    : "Broad review of the selected code and available runtime evidence.");
  byId("audit-id").textContent = audit.audit_id;
  byId("audit-meta").replaceChildren(...[
    `${readable(audit.host)} · ${audit.model}`,
    ...(audit.revision ? [`Baseline ${audit.revision.slice(0, 12)}`] : isReview ? ["Empty baseline · before first commit"] : []),
    ...(audit.mode === "traces" ? [] : [audit.code_scopes.join(", ")]),
    ...(audit.window ? [`${date(audit.window.start)} – ${date(audit.window.end)}`] : []),
  ].map((label) => element("span", "", label)));
  const stages = audit.mode === "code" ? ["evidence", "diagnosis", "clustering"] : ["import", "evidence", "diagnosis", "clustering"];
  const current = audit.pending_action ? stages.indexOf(audit.pending_action) : stages.length;
  const stageLabels = {import: "Import traces", evidence: "Review evidence", diagnosis: "Diagnose", clustering: "Group issues"};
  byId("stages").replaceChildren(...stages.map((stage, index) => {
    const item = element("li", index < current ? "done" : index === current ? "current" : "", stageLabels[stage]);
    if (index === current) item.setAttribute("aria-current", "step");
    return item;
  }));
  byId("progress-caption").textContent = audit.pending_action ? "In progress" : "Review finished";
  const c = audit.coverage;
  byId("coverage").replaceChildren(...[
    [c.reviewed_code_units, c.code_units, isReview ? "Change units reviewed" : "Code units reviewed"],
    [c.reviewed_traces, c.selected_traces, "Traces reviewed"],
    [c.findings, null, "Findings"], [c.issue_groups, null, "Issue groups"],
  ].map(([value, total, label]) => {
    const metric = element("div", "metric");
    const number = element("div", "metric-value", value);
    if (total !== null) number.append(element("span", "metric-denominator", ` / ${total}`));
    metric.append(number, element("p", "metric-label", label));
    return metric;
  }));
  byId("pending").textContent = audit.pending_action
    ? `Next: ${stageLabels[audit.pending_action].toLowerCase()}. Invoke ${isReview ? "ag:review" : "ag:audit"} in your coding agent to continue, then refresh this page.`
    : audit.state === "complete_with_limits" ? "Review finished with coverage limits. See scope and limits below."
    : isReview ? "All selected changes have been reviewed and findings grouped."
    : "All selected evidence has been reviewed and findings grouped.";
  byId("issue-count").textContent = audit.issues.length;
  if (audit.issues.length) byId("issues").replaceChildren(...audit.issues.map((issue) => issueView(issue, audit.audit_id)));
  else {
    const empty = element("div", "no-issues");
    empty.append(element("strong", "", audit.pending_action ? "No grouped issues yet" : "No actionable issues found"),
      element("p", "", audit.pending_action ? "The review is still in progress. Findings will appear as evidence is reviewed and grouped." : "This result applies to the evidence reviewed. See the coverage and limits below."));
    byId("issues").replaceChildren(empty);
  }
  byId("ungrouped-section").hidden = !audit.ungrouped_findings.length;
  byId("ungrouped").replaceChildren(...audit.ungrouped_findings.map(findingView));
  const scope = byId("scope");
  scope.replaceChildren(element("p", "", audit.mode === "traces"
    ? "Code review: not included in this audit."
    : `Code scope: ${isReview ? "Local changes · " : ""}${audit.code_scopes.join(", ")} · Skipped code files: ${audit.skipped_code_files}`));
  if (audit.revision) scope.append(element("p", "", `Baseline revision: ${audit.revision}`));
  if (isReview && audit.changes?.length) {
    scope.append(element("p", "detail-label", "Selected changes"));
    const changed = element("ul", "evidence-list");
    for (const change of audit.changes) changed.append(element("li", "", `${readable(change.change_type)} · ${changeLocation(change)}`));
    scope.append(changed);
  }
  if (audit.skipped_code?.length) {
    scope.append(element("p", "detail-label", "Excluded code"));
    const skipped = element("ul", "evidence-list");
    for (const file of audit.skipped_code) skipped.append(element("li", "", `${file.path} · ${readable(file.reason)}`));
    scope.append(skipped);
  }
  if (audit.trace_alignment) {
    const mismatch = audit.trace_alignment.status === "mismatch";
    const unverified = audit.trace_alignment.status === "unverified" || audit.trace_alignment.scope === "changes";
    scope.append(element("p", mismatch || audit.trace_alignment.warning ? "alignment-warning" : "", mismatch
      ? `Trace revision mismatch: ${audit.trace_alignment.mismatched_traces} trace(s) report another revision. Code/trace correlation is limited.`
      : audit.trace_alignment.warning || (unverified ? "The relationship between supplied traces and the captured local changes is unverified. Consult each finding's correlation rationale; traces may describe the baseline rather than the edits."
      : "Trace alignment is assumed: supplied traces reflect the captured revision. Their revision provenance has not been verified.")));
  }
  if (audit.window) scope.append(element("p", "", `Trace window: ${date(audit.window.start)} to ${date(audit.window.end)} (end exclusive).`));
  const limits = element("ul");
  for (const limit of audit.limits) limits.append(element("li", "", limit));
  scope.append(limits);
  byId("announcement").textContent = `${auditLabel(audit)}, ${readable(audit.state)}, ${audit.issues.length} issues.`;
}

const number = (value) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumSignificantDigits: 6}) : "—";
const outcome = (passed) => passed === true ? "Pass" : passed === false ? "Fail" : "Pending";

function metricView(value, baseline, variation, isBaseline) {
  const cell = element("td", "metric-cell");
  cell.append(element("strong", "", number(value)));
  if (Number.isFinite(value) && Number.isFinite(baseline)) {
    const delta = value - baseline;
    cell.append(element("span", "metric-change muted", isBaseline ? "baseline"
      : `${delta > 0 ? "+" : ""}${number(delta)} vs baseline`));
  }
  if (variation && Number.isFinite(variation.min) && Number.isFinite(variation.max) && variation.min !== variation.max) {
    cell.append(element("span", "metric-change muted", `range ${number(variation.min)}–${number(variation.max)}`));
  }
  return cell;
}

function outcomesView(items, constraint) {
  const cell = element("td", "check-cell");
  if (!items.length) cell.append(element("span", "muted", "Not recorded"));
  for (const item of items) {
    const label = constraint ? `${item.metric} ${item.op || ""} ${number(item.threshold ?? item.bound)}` : item.id;
    const line = element("p", "", `${outcome(item.passed)} · ${label}`);
    if (constraint && Number.isFinite(item.actual)) line.append(element("span", "metric-change muted", `measured ${number(item.actual)}`));
    cell.append(line);
  }
  return cell;
}

let controlSession = null;
let currentRun = null;
let controlBusy = false;
let uncertainControl = null;

async function getControlSession() {
  if (controlSession !== null) return;
  const response = await fetch("/api/session", {
    mode: "same-origin", credentials: "omit", cache: "no-store",
    headers: {"X-Agentagon-Bootstrap": "1"},
  });
  if (!response.ok) throw new Error("Unable to open this dashboard session. Reload the page.");
  controlSession = await response.json();
}

function policyFields() {
  const strategy = byId("policy-strategy").value;
  const scalar = ["argmax", "top_k", "epsilon_greedy", "softmax"].includes(strategy);
  byId("policy-objective-field").hidden = !scalar;
  byId("policy-objective").required = scalar;
  for (const [name, owner] of [["k", "top_k"], ["epsilon", "epsilon_greedy"], ["temperature", "softmax"]]) {
    byId(`policy-${name}-field`).hidden = strategy !== owner;
    byId(`policy-${name}`).required = strategy === owner;
  }
}

function candidateOptions(id, candidates, fallback) {
  const select = byId(id);
  const previous = select.value;
  select.replaceChildren(...candidates.map((candidate) => {
    const option = element("option", "", `${candidate.id} · ${readable(candidate.state)}`);
    option.value = candidate.id;
    return option;
  }));
  select.disabled = !candidates.length;
  if (!candidates.length) select.append(element("option", "", "No eligible candidates"));
  else select.value = candidates.some((candidate) => candidate.id === previous) ? previous
    : candidates.some((candidate) => candidate.id === fallback) ? fallback : candidates[0].id;
}

function renderControls(run) {
  const enabled = controlSession?.controls_enabled === true;
  byId("fix-controls").hidden = !enabled;
  byId("run-access").textContent = `${enabled ? "Controls enabled" : "Read-only"} · Stored in this workspace`;
  const operations = run.controls || [];
  byId("run-operations").hidden = !operations.length;
  byId("operations-count").textContent = operations.length;
  byId("operation-list").replaceChildren(...[...operations].reverse().map((operation) => {
    const row = element("li", "operation");
    const heading = element("div", "operation-heading");
    heading.append(element("strong", "", readable(operation.action)), element("span", "status", readable(operation.state)));
    row.append(heading);
    for (const value of [operation.text, operation.hypothesis, operation.reason, operation.error]) {
      if (value) row.append(element("p", "small", value));
    }
    if (operation.created_at) row.append(element("p", "muted small", date(operation.created_at)));
    if (enabled && operation.state === "queued") {
      const cancel = element("button", "button", "Cancel queued work");
      cancel.type = "button";
      cancel.disabled = controlBusy || uncertainControl !== null;
      cancel.addEventListener("click", () => submitControl("cancel", {target_operation_id: operation.operation_id}));
      row.append(cancel);
    }
    return row;
  }));
  if (!enabled) return;
  if (uncertainControl && operations.some((item) => item.operation_id === uncertainControl.payload.operation_id)) {
    uncertainControl = null;
    controlFeedback("Previous control confirmed. Its current state appears in the history below.");
  }
  if (uncertainControl) uncertainFeedback();
  byId("control-fields").disabled = controlBusy || uncertainControl !== null;
  const policy = run.search_policy || {strategy: "pareto", seed: 0};
  byId("policy-strategy").value = policy.strategy;
  const objectives = run.objectives?.length ? run.objectives.map((item) => item.name)
    : [...new Set(run.candidates.flatMap((candidate) => Object.keys(candidate.metrics)))];
  byId("policy-objective").replaceChildren(...objectives.map((name) => {
    const option = element("option", "", name);
    option.value = name;
    return option;
  }));
  if (policy.objective) byId("policy-objective").value = policy.objective;
  for (const [key, fallback] of [["seed", 0], ["k", 3], ["epsilon", 0.2], ["temperature", 1]]) byId(`policy-${key}`).value = policy[key] ?? fallback;
  policyFields();
  candidateOptions("expand-parent", run.candidates.filter((candidate) => candidate.state === "verified"
    && !candidate.invalidated && !candidate.expansion_exhausted && (candidate.feasible || candidate.id === run.baseline_id)), run.baseline_id);
  byId("expand-submit").disabled = byId("expand-parent").disabled || ["stopped", "exhausted"].includes(run.state);
  candidateOptions("select-candidate", run.candidates.filter((candidate) => run.frontier.includes(candidate.id)));
  byId("select-submit").disabled = byId("select-candidate").disabled;
  candidateOptions("candidate-action-target", run.candidates.filter((candidate) => ["verified", "rejected", "failed", "duplicate"].includes(candidate.state)));
  byId("candidate-action-submit").disabled = byId("candidate-action-target").disabled;
  byId("stop-run").disabled = run.state === "stopped";
  byId("continue-submit").disabled = !["stopped", "exhausted"].includes(run.state);
}

function controlFeedback(message, error = false) {
  const feedback = byId("control-feedback");
  feedback.hidden = false;
  feedback.className = `control-feedback${error ? " control-error" : ""}`;
  feedback.replaceChildren(element("span", "", message));
}

function uncertainFeedback() {
  if (!uncertainControl) return;
  const request = uncertainControl;
  controlFeedback(`Unable to confirm a control for ${request.runId}. Retry the same request to confirm its outcome.`, true);
  const button = element("button", "button", "Retry same request");
  button.type = "button";
  button.addEventListener("click", () => submitControl(request.payload.action, {}, request));
  byId("control-feedback").append(button);
}

async function submitControl(action, fields = {}, retry = null) {
  if (!controlSession?.controls_enabled || !currentRun || controlBusy) return;
  if (uncertainControl && !retry) {
    uncertainFeedback();
    return;
  }
  const request = retry || {runId: currentRun.run_id, payload: {
    version: 1, operation_id: crypto.randomUUID(), expected_revision: currentRun.revision,
    action, ...fields,
  }};
  const requestView = requestVersion;
  const stillViewing = () => view === "fix" && requestVersion === requestView && currentRun?.run_id === request.runId;
  controlBusy = true;
  byId("control-fields").disabled = true;
  for (const button of byId("operation-list").querySelectorAll("button")) button.disabled = true;
  controlFeedback("Saving control…");
  let acknowledged = false;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(request.runId)}/control`, {
      method: "POST", mode: "same-origin", credentials: "omit", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-Agentagon-Token": controlSession.token},
      body: JSON.stringify(request.payload),
    });
    const result = await response.json();
    acknowledged = response.status < 500;
    if (!acknowledged) throw new Error(result.error || "Unable to confirm the control.");
    uncertainControl = null;
    if (!response.ok) {
      if (response.status === 409) {
        const latest = await getJSON(`/api/runs/${encodeURIComponent(request.runId)}`);
        if (stillViewing()) renderRun(latest);
        throw new Error("The run changed. Results have been refreshed; review them before submitting again.");
      }
      throw new Error(result.error || "Unable to save the control.");
    }
    const latest = await getJSON(`/api/runs/${encodeURIComponent(request.runId)}`);
    if (stillViewing()) renderRun(latest);
    const operation = result.controls?.find((item) => item.operation_id === request.payload.operation_id);
    if (stillViewing()) controlFeedback(operation?.state === "queued" ? "Queued for your coding agent. Resume ag:fix to pick up this work."
      : `Control ${readable(operation?.state || "saved")}.`);
  } catch (error) {
    if (stillViewing()) controlFeedback(error.message || "Unable to confirm the control.", true);
    if (!acknowledged) {
      uncertainControl = request;
      uncertainFeedback();
    }
  } finally {
    controlBusy = false;
    byId("control-fields").disabled = uncertainControl !== null;
    for (const button of byId("operation-list").querySelectorAll("button")) button.disabled = uncertainControl !== null;
  }
}

function renderRun(run) {
  currentRun = run;
  byId("fix-run").hidden = false;
  byId("run-date").textContent = run.created_at ? date(run.created_at) : "Local run";
  byId("run-state").textContent = readable(run.state);
  byId("run-goal").textContent = run.goal || "Measured improvements against a recorded baseline.";
  byId("run-id").textContent = run.run_id;
  byId("run-meta").replaceChildren(...[
    run.runner_kind ? `${readable(run.runner_kind)} runner` : null,
    run.issue_ids.length ? `Issues: ${run.issue_ids.join(", ")}` : null,
    run.updated_at ? `Updated ${date(run.updated_at)}` : null,
  ].filter(Boolean).map((text) => element("span", "", text)));
  const result = byId("run-result");
  result.replaceChildren(element("p", "", run.selected_branch ? "Reviewable branch" : "No branch has been selected for review."));
  if (run.selected_branch) result.append(element("code", "selected-branch", run.selected_branch));
  result.append(element("p", "muted small", `Frontier: ${run.frontier.length ? run.frontier.join(", ") : "none yet"}`));
  if (run.search_policy) {
    const policy = run.search_policy;
    const labels = {pareto: "Pareto frontier", argmax: "Best objective value", top_k: "Top K candidates", epsilon_greedy: "Epsilon greedy", softmax: "Softmax sampling", pareto_per_task: "Per-task winners"};
    const parameters = [policy.objective, ...["k", "epsilon", "temperature", "seed"].filter((key) => policy[key] !== undefined).map((key) => `${key} ${policy[key]}`)].filter(Boolean);
    result.append(element("p", "muted small", `Search strategy: ${labels[policy.strategy] || readable(policy.strategy)}${parameters.length ? ` · ${parameters.join(" · ")}` : ""}`));
  }
  if (run.cleanup_pending) result.append(element("p", "cleanup-notice", "Workspace cleanup is pending."));
  byId("candidate-count").textContent = run.candidates.length;
  const container = byId("candidate-table");
  if (!run.candidates.length) container.replaceChildren(element("p", "no-issues", "Candidates will appear after the baseline is prepared."));
  else {
    const dimensions = [...new Set(run.candidates.flatMap((candidate) => Object.keys(candidate.metrics)))].sort();
    const tasks = (run.task_objectives || []).map((objective) => objective.name);
    const baseline = run.candidates.find((candidate) => candidate.id === run.baseline_id);
    const table = element("table", "candidate-table");
    table.append(element("caption", "sr-only", "Candidate lineage, every measured objective, baseline changes, constraints, checks, and review verdicts."));
    const head = element("thead");
    const headings = element("tr");
    for (const name of ["Candidate / parent", "State", ...dimensions, ...tasks.map((name) => `Task: ${name}`), "Constraints", "Checks", "Review"]) {
      const cell = element("th", "", name);
      cell.scope = "col";
      headings.append(cell);
    }
    head.append(headings);
    const body = element("tbody");
    for (const candidate of run.candidates) {
      const row = element("tr", candidate.id === run.baseline_id ? "baseline-row" : "");
      const name = element("th", "candidate-name");
      name.scope = "row";
      const inspect = element("button", "candidate-link", candidate.id);
      inspect.type = "button";
      inspect.addEventListener("click", () => openCandidate(run.run_id, candidate.id));
      name.append(inspect);
      name.append(element("span", "metric-change muted", candidate.parent_id ? `from ${candidate.parent_id}` : "Baseline"));
      if (candidate.hypothesis) name.append(element("p", "candidate-hypothesis", candidate.hypothesis));
      const state = element("td", "", candidate.invalidated ? "Invalidated" : readable(candidate.state));
      if (candidate.expansion_exhausted) state.append(element("span", "metric-change muted", "Exploration stopped"));
      if (run.frontier.includes(candidate.id) && candidate.state !== "frontier") state.append(element("span", "frontier-label", "Frontier"));
      row.append(name, state);
      for (const dimension of dimensions) row.append(metricView(candidate.metrics[dimension], baseline?.metrics[dimension], candidate.variation[dimension], candidate.id === run.baseline_id));
      for (const task of tasks) row.append(metricView(candidate.task_metrics?.[task], baseline?.task_metrics?.[task], candidate.task_variation?.[task], candidate.id === run.baseline_id));
      row.append(outcomesView(candidate.constraints, true), outcomesView(candidate.checks, false), element("td", "", candidate.review_verdict ? readable(candidate.review_verdict) : "Not recorded"));
      body.append(row);
    }
    table.append(head, body);
    container.replaceChildren(table);
  }
  const bounds = byId("run-bounds");
  bounds.replaceChildren();
  for (const [title, values] of [["Limits", run.limits], ["Usage", run.usage]]) {
    bounds.append(element("h4", "", title));
    const list = element("dl", "bounds-list");
    for (const [key, value] of Object.entries(values)) list.append(element("dt", "", readable(key)), element("dd", "", number(value)));
    bounds.append(Object.keys(values).length ? list : element("p", "muted", "Not recorded"));
  }
  byId("announcement").textContent = `Fix run, ${readable(run.state)}, ${run.candidates.length} candidates, ${run.frontier.length} on the frontier.`;
  renderControls(run);
  renderWorkflow(run);
}

function updateView() {
  const isFix = view === "fix";
  byId("view-audit").setAttribute("aria-pressed", String(view === "audit"));
  byId("view-eval").setAttribute("aria-pressed", String(view === "eval"));
  byId("view-fix").setAttribute("aria-pressed", String(isFix));
  byId("audit-history").hidden = view !== "audit";
  byId("eval-history").hidden = view !== "eval";
  byId("fix-history").hidden = !isFix;
  for (const id of ["audit", "empty", "fix-run", "fix-empty", "evaluation", "eval-empty"]) byId(id).hidden = true;
}

async function load() {
  const version = ++requestVersion;
  byId("refresh").disabled = true;
  byId("error").hidden = true;
  byId("announcement").textContent = "";
  updateView();
  try {
    if (view === "eval") { await loadEvaluations(version); return; }
    let data = await getJSON(view === "fix" ? "/api/runs" : "/api/audits");
    if (view === null) {
      view = data.selected_view || "audit";
      if (view === "fix") data = await getJSON("/api/runs");
      updateView();
    }
    if (version !== requestVersion) return;
    byId("workspace-name").textContent = data.workspace.split("/").filter(Boolean).pop();
    byId("workspace-path").textContent = data.workspace;
    const isFix = view === "fix";
    const items = isFix ? data.runs : data.audits;
    const idKey = isFix ? "run_id" : "audit_id";
    byId(isFix ? "run-count" : "audit-count").textContent = `${items.length} ${isFix ? items.length === 1 ? "fix run" : "fix runs" : items.length === 1 ? "audit or review" : "audits and reviews"} in this workspace`;
    const select = byId(isFix ? "run-select" : "audit-select");
    select.replaceChildren(...items.map((item) => {
      const option = element("option", "", `${item.created_at ? date(item.created_at) : "Undated"} · ${isFix ? readable(item.state) : auditLabel(item)} · ${item[idKey].slice(-6)}`);
      option.value = item[idKey];
      return option;
    }));
    select.disabled = !items.length;
    const requested = isFix ? selectedRun || data.selected_run_id : selected || data.selected_audit_id;
    if (!requested && !items.length) {
      select.append(element("option", "", isFix ? "No fix runs yet" : "No audits yet"));
      byId(isFix ? "fix-empty" : "empty").hidden = false;
      byId("announcement").textContent = isFix ? "No fix runs yet." : "No audits yet.";
      return;
    }
    select.value = requested;
    if (!items.some((item) => item[idKey] === requested)) throw new Error(`${isFix ? "Fix run" : "Audit"} not found in this workspace. Choose from the history selector.`);
    const detail = await getJSON(`/api/${isFix ? "runs" : "audits"}/${encodeURIComponent(requested)}`);
    if (version !== requestVersion) return;
    if (isFix) {
      await getControlSession();
      if (version !== requestVersion) return;
      renderRun(detail);
    }
    else renderAudit(detail);
  } catch (error) {
    if (version !== requestVersion) return;
    for (const id of ["audit", "empty", "fix-run", "fix-empty", "evaluation", "eval-empty"]) byId(id).hidden = true;
    byId("error").textContent = error.message || "Unable to reach the dashboard. Check that agentagon dashboard is still running.";
    byId("error").hidden = false;
  } finally {
    if (version === requestVersion) {
      byId("loading").hidden = true;
      byId("refresh").disabled = false;
    }
  }
}

byId("refresh").addEventListener("click", load);
byId("policy-strategy").addEventListener("change", policyFields);
byId("policy-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const strategy = byId("policy-strategy").value;
  const policy = {strategy, seed: Number(byId("policy-seed").value)};
  if (["argmax", "top_k", "epsilon_greedy", "softmax"].includes(strategy)) policy.objective = byId("policy-objective").value;
  for (const [key, owner] of [["k", "top_k"], ["epsilon", "epsilon_greedy"], ["temperature", "softmax"]]) {
    if (strategy === owner) policy[key] = Number(byId(`policy-${key}`).value);
  }
  submitControl("policy", {policy});
});
byId("expand-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitControl("expand", {parent_id: byId("expand-parent").value, hypothesis: byId("expand-hypothesis").value.trim()});
});
byId("directive-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitControl("directive", {text: byId("directive-text").value.trim()});
});
byId("stop-run").addEventListener("click", () => submitControl("stop"));
byId("continue-form").addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const text = byId("continue-limits").value.trim();
    const fields = text ? {limits: JSON.parse(text)} : {};
    if (text && (!fields.limits || typeof fields.limits !== "object" || Array.isArray(fields.limits))) throw new Error("Limits must be a JSON object.");
    submitControl("continue", fields);
  } catch (error) {
    controlFeedback(`Check execution limits: ${error.message}`, true);
  }
});
byId("select-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitControl("select", {candidate_id: byId("select-candidate").value});
});
byId("candidate-action-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitControl(byId("candidate-action").value, {candidate_id: byId("candidate-action-target").value, reason: byId("candidate-action-reason").value.trim()});
});
byId("audit-select").addEventListener("change", (event) => {
  selected = event.target.value;
  const url = new URL(window.location.href);
  url.searchParams.set("audit", selected);
  window.history.replaceState(null, "", url);
  load();
});
byId("run-select").addEventListener("change", (event) => {
  selectedRun = event.target.value;
  const url = new URL(window.location.href);
  url.searchParams.set("run", selectedRun);
  url.searchParams.delete("audit");
  window.history.replaceState(null, "", url);
  load();
});
for (const name of ["audit", "fix", "eval"]) byId(`view-${name}`).addEventListener("click", () => {
  view = name;
  const url = new URL(window.location.href);
  url.searchParams.delete("audit");
  url.searchParams.delete("run");
  url.searchParams.delete("evaluation");
  url.searchParams.set("view", name);
  if (name === "fix" && selectedRun) url.searchParams.set("run", selectedRun);
  if (name === "audit" && selected) url.searchParams.set("audit", selected);
  window.history.replaceState(null, "", url);
  load();
});
initWorkflow();
load();
