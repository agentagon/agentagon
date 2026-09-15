"""Bounded dashboard operations shared with the CLI's validated lifecycle APIs."""

import fcntl
import os
import uuid

from agentagon.core.records import AuditError, digest, load_json
from agentagon.storage.config import KEYS, Config, _value


def settings_projection(workspace):
    settings = Config().effective(workspace.root)
    # Profiles can carry commands and environment values; they are never served.
    displayed = {section: settings[section] for section in ("traces", "intelligence")}
    return {"settings": displayed, "revision": digest(displayed)}


def update_settings(workspace, payload):
    """Persist project settings with a durable retry receipt and optimistic revision."""
    allowed = {"version", "operation_id", "expected_revision", "values", "unset"}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("version") != 1:
        raise AuditError("unsupported settings request")
    try:
        operation_id = str(uuid.UUID(payload["operation_id"]))
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise AuditError("settings require a UUID operation_id") from exc
    values, unset = payload.get("values", {}), payload.get("unset", [])
    if (
        not isinstance(values, dict)
        or not isinstance(unset, list)
        or not all(isinstance(k, str) for k in unset)
        or not all(isinstance(k, str) for k in values)
    ):
        raise AuditError("settings require values and an unset list")
    if any(
        key not in KEYS
        or not key.startswith(("traces.", "intelligence."))
        or key == "intelligence.access_presented"
        for key in (*values, *unset)
    ):
        raise AuditError(
            "dashboard settings are limited to project trace and intelligence configuration"
        )
    values = {key: _value(key, value) for key, value in values.items()}
    workspace.require_initialized()
    directory = workspace.checked(workspace.state / "dashboard-operations")
    directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory / "settings.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        filename = workspace.checked(directory / f"{operation_id}.json")
        binding = digest(payload)
        previous = None
        if filename.exists():
            previous = load_json(filename)
            if previous["binding"] != binding:
                raise AuditError("operation_id already belongs to another settings request")
            if previous["state"] == "complete":
                return settings_projection(workspace)
        current = settings_projection(workspace)
        if previous and previous["state"] == "pending":
            project = Config().read()["projects"].get(str(workspace.root), {})
            # Recover a lost acknowledgement after Config committed its atomic
            # write, without reverting any later changes to different settings.
            applied = all(
                project.get(key.split(".")[0], {}).get(key.split(".")[1]) == value
                for key, value in values.items()
            ) and all(key.split(".")[1] not in project.get(key.split(".")[0], {}) for key in unset)
            if applied:
                workspace.write(filename, {"binding": binding, "state": "complete"})
                return current
        if payload.get("expected_revision") != current["revision"]:
            raise AuditError("settings changed; refresh before applying your changes")
        # Config validates keys, endpoints and credential references before writing.
        workspace.write(filename, {"binding": binding, "state": "pending"})
        Config().update("project", workspace.root, values, tuple(unset))
        workspace.write(filename, {"binding": binding, "state": "complete"})
        return settings_projection(workspace)
