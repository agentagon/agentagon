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

    def request(method, path, body=None):
        nonlocal client
        if client is None:
            client = ensure_service()
        return client.request(method, path, body)

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
    def save_connection(project_id: str, connection: dict) -> dict:
        """Configure a project observability connection using existing credential handling. This does not enable monitoring."""
        return request("POST", path(project_id, "connectors"), connection)

    @server.tool()
    def list_agents(project_id: str) -> dict:
        """List confirmed application agents and suggested bindings."""
        return request("GET", path(project_id, "agents"))

    @server.tool()
    def discover_agents(project_id: str, operation_id: str) -> dict:
        """Suggest application agents from project code and configured traces."""
        return request(
            "POST", path(project_id, "agents", "discover"), {"operation_id": operation_id}
        )

    @server.tool()
    def save_agent(project_id: str, binding: dict, agent_id: str | None = None) -> dict:
        """Confirm or update an explicit application-agent code/trace binding."""
        return request(
            "POST", path(project_id, "agents", *([agent_id] if agent_id else [])), binding
        )

    @server.tool()
    def list_workflows() -> dict:
        """List built-in workflow definitions and requirements."""
        return request("GET", "/api/workflows")

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
    def list_tasks(project_id: str) -> dict:
        """List shared tasks, including tasks started in the dashboard."""
        return request("GET", path(project_id, "tasks"))

    @server.tool()
    def inspect_task(project_id: str, task_id: str) -> dict:
        """Inspect progress, retained results, and the shared pending question."""
        return request("GET", path(project_id, "tasks", task_id))

    @server.tool()
    def control_task(project_id: str, task_id: str, action: str, response: dict) -> dict:
        """Answer, cancel, or explicitly resume a task. Supply operation_id and question_id for answers, as reported by inspect_task."""
        if action not in {"answer", "cancel", "resume"}:
            raise AuditError("choose answer, cancel, or resume")
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
