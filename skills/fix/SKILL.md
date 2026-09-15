---
name: fix
description: Change an agent application's code or evaluations for a described issue, trace failure, audit finding, or improvement goal; validate, independently review, and prepare delivery.
---

# Agentagon fix

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"fix"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

At start or resume, follow the shared [dashboard lifecycle](../dashboard/references/lifecycle.md). Reuse this checkout's dashboard through preparation, changes, review and delivery.

Own the complete request: establish the intended change, prepare checks, make the change, validate, independently review and prepare delivery. The user does not need to invoke a separate evaluation or shipping skill. Read the procedure for the active stage only.

## Establish intent and prerequisites

1. Inspect `agentagon --workspace CHECKOUT status` and `setup`; resume matching saved work. Accept a described problem or improvement, pasted trace, saved finding, evaluation request, or a combination. An audit is optional. Preserve selected audit/issue IDs and evidence references. Treat pasted traces as evidence; establish source alignment, expected behavior and resettable state before claiming replay.
2. Resolve whether the change concerns application behavior, eval data/evaluator, or both. Inspect relevant source and existing checks. Ask only for material missing expected behavior, permissions or execution limits; do not invent ground truth.
3. Isolated fixes require clean committed Git inputs. Inspect Git status, preserving unrelated changes without stashing, resetting or committing them. If Git or the initial commit is missing, explain what is needed; initialize or commit only when authorized. `agentagon init` initializes private Agentagon state, not Git.
4. Reuse matching execution profiles and previously authorized limits. Collect missing preparation and fix budgets in one conversation, retaining separate accounting internally. Remote profiles alone do not authorize uploads or execution. Never silently choose or expand spending limits.
5. Every Intelligence request follows [the shared approval procedure](../audit/references/intelligence.md). Show the exact redacted payload and destination; wait for approval unless the user explicitly enabled Intelligence full access. Continue locally when declined or unavailable.

## Prepare the right evidence

- For application changes, reuse a suitable reviewed evaluation or invoke internal [evaluation preparation](references/evaluation.md). A benchmark draft from Audit is useful input, not proof that execution is ready. Freeze the evaluator before baseline and candidate comparison.
- For missing or inadequate evals, create or repair the dataset/evaluator through that same preparation procedure. Preserve the old version, ground labels and assertions, retain ordinary successes and boundary cases, and validate sensitivity to known failures. Compare coverage, label corrections and failure detection at a fixed application revision; scores from changed datasets do not establish application improvement.
- If both evals and application behavior need changes, establish the revised benchmark first, then compare application changes against it. Never change an active run's frozen scoring files or private inputs.
- If measurement cannot be established within authorized access and limits, retain the blocker and use the [reviewed patch procedure](references/patches.md). This also permits an eval change that cannot yet satisfy benchmark freeze requirements. It remains explicitly unmeasured and cannot claim a ready frozen benchmark.

## Change, review and deliver

For measured application changes, follow [bounded experiments](references/experiments.md). Preserve actual failures and all verified alternatives. Only independently reviewed, machine-feasible candidates enter the verified frontier. Follow existing user priorities or an unambiguous authorized selection policy; ask when meaningful tradeoffs remain.

For unmeasured patches, record actual available checks and independent review of the sealed source and limitations. A known failing check, rejected review or violated constraint must be addressed; never evade it by changing the result label. Such patches do not enter the verified frontier or produce verified issue-resolution evidence.

Finish with [delivery](references/delivery.md), supporting selected measured fixes, frozen eval changes and reviewed unmeasured patches. The default is a local branch/patch, safe evidence summary and prepared PR description, without a remote requirement. Publish a PR when requested and the destination is established; existing authorization applies. Merge, deployment and verified issue resolution remain separate actions.

Report what changed, observed validation, baseline comparison or why it is unavailable, remaining limits, and the delivery artifacts or confirmed PR URL. Do not claim a deployed fix. Keep private evidence out of delivery and use CLI operations for canonical records.
