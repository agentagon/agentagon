# Fixed analysis bundle v1

Read the catalog returned by `agentagon resources`; use its shared judgment rules, exact facets, values, evidence rules, and counterexamples. Review every packet subject/facet exactly once, using `not_applicable` when appropriate rather than forcing a positive or negative value.

## Evidence before diagnosis

CLI measurements retain missing/error states and supporting span IDs. Treat flags as investigation candidates, not conclusions.

Use `computed` with a supported value and evidence IDs. Otherwise set the value to null: `unknown` means inspected evidence is inconclusive; `not_applicable`, the criterion does not apply; `not_evaluated`, uninspected; `error`, evaluation failed. Give a reason and confidence for each judgment; do not batch-fill unsupported positive judgments.

The catalog distinguishes task outcomes, decisions within a span, expressed user sentiment and code review. These are not independent quality scores: sentiment is context, and a well-chosen action can fail for reasons outside the agent's control. Inspect only the current span's output as its generated response; copied conversation history may supply context but must not create repeated defects. Missing reasoning, feedback or telemetry must follow the facet's applicability and uncertainty rules.

Partial fulfillment and mixed feedback create diagnosis candidates alongside the existing negative values. Explain the unmet requirements or negative feedback before deciding whether they indicate an actionable defect. Multiple facets describing one failure must not inflate the finding count.

## Code review

Inspect each code unit in context: relevant calls, prompts, tools, schemas, error paths, and tests. Cite the registered code-unit ID covering the relevant lines; an inventory alone is not review.

For `code_scope: changes`, units contain captured changed hunks with before/after paths and ranges. Read the saved diff evidence and bounded context, including removed lines; use related unchanged code only to understand changed behavior. Every finding must cite change evidence and explain the problem introduced or worsened by the edit, or an improvement to the edit. Do not report an unrelated pre-existing defect merely because it is nearby. For `code_scope: full`, the goal does not hide severe unrelated findings.

Correlate runtime and code only when references and deployed/source versions support the connection. Cite both and explain the connection. Full audits assume user-supplied traces belong to the captured revision; retain this as an unverified assumption when metadata is absent. Contradictory metadata prevents that correlation. For changes reviews, explain the traces' relationship to the captured local edits; the full-audit assumption does not establish that traces exercised uncommitted changes.

## Findings and opportunities

For each flag, explain a supported finding, dismissal, or insufficient evidence. Successful recovery may leave no actionable issue. Do not label unrelated diagnoses as tool failures or infer causality from correlation.

Each finding covers one problem: expected versus observed behavior, authority, severity, confidence, evidence, and a supported next investigation or root-cause hypothesis. Separate different problems within one trace.

Inspect these fixed opportunities independently of failures:

- **Redundant work:** equivalent requests or computations without a need for freshness, recovery, side effects, or repeated checks.
- **Avoidable serialization:** sequential work whose dependencies permit overlap without violating authorization, shared-state ordering, rate limits or resource constraints. Include coordination overhead when assessing benefit. Overlapping spans alone prove neither an issue nor a speedup.
- **Unnecessary context or tools:** context or tool work demonstrably adds no needed information or required action, including work that was unnecessary even on its first occurrence. Account for verification and authorization needs; explain the evidence and uncertainty.

Set `improvement: true` for opportunities. Require measurements for savings claims and an applicable requirement or comparable baseline for cost/latency concerns. Missing pricing stays unavailable. Do not sum ancestor aggregates with child usage.

Review changed behavior's verification independently of whether a defect was found. An actionable eval recommendation uses `kind: evaluation_coverage` and `improvement: true`, and describes the target eval/file, concrete scenario or input, expected behavior or assertion, and why that edit needs coverage in `hypothesis`. Reuse an applicable existing eval; otherwise propose a new case without claiming it already exists. Distinguish recommendations from executed checks. Retain defects, improvements and eval recommendations as distinct findings when they call for different actions.

## Grouping and priority

Group findings by the same evidenced problem or improvement mechanism; keep positive observations separate. Consult the complete saved issue catalog, including rare, dismissed, and resolved issues. Reuse matching IDs without clearing their status.

Explain each grouping. Similar wording, tool, or severity is insufficient; separate opposite observations and distinct causes. One trace can establish a severe issue.

The report sorts by severity, distinct affected traces, then confidence. Explain impact and evidence limits. Use CLI counts and percentages; keep runtime prevalence unknown for code-only findings.
