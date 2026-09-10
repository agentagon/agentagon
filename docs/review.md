# Review your changes

Use **ag:review** when you want feedback on local changes before committing or sharing them. It reports defects, improvement opportunities, and concrete evaluation cases tied to the captured diff.

**You need:** the [installed plugin](getting-started/install.md), a Git application checkout, and local changes. A first commit is not required; a repository without commits uses an empty baseline.

## Start a review

Open your application in the coding host. Review includes the net difference from `HEAD`: staged, unstaged, and non-ignored new files.

The request below is an example. Change the focus, paths, and expected behavior to match what you want reviewed - for example, retrieval quality, tool selection, or handling failed tool calls.

=== "Codex"

    Select **ag:review**, then adapt and send this example:

    ```text
    Review my local changes. Focus on tool error handling and behavior
    that needs regression coverage. Explain each finding with evidence
    and suggest concrete evaluation cases. Do not implement fixes.
    ```

=== "Claude Code"

    ```text
    /ag:review Review my local changes. Focus on tool error handling and
    behavior that needs regression coverage. Explain each finding with
    evidence and suggest concrete evaluation cases. Do not implement fixes.
    ```

Related code may be inspected to understand a change. Findings must still connect to captured changes; unrelated existing defects belong in a [full audit](audit.md).

## Narrow the scope

Name the paths you want reviewed in your request. For CLI inspection of the selected diff:

```sh
agentagon audit changes --scope src --scope tests
```

Run from the application checkout. Repeat `--scope` for additional paths, including deleted paths. `audit changes` inspects the diff without starting a review or initializing state.

This release does not provide a branch-to-branch or staged-only review. Staging a file does not exclude its unstaged changes from the net diff.

## Understand the result

Choose **ag:dashboard** in Codex or run `/ag:dashboard` in Claude Code to open the [dashboard](dashboard.md). Inspect the review’s status, findings, evidence, and issue history there.

A useful evaluation recommendation identifies the scenario, input, expected behavior or assertion, and why the change needs coverage. A review can recommend better evaluation coverage even when no defect is found.

See [Read reports and issues](reports.md) for help interpreting status and evidence.

## Empty, excluded, or changing diffs

- **No local changes:** the review stops. Use `ag:audit` to inspect current application code instead.
- **Only excluded files:** the result retains that coverage limit; it cannot establish a clean review.
- **Merge conflicts:** resolve them before capture.
- **Changes after capture:** start a new review. The earlier report remains a record of the earlier diff.

## Add traces only when relevant

Review defaults to code-only even if this checkout has saved trace settings. To use combined code and traces, explicitly provide the trace scope and explain how those executions relate to the local changes. Every finding must still cite change evidence. Trace-only mode is not a changes review.

**Next:** use [ag:eval](eval.md) to turn a recommendation into a benchmark, or keep the report as review feedback. Review itself does not implement or execute new evaluations.
