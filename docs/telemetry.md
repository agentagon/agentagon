# Anonymous product telemetry

Agentagon records anonymous skill invocations and Intelligence guidance usage. Telemetry is enabled by default and requires no Agentagon account or Intelligence API key. It never changes a workflow result or prevents local work.

## Disable or inspect

```sh
agentagon setup --scope user --set telemetry.enabled false
agentagon setup --scope user
```

Disabling clears pending events. `AGENTAGON_TELEMETRY_DISABLED=1` overrides the saved setting in the current process; an attempted tracking call clears pending events and skips collection while it is set. Re-enable with the same setup command using `true`. Events from disabled periods are not collected or backfilled.

The setting is user-wide; projects cannot override it. `setup` and `status` show effective enablement, whether a public project token is configured, pending count, dropped count and the last delivery result. These reads never create storage, send events or drain the queue. An already-started network request may finish after disablement.

## One function, one hook

```python
from agentagon.usage import track

result = track("skill_invoked", skill="audit", host="codex")
```

```sh
agentagon telemetry skill_invoked --data '{"skill":"audit","host":"codex"}'
```

Skills call this once at workflow start. Host is `codex`, `claude-code` or `unknown` (the default). Calling ordinary CLI commands or reading skill documentation does not count as an invocation.

| Event | Recorded behavior | Event-specific public fields |
|---|---|---|
| `skill_invoked` | A skill workflow started | Skill name and host enum |
| `intelligence_lookup_completed` | A lookup returned, including failure or cache reuse | Outcome enum, phase, duration in milliseconds, cache flag, result count |
| `knowledge_returned` | A new lookup receipt contains an entry | Entry ID and, when compatible, knowledge version |
| `knowledge_investigated` | The host examined local evidence against an entry | Entry ID and, when compatible, knowledge version |
| `knowledge_cited` | The host associated an entry with an existing saved finding | Entry ID and, when compatible, knowledge version |

The lookup client records a summary and new returned entries together in one delivery attempt for the owning audit, evaluation or fix run. Cached rereads record a lookup cache hit without repeating returned entries. Direct Python lookup tracking accepts `workspace`, exactly one owner reference (`audit_id`, `evaluation_id` or `run_id`), `receipt`, integer `duration_ms` (0–300,000) and boolean `cached`; it derives outcome and counts from the registered receipt, rather than caller-authored text.

To report investigated or cited stages, follow the [shared skill reference](../skills/audit/references/telemetry.md). These stages are audit-only: Python callers pass `audit_id`, `receipt`, `entry_id` and optional `finding_id`, plus `workspace` as a path or `Workspace` object. Receipts must belong to the selected audit, entries must occur in a successful receipt, and cited findings must already exist in that audit. Local annotations live beside the owning state in `usage.json`; immutable evidence is not rewritten. Each owner/receipt/entry/stage is counted once. An entry can be investigated without producing a finding, and a citation does not prove improvement.

## Data boundary

Every event includes a schema version, Agentagon release version, random event UUID and timestamp. The event UUID does not identify an installation, person, project or session. Retries preserve the same identity and timestamp. The telemetry configuration disables person profiles and location enrichment and requires connection IP data to be discarded.

Only the public fields in the table are permitted. The CLI uses audit/evaluation/run, receipt and finding references locally for validation; none is transmitted. No credentials, paths, repository identities, customer identifiers, context, focus, goals, prompts, code, traces, evidence, finding text or exception text is included. Entry IDs must match the authored catalog's lowercase letter/digit/hyphen format. Knowledge versions are transmitted only when they match the service's SHA-256 fingerprint format; other legacy opaque values are omitted.

The analytics service receives connection metadata during processing. IP-discard and enrichment settings govern stored event data; infrastructure metadata is governed by the service’s privacy settings.

These are event counts, not unique-user metrics or evidence that guidance caused an improvement. Skill/stage reporting depends on the host executing the hook. Public ingestion can receive fabricated events, and outages or queue limits can lose events.

## Queue and delivery

The private SQLite queue is stored in `config.json.telemetry/queue.sqlite3` beside the selected config file (`AGENTAGON_CONFIG` also isolates telemetry). Its directory is private and the database is mode `0600`. Pending payloads are capped at 1,000 events, 1 MiB and seven days; expired or oldest events are discarded on recording. These are retained-payload limits, not a filesystem quota for SQLite pages and journals.

Each tracking call attempts at most one batch of 20 events with a two-second total HTTP deadline. Failed requests back off from one minute to one hour, honoring bounded `Retry-After`; eligible events retry on later tracking calls. There is no daemon, polling or background process. Python calls inside an already-running asyncio loop enqueue without attempting nested synchronous delivery. Changing the project token discards pending events associated with the old destination.

Payloads are validated before queueing and again before transmission. Redirects and environment-derived HTTP proxies/credentials are disabled. Transient failures, quota-limited responses and invalid responses retain pending events; permanently rejected batches are dropped. Transport/storage failures produce a sanitized status and never interrupt the parent workflow. `accepted` means the analytics service accepted the request; individual events may take time to appear and duplicate retries may take time to reconcile.

## Maintainer setup

Before release, configure the telemetry service to discard connection IP data and disable person profiles and location enrichment. Use only public ingestion credentials; never bundle personal or secret API keys.

Verify synthetic events from the installed package before claiming live delivery. Record privacy settings and verification results in release evidence. Builds without configured ingestion retain a bounded queue and report `unconfigured` without making requests.
