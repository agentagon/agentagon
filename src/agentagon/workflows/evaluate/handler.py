"""Validate frozen evaluator identity and independent review."""

from agentagon.capabilities.experiments import preparation
from agentagon.core.records import AuditError


def validate(workspace, job, candidate, result):
    from agentagon.workflows.procedures import _require_review_session, _validate_limits

    record = preparation.load(workspace, candidate)
    state = record["state"]
    complete = state == "frozen"
    reuse = complete and candidate == job["options"].get("evaluation_id")
    if not reuse:
        _validate_limits(job, record)
    if complete:
        preparation.evaluator_identity(workspace, candidate)
        review = record.get("review") or {}
        if review.get("verdict") != "pass" or review.get("reviewer") == record.get("author"):
            raise AuditError("saved evaluator lacks a distinct passing independent review")
        if not reuse:
            _require_review_session(job, review.get("reviewer"), evaluation_id=candidate)
    return record, state, complete
