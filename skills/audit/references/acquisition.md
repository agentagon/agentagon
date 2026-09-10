# Acquire traces

Select the newest N roots starting in `[from, to)`, or all when requested. Fetch every available descendant without reapplying the root time filter. Keep acquisition within the selected project and traces; do not fetch neighboring session traces or generate new traffic.

## Estimate before downloading bodies

Reuse effective `traces` settings and resolve credential references in the provider process environment. Keep values out of command arguments, logs, receipts and exports.

Query only root IDs and start timestamps. Exhaust listing pages; mark capped or interrupted listings incomplete. Write an inventory in the ignored audit directory matching `contracts/v1/selection.json`: `source`, `project`, `from`, `to`, `method`, actual `tool_version`, `complete`, and `roots` containing `{id, started_at}`. The source, project and window must match the audit.

Run `agentagon --workspace CHECKOUT audit plan AUDIT_ID INVENTORY_JSON`. It validates scope, deduplicates roots, applies the window/count and saves selected IDs. Present the selected count, requested cap and inventory completeness before downloading. Incomplete inventories establish only a lower bound on eligible traces and selection among known roots. Explain that limit and let the user choose whether to fetch that selection. Span count and download bytes remain unknown.

Fetch exactly the saved IDs and their descendants, exhausting body pages. Resume from the saved plan. If no metadata-only route can establish root IDs, report the prerequisite and use an available local export; do not download unbounded bodies to estimate size. Existing local exports need no download plan.

## Download and retain evidence

Use the provider's official CLI recipe, pin its resolved version and inspect its version/help. Consult linked official documentation for differences; use the SDK/API when the CLI cannot express the root window, pagination or descendant fetch. Keep acquisition scripts in the ignored audit workspace and preserve native rows except agreed redaction.

For transient rate/network failures, honor Retry-After and retry at most three times. Correct authentication or unsupported-query errors before retrying. Checkpoint completed IDs; resume only missing IDs, retaining partial exports and failures.

Export into `.agentagon/audits/AUDIT_ID/exports/`. Keep logs and receipts outside that directory to avoid importing them as provider records.

Write a receipt matching `contracts/v1/acquisition.json`: `source`, `project`, `method`, actual `tool_version`, `fetched_at`, `completeness`, `pagination_complete`, `selected_trace_ids` and `failed_trace_ids`. The selected set must exactly match the plan, including failed IDs; the importer rejects mismatches.

Completeness describes the available snapshot; running traces may gain spans later. Use `unknown` when completeness cannot be established, and `partial` for failed pages, missing descendants, conflicting records or truncation. Set `pagination_complete` false for unfinished listing/fetch pages. Pagination can be complete for an N-trace selection without covering all production traffic.

Import exports with the receipt once acquisition is ready for review. The CLI reapplies the root window/count and saves diagnostics. Finish acquisition before semantic review; changing evidence after analysis starts requires a new audit.
