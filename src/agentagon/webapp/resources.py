"""Small app-facing projections for the improvement workspace."""

import copy

from agentagon.webapp import workflows
from agentagon.webapp.jobs import ACTIVE, public_job
from agentagon.webapp.providers import DEFAULT_ENDPOINTS

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


def skill_definitions():
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
    return {key: copy.deepcopy(value) for key, value in agent.items() if key != "description"} | {
        "responsibility": agent.get("description", "")
    }


def goal_projection(focus):
    return {
        "id": focus["id"],
        "project_id": focus["project_id"],
        "agent_id": focus["agent_id"],
        "name": focus["name"],
        "category": focus["category"],
        "objective": focus["goal"],
        "ideal_behavior": focus.get("target"),
        "source": copy.deepcopy(focus.get("source", {})),
        "measurement": copy.deepcopy(focus.get("measurement")),
        "state": focus.get("state", "active"),
        "version": focus.get("version"),
        "revision": focus.get("revision"),
        "created_at": focus.get("created_at"),
        "updated_at": focus.get("updated_at"),
    }


def task_summary(job, agents=None, goals=None):
    agents = agents or {}
    goals = goals or {}
    state = job.get("state", "unknown")
    agent_id = job.get("application_agent_id")
    goal_id = job.get("focus_id")
    return {
        "id": job["id"],
        "project_id": job["project_id"],
        "workflow": job.get("kind"),
        "workflow_version": job.get("workflow_version", 1),
        "agent_id": agent_id,
        "agent_name": agents.get(agent_id, {}).get("name"),
        "goal_id": goal_id,
        "goal_name": goals.get(goal_id, {}).get("name"),
        "title": job.get("goal") or workflows.REGISTRY.get(job.get("kind"), {}).get("name"),
        "state": state,
        "needs_attention": state in {"needs_input", "interrupted", "failed"}
        or bool(job.get("question")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def task_detail(job, agents=None, goals=None):
    result = task_summary(job, agents, goals)
    public = public_job(job)
    messages = [
        copy.deepcopy(message)
        if isinstance(message, dict)
        else {"role": "assistant", "text": str(message)}
        for message in public.get("messages", [])
    ]
    result.update(
        conversation=messages,
        events=copy.deepcopy(public.get("events", [])),
        question=copy.deepcopy(public.get("question")),
        progress=copy.deepcopy(public.get("progress")),
        result=copy.deepcopy(public.get("result")),
        next_action=public.get("next_action"),
        can_resume=public.get("state") == "interrupted",
        can_cancel=public.get("state") in ACTIVE | {"interrupted"},
        revision=public.get("revision"),
    )
    return result
