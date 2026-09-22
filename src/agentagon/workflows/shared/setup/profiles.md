# Execution profiles

Use this procedure for named evaluation and fix execution profiles. Inspect `settings.profiles` and reuse an appropriate profile when one already exists. Save a complete profile with `agentagon _internal --workspace CHECKOUT setup --scope SCOPE --profile NAME --profile-file PROFILE_JSON`. Do not combine a profile change with `--set` or `--unset`.

A user profile applies across checkouts. A project profile replaces the complete same-named user profile rather than merging fields. Preserve every existing field when editing one. Follow the [fix profile contract](../../optimize/references/contract.md) for the full JSON shape.

Choose `local`, an existing SSH destination or E2B. Proposals and source edits stay in the coding host. First use requires explicit values for `max_candidates`, `max_trials`, `max_elapsed_seconds`, `parallel_candidates`, `parallel_trials` and `trial_timeout_seconds`. Save and reuse those limits. Do not increase them because a run exhausts its budget, and record real independent runner capacity before enabling concurrency.

Profiles store setup commands as argument arrays and `env` as a mapping from target variable names to source environment-variable names. Never put secret values in JSON, argv or conversation. Remote execution requires authorization for the selected code/data, commands, destination and credentials; the profile alone provides none. Verify the effective profile and report its scope.

Optional complete-profile fields:

- `search` chooses parents for future candidates. Use the built-in `pareto` strategy with seed 0 unless the user selects another policy. `argmax`, `top_k`, `epsilon_greedy` and `softmax` require an objective matching a declared metric. Their optional parameters are `k` (default 3), `epsilon` (default 0.1) and positive `temperature` (default 1). All strategies accept an integer `seed`; `pareto_per_task` requires frozen task metrics. Search never changes hard constraints or the verified Pareto frontier.
- `scans` enables host analysis of completed experiment rounds. Obtain explicit positive `max_scans`, `max_input_bytes` and `scan_timeout_seconds`; input size must be 1024–1048576 bytes. Omit `scans` to keep it disabled. Saved scan limits do not authorize an external model service or transmission destination.
- `repetitions` sets the profile default, otherwise three. An explicit run specification takes precedence; the resolved count and seeds freeze at run start.

Existing runs retain their frozen profile and scan settings. Change an active search policy through `fix steer` using [optimization](../../fix/instructions.md); do not edit canonical state or imply Setup rewrites historical results.
