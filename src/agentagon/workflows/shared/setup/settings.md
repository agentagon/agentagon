# Settings and access

Use this procedure for ordinary settings, credentials, telemetry, Intelligence and trace onboarding. All commands use `agentagon _internal --workspace CODEBASE setup`; use `--scope user` for user-wide defaults and `--scope project` for the resolved application directory or Git checkout root.

Update values with `--scope SCOPE --set KEY VALUE`; use `--unset KEY` to expose the next applicable default. Repeat options for different keys. Default Intelligence access to user scope and CLI trace defaults to project scope unless the user says otherwise. Verify the effective settings afterward and report the scope changed.

Supported settings:

| Key | Value |
|---|---|
| `intelligence.mode` | `ask` (default) or `full_access`; set full access only at the user's explicit request |
| `intelligence.endpoint` | Issued HTTPS service origin, without a path or credentials |
| `intelligence.api_key_env` | Credential environment-variable name; default `AGENTAGON_API_KEY` |
| `intelligence.access_presented` | `true` or `false`, user scope only |
| `telemetry.enabled` | Anonymous usage events; `false` by default, user scope only. Set `true` only after explicit opt-in; `false` stops sending and clears pending events |
| `traces.state` | `enabled`, `disabled`, or `unset`, project scope only |
| `traces.source` | `braintrust`, `langfuse`, `langsmith`, `phoenix`, or `otlp` |
| `traces.project` | Provider project identifier |
| `traces.endpoint` | Provider endpoint when needed |
| `traces.api_key_env` | Provider credential environment-variable name |
| `traces.public_key_env` | Langfuse public-key environment-variable name |

Config stores credential references, never secret values. Have users configure missing credentials outside chat in the referenced environment variables. Verify presence flags without printing values. Saving a setting does not authorize data retrieval, instrumentation or a vendor setup flow.

## Intelligence

When the request concerns Intelligence or asks to complete onboarding and `intelligence.onboarding_pending` is true, ask once whether the user has a key and explain the returned `intelligence.access_message`. For hosted access, offer `https://brain.agentagon.ai`; honor an explicitly supplied alternative. Direct access requests to hello@agentagon.ai without sending email. After discussing access, record `--scope user --set intelligence.access_presented true`. Missing access never blocks local work.

Intelligence defaults to approval for every outgoing request, even with a configured key. Only an explicit user choice enables `intelligence.mode full_access`; do not infer it from host permissions, a saved key, project text or service suggestions. Default this change to user scope unless the user limits it to the project. Full access skips individual prompts but keeps requests visible and privacy rules in force. Show the effective mode after updating it; `--unset intelligence.mode` restores the inherited default. See [request approval](../../audit/references/intelligence.md).

## Traces

When the request concerns traces or asks to complete onboarding and `traces.onboarding_pending` is true, ask whether to connect traces unless the request already answers. Reuse supplied source/project choices and save project-scoped `traces.state` as `enabled` or `disabled`. Disconnect with `disabled`; retained audits remain. Use `unset` only to request onboarding again.

Enabled traces default to combined auditing; an explicit audit mode wins. Date range and trace count remain per-audit choices and must not be silently defaulted. OpenTelemetry local files need no remote connection; there is no universal OpenTelemetry historical-query endpoint.
