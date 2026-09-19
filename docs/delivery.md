# Prepare delivery within Fix

Init, Eval and Fix carry reviewed results through delivery. The default is a draft PR when the destination and authentication are configured, otherwise a local branch/patch with diffstat, safe evidence summary and PR description. Local preparation requires no Git remote.

## Deliver the reviewed result

| Result | Required evidence | Direct command |
|---|---|---|
| Measured application fix | Selected candidate on the current verified frontier | `agentagon _internal fix deliver --run RUN_ID` |
| Evaluation change | Exact frozen package and independently reviewed benchmark branch | `agentagon _internal fix deliver --evaluation EVALUATION_ID` |
| Unmeasured patch | Sealed source, available check evidence and passing independent review | `agentagon _internal fix deliver --patch PATCH_ID` |

For measured results, inspect baseline comparisons, variation, constraints and review. Choose among meaningful tradeoffs, or let the host follow an existing unambiguous selection instruction. The direct selection command remains `agentagon _internal fix select RUN_ID CANDIDATE_ID`.

Evaluation evidence describes the benchmark's coverage and sensitivity. An unmeasured patch is labeled **Reviewed patch — baseline comparison unavailable**. Neither is presented as measured application improvement. No-check plans explicitly report that no executable checks ran.

The existing `agentagon _internal fix ship RUN_ID` command remains available for measured results. You do not need a separate Ship skill.

## Inspect cleanup and exported content

Useful cleanup is a new measured candidate under the same frozen benchmark and remaining budget, followed by fresh independent review. Automatic replacement requires no worse results on every frozen objective; a changed tradeoff needs your choice. Failed cleanup preserves the original selected candidate and its evidence.

Inspect the exact diff and generated PR body. Private inputs, credentials and execution material are excluded from summaries; protected eval artifacts must not be added to delivery just to make it self-contained.

## Publish the draft PR

For a configured GitHub draft-PR destination within the journey's authorized scope, use the same command with `--publish`, for example:

```sh
agentagon _internal fix deliver --run RUN_ID --remote origin --base main --publish
```

Use `--evaluation` or `--patch` for those result types. Publication validates the remote and exact reviewed source before pushing and creating a draft PR. Existing authorization applies; a local package alone does not authorize publication.

Repeat the same command after interruption so saved receipts can reconcile a prior push or PR. Changed source or destination base requires fresh applicable validation and review. Conflicting remote branches are not overwritten.

Merging, deployment and issue resolution follow your normal process and remain separate actions. Verified resolution needs matching applied source and evidence for the specific issue; see [issue states](reports.md).

## Stack an application PR on its eval PR

When the eval-only PR is still open, use the exact reviewed eval branch as the application's parent:

```sh
agentagon _internal fix deliver --run RUN_ID --eval-parent EVALUATION_ID --remote origin --publish
```

The operation verifies the recorded eval-parent/application-child relationship and unchanged eval files, then uses the eval branch as the PR base. A different base flag alone is insufficient. Merge the eval PR before the application PR; merging remains a separate action.

## Export a safe report

```sh
agentagon journey export --baseline BASELINE_ID
agentagon journey export --run RUN_ID
```

The returned readable and JSON reports include agreed goals and scoring, behavior results, evidence summaries and source/evaluator identities. Raw traces, private inputs and credentials remain excluded. Detailed evidence remains available locally.
