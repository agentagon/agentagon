import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, post, projectPath } from "./api";
import { Button } from "./components";
import { commandArguments, displayCommand } from "./execution";
import { MeasurementPlanView } from "./results";
import type { MeasurementPlan } from "./types";

type NativeEvaluator = { id: string; framework: string; entrypoint: string; command?: { argv: string[]; cwd?: string }; confidence: string; evidence: Array<{ path: string; line: number; detail: string }> };
type FrozenEvaluator = { evaluation_id: string; goal?: string; scoring?: NonNullable<MeasurementPlan["scoring"]> & { metrics?: MeasurementPlan["metrics"]; behaviors?: MeasurementPlan["behaviors"] } };
type DesignInventory = { draft: MeasurementPlan | null; evaluators: NativeEvaluator[]; frozen_evaluators: FrozenEvaluator[]; limitations: string[]; snapshots: Array<{ id: string; name: string; count: number; provenance?: { dataset_partition?: string }; missing_expectations?: number }>; attached_cases?: { dataset_snapshot_id?: string } | null };

export function EvaluationDesignEditor({ projectId, agentId, goalId, defaultReuse = false, onSaved, onCancel }: { projectId: string; agentId: string; goalId: string; defaultReuse?: boolean; onSaved: () => void; onCancel: () => void }) {
  const cache = useQueryClient();
  const route = projectPath(projectId, `/agents/${agentId}/goals/${goalId}/design`);
  const inventory = useQuery({ queryKey: ["projects", projectId, "agents", agentId, "goals", goalId, "design"], queryFn: () => api<DesignInventory>(route) });
  const [initialized, setInitialized] = useState(false);
  const [draft, setDraft] = useState<MeasurementPlan | null>(null);
  const [selection, setSelection] = useState(defaultReuse ? "" : "create");
  const [framework, setFramework] = useState("custom");
  const [entrypoint, setEntrypoint] = useState("");
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState(".");
  const [scorer, setScorer] = useState("");
  const [dataset, setDataset] = useState("");
  const [metric, setMetric] = useState("");
  const [direction, setDirection] = useState("max");
  const [unit, setUnit] = useState("score");
  const [mapping, setMapping] = useState("");
  const [bound, setBound] = useState("");
  const [behavior, setBehavior] = useState("");
  useEffect(() => {
    if (!inventory.data || initialized) return;
    const current = inventory.data.draft;
    setDraft(current);
    const source = current?.evaluation;
    if (source) {
      setSelection(source.evaluation_id || source.candidate_id || (source.mode === "reuse" ? "custom" : "create"));
      setFramework(source.framework || "custom"); setEntrypoint(source.entrypoint || "");
      if (source.command && typeof source.command === "object") { setCommand(displayCommand(source.command.argv)); setCwd(source.command.cwd || "."); }
      setScorer(source.scorer || "");
      setMapping(Object.entries(source.output_mapping || {}).map(([key, value]) => `${key}=${value}`).join("\n"));
    }
    setDataset(source?.dataset_snapshot_id || inventory.data.attached_cases?.dataset_snapshot_id || "");
    setInitialized(true);
  }, [inventory.data, initialized]);
  const selectedFrozen = inventory.data?.frozen_evaluators.find(item => item.evaluation_id === selection);
  const selectedNative = inventory.data?.evaluators.find(item => item.id === selection);
  function choose(value: string) {
    setSelection(value);
    if (inventory.data?.frozen_evaluators.some(item => item.evaluation_id === value)) setDataset("");
    const candidate = inventory.data?.evaluators.find(item => item.id === value);
    if (candidate) { setFramework(candidate.framework); setEntrypoint(candidate.entrypoint); setCommand(displayCommand(candidate.command?.argv)); setCwd(candidate.command?.cwd || "."); }
  }
  const save = useMutation({
    mutationFn: () => {
      if (!selection) throw new Error("Choose an evaluator or create a new one.");
      const frozenScore = selectedFrozen?.scoring;
      if (selectedFrozen && !frozenScore) throw new Error("This evaluator has no reusable scoring definition. Create a new evaluation instead.");
      const definition = frozenScore || draft;
      const preservedScoring = frozenScore ? Object.fromEntries(Object.entries(frozenScore).filter(([key]) => ["mode", "primary", "custom_metric", "target"].includes(key))) : draft?.scoring;
      if (!definition?.metrics && !/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(metric)) throw new Error("Enter a metric name, such as success_rate or latency_ms.");
      if (!definition?.metrics && (!behavior.trim() || bound.trim() === "" || !Number.isFinite(Number(bound)))) throw new Error("Describe the required behavior and its passing threshold.");
      const metrics = definition?.metrics || { [metric]: { direction, aggregation: "mean", missing: "fail", unit } };
      const behaviors = definition?.behaviors || [{ id: "required_behavior", description: behavior.trim(), required: true, metric, op: direction === "max" ? "gte" : "lte", bound: Number(bound) }];
      const scoring = preservedScoring || { mode: "primary", primary: metric };
      let outputMapping: Record<string, string> = {};
      if (mapping.trim()) {
        for (const line of mapping.split("\n").filter(line => line.trim())) {
          const separator = line.indexOf("=");
          if (separator < 1 || !line.slice(separator + 1).trim()) throw new Error("Map outputs as metric_name=result.path, one per line.");
          outputMapping[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
        }
      }
      const evaluation = selectedFrozen ? { mode: "reuse", evaluation_id: selectedFrozen.evaluation_id } : {
        mode: selection === "create" ? "create" : "reuse", framework,
        ...(selectedNative ? { candidate_id: selectedNative.id } : {}),
        ...(entrypoint.trim() ? { entrypoint: entrypoint.trim() } : {}),
        ...(command.trim() ? { command: { argv: commandArguments(command), cwd: cwd.trim() || "." } } : {}),
        scorer: scorer.trim(), output_mapping: outputMapping,
        ...(dataset ? { dataset_snapshot_id: dataset } : {}),
      };
      if (selectedFrozen && dataset) throw new Error("A frozen evaluator keeps its original cases. Choose Create evaluation to use this dataset.");
      return post(route, { expected_revision: draft?.revision || 0, behaviors, metrics, scoring, evaluation, evidence: draft?.evidence || [], background: draft?.background || [], limitations: draft?.limitations || [] });
    },
    onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["projects", projectId] }); onSaved(); },
  });
  if (inventory.isLoading || (!initialized && inventory.data)) return <p role="status">Inspecting evaluators and saved cases…</p>;
  if (!inventory.data) return <div role="alert" className="error-banner">{inventory.error?.message || "Evaluator discovery is unavailable."}<Button onClick={() => inventory.refetch()}>Retry</Button></div>;
  const preservesChecks = Boolean(selectedFrozen?.scoring || draft?.metrics);
  return <form className="settings-card form evaluation-design-editor" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
    <h3>{defaultReuse ? "Choose an existing evaluator" : "Evaluation draft"}</h3>
    <p className="quiet-copy">Inspection reads source only. Saving a draft does not run commands or accept scoring.</p>
    <label>Evaluator<select value={selection} onChange={event => choose(event.target.value)} required><option value="">Choose an evaluator</option>{Boolean(inventory.data.frozen_evaluators.length) && <optgroup label="Frozen and reviewed">{inventory.data.frozen_evaluators.map(item => <option key={item.evaluation_id} value={item.evaluation_id}>{item.goal || item.evaluation_id}</option>)}</optgroup>}{Boolean(inventory.data.evaluators.length) && <optgroup label="Discovered in this project">{inventory.data.evaluators.map(item => <option key={item.id} value={item.id}>{item.entrypoint} · {item.framework}</option>)}</optgroup>}<option value="custom">Use an existing custom command</option><option value="create">Create a new evaluator</option></select></label>
    {!inventory.data.evaluators.length && !inventory.data.frozen_evaluators.length && <p className="quiet-surface">No evaluator was discovered. Enter an existing command or choose Create a new evaluator.</p>}
    {selectedNative && <details><summary>Source evidence</summary>{selectedNative.evidence.map((item, index) => <p key={index}><code>{item.path}:{item.line}</code> · {item.detail}</p>)}<p>Discovery confidence: {selectedNative.confidence}. This does not establish evaluator quality or permission to execute.</p></details>}
    {selection && (selectedFrozen ? <><p className="quiet-surface">Reuses the exact frozen cases and scoring. Saving verifies that any attached cases are already included. To add new cases, choose Create a new evaluator.</p><MeasurementPlanView plan={{ metrics: selectedFrozen.scoring?.metrics, behaviors: selectedFrozen.scoring?.behaviors, scoring: selectedFrozen.scoring, evaluation: { mode: "reuse", evaluation_id: selectedFrozen.evaluation_id } }} /></> : <>
      <div className="form-grid"><label>Framework<select value={framework} onChange={event => setFramework(event.target.value)}><option value="custom">Custom</option><option value="pytest">pytest</option><option value="braintrust">Braintrust</option><option value="deepeval">DeepEval</option></select></label><label>Evaluator source<input value={entrypoint} onChange={event => setEntrypoint(event.target.value)} placeholder="evals/check_behavior.py" required={selection !== "create"} /><small>Path relative to this project.</small></label></div>
      <label>Evaluation command<input value={command} onChange={event => setCommand(event.target.value)} placeholder="python evals/check_behavior.py" /><small>Arguments support quotes; no shell expansion. Commands run only after an explicit workflow start.</small></label>
      <label>Working directory<input value={cwd} onChange={event => setCwd(event.target.value)} /></label>
      <label>Dataset<select value={dataset} onChange={event => setDataset(event.target.value)}><option value="">Use evaluator-owned inputs</option>{inventory.data.snapshots.filter(item => item.provenance?.dataset_partition !== "final_holdout").map(item => <option key={item.id} value={item.id}>{item.name} · {item.count} cases{item.missing_expectations ? " · expectations incomplete" : ""}</option>)}</select></label>
      <label>Scorer and correctness rule<textarea value={scorer} onChange={event => setScorer(event.target.value)} rows={3} placeholder="Explain how output is checked, or name the scorer source." /></label>
      {preservesChecks ? <details><summary>Preserved measures and required guards</summary><MeasurementPlanView plan={draft!} /></details> : <><div className="form-grid"><label>Primary metric<input value={metric} onChange={event => setMetric(event.target.value)} placeholder="success_rate" required /></label><label>Unit<input value={unit} onChange={event => setUnit(event.target.value)} required /></label><label>Better values<select value={direction} onChange={event => setDirection(event.target.value)}><option value="max">Higher</option><option value="min">Lower</option></select></label><label>Required passing threshold<input type="number" step="any" value={bound} onChange={event => setBound(event.target.value)} required /></label></div><label>Required behavior<textarea value={behavior} onChange={event => setBehavior(event.target.value)} rows={2} required /></label><p className="quiet-copy">The draft uses the mean of this metric. Missing values fail; the required behavior must meet the threshold.</p></>}
      <details><summary>Native output mapping</summary><label>Metric output paths<textarea value={mapping} onChange={event => setMapping(event.target.value)} placeholder={Object.entries(draft?.evaluation?.output_mapping || {}).map(([key, value]) => `${key}=${value}`).join("\n") || "success_rate=summary.success_rate"} /><small>One metric_name=result.path per line. Leave blank to remove mappings from this draft.</small></label></details>
    </>)}
    {inventory.data.limitations.length > 0 && <details><summary>Discovery limits</summary><ul>{inventory.data.limitations.map(item => <li key={item}>{item}</li>)}</ul></details>}
    {save.error && <p className="error-banner" role="alert">{save.error.message}</p>}
    <footer className="form-actions"><Button type="button" tone="secondary" onClick={onCancel} disabled={save.isPending}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save evaluation draft"}</Button></footer>
  </form>;
}
