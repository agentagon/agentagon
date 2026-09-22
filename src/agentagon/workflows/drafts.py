"""Private, revision-bound workflow form drafts; saving never prepares or starts work."""

import copy
import re

from agentagon.core.records import AuditError, encoded
from agentagon.storage.state import identifier
from agentagon.workflows.registry import REGISTRY
from agentagon.workflows.runtime import operation_id

KIND = "workflow_drafts"
MAX_BYTES = 16 * 1024
FIELDS = {
    "workflow",
    "agentId",
    "goalId",
    "problem",
    "expected",
    "helpDefine",
    "traceId",
    "profile",
    "operationId",
    "operationBinding",
}
BINDING = re.compile(r"[0-9]{1,7}:[a-f0-9]{1,8}:[a-f0-9]{1,8}")


class DraftConflict(AuditError):
    status_code = 409

    def __init__(self, text, revision, *, code="draft_conflict"):
        super().__init__(text)
        self.details = {"code": code, "retryable": False, "current_revision": revision}


def _key(value):
    if not isinstance(value, str) or not BINDING.fullmatch(value):
        raise AuditError("draft key must be the workflow draft key's operation binding")
    return value


def _revision(payload, fields):
    if not isinstance(payload, dict) or set(payload) != fields:
        raise AuditError("provide only the expected draft revision and requested draft fields")
    revision = payload["expected_revision"]
    if type(revision) is not int or revision < 0:
        raise AuditError("expected_revision must be a nonnegative integer")
    if len(encoded(payload).encode()) > MAX_BYTES:
        raise AuditError("workflow draft exceeds the 16 KB limit")
    return revision


def _draft(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise AuditError(
            "unsupported workflow draft fields; raw traces and credentials are not draft fields"
        )
    if not isinstance(value.get("workflow"), str) or value["workflow"] not in REGISTRY:
        raise AuditError("choose a known workflow for the draft")
    result = copy.deepcopy(value)
    result["operationId"] = operation_id(value.get("operationId"))
    if not isinstance(value.get("operationBinding"), str) or not BINDING.fullmatch(
        value["operationBinding"]
    ):
        raise AuditError("provide the current request's operationBinding")
    for field, prefix in (("agentId", "agent"), ("goalId", "goal"), ("traceId", "snapshot")):
        if field in value and value[field] != "":
            identifier(value[field], prefix)
    for field in ("problem", "expected"):
        if field in value and not isinstance(value[field], str):
            raise AuditError(f"{field} must be text")
    if "helpDefine" in value and type(value["helpDefine"]) is not bool:
        raise AuditError("helpDefine must be a boolean")
    if "profile" in value and (
        not isinstance(value["profile"], str)
        or (value["profile"] and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value["profile"]))
    ):
        raise AuditError("profile must be empty or a valid execution profile name")
    return result


def _public(record):
    return {
        "draft": copy.deepcopy(record.get("draft")) if record else None,
        "revision": record["revision"] if record else 0,
    }


def inspect(state, project_id, key):
    return _public(state.db.get_record(project_id, KIND, _key(key)))


def save(state, project_id, key, payload):
    key = _key(key)
    expected = _revision(payload, {"expected_revision", "draft"})
    draft = _draft(payload["draft"])
    with state.db.transaction() as transaction:
        current = transaction.get_record(project_id, KIND, key)
        revision = current["revision"] if current else 0
        previous = current.get("draft") if current else None
        if previous == draft and expected <= revision:
            return _public(current)
        if revision != expected:
            raise DraftConflict(
                "This draft changed in another session. Reload it before saving.", revision
            )
        if previous and previous["operationId"] == draft["operationId"]:
            raise DraftConflict(
                "Changed workflow content requires a new operationId.",
                revision,
                code="draft_operation_reused",
            )
        saved = transaction.put_record(
            project_id, KIND, key, {"draft": draft}, expected_revision=expected
        )
    return _public(saved)


def clear(state, project_id, key, payload):
    key = _key(key)
    expected = _revision(payload, {"expected_revision"})
    with state.db.transaction() as transaction:
        current = transaction.get_record(project_id, KIND, key)
        revision = current["revision"] if current else 0
        if current and current.get("draft") is None and expected <= revision:
            return _public(current)
        if revision != expected:
            raise DraftConflict(
                "This draft changed in another session. Reload it before clearing.", revision
            )
        if not current:
            return _public(None)
        saved = transaction.put_record(
            project_id, KIND, key, {"draft": None}, expected_revision=expected
        )
    return _public(saved)
