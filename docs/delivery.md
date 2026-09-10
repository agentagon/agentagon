# Select and deliver a fix

Once a fix run has verified candidates, inspect the tradeoffs and choose the one you want to deliver. Agentagon prepares that exact candidate as a reviewable branch and delivery package.

**You need:** a completed [measured comparison](fix.md), a selectable verified candidate, and a clear choice.

## 1. Compare the verified alternatives

The fix and ship skills automatically open the [dashboard](dashboard.md) for your run; use **ag:dashboard** to reopen it. Read the diff, actual measurements, variation, checks, constraints, and independent review. Failed or incomplete candidates are evidence of attempts, not selectable verified results.

If one option is faster and another is more accurate, decide which fits your requirements. Search preference does not make that final decision for you.

## 2. Select a candidate

Tell the coding agent which candidate you choose, or run:

```sh
agentagon fix select RUN_ID CANDIDATE_ID
```

Selection creates a reviewable branch from the verified source snapshot. It preserves the origin checkout and retained alternatives. It does not apply the change to your current branch, publish, merge, deploy, or resolve an issue.

## 3. Prepare delivery

Choose **ag:ship** in Codex, or use `/ag:ship` in Claude Code:

```text
Prepare local delivery for selected run RUN_ID. Inspect the diff for
useful cleanup, reverify any cleanup as a separate candidate, and produce
the patch, measurement summary, and draft PR body. Stop before publication.
```

The resulting package includes the selected patch, diffstat, measurement summary, and draft PR body. Inspect the files returned by the workflow.

Cleanup is a new candidate under the same frozen evaluation and remaining budget. It needs new execution and independent review. Automatic replacement requires a verified result no worse on every frozen objective. A failed or regressed cleanup preserves the original selection; a changed tradeoff needs your decision.

If there is no useful cleanup, the selected candidate can be delivered as it is.

For direct local preparation:

```sh
agentagon fix ship RUN_ID
```

This command does not publish. Repeating it prepares or reconciles local delivery rather than creating a PR.

## 4. Publish a draft PR, when ready

Ask your coding agent to publish the selected fix when you are ready.

When you authorize publication, the workflow can use:

```sh
agentagon fix ship RUN_ID --publish
```

It pushes the selected branch and creates a **draft pull request**. The summary omits private execution material. Review the diff and measurements in the returned PR before requesting review.

If delivery was interrupted, retry the same run so recorded receipts can reconcile prior success. If source or base changed, obtain fresh matching verification instead of bypassing the check. An unrelated existing remote branch is not overwritten to force publication.

## 5. Apply and verify through your normal process

Agentagon does not merge or deploy the PR. Use your team’s review and deployment process.

Selection and publication do not automatically close the original audit issue. After applying a fix, verified resolution also requires matching application source and evidence supporting that specific issue. See [issue states](reports.md#follow-persistent-issues).

**Related:** [Detailed cleanup and delivery behavior](reference/fix.md#dashboard-controls-and-delivery) · [Troubleshooting](troubleshooting.md)
