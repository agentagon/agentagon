# Task evidence

Benchmarks can explain individual tasks without changing measurement or verification. Write UTF-8 JSONL to `AGENTAGON_EVENTS_PATH` and files to `AGENTAGON_ARTIFACTS_DIR`. Continue writing measurements to `AGENTAGON_RESULT_PATH`; task events never establish a score or pass a check.

The [event contract](../contracts/v1/task-event.json) has five fields: `version: 1`, `event`, `task_id`, `at`, and an object `data`. Event names are `task_start`, `progress`, `input`, `output`, `failure`, `artifact`, and `task_end`. Use `data.path` for an artifact path relative to the artifacts directory. End a completed task with `task_end`; include an explicit outcome in its data.

Copy the [Python helper](../skills/eval/helpers/agentagon_events.py) or [Node helper](../skills/eval/helpers/agentagon_events.cjs) into the benchmark package. Both use standard libraries, append one record at a time, and return false when instrumentation is disabled. Other languages can write the same format. Record only inputs and outputs the user has permitted you to retain.

```python
from agentagon_events import emit

emit("task_start", "search-17", {"case": "missing document"})
emit("failure", "search-17", {"message": "expected abstention; got an answer"})
emit("task_end", "search-17", {"ok": False})
```

New fix runs freeze explicit evidence limits in their profile: 2,000 events, 256 KiB of event input, 20 artifacts, 256 KiB per artifact, and 1 MiB of artifacts per trial. Profiles can configure these within the published schema bounds. These are retention limits, not a process disk quota. The helpers cap each event at 64 KiB.

Collection attaches the run, candidate, trial, source, and evaluation identities. Emitters cannot set those identities. Local, SSH, and E2B workers retain completed events after a failed or interrupted task, flag incomplete lines and missing task endings, reject malformed events, links and special files, and bound retained content. Oversized artifacts are rejected; events retain the complete prefix within the limits. Credential values injected by the profile are redacted from retained events, artifact bytes and artifact filenames.

The runner saves progress locally during execution. Dashboard reads use that mirror and never reconnect to a remote worker. Final events and artifacts are saved with the trial result before remote cleanup. Historical runs without evidence settings retain their existing execution contract.
