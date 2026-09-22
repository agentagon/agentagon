"""Shared task accounting and recovery facts for every application interface."""

from __future__ import annotations

from datetime import datetime

from agentagon.core.records import AuditError, now

TIMING_FIELDS = ("active", "waiting", "queued", "paused", "offline", "unknown")
RESUMABLE = {"paused", "interrupted", "failed", "needs_input"}
FINISHED = {"completed", "completed_with_limits", "failed", "cancelled"}


class TaskControlError(AuditError):
    def __init__(self, message, code, *, action=None):
        super().__init__(message)
        self.details = {"code": code, "retryable": False}
        if action:
            self.details["resolution"] = {"action": action}


def seconds_between(start, end):
    if not start:
        return 0.0
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def accounting(job):
    """Project retained execution and live idle durations without mutating state."""
    saved = job.get("accounting") or {}
    values = {
        f"{field}_seconds": float(saved.get(f"{field}_seconds", 0)) for field in TIMING_FIELDS
    }
    phase = saved.get("phase")
    if phase in {"queued", "paused", "waiting"} and not job.get("attempt_started_at"):
        values[f"{phase}_seconds"] += seconds_between(saved.get("checkpoint_at"), now())
    values["active_seconds"] = float(job.get("elapsed_seconds", values["active_seconds"]))
    limit = float(job.get("options", {}).get("max_elapsed_seconds", 0))
    # Unreconciled execution reserves budget without pretending it was measured usage.
    values["remaining_seconds"] = max(
        0.0, limit - values["active_seconds"] - values["unknown_seconds"]
    )
    return values


def recovery(job, *, host_active=False):
    state = job.get("state")
    timing = accounting(job)
    remaining = timing["remaining_seconds"]
    allowed = state in RESUMABLE and not host_active and remaining > 0
    if state == "needs_input" and job.get("question"):
        allowed = False
    if host_active:
        reason = "host_stopping" if state in RESUMABLE else "task_active"
    elif state not in RESUMABLE:
        reason = "task_finished" if state in FINISHED else "task_active"
    elif remaining <= 0:
        reason = "execution_unreconciled" if timing["unknown_seconds"] else "budget_exhausted"
    elif state == "needs_input" and job.get("question"):
        reason = "answer_required"
    else:
        reason = None
    continuation = (
        job.get("kind") == "assess"
        and isinstance(job.get("start_intent"), dict)
        and (
            state in {"failed", "interrupted", "cancelled"}
            or (state in {"paused", "needs_input"} and remaining <= 0)
        )
        and not host_active
    )
    suggested = (
        "resume"
        if allowed
        else "answer"
        if reason == "answer_required"
        else "continue"
        if continuation
        else "review_result"
        if state in FINISHED
        else None
    )
    return {
        "resume_allowed": allowed,
        "continuation_allowed": continuation,
        "reason_code": reason,
        "remaining_seconds": remaining,
        "suggested_action": suggested,
    }


def available_actions(job, *, host_active=False):
    status = recovery(job, host_active=host_active)
    state = job.get("state")
    actions = []
    if state not in FINISHED:
        actions.extend(["cancel", "message"])
    if state in {"queued", "running", "needs_input"} or job.get("continue_after_guidance"):
        actions.append("pause")
    if (
        state == "needs_input"
        and job.get("question")
        and not job["question"].get("answered")
        and (host_active or status["remaining_seconds"] > 0)
    ):
        actions.append("answer")
    if status["resume_allowed"] and not job.get("goal_run_id"):
        actions.append("resume")
    if status["continuation_allowed"]:
        actions.append("continue")
    return actions


def message(text, *, role="system", kind="status", operation_id=None, question_id=None):
    value = {"role": role, "kind": kind, "text": text, "created_at": now()}
    if operation_id:
        value.update(operation_id=operation_id, delivery_state="pending")
    if question_id:
        value["question_id"] = question_id
    return value
