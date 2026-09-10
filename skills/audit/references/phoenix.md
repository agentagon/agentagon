# Phoenix

Use Arize Phoenix, not Arize AX: `PHOENIX_ENDPOINT`, `PHOENIX_PROJECT` and `PHOENIX_API_KEY` when authentication is required.

For preflight, use `GET /v1/projects/{project_identifier}/traces` with `start_time`, exclusive `end_time`, `sort=start_time`, `order=desc`, pagination `cursor` and `include_spans=false`. Retain `trace_id`/`start_time`. Require server support for this [trace-list API](https://arize.com/docs/phoenix/sdk-api-reference/rest-api/api-reference/traces/list-traces-for-a-project); client installation alone does not establish support.

Use `@arizeai/phoenix-cli`, executable `px`; inspect trace-list help.

```sh
px trace list EXPORT_DIRECTORY --since START --limit COUNT --format raw --no-progress
```

If the CLI cannot apply the upper bound before selecting N roots, use SDK/API date filters to select IDs, then retrieve each trace. Filtering after a newest-first capped fetch can miss the requested historical range.

Retain native span JSON or OTLP JSON, including parent IDs, attributes, resource and instrumentation scope. OpenInference attributes supply message/tool context. Do not import pretty terminal output. Annotations/notes are optional evidence within selected traces.

Sources: [CLI](https://arize.com/docs/phoenix/sdk-api-reference/typescript/arizeai-phoenix-cli), [span client](https://arize-phoenix.readthedocs.io/projects/client/api/spans.html).
