# Remove the temporary GEPA compatibility code

**TODO:** Replace `src/agentagon/experiments/gepa_runtime.py` with upstream
imports once a compatible GEPA release is available on PyPI.

Agentagon 0.1.3 depends on published `gepa==0.1.4` for the actual search
algorithm. That wheel lacks `gepa.oa`, so Agentagon carries only the
in-process orchestration needed by its native-host adapters. PyPI rejects
direct URL dependencies; a GitHub archive cannot remain in package metadata.

The subset is adapted from upstream revision
`0632cdb5dcc052e690eab439e1b4a7e3e9cfe407`, whose archive SHA-256 is
`1ea34ad34724b65a67cf6531bae6d403726eec3cf7dca7be62c1162c80641e1b`.
Its sources are `oa/budget.py`, `oa/config.py`, `oa/task.py`, `oa/engine.py`, `oa/eval_server.py`,
`oa/ensemble.py` and `oa/engines/gepa.py` in
[GEPA](https://github.com/gepa-ai/gepa/tree/0632cdb5dcc052e690eab439e1b4a7e3e9cfe407/src/gepa).
The MIT notice is retained in `THIRD_PARTY_NOTICES.txt`.

Local adaptations keep only string candidates, caller-owned stages, serialized
in-process evaluation and native-host proposal callbacks. The engine uses
the published `gepa.optimize_anything` API. No GEPA algorithm, HTTP endpoint,
external host launcher, examples or unrelated integration code is copied.
Agentagon's durable ledger continues to own trial admission and evidence;
the compatibility code does not invent scores for failed measurements.

## Removal checklist

1. Inspect the actual PyPI wheel for the budget, task, configuration, result,
   evaluation-server, parallel-composition and GEPA engine APIs used by
   `experiments/optimizer.py`. Upstream source availability alone is insufficient.
2. Pin the compatible published version and replace the local imports. Preserve
   the recorded runtime identity so later results identify the implementation.
3. Run `tests/test_optimizer.py` and `tests/test_optimize_run.py` against that
   wheel. Verify all engines, fresh refinement, host concurrency, interrupted
   replay, repeated-trial bounds, target stopping and failed measurements.
4. Run the full suite and fresh-wheel installation checks. Then remove the
   compatibility module, its tests, this TODO and the copied-source notice.
   The upstream distribution continues to carry its own license.
