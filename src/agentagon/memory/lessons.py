"""Validate explicit references to the frozen lessons supplied to a managed task."""

from agentagon.core.records import AuditError


def validate(job, result):
    supplied = {
        (entry["id"], entry["version"]): entry
        for group in job.get("improvement_memory", [])
        for entry in group["entries"]
    }
    lessons = result.get("lessons", [])
    if not isinstance(lessons, list) or len(lessons) > len(supplied):
        raise AuditError("lessons must reference the supplied memory snapshot")
    seen = set()
    for item in lessons:
        if not isinstance(item, dict) or set(item) != {"id", "version", "decision", "reason"}:
            raise AuditError("lesson requires id, version, decision and reason")
        if (
            not isinstance(item["id"], str)
            or type(item["version"]) is not int
            or not isinstance(item["decision"], str)
        ):
            raise AuditError("lesson reference is not in the frozen recall")
        key = (item["id"], item["version"])
        if key not in supplied or key in seen or item["decision"] not in {"used", "rejected"}:
            raise AuditError("lesson reference is not in the frozen recall")
        if not isinstance(item["reason"], str) or not 1 <= len(item["reason"]) <= 2000:
            raise AuditError("lesson decision requires a bounded reason")
        seen.add(key)
    return lessons


def learning(result):
    value = result.get("learning", {})
    if not isinstance(value, dict) or set(value) - {
        "hypothesis",
        "action",
        "result",
        "failed_approaches",
        "uncertainty",
    }:
        raise AuditError("unsupported learning fields")
    for key, text in value.items():
        if key == "failed_approaches":
            if (
                not isinstance(text, list)
                or len(text) > 20
                or any(not isinstance(s, str) or len(s) > 2000 for s in text)
            ):
                raise AuditError("failed approaches must be bounded text")
        elif not isinstance(text, str) or len(text) > 4000:
            raise AuditError("learning fields must be bounded text")
    return value
