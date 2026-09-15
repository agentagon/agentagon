"""Bounded task evidence and shared retained results. Measurements stay separate."""

import base64
import copy
import fcntl
import hashlib
import re

from agentagon.core.records import AuditError, load_json

DEFAULT_LIMITS = {
    "max_events": 2000,
    "max_event_bytes": 262144,
    "max_artifacts": 20,
    "max_artifact_bytes": 262144,
    "max_total_artifact_bytes": 1048576,
}
MAXIMUM_LIMITS = {
    "max_events": 10000,
    "max_event_bytes": 524288,
    "max_artifacts": 100,
    "max_artifact_bytes": 1048576,
    "max_total_artifact_bytes": 2097152,
}


def validate_limits(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise AuditError("evidence limits must declare every event and artifact bound")
    for key, number in value.items():
        if type(number) is not int or not 1 <= number <= MAXIMUM_LIMITS[key]:
            raise AuditError(f"evidence limit {key} must be between 1 and {MAXIMUM_LIMITS[key]}")
    if value["max_artifact_bytes"] > value["max_total_artifact_bytes"]:
        raise AuditError("per-artifact limit exceeds total artifact limit")
    return dict(value)


def artifact_bytes(workspace, entry: dict) -> bytes:
    """Read both legacy inline artifacts and content-addressed binary files."""
    try:
        if "content_path" in entry:
            expected = f".agentagon/evidence/{entry['sha256']}.bin"
            if entry["content_path"] != expected or not re.fullmatch(
                r"[0-9a-f]{64}", entry["sha256"]
            ):
                raise AuditError("artifact content path is invalid")
            content = workspace.read_blob(expected)
        else:
            content = base64.b64decode(entry["content_base64"], validate=True)
        if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise AuditError("artifact checksum changed")
        return content
    except AuditError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditError("artifact content is invalid") from exc


def _compact(workspace, result: dict) -> dict:
    compact = copy.deepcopy(result)
    for entry in (compact.get("evidence") or {}).get("artifacts", []):
        content = artifact_bytes(workspace, entry)
        entry["content_path"] = workspace.blob(content, ".bin")
        entry.pop("content_base64", None)
    return compact


def read_result(workspace, relative: str) -> dict:
    """Check the result and any external bytes before relying on its evidence."""
    result = workspace.read_artifact(relative)
    for entry in (result.get("evidence") or {}).get("artifacts", []):
        if "content_path" in entry:
            artifact_bytes(workspace, entry)
    return result


def retain_result(workspace, attempt_dir, result: dict) -> str:
    """Keep one payload, then reclaim replaceable copies after durable collection.

    Hard links leave the runner's cache format and recovery path unchanged. Every
    cache update must use atomic replacement, never an in-place write.
    """
    compact = _compact(workspace, result)
    relative = workspace.artifact(compact)
    attempt_dir = workspace.checked(attempt_dir)
    collected = attempt_dir / "collected.json"
    if not result.get("finalized") or not collected.exists():
        return relative
    with (attempt_dir / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Another collector may have updated remote cleanup state in the meantime.
        if _compact(workspace, load_json(workspace.checked(collected))) != compact:
            return relative
        workspace.link_artifact(relative, collected)
        job = workspace.checked(attempt_dir / "job")
        if not job.is_dir():
            return relative
        with (job / "worker.lock").open("a") as worker_lock:
            try:
                fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return relative
            if (job / "result.json").exists():
                workspace.link_artifact(relative, job / "result.json")
            # Only engine-owned output copies are disposable. Keep ownership,
            # request and lock records so a late launch cannot repeat execution.
            disposable = [attempt_dir / "progress.json", job / "progress.json"]
            for path in job.iterdir():
                if re.fullmatch(r"[0-9]+\.(stdout|stderr)\.log|benchmark-[0-9]+\.json", path.name):
                    disposable.append(path)
            for path in disposable:
                workspace.checked(path).unlink(missing_ok=True)
    return relative
