# One workflow runtime

Dashboard and MCP are thin local interfaces to shared application operations. The runtime owns task state, budgets, questions, cancellation and resumption. The managed brain supplies reasoning, candidate edits and independent reviewer sessions. Validated capabilities own execution and evidence.

| Package | Ownership |
|---|---|
| `dashboard/` | HTTP, display projections, packaged React assets |
| `brain/` | Codex/Claude adapters and sessions |
| `workflows/` | Shared application service, task runtime, built-in definitions, instructions and handlers |
| `memory/` | Folder stores, group access, recall, versions and snapshots |
| `capabilities/` | Discovery, traces, evaluations, bounded engines, Git, delivery and Intelligence |
| `domain/` | Agent, Goal, Issue, Improvement, Deployment, Monitor, Observation and relationships |
| `storage/` | Configuration, SQLite metadata, immutable evidence and filesystem operations |
| `mcp/` | Official SDK stdio tools calling service operations |

React source remains in `frontend/`. Workflow resources ship inside their packages. Public CLI commands launch the application/service/MCP; `_internal` is private to managed sessions. There are no installed public skills, native registration hooks, compatibility wrappers or state migrations.

One SQLite database owns mutable application metadata. Project evidence and evaluation snapshots are immutable filesystem artifacts. Named memory groups own versioned entry files in explicitly registered folders. Reads do not reconstruct issues from audits or reconnect to runners. Audit completion publishes findings into independent issue records.

Assess project and Observe production are ordinary runtime tasks. A service-owned scheduler persists monitor policies, pending windows and stable operation IDs. Completed observation and acquisition checkpoint updates commit together. Interrupted tasks block their monitor until explicit resume or discard.

Production comparisons consume immutable bounded evidence under versioned measurement criteria. Recalled improvement lessons are pinned per task; reported lesson references and recurrence reviews are validated against the supplied versions and trace evidence. Production outcomes feed memory without replacing operational records. See [production monitoring](../production.md).

Service lifetime is independent of interface connections. Operation IDs deduplicate task submissions and controls. Restart reconciles unfinished tasks to interrupted; only explicit resume re-enters the saved session. Pending execution/review identities and budget consumption remain retained.

See [product decisions](product-decisions.md), [workflows](../workflows.md), [MCP](../mcp.md), and [memory](../memory.md).
