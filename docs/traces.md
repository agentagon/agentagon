# Connect execution traces

Traces add recorded runtime behavior to an audit: calls, timing, failures, and available usage. Use them when source inspection alone cannot show how your agent behaved in real executions.

Agentagon imports supported exports. Your coding host retrieves provider data through available tools; Agentagon is not an always-on production trace collector.

## Choose a source

| Source | How to start |
|---|---|
| Braintrust | Supply a project and provider access, or a compatible export. |
| Langfuse | Supply project/endpoint and the required credential references, or an export. |
| LangSmith | Supply project and provider access, or an export. |
| Phoenix | Supply project/endpoint and access when required, or an export. |
| OpenTelemetry | Supply a supported local export. There is no universal historical-query endpoint. |

Exact supported shapes are documented in the [export-format reference](../skills/audit/references/formats.md). A file from a provider is not automatically compatible with every export endpoint or format that provider offers.

## Option A: use an existing local export

Open the application in your coding host and select **ag:audit**. Give it the file and explicit selection details:

```text
Audit the traces in /path/to/export.json in trace-only mode.
Provider: braintrust. Project: MY_PROJECT.
Use the window from 2026-08-01T00:00:00Z to 2026-08-02T00:00:00Z
and select up to 50 whole traces. Use the local export; do not fetch
additional provider data. Explain any import or coverage limitations.
```

Replace the provider, path, project, dates, and count with values that match your data. Dates must include a timezone. A count selects whole traces, not individual spans. Use `all` only when you intend to inspect every matching trace within the chosen window.

Local exports do not require a provider connection. Use only data permitted for your coding host to inspect.

## Option B: save a provider connection

Use **ag:setup** in Codex or `/ag:setup` in Claude Code. For example:

```text
Configure Braintrust traces for this checkout using project MY_PROJECT.
The provider key is available to this host as BRAINTRUST_API_KEY.
Store that credential reference and enable traces. Do not retrieve data yet.
```

Configure the actual secret outside chat and ensure the host can read it. The saved setting contains only its variable name.

For direct configuration:

```sh
agentagon setup --scope project --set traces.source braintrust --set traces.project MY_PROJECT --set traces.api_key_env BRAINTRUST_API_KEY --set traces.state enabled
```

Other providers may need endpoint or public-key references; use [Settings and profiles](settings.md). Saving a connection does not authorize downloads or production instrumentation.

## Plan the retrieval

When asking for an audit, provide a timezone-aware time window and trace count. Before fetching bodies, the coding host lists root metadata and prepares a selection plan. Inspect the source, project, dates, and selected IDs/count.

An incomplete provider inventory is a lower bound, not proof that all relevant traces were found. The host retrieves the selected roots and available descendants, then the CLI imports and normalizes them.

## Combine traces with code

Choose `combined` mode and state any known deployment or revision mismatch:

```text
Audit this application and the selected traces in combined mode.
The traces came from the last deployment; this checkout includes newer
uncommitted changes. Preserve that limitation when relating code to failures.
```

With dirty or non-Git source, alignment cannot be verified. Even matching revision metadata in a clean checkout remains a provenance assumption. Contradictory metadata prevents unsupported code/trace correlation. The report retains these limitations.

Changes reviews stay code-only by default. To add traces to `ag:review`, explicitly explain their connection to the captured changes; trace-only mode cannot satisfy a changes review.

## Interpret runtime measurements

Missing token or cost values remain unknown. Parent/child usage is not blindly summed when it overlaps. Trace counts and failure rates describe the selected reviewed corpus, not estimated production prevalence.

Import diagnostics and coverage matter as much as the findings. Read [Reports and issues](reports.md) before generalizing from a sample.

## Disconnect later

```sh
agentagon setup --scope project --set traces.state disabled
```

This changes future audit defaults and retains existing audit evidence. Use `unset` instead of `disabled` only if you want the trace-setup question to appear again.
