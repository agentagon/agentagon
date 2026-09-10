"""Canonical experiment state belongs to the originating audit checkout."""

import fcntl
import os
import re
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.storage.workspace import Workspace

RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")


def run_dir(workspace: Workspace, run_id: str):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise AuditError("invalid fix run ID")
    return workspace.checked(workspace.state / "runs" / run_id)


def load_run(workspace: Workspace, run_id: str) -> dict:
    filename = run_dir(workspace, run_id) / "state.json"
    if not filename.exists():
        raise AuditError("fix run not found in this checkout")
    data = load_json(filename)
    if (
        data.get("version") != 1
        or data.get("run_id") != run_id
        or data.get("origin") != str(workspace.root)
    ):
        raise AuditError("incompatible fix run or origin checkout")
    if (
        digest(data["spec"]) != data["evaluation_digest"]
        or digest(data["profile"]) != data["profile_digest"]
    ):
        raise AuditError("frozen evaluation or runner profile changed")
    return data


def list_runs(workspace: Workspace) -> list[dict]:
    if not workspace.state.exists():
        return []
    return sorted(
        [
            load_run(workspace, p.parent.name)
            for p in (workspace.state / "runs").glob("*/state.json")
        ],
        key=lambda value: (value["created_at"], value["run_id"]),
        reverse=True,
    )


@contextmanager
def locked(workspace: Workspace, run_id: str):
    directory = run_dir(workspace, run_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = directory / "state.lock"
    fd = os.open(filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def save_run(workspace: Workspace, data: dict) -> None:
    from agentagon.reporting import build_fix_report, render_fix_markdown

    data["revision"] = data.get("revision", 0) + 1
    data["updated_at"] = now()
    directory = run_dir(workspace, data["run_id"])
    workspace.write(directory / "state.json", data)
    report = build_fix_report(workspace, data)
    workspace.write(directory / "report.json", report)
    workspace.write_bytes(directory / "report.md", render_fix_markdown(report).encode())
