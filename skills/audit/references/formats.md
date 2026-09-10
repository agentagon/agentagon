# Local export formats

| Source | Accepted input |
|---|---|
| Braintrust | Native BTQL span rows, JSON arrays or NDJSON; `data`/`spans`/`traces` list wrappers |
| Langfuse | Observation rows with `id`/`traceId`; observation/data list wrappers; stringified JSON I/O |
| LangSmith | Native run rows with `id`/`trace_id`/`run_type`; run lists and nested `child_runs` |
| Phoenix | Native span rows and `spans` wrappers, or OTLP JSON with OpenInference/GenAI attributes |
| OpenTelemetry | OTLP JSON documents or JSONL records containing `resourceSpans` → `scopeSpans` → `spans` |

Use the provider's source selector for local files. Files may be outside the checkout; normalized snapshots and retained records live in its ignored state. Originals remain unchanged.

The importer reads `.json`, `.jsonl` and `.ndjson`; unsupported files and malformed records become diagnostics. CSV, Parquet and protobuf are unsupported. Remote OpenTelemetry stores need a separately supported acquisition route.

Missing timestamps/usage remain unknown. Input/output may be arbitrary JSON; media references are preserved without downloading attachments. Retained records redact common credential fields/patterns, not every secret or PII. Apply the customer's permitted-data policy before model inspection.

Canonical timestamps are integer nanoseconds; provider IDs remain source-scoped.

Source: [OTLP file format](https://opentelemetry.io/docs/specs/otel/protocol/file-exporter/).
