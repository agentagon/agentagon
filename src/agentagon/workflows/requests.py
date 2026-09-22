"""One typed start contract shared by HTTP and MCP."""

from agentagon.core.records import AuditError, digest
from agentagon.domain.issues import assign_agent, link_task
from agentagon.workflows.operations.preparation import (
    agent_review_scope,
    prepare,
    prepare_workflow_start,
)
from agentagon.workflows.registry import REGISTRY
from agentagon.workflows.runtime import operation_id


def submit(application, project_id, payload, *, scheduled=False):
    if not isinstance(payload, dict):
        raise AuditError("unsupported task fields")
    operation = operation_id(payload.get("operation_id"))
    binding = digest(payload)
    existing = application.runtime.existing_submission(project_id, operation, binding)
    if existing:
        if not scheduled:
            from agentagon.domain.funnel import record_duplicate_submission

            record_duplicate_submission(application.state, project_id, existing)
        return public_start(project_id, existing)
    prepared = prepare(
        application,
        project_id,
        payload,
        scheduled=scheduled,
        require_operation=True,
    )
    if not scheduled:
        from agentagon.domain.funnel import record_intent

        record_intent(application.state, project_id, prepared)
    prepared.require_ready()
    workflow = prepared.workflow
    if workflow in {"assess", "observe"}:
        normalized = prepared.normalized_intent
        normalized["options"] = {
            key: value
            for key, value in prepared.options.items()
            if key not in {"max_trials", "max_elapsed_seconds", "trial_timeout_seconds"}
        }
        return application.production.submit(project_id, normalized, binding, scheduled)
    source = prepared.source
    options = prepared.options
    workspace = prepared.workspace
    issue = prepared.issue
    agent = prepared.agent
    agent_id = agent["id"] if agent else None
    goal_id = prepared.goal["id"] if prepared.goal else None
    objective = prepared.objective
    body = {
        "operation_id": operation,
        "kind": workflow,
        "workflow_version": REGISTRY[workflow]["version"],
        "application_agent_id": agent_id,
        "goal_id": goal_id,
        "agent": payload.get("assistant"),
        "options": options,
        "input": source,
    }
    if "model" in payload:
        body["model"] = payload["model"]
    if workflow in {"discover", "fix"}:
        body["goal"] = objective
        if workflow == "fix":
            options["code_scopes"] = agent["code_scopes"]
            if prepared.suite and prepared.suite["members"]:
                # A focused repair has no goal row; its suite still preserves all accepted goals.
                suite = {**prepared.suite, "goal_id": None}
                suite["digest"] = digest(
                    {key: value for key, value in suite.items() if key not in {"digest", "missing"}}
                )
                options["suite_manifest"] = suite
        with application.lock:
            if workflow == "discover" and agent and agent["status"] == "suggested":
                current_agent = application.catalog.agent(project_id, agent_id)
                if current_agent["status"] != "suggested" or options.get(
                    "agent_review_scope"
                ) != agent_review_scope(current_agent):
                    raise AuditError("suggested identity changed; review it again")
            groups = application.memory.improvement_groups(project_id, agent_id)
            body["improvement_memory"] = [
                application.memory.recall(project_id, g["id"], objective, agent_id) for g in groups
            ]
            body["memory_snapshot"] = application.memory.snapshot(project_id, agent_id)
            if issue:
                assign_agent(workspace, issue["issue_id"], agent_id)
            task = application.runtime.submit(project_id, body, request_binding=binding)
    else:
        # Goal workflows use their accepted measurement plans and the same runtime.
        task = application.submit_job(
            project_id,
            body,
            request_binding=binding,
            suggested_read_only_scope=options.get("agent_review_scope"),
        )
    if issue:
        link_task(workspace, issue["issue_id"], task["id"])
    return public_start(project_id, task)


def public_start(project_id, task):
    return {
        **task,
        "task_id": task["id"],
        "dashboard_url": f"/projects/{project_id}/tasks/{task['id']}",
    }


__all__ = ["prepare_workflow_start", "public_start", "submit"]
