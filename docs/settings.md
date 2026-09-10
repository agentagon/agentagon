# Settings and profiles

Use **ag:setup** to configure how Agentagon works for you and for a particular application. Core code audits need no configuration; save settings when you want reusable execution limits, trace connections, or optional guidance.

## Inspect what applies

From your application directory:

```sh
agentagon setup
```

The output shows the config path, effective settings, execution profiles, and whether optional access is configured. It reports credential-presence flags without displaying secret values.

In Codex, select **ag:setup**. In Claude Code, use `/ag:setup`. For example:

```text
Show the effective settings for this application. Keep traces disabled
and Intelligence unconfigured. Explain any settings I still need before
running a local evaluation.
```

## User defaults and project overrides

Settings take precedence in this order:

1. Explicit choices in the current invocation.
2. Project overrides for this application checkout.
3. Your user defaults.
4. Built-in defaults.

Project entries use resolved directory paths or Git checkout roots. Separate worktrees have separate project settings.

One file stores user defaults and project entries: `$XDG_CONFIG_HOME/agentagon/config.json`, falling back to `~/.config/agentagon/config.json`. Set `AGENTAGON_CONFIG` to an explicit file to isolate settings for an experiment. Ensure the coding host and CLI use the same file.

## Save and remove a setting

```sh
agentagon setup --scope user --set traces.source braintrust
agentagon setup --scope project --set traces.state disabled
agentagon setup --scope project --unset traces.source
```

Removing an override exposes the next applicable value. Re-read `agentagon setup` to confirm the result. Use `--workspace /path/to/application` before `setup` when outside the application directory.

| Setting | Value and scope |
|---|---|
| `traces.state` | `enabled`, `disabled`, or `unset`; project scope. `unset` asks about traces again. |
| `traces.source` | `braintrust`, `langfuse`, `langsmith`, `phoenix`, or `otlp`. |
| `traces.project` | Provider project identifier. |
| `traces.endpoint` | Provider endpoint when needed. |
| `traces.api_key_env` | Environment-variable name containing the provider key. |
| `traces.public_key_env` | Environment-variable name for a Langfuse public key. |
| `intelligence.endpoint` | Issued Intelligence service origin, such as `https://brain.agentagon.ai`; see [Intelligence](intelligence.md). |
| `intelligence.api_key_env` | Environment-variable name; defaults to `AGENTAGON_API_KEY`. |
| `intelligence.access_presented` | `true` or `false`; user scope. Remembers whether optional access was discussed. |
| `telemetry.enabled` | `true` or `false`; user scope. Projects cannot override it. |

Secrets belong in environment variables or a credential store, not configuration JSON, command arguments, or chat. Save only the variable name. The coding host and the process running the CLI need access to that environment.

## Configure execution before evaluations or fixes

An **execution profile** is a named configuration for the runner, environment references, setup commands, and limits. Choose one through `ag:setup` before starting your first evaluation or fix.

For a small local experiment, you could request:

```text
Save a project execution profile named local. Use the local runner,
3 candidates, 24 total trials, 1800 seconds elapsed time, 60 seconds per
trial, one candidate at a time, and one trial at a time. Use no setup
commands or injected credentials. Show the saved settings.
```

These are example limits to choose for your workload, not a guarantee the job will finish. Trials include baseline executions and cancelled or replaced attempts. Later runs reuse the saved profile; exhaustion does not silently increase it.

For the complete JSON shape and runner options, see [Configure execution once](reference/fix.md#configure-execution-once).

## Save a complete profile directly

Prepare a profile file using that documented shape, then:

```sh
agentagon setup --scope project --profile local --profile-file PROFILE_JSON
agentagon setup
```

Replace `PROFILE_JSON` with the file path. Profile input cannot be combined with `--set` or `--unset`. A project profile replaces the entire same-named user profile, so preserve its fields when editing it.

## What changes affect active work?

An active run retains its frozen execution profile. Editing the saved profile changes future runs. Use [run controls](reference/fix.md#steer-a-run-without-losing-queued-work) to change supported active-run behavior, such as future search policy or explicit continuation limits.

Trace dates and counts are per-audit choices; settings never silently supply a new time window. Saving provider settings does not authorize data retrieval or instrumentation.
