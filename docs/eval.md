# Prepare an evaluation

Use **ag:eval** to turn a goal, audit finding, or review recommendation into a reusable benchmark. The coding agent checks existing tests, defines expected behavior, validates that the benchmark catches mistakes, and obtains an independent review before freezing it.

You receive a frozen evaluation package, a benchmark-only review branch, and a specification ready for a measured fix.

## Before you begin

You need:

- A named [execution profile](settings.md#configure-execution-before-evaluations-or-fixes).
- Expected behavior you can trust: assertions, labeled cases, accepted outputs, or another declared correctness rule.
- Explicit preparation limits for trials, elapsed time, and each trial’s timeout.

Keep private inputs and workflow files in ignored `.agentagon/` storage. Do not commit them just to make the checkout clean. If the application has unrelated changes, have their owner handle them before evaluation preparation.

## 1. Describe what should be tested

For a saved issue, open it in the dashboard and choose **Create regression evaluation**. Copy the request into your coding agent. It carries the audit and issue IDs into preparation, which retains the selected findings, observed behavior, proposed expectations and source digests. Opening or copying the request does not start execution.

Review the proposed expectations before using them as labels. Include ordinary successful behavior and boundary cases; establish permitted inputs, tool responses and resettable state before treating a trace as replayable. Source alignment and missing evidence remain limits of the case.

Open the application checkout in your coding host. For a tool-routing application:

=== "Codex"

    Select **ag:eval**, then send:

    ```text
    Prepare an evaluation for the audit's tool-routing finding.
    Expected behavior: known tool names select the matching tool; unknown or
    empty names return an explicit validation error and invoke no tool.
    Use existing tests and synthetic cases. Use the saved local profile.
    Preparation limits: 12 trials, 1800 seconds total, 60 seconds per trial.
    Do not change the application's source. Include useful regression tests
    in the intended delivery paths. Obtain an independent review before freezing.
    ```

=== "Claude Code"

    ```text
    /ag:eval Prepare an evaluation for the audit's tool-routing finding.
    Expected behavior: known tool names select the matching tool; unknown or
    empty names return an explicit validation error and invoke no tool.
    Use existing tests and synthetic cases. Use the saved local profile.
    Preparation limits: 12 trials, 1800 seconds total, 60 seconds per trial.
    Do not change the application's source. Include useful regression tests
    in the intended delivery paths. Obtain an independent review before freezing.
    ```

Adapt the behavior and limits to your application. A goal such as “improve answer quality” is not ground truth by itself. If expected behavior is missing, the host should ask for it rather than invent successful labels.

## Optional Intelligence guidance

When [Intelligence](intelligence.md) is configured, your coding agent handles the lookup and prepares the privacy-safe context. Its suggestions help identify benchmark risks and coverage gaps. You do not need to run a separate command; evaluation preparation also works without it.

## 2. Inspect the evaluation plan

The host works in an isolated preparation checkout. Confirm the plan covers:

| Item | Question to answer |
|---|---|
| Cases and expected results | What observable behavior counts as correct? |
| Baseline | Does the current application actually run under this evaluation? |
| Negative controls | Does deliberately wrong behavior fail the declared expectations? |
| Metrics and directions | What is measured, in which units, and is higher or lower better? |
| Metric discrimination | Do known correct variants receive the expected ranking and separation? |
| Coverage | Which requirements remain untested? |
| Delivery paths | Which regression tests should accompany the final fix? |

A negative control is a deliberately incorrect case or behavior used to test the benchmark’s sensitivity. A crashed harness or a check that always passes does not establish useful sensitivity.

When you have correct implementations with a known quality, cost or latency difference, add [metric discrimination cases](../skills/eval/references/preparation.md#metric-discrimination). Agentagon checks their ranking before freezing the evaluation. Include their repetitions in the preparation budget; these comparisons supplement correctness checks and do not prove generalization.

Mark cheap, self-contained checks as [preflights](../skills/fix/references/contract.md#preflight-checks) to skip expensive benchmarks for broken fix candidates. Preparation still runs the full sequence so negative controls remain verifiable.

## 3. Validate and review

Agentagon executes the declared commands and checks their expectations. Preparation has its own budget. Failed attempts and retries retain identities and consume the relevant limits; preparation does not silently borrow from a future fix budget.

An independent reviewer inspects the current evaluation and its evidence. Missing expected behavior, failed sensitivity checks, or missing independent review leaves preparation incomplete.

The skill opens the [dashboard](dashboard.md) automatically and selects this evaluation to show progress and evidence. Use **ag:dashboard** to reopen it. If the host pauses, ask it to resume evaluation `EVALUATION_ID`, using the actual returned ID.

## 4. Freeze the package

The frozen package binds source, benchmark files, input data, seeds, metrics, coverage, execution evidence, and independent review. Save the returned evaluation ID and digest.

Application source remains unchanged. The benchmark-only branch excludes private inputs. Instrumentation stays out of fix delivery by default; explicitly declared `deliver_paths` include intended regression tests in the fix baseline.

Freezing is local. It does not publish the branch or open a PR.

## 5. Use it for a measured fix

Choose **ag:fix** with:

```text
Use evaluation EVALUATION_ID and profile local to address this finding.
Measure a fresh baseline and compare independently reviewed candidates.
Keep the frozen evaluator unchanged and show verified alternatives for selection.
```

Replace `EVALUATION_ID`. The fix must start from the same committed application source. It measures a new baseline; the preparation measurements are not reused as optimization results.

[Continue with measured fixes →](fix.md)

## Revise or reuse an evaluation

A frozen package is immutable. [Create a new draft](reference/evaluation.md#revise-or-reuse-an-evaluation) to change its benchmark or validate it against different source, then obtain new execution evidence and review.

## Direct CLI reference

See the [preparation commands, budget file, and contracts](reference/evaluation.md#direct-cli-reference) for direct use. A passing benchmark establishes evidence for its declared cases; it does not prove production improvement.
