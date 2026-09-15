# Initialize an agent

Use **ag:init** to inspect your agent, agree on what better means, prepare reusable evaluations and establish a scored baseline. Init reuses your settings and existing evals; you can complete discovery before execution is ready.

## Start in your application

Choose **ag:init** in Codex, or enter this in Claude Code:

```text
/ag:init Inspect this agent and its existing evals. Propose the behaviors,
scoring and execution budget we should use, then establish a baseline.
Use existing provider settings and show any missing prerequisites.
```

The host inspects code, requirements and available traces. It proposes a small set of relevant behaviors, metrics and limits for you to accept or customize. When suitable evals already exist, it reuses them. When they are absent or unusable, it confirms that finding and the proposed creation and running of missing evals. An explicit request to create evals already authorizes that work.

## Agree on what better means

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

Init produces the accepted definition, a reviewed evaluator, a scored baseline when executable, and the [dashboard](dashboard.md). New eval source is delivered as an eval-only draft PR when publication is configured, or a local branch/patch. Application changes follow through [Fix](fix.md).

A baseline records source commit and branch, evaluator version, execution settings, score components and evidence status. [Reruns](baselines.md) create new measurements while preserving those definitions. Fixed benchmark results and recent-trace scores describe separate populations.

[Improve the baseline with Fix →](fix.md)
