# Use the local workspace

Run `agentagon` in any local agent project. A GitHub remote is optional. The React workspace connects to a detached local service, so closing the browser does not cancel managed work.

On first use, choose **Open local folder** for an existing directory or **Clone repository** only when you need a new checkout. The folder does not need a GitHub remote, and code-only assessment also works outside Git. Setup shows the code source, managed coding backend, and optional trace evidence separately. **Analyze project** performs a bounded, read-only assessment. A code-only assessment remains useful when no observability provider is connected.

The main navigation follows the engineer's recurring jobs:

- **Overview** shows work that needs a decision, one recommended next action, agents, and recent outcomes.
- **Agents** shows confirmed and suggested application agents, their responsibilities, source scopes, and identity state.
- **Production** shows monitor coverage, observations, deployments, and evidence gaps.
- **Activity** shows tasks that need input, are running, or have finished.
- **Settings** configures the project, coding backend, connections, execution, and data boundaries.

Goals, issues, workflows, connections, and lessons remain available from the agent or action where they matter. They are not separate destinations that must be visited before doing useful work.

## Fix a supplied failure

Choose **All issues** from Overview or an agent workspace, or use **Fix a problem** from an agent. Paste or upload supported trace JSON/JSONL, select an imported snapshot, choose a configured provider trace, or describe the failure directly. A saved goal is optional.

Pasted, uploaded, and provider traces use a review step. Choose **Check trace** first. Agentagon shows the detected format, trace identities, usable spans, incomplete coverage, redaction, and any blockers without retaining evidence. When the preview is usable, choose **Import and investigate** or **Import and prepare fix** to create the immutable project snapshot and continue. Editing the input invalidates the preview and requires another check.

Open a retained issue to review its evidence and lifecycle. You can assign a confirmed agent, save reviewed expected behavior, mark the issue **Not actionable** with a reason, or reopen it. These decisions preserve the original evidence and use issue revisions to prevent one client from overwriting another.

Before work starts, Agentagon prepares the complete intent and displays ownership, source identity, verification prerequisites, expected outputs, and known limits. Submission rechecks the same revisions. Missing ownership or expected behavior becomes an explicit question instead of an unsupported repair.

A completed Fix or Optimize task opens a full result page. It separates the task's recommendation from your decision, compares the current version and verified candidates, shows the exact source diff and independent review, retains failed attempts, and lists limitations. **Select this change** or **Keep current version** creates a durable decision. **Prepare local delivery** creates reviewable local artifacts; it does not merge, push, publish, or deploy.

## Improve and observe

Use an agent's **Evaluations** tab to inspect the complete measurement proposal before accepting it. Prepare the evaluator and baseline, then run Optimize for broader goals. Values, directions, guard outcomes, sample identity, and limits stay attached to the result.

Monitoring is enabled explicitly for one confirmed agent and environment. Production keeps collection state, comparison evidence, deployment declarations, and tested revisions separate. A verified code change does not become production recovery automatically. Missing samples, mixed releases, partial coverage, and stale collection remain visible limitations.

Tasks survive dashboard or MCP disconnection. Service interruption leaves work interrupted and requires an explicit resume. Reads do not start work or reconnect providers. Mutations require the exact local origin and session token.

[Production monitoring](production.md) · [Memory groups](memory.md) · [MCP](mcp.md) · [Workflow behavior](workflows.md)
