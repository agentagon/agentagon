# Get a first quality baseline {#initialize-an-agent}

Use **ag:init** to find a useful first check of your agent, reuse existing evals and measure how it performs. The host recommends what to check and handles the evaluation details within your agreed limits.

## Start in your application

Choose **ag:init** in Codex, or enter this in Claude Code:

```text
/ag:init Help me understand how well this agent completes its tasks.
Reuse existing evals and recommend a first baseline with a time and run limit.
```

The first response explains what the recommended check will tell you, why it fits your agent, and the total limits or missing prerequisite. You can accept or adjust the recommendation; you do not need to design a scoring system. Detailed discovery and evaluation records stay available in the dashboard.

The host inspects requirements, entry points and existing evals before recommending a measurement. A full audit is not required for this initial recommendation. It reuses suitable evals and confirms creation and execution of missing checks when not already authorized. A local check of tool permissions or output formatting is labeled with that scope; it does not establish answer quality.

## Customize the definition when needed

The host prepares these details from your requirements and existing tests. Review or change them when they affect what you want to measure.

| Definition | Example |
|---|---|
| Behavior | An unknown tool name returns a validation error and invokes no tool. |
| Score | Successful task rate, higher is better, averaged over the frozen cases. |
| Required gate | No duplicate external writes. A high score cannot offset this failure. |
| Limit | A fixed maximum number of evaluation runs and elapsed time. |
| Target | Stop after final verification confirms the accepted success-rate target. |

Use a primary metric, a weighted score, or an existing/custom scoring function. The definition records directions, aggregation, missing-data policy and mappings from behaviors to checks or judge rubrics. Missing measurements remain unknown; they are not assigned invented zero scores. The fixed Audit rubric guides discovery and is separate from your quality score.

## Prepare and validate evals

Cases, assertions, judge prompts, scoring and harness code live in your repository. Intent, accepted proposals and detailed execution evidence live in ignored `.agentagon/` records. Private inputs stay excluded from delivery.

The host validates known correct/incorrect examples and obtains independent review before freezing the evaluator. A judge's configuration is versioned with the evaluator. A separate coding-host grading pass can judge actual saved outputs when an API judge is unavailable; such results are labeled **coding-agent judged** and may be slower and less consistent or accurate. The application itself must still execute.

Discovery accepts dirty or non-Git directories. Measurement needs clean committed inputs, trustworthy expectations, a configured [execution profile](settings.md#configure-execution-before-evaluations-or-fixes) and accepted limits. Missing prerequisites remain visible without losing the assessment.

## Receive the result

The result leads with what was measured, the score and relevant failures, or the blocker and next step when measurement cannot run. Inspect the accepted definition and supporting evidence in the [results view](dashboard.md). New eval source is delivered as an eval-only draft PR when publication is configured, or a local branch/patch. Application changes follow through [Fix](fix.md).

A baseline records source commit and branch, evaluator version, execution settings, score components and evidence status. [Reruns](baselines.md) create new measurements while preserving those definitions. Fixed benchmark results and recent-trace scores describe separate populations.

[Improve the baseline with Fix →](fix.md)
