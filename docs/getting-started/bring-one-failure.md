# Bring one failure

Start with one recurring agent failure. Work in your existing application and aim to leave with a measured repair and a regression test you keep.

Choose **ag:audit** if you need to investigate the problem, or **ag:fix** if you already know the failure and expected behavior. Fix reuses a suitable evaluation or moves into preparation. Review, Eval and Ship remain available for their specific steps.

## Describe the case

Open your application in Codex or Claude Code and provide:

```text
Help improve this agent's handling of this recurring failure: [describe it].
Expected behavior: [what must actually happen and what must never happen].
Use the existing application and permitted evidence. Inspect prerequisites and
existing tests, propose a small execution profile and preparation/fix limits,
and ask for any missing expected behavior before running evaluations.
Preserve ordinary successful cases. Compare a repair against the same evaluation,
obtain independent review, and include the intended regression test in delivery.
```

Replace the bracketed text. For a ticket timeout, specify that the request must create exactly one ticket and return its confirmation; distinct requests must still create distinct tickets. A message saying “done” is insufficient evidence of the actual outcome.

If you have a saved audit, open its issue in the dashboard and choose **Create regression evaluation**. Copy the request into your coding agent. It retains the selected audit/issue identities and carries the findings and source digests into the draft. Proposed expectations remain pending review; an observed answer is not ground truth.

## Establish a runnable, bounded check

The host checks the application entry point, dependencies, existing tests, model/tool access and source state. A code audit can begin without a runnable benchmark. Evaluation and fix workflows need a clean committed application, a named profile and explicit execution limits.

Use permitted inputs, include ordinary successes and edge cases, and reset relevant tool state between attempts. Start with controlled tools where live operations would change real data. Missing state or redacted inputs may prevent faithful replay; keep that limitation visible.

Follow [Prepare an evaluation](../eval.md) to test that the evaluator catches deliberately incorrect behavior. Use its dashboard and independent review before freezing the comparison. The [ticket-retry example](../../examples/ticket-retry/README.md) provides a seeded, actual-model case and a retained deterministic regression test.

## Inspect and retain the result

Compare the baseline, proposed change, ordinary successes, failures, observed variation and review. A failing candidate or inconclusive outcome is useful evidence but is not a verified repair. Choose among [verified alternatives](../delivery.md), then prepare the selected change and intended tests through Ship.

Run the retained tests through your normal CI on later changes. For a pilot, record setup time, review effort, execution cost coverage, whether the owner accepted the outcome, and whether a second case or later regression made the test useful.
