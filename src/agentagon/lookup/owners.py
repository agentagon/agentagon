"""Receipt ownership and locking for the three Intelligence workflows."""

import fcntl
import os
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest
from agentagon.experiments import preparation, store
from agentagon.storage.workspace import Workspace

WORKFLOWS = {"audit", "eval", "fix"}


def validate_workflow(workflow: str) -> None:
    if workflow not in WORKFLOWS:
        raise AuditError("lookup workflow must be audit, eval or fix")


def load(workspace: Workspace, workflow: str, owner_id: str) -> dict:
    validate_workflow(workflow)
    if workflow == "audit":
        return workspace.read_audit(owner_id)
    if workflow == "eval":
        return preparation.load(workspace, owner_id)
    return store.load_run(workspace, owner_id)


@contextmanager
def locked(workspace: Workspace, workflow: str, owner_id: str):
    validate_workflow(workflow)
    if workflow == "audit":
        with workspace.locked():
            yield
        return
    workspace.require_initialized()
    # Unlike workflow start, a lookup must not create a missing owner directory.
    load(workspace, workflow, owner_id)
    directory = (
        preparation.directory(workspace, owner_id)
        if workflow == "eval"
        else store.run_dir(workspace, owner_id)
    )
    fd = os.open(directory / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditError(
                f"{workflow} lookup owner is busy; retry after its active operation finishes"
            ) from exc
        yield


def save(workspace: Workspace, workflow: str, data: dict) -> None:
    if workflow == "audit":
        workspace.save_audit(data)
    elif workflow == "eval":
        preparation._save(workspace, data)
    else:
        store.save_run(workspace, data)


def binding(workflow: str, data: dict):
    if workflow == "audit":
        return data["generation"]
    # Receipt appends alone do not invalidate another in-flight lookup.
    return digest(
        {
            key: value
            for key, value in data.items()
            if key not in {"intelligence", "updated_at", "revision"}
        }
    )


def usage_path(workspace: Workspace, workflow: str, owner_id: str):
    if workflow == "audit":
        return workspace.audit_path(owner_id).with_name("usage.json")
    if workflow == "eval":
        return preparation.directory(workspace, owner_id) / "usage.json"
    return store.run_dir(workspace, owner_id) / "usage.json"
