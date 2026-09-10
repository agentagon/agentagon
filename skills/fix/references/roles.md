# Host roles and durable rounds

The active `ag:fix` task is the coordinator. Use native host agents for independent author branches, reviews, scan shards and hypothesis generation. The CLI reserves work and records evidence; it never starts a separate model service. Check `fix next RUN_ID` before dispatch. Honor queued directives and explicit stop controls first.

## Candidate author

Give the author its reserved candidate ID, local worktree, verified parent, hypothesis, permitted edit paths, evidence references, branch ID and depth allowance. Read the actual parent before editing. Do not edit another branch, canonical state, frozen checks or protected inputs. The author should explain the change and return its existing candidate ID; only `fix run` establishes measurements. Call `fix assign RUN_ID CANDIDATE_ID --host-id HOST_ID --agent-id AGENT_ID` after native dispatch. Save and resume that agent identity; do not spawn another author for an assigned reservation. After a host restart, locate or resume the recorded native task; if it cannot resume, continue the same worktree and reservation in the active coordinator and explain the interruption.

## Independent reviewer

Use a different agent from the author. Supply `fix inspect RUN_ID CANDIDATE_ID`, the frozen requirements and exact review template. Inspect the sealed diff, task failures, trial logs, constraints and source/evaluation identities. Assess correctness and evaluator integrity. Retain the exact review bindings and cite retained evidence. Review prose cannot supply measurements. After a passing review, submit `fix run --review-file`; only then may the branch expand.

## Round coordinator

Save a JSON request and submit `fix round RUN_ID --briefs FILE`. A new round includes `operation_id`, `host_id`, actual available `host_capacity`, and `branches`. Each brief contains `hypothesis`, `author`, `editable_paths`, `evidence`, optional eligible `parent_id`, and optional `depth_limit`. Omit the parent to use the saved search strategy. Multiple branches may share one eligible parent. Width is bounded by saved round width, candidate concurrency, host capacity, resource slots and remaining candidate/trial budgets; it is unrelated to parent-pool size.

The response reserves every candidate identity before any dispatch. Reuse the **same request and operation ID** after interruptions. Changed arguments need a new operation ID. Keep native dispatch concurrency below both the saved limit and the host's currently available slots. A local or SSH profile without independent measurement capacity still executes one trial at a time.

For a descendant, submit a new operation with the existing `round_id`; each brief must name its `branch_id` and the most recent verified `parent_id`. The depth allowance cannot increase. A branch can finish early using `finish_branches: [{"branch_id": "...", "reason": "..."}]` instead of more briefs. Collect or cancel active executions first. A round closes when every branch finishes, fails, is invalidated or is cancelled, regardless of the number of eligible parents.

## Scan worker and hypothesis author

When `fix next` requests a scan, prepare it through the existing `scan` control. Dispatch its `shards` up to the available native host capacity. Their combined input bytes stay within one shared scan input limit and all shards consume one scan slot. Workers interpret only their provided evidence and return advisory, evidence-linked findings. The coordinator merges at most 20 insights using the parent scan's response template and existing `insights` control. Retain contradictory findings with their evidence and uncertainty; do not invent agreement. Failed workers remain visible through `scan_fail` or a merged result whose uncertainty describes missing coverage. Retain partial responses in ignored state for review.

Before stagnation stops a run, `fix next` may return an `ideate` packet. A separate hypothesis agent proposes untried alternatives grounded in its failures and retains unsuccessful approaches. Submit `fix ideate --response-file FILE` with `packet_digest`, `author`, `hypotheses` and `unsuccessful`. Each hypothesis has `hypothesis` and a nonempty `evidence` array from that packet. The saved ideation-pass bound allows at most one extra round per accepted pass and never increases candidate, trial or elapsed-time budgets. An empty alternative list ends exploration.

## Session continuation

Bind only the current native root task with `fix bind RUN_ID --host codex|claude-code --session-id SESSION_ID`. Never guess another task's session ID. Installation bundles `SessionStart`, `Stop`, `SessionEnd` and `UserPromptSubmit` command hooks. They use the native host's permission and trust flow; do not write trust hashes or bypass review. Until a `Stop` callback is observed, the CLI reports `manual_resume`; a restoration callback alone does not establish continuation support. The binding reports which native events have been observed.

Stop hooks request one more pass only when actionable work and the progress digest changed. Identical polling, elapsed time and hook writes do not count as progress. Stop, selection, exhausted work, unchanged progress and `fix bind ... --pause` suppress continuation. Pause before asking for user input. A new user prompt pauses the old binding; after interpreting that prompt, rebind only if this task should continue. Session restoration supplies context without dispatching work. Session end disables continuation. Unsupported or untrusted hooks require manual `ag:fix` resume. The handler accepts Codex's optional `Interrupt` event, but the shared bundle omits it for hosts that do not support that lifecycle capability.
