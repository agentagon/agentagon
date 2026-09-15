# Fix agents and evals

Use **ag:fix** for a known failure, desired improvement, pasted trace, saved audit finding, or an eval dataset that needs work. Fix establishes checks, makes isolated changes, validates and independently reviews them, and prepares delivery. You do not need to start an audit or invoke a separate evaluation or shipping skill.

<span id="start-with-your-coding-agent"></span>

## Describe the outcome

Open the application in Codex or Claude Code and select **ag:fix** (or start with `/ag:fix` in Claude Code):

```text
Fix this recurring failure: [describe it or include the trace].
Expected behavior: [what must happen and what must never happen].
Reuse suitable evals or prepare missing regression cases. Preserve ordinary
successful cases. Compare against a baseline when available, independently
review the change, and prepare local delivery.
```

For eval work:

```text
Fix the support agent's eval dataset. Correct unsupported labels and add
coverage for duplicate-ticket failures. Validate that the revised checks
catch known failures and prepare the dataset change for delivery.
```

The host inspects code, existing tests and permitted evidence, then asks only for material missing expectations, permissions or limits. Observed trace outputs are not automatically ground truth.

## Establish checks

Isolated fixes require a clean committed Git checkout. Keep private inputs in ignored `.agentagon/` storage and preserve unrelated changes. Git setup and commits require authorization; `agentagon init` only initializes private Agentagon state.

Fix reuses a suitable reviewed benchmark or handles [evaluation preparation](eval.md) within the same journey. Execution uses named [profiles and limits](settings.md); saved authorized choices are reused, and missing preparation/fix limits are collected together while accounting stays separate.

[Intelligence](intelligence.md) is optional. Every outgoing request shows its redacted payload and destination and asks for approval unless you explicitly set Agentagon Intelligence to full access. Declining continues the fix locally.

<span id="what-to-expect-during-a-run"></span>

## Validate the change

| Change | Evidence |
|---|---|
| Application behavior | A fresh baseline and isolated candidates run against the same frozen evaluator, followed by independent review. |
| Dataset or evaluator | A new version with grounded labels, coverage and sensitivity checks at a fixed application revision. |
| Baseline unavailable | A separately recorded, independently reviewed patch with actual available checks and explicit measurement limits. |

If both application and evals need work, establish the revised benchmark first, then compare application changes against it. Scores from different datasets do not prove the application improved.

Failed, dominated and incomplete experiments remain available. Candidates cannot change frozen evaluation files to improve their scores. A failed check or rejected review cannot be bypassed by relabeling a change unmeasured.

<span id="what-verification-means"></span>

A **reviewed unmeasured patch** is deliverable after independent review, but it does not enter the verified candidate frontier or establish verified issue resolution. If no meaningful executable checks are available, its plan must explicitly explain why and the output says no executable checks ran. See [what verified means](concepts.md#what-verified-means).

## Receive delivery

Fix opens the [dashboard](dashboard.md) and finishes with a local branch/patch, safe evidence summary and prepared PR description. A local package needs no remote. Ask for a PR when that is your intended destination.

If several verified options have meaningful tradeoffs, choose using their measurements and your priorities. Existing unambiguous selection instructions are reused. [Delivery](delivery.md) preserves the exact reviewed source; publication, merging and deployment are separate actions.

If work pauses, resume the returned audit, benchmark, evaluation, fix run or patch ID in the same coding host. Saved evidence remains intact; an exhausted execution budget requires an explicit extension.

<span id="configure-execution-once"></span>
<span id="define-the-benchmark-and-hard-constraints"></span>
<span id="choose-how-to-explore"></span>
<span id="explore-review-and-select"></span>
<span id="steer-a-run-without-losing-queued-work"></span>
<span id="learn-from-completed-experiments"></span>
<span id="dashboard-controls-and-delivery"></span>

## Direct control and references

[Execution, search and controls](reference/fix.md) · [Evaluation preparation](reference/evaluation.md) · [Reviewed patch commands](reference/patches.md) · [Task evidence](task-evidence.md) · [Delivery](delivery.md)
