---
name: init
description: Onboard an agent application by recommending a useful first quality check, reusing or preparing evaluations, and measuring a baseline within agreed limits.
---

# Agentagon Init

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"init"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

At start or resume, follow the shared [dashboard lifecycle](../dashboard/references/lifecycle.md). Reuse this checkout's dashboard throughout onboarding, evaluation preparation and baseline measurement.

Own onboarding through a reusable evaluator and a scored baseline when execution is ready. `agentagon init` initializes private state; it does not initialize Git or complete this journey. Read `status` and `setup` first and reuse saved goals, provider connections, execution profiles, authorization and pending records.

## Make the next step easy to understand

Lead with what the developer will learn about their agent and the recommended next step. Use a short paragraph or a few bullets: the useful check, why it fits this application, and the total time/run/cost limits or the prerequisite that prevents it. Ask only for a decision or authorization that is still missing; otherwise continue. Explain scores in ordinary language, such as “the percentage of tasks completed correctly.”

Keep discovery inventories, revisions, evaluator IDs, archived runs, scoring formulas, per-stage allocations and routine permission checks in saved evidence and the dashboard. Surface a detail when it changes the recommendation, permission needed or interpretation of a result. Shared procedures define the work and records required; their reporting lists are not an onboarding response template. Provide the detailed plan on request.

Recommend one path that addresses the user's goal. A local assertion suite may check tool permissions or output formatting, but it cannot establish LLM answer quality. Do not substitute an easy-to-run check for the intended outcome without explaining the narrower benefit. Offer an alternative only when a real tradeoff needs the user's choice. Present optional Intelligence only when a useful lookup is proposed, following its consent procedure.

## Inspect and agree

Use the focused procedures for [goal definition](../workflows/define-goal.md), [evidence analysis](../workflows/analyze-evidence.md), [measurement design](../workflows/design-measurement.md) and [native evaluator adaptation](../workflows/adapt-evaluation.md). Their outputs are shared with the web app; keep execution and acceptance in the validated host workflow.

1. Inspect application requirements, entry points, configuration and existing evals far enough to recommend a useful first measurement. Discovery accepts dirty or non-Git directories. A full audit is not a prerequisite for that recommendation. When a full audit is requested or needed to support a claim, follow [Audit](../audit/SKILL.md), preserving its complete rubric, evidence scope and trace-alignment limits. Describe initial inspection as discovery, not a completed audit or measured coverage.
2. Use the shared [goals, scoring and eval authoring procedure](../eval/references/authoring.md). Infer relevant behaviors and propose a scoring definition, required gates and one understandable time/evaluation budget from the evidence. Present their practical meaning using the response guidance above; let the user customize them without requiring them to design the evaluation. Do not turn audit facets into a universal score.
3. Resolve existing eval code before proposing new evals. If usable evals are absent, confirm that finding and the proposed creation/running of missing evals. An explicit request to create evals already supplies that authorization; do not ask again.
4. Reuse configured provider and judge access. Follow [Setup](../setup/SKILL.md) only for missing settings. Store credential references. A remote execution profile alone does not authorize uploads or execution. Optional Intelligence follows its separate [consent procedure](../audit/references/intelligence.md).

## Establish the baseline

Run the shared [evaluation preparation](../fix/references/evaluation.md), preserving application source. Cases, assertions, judge prompts, scoring and execution logic belong in the user's repository. Save accepted intent and supporting evidence through CLI operations in private `.agentagon/` records.

Validate representative known judgments and sensitivity, obtain independent review and freeze the evaluator before comparison. Measure a fresh baseline with the saved definition and execution settings. Init-only work budgets its applicable preparation and baseline stages; it does not reserve an optimization stage. Missing source, access, judge or execution prerequisites remain visible and do not discard discovery.

Use [evaluation review](../workflows/review-evaluation.md) and [baseline interpretation](../workflows/interpret-baseline.md) for their distinct evidence requirements.

Deliver reviewed eval creation as an eval-only draft PR when the destination and authentication are configured; otherwise prepare the local branch/patch using [delivery](../fix/references/delivery.md). Do not create an application patch during onboarding. Lead the result with what was measured, the score and relevant failures, or the precise blocker and next step. State material limits on what the result proves. Link the delivered eval changes and dashboard; retain the full agreed definition, evaluator identity and execution evidence there.

After the first successful journey, offer one non-blocking invitation to [star Agentagon](https://github.com/agentagon/agentagon), only when `agentagon journey invitation` returns that the invitation should be shown. Never star automatically or repeat the invitation for each run.
