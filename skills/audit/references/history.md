# Persistent issue history

`audit issues list` reads issues across local audits. Prevalence uses each audit's reviewed corpus, never a cross-audit denominator.

Record authorized decisions with `audit issues update ISSUE_ID --status STATUS --reason REASON`. Optional `--evidence` values must refer to saved findings or their source evidence.

| State | Meaning |
|---|---|
| `open` / `in_progress` | Supported issue awaiting or undergoing work |
| `resolved_user` | Customer reports resolution; unverified |
| `resolved_verified` | Engine-produced candidate evidence and matching applied checkout support resolution |
| `dismissed` | Reviewed and rejected, with a reason |
| `reopened` | A previously closed issue recurred or was explicitly reopened |

New `resolved_verified` updates require `--run RUN_ID --candidate CANDIDATE_ID`. The engine must establish that the candidate is verified, its evidence supports this issue, and the current checkout matches the tested application state. A selected branch alone does not resolve the issue. If the applied source differs, obtain a new matching engine verification rather than reusing an unrelated result. See [the fix workflow](../../fix/SKILL.md).

Caller-written `--verification` receipts cannot establish new verified resolution. Older receipt-backed history remains a record of the earlier, weaker validation; do not present it as native engine execution. User-reported resolution still uses `resolved_user` and remains explicitly unverified. Engine execution proves the recorded checks ran, not that arbitrary check definitions adequately establish every product requirement.

Reimporting old data preserves resolution. Later trace occurrences can reopen the issue while retaining resolution history. Code-only recurrence and ambiguous deployment versions require a reviewed status decision. Absence from a smaller or later sample never closes an issue.

Responses and evidence snapshots remain under `.agentagon/`. An interrupted writer leaves completed atomic state readable. Remove a surviving `write.lock` only after verifying its owner has exited. Never delete audit, evidence, or history directories to bypass validation.
