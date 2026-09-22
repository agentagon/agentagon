"""Small app-facing projections for the improvement workspace."""

import copy

from agentagon.capabilities.traces.providers import DEFAULT_ENDPOINTS
from agentagon.domain import tasks as task_facts
from agentagon.workflows import procedures as workflows
from agentagon.workflows.runtime import public_task

CONNECTOR_TYPES = {
    "braintrust": {
        "id": "braintrust",
        "name": "Braintrust",
        "capabilities": ["Traces", "Datasets", "Dataset publication"],
    },
    "langsmith": {
        "id": "langsmith",
        "name": "LangSmith",
        "capabilities": ["Traces", "Datasets"],
    },
    "langfuse": {
        "id": "langfuse",
        "name": "Langfuse",
        "capabilities": ["Traces", "Datasets"],
    },
}


def _display_events(events):
    """Keep host protocol noise out of dashboard and MCP task projections."""
    visible = []
    for event in events:
        if not isinstance(event, dict) or event.get("visibility") == "diagnostic":
            continue
        if event.get("type") in {"message", "session", "reflection_session", "tool_activity"}:
            continue
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        visible.append(copy.deepcopy(event))
    return visible


def workflow_definitions():
    return [
        {key: copy.deepcopy(value) for key, value in definition.items() if key != "references"}
        | {"workflow": kind}
        for kind, definition in workflows.REGISTRY.items()
    ]


def connector_types():
    return [
        {**copy.deepcopy(definition), "default_endpoint": DEFAULT_ENDPOINTS[identifier]}
        for identifier, definition in CONNECTOR_TYPES.items()
    ]


def agent_projection(agent):
    inference = copy.deepcopy(agent.get("responsibility_inference"))
    if not inference:
        inference = {
            "state": "unavailable",
            "reason": "No retained responsibility inference is available for this saved identity.",
        }
    evidence_kinds = {
        item.get("kind") for item in agent.get("evidence", []) if isinstance(item, dict)
    }
    origin = (
        "code"
        if "code" in evidence_kinds
        else "traces"
        if evidence_kinds & {"traces", "trace_metadata"}
        else "manual"
    )
    return {key: copy.deepcopy(value) for key, value in agent.items() if key != "description"} | {
        "origin": origin,
        "role": agent.get("role", "unknown"),
        "responsibility": agent.get("description", ""),
        "responsibility_inference": inference,
    }


def goal_projection(goal_record):
    return {
        "id": goal_record["id"],
        "project_id": goal_record["project_id"],
        "agent_id": goal_record["agent_id"],
        "name": goal_record["name"],
        "category": goal_record["category"],
        "objective": goal_record["objective"],
        "ideal_behavior": goal_record.get("ideal_behavior"),
        "source": copy.deepcopy(goal_record.get("source", {})),
        "measurement": copy.deepcopy(goal_record.get("measurement")),
        "state": goal_record.get("state", "active"),
        "version": goal_record.get("version"),
        "revision": goal_record.get("revision"),
        "created_at": goal_record.get("created_at"),
        "updated_at": goal_record.get("updated_at"),
    }


def task_summary(job, agents=None, goals=None):
    agents = agents or {}
    goals = goals or {}
    state = job.get("state", "unknown")
    agent_id = job.get("application_agent_id")
    goal_id = job.get("goal_id")
    host_active = bool(job.get("attempt_started_at"))
    workflow_name = workflows.REGISTRY.get(job.get("kind"), {}).get("name")
    title = workflow_name if job.get("goal_run_id") else job.get("goal") or workflow_name
    return {
        "id": job["id"],
        "project_id": job["project_id"],
        "workflow": job.get("kind"),
        "workflow_version": job.get("workflow_version", 1),
        "agent_id": agent_id,
        "agent_name": agents.get(agent_id, {}).get("name"),
        "goal_id": goal_id,
        "goal_run_id": job.get("goal_run_id"),
        "goal_name": goals.get(goal_id, {}).get("name"),
        "title": title,
        "state": state,
        "needs_attention": state in {"needs_input", "interrupted", "failed"}
        or bool(job.get("question"))
        or (bool(job.get("memory_note")) and int(job.get("memory_retry_count", 0)) >= 3),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "recovery": copy.deepcopy(job.get("recovery"))
        or task_facts.recovery(job, host_active=host_active),
        "available_actions": copy.deepcopy(job.get("available_actions"))
        if "available_actions" in job
        else task_facts.available_actions(job, host_active=host_active),
        "accounting": task_facts.accounting(job),
    }


def task_detail(job, agents=None, goals=None):
    result = task_summary(job, agents, goals)
    public = job if "recovery" in job else public_task(job)
    result.update(
        conversation=copy.deepcopy(public.get("messages", [])),
        events=_display_events(public.get("events", [])),
        question=copy.deepcopy(public.get("question")),
        progress=copy.deepcopy(public.get("progress")),
        result=copy.deepcopy(public.get("result")),
        workflow_ids={
            key: value
            for key, value in public.get("workflow_ids", {}).items()
            if key in {"audit_id", "evaluation_id", "baseline_id", "run_id", "patch_id"}
            and isinstance(value, str)
        },
        next_action=public.get("next_action"),
        recovery=public["recovery"],
        accounting=public["accounting"],
        available_actions=public["available_actions"],
        continuation_of=public.get("continuation_of"),
        revision=public.get("revision"),
        memory_recording=(
            {
                "state": (
                    "needs_attention"
                    if int(public.get("memory_retry_count", 0)) >= 3
                    else "retrying"
                ),
                "message": public["memory_note"],
                "automatic_retry": int(public.get("memory_retry_count", 0)) < 3,
                "attempts": int(public.get("memory_retry_count", 0)),
                "last_attempt_at": public.get("memory_retry_at"),
            }
            if public.get("memory_note")
            else None
        ),
    )
    return result
