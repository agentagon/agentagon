---
name: eval
description: Create, repair or reuse an agent evaluation from agreed behaviors and scoring; validate sensitivity, independently review, freeze and prepare eval-only delivery.
---

# Agentagon Eval

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"eval"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

Follow the shared [dashboard lifecycle](../dashboard/references/lifecycle.md). Inspect `status` and reuse matching saved intent, evaluation drafts, profiles and authorized limits.

Use the shared [goals, scoring and authoring procedure](references/authoring.md) to resolve behaviors, executable checks, scoring and judge setup. Reuse suitable existing eval code. An explicit request to create or repair evals authorizes that work; otherwise confirm missing/unusable evals and the proposed creation/running before editing.

Continue through [evaluation preparation](../fix/references/evaluation.md): edit only scoped repository eval files in the returned preparation worktree, validate the baseline and negative controls, check known metric rankings and judge examples, obtain independent review, and freeze. Save intent and evidence through validated CLI operations. Private inputs remain private; intended cases, assertions, judge prompts, scorers and harness source belong in the repository and declared delivery paths.

A dataset, behavior, scorer or judge change creates a new evaluator version. Compare evaluation quality at fixed application source. Never claim application improvement from scores on different evaluators. Missing executable prerequisites leave a precise pending action; a coding-host judge cannot replace an application that cannot run.

Finish with [eval-only delivery](../fix/references/delivery.md), the evaluator identity, observed validation, coverage limits and baseline if measured. Application optimization belongs to [Fix](../fix/SKILL.md) and requires the corresponding user request. Keep earlier versions and private evidence intact.
