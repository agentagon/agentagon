# Managed authors and independent reviewers

The shared runtime owns this task and its Codex or Claude sessions. The author uses the private CLI prefix supplied in the task prompt. The runtime dispatches independent review and GEPA reflection sessions; the author must not launch reviewers, fabricate their identities, or submit self-authored reviews.

## Author

Read the task context, frozen goal, evaluator, memory snapshot, permitted paths and limits. Reuse the saved run and candidate identities after interruption. Edit only the assigned worktree. Candidate execution establishes measurements; reasoning and reports cannot replace those measurements. Retain failed attempts and uncertain findings.

## Independent review

When an evaluator or candidate awaits review, return the exact managed `needs_review` handoff described in the task prompt. The runtime supplies the sealed diff, frozen requirements, trial evidence and exact review template to a separate session. Only its validated response can establish independent review. Wait for that response before expanding or selecting a candidate.

## Reflection

Return `needs_reflection` for an existing `gepa-reflection-v1` request. The runtime forwards the original prompt to a dedicated session and gives its raw response back to the optimizer. Never reconstruct or replace that prompt, duplicate an outstanding request, or create another optimizer supervisor.

## Durable continuation

The runtime preserves task, session, engine and request identities. User questions are shared by dashboard and MCP. Cancellation stops bounded execution; client disconnection does not cancel a task. Service interruption marks tasks interrupted and requires explicit resume. Resume existing records and inspect retained progress before executing another step. There are no host lifecycle hooks or installed skill bindings.
