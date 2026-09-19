"""One typed start contract shared by HTTP and MCP."""

import copy

from agentagon.core.records import AuditError, digest
from agentagon.domain.issues import assign_agent, get_issue, link_task
from agentagon.workflows.registry import REGISTRY
from agentagon.workflows.runtime import operation_id


def submit(application, project_id, payload, *, scheduled=False):
    if not isinstance(payload, dict) or set(payload) - {
        "operation_id",
        "workflow",
        "agent_id",
        "input",
        "scope",
        "limits",
        "assistant",
        "model",
        "options",
    }:
        raise AuditError("unsupported task fields")
    operation = operation_id(payload.get("operation_id"))
    binding = digest(payload)
    existing = application.runtime.existing_submission(project_id, operation, binding)
    if existing:
        return public_start(project_id, existing)
    workflow = payload.get("workflow")
    if workflow not in REGISTRY:
        raise AuditError("choose a built-in workflow")
    if workflow in {"assess", "observe"}:
        return application.production.submit(project_id, payload, binding, scheduled)
    source = copy.deepcopy(payload.get("input", {}))
    if not isinstance(source, dict) or source.get("type") not in {
        "goal",
        "issue",
        "trace",
        "description",
        "agent",
    }:
        raise AuditError("input.type must be goal, issue, trace, description, or agent")
    if set(source) - {"type", "id", "text"}:
        raise AuditError("unsupported workflow input fields")
    options = copy.deepcopy(payload.get("options", {}))
    limits = payload.get("limits", {})
    if (
        not isinstance(options, dict)
        or not isinstance(limits, dict)
        or set(limits) - {"max_trials", "max_elapsed_seconds", "trial_timeout_seconds"}
    ):
        raise AuditError("invalid workflow options or limits")
    if set(options) - {
        "profile",
        "engine",
        "finalist_count",
        "host_concurrency",
        "evaluation_id",
        "baseline_id",
        "dataset_snapshot_id",
    }:
        raise AuditError("unsupported workflow configuration")
    options.update(limits)
    workspace = application.state.workspace(project_id)
    issue = get_issue(workspace, source.get("id")) if source["type"] == "issue" else None
    agent_id = payload.get("agent_id") or (issue.get("agent_id") if issue else None)
    if not agent_id and workflow != "discover":
        agents = [a for a in application.catalog.agents(project_id) if a["status"] == "confirmed"]
        if len(agents) != 1:
            raise AuditError("Select the agent that owns this issue; ownership is ambiguous.")
        agent_id = agents[0]["id"]
    agent = application.catalog.agent(project_id, agent_id) if agent_id else None
    if agent and agent["status"] != "confirmed":
        raise AuditError("confirm the agent binding before starting work")
    if issue and issue.get("agent_id") not in (None, agent_id):
        raise AuditError("issue belongs to another agent")
    if source["type"] == "trace":
        from agentagon.capabilities.traces import snapshots

        trace = snapshots.load(workspace, source.get("id"))
        if trace["kind"] != "traces":
            raise AuditError("select a trace snapshot")
        options["trace_snapshot_id"] = trace["id"]
    if workflow == "discover" and source["type"] != "trace":
        raise AuditError("Discover issues requires selected traces")
    if workflow == "fix" and source["type"] not in {"issue", "trace", "description"}:
        raise AuditError("Fix requires an issue, trace, or problem description")
    if issue:
        options["issue_id"] = issue["issue_id"]
        sources = [
            o.get("source_id")
            for o in issue["occurrences"]
            if str(o.get("source_id", "")).startswith("snapshot_")
        ]
        if sources:
            options["trace_snapshot_id"] = sources[-1]
    goal_id = source.get("id") if source["type"] == "goal" else None
    objective = source.get("text") or (issue["summary"] if issue else None)
    if source["type"] == "description" and (
        not isinstance(objective, str) or not objective.strip()
    ):
        raise AuditError("describe the problem to fix")
    objective = objective or (
        "Find supported issues in the selected traces"
        if workflow == "discover"
        else "Fix the failure shown in the selected trace"
    )
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
            from agentagon.capabilities.experiments.checkouts import under

            if not agent["code_scopes"]:
                raise AuditError("Bind application code before repairing this trace-only agent.")
            permitted = payload.get("scope", agent["code_scopes"])
            if (
                not isinstance(permitted, list)
                or not permitted
                or any(
                    not isinstance(p, str)
                    or not any(
                        under(p, s) for s in agent["code_scopes"] + agent["shared_dependencies"]
                    )
                    for p in permitted
                )
            ):
                raise AuditError("repair scope must stay inside the confirmed agent binding")
            options.update(permitted_paths=permitted, code_scopes=agent["code_scopes"])
            suite = application.catalog.suite(project_id, agent_id, None, permitted_paths=permitted)
            if suite["missing"]:
                raise AuditError(
                    "Existing regression goals need current baselines before repair: "
                    + ", ".join(m["name"] for m in suite["missing"])
                )
            if suite["members"]:
                # A focused repair has no goal row; its suite still preserves all accepted goals.
                suite["goal_id"] = None
                suite["digest"] = digest(
                    {k: v for k, v in suite.items() if k not in {"digest", "missing"}}
                )
                options["suite_manifest"] = suite
        with application.lock:
            groups = application.memory.improvement_groups(project_id, agent_id)
            body["improvement_memory"] = [
                application.memory.recall(project_id, g["id"], objective, agent_id) for g in groups
            ]
            body["memory_snapshot"] = application.memory.snapshot(project_id, agent_id)
            if issue:
                assign_agent(workspace, issue["issue_id"], agent_id)
            task = application.runtime.submit(project_id, body, request_binding=binding)
    else:
        if REGISTRY[workflow]["requires_goal"] and not goal_id:
            raise AuditError("select a goal for this workflow")
        if payload.get("scope"):
            options["permitted_paths"] = payload["scope"]
        # Goal workflows use their accepted measurement plans and the same runtime.
        task = application.submit_job(project_id, body, request_binding=binding)
    if issue:
        link_task(workspace, issue["issue_id"], task["id"])
    return public_start(project_id, task)


def public_start(project_id, task):
    return {
        **task,
        "task_id": task["id"],
        "dashboard_url": f"/projects/{project_id}/tasks/{task['id']}",
    }
