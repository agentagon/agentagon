# Langfuse

Use configured references for `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` with the regional/custom endpoint. Keys determine project access.

Use `langfuse-cli`; inspect its API schema/help. Require the v2 observations route, including on self-hosted servers. If unsupported by the CLI, use the API; do not substitute a deprecated trace endpoint.

For preflight, query `GET /api/public/v2/observations` with `fields=core`, `parentObservationId=` (physical roots), `fromStartTime` and exclusive `toStartTime`. Follow `meta.cursor`, retaining `traceId` and `startTime`. Do not substitute logical `isRootObservation`, which can include physical non-root spans.

For each selected trace, query the same route by `traceId` with field groups core, basic, time, io, metadata, model, usage, prompt, metrics and trace_context. Preserve native observations; stringified input/output is supported. Scores are separate data; model-generated scores are not human feedback.

Sources: [CLI](https://github.com/langfuse/langfuse-cli), [v2 observations API](https://langfuse.com/docs/api-and-data-platform/features/observations-api).
