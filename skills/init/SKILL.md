---
name: init
description: Onboard an agent application, agree on behaviors, scoring and limits, reuse or prepare evaluations, and establish a scored baseline.
---

# Agentagon Init

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"init"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

At start or resume, follow the shared [dashboard lifecycle](../dashboard/references/lifecycle.md). Reuse this checkout's dashboard throughout onboarding, evaluation preparation and baseline measurement.

Own onboarding through a reusable evaluator and a scored baseline when execution is ready. `agentagon init` initializes private state; it does not initialize Git or complete this journey. Read `status` and `setup` first and reuse saved goals, provider connections, execution profiles, authorization and pending records.

## Inspect and agree

1. Inspect application requirements, entry points, configuration and existing evals. Follow [Audit](../audit/SKILL.md) for evidence capture and the fixed discovery rubric; preserve its scope and trace-alignment limits. Discovery accepts dirty or non-Git directories.
2. Use the shared [goals, scoring and eval authoring procedure](../eval/references/authoring.md). Propose a small set of relevant behaviors, a scoring definition, required gates and one understandable time/evaluation budget. Let the user customize them. Do not turn audit facets into a universal score.
3. Resolve existing eval code before proposing new evals. If usable evals are absent, confirm that finding and the proposed creation/running of missing evals. An explicit request to create evals already supplies that authorization; do not ask again.
4. Reuse configured provider and judge access. Follow [Setup](../setup/SKILL.md) only for missing settings. Store credential references. A remote execution profile alone does not authorize uploads or execution. Optional Intelligence follows its separate [consent procedure](../audit/references/intelligence.md).

## Establish the baseline

Run the shared [evaluation preparation](../fix/references/evaluation.md), preserving application source. Cases, assertions, judge prompts, scoring and execution logic belong in the user's repository. Save accepted intent and supporting evidence through CLI operations in private `.agentagon/` records.

Validate representative known judgments and sensitivity, obtain independent review and freeze the evaluator before comparison. Measure a fresh baseline with the saved definition and execution settings. Init-only work budgets its applicable preparation and baseline stages; it does not reserve an optimization stage. Missing source, access, judge or execution prerequisites remain visible and do not discard discovery.

Deliver reviewed eval creation as an eval-only draft PR when the destination and authentication are configured; otherwise prepare the local branch/patch using [delivery](../fix/references/delivery.md). Do not create an application patch during onboarding. Report the agreed behaviors, scoring and limits, evaluator identity, measured baseline or precise blocker, and the dashboard link.

After the first successful journey, offer one non-blocking invitation to [star Agentagon](https://github.com/agentagon/agentagon), only when `agentagon journey invitation` returns that the invitation should be shown. Never star automatically or repeat the invitation for each run.
