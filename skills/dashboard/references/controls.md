# Interactive fix controls

Read this procedure only when the user asks to control a fix run. Start or reuse the dashboard through [the lifecycle](lifecycle.md), adding `--controls` when launching a server that needs controls. If the existing server is read-only, restart it in controls mode rather than implying controls are available. Report that controls are enabled.

The page can change future search policy, queue a proposal from an eligible parent, send a directive, stop or request continuation, select a verified frontier candidate, invalidate or exhaust candidate branches, and cancel queued work. Session-only credentials and exact-origin checks protect mutations. On a stale-revision conflict, reload current run state. If an outcome is uncertain, retain the same operation identity when reconciling it.

Queued `directive`, `expand` and `continue` actions wait for [ag:fix](../../fix/SKILL.md) to consume them. Their `queued`, `acknowledged`, `applied`, `failed` and `cancelled` states describe the control rather than candidate verification or deployment. The dashboard never invokes a model service, submits scan judgments or acknowledges host work.

Audit and changes-only views remain read-only even when fix controls are enabled. A selection does not publish, merge or deploy. Follow [delivery within Fix](../../fix/references/delivery.md) for a separately authorized draft PR.
