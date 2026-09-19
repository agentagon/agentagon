"""Pin target-agent memory as an ordinary immutable evaluation input."""

import copy
import os
from pathlib import Path

from agentagon.core.records import AuditError, load_json

MEMORY_INPUT = "agentagon-private/memory.json"


def freeze_spec(workspace, spec):
    value = os.environ.get("AGENTAGON_MEMORY_SNAPSHOT")
    if not value:
        return spec
    source = Path(value)
    if not source.is_absolute():
        source = workspace.root / source
    relative = str(source.relative_to(workspace.root))
    snapshot = workspace.read_artifact(relative)
    if snapshot.get("purpose") != "agent" or not snapshot.get("groups"):
        return spec
    result = copy.deepcopy(spec)
    inputs = result.setdefault("inputs", [])
    existing = next((i for i in inputs if i["path"] == MEMORY_INPUT), None)
    if existing:
        if workspace.read_blob(existing["source"]) != workspace.read_blob(relative):
            raise AuditError("target-agent memory changed; prepare a new evaluator version")
    else:
        inputs.append({"source": relative, "path": MEMORY_INPUT})
    return result


def verify_snapshot(workspace, job, record):
    snapshot = job.get("memory_snapshot")
    if not snapshot or not workspace.read_artifact(snapshot["path"]).get("groups"):
        return
    frozen = next((f for f in record["frozen"] if f["path"] == MEMORY_INPUT), None)
    if frozen is None or frozen.get("digest") != Path(snapshot["path"]).stem:
        raise AuditError("evaluation omitted or changed the task's frozen memory snapshot")


def recalled_snapshot(path, project_id, group_id, query, agent_id, limit=10):
    """MCP children of evaluated agents read only their staged snapshot."""
    import re

    from agentagon.core.records import encoded

    file = Path(path)
    if file.is_symlink() or file.stat().st_size > 4_000_000:
        raise AuditError("invalid evaluation memory snapshot")
    snapshot = load_json(file)
    if (
        snapshot.get("project_id") != project_id
        or snapshot.get("agent_id") != agent_id
        or snapshot.get("purpose") != "agent"
    ):
        raise AuditError("memory snapshot belongs to another agent or project")
    group = next((g for g in snapshot["groups"] if g["group_id"] == group_id), None)
    if group is None:
        raise AuditError("memory group is absent from the frozen evaluation")
    if (
        not isinstance(query, str)
        or len(query) > 4000
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise AuditError("invalid memory query or limit")
    terms = set(re.findall(r"\w+", query.casefold()))
    ranked = sorted(
        group["entries"],
        key=lambda e: (
            -len(terms & set(re.findall(r"\w+", (e["key"] + " " + e["text"]).casefold()))),
            e["id"],
        ),
    )
    entries, size = [], 0
    for entry in ranked:
        if terms and not terms & set(
            re.findall(r"\w+", (entry["key"] + " " + entry["text"]).casefold())
        ):
            continue
        size += len(encoded(entry).encode())
        if len(entries) == limit or size > 64000:
            break
        entries.append(entry)
    return {"group_id": group_id, "entries": entries, "frozen": True, "advisory": True}
