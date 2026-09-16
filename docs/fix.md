# Improve agents and evals

Use **ag:fix** to improve the saved goal or a named issue. Fix reuses accepted behaviors, scoring and limits, compares candidates against a frozen evaluator, independently verifies improvements, and prepares a draft PR or local branch/patch.

<span id="start-with-your-coding-agent"></span>

## Describe the outcome

After [Init](init.md), choose **ag:fix** in Codex or run `/ag:fix` in Claude Code. A bare invocation uses the saved goal and compatible baseline. To focus it:

```text
Fix duplicate-ticket failures. Reuse the saved behaviors, evaluator and
budget. Compare verified improvements against the baseline and prepare
a draft PR if the destination is configured.
```

A known problem can also start Fix directly. The host resolves requirements and suitable evals first. Missing or unusable eval creation is confirmed unless already requested. Expected behavior comes from trusted requirements and evidence; trace outputs are not automatically correct labels.

For eval work, request the dataset or evaluator change explicitly. It creates a new evaluator version and compares coverage, labels and sensitivity at fixed application source. If both evals and application behavior need changes, establish the revised evaluator first.

## One budget

Fix reuses the accepted total time and evaluation-run limits. Starting allocations are 20% preparation/baseline, 60% optimization and 20% final verification. The baseline and minimum verification must fit before work starts. Unused preparation capacity can flow to optimization; verification keeps its reserve.

Every actual seeded/repeated evaluation, failed trial, retry and final verification counts. Host proposals, judging and review count against applicable time/cost limits. Monetary cost is shown when measured; subscription usage is not assigned an invented price. The budget never expands automatically.

Measurement needs clean committed source, a runnable application and a configured [execution profile](settings.md). Without a runnable baseline, Fix reports the blocker. An unmeasured application patch requires your explicit request and follows a separate review process.

<span id="what-to-expect-during-a-run"></span>
<span id="choose-how-to-explore"></span>

## Optimize with Omni

Omni is the default composition of GEPA, Agentagon's native-host AutoResearch adapter and Agentagon's native-host Meta-Harness adapter. Exploration starts across all three; fresh GEPA refines the strongest candidate. Three quarters of optimization capacity initially goes to exploration, split evenly, and one quarter to refinement. Work respects your host's actual concurrency.

The three engines are also advanced standalone choices. Host/model overrides apply to candidate authoring; judge configuration remains separate. Unavailable host work stays pending, and durable request identities allow resumption without duplicating completed work.

The optimizer proposes candidates. Agentagon enforces the frozen evaluator, permitted edit scope, budgets, gates and independent review. Failed, dominated and incomplete attempts remain evidence. Candidates cannot change their evals to improve scores.

<span id="what-verification-means"></span>
<span id="explore-review-and-select"></span>

## Select a verified improvement

The winner is the highest-scoring independently verified candidate that satisfies every required behavior and limit and establishes improvement over baseline. A higher score cannot offset a failed gate. Search stops when a candidate reaches your target and passes final verification, or when the budget ends.

The report compares the winner and next two qualifying alternatives with the baseline: score components, checks, limits and concrete changes. It shows fewer when fewer qualify. You can choose another verified alternative. If none establishes improvement, the baseline is retained.

Changed datasets do not establish application improvement. A reviewed unmeasured patch never enters the verified candidate frontier or establishes verified issue resolution. See [what verified means](concepts.md#what-verified-means).

## Receive delivery

The default result is a draft PR when the destination and authentication are configured, otherwise a local branch/patch. An open eval PR becomes the parent of the application PR; delivery verifies the exact commit relationship and explains that the eval PR merges first. Private inputs and evidence stay excluded.

Fix opens the [dashboard](dashboard.md), retains detailed local evidence and exports readable and JSON summaries. Merging and deployment remain separate actions. [Intelligence](intelligence.md) remains optional with its own consent requirements.

If interrupted, resume the recorded IDs in the same coding host. Do not create replacement requests just because work is pending. An exhausted budget requires explicit extension.

<span id="configure-execution-once"></span>
<span id="define-the-benchmark-and-hard-constraints"></span>
<span id="steer-a-run-without-losing-queued-work"></span>
<span id="learn-from-completed-experiments"></span>
<span id="dashboard-controls-and-delivery"></span>

## Direct control and references

[Execution and controls](reference/fix.md) · [Evaluation preparation](reference/evaluation.md) · [Baselines](baselines.md) · [Reviewed patch commands](reference/patches.md) · [Delivery](delivery.md)
