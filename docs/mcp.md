# Local MCP interface

Configure a stdio MCP server with executable `agentagon` and arguments `["mcp"]`. Agentagon uses the official Python SDK with a bounded major-version dependency. MCP connects to the same local service as the dashboard, starting it without a browser when necessary. It launches Agentagon-managed brain sessions; the calling agent does not execute the workflow.

Tools expose project and application-agent discovery, goals, workflows, issues, explicit trace import, task start/inspection/control, and memory groups/recall/record. `list_workflows` returns the built-in workflow names and requirements. `import_trace` accepts supported pasted/uploaded JSON or JSONL, or a provider connection and trace ID (or bounded selection).

`inspect_issue` returns the current issue revision, retained evidence, lifecycle facets, and user decision history. `update_issue` records one revision-bound triage decision: mark it not actionable with a reason, reopen it, assign it to a confirmed application agent, or save reviewed expected behavior with a reason. If another client changes the issue first, inspect it again and decide against the new revision.

`start_workflow` takes `workflow`, `project_id`, optional `agent_id`, typed `input`, optional `scope`, `limits`, `options`, and an `operation_id`. Inputs are `{type: "goal"|"issue"|"trace", id: "..."}`, `{type: "description", text: "..."}`, or `{type: "agent"}`. Limits bound trials, elapsed time and per-trial timeout. The response immediately includes `task_id`, `state`, and `dashboard_url`. Repeating the same operation ID and payload returns the same task; changing its payload is rejected.

`inspect_task` exposes the shared pending question. `control_task` accepts `answer`, `cancel`, or `resume` with an operation ID and the current question ID/answer where required. Closing MCP leaves tasks running. Service interruption requires explicit resume, preserving existing execution and review identities.

`recall_memory` and `record_memory` enforce project/agent bindings. Inside an evaluation, recall uses its pinned snapshot and writes are rejected. See [memory](memory.md).

## Assessment and production

`clone_project` and `inspect_coding_backends` reuse the dashboard setup operations. Provider setup uses the same two-step credential flow as the dashboard:

1. Call `discover_connection` with `provider`, its credential fields and an optional endpoint. The result contains an expiring `discovery_id` and provider projects with opaque `selection_id` values.
2. Call `save_connection` with that `discovery_id` and the chosen `project_selection_id`.

The local service owns temporary and durable credential references; connection results do not expose credential values. Discovery does not save a connection, import traces or enable monitoring. `test_connection` refreshes a saved connection's readiness, and `disconnect_connection` removes the connection and its stored credential references. `list_connections` is read-only.

`configure_onboarding` saves assessment scope; `inspect_onboarding` resumes setup. Start `assess` with `{type: "project", id: PROJECT_ID}`. `discover_agents` also starts an assessment and requires an operation ID. `list_recommendations` returns issues and measured or unmeasured improvement paths.

`save_monitor` creates or updates an explicit agent/environment policy with accepted measurements and bounds. Updates require the current revision. `control_monitor` accepts pause, enable, or discard. Start `observe` with `{type: "monitor", id: MONITOR_ID}` for Analyze now. It uses the saved policy; callers cannot supply scheduler-owned windows or widen selectors.

`inspect_production` returns improvements, deployments, monitors, immutable observation references and attention items. `record_deployment` takes an operation ID, selected improvement ID, release, environment, actual revision and deployment time. This declares deployment without performing one. See [production monitoring](production.md) for criteria and local service limits.
