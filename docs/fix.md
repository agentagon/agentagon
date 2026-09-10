# Measure candidate fixes

Use **ag:fix** to compare improvements against a frozen benchmark. Your coding agent authors isolated candidates; Agentagon records measurements, checks constraints, and retains independently reviewed alternatives for you to choose from.

<span id="start-with-your-coding-agent"></span>

## Before you begin

You need a named [execution profile](settings.md#configure-execution-before-evaluations-or-fixes), and either a [prepared evaluation](eval.md) or an explicit [benchmark specification](reference/fix.md#define-the-benchmark-and-hard-constraints). An audit is optional if you already know the goal and how to measure it.

Keep private inputs in ignored `.agentagon/` storage. Have the owner of unrelated changes handle them before starting. For a first local run, use one candidate and one trial at a time to make comparisons easier to interpret.

## 1. Ask your coding agent

Open the application in Codex or Claude Code. Replace `EVALUATION_ID` with the ID returned by evaluation preparation.

=== "Codex"

    Select **ag:fix**, then send:

    ```text
    Use evaluation EVALUATION_ID and the saved local profile to improve the
    application. Measure a fresh baseline, propose isolated candidates,
    and run the frozen checks. Obtain independent reviews of the baseline
    and candidates. Stay within the saved limits and show verified options
    for me to choose from before selection.
    ```

=== "Claude Code"

    ```text
    /ag:fix Use evaluation EVALUATION_ID and the saved local profile to
    improve the application. Measure a fresh baseline, propose isolated
    candidates, and run the frozen checks. Obtain independent reviews of
    the baseline and candidates. Stay within the saved limits and show
    verified options for me to choose from before selection.
    ```

The host should explain the editable paths, metrics, hard constraints, and execution limits before measuring. If these are missing, define them first; a broad goal alone cannot establish an improvement.

When [Intelligence](intelligence.md) is configured, your coding agent handles the lookup and prepares privacy-safe context about the issue. Suggestions help guide candidate improvements; the frozen benchmark still determines how they perform. The workflow also works without Intelligence.

<span id="what-to-expect-during-a-run"></span>

## 2. Follow the comparison

1. **Baseline:** Agentagon freezes the experiment, measures the starting source, and requests independent review.
2. **Candidates:** the host edits isolated alternatives and evaluates each against the same frozen benchmark.
3. **Verification:** eligible measured results receive independent review tied to their source and trial evidence.
4. **Results:** reports retain verified tradeoffs alongside failed, dominated, or incomplete attempts.

The original application checkout is not modified. The loop continues within the saved limits; opening a dashboard does not continue the coding host's work.

## 3. Inspect the result

The skill opens the [dashboard](dashboard.md) automatically for your fix run; use **ag:dashboard** to reopen it. Compare:

| Check | What it tells you |
|---|---|
| Baseline and candidate metrics | Whether the declared objectives improved, and by how much. |
| Repetitions and observed range | How much the measurements varied. |
| Checks and hard constraints | Whether a candidate preserved the required behavior. |
| Independent review and diff | Whether the reviewed change matches the measured source. |
| Failed or incomplete attempts | What was tried and why it is not a verified option. |

<span id="what-verification-means"></span>

Verification applies to the declared cases, constraints, and source snapshot. Read [what “verified” means](concepts.md#what-verified-means) before interpreting it as evidence for broader production behavior.

## 4. Choose the next step

If you are satisfied, compare the tradeoffs and [select and deliver a candidate](delivery.md). A faster option and a higher-quality option can both be useful; the search policy does not choose the final winner for you.

If work paused, ask the same coding host to resume `RUN_ID`. A stopped run or exhausted budget needs [explicit continuation](reference/fix.md#explore-review-and-select). Keep completed evidence intact when resuming.

Selection, publication, merge, deployment, and issue resolution remain separate actions.

<span id="configure-execution-once"></span>
<span id="define-the-benchmark-and-hard-constraints"></span>
<span id="choose-how-to-explore"></span>
<span id="explore-review-and-select"></span>
<span id="steer-a-run-without-losing-queued-work"></span>
<span id="learn-from-completed-experiments"></span>
<span id="dashboard-controls-and-delivery"></span>

## Configuration and direct control

| You need to | Reference |
|---|---|
| Configure a runner and execution limits | [Execution profiles](reference/fix.md#configure-execution-once) |
| Define measurements, inputs, and constraints | [Benchmark specification](reference/fix.md#define-the-benchmark-and-hard-constraints) |
| Choose candidate parents | [Search policies](reference/fix.md#choose-how-to-explore) |
| Run or resume from the CLI | [Execution and review commands](reference/fix.md#explore-review-and-select) |
| Steer, stop, or continue an active run | [Run controls](reference/fix.md#steer-a-run-without-losing-queued-work) |
| Retain lessons from completed experiments | [Scans and lessons](reference/fix.md#learn-from-completed-experiments) |
| Enable dashboard controls or inspect cleanup | [Controls and delivery](reference/fix.md#dashboard-controls-and-delivery) |

## Related guides

[Evaluation preparation](eval.md) · [Task evidence](task-evidence.md) · [Host orchestration](orchestration.md) · [Troubleshooting](troubleshooting.md)
