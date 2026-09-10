"""Native lifecycle callbacks, bound to one explicitly registered active host session."""

import fcntl
import os
from pathlib import Path

from agentagon.core.records import AuditError, identifier, load_json, now
from agentagon.experiments import orchestration
from agentagon.experiments.store import load_run
from agentagon.storage.workspace import Workspace


def _path(workspace: Workspace, session_id: str) -> Path:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 200:
        raise AuditError("a native host session ID is required")
    return workspace.checked(
        workspace.state / "host-sessions" / (identifier("session", session_id) + ".json")
    )


def bind(
    workspace: Workspace, run_id: str, session_id: str, host: str, *, pause: bool = False
) -> dict:
    if host not in {"codex", "claude-code"}:
        raise AuditError("continuation supports the existing Codex and Claude Code hosts")
    data = load_run(workspace, run_id)
    if "orchestration" not in data["profile"]:
        raise AuditError(
            "historical runs retain manual resume; start a new run for session orchestration"
        )
    target = _path(workspace, session_id)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = load_json(target) if target.exists() else {}
        if previous.get("run_id") != run_id or previous.get("host") != host:
            previous = {}
        record = {
            **previous,
            "run_id": run_id,
            "session_id": session_id,
            "host": host,
            "origin": str(workspace.root),
            "paused": pause,
            "updated_at": now(),
        }
        workspace.write(target, record)
    return {
        "run_id": run_id,
        "host": host,
        "session_id": session_id,
        "paused": pause,
        "continuation": "observed_native_hook"
        if record.get("native_observed")
        else "manual_resume",
        "observed_events": record.get("observed_events", []),
        "next_action": "Use fix next to resume. Native hooks must be supported and trusted by the host; installation never grants that trust.",
    }


def handle(payload: dict) -> dict:
    """A failed or unbound callback returns no nudge. Never dispatch agents or evaluations."""
    if not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str):
        return {}
    event = payload.get("hook_event_name")
    if event not in {"SessionStart", "Stop", "SessionEnd", "Interrupt", "UserPromptSubmit"}:
        return {}
    try:
        workspace = Workspace(Path(payload["cwd"]))
        target = _path(workspace, payload.get("session_id"))
        if not target.exists():
            return {}
        fd = os.open(target.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = load_json(target)
            if (
                record.get("origin") != str(workspace.root)
                or record.get("session_id") != payload["session_id"]
            ):
                return {}
            record["observed_events"] = sorted({*record.get("observed_events", []), event})
            # Session restoration alone does not establish support or trust for Stop.
            if event == "Stop":
                record["native_observed"] = True
            if event in {"SessionEnd", "Interrupt", "UserPromptSubmit"}:
                if (
                    event == "UserPromptSubmit"
                    and payload.get("prompt") == record.get("last_nudge_reason")
                    and record.get("last_nudge_reason")
                ):
                    return {}
                record["paused"] = True
                workspace.write(target, record)
                return {}
            packet = orchestration.next_packet(workspace, record["run_id"])
            if event == "SessionStart":
                workspace.write(target, record)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": f"Agentagon has retained run {record['run_id']} in {packet['state']} state. Read fix next {record['run_id']} for context. Restoration does not launch work. Resume only within this task's authorization; rebind after a user prompt if appropriate.",
                    }
                }
            if (
                record.get("paused")
                or not packet["actionable"]
                or record.get("last_progress") == packet["progress_digest"]
            ):
                workspace.write(target, record)
                return {}
            record["last_progress"] = packet["progress_digest"]
            record["last_nudge_at"] = now()
            reason = f"Continue this authorized Agentagon task: read fix next {record['run_id']} and handle its actionable work within saved limits. Reuse existing candidate and agent reservations. Pause the session binding when user input is required; stop on unchanged progress, selection, stop or exhaustion."
            record["last_nudge_reason"] = reason
            workspace.write(target, record)
            return {"decision": "block", "reason": reason}
    except (AuditError, OSError, ValueError, KeyError, TypeError):
        return {}
