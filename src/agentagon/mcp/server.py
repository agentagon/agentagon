"""Thin stdio interface to the same local service used by the dashboard."""

import os
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from agentagon.core.records import AuditError
from agentagon.workflows.service_host import ensure_service


def create_mcp(client=None):
    server = FastMCP(
        "Agentagon",
        instructions="Agentagon manages its own brain sessions. Start a built-in workflow and inspect its task. Answer shared questions explicitly; a client disconnect does not cancel work.",
    )

    def request(method, path, body=None, *, query=None):
        nonlocal client
        if client is None:
            client = ensure_service()
        return client.request(method, path, body, query=query)

    def path(project_id, *parts):
        return "/api/projects/" + "/".join(quote(p, safe="") for p in (project_id, *parts))

    @server.tool()
    def list_projects() -> dict:
        """List registered local projects."""
        return request("GET", "/api/projects")

    @server.tool()
    def register_project(directory: str) -> dict:
        """Register an existing local project directory."""
        return request("POST", "/api/projects", {"path": directory})

    @server.tool()
    def clone_project(repository: str, directory: str) -> dict:
        """Clone a selected GitHub repository into a new local directory and register it."""
        return request("POST", "/api/projects/clone", {"repository": repository, "path": directory})

    @server.tool()
    def inspect_coding_backends() -> dict:
        """Detect configured managed coding backends and authentication readiness."""
        return request("GET", "/api/assistants")

    @server.tool()
    def configure_coding_backend(settings: dict) -> dict:
        """Configure managed backend defaults through the same credential handling as dashboard Settings."""
        return request("POST", "/api/assistants", settings)

    @server.tool()
    def list_connections(project_id: str) -> dict:
        """List this project's observability connections without acquiring traces."""
        return request("GET", path(project_id, "connectors"))

    @server.tool()
    def discover_connection(
        project_id: str,
        provider: str,
        credentials: dict,
        endpoint: str | None = None,
        connection_id: str | None = None,
    ) -> dict:
        """Check provider credentials and return an expiring discovery ID plus selectable projects.

        provider is braintrust, langsmith, or langfuse. credentials uses api_key for
        Braintrust/LangSmith and public_key plus secret_key for Langfuse. Pass
        connection_id only when replacing credentials for an existing connection.
        This check does not save a connection or enable trace collection.
        """
        body = {"provider": provider, "credentials": credentials}
        if endpoint is not None:
            body["endpoint"] = endpoint
        if connection_id is not None:
            body["id"] = connection_id
        return request("POST", path(project_id, "connectors", "discover"), body)

    @server.tool()
    def save_connection(project_id: str, discovery_id: str, project_selection_id: str) -> dict:
        """Save one project returned by discover_connection; this does not enable monitoring.

        project_selection_id is the selected project's selection_id, not its provider
        project ID. The local service owns credential references and never returns the
        credential value in the saved connection.
        """
        return request(
            "POST",
            path(project_id, "connectors"),
            {"discovery_id": discovery_id, "project": project_selection_id},
        )

    @server.tool()
    def test_connection(project_id: str, connection_id: str) -> dict:
        """Test a saved provider connection and update its retained readiness state."""
        return request("POST", path(project_id, "connectors", connection_id, "test"), {})

    @server.tool()
    def disconnect_connection(project_id: str, connection_id: str) -> dict:
        """Disconnect a saved provider and delete its stored credential references."""
        return request("DELETE", path(project_id, "connectors", connection_id))

    @server.tool()
    def list_agents(project_id: str) -> dict:
        """List confirmed application agents and suggested bindings."""
        return request("GET", path(project_id, "agents"))

    @server.tool()
    def save_agent(project_id: str, binding: dict, agent_id: str | None = None) -> dict:
        """Confirm or update an explicit application-agent code/trace binding."""
        return request(
            "POST", path(project_id, "agents", *([agent_id] if agent_id else [])), binding
        )

    @server.tool()
    def exclude_suggested_agent(
        project_id: str, agent_id: str, reason: str, expected_revision: int
    ) -> dict:
        """Exclude a discovered identity with a retained reason; later scans keep it excluded."""
        return request(
            "POST",
            path(project_id, "agents", agent_id, "exclude"),
            {"reason": reason, "expected_revision": expected_revision},
        )

    @server.tool()
    def restore_suggested_agent(project_id: str, agent_id: str, expected_revision: int) -> dict:
        """Restore an excluded discovered identity to the review queue."""
        return request(
            "POST",
            path(project_id, "agents", agent_id, "restore"),
            {"expected_revision": expected_revision},
        )

    @server.tool()
    def list_workflows() -> dict:
        """List built-in workflow definitions and requirements."""
        return request("GET", "/api/workflows")

    @server.tool()
    def inspect_execution_profiles(project_id: str) -> dict:
        """Inspect effective execution profiles and their settings revision without connecting."""
        return request("GET", path(project_id, "settings"))

    @server.tool()
    def save_execution_profile(
        project_id: str, name: str, profile: dict, expected_revision: str
    ) -> dict:
        """Validate and save a project runner profile. Does not execute commands or connect.

        Profile credentials are environment-variable references only. Existing runs
        retain their frozen configuration. Use the displayed settings revision.
        """
        return request(
            "POST",
            path(project_id, "settings"),
            {
                "scope": "project",
                "profile_name": name,
                "profile": profile,
                "expected_revision": expected_revision,
            },
        )

    @server.tool()
    def inspect_evaluation_design(project_id: str, agent_id: str, goal_id: str) -> dict:
        """Inspect a draft, discovered native evaluators, frozen evaluators and datasets.

        Source inspection does not import application code or execute evaluators.
        """
        return request("GET", path(project_id, "agents", agent_id, "goals", goal_id, "design"))

    @server.tool()
    def save_evaluation_design(project_id: str, agent_id: str, goal_id: str, draft: dict) -> dict:
        """Save a revision-bound evaluation draft without accepting or running it.

        Preserve required behaviors and scoring. Frozen reuse cannot substitute
        another dataset, command or scorer; those changes require a new evaluator.
        """
        return request(
            "POST", path(project_id, "agents", agent_id, "goals", goal_id, "design"), draft
        )

    @server.tool()
    def attach_evaluation_cases(
        project_id: str,
        agent_id: str,
        goal_id: str,
        dataset_snapshot_id: str,
        operation_id: str,
        expected_revision: int,
        expected_cases_revision: int,
    ) -> dict:
        """Attach saved reviewed cases to a goal without accepting or running an evaluation.

        Inspect the evaluation design first for draft and attached_cases revisions,
        using zero when absent. Repeat an identical operation ID to retrieve its
        receipt. Existing cases are retained; frozen reuse becomes a new draft.
        A goal without a draft retains its cases for the later measurement design.
        """
        return request(
            "POST",
            path(project_id, "agents", agent_id, "goals", goal_id, "design", "cases"),
            {
                "dataset_snapshot_id": dataset_snapshot_id,
                "operation_id": operation_id,
                "expected_revision": expected_revision,
                "expected_cases_revision": expected_cases_revision,
            },
        )

    @server.tool()
    def list_goals(project_id: str, agent_id: str) -> dict:
        """List broader improvement goals for an application agent."""
        return request("GET", path(project_id, "agents", agent_id, "goals"))

    @server.tool()
    def save_goal(project_id: str, agent_id: str, goal: dict) -> dict:
        """Create or update a broader improvement goal."""
        return request("POST", path(project_id, "agents", agent_id, "goals"), goal)

    @server.tool()
    def list_issues(project_id: str) -> dict:
        """List independent issues with trace occurrences and verification evidence."""
        return request("GET", path(project_id, "issues"))

    @server.tool()
    def import_trace(
        project_id: str,
        operation_id: str,
        provider: str | None = None,
        data: dict | list | str | None = None,
        connection_id: str | None = None,
        trace_id: str | None = None,
        selection: dict | None = None,
    ) -> dict:
        """Import explicitly selected JSON/JSONL data or a connected provider trace reference."""
        body = {"operation_id": operation_id}
        body.update(
            {
                k: v
                for k, v in {
                    "provider": provider,
                    "data": data,
                    "connection_id": connection_id,
                    "trace_id": trace_id,
                    "selection": selection,
                }.items()
                if v is not None
            }
        )
        return request("POST", path(project_id, "traces", "import"), body)

    @server.tool()
    def inspect_trace(
        project_id: str,
        snapshot_id: str,
        trace_id: str | None = None,
        max_spans: int = 100,
    ) -> dict:
        """Inspect bounded normalized spans, errors, provenance, coverage, and redaction limits."""
        query = {"max_spans": max_spans}
        if trace_id is not None:
            query["trace_id"] = trace_id
        return request("GET", path(project_id, "traces", snapshot_id), query=query)

    @server.tool()
    def select_trace(project_id: str, snapshot_id: str, trace_id: str) -> dict:
        """Freeze one chosen trace as the exact input for a subsequent managed workflow."""
        return request(
            "POST",
            path(project_id, "traces", snapshot_id, "select"),
            {"trace_id": trace_id},
        )

    @server.tool()
    def detect_agents(
        project_id: str, operation_id: str, snapshot_ids: list[str] | None = None
    ) -> dict:
        """Discover and activate named local agents without model or provider calls. Optional snapshot_ids must name already retained evidence."""
        return request(
            "POST",
            path(project_id, "agents", "detect"),
            {
                "operation_id": operation_id,
                "snapshot_ids": snapshot_ids or [],
            },
        )

    @server.tool()
    def start_goal_run(
        project_id: str,
        agent_id: str,
        goal_id: str,
        operation_id: str,
        details: str = "",
        profile: str | None = None,
        max_elapsed_seconds: int | None = None,
        max_trials: int | None = None,
    ) -> dict:
        """Go: automatically design/accept measurements, prepare evaluation, baseline and optimize. Reuses valid evidence and the active goal run. Defaults are 30 minutes/30 trials, bounded by configured settings. Authorizes inferred-plan acceptance and local execution defaults, never publishing or deployment. Ask only for genuinely missing input; inspect active_task_id for shared questions. Retry the same operation and inputs."""
        body = {"operation_id": operation_id, "details": details}
        body.update(
            {
                key: value
                for key, value in {
                    "profile": profile,
                    "max_elapsed_seconds": max_elapsed_seconds,
                    "max_trials": max_trials,
                }.items()
                if value is not None
            }
        )
        return request("POST", path(project_id, "agents", agent_id, "goals", goal_id, "runs"), body)

    @server.tool()
    def list_goal_runs(project_id: str, agent_id: str, goal_id: str) -> dict:
        """Read saved goal progress; does not start work or reconnect execution."""
        return request("GET", path(project_id, "agents", agent_id, "goals", goal_id, "runs"))

    @server.tool()
    def inspect_goal_run(project_id: str, run_id: str) -> dict:
        """Read goal state, active task, required inputs, allowance and verified outcome reference."""
        return request("GET", path(project_id, "goal-runs", run_id))

    @server.tool()
    def control_goal_run(project_id: str, run_id: str, action: str, operation_id: str) -> dict:
        """Pause, resume or cancel the same goal run. Resume preserves uncertain child identities and the overall allowance."""
        if action not in {"pause", "resume", "cancel"}:
            raise AuditError("choose pause, resume or cancel")
        return request(
            "POST", path(project_id, "goal-runs", run_id, action), {"operation_id": operation_id}
        )

    @server.tool()
    def inspect_workflow_draft(project_id: str, key: str) -> dict:
        """Read a private workflow form draft and revision; no preparation or execution."""
        return request("GET", path(project_id, "drafts", key))

    @server.tool()
    def save_workflow_draft(project_id: str, key: str, expected_revision: int, draft: dict) -> dict:
        """Save a <=16KB local draft. key is the dashboard's draft-key operation binding (length:hex:hex). draft requires workflow, operationId UUID and operationBinding; optional fields are agentId, goalId, problem, expected, helpDefine, traceId (snapshot reference), profile. Incomplete forms are allowed. Any changed content needs a fresh operationId. Start at revision 0; stale saves conflict. Do not include raw traces or credentials; text fields are stored as entered."""
        return request(
            "POST",
            path(project_id, "drafts", key),
            {"expected_revision": expected_revision, "draft": draft},
        )

    @server.tool()
    def clear_workflow_draft(project_id: str, key: str, expected_revision: int) -> dict:
        """Clear an accepted or discarded form at its displayed revision; preserve concurrent edits."""
        return request(
            "POST",
            path(project_id, "drafts", key, "clear"),
            {"expected_revision": expected_revision},
        )

    @server.tool()
    def prepare_workflow(
        project_id: str,
        workflow: str,
        input: dict,
        agent_id: str | None = None,
        scope: list[str] | None = None,
        limits: dict | None = None,
        options: dict | None = None,
        assistant: str | None = None,
        model: str | None = None,
        expected_revisions: dict | None = None,
    ) -> dict:
        """Validate a complete workflow intent and return exact prerequisites without starting work."""
        body = {"workflow": workflow, "input": input}
        body.update(
            {
                key: value
                for key, value in {
                    "agent_id": agent_id,
                    "scope": scope,
                    "limits": limits,
                    "options": options,
                    "assistant": assistant,
                    "model": model,
                    "expected_revisions": expected_revisions,
                }.items()
                if value is not None
            }
        )
        return request("POST", path(project_id, "workflows", "prepare"), body)

    @server.tool()
    def start_workflow(
        project_id: str,
        workflow: str,
        input: dict,
        operation_id: str,
        agent_id: str | None = None,
        scope: list[str] | None = None,
        limits: dict | None = None,
        options: dict | None = None,
        assistant: str | None = None,
        model: str | None = None,
        expected_revisions: dict | None = None,
    ) -> dict:
        """Start managed work immediately. input is {type: goal|issue|trace|description|agent|project|monitor, id?: string, text?: string}. Retry with the same operation_id and inputs to get the same task."""
        body = {"workflow": workflow, "input": input, "operation_id": operation_id}
        body.update(
            {
                k: v
                for k, v in {
                    "agent_id": agent_id,
                    "scope": scope,
                    "limits": limits,
                    "options": options,
                    "assistant": assistant,
                    "model": model,
                    "expected_revisions": expected_revisions,
                }.items()
                if v is not None
            }
        )
        return request("POST", path(project_id, "tasks"), body)

    @server.tool()
    def inspect_issue(project_id: str, issue_id: str) -> dict:
        """Read an issue's diagnoses, occurrences, repair tasks and verification evidence."""
        return request("GET", path(project_id, "issues", issue_id))

    @server.tool()
    def update_issue(
        project_id: str,
        issue_id: str,
        action: str,
        expected_revision: int,
        reason: str = "",
        agent_id: str | None = None,
        expected_behavior: str | None = None,
    ) -> dict:
        """Record an optimistic, revision-bound issue triage decision.

        action is dismiss, reopen, assign, or set_expectation. Dismiss and
        set_expectation require a reason; assign requires a confirmed agent_id;
        set_expectation requires expected_behavior. Inspect the issue again after
        a revision conflict instead of replaying a stale decision.
        """
        body = {
            "action": action,
            "expected_revision": expected_revision,
            "reason": reason,
        }
        if agent_id is not None:
            body["agent_id"] = agent_id
        if expected_behavior is not None:
            body["expected_behavior"] = expected_behavior
        return request("POST", path(project_id, "issues", issue_id), body)

    @server.tool()
    def list_tasks(project_id: str) -> dict:
        """List shared tasks, including tasks started in the dashboard."""
        return request("GET", path(project_id, "tasks"))

    @server.tool()
    def inspect_task(project_id: str, task_id: str) -> dict:
        """Inspect progress, retained results, and the shared pending question."""
        return request("GET", path(project_id, "tasks", task_id))

    @server.tool()
    def continue_assessment(
        project_id: str, task_id: str, operation_id: str, max_elapsed_seconds: int
    ) -> dict:
        """Start a linked assessment attempt with explicit new seconds after inspecting recovery.

        operation_id must be a UUID. Retry an uncertain request with exactly the same
        UUID and payload. Original consumption and retained evidence remain unchanged;
        current source/scope readiness is checked before starting the new attempt.
        """
        return request(
            "POST",
            path(project_id, "tasks", task_id, "continue"),
            {
                "operation_id": operation_id,
                "max_elapsed_seconds": max_elapsed_seconds,
            },
        )

    @server.tool()
    def inspect_result(project_id: str, workflow: str, result_id: str) -> dict:
        """Inspect a typed workflow result, its evidence-backed alternatives, and permitted actions."""
        return request("GET", path(project_id, "results", workflow, result_id))

    @server.tool()
    def decide_result(
        project_id: str,
        workflow: str,
        result_id: str,
        operation_id: str,
        expected_revision: int,
        decision: str,
        candidate_id: str | None = None,
    ) -> dict:
        """Select a verified Fix/Optimize candidate or keep the current version, with an idempotent user receipt."""
        body = {
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "decision": decision,
        }
        if candidate_id is not None:
            body["candidate_id"] = candidate_id
        return request("POST", path(project_id, "results", workflow, result_id, "decision"), body)

    @server.tool()
    def prepare_delivery(
        project_id: str,
        kind: str,
        source_id: str,
        publish: bool = False,
        remote: str | None = None,
        base: str | None = None,
        eval_parent_id: str | None = None,
        delivery_id: str | None = None,
    ) -> dict:
        """Prepare a local result package, or explicitly publish its already-reviewed package."""
        body = {"kind": kind, "source_id": source_id, "publish": publish}
        body.update(
            {
                key: value
                for key, value in {
                    "remote": remote,
                    "base": base,
                    "eval_parent_id": eval_parent_id,
                    "delivery_id": delivery_id,
                }.items()
                if value is not None
            }
        )
        return request("POST", path(project_id, "deliveries"), body)

    @server.tool()
    def control_task(project_id: str, task_id: str, action: str, response: dict) -> dict:
        """Answer, pause, cancel, resume, send guidance, or retry failed lesson recording. Supply operation_id; answers also need the inspected question_id, and message needs a message string."""
        if action not in {"answer", "pause", "cancel", "resume", "message", "retry-memory"}:
            raise AuditError("choose answer, pause, cancel, resume, message, or retry-memory")
        return request("POST", path(project_id, "tasks", task_id, action), response)

    @server.tool()
    def inspect_onboarding(project_id: str) -> dict:
        """Inspect resumable project setup."""
        return request("GET", path(project_id, "onboarding"))

    @server.tool()
    def configure_onboarding(project_id: str, scope: dict) -> dict:
        """Save explicit assessment scope; start assess separately."""
        return request("POST", path(project_id, "onboarding"), {"scope": scope, "state": "ready"})

    @server.tool()
    def list_recommendations(project_id: str) -> dict:
        """Read evidence-backed issues and unmeasured improvement paths."""
        return request("GET", path(project_id, "recommendations"))

    @server.tool()
    def disposition_recommendation(
        project_id: str,
        recommendation_id: str,
        evidence_revision: str,
        value: str,
        reason: str = "",
        expected_revision: int = 0,
    ) -> dict:
        """Dismiss one exact recommendation revision as not now or not relevant."""
        return request(
            "POST",
            path(project_id, "recommendations", recommendation_id, "disposition"),
            {
                "evidence_revision": evidence_revision,
                "value": value,
                "reason": reason,
                "expected_revision": expected_revision,
            },
        )

    @server.tool()
    def inspect_production(project_id: str) -> dict:
        """Read improvements, deployments, monitors and production observations."""
        return request("GET", path(project_id, "production"))

    @server.tool()
    def save_monitor(project_id: str, policy: dict, monitor_id: str | None = None) -> dict:
        """Create or update explicit monitoring scope, accepted measurements and limits."""
        return request(
            "POST", path(project_id, "monitors", *([monitor_id] if monitor_id else [])), policy
        )

    @server.tool()
    def control_monitor(project_id: str, monitor_id: str, action: str) -> dict:
        """Pause, enable or discard an unresolved observation. Analyze now through start_workflow observe."""
        return request("POST", path(project_id, "monitors", monitor_id, action), {})

    @server.tool()
    def record_deployment(project_id: str, deployment: dict) -> dict:
        """Declare release, environment, actual revision and deployed_at for a selected improvement."""
        return request("POST", path(project_id, "deployments"), deployment)

    @server.tool()
    def list_memory_groups(project_id: str) -> dict:
        """List memory groups explicitly shared with this project."""
        return request("GET", path(project_id, "memory"))

    @server.tool()
    def create_memory_group(project_id: str, group: dict) -> dict:
        """Register a named local folder with purpose, project_ids, write_project_ids and agent_ids."""
        return request("POST", path(project_id, "memory"), group)

    @server.tool()
    def recall_memory(
        project_id: str,
        group_id: str,
        query: str = "",
        agent_id: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Recall bounded advisory lessons. Evaluations read their pinned snapshot."""
        if snapshot := os.environ.get("AGENTAGON_MEMORY_SNAPSHOT"):
            from agentagon.memory.execution import recalled_snapshot

            return recalled_snapshot(snapshot, project_id, group_id, query, agent_id, limit)
        return request(
            "POST",
            path(project_id, "memory", group_id, "recall"),
            {"query": query, "agent_id": agent_id, "limit": limit},
        )

    @server.tool()
    def record_memory(
        project_id: str,
        group_id: str,
        key: str,
        text: str,
        evidence: list[str],
        uncertainty: str = "",
        agent_id: str | None = None,
        expected_version: int | None = None,
    ) -> dict:
        """Record a versioned lesson with evidence references; target-agent memory requires its agent binding."""
        if os.environ.get("AGENTAGON_MEMORY_SNAPSHOT"):
            raise AuditError("evaluation memory is frozen; record outcomes outside the evaluation")
        entry = {"key": key, "text": text, "evidence": evidence, "uncertainty": uncertainty}
        if expected_version is not None:
            entry["expected_version"] = expected_version
        return request(
            "POST",
            path(project_id, "memory", group_id, "record"),
            {"agent_id": agent_id, "entry": entry},
        )

    return server
