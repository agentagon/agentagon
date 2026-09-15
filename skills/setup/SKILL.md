---
name: setup
description: Configure Agentagon settings, Intelligence and trace access, or execution profiles for an application or user.
---

# Agentagon setup

At workflow start, run `agentagon telemetry skill_invoked --data '{"skill":"setup"}'` once per invocation. Add `"host":"codex"` or `"host":"claude-code"` when known. Honor opt-out and continue if the hook is unavailable. See [telemetry](../audit/references/telemetry.md).

Inspect `agentagon --workspace CODEBASE setup` before asking for missing choices. It reports the config path, effective settings, credential-presence flags and pending onboarding. User-wide setup works without an application checkout; do not invent one or open a dashboard for settings alone. If Setup is nested inside Init, Audit, Eval or Fix, preserve that journey's existing dashboard and selection.

One config file holds user defaults and project overrides. Precedence is explicit invocation, project setting, user default, then built-in default. Each resolved checkout, including a Git worktree, has a separate project entry.

Route by the requested change and read only the relevant procedure:

- For Intelligence, traces, telemetry, onboarding, ordinary `--set`/`--unset` operations or credential references, read [settings and access](references/settings.md).
- For local, SSH or E2B execution profiles, budgets, search policies, scans or repetitions, read [execution profiles](references/profiles.md).

Make only the requested configuration change. Reuse existing choices; ask only for material missing values. Configuration stores references rather than secret values and never grants permission to upload data, run remote commands, install dependencies or expand an exhausted budget.

After updating, inspect Setup again. Report the changed scope and effective values, using credential-presence flags instead of values. Existing audits and runs retain their recorded evidence and execution settings.
