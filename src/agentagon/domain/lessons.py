"""Lesson projections and immutable corrections over retained memory evidence."""

import copy

from agentagon.core.records import AuditError
from agentagon.memory.store import FolderStore
from agentagon.workflows import procedures as workflows

MAX_LESSONS = 500
MAX_CONSIDERATIONS = 100


def _group_projection(group, project_id):
    return {
        "id": group["id"],
        "name": group["name"],
        "purpose": group["purpose"],
        "scope": "agent" if group["agent_ids"] else "project",
        "agent_ids": copy.deepcopy(group["agent_ids"]),
        "writable": project_id in group["write_project_ids"],
    }


def _task_title(task):
    return task.get("goal") or workflows.REGISTRY.get(task.get("kind"), {}).get(
        "name", "Agentagon task"
    )


def _source_projection(application, project_id, entry, tasks):
    source_id = entry["key"]
    if source_id.startswith("task_"):
        task = tasks.get(source_id)
        if task is None:
            return {"type": "task", "id": source_id, "available": False}
        result = task.get("result") or {}
        learning = result.get("learning") if isinstance(result.get("learning"), dict) else {}
        return {
            "type": "task",
            "id": source_id,
            "available": True,
            "title": _task_title(task),
            "workflow": task.get("kind"),
            "state": task.get("state"),
            "agent_id": task.get("application_agent_id"),
            "outcome": result.get("summary") or task.get("next_action"),
            "learning": {
                key: copy.deepcopy(learning[key])
                for key in (
                    "hypothesis",
                    "action",
                    "result",
                    "failed_approaches",
                    "uncertainty",
                )
                if key in learning
            },
        }
    if source_id.startswith("observation_"):
        observation = application.state.db.get_record(project_id, "observations", source_id)
        if observation is None:
            return {"type": "observation", "id": source_id, "available": False}
        return {
            "type": "observation",
            "id": source_id,
            "available": True,
            "agent_id": observation.get("agent_id"),
            "outcome": observation.get("outcome") or observation.get("classification"),
            "window": copy.deepcopy(observation.get("window")),
        }
    return {"type": "record", "id": source_id, "available": True}


def _considerations(tasks):
    by_entry = {}
    for task in tasks.values():
        cited = {
            (item.get("id"), item.get("version")): item
            for item in (task.get("result") or {}).get("lessons", [])
            if isinstance(item, dict)
        }
        for group in task.get("improvement_memory", []):
            if not isinstance(group, dict):
                continue
            for entry in group.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                key = (entry.get("id"), entry.get("version"))
                if not isinstance(key[0], str) or type(key[1]) is not int:
                    continue
                citation = cited.get(key)
                item = {
                    "task_id": task["id"],
                    "title": _task_title(task),
                    "workflow": task.get("kind"),
                    "state": task.get("state"),
                    "agent_id": task.get("application_agent_id"),
                    "updated_at": task.get("updated_at") or task.get("created_at"),
                    "supplied": True,
                    "decision": citation.get("decision") if citation else None,
                    "reason": citation.get("reason") if citation else None,
                }
                by_entry.setdefault(key, []).append(item)
    for values in by_entry.values():
        values.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        del values[MAX_CONSIDERATIONS:]
    return by_entry


def _entry_projection(application, project_id, group, entry, tasks, considerations):
    uses = considerations.get((entry["id"], entry["version"]), [])
    return {
        "id": entry["id"],
        "group_id": group["id"],
        "key": entry["key"],
        "statement": entry["text"],
        "evidence": copy.deepcopy(entry["evidence"]),
        "uncertainty": entry.get("uncertainty", ""),
        "status": entry.get("status", "active"),
        "revision_kind": entry.get("revision_kind", "recorded"),
        "revision_note": entry.get("revision_note", ""),
        "version": entry["version"],
        "created_at": entry["created_at"],
        "group": _group_projection(group, project_id),
        "source": _source_projection(application, project_id, entry, tasks),
        "considerations": copy.deepcopy(uses),
        "supplied_count": len(uses),
        "used_count": sum(item.get("decision") == "used" for item in uses),
        "rejected_count": sum(item.get("decision") == "rejected" for item in uses),
        "last_considered_at": next(
            (item.get("updated_at") for item in uses if item.get("updated_at")), None
        ),
    }


def list_lessons(application, project_id, agent_id=None):
    if agent_id:
        application.catalog.agent(project_id, agent_id)
    groups = [
        group
        for group in application.memory.list(project_id, agent_id, "improvement")
        if not group["agent_ids"] or agent_id in group["agent_ids"]
    ]
    tasks = {task["id"]: task for task in application.runtime.list(project_id)}
    considerations = _considerations(tasks)
    lessons = []
    for group in groups:
        lessons.extend(
            _entry_projection(application, project_id, group, entry, tasks, considerations)
            for entry in FolderStore(group).entries()
        )
    lessons.sort(
        key=lambda item: item.get("last_considered_at") or item["created_at"], reverse=True
    )
    truncated = len(lessons) > MAX_LESSONS
    return {
        "agent_id": agent_id,
        "groups": [_group_projection(group, project_id) for group in groups],
        "lessons": lessons[:MAX_LESSONS],
        "truncated": truncated,
    }


def lesson_detail(application, project_id, group_id, entry_id, agent_id=None):
    if agent_id:
        application.catalog.agent(project_id, agent_id)
    history = application.memory.history(project_id, group_id, entry_id, agent_id)
    group = history["group"]
    if group["purpose"] != "improvement":
        raise AuditError("this memory entry is not an improvement lesson")
    tasks = {task["id"]: task for task in application.runtime.list(project_id)}
    considerations = _considerations(tasks)
    versions = [
        _entry_projection(application, project_id, group, entry, tasks, considerations)
        for entry in reversed(history["versions"])
    ]
    return {
        "agent_id": agent_id,
        "group": _group_projection(group, project_id),
        "latest": versions[0],
        "versions": versions,
    }


def correct_lesson(application, project_id, group_id, entry_id, payload):
    """Append a correction while preserving the lesson's retained evidence."""
    if not isinstance(payload, dict):
        raise AuditError("lesson correction must be an object")
    action = payload.get("action")
    agent_id = payload.get("agent_id")
    expected_version = payload.get("expected_version")
    common = {"action", "agent_id", "expected_version"}
    action_fields = {
        "add_note": {"note"},
        "mark_outdated": {"reason"},
        "revise": {"statement", "uncertainty", "reason"},
    }
    if action not in action_fields or set(payload) - (common | action_fields[action]):
        raise AuditError("unsupported lesson correction fields")
    if agent_id is not None and not isinstance(agent_id, str):
        raise AuditError("lesson correction agent must be an identifier")
    if agent_id is not None:
        application.catalog.agent(project_id, agent_id)
    if type(expected_version) is not int or expected_version < 1:
        raise AuditError("lesson correction requires the version currently shown")
    history = application.memory.history(project_id, group_id, entry_id, agent_id)
    group = history["group"]
    if group["purpose"] != "improvement":
        raise AuditError("this memory entry is not an improvement lesson")
    latest = history["versions"][-1]
    if latest["version"] != expected_version:
        raise AuditError("memory entry changed; reload before updating")
    base = {
        "key": latest["key"],
        "text": latest["text"],
        "evidence": copy.deepcopy(latest["evidence"]),
        "uncertainty": latest.get("uncertainty", ""),
        "status": latest.get("status", "active"),
        "expected_version": expected_version,
    }
    if action == "add_note":
        note = _correction_text(payload.get("note"), "note")
        correction = {**base, "revision_kind": "note", "revision_note": note}
    elif action == "mark_outdated":
        if base["status"] == "outdated":
            raise AuditError("lesson is already outdated")
        reason = _correction_text(payload.get("reason"), "reason")
        correction = {
            **base,
            "status": "outdated",
            "revision_kind": "outdated",
            "revision_note": reason,
        }
    else:
        statement = _correction_text(payload.get("statement"), "revised lesson", limit=28000)
        uncertainty = payload.get("uncertainty", "")
        if not isinstance(uncertainty, str) or len(uncertainty) > 4000:
            raise AuditError("lesson uncertainty must be bounded text")
        reason = _correction_text(payload.get("reason"), "revision reason")
        correction = {
            **base,
            "text": statement,
            "uncertainty": uncertainty.strip(),
            "status": "active",
            "revision_kind": "revised",
            "revision_note": reason,
        }
    application.memory.record(project_id, group_id, correction, agent_id)
    return lesson_detail(application, project_id, group_id, entry_id, agent_id)


def _correction_text(value, name, *, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AuditError(f"lesson {name} must be bounded text")
    return value.strip()
