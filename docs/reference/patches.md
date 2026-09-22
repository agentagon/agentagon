# Reviewed patch commands

Fix uses this path when trustworthy baseline comparison cannot run within authorized access and limits. The result retains independent review and actual available checks, with an explicit unmeasured status. It never enters the measured frontier.

Use the [host procedure](../../src/agentagon/workflows/optimize/references/patches.md) and the patch-plan contract returned by the managed task context’s resource paths. A plan names editable paths, check commands, a per-attempt timeout and maximum attempts. An empty check list additionally requires `no_checks_reason`; the result states that no executable checks ran.

```sh
agentagon _internal fix patch start --plan PLAN_JSON --goal GOAL --author AUTHOR --reason-no-comparison REASON
agentagon _internal fix patch check PATCH_ID
agentagon _internal fix patch status PATCH_ID
agentagon _internal fix patch review PATCH_ID --review REVIEW_JSON
agentagon _internal fix deliver --patch PATCH_ID
```

Edit only the returned worktree and authorized paths. Checking seals source and records actual evidence. The independent reviewer must differ from the author and use the exact returned template. Source or evidence changes invalidate the review.

Known failing checks and rejected reviews must be addressed; they cannot be bypassed by calling the change unmeasured. A dataset patch that has not met evaluation freeze requirements remains unmeasured and is not a ready frozen benchmark.
