# Propose and agree on measurements

Input: goal, scoped evidence, existing evaluator inventory and optional previous proposal.

Propose observable behaviors and the smallest useful metric set. Define success explicitly. Choose a primary metric, or explain weights and scales for a weighted score. Keep required behaviors as separate gates; a higher score cannot offset failure. Missing observations remain unknown or fail according to the accepted policy. Demonstrate how the measurement distinguishes a correct example from an incorrect one.

For an app-managed design task, return final JSON with `summary` and `measurement_design`. The latter has these fields:

- `behaviors`: objects with `id`, `description`, `required`, and either `check` (the executable check to prepare) or `metric`, `op` (`gte`/`lte`), `bound`. Optional `rubric`, `evidence` and `prerequisites` describe authority and missing observation needs.
- `metrics`: map of metric names to `direction` (`min`/`max`), `unit`, `aggregation` (`mean`/`median`/`min`/`max`/`sum`), `missing` (`unknown`/`fail`), optional positive `scale`, positive `weight`, `description`, `evidence` and `prerequisites`. Weighted scoring needs every weight.
- `scoring`: `mode` (`primary`/`weighted`/`custom`), `primary` or `custom_metric` when applicable, optional numeric `target`.
- `evaluation`: for an existing frozen evaluator, use exactly `{"mode":"reuse","evaluation_id":"eval_..."}` and preserve its scoring. Otherwise use `mode` (`reuse`/`create`), `framework` (`braintrust`/`deepeval`/`pytest`/`custom`), optional native `command` (`argv` array, relative `cwd`), `entrypoint`, `dataset_snapshot_id`, `scorer` description/reference and `output_mapping` (metric to native result field). A detected source candidate is not a frozen evaluator.
- `evidence` and `limitations`: lists of concise strings. `background`: supported goal-specific observations and reasoning to retain for optimization, with references and uncertainty.

The proposal does not run or accept an evaluation. The browser saves it for editing and explicit acceptance. Do not claim acceptance from silence or from completion of this task. The service freezes accepted revisions and checks subsequent evaluator scoring against them.
