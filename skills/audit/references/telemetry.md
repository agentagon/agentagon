# Anonymous usage

Run each skill's telemetry hook once when its workflow starts. Referencing another skill's documentation is not an invocation. Do not repeat the hook on routine continuation or retry it solely to obtain telemetry delivery. If the CLI is not installed yet, skip the hook and continue setup.

Telemetry is enabled by default and sends anonymous event counts directly to PostHog Cloud US. Users can disable it with `agentagon setup --scope user --set telemetry.enabled false` or `AGENTAGON_TELEMETRY_DISABLED=1`. Honor an instruction not to send telemetry before invoking a hook; do not ask the user to configure analytics. Existing `setup` and `status` commands report enablement and pending-event count without sending.

Lookup code records outcomes and returned knowledge entries automatically for audit, evaluation and fix owners. Do not send `knowledge_returned` yourself or treat cached guidance as newly returned. Record later stages only after doing the corresponding work; those stages are audit-only:

```sh
agentagon --workspace CODEBASE telemetry knowledge_investigated --data '{"audit_id":"AUDIT_ID","receipt":"RECEIPT_PATH","entry_id":"ENTRY_ID"}'
agentagon --workspace CODEBASE telemetry knowledge_cited --data '{"audit_id":"AUDIT_ID","receipt":"RECEIPT_PATH","entry_id":"ENTRY_ID","finding_id":"FINDING_ID"}'
```

Replace placeholders with exact existing values. `receipt` is the checkout-relative receipt path returned by lookup, not a context file. Investigated means you examined local evidence against the suggestion, including when it yielded no finding. Cited means you explicitly associated it with an existing saved finding: cite the knowledge ID in the diagnosis, submit the finding, then use its assigned finding ID in this command. Reading a suggestion is neither stage. A citation is not proof of benefit or causation.

The CLI validates receipt membership and finding ownership, retains local stage annotations and deduplicates each audit/receipt/entry/stage. Only allowed public event properties are sent; audit IDs, receipt paths, finding IDs and evidence remain local. Never add context, focus, code, traces, project names, personal information or explanatory text to `--data`.

The same hook drains a small batch of eligible queued events. It may report `queued`, `unconfigured`, `disabled`, `invalid_event`, `unavailable`, `retry`, `quota_limited`, `invalid_response`, `rejected`, `accepted` or `idle`. These are telemetry results, not audit results; none blocks the workflow. Do not claim `accepted` proves the event appeared in PostHog.
