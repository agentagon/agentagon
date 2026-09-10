# LangSmith

Use `LANGSMITH_API_KEY`, `LANGSMITH_ENDPOINT` and any required workspace context. Resolve the project ID from the project list.

For preflight, SDK `list_runs` supports `is_root=True`, time filtering and selecting only `id`, `trace_id` and `start_time`. Use the upper-bound `lt(start_time, "END")` filter. Preserve the root-run/trace-ID mapping; the selection inventory needs trace IDs. See [SDK query guidance](https://docs.langchain.com/langsmith/export-traces).

Install the platform binary from a pinned GitHub release and verify its published checksum. Require a backend-compatible version; SmithDB support is documented from 0.2.44. Inspect trace export help.

```sh
langsmith trace export EXPORT_DIRECTORY --project-id PROJECT_ID --since START --limit COUNT --full --filter FILTER
```

Supply the same upper-bound filter. Check trace-tree behavior: `--full` controls fields and does not prove children were fetched. Use hierarchy or trace-specific retrieval when needed.

Keep native JSONL run rows, including `id`, `trace_id`, `parent_run_id`, inputs, outputs, error and usage. Override default recent-window/count limits for the requested scope. Never run `trace setup`.

Sources: [CLI and releases](https://github.com/langchain-ai/langsmith-cli), [query syntax](https://docs.langchain.com/langsmith/trace-query-syntax).
