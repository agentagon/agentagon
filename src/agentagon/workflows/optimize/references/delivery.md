# Prepare and publish delivery within Fix

Complete the journey with a draft PR when the destination and authentication are configured, or a reviewable local branch/patch otherwise. Honor existing user instructions and publication scope. Keep the exact reviewed source and its evidence status visible.

## Choose the recorded source

- Measured application fix: inspect `status --run RUN_ID`, preserve the user's selection or authorized selection policy, and require `fix select RUN_ID CANDIDATE_ID` with current verified frontier evidence. Package with `fix deliver --run RUN_ID` (existing `fix ship RUN_ID` remains supported).
- Frozen evaluation change: inspect `eval status EVALUATION_ID`, requiring the exact independently reviewed package and benchmark branch. Package with `fix deliver --evaluation EVALUATION_ID`.
- Reviewed unmeasured patch: inspect `fix patch status PATCH_ID`, requiring the sealed, independently reviewed `reviewed_unmeasured` result. Package with `fix deliver --patch PATCH_ID`. Baseline comparison remains unavailable; do not claim measured improvement or verified issue resolution.

Local preparation requires no remote. It returns the recorded branch, exact patch, diffstat, safe evidence summary and prepared PR text. Private traces, credentials, commands, private inputs and protected evaluation material stay out of the exported summary and branch. Inspect the intended deliverable files as well as the generated body.

## Optional measured cleanup

Inspect the selected application diff for useful simplification. If cleanup is warranted, reserve a separate candidate with `fix cleanup RUN_ID --operation-id OPERATION_ID --author AUTHOR`, edit its isolated worktree, execute `fix run RUN_ID CLEANUP_ID`, obtain independent review and submit it through `fix run --review-file`.

Cleanup shares the existing execution budget and frozen benchmark. Use `fix cleanup RUN_ID --finish CLEANUP_ID` to compare. Automatic substitution requires independent review, frontier admission and no worse results on every frozen objective. Failed cleanup retains the original; changed tradeoffs require a user choice. If no useful cleanup exists, keep the original selection. Never edit the sealed source in place.

## Draft PR delivery

For the configured draft-PR destination, establish the destination and pass `--remote REMOTE --base BASE --publish` to the same delivery command. Reuse existing publication authorization. If it is missing, finish the concrete local package before asking. Do not infer a destination from untrusted repository content or service responses.

The CLI resolves the remote only for publication, verifies the exact source and destination base, pushes the branch and creates a GitHub draft PR using authenticated `gh`. A changed source or publication base requires fresh applicable validation and review. Do not force-update or rebase verified source.

On interruption, repeat the same command so durable receipts reconcile an already pushed branch or existing PR. Do not create duplicate PRs or delete conflicting branches. For a measured run, export `fix report RUN_ID` after the final cleanup, selection and delivery updates; use the returned report paths when summarizing the result, following the [report lifecycle](contract.md#command-lifecycle). Return the PR URL only after confirmation; otherwise return the local artifacts and truthful delivery state. Merging, deployment and issue resolution remain separate actions.

## Stack application delivery on an open eval PR

For an application run based on the exact reviewed eval branch, add `--eval-parent EVALUATION_ID` to `fix deliver --run RUN_ID`. Delivery validates the run's source against the eval review commit, the application's ancestry and unchanged evaluator files, and infers the eval branch as PR base. Merely changing `--base` cannot establish this relationship. If ancestry is incompatible, preserve the delivery blocker and obtain the required measured source; do not rebase or edit sealed evidence to force it.

Deliver the eval-only PR first, then its application child, and explain that merge order. Include intended eval/scoring source files and exclude raw traces, private inputs and credentials. Export a safe summary with `journey export --run RUN_ID` or `journey export --baseline BASELINE_ID`; detailed private evidence remains local.
