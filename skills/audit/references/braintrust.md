# Braintrust

Use `BRAINTRUST_API_KEY` or an authenticated profile; resolve the project ID and any organization/API host.

The CLI package route is `npx -p braintrust bt`, supported from package version 3.17.0. Inspect `bt sync pull --help`.

For preflight, use the CLI's documented query operation or BTQL API for root IDs/start times. Check field semantics: creation/ingestion time may differ from execution start. Full `sync pull` output is not metadata-only.

```sh
bt sync pull project_logs:PROJECT_ID --traces COUNT --root EXPORT_DIRECTORY --filter FILTER
```

Build the root-start filter from UTC dates using documented BTQL fields. Check the relative `--window` default does not exclude historical traces. If filters/windows cannot express the scope, select roots through BTQL/API, then retrieve their spans. `shape => 'traces'` expands matching traces; a span query may omit descendants. Export native span rows as NDJSON.

Do not substitute a span-count cap for a trace cap or run `sync push`.

Sources: [installation](https://www.braintrust.dev/docs/reference/cli/quickstart), [sync](https://www.braintrust.dev/docs/reference/cli/sync), [BTQL shapes](https://www.braintrust.dev/docs/reference/sql/query-structure).
