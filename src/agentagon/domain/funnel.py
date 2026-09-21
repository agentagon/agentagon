"""Project-local product funnel metrics derived from durable application facts.

Tasks, agents, result decisions, and delivery receipts remain authoritative.  This
module records only action-intent preparation and duplicate submissions because
those attempts otherwise leave no durable product record.  The retained journal
and public projection are deliberately bounded and are never sent by telemetry.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from agentagon.core.records import AuditError, load_json, now
from agentagon.storage.state import private_directory

JOURNAL_ID = "local"
MAX_INTENTS = 500
MAX_DUPLICATES = 500
MAX_PUBLIC_ROWS = 100
MAX_DELIVERY_RECEIPTS = 500
ABANDONED_AFTER_SECONDS = 24 * 60 * 60

DEFINITIONS = {
    "registration_to_first_understood_agent": {
        "start": "The project registration creation time.",
        "end": (
            "The earliest retained agent identity with a nonempty responsibility, using "
            "its responsibility-inference time, identity-review time, or creation time."
        ),
    },
    "action_intent_to_accepted_start": {
        "start": "The first successful preparation of an operation ID through the shared start contract.",
        "end": "Creation of the task bound to that operation ID.",
    },
    "prerequisite_abandonment": {
        "start": "A prepared action reports at least one blocking prerequisite.",
        "end": (
            "No task bound to that operation ID exists after 24 hours. Until then the "
            "intent is reported as unresolved, not abandoned."
        ),
    },
    "questions": {
        "start": "A managed task persists a question.",
        "end": "The answer is durably accepted, or the current question remains open.",
    },
    "duplicate_submissions": {
        "start": "A start request repeats an existing operation ID and identical request binding.",
        "end": "The existing task is returned without creating another execution.",
    },
    "result_to_selection": {
        "start": "The first verified Improvement record for a Fix or Optimize run is created.",
        "end": "A durable user result-decision receipt is created for that run.",
    },
    "selection_to_delivery": {
        "start": "A durable user decision selects a verified candidate.",
        "end": "A matching local delivery receipt is durably written.",
    },
}


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        left = datetime.fromisoformat(start.replace("Z", "+00:00"))
        right = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if left.tzinfo is None:
        left = left.replace(tzinfo=UTC)
    if right.tzinfo is None:
        right = right.replace(tzinfo=UTC)
    return round(max(0.0, (right - left).total_seconds()), 3)


def _task_id(project_id: str, operation: str) -> str:
    return "task_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]


def _journal(state, project_id: str) -> dict:
    return state.db.get_record(project_id, "funnel", JOURNAL_ID) or _empty_journal()


def _empty_journal() -> dict:
    return {
        "id": JOURNAL_ID,
        "schema_version": 1,
        "intents": {},
        "duplicates": {},
        "dropped_intents": 0,
        "dropped_duplicate_groups": 0,
    }


def _trim(values: dict, maximum: int, timestamp: str) -> tuple[dict, int]:
    if len(values) <= maximum:
        return values, 0
    ordered = sorted(
        values.items(),
        key=lambda item: (item[1].get(timestamp, ""), item[0]),
        reverse=True,
    )
    return dict(ordered[:maximum]), len(values) - maximum


def record_intent(state, project_id: str, prepared) -> bool:
    """Retain one bounded action intent; instrumentation never blocks preparation."""

    operation = getattr(prepared, "operation", None)
    if not operation:
        return False
    try:
        recorded_at = now()
        with state.db.transaction() as transaction:
            journal = transaction.get_record(project_id, "funnel", JOURNAL_ID) or _empty_journal()
            intents = dict(journal.get("intents") or {})
            previous = intents.get(operation, {})
            blockers = sorted(
                {
                    item["code"]
                    for item in prepared.prerequisites
                    if item.get("blocking") and isinstance(item.get("code"), str)
                }
            )
            encountered = sorted(set(previous.get("encountered_blockers", [])) | set(blockers))
            intents[operation] = {
                "operation_id": operation,
                "workflow": prepared.workflow,
                "prepared_at": previous.get("prepared_at", recorded_at),
                "latest_prepared_at": recorded_at,
                "preparation_count": int(previous.get("preparation_count", 0)) + 1,
                "latest_state": prepared.state,
                "latest_blockers": blockers,
                "encountered_blockers": encountered,
                "first_blocked_at": previous.get("first_blocked_at")
                or (recorded_at if blockers else None),
            }
            intents, dropped = _trim(intents, MAX_INTENTS, "latest_prepared_at")
            transaction.put_record(
                project_id,
                "funnel",
                JOURNAL_ID,
                {
                    **journal,
                    "schema_version": 1,
                    "intents": intents,
                    "dropped_intents": int(journal.get("dropped_intents", 0)) + dropped,
                },
                expected_revision=journal.get("revision", 0),
            )
        return True
    except (AuditError, OSError, TypeError, ValueError, AttributeError, KeyError):
        return False


def record_duplicate_submission(state, project_id: str, task: dict) -> bool:
    """Count an idempotent repeated start without storing request content."""

    task_id = task.get("id")
    if not isinstance(task_id, str):
        return False
    try:
        recorded_at = now()
        with state.db.transaction() as transaction:
            journal = transaction.get_record(project_id, "funnel", JOURNAL_ID) or _empty_journal()
            duplicates = dict(journal.get("duplicates") or {})
            previous = duplicates.get(task_id, {})
            duplicates[task_id] = {
                "task_id": task_id,
                "workflow": task.get("kind", "unknown"),
                "count": int(previous.get("count", 0)) + 1,
                "first_at": previous.get("first_at", recorded_at),
                "last_at": recorded_at,
            }
            duplicates, dropped = _trim(duplicates, MAX_DUPLICATES, "last_at")
            transaction.put_record(
                project_id,
                "funnel",
                JOURNAL_ID,
                {
                    **journal,
                    "schema_version": 1,
                    "duplicates": duplicates,
                    "dropped_duplicate_groups": int(journal.get("dropped_duplicate_groups", 0))
                    + dropped,
                },
                expected_revision=journal.get("revision", 0),
            )
        return True
    except (AuditError, OSError, TypeError, ValueError, AttributeError, KeyError):
        return False


def _understood_agent(agents: list[dict]) -> dict | None:
    candidates = []
    for agent in agents:
        if not str(agent.get("description", "")).strip():
            continue
        inference = agent.get("responsibility_inference") or {}
        review = agent.get("identity_review") or {}
        understood_at = inference.get("at") or review.get("at") or agent.get("created_at")
        if isinstance(understood_at, str):
            candidates.append(
                {
                    "agent_id": agent.get("id"),
                    "understood_at": understood_at,
                    "basis": (
                        "responsibility_inference"
                        if inference.get("at")
                        else "identity_review"
                        if review.get("at")
                        else "manual_identity"
                    ),
                }
            )
    return min(candidates, key=lambda item: item["understood_at"]) if candidates else None


def _delivery_receipts(state, project_id: str) -> tuple[list[dict], bool]:
    workspace = state.workspace(project_id)
    directory = private_directory(workspace, "deliveries")
    if not directory.exists() or directory.is_symlink():
        return [], False
    receipts = []
    truncated = False
    try:
        entries = directory.iterdir()
        for index, path in enumerate(entries):
            if index >= MAX_DELIVERY_RECEIPTS:
                truncated = True
                break
            if not re.fullmatch(r"delivery_[a-f0-9]{24}\.json", path.name):
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            value = load_json(workspace.checked(path))
            if value.get("delivery_id") != path.stem:
                continue
            receipts.append(
                {
                    "delivery_id": path.stem,
                    "kind": value.get("app_kind"),
                    "source_id": value.get("app_source_id"),
                    "state": value.get("state"),
                    "created_at": value.get("created_at") or value.get("updated_at"),
                    "user_decision_id": value.get("user_decision_id"),
                }
            )
    except (AuditError, OSError, TypeError, ValueError, AttributeError, KeyError):
        return receipts, True
    return receipts, truncated


def projection(state, project_id: str) -> dict:
    """Return a bounded local-only funnel projection suitable for JSON export."""

    project = state.project(project_id)
    journal = _journal(state, project_id)
    tasks = state.db.list_records(project_id, "tasks")
    task_by_id = {task["id"]: task for task in tasks}
    agents = state.db.list_records(project_id, "application_agents")
    first_agent = _understood_agent(agents)
    registration_at = project.get("created_at")

    current_at = now()
    action_rows = []
    blocker_counts: dict[str, int] = {}
    for intent in (journal.get("intents") or {}).values():
        task_id = _task_id(project_id, intent["operation_id"])
        task = task_by_id.get(task_id)
        blockers = list(intent.get("encountered_blockers") or [])
        for blocker in blockers:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        blocked_age = _seconds(intent.get("first_blocked_at"), current_at)
        abandoned = bool(
            not task
            and intent.get("latest_blockers")
            and blocked_age is not None
            and blocked_age >= ABANDONED_AFTER_SECONDS
        )
        action_rows.append(
            {
                "workflow": intent.get("workflow"),
                "prepared_at": intent.get("prepared_at"),
                "accepted_at": task.get("created_at") if task else None,
                "duration_seconds": _seconds(
                    intent.get("prepared_at"), task.get("created_at") if task else None
                ),
                "state": "accepted"
                if task
                else "abandoned"
                if abandoned
                else "blocked"
                if intent.get("latest_blockers")
                else "prepared",
                "blockers": intent.get("latest_blockers", []),
                "encountered_blockers": blockers,
                "preparation_count": intent.get("preparation_count", 1),
                "task_id": task_id if task else None,
            }
        )
    action_rows.sort(key=lambda item: item.get("prepared_at") or "", reverse=True)
    accepted_actions = [item for item in action_rows if item["state"] == "accepted"]

    question_rows = []
    total_questions = 0
    for task in tasks:
        answered = len(task.get("answers") or {})
        open_question = int(bool(task.get("question")))
        count = answered + open_question
        if not count:
            continue
        total_questions += count
        question_rows.append(
            {
                "task_id": task["id"],
                "workflow": task.get("kind"),
                "question_count": count,
                "repeated_count": max(0, count - 1),
                "open": bool(open_question),
            }
        )
    question_rows.sort(key=lambda item: item["question_count"], reverse=True)

    duplicates = list((journal.get("duplicates") or {}).values())
    duplicates.sort(key=lambda item: item.get("last_at", ""), reverse=True)

    improvements = state.db.list_records(project_id, "improvements")
    result_ready: dict[str, str] = {}
    for improvement in improvements:
        run_id, created_at = improvement.get("run_id"), improvement.get("created_at")
        if isinstance(run_id, str) and isinstance(created_at, str):
            result_ready[run_id] = min(result_ready.get(run_id, created_at), created_at)
    decisions = state.db.list_records(project_id, "result_decisions")
    deliveries, deliveries_truncated = _delivery_receipts(state, project_id)
    deliveries_by_decision: dict[str, list[dict]] = {}
    for receipt in deliveries:
        decision_id = receipt.get("user_decision_id")
        if isinstance(decision_id, str):
            deliveries_by_decision.setdefault(decision_id, []).append(receipt)
    result_rows = []
    for decision in decisions:
        run_id = decision.get("run_id")
        selected_at = decision.get("decided_at")
        matched = sorted(
            deliveries_by_decision.get(decision.get("id"), []),
            key=lambda item: item.get("created_at") or "",
        )
        delivered = matched[0] if matched else None
        result_rows.append(
            {
                "workflow": decision.get("workflow"),
                "run_id": run_id,
                "decision": decision.get("decision"),
                "result_ready_at": result_ready.get(run_id),
                "selected_at": selected_at,
                "result_to_selection_seconds": _seconds(result_ready.get(run_id), selected_at),
                "delivered_at": delivered.get("created_at") if delivered else None,
                "selection_to_delivery_seconds": _seconds(
                    selected_at, delivered.get("created_at") if delivered else None
                ),
                "delivery_state": delivered.get("state") if delivered else None,
            }
        )
    result_rows.sort(key=lambda item: item.get("selected_at") or "", reverse=True)

    return {
        "schema_version": 1,
        "generated_at": current_at,
        "privacy": {
            "storage": "project-local application metadata and project evidence",
            "automatic_external_transmission": False,
            "export": "manual JSON download only",
            "contains_project_path": False,
            "contains_source_or_trace_content": False,
        },
        "definitions": DEFINITIONS,
        "metrics": {
            "registration_to_first_understood_agent": {
                "registration_at": registration_at,
                "understood_at": first_agent.get("understood_at") if first_agent else None,
                "duration_seconds": _seconds(
                    registration_at, first_agent.get("understood_at") if first_agent else None
                ),
                "completed": bool(first_agent),
                "basis": first_agent.get("basis") if first_agent else None,
            },
            "action_intent_to_accepted_start": {
                "retained_intents": len(action_rows),
                "accepted": len(accepted_actions),
                "blocked": sum(item["state"] == "blocked" for item in action_rows),
                "abandoned": sum(item["state"] == "abandoned" for item in action_rows),
                "durations_seconds": [
                    item["duration_seconds"]
                    for item in accepted_actions
                    if item["duration_seconds"] is not None
                ],
            },
            "prerequisite_blocks": {
                "by_code": dict(sorted(blocker_counts.items())),
                "intent_count": sum(bool(item["encountered_blockers"]) for item in action_rows),
            },
            "prerequisite_abandonment": {
                "blocked_intents": sum(bool(item["encountered_blockers"]) for item in action_rows),
                "unresolved": sum(item["state"] == "blocked" for item in action_rows),
                "abandoned": sum(item["state"] == "abandoned" for item in action_rows),
                "after_seconds": ABANDONED_AFTER_SECONDS,
            },
            "questions": {
                "total": total_questions,
                "tasks": len(question_rows),
                "repeated": sum(item["repeated_count"] for item in question_rows),
                "open": sum(item["open"] for item in question_rows),
            },
            "duplicate_submissions": {
                "total": sum(int(item.get("count", 0)) for item in duplicates),
                "task_count": len(duplicates),
            },
            "result_selection": {
                "decisions": len(result_rows),
                "selected_candidates": sum(
                    item["decision"] == "select_candidate" for item in result_rows
                ),
                "delivered": sum(bool(item["delivered_at"]) for item in result_rows),
            },
        },
        "recent": {
            "action_intents": action_rows[:MAX_PUBLIC_ROWS],
            "questions": question_rows[:MAX_PUBLIC_ROWS],
            "duplicate_submissions": duplicates[:MAX_PUBLIC_ROWS],
            "result_decisions": result_rows[:MAX_PUBLIC_ROWS],
        },
        "limits": {
            "retained_action_intents": MAX_INTENTS,
            "retained_duplicate_task_groups": MAX_DUPLICATES,
            "public_rows_per_section": MAX_PUBLIC_ROWS,
            "delivery_receipts_scanned": MAX_DELIVERY_RECEIPTS,
            "abandonment_after_seconds": ABANDONED_AFTER_SECONDS,
            "dropped_action_intents": int(journal.get("dropped_intents", 0)),
            "dropped_duplicate_task_groups": int(journal.get("dropped_duplicate_groups", 0)),
            "delivery_receipts_truncated": deliveries_truncated,
        },
    }
