# Local memory groups

Register a named group with an empty folder path, purpose (`improvement` or `agent`), explicit `project_ids`, `write_project_ids`, and optional `agent_ids`. Target-agent groups require explicit agent bindings. Groups can live in different folders; registry metadata lives in SQLite. Folder identity checks prevent silently adopting a different store.

Entries have stable keys, immutable numbered revisions, content checksums, text, evidence references and uncertainty. Repeating the same entry is idempotent; `expected_version` detects concurrent updates. Recall is bounded lexical text retrieval, not a claim that advice is correct. Limits are 32 KB per submitted entry, 2,000 retained revisions per group, 50 entries and 64 KB per recall, and 4 MB per evaluation snapshot.

Workflows recall improvement lessons and record outcomes, including unsuccessful attempts. Target agents explicitly recall/record through MCP. Evaluations pin target-agent memory into frozen private inputs; later writes cannot alter the comparison. A changed memory snapshot requires a new evaluator version. Raw evidence remains authoritative and is never replaced by a lesson.

External stores, automatic instrumentation and procedural extraction are deferred.

## Production feedback

Each task freezes the recalled entry IDs and versions. Structured results identify supplied lessons used or rejected, with reasons; unknown references are rejected. Material production outcomes append evidence-backed lessons, while unchanged observations add no entry. Recording failures remain retryable without invalidating task evidence. Operational status stays in domain records. See the [production guide](production.md).
