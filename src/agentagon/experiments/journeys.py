"""Versioned accepted intent, shared by onboarding, eval, audit, and fix."""

import copy
import fcntl
import os
import re
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest, identifier, load_json, now, validate_record
from agentagon.experiments import scoring
from agentagon.storage.workspace import Workspace


def directory(workspace: Workspace, kind: str, record_id: str):
    prefix = {"intents": "intent", "baselines": "baseline"}[kind]
    if not isinstance(record_id, str) or not re.fullmatch(prefix + r"_[0-9a-f]{24}", record_id):
        raise AuditError(f"invalid {prefix} ID")
    return workspace.checked(workspace.state / kind / record_id)


@contextmanager
def locked(workspace: Workspace, kind: str, record_id: str):
    root = directory(workspace, kind, record_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield root


def save(workspace: Workspace, definition: dict, evaluation_id: str | None = None) -> dict:
    workspace.require_initialized()
    validate_record("journey-intent", definition)
    definition = copy.deepcopy(definition)
    definition["scoring"] = scoring.validate(definition["scoring"])
    discovery = definition["discovery"]
    if discovery["status"] == "usable" and not discovery["paths"]:
        raise AuditError("reusing evals requires discovered repository paths")
    from agentagon.experiments.spec import path

    discovery["paths"] = [path(p) for p in discovery["paths"]]
    if evaluation_id:
        from agentagon.experiments import preparation

        data = preparation.load(workspace, evaluation_id)
        if (
            data["state"] != "frozen"
            or data["package"]["spec"].get("scoring") != definition["scoring"]
        ):
            raise AuditError("intent must match the reviewed frozen evaluator scoring definition")
        if discovery["status"] != "usable" and not discovery["creation_authorized"]:
            raise AuditError(
                "missing or unusable eval creation requires recorded user authorization"
            )
    intent_id = identifier("intent", definition, evaluation_id)
    with locked(workspace, "intents", intent_id) as root:
        if (root / "state.json").exists():
            return load(workspace, intent_id)
        data = {
            "version": 1,
            "intent_id": intent_id,
            "created_at": now(),
            "definition": definition,
            "definition_digest": digest(definition),
            "evaluation_id": evaluation_id,
            "origin": str(workspace.root),
        }
        workspace.write(root / "state.json", data)
    return data


def load(workspace: Workspace, intent_id: str) -> dict:
    file = directory(workspace, "intents", intent_id) / "state.json"
    if not file.exists():
        raise AuditError("saved intent not found in this checkout")
    data = load_json(file)
    if (
        data.get("origin") != str(workspace.root)
        or data.get("version") != 1
        or data.get("intent_id") != intent_id
        or digest(data["definition"]) != data.get("definition_digest")
        or identifier("intent", data["definition"], data.get("evaluation_id")) != intent_id
    ):
        raise AuditError("saved intent identity changed")
    validate_record("journey-intent", data["definition"])
    return data


def status(workspace: Workspace, intent_id: str | None = None) -> dict:
    if intent_id:
        return load(workspace, intent_id)
    records = sorted(
        [
            load(workspace, p.parent.name)
            for p in (workspace.state / "intents").glob("*/state.json")
        ],
        key=lambda r: (r["created_at"], r["intent_id"]),
        reverse=True,
    )
    return {"intents": records, "latest_intent_id": records[0]["intent_id"] if records else None}


def invitation(workspace: Workspace) -> dict:
    """Consume a non-blocking invitation once, after a successful journey."""
    from agentagon.experiments.baselines import list_baselines
    from agentagon.experiments.store import list_runs

    with workspace.locked():
        file = workspace.state / "star-invitation.json"
        if file.exists():
            return {"show": False}
        success = any(b["state"] == "completed" for b in list_baselines(workspace)) or any(
            r.get("selected") for r in list_runs(workspace)
        )
        if not success:
            return {"show": False}
        workspace.write(file, {"offered_at": now()})
    return {
        "show": True,
        "message": "If Agentagon helped, consider starring the project.",
        "url": "https://github.com/agentagon/agentagon",
    }
