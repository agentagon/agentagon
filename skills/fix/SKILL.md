---
name: fix
description: Improve saved goals or a named issue with bounded Omni optimization, frozen evaluations and independent verification; select an improvement and prepare a draft PR.
---

# Agentagon Fix

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"fix"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

At start or resume, follow the shared [dashboard lifecycle](../dashboard/references/lifecycle.md). Own the request through preparation, measurement, independent review, selection and delivery.

## Establish intent and prerequisites

1. Inspect `status`, `setup` and `journey status`. Bare Fix uses the saved goal and a compatible baseline. A named issue narrows the requested improvement. Preserve audit/issue IDs, expected behavior and source alignment. An audit is optional.
2. Reuse accepted behaviors, scoring and execution limits through the shared [authoring procedure](../eval/references/authoring.md). Resolve whether the request concerns application behavior, eval quality or both. Eval changes create a new evaluator version; compare their quality at fixed application source.
3. Measured execution requires clean committed Git inputs. Preserve unrelated changes; do not stash, reset or commit them to satisfy the workflow. `agentagon init` initializes private state, not Git.
4. Reuse the overall budget and configured profile. Start with 20% preparation/baseline, 60% optimization and 20% final verification. Check minimum feasibility before spending. Unused preparation flows to optimization; final verification retains its reserve. Count actual evaluations, retries and final trials, plus applicable host time/cost. Never silently extend limits. A remote profile alone does not authorize uploads or execution.
5. Optional Intelligence uses its separate [approval procedure](../audit/references/intelligence.md); declining or missing access does not block local work.

## Prepare measurement

Reuse a suitable reviewed evaluator or follow shared [evaluation preparation](references/evaluation.md). Confirm missing/unusable eval creation and execution unless already requested. Keep cases, assertions, judge prompts, scorers and execution logic in the repository, with private intent and evidence saved through CLI operations.

Freeze the evaluator before baseline and candidate comparison. Application candidates cannot change its data, behaviors, scores or judge configuration. If both evals and application behavior need changes, establish and deliver the eval version first. Scores from changed datasets cannot establish application improvement.

Without a runnable baseline, preserve the blocker. Use the separate [reviewed patch procedure](references/patches.md) only when the user explicitly requests an unmeasured application patch. A coding-host judge cannot substitute for an application that cannot execute.

## Optimize, verify and select

Use Omni by default: bounded exploration across GEPA, Agentagon's native-host AutoResearch adapter and Agentagon's native-host Meta-Harness adapter, followed by fresh GEPA refinement of the strongest candidate. GEPA, AutoResearch and Meta-Harness are advanced standalone options. Start with three quarters of optimization capacity split evenly across exploration and one quarter for refinement; respect actual host concurrency.

Run the [native-host optimizer and request procedure](references/native-host.md), servicing each available proposal, grading and review request before advancing the coordinator. The optimizer proposes work; Agentagon enforces scope, admission budgets, complete attempt history, measurement validity, gates and review. Use durable host request/reply identities for proposals, grading and independent reviews. Honor the saved host/model and role; unavailable work stays pending. Resume the same request without duplicating edits, evaluations or charges. Keep judge configuration separate from proposer configuration.

Use [optimization context](../workflows/prepare-optimization-context.md) to bind accepted evidence and optional approved guidance. Use [candidate review](../workflows/review-candidate.md) for each finalist and [delivery preparation](../workflows/prepare-delivery.md) after selection. These procedures are also bundled into the web app.

Allow code, prompts, harnesses, tool code/names and ordinary configuration within agreed scope. Directional model substitutions should be a small fixed step within the existing family or configured router. Broader provider/model changes and permission expansion require approval.

Follow [bounded experiments](references/experiments.md) for execution and review invariants. Every failed or dominated attempt remains evidence. Stop when a target-reaching candidate passes final verification or the budget ends. Select the highest-scoring independently verified candidate that satisfies all gates and establishes improvement over baseline. Preserve the user's ability to choose another verified alternative; retain the baseline when no improvement is established.

Retain the configured number of finalists, default three and bounded from one to ten, excluding the baseline. Report their score components, checks, limits and concrete changes. Verify each against the full required suite within the reserved budget and show fewer when fewer qualify. Never substitute host-written score JSON for bound execution or grading observations.

## Deliver

Follow [delivery](references/delivery.md). Prepare a draft PR for a verified application winner or reviewed eval-only change when the destination and authentication are configured, honoring existing user instructions and publication scope; otherwise return a local branch/patch. If an eval PR remains open, stack the application PR on its exact reviewed eval parent and explain merge order. The delivery operation must validate ancestry; changing only the PR base is insufficient.

Return actual measurements, source/evaluator identities, coverage limits, a readable report and safe JSON, and the confirmed PR URL or local artifacts. Exclude raw traces, private inputs and credentials. Merging and deployment remain separate actions.

After a successful journey, call `agentagon journey invitation` and show the non-blocking star invitation only when its returned state requests it. Never star automatically.
