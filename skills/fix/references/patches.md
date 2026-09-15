# Reviewed patches without baseline measurement

Use this path only when trustworthy baseline measurement cannot be established within authorized access and limits. Preserve why comparison is unavailable. Never downgrade a measured failure, rejected review, violated constraint or stale evidence into a deliverable unmeasured result.

1. Require clean committed Git inputs and inspect source and available tests. Preserve selected findings and permitted trace evidence in the goal and review context. Define the intended edit scope and success criteria before editing.
2. Write a plan in ignored `.agentagon/` storage with `editable_paths`, `checks` (each has `id`, `argv` and optional `cwd`), `timeout_seconds`, and `max_attempts`. These are explicit authorized limits. An empty check list requires a nonempty `no_checks_reason` and must be explained in the result; do not invent a dummy benchmark. Use `agentagon resources` for the patch plan contract.
3. Run `fix patch start --plan PLAN_JSON --goal GOAL --author AUTHOR --reason-no-comparison REASON`. Edit only the returned worktree and declared paths. Application changes remain isolated from the origin checkout.
4. Run `fix patch check PATCH_ID`. The CLI seals the source, executes the declared checks in isolation, and retains real logs and exit codes. Address known failures within remaining limits. After interruption inspect `fix patch status PATCH_ID`; do not rewrite canonical evidence or spend the same attempt twice.
5. Give an independent reviewer the sealed diff, intended behavior, requirement/trace evidence, actual check results and missing-measurement limitations. Use the returned exact review template, with a reviewer different from the author. Submit with `fix patch review PATCH_ID --review REVIEW_JSON`. A source or evidence change requires new checking and review.
6. Only `reviewed_unmeasured` is ready for [delivery](delivery.md), using `fix deliver --patch PATCH_ID`. Report "Reviewed patch — baseline comparison unavailable". Do not invent metrics, mark a benchmark frozen, admit the patch to the verified frontier or claim verified issue resolution.

This path can also deliver independently reviewed eval changes that cannot yet satisfy benchmark freeze requirements. Describe their remaining validation work explicitly.
