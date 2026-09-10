"""Copy this stdlib-only helper into a benchmark; no Agentagon import is required."""

import json
import os
from datetime import datetime, timezone

EVENTS = {"task_start", "progress", "input", "output", "failure", "artifact", "task_end"}


def emit(event, task_id, data=None):
    """Append one diagnostic JSONL record. Return False when instrumentation is disabled."""
    path = os.environ.get("AGENTAGON_EVENTS_PATH")
    if not path:
        return False
    if event not in EVENTS or not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
        raise ValueError("invalid task event or task identity")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("event data must be an object")
    record = {
        "version": 1,
        "event": event,
        "task_id": task_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    line = (json.dumps(record, allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > 65536:
        raise ValueError("event exceeds 64 KiB; retain large output as an artifact")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(descriptor, line) != len(line):
            raise OSError("partial task event write")
    finally:
        os.close(descriptor)
    return True
