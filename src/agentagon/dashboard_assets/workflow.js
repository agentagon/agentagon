"use strict";

let selectedCandidate = null;
let selectedEvaluation = new URLSearchParams(window.location.search).get("evaluation");
let inspectorVersion = 0;

function candidateButton(run, candidate) {
  const button = element("button", "candidate-link", candidate.hypothesis || candidate.id);
  button.type = "button";
  button.addEventListener("click", () => openCandidate(run.run_id, candidate.id));
  return button;
}

function renderWorkflow(run) {
  const work = byId("run-work");
  work.replaceChildren();
  for (const item of run.work || []) {
    const label = item.action === "author" ? item.assignment?.state === "assigned" ? "Assigned to author" : "Queued for author"
      : item.action === "execute" || item.action === "monitor" ? "Executing / awaiting execution"
      : item.action === "independent_review" ? "Awaiting independent review"
      : item.action === "reserve_round" ? "Waiting for the host to reserve a round"
      : ["scan", "ideate", "next_branch_hypothesis", "acknowledge_control", "recover_reservation"].includes(item.action) ? `Waiting for the host · ${readable(item.action)}` : readable(item.action);
    work.append(element("p", "work-row", `${label}${item.candidate_id ? ` · ${item.candidate_id.slice(-8)}` : ""}`));
  }
  if (!work.children.length) work.append(element("p", "muted", run.work_reason || "Resume ag:fix in the active coding agent to inspect pending work."));

  const visited = new Set();
  const tree = element("ul", "experiment-tree");
  function appendCandidate(candidate, list) {
    if (visited.has(candidate.id)) return;
    visited.add(candidate.id);
    const item = element("li");
    item.append(candidateButton(run, candidate), element("span", "tree-state", `${readable(candidate.display_state || candidate.state)}${run.frontier.includes(candidate.id) ? " · frontier" : ""}`));
    const children = run.candidates.filter((child) => child.parent_id === candidate.id && !visited.has(child.id));
    if (children.length) {
      const branch = element("ul");
      for (const child of children) appendCandidate(child, branch);
      item.append(branch);
    }
    list.append(item);
  }
  for (const root of run.candidates.filter((c) => !c.parent_id)) appendCandidate(root, tree);
  for (const candidate of run.candidates) if (!visited.has(candidate.id)) appendCandidate(candidate, tree);
  byId("experiment-tree").replaceChildren(tree);

  const metrics = run.objectives?.length ? run.objectives : [...new Set(run.candidates.flatMap((c) => Object.keys(c.metrics)))].map((name) => ({name}));
  const objectives = [...metrics.map((metric) => ({...metric, key: metric.name})), ...(run.task_objectives || []).map((metric) => ({...metric, name: `Task: ${metric.name}`, key: `task:${metric.name}`}))];
  for (const [id, index] of [["objective-x", 0], ["objective-y", 1]]) {
    const select = byId(id), previous = select.value;
    select.replaceChildren(...objectives.map((objective) => {
      const option = element("option", "", `${objective.name}${objective.direction ? ` (${objective.direction}, ${objective.unit})` : ""}`);
      option.value = objective.key;
      return option;
    }));
    select.value = objectives.some((o) => o.key === previous) ? previous : objectives[Math.min(index, objectives.length - 1)]?.key || "";
    select.disabled = !objectives.length;
  }
  renderObjectivePlot();
  const select = byId("inspect-candidate");
  select.replaceChildren(...run.candidates.map((candidate) => {
    const option = element("option", "", `${candidate.id.slice(-8)} · ${readable(candidate.state)} · ${candidate.hypothesis || "Baseline"}`);
    option.value = candidate.id;
    return option;
  }));
  if (!run.candidates.some((c) => c.id === selectedCandidate)) selectedCandidate = run.baseline_id || run.candidates[0]?.id;
  if (selectedCandidate) openCandidate(run.run_id, selectedCandidate);
  else byId("candidate-inspector").replaceChildren(element("p", "muted", "No candidates to inspect."));

  const lessons = byId("run-lessons");
  lessons.replaceChildren();
  for (const lesson of run.lesson_context?.lessons || []) {
    const details = element("details", "scope-details");
    details.append(element("summary", "", lesson.summary));
    const content = element("div", "details-body");
    content.append(element("p", "", lesson.rationale), element("p", "muted", `Uncertainty: ${lesson.uncertainty}`), element("p", "small", `Evidence: ${lesson.evidence.join(", ")}`));
    if (!lesson.same_source || !lesson.same_evaluation) content.append(element("p", "muted small", "Source or evaluation differs. This lesson is a hypothesis; fresh measurements are required."));
    if (lesson.invalidated_evidence) content.append(element("p", "muted small", "The candidate evidence behind this lesson has been invalidated. Verify the hypothesis against fresh evidence."));
    details.append(content);
    lessons.append(details);
  }
  if (!lessons.children.length) lessons.append(element("p", "muted", "No evidence-linked lessons yet."));
  for (const scan of run.scan_history || []) lessons.append(element("p", "muted small", `Scan ${scan.scan_id.slice(-8)} · ${readable(scan.state)}${scan.reason ? ` · ${scan.reason}` : ""}`));
}

function svgElement(tag, attributes, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderObjectivePlot() {
  const x = byId("objective-x").value, y = byId("objective-y").value;
  const axisLabel = (name) => name.startsWith("task:") ? `Task: ${name.slice(5)}` : name;
  const value = (candidate, name) => name.startsWith("task:") ? candidate.task_metrics?.[name.slice(5)] : candidate.metrics[name];
  const points = (currentRun?.candidates || []).filter((c) => Number.isFinite(value(c, x)) && Number.isFinite(value(c, y)));
  const container = byId("objective-plot");
  if (!points.length) { container.replaceChildren(element("p", "muted", "Measured objectives will appear after baseline execution.")); return; }
  const domain = (name) => {
    const values = points.map((p) => value(p, name));
    const low = Math.min(...values), high = Math.max(...values), pad = (high - low || Math.abs(low) || 1) * 0.12;
    return [low - pad, high + pad];
  };
  const [xmin, xmax] = domain(x), [ymin, ymax] = domain(y);
  const xp = (value) => 72 + (value - xmin) / (xmax - xmin) * 536;
  const yp = (value) => 266 - (value - ymin) / (ymax - ymin) * 226;
  const svg = svgElement("svg", {viewBox: "0 0 680 326", class: "objective-chart", role: "group", "aria-label": `${axisLabel(x)} versus ${axisLabel(y)}. Candidate values are also available in the comparison table.`});
  svg.append(svgElement("line", {x1: 72, y1: 266, x2: 608, y2: 266, class: "plot-axis"}), svgElement("line", {x1: 72, y1: 40, x2: 72, y2: 266, class: "plot-axis"}));
  for (let tick = 0; tick < 5; tick++) {
    const a = xmin + tick / 4 * (xmax - xmin), b = ymin + tick / 4 * (ymax - ymin);
    svg.append(svgElement("text", {x: xp(a), y: 288, "text-anchor": "middle", class: "plot-tick"}, number(a)), svgElement("text", {x: 65, y: yp(b) + 4, "text-anchor": "end", class: "plot-tick"}, number(b)));
  }
  svg.append(svgElement("text", {x: 340, y: 317, "text-anchor": "middle", class: "plot-label"}, axisLabel(x)), svgElement("text", {x: 72, y: 22, class: "plot-label"}, axisLabel(y)));
  for (const candidate of points) {
    const label = `${candidate.id.slice(-8)}: ${axisLabel(x)} ${number(value(candidate, x))}; ${axisLabel(y)} ${number(value(candidate, y))}; ${readable(candidate.state)}`;
    const point = svgElement("circle", {cx: xp(value(candidate, x)), cy: yp(value(candidate, y)), r: candidate.id === selectedCandidate ? 9 : 6, class: `plot-point${currentRun.frontier.includes(candidate.id) ? " is-frontier" : ""}`, tabindex: "0", role: "button", "aria-label": label});
    point.append(svgElement("title", {}, label));
    point.addEventListener("click", () => openCandidate(currentRun.run_id, candidate.id));
    point.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); openCandidate(currentRun.run_id, candidate.id); } });
    svg.append(point);
  }
  container.replaceChildren(svg);
}

function detailBlock(title, text, open = false) {
  const details = element("details", "scope-details");
  details.open = open;
  details.append(element("summary", "", title), element("pre", "evidence-text", text));
  return details;
}

async function openCandidate(runId, candidateId) {
  const version = ++inspectorVersion;
  selectedCandidate = candidateId;
  byId("inspect-candidate").value = candidateId;
  renderObjectivePlot();
  const container = byId("candidate-inspector");
  container.replaceChildren(element("p", "muted", "Loading retained evidence…"));
  try {
    const detail = await getJSON(`/api/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}`);
    if (version !== inspectorVersion || currentRun?.run_id !== runId || view !== "fix") return;
    container.replaceChildren(element("p", "inspector-title", detail.candidate.hypothesis || candidateId));
    if (detail.candidate.brief) container.append(element("p", "muted small", `Branch depth ${detail.candidate.brief.depth}/${detail.candidate.brief.depth_limit} · Permitted edits: ${detail.candidate.brief.editable_paths.join(", ")}`));
    container.append(detailBlock(`Candidate diff${detail.diff.truncated ? " (truncated)" : ""}`, detail.diff.text || "No source differences."));
    if (detail.review) container.append(detailBlock(`Independent review · ${detail.review.verdict}`, detail.review.rationale));
    for (const trial of detail.trials) {
      const block = element("section", "trial-evidence");
      block.append(element("h4", "", `Trial ${trial.trial_id.slice(-8)} · ${readable(trial.state)}`));
      if (trial.error) block.append(element("p", "error", trial.error));
      const events = trial.evidence?.events || [];
      const failures = events.filter((event) => event.event === "failure");
      block.append(element("p", "muted small", `${events.length} retained task events · ${failures.length} failures${trial.evidence?.incomplete ? " · incomplete evidence" : ""}${trial.evidence?.truncated ? " · retention limit reached" : ""}`));
      if (failures.length) block.append(detailBlock("Task failures", failures.map((event) => `${event.task_id}: ${JSON.stringify(event.data)}`).join("\n"), true));
      if (events.length) block.append(detailBlock("Task inputs, outputs and progress", events.slice(0, 100).map((event) => `${event.task_id} · ${readable(event.event)}\n${JSON.stringify(event.data, null, 2)}`).join("\n\n") + (events.length > 100 ? "\n\nShowing the first 100 events. Use fix inspect for the complete retained set." : "")));
      for (const command of trial.commands) block.append(detailBlock(`${command.id} · exit ${command.exit_code ?? "unknown"}`, `stdout${command.stdout_truncated ? " (truncated)" : ""}\n${command.stdout || ""}\n\nstderr${command.stderr_truncated ? " (truncated)" : ""}\n${command.stderr || ""}`));
      for (const command of trial.skipped_commands || []) block.append(element("p", "muted small", `${command.id} · skipped: ${command.reason}`));
      for (const [index, artifact] of (trial.evidence?.artifacts || []).entries()) {
        const link = element("a", "artifact-link", `${artifact.path} · ${number(artifact.bytes)} bytes`);
        link.href = `/api/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/trials/${encodeURIComponent(trial.trial_id)}/artifacts/${index}`;
        link.setAttribute("download", "");
        block.append(link);
      }
      if (trial.evidence?.rejected_artifacts?.length) block.append(element("p", "muted small", `${trial.evidence.rejected_artifacts.length} unsafe or oversized artifacts were excluded.`));
      container.append(block);
    }
    if (!detail.trials.length) container.append(element("p", "muted", "No execution evidence yet."));
  } catch (error) {
    if (version === inspectorVersion) container.replaceChildren(element("p", "error", error.message));
  }
}

async function loadEvaluations(version) {
  const data = await getJSON("/api/evaluations");
  if (version !== requestVersion) return;
  byId("workspace-name").textContent = data.workspace.split("/").filter(Boolean).pop();
  byId("workspace-path").textContent = data.workspace;
  byId("eval-count").textContent = `${data.evaluations.length} evaluation${data.evaluations.length === 1 ? "" : "s"} in this checkout`;
  const select = byId("eval-select");
  select.replaceChildren(...data.evaluations.map((evaluation) => {
    const option = element("option", "", `${readable(evaluation.state)} · ${evaluation.goal || evaluation.evaluation_id.slice(-8)}`);
    option.value = evaluation.evaluation_id;
    return option;
  }));
  select.disabled = !data.evaluations.length;
  if (!data.evaluations.length) {
    select.append(element("option", "", "No evaluations yet"));
    byId("eval-empty").hidden = false;
    byId("announcement").textContent = "No evaluations yet.";
    return;
  }
  selectedEvaluation ||= data.evaluations[0].evaluation_id;
  const evaluation = data.evaluations.find((item) => item.evaluation_id === selectedEvaluation);
  if (!evaluation) throw new Error("Evaluation not found in this checkout. Choose from the history selector.");
  select.value = selectedEvaluation;
  byId("evaluation").hidden = false;
  byId("eval-date").textContent = date(evaluation.created_at);
  byId("eval-state").textContent = readable(evaluation.state);
  byId("eval-goal").textContent = evaluation.goal || "Prepare checks for saved findings.";
  const content = byId("eval-content");
  content.replaceChildren(element("p", "pending", `${evaluation.usage.trials}/${evaluation.budget.max_trials} preparation trials used · ${evaluation.budget.max_elapsed_seconds}s draft time limit`));
  if (evaluation.coverage) content.append(element("p", "", `Coverage: ${readable(evaluation.coverage.status)}. ${evaluation.coverage.rationale}`));
  if (evaluation.review_branch) content.append(element("p", "muted", `Benchmark review branch: ${evaluation.review_branch}`));
  for (const trial of evaluation.trials) {
    const outcome = trial.state === "passed" ? trial.case_id === "baseline" ? "baseline expectations met" : trial.kind === "metric" ? "correctness checks passed" : "expected rejection confirmed" : readable(trial.state);
    content.append(element("p", "work-row", `${trial.case_id} · repetition ${trial.repetition + 1} · ${outcome}${trial.error ? ` · ${trial.error}` : ""}`));
  }
  if (evaluation.metric_comparison_error) content.append(element("p", "error", evaluation.metric_comparison_error));
  for (const comparison of evaluation.metric_comparisons || []) {
    const unit = evaluation.metrics[comparison.metric]?.unit || "";
    content.append(element("p", comparison.passed ? "work-row" : "error", `${comparison.metric} comparison · ${comparison.passed ? "passed" : "failed"} · ${comparison.better}: ${number(comparison.better_value)} ${unit}; ${comparison.worse}: ${number(comparison.worse_value)} ${unit} · improvement ${number(comparison.improvement)} ${unit}; required > 0 and ≥ ${number(comparison.min_delta)} ${unit}`));
  }
  if (!evaluation.trials.length) content.append(element("p", "muted", "The host is preparing the benchmark and expected behavior. No checks have run yet."));
  byId("announcement").textContent = `Evaluation ${readable(evaluation.state)}; ${evaluation.usage.trials} trials used.`;
}

function initWorkflow() {
  for (const id of ["objective-x", "objective-y"]) byId(id).addEventListener("change", renderObjectivePlot);
  byId("inspect-candidate").addEventListener("change", (event) => openCandidate(currentRun.run_id, event.target.value));
  byId("eval-select").addEventListener("change", (event) => {
    selectedEvaluation = event.target.value;
    const url = new URL(window.location.href);
    url.searchParams.set("evaluation", selectedEvaluation);
    url.searchParams.set("view", "eval");
    window.history.replaceState(null, "", url);
    load();
  });
}
