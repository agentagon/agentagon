# Read reports and issues

Start with the report’s scope and completion state, then examine its findings. A finding is a claim supported by specific evidence.

## Find your report

Choose **ag:dashboard** in Codex or run `/ag:dashboard` in Claude Code to open the [dashboard](dashboard.md). Find your audit, review, or fix run and inspect its status, findings, evidence, and results.

For a specific result, ask your coding agent to open it in the dashboard. If you need Markdown or JSON reports, ask the agent for the saved report files. Direct commands are listed in the [CLI reference](reference/cli.md).

## Check status and coverage first

| What you see | What it means | Your next step |
|---|---|---|
| Pending evidence, diagnosis, or clustering | The coding agent has not finished that stage. | Resume the audit with unchanged inputs. |
| A complete audit with coverage limits | The selected review finished, but exclusions or evidence limitations remain. | Read the limits before acting on the findings. |
| `complete_with_limits` for trace alignment | The selected traces cannot be confidently matched to the current source. | Preserve the limitation or audit better-aligned evidence. |
| No grouped issues in an unfinished report | No completed conclusion is available yet. | Do not interpret the empty list as a pass. |
| A completed changes review | The captured diff was reviewed. | Do not generalize it to the entire application. |

Coverage identifies inspected and skipped evidence. A smaller sample cannot establish that an issue disappeared from production.

## Evaluate a finding

Look for the behavior, severity, evidence citation, confidence, and uncertainty. Findings can be based on:

- **Implementation evidence:** the current code permits or causes the behavior.
- **Runtime evidence:** it occurred in selected recorded executions.
- **Correlated evidence:** code and traces support the same conclusion, with a stated relationship.

Improvements should describe the expected benefit and what to measure. Evaluation recommendations should identify a scenario and expected assertion. If either is vague, ask the host to make the recommendation specific before preparing a benchmark.

For example, “test tool errors” is incomplete. “When the search tool times out, return an explicit unavailable result and do not fabricate a source” gives an evaluation author something observable to check.

## Follow persistent issues

Related findings can be retained as issues across audits. Use the dashboard to inspect their history and current state.

| Issue state | Meaning |
|---|---|
| `open` | Supported issue awaiting work. |
| `in_progress` | Work is underway. |
| `resolved_user` | A user reports resolution; it is not independently verified by the engine. |
| `resolved_verified` | Candidate evidence and the matching applied checkout support resolution. |
| `dismissed` | Reviewed and rejected with a reason. |
| `reopened` | The issue recurred or was explicitly reopened. |

Ask your coding agent to update an issue with the reason and appropriate evidence. Selecting a branch or creating a PR does not resolve it. Verified resolution must refer to a verified run and candidate that support this issue, and the current checkout must match the tested application state.

The complete [issue-history reference](../skills/audit/references/history.md) documents CLI status updates. Use supported commands; do not rewrite saved history files.

## Read a fix comparison

Inspect the baseline, candidate results, hard constraints, repetitions, and observed variation. A good result has actual engine-bound measurements and an accepted independent review of the same source snapshot.

Failed, dominated, and interrupted candidates stay in history. A dominated candidate is one for which another verified option is no worse on every objective and better on at least one. Retaining it explains what was tried and why it was not preferred.

If two candidates trade quality for latency, there may be no single winner. [Choose and deliver](delivery.md) the option that fits your requirements.
