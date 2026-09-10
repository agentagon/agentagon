---
name: setup
description: Configure Agentagon intelligence access, trace connections, named execution profiles, and user or checkout settings.
argument-hint: "[settings to change]"
---

# Agentagon setup

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"setup"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Anonymous telemetry is enabled by default; honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

At start or resume, follow [automatic dashboard opening](../dashboard/SKILL.md#automatic-workflow-start) for this application before substantive work. Reuse the session dashboard across nested skills, select this workflow’s active record as soon as its ID is known, and include its link in the result.

Inspect `agentagon --workspace CODEBASE setup` before asking for missing choices. Source directories without Git support project settings too; use `agentagon setup --scope user` for user-wide defaults. Output shows the config path, effective settings, credential-presence flags, and pending onboarding.

One config file holds user defaults and project overrides. Precedence: explicit invocation choices, project settings, user defaults, built-in defaults. Each resolved checkout root, including a Git worktree, has a separate project entry; audit history stays in its ignored `.agentagon/`.

Update with `agentagon --workspace CODEBASE setup --scope SCOPE --set KEY VALUE`; use `--unset KEY` to remove an override. `SCOPE` is `user` or `project`; project scope uses the resolved source directory or Git checkout root. Repeat options for different keys. Default intelligence access to user scope and provider connections to project scope unless directed otherwise. Verify effective settings with the inspection command and report which scope changed.

Supported settings:

| Key | Value |
|---|---|
| `intelligence.endpoint` | Issued HTTPS service origin; no path or credentials |
| `intelligence.api_key_env` | Key environment variable name; default `AGENTAGON_API_KEY` |
| `intelligence.access_presented` | `true` or `false`, user scope only |
| `telemetry.enabled` | Anonymous usage events; `true` by default, user scope only. Set `false` to stop sending and clear pending events. |
| `traces.state` | `enabled`, `disabled`, or `unset`, project scope only |
| `traces.source` | `braintrust`, `langfuse`, `langsmith`, `phoenix`, or `otlp` |
| `traces.project` | Provider project identifier |
| `traces.endpoint` | Provider endpoint when needed |
| `traces.api_key_env` | Provider credential environment variable name |
| `traces.public_key_env` | Langfuse public-key environment variable name |

Config stores credential references, never secret values. Reuse configured credentials and provider profiles. Have users configure missing credentials outside chat in the referenced environment variables; verify presence flags without printing values. Saving settings does not authorize downloads, instrumentation, or vendor setup wizards.

## Execution profiles

For `ag:fix`, inspect `settings.profiles` and reuse an appropriate named profile. Save a complete profile using `agentagon --workspace CHECKOUT setup --scope SCOPE --profile NAME --profile-file PROFILE_JSON`. Do not combine profile changes with `--set` or `--unset`. User profiles apply across checkouts; a project entry replaces the entire same-named user profile, rather than merging individual runner or limit fields.

Follow [the fix profile contract](../fix/references/contract.md). Choose `local`, an existing SSH destination, or E2B for evaluation execution; proposals and edits remain local. Ask only for missing material choices. First use requires the user's explicit `max_candidates`, `max_trials`, `max_elapsed_seconds`, `parallel_candidates`, `parallel_trials` and `trial_timeout_seconds`; save and reuse those limits. Do not select larger limits because a run exhausts its budget. Record honest independent runner capacity before enabling concurrent trials.

Profiles store argument arrays for setup commands and an `env` mapping from target variable names to source environment-variable names. Never place secret values in JSON, argv or conversations. Remote execution requires authorization for the chosen code/data, commands, destination and credentials; configuration alone is not that authorization. Verify the saved effective profile and identify the changed scope.

Save optional `search`, `scans` and `repetitions` inside the complete profile, not through `--set` keys:

- `search` selects parents for future candidates. Default to the built-in `pareto` strategy with seed 0 unless the user chooses another policy. `argmax`, `top_k`, `epsilon_greedy` and `softmax` require an explicit `objective` matching a declared metric. Their respective optional parameters are `k` (default 3), `epsilon` (default 0.1) and positive `temperature` (default 1). All strategies accept integer `seed`; `pareto_per_task` requires task metrics frozen in the run specification. Search never replaces the verified Pareto frontier or changes hard constraints.
- `scans` enables host analysis of completed experiment rounds. Obtain explicit positive `max_scans`, `max_input_bytes` and `scan_timeout_seconds` choices before saving it; the input limit must be 1024–1048576 bytes. Omit `scans` to keep this optional analysis disabled. Saved scan limits do not authorize a new external model service or transmission destination.
- `repetitions` sets the profile default, otherwise three. An explicit run specification takes precedence; the resolved repetition count and seeds freeze at run start.

Preserve all existing profile fields when changing these options because saving a profile replaces the complete same-named profile. Existing runs retain their original execution and scan settings. Change a running search policy through `fix steer` using [ag:fix](../fix/SKILL.md); do not edit canonical state or imply that changing setup rewrites prior results.

## Audit onboarding

When `intelligence.onboarding_pending` is true, ask once whether the user has a key; explain the returned `intelligence.access_message`. For hosted access, offer the documented origin `https://brain.agentagon.ai`; honor an explicitly supplied alternative origin. Reuse access choices already made in this session. Privacy-safe workflow fields can yield additional suggestions: context/focus for audits, context/goal for evaluations, and required focus with optional context for fixes. Direct access requests to hello@agentagon.ai without sending email. After discussing access, record `--scope user --set intelligence.access_presented true`. Missing access never blocks a local audit, evaluation preparation or fix run.

When `traces.onboarding_pending` is true, ask whether to connect traces unless the request already answers that question. Reuse source/project choices and save `--scope project --set traces.state enabled` or `disabled` to remember the decision. Disconnect with `disabled`; retained audits remain. Use `unset` only to request onboarding again. Enabled traces default to combined auditing; an explicit audit mode wins.

Date range and trace count are per-audit choices; never silently default them. OpenTelemetry local files need no remote connection; there is no universal OpenTelemetry historical-query endpoint.
