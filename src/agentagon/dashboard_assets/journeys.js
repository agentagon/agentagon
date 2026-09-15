"use strict";

async function journeyPost(path, payload) {
  await getControlSession();
  const response = await fetch(path, {
    method: "POST", mode: "same-origin", credentials: "omit",
    headers: {"Content-Type": "application/json", "X-Agentagon-Token": controlSession.token},
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Unable to save this operation.");
  return result;
}

function scoreSummary(score) {
  if (!score) return "Unmeasured";
  const value = typeof score === "number" ? score : score.value ?? score.score;
  return typeof value === "number" ? String(value) : readable(score.state || "unmeasured");
}

function reportLinks(kind, id) {
  const links = element("p", "artifact-links");
  for (const [ext, label] of [["md", "Readable report"], ["json", "JSON report"]]) {
    const link = element("a", "", label);
    link.href = `/api/${kind}/${encodeURIComponent(id)}/report.${ext}`;
    link.download = `agentagon-${id}.${ext}`;
    links.append(link, document.createTextNode(" "));
  }
  return links;
}

async function loadJourneyView(name, version) {
  const data = await getJSON(name === "baselines" ? "/api/baselines" : "/api/settings");
  await getControlSession();
  if (version !== requestVersion) return;
  byId(name).hidden = false;
  if (data.workspace) {
    byId("workspace-name").textContent = data.workspace.split("/").filter(Boolean).pop();
    byId("workspace-path").textContent = data.workspace;
  }
  if (name === "baselines") renderBaselines(data.baselines);
  else renderSettings(data);
}

function renderBaselines(records) {
  const container = byId("baseline-history");
  container.replaceChildren();
  if (!records.length) {
    container.append(element("p", "", "No baselines yet. Run ag:init in your coding agent to agree on goals and measure current code."));
    return;
  }
  for (const record of records) {
    const card = element("section", "workflow-card baseline-card");
    card.append(element("h3", "", `${record.branch || "Detached source"} · ${(record.source_revision || "unknown").slice(0, 12)}`));
    card.append(element("p", "muted small", `${record.created_at ? date(record.created_at) : ""} · ${readable(record.state)}`));
    card.append(element("p", "", `Evaluator: ${record.evaluator_digest || record.evaluation_id || "unknown"}`));
    const scores = element("dl", "baseline-scores");
    scores.append(element("dt", "", "Fixed benchmark"), element("dd", "", scoreSummary(record.benchmark_score)));
    const traces = record.recent_traces || {};
    scores.append(element("dt", "", "Recent traces"), element("dd", "", scoreSummary(traces.score)));
    card.append(scores, element("p", "muted small", `Acquisition: ${readable(traces.state || traces.status || "unavailable")} · Provider: ${traces.provider || "none"}`));
    if (record.benchmark_score?.judging === "coding-agent judged") card.append(element("p", "muted small", "Benchmark: coding-agent judged"));
    if (traces.window) card.append(element("p", "muted small", `Trace window: ${traces.window.start || "unknown"} – ${traces.window.end || "unknown"}`));
    if (traces.alignment) card.append(element("p", "muted small", `Revision alignment: ${typeof traces.alignment === "string" ? traces.alignment : readable(traces.alignment.status)}`));
    if (traces.next_action) card.append(element("p", "pending", `Trace evidence: ${traces.next_action}`));
    if (traces.score?.judging) card.append(element("p", "muted small", `${traces.score.judging} · ${traces.score.measured_count || 0} measured traces`));
    if (record.pending_action) card.append(element("p", "pending", `Next: ${typeof record.pending_action === "string" ? record.pending_action : readable(record.pending_action.kind || record.pending_action.action)}`));
    card.append(reportLinks("baselines", record.baseline_id));
    if (controlSession?.controls_enabled && record.evaluation_id) {
      const button = element("button", "button", "Rerun baseline");
      const feedback = element("p", "muted small");
      feedback.setAttribute("role", "status");
      // Keep the same request identity on an uncertain network response.
      const payload = {baseline_id: record.baseline_id, operation_id: crypto.randomUUID()};
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await journeyPost("/api/baselines/rerun", payload);
          feedback.textContent = `Saved ${result.baseline_id}. ${readable(result.state)}. Refresh to see progress; this job survives closing the page.`;
        } catch (error) {
          feedback.textContent = error.message;
          button.disabled = false;
        }
      });
      card.append(button, feedback);
    }
    container.append(card);
  }
}

function renderSettings(data) {
  const container = byId("settings-content");
  container.replaceChildren();
  const form = element("form", "settings-form");
  const fields = [];
  for (const section of ["traces", "intelligence"]) {
    const group = element("fieldset");
    group.append(element("legend", "", section === "traces" ? "Trace provider" : "Optional Intelligence"));
    for (const [key, value] of Object.entries(data.settings[section] || {})) {
      if (key === "access_presented" || (typeof value !== "string" && value !== null)) continue;
      const id = `setting-${section}-${key}`;
      const label = element("label", "", `${section}.${key}`);
      label.htmlFor = id;
      const input = element("input");
      input.id = id;
      input.value = value || "";
      input.disabled = !controlSession?.controls_enabled;
      input.autocomplete = "off";
      fields.push({input, key: `${section}.${key}`, original: input.value});
      group.append(label, input);
    }
    form.append(group);
  }
  if (controlSession?.controls_enabled) {
    const save = element("button", "button", "Save settings");
    save.type = "submit";
    const feedback = element("p", "muted small");
    feedback.setAttribute("role", "status");
    let pending = null;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      if (!pending) {
        const values = {}, unset = [];
        for (const field of fields) {
          if (field.input.value === field.original) continue;
          if (field.input.value.trim()) values[field.key] = field.input.value.trim();
          else unset.push(field.key);
        }
        pending = {version: 1, operation_id: crypto.randomUUID(), expected_revision: data.revision, values, unset};
      }
      try {
        const result = await journeyPost("/api/settings", pending);
        data.revision = result.revision;
        for (const field of fields) field.original = field.input.value;
        pending = null;
        feedback.textContent = "Project settings saved.";
      } catch (error) { feedback.textContent = error.message; }
      save.disabled = false;
    });
    form.append(save, feedback);
  } else container.append(element("p", "muted", "Restart the dashboard with --controls to change settings or rerun a baseline."));
  container.append(form);
}
