# How Agentagon works

Agentagon separates a suggestion from the evidence needed to trust it. Your coding agent reasons about the application; the CLI records what was inspected, executes declared checks, and keeps the results tied to the source that produced them.

## The coding host and the CLI

The **coding host** is Codex or Claude Code. Agentagon’s seven skills guide that host through audits, reviews, evaluation preparation, measured fixes, and delivery. See [where work runs](execution.md) for the distinction between model reasoning and benchmark execution.

The **CLI** is the `agentagon` command. It stores local evidence, validates submissions, manages isolated candidate checkouts, runs evaluations, and produces reports. Running CLI commands by themselves does not supply an independent reviewer or model judgment.

The **dashboard** is a browser view of one application checkout’s saved work. It has no separate reasoning model and does not supervise work when the coding host is unavailable.

## Choose the right starting point

| Your situation | Start with | Result |
|---|---|---|
| You want to understand the current application | `ag:audit` | Findings from selected code, traces, or both. |
| You changed a prompt, tool handler, or application code | `ag:review` | Findings scoped to the captured local changes. |
| You know the desired behavior but need a benchmark | `ag:eval` | A checked and independently reviewed evaluation package. |
| You have a benchmark or explicit measurement specification | `ag:fix` | Measured, reviewed candidates and retained alternatives. |
| You have chosen a verified candidate | `ag:ship` | A delivery package and, when authorized, a draft PR. |

## Evidence, findings, and issues

**Evidence** is captured source or runtime data. A **finding** is an interpretation tied to that evidence. Related findings can become a persistent **issue**, with occurrences and status decisions across audits.

Code can show that a risky fallback exists. Traces can show that it occurred in selected runs. Neither establishes its production frequency without representative data. Reports retain the difference between implementation-only, runtime-only, and correlated evidence.

## Baseline, candidate, and frozen evaluation

A **baseline** is a measurement of the starting application. A **candidate** is an isolated proposed change, with its own source snapshot and trial evidence.

A **frozen evaluation** fixes the checks, input data, objective definitions, and other comparison inputs before measuring fixes. A candidate cannot improve its score by editing the protected benchmark. Changing the evaluator requires a new comparison.

The fix workflow measures a fresh baseline even when you prepared the benchmark earlier. Preparation results are not reused as fix-run measurements.

## What “verified” means

A verified candidate has:

1. Completed the required executions with valid measurements.
2. Passed the frozen checks and hard constraints.
3. Received an accepted independent review bound to its source and execution evidence.

Independent review is performed by a reviewer other than the author. A different name in a review file is not a substitute for an actual independent review. Missing host capabilities leave the workflow pending.

Verification applies to the declared cases and constraints. It does not establish universal correctness or predict every production outcome.

## Why there may be several good candidates

One candidate may be faster while another has better answer quality. Agentagon retains verified options where neither is better on every objective. This set is called the **Pareto frontier**.

Search policy chooses which parent to explore next. It does not choose the final winner or replace verification. You inspect the tradeoffs and select a candidate.

## Selection and shipping are separate

Selection creates a reviewable branch from a verified candidate. Delivery prepares the patch and summary. Publication pushes the branch and creates a draft PR when you ask for it.

None of those steps automatically merges, deploys, or resolves an audit issue. Verified issue resolution also needs evidence that the applied application matches the tested candidate.

## Where work is saved

Evidence and workflow state live in `.agentagon/` within the application directory. Preferences live in a separate user configuration file, with project overrides. Candidate edits happen in isolated worktrees.

Local worktrees protect the origin’s files; they are not a machine or network sandbox. Your coding host and execution profile determine where processing occurs. See [Privacy and data](privacy.md).

**Next:** [Your first audit](getting-started/first-audit.md) walks through the smallest complete agent-led workflow.
